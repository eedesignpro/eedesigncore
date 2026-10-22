"""Symbol extract / inject operations (spec §2.3-2.4, §5, R11).

These are pure model transforms between a ``.kicad_sch`` ``Schematic`` and a
standalone ``.kicad_sym``-ready ``Symbol``. Neither performs disk IO.

``extract_symbol`` lifts a ``(lib_symbols)`` entry out into a library-clean
``Symbol`` (de-qualified name, ready to nest into a ``SymbolLib`` /
``.kicad_sym``); ``inject_symbol`` registers a library symbol definition into a
schematic's ``(lib_symbols)`` cache and synthesizes the placed ``SCH_SYMBOL``
instance node (``PlacedSymbol``).

Every transform step cites the delta-report Part-3 row it implements
(``auditor-report-embed-context-deltas-2026-07-09.md`` Part 2 / Part 3) and the
extract-inject spec (``extract-inject-spec-2026-07-13.md`` §2.3-2.4 / §5); the
KiCad ``file:line`` citations are carried from those reports. ``.kicad_sym``
carries no uuids (uuid-lifecycle Task 4), so there is no uuid transform on
extract; the placed instance and per-pin uuids on inject are schematic-side KIIDs
minted at placement (§5.1).

This module lives under ``sch/`` (mirroring ``pcb/operations.py``, ADR-035): the
operations bridge ``sch.Schematic`` + ``sch.PlacedSymbol`` and the shared
``common/sch_family`` ``Symbol``; ``sch`` already depends on both, so this is the
acyclic home.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from uuid import uuid4

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.sch.models import (
    InstancePath,
    InstanceProject,
    PlacedSymbol,
    PlacedSymbolPin,
    Schematic,
)
from eedesigncore.tables.resolver import ResolvedLibTable, qualify_item_name


def _bare_item_name(lib_id: str) -> str:
    """Return the bare library item name from a possibly ``Lib:Name`` lib_id."""
    return lib_id.split(":", 1)[1] if ":" in lib_id else lib_id


def _defs_equal_ignoring_name(a: Symbol, b: Symbol) -> bool:
    """Compare two symbol definitions ignoring their names, top level and units.

    Mirrors ``SCH_SCREEN::Append``'s equality check, which temporarily aligns the
    incoming symbol's name to the cached one before comparing
    (``sch_screen.cpp:215-224``): the collision test is on the definition body, not
    the (context-dependent) top-level name.

    Both sides are aligned to one name through :meth:`Symbol.renamed`, so the
    UNIT names are normalised as well. A unit name is derived data — KiCad
    composes it on every save and never stores it — so a difference there is
    never a difference in the symbol. Comparing the raw models instead made a
    reused ``R_1`` entry, whose units this library had already re-encoded, look
    different from the incoming definition and minted a needless ``R_2``.
    """
    return a.renamed("x") == b.renamed("x")


def extract_symbol(sch: Schematic, *, lib_id: str) -> Symbol:
    """Lift a ``(lib_symbols)`` entry into a ``.kicad_sym``-ready ``Symbol`` (spec §2.3).

    Selects the entry keyed by full ``lib_id`` (``Symbol.name`` carries the
    verbatim ``Lib:Name`` in schematic context, ADR-037), deep-copies it, and
    applies the EXTRACT transform (Part-3 "sch lib_symbols -> .kicad_sym"):

    - Step 1 REWRITE ``(symbol "Lib:Name")`` -> ``"Name"``: de-qualify the
      top-level name (``SaveSymbol:365-382``).
    - Steps 2/3 KEEP per-unit ``"Name_u_b"`` child names and ``(extends "parent")``
      -- already bare, stored in separate fields, untouched (``SaveSymbol:492-538``).
    - Step 6 CONSIDER derived flattening: whatever ``(extends)`` the source carries
      is preserved verbatim; flattening is not required for correctness
      (spec §5, ``sch_screen.cpp:837`` UNVERIFIED universality).

    ``.kicad_sym`` carries no uuids (uuid-lifecycle Task 4), so no uuid transform
    is applied. The returned ``Symbol`` is directly nestable into a ``SymbolLib``
    for a standalone ``.kicad_sym``. The source schematic is not mutated.

    Raises ``KeyError`` if no ``(lib_symbols)`` entry matches ``lib_id``.
    """
    for entry in sch.lib_symbols:
        if entry.name == lib_id:
            sym = entry.model_copy(deep=True)
            # Step 1 REWRITE (symbol "Lib:Name") -> "Name" (SaveSymbol:365-382)
            sym.name = _bare_item_name(sym.name)
            return sym
    raise KeyError(f"no lib_symbols entry with lib_id {lib_id!r} in schematic")


def _register_lib_def(sch: Schematic, incoming: Symbol) -> str:
    """Register ``incoming`` in ``sch.lib_symbols``, returning the resolved key (§5.2).

    Implements the ``SCH_SCREEN::Append`` collision-rename
    (``sch_screen.cpp:179-263``). ``incoming.name`` is the desired full lib_id key:

    1. Key absent -> insert ``incoming``; key is used verbatim.
    2. Key present AND cached def equals ``incoming`` (ignoring name) -> reuse; no
       insert.
    3. Key present but the cached def differs -> among existing ``<ItemName>_<n>``
       renamed entries, reuse one whose def matches; otherwise mint a fresh unique
       bare ``<ItemName>_<n>`` (empty nickname, ``LIB_ID(wxEmptyString, newName)``,
       ``sch_screen.cpp:236-263``), insert ``incoming`` under it, and return it.

    The case-3 rename goes through :meth:`Symbol.renamed`, which re-encodes the
    unit names. Setting ``name`` alone left the units on the old prefix and
    KiCad then refused the whole file (``sch_io_kicad_sexpr_parser.cpp:501``).
    """
    key = incoming.name
    existing = {entry.name: entry for entry in sch.lib_symbols}

    if key not in existing:  # case 1
        sch.lib_symbols.append(incoming)
        return key

    if _defs_equal_ignoring_name(existing[key], incoming):  # case 2
        return key

    # case 3: collision with a differing definition -> rename to bare "<Name>_<n>".
    bare = _bare_item_name(key)
    match_pattern = re.compile(rf"^{re.escape(bare)}_(\d+)$")
    for name, entry in existing.items():
        if match_pattern.match(name) and _defs_equal_ignoring_name(entry, incoming):
            return name  # reuse an already-renamed identical entry

    n = 1
    while f"{bare}_{n}" in existing:
        n += 1
    new_name = f"{bare}_{n}"
    sch.lib_symbols.append(incoming.renamed(new_name))
    return new_name


def _collect_pins(symbol: Symbol) -> list[Pin]:
    """Return the symbol's pins across all unit sub-symbols (``GetRawPins``).

    The placed instance emits one ``(pin "num" (uuid))`` per lib-symbol pin
    (``saveSymbol:824-841``); pins live in the unit sub-symbols' item lists.
    """
    return [item for unit in symbol.units for item in unit.items if isinstance(item, Pin)]


def _refdes_prefix(reference: str) -> str:
    """Return the reference-designator prefix, mirroring KiCad.

    Ports ``UTIL::GetRefDesPrefix`` (``common/refdes_utils.cpp:35-44``): scan
    from the back and drop every trailing digit and question mark. ``"R12"`` and
    ``"R?"`` both give ``"R"``. The C++ code uses ASCII ``std::isdigit``, so this
    port tests the ASCII range instead of ``str.isdigit``.
    """
    end = len(reference)
    while end > 0 and (reference[end - 1] == "?" or "0" <= reference[end - 1] <= "9"):
        end -= 1
    return reference[:end]


def _refdes_number(reference: str) -> int:
    """Return the reference-designator number, or -1 when there is none.

    Ports ``UTIL::GetRefDesNumber`` (``common/refdes_utils.cpp:54-70``): take the
    text from the first digit to the end and convert it. A tail that is not all
    digits gives -1, matching the ``wxString::ToLong`` failure branch.
    """
    for index, char in enumerate(reference):
        if "0" <= char <= "9":
            tail = reference[index:]
            if all("0" <= c <= "9" for c in tail):
                return int(tail)
            return -1
    return -1


def _instance_references(sch: Schematic) -> Iterator[str]:
    """Yield every reference designator already placed in ``sch``.

    Two sources carry a reference. The per-sheet ``(instances (project (path
    (reference …))))`` entry is the authoritative one (``saveSymbol:944-947``).
    The ``Reference`` field is the cached display copy (``saveSymbol:791-797``).
    This yields both, because a hand-built model can hold only one of them.
    """
    for placed in sch.symbols:
        for field in placed.fields:
            if field.name == "Reference":
                yield field.value
        for project in placed.instances:
            for path in project.paths:
                yield path.reference


def _next_free_reference(sch: Schematic, prefix: str) -> str:
    """Return ``<prefix><n>`` for the lowest free ``n``, counting from 1."""
    used = {
        number
        for reference in _instance_references(sch)
        if _refdes_prefix(reference) == prefix and (number := _refdes_number(reference)) >= 0
    }
    number = 1
    while number in used:
        number += 1
    return f"{prefix}{number}"


def _auto_reference(sch: Schematic, fields: list[Property]) -> str:
    """Assign ``<Prefix><n>`` from the lib def prefix and the target schematic.

    The prefix comes from the library definition Reference field. An empty
    prefix falls back to ``"U"``, which is what KiCad does
    (``eeschema/sch_symbol.cpp:716-720``).

    **The prefix is only as good as the definition.** It is read off the
    definition handed in, and nothing else. A consumer hit this: a resistor was
    annotated ``U2``. The definition was a metadata-only symbol built from a
    database row whose drawn symbol could not be found, and such a symbol
    carries the Reference ``U`` that :meth:`Symbol.new` seeds, exactly as
    KiCad's own ``LIB_SYMBOL`` constructor seeds it
    (``lib_symbol.cpp:247-251``). So the designator was a faithful reading of a
    definition that should never have been placed.

    The guard against that is upstream, not here:
    :func:`eedesigncore.library.database.resolve_row` reports
    ``metadata_only`` and says why, and
    :attr:`ResolvedLibTable.skipped` names any library the read confinement
    dropped. Check the first before placing a row-built definition. Passing
    ``reference`` explicitly also removes the question.

    No refusal is raised for a definition with no Reference field at all. KiCad
    falls back to ``U`` there too, and a caller may legitimately place a bare
    ``Symbol(name=…)``.
    """
    prefix = _refdes_prefix(_reference_default(fields)) or "U"
    return _next_free_reference(sch, prefix)


def _placed_field(field: Property, at: tuple[float, float]) -> Property:
    """Copy a library field and move it to its ABSOLUTE placed position.

    A library definition stores a field position as an offset from the symbol
    origin. A placed symbol stores it as a sheet coordinate. KiCad converts on
    construction with one vector add, ``schField->SetTextPos( m_pos +
    libField->GetTextPos() )`` (``eeschema/sch_symbol.cpp:1408``, ``:1417``).

    **The Y offset is SUBTRACTED, not added, and that is not a sign error.**
    KiCad's parser inverts Y when it reads a LIBRARY field and does not when
    it reads a PLACED field: ``parseXY( true )`` against ``parseXY()``
    (``sch_io_kicad_sexpr_parser.h:169-178``, the library case at
    ``sch_io_kicad_sexpr_parser.cpp:1127``, the placed case at ``:4726``). So
    KiCad's in-memory library offset is the NEGATED file value, and its vector
    add comes out as ``at.y - file_y``. This model stores the file value
    verbatim, on both the read and the write side, which is what keeps a
    ``.kicad_sym`` round trip byte-identical — so the subtraction belongs
    here.

    Measured over 579 corpus schematics, on hidden fields with a non-zero
    library Y offset and the symbol at angle 0: 4095 sit at
    ``at.y - offset``, 48 at ``at.y + offset`` and 576 at neither, the last
    two groups being fields an engineer moved. The iteration-20 QA review
    found this; the first version of this function added on both axes and
    was wrong by twice the offset on every field that had one.

    Measured on a corpus symbol KiCad placed at (227.33, 114.3): Footprint,
    Datasheet and Description, all hidden and all at offset (0, 0) in the
    library, sit at exactly (227.33, 114.3) in the file. Without the add every
    field landed at the sheet origin. A zero offset is the case where the two
    sign conventions agree, which is why it hid the fault above.

    **The offset is NOT rotated.** ``rotation`` moves the body, not the field
    offsets. That is KiCad's own behaviour: ``SCH_SYMBOL::Rotate``
    (``:2810-2826``) moves a field only by the symbol's own displacement, so
    turning a symbol on the spot leaves its field positions where they were.
    What moves a visible field after a turn in the GUI is the field autoplacer,
    which this injection operation does not run. Measured: a corpus ``power:GND``
    placed at 90 degrees carries ``fields_autoplaced`` set, and a corpus
    database part at 0 degrees does not. A caller that needs KiCad's tidy
    visible-field placement can call ``autoplace_fields`` for isolated placement.
    """
    moved = field.model_copy(deep=True)
    moved.position = (at[0] + field.position[0], at[1] - field.position[1])
    return moved


def inject_symbol(
    sch: Schematic,
    symbol: Symbol,
    *,
    at: tuple[float, float],
    rotation: float = 0.0,
    unit: int = 1,
    body_style: int = 1,
    reference: str | None = None,
    project_name: str | None = None,
    sheet_path: str | None = None,
    lib_nickname: str | None = None,
    lib_table: ResolvedLibTable | None = None,
) -> PlacedSymbol:
    """Register a symbol def and synthesize its placed instance (spec §2.4, §5).

    Deep-copies ``symbol``, applies the INJECT transform (Part-3 ".kicad_sym ->
    sch lib_symbols + placed instance"), appends the synthesized ``PlacedSymbol``
    to ``sch.symbols`` and returns it. Steps:

    - Step 1 REWRITE ``(symbol "Name")`` -> ``"Lib:Name"``: a bare incoming name
      takes ``lib_nickname`` when given, else the ``lib_table`` match, else the
      placeholder nickname ``"eedesigncore"`` (``SaveSymbol:365-382``,
      ADR-041 amendment 1, ADR-047).
    - Steps 2/5 REWRAP + REGISTER: register the def in ``sch.lib_symbols`` keyed by
      the full lib_id, with collision-rename (§5.2, ``sch_screen.cpp:179-263``).
    - Step 3 SYNTHESIZE the placed ``(symbol …)`` node (§5.1): ``lib_id`` (the
      resolved key), ``at``/``rotation``, ``unit``/``body_style``, the five bools
      copied from the def, a fresh instance ``(uuid)`` + per-pin uuids
      (schematic-side KIIDs, uuid-lifecycle Task 4), fields copied from the def
      with the Reference forced to ``reference``, and one ``(instances (project
      (path (reference)(unit))))`` entry. Every field position is moved to its
      ABSOLUTE sheet coordinate by adding ``at``, which is what KiCad's own
      constructor does (``sch_symbol.cpp:1408``, ``:1417``). The offset is not
      rotated; see :func:`_placed_field` for why, and for what KiCad does
      instead.

    ``lib_nickname`` mirrors ``inject_footprint`` (ADR-035): it is the library
    nickname prepended to a bare incoming name. A pre-qualified ``Lib:Name`` is
    left as-is.

    ``lib_table`` supplies the resolved ``sym-lib-table`` namespace from
    :func:`eedesigncore.resolve_lib_table`. A bare name then takes the
    nickname of the first discovered library that defines it (R4, ADR-047).
    Precedence: an explicit ``lib_nickname`` wins, the table match comes next,
    and the placeholder ``"eedesigncore"`` covers the no-match case. Passing
    an ``fp`` table raises ``ValueError``. ``project_name`` defaults to an
    existing placed symbol's project name (else empty); ``sheet_path`` defaults
    to the root path ``"/"``. The renamed key of a collision (§5.2) stays a bare
    ``<ItemName>_<n>`` regardless of the qualifying nickname.

    Reference auto-annotation (R2, ADR-046). When the caller omits
    ``reference``, this assigns ``<Prefix><n>``. The prefix comes from the
    library definition Reference field, through the KiCad rule
    ``UTIL::GetRefDesPrefix`` (``common/refdes_utils.cpp:35-44``). An empty
    prefix falls back to ``"U"`` (``eeschema/sch_symbol.cpp:716-720``). The
    number is the lowest free one from 1 up, counted over every reference
    already in ``sch``. This is library policy, not a copy of the KiCad
    annotator. It also sees one schematic file only (ADR-040), so it cannot
    detect a collision on another sheet of the same project. Pass ``reference``
    explicitly when the caller needs exact control.

    The resolved reference lands in both places KiCad writes it: the Reference
    field of the placed symbol and the ``(instances … (path … (reference …)))``
    entry.

    The incoming ``symbol`` is not mutated (a deep copy is registered).
    """
    if lib_table is not None and lib_table.kind != "sym":
        raise ValueError("inject_symbol needs a 'sym' lib table, got an 'fp' one")

    incoming = symbol.model_copy(deep=True)

    # Step 1 REWRITE bare "Name" -> "Lib:Name" (SaveSymbol:365-382). A bare name
    # takes the explicit nickname, else the resolved sym-lib-table match, else
    # the placeholder (R4, ADR-047).
    incoming.name = qualify_item_name(
        incoming.name, lib_nickname=lib_nickname, lib_table=lib_table
    )

    # Steps 2 + 5 REWRAP + REGISTER (+ collision-rename §5.2)
    resolved_key = _register_lib_def(sch, incoming)

    # Step 3 SYNTHESIZE the placed instance node (§5.1)
    fields = [_placed_field(f, at) for f in incoming.fields]
    if reference is None:
        reference = _auto_reference(sch, fields)
    for field in fields:
        if field.name == "Reference":
            field.value = reference
            break

    pins = [
        PlacedSymbolPin(number=pin.number, uuid=str(uuid4())) for pin in _collect_pins(incoming)
    ]

    if project_name is None:
        project_name = next((p.name for s in sch.symbols for p in s.instances if p.name), "")

    placed = PlacedSymbol(
        lib_id=resolved_key,
        position=at,
        angle=rotation,
        unit=unit,
        body_style=body_style,
        exclude_from_sim=incoming.exclude_from_sim,
        in_bom=incoming.in_bom,
        on_board=incoming.on_board,
        in_pos_files=incoming.in_pos_files,
        uuid=str(uuid4()),
        fields=fields,
        pins=pins,
        instances=[
            InstanceProject(
                name=project_name,
                paths=[
                    InstancePath(
                        path=sheet_path if sheet_path is not None else "/",
                        reference=reference,
                        unit=unit,
                    )
                ],
            )
        ],
    )

    sch.symbols.append(placed)
    return placed


def _reference_default(fields: list[Property]) -> str:
    """Return the Reference field value, or empty string if absent."""
    return next((f.value for f in fields if f.name == "Reference"), "")
