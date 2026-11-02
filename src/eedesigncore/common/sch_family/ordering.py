"""Canonical save order for the SCH-family ``Symbol`` sub-grammar.

KiCad re-sorts the draw items of every symbol unit on every save, through a
``std::multiset`` keyed on ``SCH_ITEM::operator<``
(``sch_io_kicad_sexpr_lib_cache.cpp:507-520``). Stored order never reaches the
file. This module ports that rule so a MUTATED model writes the same sibling
order KiCad would write. Audit: ``context/audit/symbol-save-ordering-2026-08-13.md``
sections 5, 6, 8 and 13.3.

Two traps govern the port.

*Coordinates.* KiCad compares INTERNAL coordinates, and the library-symbol
writer negates Y on output (``sch_io_kicad_sexpr_common.cpp:267-285``). Our
models store FILE-space Y, so every position key here negates Y before it
compares. Descending file Y is ascending internal Y.

*Ties.* KiCad breaks a final tie on a runtime UUID that the symbol file never
stores (audit C-3). Every sort below is therefore STABLE, and tied items keep
their encounter order. That case is a KiCad non-determinism, not a defect here.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cmp_to_key
from typing import Any

from eedesigncore.common.kimath import calc_arc_center_iu, int32, kiround
from eedesigncore.common.models import Property
from eedesigncore.common.ordering import FILL_RANK, LINE_STYLE_RANK, str_num_cmp
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.common.sch_family.text import SymText, SymTextBox

# Schematic internal unit: 1 IU = 100 nm (``include/base_units.h:72``).
SCH_IU_PER_MM = 1e4

# ``EDA_SHAPE::Compare`` tolerance in internal units (``common/eda_shape.cpp:2451``).
_SHAPE_EPSILON = 2

# Draw-item rank, from the ``KICAD_T`` order at ``include/core/typeinfo.h:152-157``.
# ``SCH_FIELD_T`` sits between shapes and text in that enum, but fields never
# reach a unit block (``eeschema/lib_symbol.cpp:1967-1968, 1987-1988``).
_RANK_SHAPE = 0
_RANK_TEXT = 1
_RANK_TEXTBOX = 2
_RANK_PIN = 3
_RANK_UNKNOWN = 4

# ``SHAPE_T`` order (``include/eda_shape.h:44-53``).
_SHAPE_KIND_RECTANGLE = 1
_SHAPE_KIND_ARC = 2
_SHAPE_KIND_CIRCLE = 3
_SHAPE_KIND_POLY = 4
_SHAPE_KIND_BEZIER = 5

# ``ELECTRICAL_PINTYPE`` and ``GRAPHIC_PINSHAPE`` (``common/pin_type.h:35-97``).
# Both Python enums are declared in the KiCad enum order.
_PIN_TYPE_RANK: dict[PinElectricalType, int] = {
    member: index for index, member in enumerate(PinElectricalType)
}
_PIN_SHAPE_RANK: dict[PinShape, int] = {member: index for index, member in enumerate(PinShape)}

# ``PIN_ORIENTATION`` (``common/pin_type.h:104-137``), keyed by the file angle the
# parser maps to it (``sch_io_kicad_sexpr_parser.cpp:1672-1676``).
_PIN_ORIENTATION_RANK: dict[int, int] = {0: 0, 180: 1, 90: 2, 270: 3}

# Mandatory field ids from ``FIELD_T`` (``include/template_fieldnames.h:43-61``),
# keyed by the canonical field name folded for case-insensitive lookup.
_MANDATORY_FIELD_ORDINAL: dict[str, int] = {
    "reference": 1,
    "value": 2,
    "footprint": 3,
    "datasheet": 4,
    "description": 5,
}

# First ordinal a user field can take (``eeschema/sch_field.h:361``).
_USER_FIELD_ORDINAL_BASE = 42


# ---------------------------------------------------------------------------
# Internal-unit helpers
# ---------------------------------------------------------------------------


def to_iu(millimetres: float) -> int:
    """Convert a millimetre value to schematic internal units."""
    return kiround(millimetres * SCH_IU_PER_MM)


def x_iu(file_x: float) -> int:
    """Convert a file-space X in millimetres to internal units."""
    return to_iu(file_x)


def y_iu(file_y: float) -> int:
    """Convert a file-space Y in millimetres to internal units, negating it.

    The library-symbol writer emits ``-y`` (``sch_io_kicad_sexpr_lib_cache.cpp:619-621``),
    so internal Y is the negated file Y. Every position key depends on this.
    """
    return to_iu(-file_y)


def _point_iu(point: tuple[float, float]) -> tuple[int, int]:
    return (x_iu(point[0]), y_iu(point[1]))


# ---------------------------------------------------------------------------
# Arc centre (the position key of an arc)
# ---------------------------------------------------------------------------


def arc_center_iu(arc: SchArc) -> tuple[int, int]:
    """Return the internal-unit centre of *arc*, which is its position key.

    ``EDA_SHAPE::getPosition`` returns the arc centre for an arc
    (``common/eda_shape.cpp:432-440``), and ``SetArcGeometry`` derives that centre
    from start, mid and end (``common/eda_shape.cpp:1185``).
    """
    start = _point_iu(arc.start)
    mid = _point_iu(arc.mid)
    end = _point_iu(arc.end)
    return calc_arc_center_iu(start, mid, end)


# ---------------------------------------------------------------------------
# Item classification
# ---------------------------------------------------------------------------

_SHAPE_TYPES = (SchArc, SchCircle, SchRect, SchBezier, SchPolyline)


def _type_rank(item: Any) -> int:
    if isinstance(item, _SHAPE_TYPES):
        return _RANK_SHAPE
    if isinstance(item, SymText):
        return _RANK_TEXT
    if isinstance(item, SymTextBox):
        return _RANK_TEXTBOX
    if isinstance(item, Pin):
        return _RANK_PIN
    return _RANK_UNKNOWN


def _is_private(item: Any) -> bool:
    """Report the private flag, which sorts last (``eeschema/sch_item.cpp:717-718``).

    A ``Pin`` carries no private flag, because the symbol pin grammar has no
    ``private`` token.
    """
    return bool(getattr(item, "private", False))


def internal_position(item: Any) -> tuple[int, int]:
    """Return the internal-unit position key of *item*.

    Mirrors ``EDA_SHAPE::getPosition`` (``common/eda_shape.cpp:432-440``): the
    centre for an arc, the first vertex for a polygon, and the start point
    otherwise.
    """
    if isinstance(item, SchArc):
        return arc_center_iu(item)
    if isinstance(item, SchCircle):
        return _point_iu(item.center)
    if isinstance(item, SchRect):
        return _point_iu(item.start)
    if isinstance(item, SchBezier):
        return _point_iu(item.pts[0]) if item.pts else (0, 0)
    if isinstance(item, SchPolyline):
        return _point_iu(item.pts[0]) if item.pts else (0, 0)
    if isinstance(item, SymText | SymTextBox | Pin):
        return _point_iu(item.position)
    return (0, 0)


def _shape_kind(item: Any) -> int:
    if isinstance(item, SchRect | SymTextBox):
        return _SHAPE_KIND_RECTANGLE
    if isinstance(item, SchArc):
        return _SHAPE_KIND_ARC
    if isinstance(item, SchCircle):
        return _SHAPE_KIND_CIRCLE
    if isinstance(item, SchPolyline):
        return _SHAPE_KIND_POLY
    return _SHAPE_KIND_BEZIER


def _shape_start_iu(item: Any) -> tuple[int, int]:
    """Return ``EDA_SHAPE::m_start`` in internal units.

    ``SetPolyPoints`` never touches ``m_start`` (``common/eda_shape.cpp:1947-1954``),
    so a polygon reports the origin here and discriminates on its vertices later.
    """
    if isinstance(item, SchArc):
        return _point_iu(item.start)
    if isinstance(item, SchCircle):
        return _point_iu(item.center)
    if isinstance(item, SchRect):
        return _point_iu(item.start)
    if isinstance(item, SchBezier):
        return _point_iu(item.pts[0]) if item.pts else (0, 0)
    if isinstance(item, SymTextBox):
        return _point_iu(item.position)
    return (0, 0)


def _shape_end_iu(item: Any) -> tuple[int, int]:
    """Return ``EDA_SHAPE::m_end`` in internal units.

    A circle stores ``m_end = m_start + (radius, 0)`` (``include/eda_shape.h:253-257``).
    A text box stores ``m_end = position + size``, where the parser reads the size
    with Y inversion (``sch_io_kicad_sexpr_parser.cpp:2068, 2103-2106``).
    """
    if isinstance(item, SchArc):
        return _point_iu(item.end)
    if isinstance(item, SchCircle):
        center = _point_iu(item.center)
        return (center[0] + to_iu(item.radius), center[1])
    if isinstance(item, SchRect):
        return _point_iu(item.end)
    if isinstance(item, SchBezier):
        return _point_iu(item.pts[3]) if len(item.pts) > 3 else (0, 0)
    if isinstance(item, SymTextBox):
        start = _point_iu(item.position)
        return (start[0] + x_iu(item.size[0]), start[1] + y_iu(item.size[1]))
    return (0, 0)


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------


def _cmp_e(first: int, second: int) -> int:
    """Compare with the ``EDA_SHAPE::Compare`` tolerance (``TEST_E``)."""
    return int32(first - second) if abs(first - second) > _SHAPE_EPSILON else 0


def _cmp_point(first: tuple[int, int], second: tuple[int, int]) -> int:
    """Compare two internal points with the ``TEST_PT`` tolerance."""
    result = _cmp_e(first[0], second[0])
    if result:
        return result
    return _cmp_e(first[1], second[1])


def _compare_eda_shape(first: Any, second: Any) -> int:
    """Port of ``EDA_SHAPE::Compare`` (``common/eda_shape.cpp:2449-2491``).

    The flattened bezier point list is not compared, because it is derived state
    that the file never stores. Two beziers with equal control points cannot
    differ in it.
    """
    result = _cmp_point(_shape_start_iu(first), _shape_start_iu(second))
    if result:
        return result

    result = _cmp_point(_shape_end_iu(first), _shape_end_iu(second))
    if result:
        return result

    kind_first = _shape_kind(first)
    kind_second = _shape_kind(second)
    if kind_first != kind_second:
        return kind_first - kind_second

    if kind_first == _SHAPE_KIND_RECTANGLE:
        radius_first = to_iu(getattr(first, "corner_radius", None) or 0.0)
        radius_second = to_iu(getattr(second, "corner_radius", None) or 0.0)
        if radius_first != radius_second:
            return int32(radius_first - radius_second)
    elif kind_first == _SHAPE_KIND_ARC:
        result = _cmp_point(_point_iu(first.mid), _point_iu(second.mid))
        if result:
            return result
    elif kind_first == _SHAPE_KIND_BEZIER:
        result = _cmp_point(_point_iu(first.pts[1]), _point_iu(second.pts[1]))
        if result:
            return result
        result = _cmp_point(_point_iu(first.pts[2]), _point_iu(second.pts[2]))
        if result:
            return result
    elif kind_first == _SHAPE_KIND_POLY:
        if len(first.pts) != len(second.pts):
            return len(first.pts) - len(second.pts)
        for vertex_first, vertex_second in zip(first.pts, second.pts, strict=True):
            result = _cmp_point(_point_iu(vertex_first), _point_iu(vertex_second))
            if result:
                return result

    result = _cmp_e(to_iu(first.stroke.width), to_iu(second.stroke.width))
    if result:
        return result

    style_first = LINE_STYLE_RANK[first.stroke.type]
    style_second = LINE_STYLE_RANK[second.stroke.type]
    if style_first != style_second:
        return style_first - style_second

    fill_first = FILL_RANK[first.fill.type]
    fill_second = FILL_RANK[second.fill.type]
    return fill_first - fill_second


def _alternate_key(alternate: PinAlternate) -> str:
    return alternate.name


def _compare_pins(first: Pin, second: Pin, *, legacy: bool = False) -> int:
    """Port of ``SCH_PIN::compare`` for a library pin (``eeschema/sch_pin.cpp:1725-1818``).

    The number comparison returns its result even when it is zero, exactly as the
    C++ does. So ``"01"`` and ``"1"`` compare equal and never reach the position
    keys.

    *legacy* is the KiCad 9 order (9.0.9 ``eeschema/sch_pin.cpp:1889-1908``):
    there ``SCH_PIN::compare`` does not pass ``SKIP_TST_POS``, so
    ``SCH_ITEM::compare`` ranks the pins on position (x, then internal y)
    BEFORE the number. KiCad 10 added the flag and moved position after the
    number.
    """
    if legacy:
        position_first = _point_iu(first.position)
        position_second = _point_iu(second.position)
        if position_first[0] != position_second[0]:
            return int32(position_first[0] - position_second[0])
        if position_first[1] != position_second[1]:
            return int32(position_first[1] - position_second[1])

    if first.number != second.number:
        return str_num_cmp(first.number, second.number)

    position_first = _point_iu(first.position)
    position_second = _point_iu(second.position)
    if position_first[0] != position_second[0]:
        return int32(position_first[0] - position_second[0])
    if position_first[1] != position_second[1]:
        return int32(position_first[1] - position_second[1])

    length_first = to_iu(first.length)
    length_second = to_iu(second.length)
    if length_first != length_second:
        return int32(length_first - length_second)

    orientation_first = _PIN_ORIENTATION_RANK.get(int(first.angle) % 360, 0)
    orientation_second = _PIN_ORIENTATION_RANK.get(int(second.angle) % 360, 0)
    if orientation_first != orientation_second:
        return orientation_first - orientation_second

    shape_first = _PIN_SHAPE_RANK[first.shape]
    shape_second = _PIN_SHAPE_RANK[second.shape]
    if shape_first != shape_second:
        return shape_first - shape_second

    type_first = _PIN_TYPE_RANK[first.electrical_type]
    type_second = _PIN_TYPE_RANK[second.electrical_type]
    if type_first != type_second:
        return type_first - type_second

    hidden_first = int(not first.visible)
    hidden_second = int(not second.visible)
    if hidden_first != hidden_second:
        return hidden_first - hidden_second

    number_size_first = to_iu(first.number_text_size)
    number_size_second = to_iu(second.number_text_size)
    if number_size_first != number_size_second:
        return int32(number_size_first - number_size_second)

    name_size_first = to_iu(first.name_text_size)
    name_size_second = to_iu(second.name_text_size)
    if name_size_first != name_size_second:
        return int32(name_size_first - name_size_second)

    if len(first.alternates) != len(second.alternates):
        return len(first.alternates) - len(second.alternates)

    # KiCad holds alternates in a std::map keyed by name, so it walks them in
    # name order rather than file order.
    alternates_first = sorted(first.alternates, key=_alternate_key)
    alternates_second = sorted(second.alternates, key=_alternate_key)
    for alternate_first, alternate_second in zip(alternates_first, alternates_second, strict=True):
        if alternate_first.name != alternate_second.name:
            return -1 if alternate_first.name < alternate_second.name else 1
        alt_type_first = _PIN_TYPE_RANK[alternate_first.electrical_type]
        alt_type_second = _PIN_TYPE_RANK[alternate_second.electrical_type]
        if alt_type_first != alt_type_second:
            return alt_type_first - alt_type_second
        alt_shape_first = _PIN_SHAPE_RANK[alternate_first.shape]
        alt_shape_second = _PIN_SHAPE_RANK[alternate_second.shape]
        if alt_shape_first != alt_shape_second:
            return alt_shape_first - alt_shape_second

    return 0


def compare_draw_items(first: Any, second: Any, *, legacy: bool = False) -> int:
    """Compare two draw items as ``SCH_ITEM::operator<`` orders them.

    Key order (``eeschema/sch_item.cpp:691-742``): type rank, then the private
    flag, then per-type keys. Unit and body style are not compared, because a
    unit block holds one unit and one body style by construction
    (``eeschema/lib_symbol.cpp:1981-2014``).
    """
    rank_first = _type_rank(first)
    rank_second = _type_rank(second)
    if rank_first != rank_second:
        return rank_first - rank_second

    private_first = _is_private(first)
    private_second = _is_private(second)
    if private_first != private_second:
        return 1 if private_first else -1

    if rank_first == _RANK_PIN:
        return _compare_pins(first, second, legacy=legacy)

    if rank_first == _RANK_UNKNOWN:
        return 0

    position_first = internal_position(first)
    position_second = internal_position(second)
    if position_first[0] != position_second[0]:
        return int32(position_first[0] - position_second[0])
    if position_first[1] != position_second[1]:
        return int32(position_first[1] - position_second[1])

    if rank_first == _RANK_TEXT:
        # C-3: KiCad breaks this tie on a UUID the symbol file never stores.
        return 0

    return _compare_eda_shape(first, second)


# ---------------------------------------------------------------------------
# Public sort entry points
# ---------------------------------------------------------------------------


def sort_draw_items(items: list[Any], *, legacy: bool = False) -> list[Any]:
    """Return the draw items of one unit block in KiCad save order.

    Mirrors the ``std::multiset`` at ``sch_io_kicad_sexpr_lib_cache.cpp:507-520``.
    The sort is stable, so items that tie on every persisted key keep their
    encounter order (audit C-3). *legacy* selects the KiCad 9 pin order.
    """

    def _compare(first: Any, second: Any) -> int:
        return compare_draw_items(first, second, legacy=legacy)

    return sorted(items, key=cmp_to_key(_compare))


def sort_units(units: list[SymbolUnit]) -> list[SymbolUnit]:
    """Return the unit blocks in KiCad save order: unit index, then body style.

    Source: ``sch_io_kicad_sexpr_lib_cache.cpp:479-487``.
    """
    return sorted(units, key=lambda unit: (unit.unit_index, unit.body_style_index))


def field_ordinal(name: str, user_index: int) -> int:
    """Return the ordinal KiCad assigns to a symbol property.

    ``SCH_FIELD::GetOrdinal`` returns the mandatory field id for a mandatory
    field and the stored ordinal otherwise (``eeschema/sch_field.h:129-132``).
    The five mandatory ids are 1 to 5 (``include/template_fieldnames.h:43-61``),
    and a user field starts at 42 and counts up in file order
    (``eeschema/sch_field.h:361-366``). So the mandatory five always outrank user
    fields, wherever the file put them. The name match is case-insensitive, as
    the parser's ``CmpNoCase`` is (``sch_io_kicad_sexpr_parser.cpp:1082-1089``).
    """
    mandatory = _MANDATORY_FIELD_ORDINAL.get(name.casefold())
    if mandatory is not None:
        return mandatory
    return _USER_FIELD_ORDINAL_BASE + user_index


def sort_symbol_fields(fields: list[Property]) -> list[Property]:
    """Return the symbol properties in ordinal order.

    ``LIB_SYMBOL::GetFields`` sorts on the field ordinal
    (``eeschema/lib_symbol.cpp:1510-1530``). The grammar stores no ordinal token,
    so the ordinal comes from the field name and the file position, per
    :func:`field_ordinal`. A KiCad-written file already lists the mandatory five
    first, so this sort is the identity on it.
    """
    ordinals: list[int] = []
    user_index = 0
    for field in fields:
        ordinal = field_ordinal(field.name, user_index)
        if ordinal >= _USER_FIELD_ORDINAL_BASE:
            user_index += 1
        ordinals.append(ordinal)

    order = sorted(range(len(fields)), key=lambda index: (ordinals[index], index))
    return [fields[index] for index in order]


_SLASH_ESCAPE = "{slash}"


def unescape_slash(name: str) -> str:
    """Reverse the legacy ``{slash}`` escape in a symbol or ``extends`` name.

    KiCad applies exactly this replacement, and only this one, to a top-level
    symbol name (``sch_io_kicad_sexpr_parser.cpp:343``) and to an ``extends``
    value (``:477``), before either takes part in parent resolution. ``/`` stopped
    being an illegal ``LIB_ID`` character, so the escape survives only in legacy
    files.
    """
    return name.replace(_SLASH_ESCAPE, "/")


def parent_map_key(name: str) -> str:
    """Return the key KiCad stores *name* under in its parent-resolution map.

    KiCad unescapes ``{slash}``, parses the result as a ``LIB_ID``, and keys the
    map on ``LIB_ID::GetLibItemName`` (``sch_io_kicad_sexpr_parser.cpp:343,364,254``).
    So the library nickname is discarded on the KEY side. This library keeps the
    file's text verbatim in ``Symbol.name`` (ADR-037), so the reduction happens
    here instead.
    """
    return unescape_slash(name).rsplit(":", 1)[-1]


def build_bare_name_index(by_name: Mapping[str, Symbol]) -> dict[str, Symbol | None]:
    """Index *by_name* under :func:`parent_map_key`, mapping a collision to None.

    None covers both no match and more than one, which is what every caller
    needs. This library declines to guess on an ambiguous bare name where KiCad
    takes the last definition in file order (audit 16.1.7, divergence D3); both
    are unreachable on a KiCad-written file, where the key is already bare.
    """
    index: dict[str, Symbol | None] = {}
    for key, value in by_name.items():
        bare = parent_map_key(key)
        index[bare] = None if bare in index else value
    return index


def resolve_parent_symbol(
    symbol: Symbol,
    by_name: Mapping[str, Symbol],
    bare_names: Mapping[str, Symbol | None] | None = None,
) -> Symbol | None:
    """Return the symbol *symbol* extends, or None when it resolves to nothing.

    The single parent-resolution rule of this library, shared by the writer's
    keyword and filter fallback and by :func:`inheritance_depth`. It tracks
    ``SCH_IO_KICAD_SEXPR_LIB_CACHE::updateParentSymbolLinks``
    (``sch_io_kicad_sexpr_lib_cache.cpp:850-870``): the ``extends`` value is
    unescaped and then matched VERBATIM, never reduced, because KiCad never
    parses it as a ``LIB_ID`` (``sch_io_kicad_sexpr_parser.cpp:462-482``). Only
    the key side reduces, through :func:`parent_map_key`. The compare is
    case-sensitive, as the ``std::map`` key compare is
    (``symbol_library_common.h:42-48``).

    An exact key match wins first, which is the identity on a ``.kicad_sym``
    whose keys are already bare. Failing that, a UNIQUE reduced-key match wins.
    An ambiguous or absent parent resolves to None and the caller applies no
    fallback; KiCad raises ``THROW_IO_ERROR`` there
    (``sch_io_kicad_sexpr_lib_cache.cpp:859-866``).

    Pass *bare_names* to reuse a :func:`build_bare_name_index` result across
    calls; omit it and the index is rebuilt per call.
    """
    if not symbol.extends:
        return None

    target = unescape_slash(symbol.extends)
    exact = by_name.get(target)
    if exact is not None:
        return exact

    index = build_bare_name_index(by_name) if bare_names is None else bare_names
    return index.get(target)


def inheritance_depth(
    symbol: Symbol,
    by_name: dict[str, Symbol],
    bare_names: Mapping[str, Symbol | None] | None = None,
) -> int:
    """Return how many ``extends`` hops separate *symbol* from its root.

    Port of ``LIB_SYMBOL::GetInheritanceDepth`` (``eeschema/lib_symbol.cpp:394-420``),
    including its cycle guard. An unresolved parent ends the walk, which matches
    the C++ behaviour for a parent pointer that never resolved. Resolution goes
    through :func:`resolve_parent_symbol`, the one rule; KiCad's own depth walk
    follows the ``m_parent`` pointers that ``updateParentSymbolLinks`` set, so the
    depth sort and the writer's keyword fallback must agree on what a parent is.

    Pass *bare_names* to share one :func:`build_bare_name_index` across a whole
    sort. Omit it and the index is built here, which costs a pass over *by_name*
    per call.
    """
    index = build_bare_name_index(by_name) if bare_names is None else bare_names
    depth = 0
    visited = {id(symbol)}
    parent = resolve_parent_symbol(symbol, by_name, index)

    while parent is not None:
        if id(parent) in visited:
            break
        visited.add(id(parent))
        depth += 1
        parent = resolve_parent_symbol(parent, by_name, index)

    return depth


def sort_library_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """Return library symbols in KiCad save order: inheritance depth, then name.

    Source: ``sch_io_kicad_sexpr_lib_cache.cpp:249-262``, whose comment reads
    "Library must be ordered by inheritance depth." The name comparison is a
    plain ``wxString`` compare, not the natural order of ``str_num_cmp``.

    The reduced-key index is built once here and shared by every depth walk, so
    the sort stays linear in the symbol count rather than quadratic (ADR-077).
    """
    by_name = {symbol.name: symbol for symbol in symbols}
    bare_names = build_bare_name_index(by_name)
    return sorted(
        symbols,
        key=lambda symbol: (inheritance_depth(symbol, by_name, bare_names), symbol.name),
    )


def sort_schematic_lib_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """Return the ``(lib_symbols …)`` entries of a schematic in save order.

    A schematic holds its library symbols in a
    ``std::map<wxString, LIB_SYMBOL*>`` (``eeschema/sch_screen.h:497,710``) whose
    key is the full lib id text, ``LIB_ID::Format()``
    (``eeschema/sch_screen.cpp:1452``). The writer iterates that map
    (``sch_io_kicad_sexpr.cpp:430-431``), so entries emit by lib id ascending.

    The comparison is a plain ``wxString`` compare. It is NOT the natural order
    of :func:`str_num_cmp`, and it is NOT case insensitive. The sweep report
    (section 3.2) proves both against a KiCad-written file:
    ``0-http:JLCPCB_C15195`` emits before ``0-http:JLCPCB_C1546``, and
    ``Sensor_Energy:…`` emits before ``power:…``.

    This rule differs from :func:`sort_library_symbols`, which orders a
    ``.kicad_sym`` library by inheritance depth first. A schematic never sorts on
    inheritance.
    """
    return sorted(symbols, key=lambda symbol: symbol.name)


def kiid_path_elements(path: str) -> tuple[str, ...]:
    """Split a canonical sheet path such as ``"/uuid/uuid"`` into its KIIDs.

    ``KIID_PATH::AsString`` joins each KIID with a leading ``/``
    (``common/kiid.cpp:365-373``), so an empty leading segment is not an element.
    """
    return tuple(element for element in path.split("/") if element)


def kiid_path_sort_key(path: str) -> tuple[int, tuple[str, ...]]:
    """Return the sort key of a sheet path, matching ``KIID_PATH::operator<``.

    ``include/kiid.h:189-203`` compares the element COUNT first and only then
    walks the elements:

    .. code-block:: cpp

        if( size() != rhs.size() )
            return size() < rhs.size();

    So a shorter path always sorts first, whatever its content. That is NOT a
    plain string compare of the path text, and the two orders genuinely differ:
    ``/1bbb…`` sorts before ``/0aaa…/2ccc…`` under KiCad and after it under a
    string compare. The sweep report claimed the two agree (section 3.3), which
    this key corrects.

    Element comparison stays a string compare, because ``KIID::operator<``
    compares the 16 raw bytes (``include/kiid.h:129-132``) and
    ``KIID::AsString`` renders those bytes as fixed-width lowercase hex
    (``common/kiid.cpp:244-247``), so the two orders agree element by element.
    """
    elements = kiid_path_elements(path)
    return (len(elements), elements)


def resolve_symbol_definition(
    symbol: Symbol, symbols: Mapping[str, Symbol]
) -> tuple[Symbol, list[Property]]:
    chain = [symbol]
    seen = {id(symbol)}
    while chain[-1].extends:
        parent = resolve_parent_symbol(chain[-1], symbols)
        if parent is None:
            raise ValueError(f"Supply the parent symbol in memory: {chain[-1].extends}")
        if id(parent) in seen:
            raise ValueError("The symbol inheritance contains a cycle.")
        seen.add(id(parent))
        chain.append(parent)
    fields = {field.name: field for field in chain[-1].fields}
    mandatory = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
    for child in reversed(chain[:-1]):
        for field in child.fields:
            if field.value or field.name not in mandatory:
                fields[field.name] = field
    return chain[-1], list(fields.values())
