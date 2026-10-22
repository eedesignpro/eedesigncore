"""Turn a supplied database row into a symbol definition, the way KiCad does.

This is the half of a part database nobody but this library is positioned to
do: the transform is KiCad behaviour, cited to KiCad source, and checkable
against schematics KiCad itself wrote. The other half — connecting, querying,
and choosing which part — is the caller's, per ADR-100.

A row is a plain mapping from column name to value. That is the same shape an
ODBC driver hands back and the same shape a hand-written file hands back, so
every backend works and none of them appears in this code.

The port is ``SCH_IO_DATABASE::loadSymbolFromRow``
(``eeschema/sch_io/database/sch_io_database.cpp:449-700``), in KiCad's order:

1. Read the symbol column as a library id and load that definition. Duplicate
   it (``:455-483``).
2. No definition found, or an invalid id: build an EMPTY symbol carrying the
   metadata alone. KiCad shows the error in the chooser rather than refusing
   to place (``:485-494``).
3. Rename it to the display name and set the sub-library name (``:490-499``).
   The rename goes through :meth:`Symbol.renamed`, so the unit names carry the
   new prefix. KiCad composes a unit name on every save and so cannot get this
   wrong; this model stores it, so the rename must propagate.
4. Split the footprint column on ``;``, tab, carriage return and newline. The
   FIRST token becomes the Footprint field; EVERY token becomes a footprint
   filter (``:501-517``).
5. Apply the ``properties`` block: description, keywords, footprint filters,
   and the three exclude flags through a permissive boolean cast. A value that
   will not cast is ignored, not an error (``:519-592``, ``:400-447``).
6. Walk the field mappings (``:594-690``). A column absent from the row is
   skipped. A ``Footprint`` mapping onto the footprint column is skipped,
   because step 4 already handled it. ``Value`` and ``Datasheet`` write the
   built-in fields; anything else finds or creates a user field.
7. Sort the draw items (``:692``).

One flag is dropped with a reason. KiCad marks a field auto-added
(``SCH_FIELD::SetAutoAdded``), which decides whether an update from the library
may remove it again. That flag has no representation in a ``.kicad_sym`` or a
``.kicad_sch`` file — the sexpr writer never emits it — so a round-trip cannot
carry it and this port does not invent a field for it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Final

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.sch_family import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.library.dbl import DatabaseLibrary, DatabaseSubLibrary
from eedesigncore.library.records import (
    DATASHEET_FIELD,
    DESCRIPTION_FIELD,
    FOOTPRINT_FIELD,
    VALUE_FIELD,
    PartRecord,
)

RowValue = str | int | float | bool | None
Row = Mapping[str, RowValue]
"""One database row: column name to value. Every backend produces this shape."""

SymbolLoader = Callable[[str], Symbol | None]
"""Resolves a ``<nickname>:<name>`` library id to a definition, or ``None``."""

_FOOTPRINT_SPLIT: Final = re.compile(r"[;\t\r\n]+")
"""KiCad's footprint tokenizer (``sch_io_database.cpp:508``)."""

_TRUE_WORDS: Final = frozenset({"true", "yes", "y", "1"})
_FALSE_WORDS: Final = frozenset({"false", "no", "n", "0"})
"""``SCH_IO_DATABASE::boolFromAny`` (``sch_io_database.cpp:429-440``)."""

MAX_FOOTPRINT_TOKENS: Final = 256
"""Cap on the footprint filters one row may produce. KiCad has no cap.

A row value is untrusted: it comes from a backend this library does not own. A
cell holding megabytes of separators produced an unbounded filter list and an
unbounded output file. The real rows hold one or two tokens.
"""

MAX_ROW_VALUE_LENGTH: Final = 8192
"""Cap on the text one row value may write into a field. KiCad has no cap.

The longest real value measured is a datasheet URL of 190 characters. This
bounds a hostile cell without touching any real one, and mirrors
``search.MAX_TERM_LENGTH``.
"""


def bool_from_value(value: RowValue) -> bool | None:
    """Port ``boolFromAny`` (``sch_io_database.cpp:400-447``).

    A boolean passes through. An integer casts. A string is lowered and read
    against the true and the false word lists. Anything else, and an empty
    string, returns ``None``, which the caller treats as "not set" rather than
    as an error.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, float):
        return None
    if isinstance(value, str):
        text = value.strip().casefold()
        if not text:
            return None
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return None


def _text(value: RowValue) -> str:
    """Return a row value as text, truncated to :data:`MAX_ROW_VALUE_LENGTH`.

    Only a string casts, which is KiCad's rule: it reads a row value with
    ``std::any_cast<std::string>`` and an ODBC driver hands it a string for
    every column (``:620-628``). A caller supplying rows from a numeric backend
    must stringify them, and the type of :data:`RowValue` says so.
    """
    return value[:MAX_ROW_VALUE_LENGTH] if isinstance(value, str) else ""


def is_valid_lib_id(text: str) -> bool:
    """Report whether *text* is a usable library id (``lib_id.h:172-175``).

    ``LIB_ID::IsValid`` requires both halves to be non-empty, so a bare item
    name with no nickname is NOT valid. An id with an illegal character fails
    to parse (``lib_id.cpp:52-89``), and a colon in the item half is illegal,
    so exactly one colon is allowed.
    """
    nickname, separator, item = text.partition(":")
    if not separator or not nickname or not item:
        return False
    return ":" not in item and not any(char in item for char in '\t\n\r\\<>"')


def _split_footprints(value: str) -> list[str]:
    """Split the footprint column, capped at :data:`MAX_FOOTPRINT_TOKENS`."""
    tokens = [token for token in _FOOTPRINT_SPLIT.split(value) if token]
    return tokens[:MAX_FOOTPRINT_TOKENS]


def _field_index(symbol: Symbol) -> dict[str, Property]:
    """Index a symbol's fields by name, first sighting winning.

    Built once per symbol and threaded through the transform. A linear scan
    per mapping made building one symbol quadratic in the field-mapping
    count, which the iteration-20 security review measured at 50000
    mappings.
    """
    index: dict[str, Property] = {}
    for field in symbol.fields:
        index.setdefault(field.name, field)
    return index


def _set_field(
    index: dict[str, Property],
    symbol: Symbol,
    name: str,
    value: str,
    *,
    visible: bool | None = None,
    show_name: bool | None = None,
) -> None:
    field = index.get(name)
    if field is None:
        # TextAttributes() is KiCad's default text formatting, and a placed
        # field ALWAYS carries the block: SCH_FIELD allows multiline, so
        # EDA_TEXT::IsDefaultFormatting is false and saveField emits the
        # effects unconditionally (sch_io_kicad_sexpr.cpp:1021-1025,
        # eda_text.cpp:1054-1058). Measured across 200 corpus schematics:
        # 55338 placed fields carry an effects block and the 61 that do not
        # are all legacy demo files. Without this a new field wrote no
        # effects at all and every database field differed from KiCad.
        field = Property(name=name, value=value, visible=False, attributes=TextAttributes())
        symbol.fields.append(field)
        index[name] = field
    else:
        field.value = value
    if visible is not None:
        field.visible = visible
    if show_name is not None:
        field.show_name = show_name


class RowSymbol(KicadModel):
    """The definition built from one row, and what is known about its source.

    :func:`definition_from_row` returns the definition alone, which is enough
    when the row's symbol reference resolved. When it did not, the definition
    is a metadata-only symbol: correct fields, no pins, no body. KiCad does the
    same and shows the error in its chooser
    (``sch_io_database.cpp:485-494``), but a library has no chooser, so the
    outcome has to be reportable. A bodyless part placed into a schematic looks
    like a real part in the file and in KiCad's own tree.

    ``metadata_only`` is the flag to branch on. ``unresolved_reason`` says why,
    in words meant for a person.
    """

    definition: Symbol
    symbol_reference: str = ""
    source_found: bool = False
    metadata_only: bool = False
    has_pins: bool = False
    unresolved_reason: str = ""


def resolve_row(
    row: Row,
    sub_library: DatabaseSubLibrary,
    *,
    globally_unique_keys: bool = False,
    symbol_loader: SymbolLoader | None = None,
) -> RowSymbol:
    """Build the definition for one row AND report what happened to its source.

    Same transform as :func:`definition_from_row`, same arguments. Use this one
    whenever the caller is going to place the result, so it can refuse a
    bodyless part:

    .. code-block:: python

        built = resolve_row(row, resistors, symbol_loader=load)
        if built.metadata_only:
            raise SystemExit(f"will not place {built.definition.name}: "
                             f"{built.unresolved_reason}")
    """
    return _build_from_row(
        row,
        sub_library,
        globally_unique_keys=globally_unique_keys,
        symbol_loader=symbol_loader,
    )


def definition_from_row(
    row: Row,
    sub_library: DatabaseSubLibrary,
    *,
    globally_unique_keys: bool = False,
    symbol_loader: SymbolLoader | None = None,
    require_source: bool = False,
) -> Symbol:
    """Build the symbol definition KiCad would build from one row.

    ``row`` must carry the key column; a row without it is what KiCad skips
    outright (``sch_io_database.cpp:254-256``), so this raises
    :class:`ValueError` rather than guessing a name.

    ``symbol_loader`` resolves the row's symbol reference to a definition.
    Omitted, or returning ``None``, yields the metadata-only symbol KiCad
    yields, which is placeable and shows its error in the chooser
    (``:485-494``). :func:`eedesigncore.library.catalog.table_symbol_loader`
    builds one from a resolved ``sym-lib-table``.

    ``require_source`` raises :class:`ValueError` instead of returning a
    metadata-only symbol. Leave it false for KiCad's behaviour. Set it true, or
    use :func:`resolve_row`, when the result is going to be placed: a bodyless
    part in a schematic looks like a real one and nothing later will catch it.
    """
    built = _build_from_row(
        row,
        sub_library,
        globally_unique_keys=globally_unique_keys,
        symbol_loader=symbol_loader,
    )
    if require_source and built.metadata_only:
        raise ValueError(
            f"row {built.definition.name!r}: {built.unresolved_reason}. "
            "Pass require_source=False to accept a metadata-only symbol, or use "
            "resolve_row to inspect the outcome."
        )
    return built.definition


def _build_from_row(
    row: Row,
    sub_library: DatabaseSubLibrary,
    *,
    globally_unique_keys: bool = False,
    symbol_loader: SymbolLoader | None = None,
) -> RowSymbol:
    raw_key = row.get(sub_library.key)
    if raw_key is None:
        raise ValueError(f"row carries no key column {sub_library.key!r}")

    name = sub_library.display_name(
        _text(raw_key) or str(raw_key), globally_unique_keys=globally_unique_keys
    )

    symbol: Symbol | None = None
    reference_text = _text(row.get(sub_library.symbols))
    reason = ""

    if sub_library.symbols not in row:
        reason = f"the row carries no {sub_library.symbols!r} column, so no symbol was drawn"
    elif not is_valid_lib_id(reference_text):
        reason = (
            f"the symbol reference {reference_text!r} is not a valid library id, so KiCad "
            "would draw no symbol either"
        )
    elif symbol_loader is None:
        reason = (
            f"no symbol loader was given, so {reference_text!r} was never looked up. Pass "
            "symbol_loader=table_symbol_loader(resolve_lib_table('sym'))"
        )
    else:
        loaded = symbol_loader(reference_text)
        if loaded is None:
            reason = (
                f"the symbol loader found nothing for {reference_text!r}. Either the library "
                "holds no such definition, or its nickname is missing from the resolved "
                "sym-lib-table. Check ResolvedLibTable.skipped for a library the read "
                "confinement dropped"
            )
        else:
            symbol = loaded.model_copy(deep=True)

    # Symbol.renamed, never `symbol.name = name`: a unit name carries the parent's
    # item name as its prefix, and KiCad refuses the whole schematic when it does
    # not match (sch_io_kicad_sexpr_parser.cpp:501).
    symbol = Symbol.new(name) if symbol is None else symbol.renamed(name)
    index = _field_index(symbol)

    footprints: list[str] = []
    if sub_library.footprints in row:
        footprints = _split_footprints(_text(row.get(sub_library.footprints)))
        if footprints:
            _set_field(index, symbol, FOOTPRINT_FIELD, footprints[0])

    properties = sub_library.properties

    if properties.description and properties.description in row:
        _set_field(index, symbol, DESCRIPTION_FIELD, _text(row[properties.description]))

    if properties.keywords and properties.keywords in row:
        symbol.keywords = _text(row[properties.keywords])

    filters = list(footprints)
    if properties.footprint_filters and properties.footprint_filters in row:
        filters.append(_text(row[properties.footprint_filters]))
    symbol.fp_filters = filters

    if properties.exclude_from_sim and properties.exclude_from_sim in row:
        flag = bool_from_value(row[properties.exclude_from_sim])
        if flag is not None:
            symbol.exclude_from_sim = flag

    if properties.exclude_from_board and properties.exclude_from_board in row:
        flag = bool_from_value(row[properties.exclude_from_board])
        if flag is not None:
            symbol.on_board = not flag

    if properties.exclude_from_bom and properties.exclude_from_bom in row:
        flag = bool_from_value(row[properties.exclude_from_bom])
        if flag is not None:
            symbol.in_bom = not flag

    for mapping in sub_library.fields:
        if mapping.column not in row:
            continue
        if mapping.name == FOOTPRINT_FIELD and mapping.column == sub_library.footprints:
            continue

        value = _text(row[mapping.column])
        existing = index.get(mapping.name)
        apply_flags = not mapping.inherit_properties or existing is None

        _set_field(
            index,
            symbol,
            mapping.name,
            value,
            visible=mapping.visible_on_add if apply_flags else None,
            show_name=mapping.show_name if apply_flags else None,
        )

    # KiCad also records the source library id on LIB_SYMBOL::m_sourceLibId
    # (:474). That is a runtime member with no file token, so it is not carried
    # here; the row still holds it in the symbol column.
    return RowSymbol(
        definition=symbol,
        symbol_reference=reference_text,
        source_found=not reason,
        metadata_only=bool(reason),
        has_pins=any(isinstance(item, Pin) for unit in symbol.units for item in unit.items),
        unresolved_reason=reason,
    )


def record_from_row(
    row: Row,
    sub_library: DatabaseSubLibrary,
    *,
    nickname: str = "",
    globally_unique_keys: bool = False,
) -> PartRecord:
    """Build a searchable record from one row, with no symbol load.

    This is the cheap path, and it is the one a search wants: it reads the row
    only, so ranking a whole catalog never touches a symbol library. The
    fields are the mapped columns, in mapping order, with the built-in five
    resolved the way :func:`definition_from_row` resolves them.

    ``nickname`` is the library nickname the ``Database`` row carries in the
    ``sym-lib-table``, which is what a placed ``lib_id`` uses.
    """
    raw_key = row.get(sub_library.key)
    if raw_key is None:
        raise ValueError(f"row carries no key column {sub_library.key!r}")

    name = sub_library.display_name(
        _text(raw_key) or str(raw_key), globally_unique_keys=globally_unique_keys
    )

    fields: dict[str, str] = {}
    footprints: list[str] = []

    if sub_library.footprints in row:
        footprints = _split_footprints(_text(row[sub_library.footprints]))
        if footprints:
            fields[FOOTPRINT_FIELD] = footprints[0]

    properties = sub_library.properties
    if properties.description and properties.description in row:
        fields[DESCRIPTION_FIELD] = _text(row[properties.description])

    keywords = ""
    if properties.keywords and properties.keywords in row:
        keywords = _text(row[properties.keywords])

    filters = list(footprints)
    if properties.footprint_filters and properties.footprint_filters in row:
        filters.append(_text(row[properties.footprint_filters]))

    for mapping in sub_library.fields:
        if mapping.column not in row:
            continue
        if mapping.name == FOOTPRINT_FIELD and mapping.column == sub_library.footprints:
            continue
        fields[mapping.name] = _text(row[mapping.column])

    fields.setdefault(VALUE_FIELD, "")
    fields.setdefault(DATASHEET_FIELD, "")

    return PartRecord(
        name=name,
        nickname=nickname,
        fields=fields,
        keywords=keywords,
        footprint_filters=filters,
        source="database",
        sub_library=sub_library.name,
        source_path=_text(row.get(sub_library.symbols)),
    )


def database_records(
    library: DatabaseLibrary,
    rows_by_sub_library: Mapping[str, Iterable[Row]],
    *,
    nickname: str = "",
) -> Iterator[PartRecord]:
    """Yield a record per row, for rows a caller has already read.

    ``rows_by_sub_library`` is keyed by sub-library display name, which is the
    ``name`` of each ``libraries`` entry and the key of
    :meth:`DatabaseDeclaration.table_for`. A key naming no sub-library is
    ignored, and a row missing the key column is skipped rather than raising,
    which is what KiCad does when it walks a table (``:254-256``).
    """
    for entry in library.libraries:
        rows = rows_by_sub_library.get(entry.name)
        if rows is None:
            continue
        for row in rows:
            if row.get(entry.key) is None:
                continue
            yield record_from_row(
                row,
                entry,
                nickname=nickname,
                globally_unique_keys=library.globally_unique_keys,
            )
