"""Symbol library writer: ``SymbolLib`` → flat S-expression string → Prettify.

Follows KiCad's token ordering from ``sch_io_kicad_sexpr_lib_cache.cpp``
and ``sch_io_kicad_sexpr_common.cpp``.

Sibling order is canonical too. KiCad re-sorts draw items, units and library
symbols on every save, so this writer applies the same comparators through
``common/sch_family/ordering.py``. Sorting is always on, because sorted input is
a fixed point of the sort and KiCad-written files already satisfy it (ADR-055).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from eedesigncore.common.models import (
    EmbeddedFile,
    Property,
)
from eedesigncore.common.sch_family.ordering import (
    build_bare_name_index,
    resolve_parent_symbol,
    sort_draw_items,
    sort_library_symbols,
    sort_symbol_fields,
    sort_units,
)
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit, join_fp_filters
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.common.writers import (
    dump_bool as _bool,
)
from eedesigncore.common.writers import (
    dump_effects as _dump_effects,
)
from eedesigncore.common.writers import (
    dump_embedded_files as _dump_embedded_files_common,
)
from eedesigncore.common.writers import (
    dump_field_effects as _dump_field_effects,
)
from eedesigncore.common.writers import (
    dump_fill_sch as _dump_fill,
)
from eedesigncore.common.writers import (
    dump_stroke as _dump_stroke,
)
from eedesigncore.common.writers import (
    esc as _esc,
)
from eedesigncore.common.writers import (
    fmt_angle as _fmt_angle,
)
from eedesigncore.common.writers import (
    fmt_iu as _fmt,
)
from eedesigncore.common.writers import (
    q as _q,
)
from eedesigncore.sexpr.writer import prettify
from eedesigncore.sym.models import SymbolLib

# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


def _dump_property(prop: Property) -> str:
    """Emit a ``property`` token."""
    private_tok = "private " if prop.private else ""
    # KiCad uses fmt::format("{:g}", angle) for property angles
    angle_str = _fmt_g(prop.angle)
    parts = [
        f"(property {private_tok}{_q(prop.name)} {_q(prop.value)}"
        f" (at {_fmt(prop.position[0])} {_fmt(prop.position[1])} {angle_str})"
    ]
    parts.append(f"(show_name {_bool(prop.show_name)})")
    parts.append(f"(do_not_autoplace {_bool(prop.do_not_autoplace)})")
    if not prop.visible:
        parts.append("(hide yes)")
    # saveField always calls EDA_TEXT::Format, which always prints (effects …)
    # (sch_io_kicad_sexpr_lib_cache.cpp:682, eda_text.cpp:1061-1119). ADR-068.
    parts.append(_dump_field_effects(prop.attributes))
    parts.append(")")
    return " ".join(parts)


def _fmt_g(value: float) -> str:
    """Format a float using ``%g``-style (no trailing zeros, no trailing dot).

    KiCad uses ``fmt::format("{:g}", angle.AsDegrees())`` for property/text angles.
    For integer-valued floats this produces "0" not "0.0".
    """
    result = f"{value:g}"
    return result


# ---------------------------------------------------------------------------
# Graphic shapes
# ---------------------------------------------------------------------------


def _dump_pts(pts: list[tuple[float, float]]) -> str:
    xy_list = " ".join(f"(xy {_fmt(x)} {_fmt(y)})" for x, y in pts)
    return f"(pts {xy_list})"


def _dump_arc(arc: SchArc) -> str:
    private = "private " if arc.private else ""
    parts = [
        f"(arc {private}"
        f"(start {_fmt(arc.start[0])} {_fmt(arc.start[1])})"
        f" (mid {_fmt(arc.mid[0])} {_fmt(arc.mid[1])})"
        f" (end {_fmt(arc.end[0])} {_fmt(arc.end[1])})"
    ]
    parts.append(_dump_stroke(arc.stroke))
    parts.append(_dump_fill(arc.fill))
    if arc.id is not None:
        parts.append(f"(uuid {_q(arc.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_circle(circle: SchCircle) -> str:
    private = "private " if circle.private else ""
    parts = [
        f"(circle {private}"
        f"(center {_fmt(circle.center[0])} {_fmt(circle.center[1])})"
        f" (radius {_fmt(circle.radius)})"
    ]
    parts.append(_dump_stroke(circle.stroke))
    parts.append(_dump_fill(circle.fill))
    if circle.id is not None:
        parts.append(f"(uuid {_q(circle.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_rect(rect: SchRect) -> str:
    """Emit a ``(rectangle …)`` token (``formatRect``, sch_io_kicad_sexpr_common.cpp:266-284).

    The ``(radius …)`` token appears only for a POSITIVE corner radius, matching
    ``if( aRect->GetCornerRadius() > 0 )`` at :275. A model that sets
    ``corner_radius=0.0`` therefore emits no radius, exactly as KiCad does
    (GAP-023). Loaded files are unaffected: KiCad never writes ``(radius 0)``.
    """
    private = "private " if rect.private else ""
    parts = [
        f"(rectangle {private}"
        f"(start {_fmt(rect.start[0])} {_fmt(rect.start[1])})"
        f" (end {_fmt(rect.end[0])} {_fmt(rect.end[1])})"
    ]
    if rect.corner_radius is not None and rect.corner_radius > 0:
        parts.append(f"(radius {_fmt(rect.corner_radius)})")
    parts.append(_dump_stroke(rect.stroke))
    parts.append(_dump_fill(rect.fill))
    if rect.id is not None:
        parts.append(f"(uuid {_q(rect.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_bezier(bezier: SchBezier) -> str:
    private = "private " if bezier.private else ""
    parts = [f"(bezier {private}{_dump_pts(bezier.pts)}"]
    parts.append(_dump_stroke(bezier.stroke))
    parts.append(_dump_fill(bezier.fill))
    if bezier.id is not None:
        parts.append(f"(uuid {_q(bezier.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_polyline(polyline: SchPolyline) -> str:
    private = "private " if polyline.private else ""
    parts = [f"(polyline {private}{_dump_pts(polyline.pts)}"]
    parts.append(_dump_stroke(polyline.stroke))
    parts.append(_dump_fill(polyline.fill))
    if polyline.id is not None:
        parts.append(f"(uuid {_q(polyline.id)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Text items
# ---------------------------------------------------------------------------


def _dump_sym_text(text: SymText) -> str:
    private = "private " if text.private else ""
    angle_str = str(text.angle)  # stored as tenths of degrees integer
    parts = [
        f"(text {private}{_q(text.content)}"
        f" (at {_fmt(text.position[0])} {_fmt(text.position[1])} {angle_str})"
    ]
    if text.attributes is not None:
        parts.append(_dump_effects(text.attributes))
    parts.append(")")
    return " ".join(parts)


def _dump_sym_text_box(tb: SymTextBox) -> str:
    private = "private " if tb.private else ""
    angle_str = _fmt_g(tb.angle)
    parts = [
        f"(text_box {private}{_q(tb.content)}"
        f" (at {_fmt(tb.position[0])} {_fmt(tb.position[1])} {angle_str})"
        f" (size {_fmt(tb.size[0])} {_fmt(tb.size[1])})"
        f" (margins {_fmt(tb.margins[0])} {_fmt(tb.margins[1])}"
        f" {_fmt(tb.margins[2])} {_fmt(tb.margins[3])})"
    ]
    parts.append(_dump_stroke(tb.stroke))
    parts.append(_dump_fill(tb.fill))
    if tb.attributes is not None:
        parts.append(_dump_effects(tb.attributes))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Pin
# ---------------------------------------------------------------------------


def _dump_pin_alternate(alt: PinAlternate) -> str:
    return f"(alternate {_q(alt.name)} {alt.electrical_type.value} {alt.shape.value})"


def _dump_pin(pin: Pin) -> str:
    angle_str = _fmt_angle(pin.angle)
    parts = [
        f"(pin {pin.electrical_type.value} {pin.shape.value}"
        f" (at {_fmt(pin.position[0])} {_fmt(pin.position[1])} {angle_str})"
        f" (length {_fmt(pin.length)})"
    ]
    if not pin.visible:
        parts.append("(hide yes)")
    # name and number effects: KiCad writes size twice (h == w)
    parts.append(
        f"(name {_q(pin.name)} (effects (font (size {_fmt(pin.name_text_size)}"
        f" {_fmt(pin.name_text_size)}))))"
    )
    parts.append(
        f"(number {_q(pin.number)} (effects (font (size {_fmt(pin.number_text_size)}"
        f" {_fmt(pin.number_text_size)}))))"
    )
    for alt in pin.alternates:
        parts.append(_dump_pin_alternate(alt))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Unit sub-symbol
# ---------------------------------------------------------------------------


def _dump_unit(unit: SymbolUnit) -> str:
    parts = [f"(symbol {_q(unit.name)}"]
    if unit.unit_display_name is not None:
        parts.append(f"(unit_name {_q(unit.unit_display_name)})")
    for item in sort_draw_items(unit.items):
        if isinstance(item, SchArc):
            parts.append(_dump_arc(item))
        elif isinstance(item, SchCircle):
            parts.append(_dump_circle(item))
        elif isinstance(item, SchRect):
            parts.append(_dump_rect(item))
        elif isinstance(item, SchBezier):
            parts.append(_dump_bezier(item))
        elif isinstance(item, SchPolyline):
            parts.append(_dump_polyline(item))
        elif isinstance(item, Pin):
            parts.append(_dump_pin(item))
        elif isinstance(item, SymText):
            parts.append(_dump_sym_text(item))
        elif isinstance(item, SymTextBox):
            parts.append(_dump_sym_text_box(item))
    parts.append(")")
    return " ".join(parts)


def _dump_embedded_files(files: list[EmbeddedFile]) -> str:
    return _dump_embedded_files_common(files)


# ---------------------------------------------------------------------------
# DCM data (ki_locked / ki_keywords / ki_fp_filters)
# ---------------------------------------------------------------------------
#
# KiCad synthesizes these three as stack-local SCH_FIELD objects on every save
# (``sch_io_kicad_sexpr_lib_cache.cpp:470-474`` and ``:562-591``). They carry no
# ordinal, so they emit after the whole ordinal-sorted block rather than inside
# it. Each field is built with a nullptr parent, so its geometry is the
# SCH_FIELD default and can never vary: ``(at 0 0 0)``, ``(show_name no)``,
# ``(do_not_autoplace no)`` and a 1.27 mm square font (audit 15.2.3, 15.2.5).


class _ParentIndex(dict[str, Symbol]):
    """The by-name symbol map for one write, carrying the caches the walk needs.

    It subclasses ``dict`` so ``_dump_symbol`` keeps its plain ``by_name``
    contract, while one write shares a single reduced-key index and one memo
    entry per symbol. Rebuilding either per hop made the write cubic in the
    symbol count (MEDIUM-5 of the 11.8 security review). It caches only; the
    resolution rule itself lives in ``common/sch_family/ordering``.
    """

    __slots__ = ("bare_names", "fp_filters_memo", "keywords_memo")

    def __init__(self, source: Mapping[str, Symbol]) -> None:
        super().__init__(source)
        self.bare_names: dict[str, Symbol | None] | None = None
        self.keywords_memo: dict[int, tuple[Symbol, str]] = {}
        self.fp_filters_memo: dict[int, tuple[Symbol, list[str]]] = {}

    def bare_index(self) -> dict[str, Symbol | None]:
        """Return the shared reduced-key index, building it on first use.

        A library with no derived symbol never resolves a parent and so never
        pays for it.
        """
        if self.bare_names is None:
            self.bare_names = build_bare_name_index(self)
        return self.bare_names


def _as_index(by_name: dict[str, Symbol] | None) -> _ParentIndex | None:
    """Wrap a caller-supplied plain map so the walk gets its caches either way."""
    if by_name is None or isinstance(by_name, _ParentIndex):
        return by_name
    return _ParentIndex(by_name)


def _resolve_parent(sym: Symbol, by_name: _ParentIndex) -> Symbol | None:
    """Resolve *sym*'s parent through the one shared rule, reusing this write's index.

    The rule itself lives in ``common/sch_family/ordering.resolve_parent_symbol``,
    so the write-time keyword fallback and the inheritance-depth sort cannot
    disagree about what a parent is (ADR-078).
    """
    if sym.extends is None:
        return None
    return resolve_parent_symbol(sym, by_name, by_name.bare_index())


_Inherited = TypeVar("_Inherited", str, list[str])


def _walk_for_value(
    sym: Symbol,
    by_name: _ParentIndex,
    memo: dict[int, tuple[Symbol, _Inherited]],
    value_of: Callable[[Symbol], _Inherited],
    empty: _Inherited,
) -> _Inherited:
    """First non-empty *value_of* along the ``extends`` chain from *sym*, or *empty*.

    Call it only when *sym*'s own value is empty. Every symbol reached on the way
    is empty too, and the chain from it is a suffix of the chain walked here, so
    all of them share this answer. Recording all of them turns an N-symbol chain
    into one walk instead of N. Each memo entry holds its symbol alongside the
    answer, so no ``id()`` can be recycled while the entry lives.
    """
    cached = memo.get(id(sym))
    if cached is not None:
        return cached[1]

    walked = [sym]
    visited = {id(sym)}
    found = empty
    parent = _resolve_parent(sym, by_name)
    while parent is not None and id(parent) not in visited:
        value = value_of(parent)
        if value:
            found = value
            break
        visited.add(id(parent))
        walked.append(parent)
        parent = _resolve_parent(parent, by_name)

    for node in walked:
        memo[id(node)] = (node, found)
    return found


def _effective_keywords(sym: Symbol, by_name: _ParentIndex | None) -> str:
    """Return the keywords KiCad would save for *sym*, parent fallback included.

    ``LIB_SYMBOL::GetKeyWords`` returns the parent's value when the child's own
    member is empty and the symbol is derived (``lib_symbol.h:179-188``), and the
    parent applies the same rule to its own parent. A None *by_name* means the
    caller supplied no library, so no fallback applies.
    """
    if sym.keywords:
        return sym.keywords
    if by_name is None:
        return ""
    return _walk_for_value(sym, by_name, by_name.keywords_memo, _own_keywords, "")


def _effective_fp_filters(sym: Symbol, by_name: _ParentIndex | None) -> list[str]:
    """Return the filter list KiCad would save for *sym*, parent fallback included.

    ``LIB_SYMBOL::GetFPFilters`` has the same inheriting shape as ``GetKeyWords``
    (``lib_symbol.h:211-220``).
    """
    if sym.fp_filters:
        return sym.fp_filters
    if by_name is None:
        return []
    return _walk_for_value(sym, by_name, by_name.fp_filters_memo, _own_fp_filters, [])


def _own_keywords(sym: Symbol) -> str:
    return sym.keywords or ""


def _own_fp_filters(sym: Symbol) -> list[str]:
    return sym.fp_filters


def _dump_dcm_fields(
    sym: Symbol, by_name: dict[str, Symbol] | None, *, is_root: bool
) -> list[str]:
    """Emit the synthesized DCM properties, each behind its own gate.

    ``ki_locked`` is root-only: the block lives inside the ``IsRoot()`` branch at
    ``sch_io_kicad_sexpr_lib_cache.cpp:380,470``, so a derived symbol never emits
    one however its lock flag reads. It also carries an empty value and NO
    ``(hide yes)``, because ``SetText`` is never called on it and the SCH_FIELD
    constructor leaves it visible (``:472-473``, ``sch_field.cpp:75``).
    """
    parts: list[str] = []
    index = _as_index(by_name)

    if is_root and sym.units_locked:
        parts.append(_dump_property(Property(name="ki_locked", value="", visible=True)))

    keywords = _effective_keywords(sym, index)
    if keywords:
        parts.append(_dump_property(Property(name="ki_keywords", value=keywords, visible=False)))

    fp_filters = _effective_fp_filters(sym, index)
    if fp_filters:
        value = join_fp_filters(fp_filters)
        parts.append(_dump_property(Property(name="ki_fp_filters", value=value, visible=False)))

    return parts


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------


def _dump_symbol(sym: Symbol, *, by_name: dict[str, Symbol] | None = None) -> str:
    """Emit one library symbol.

    *by_name* is the enclosing ``.kicad_sym`` library keyed by symbol name. It
    exists only so a derived symbol can materialize its parent's keywords and
    filters the way KiCad does at save time. Omit it and no fallback applies,
    which is what the ``.kicad_sch`` ``lib_symbols`` path does, because KiCad
    resolves no parent inside that block (audit 16.1.5).
    """
    parts = [f"(symbol {_q(sym.name)}"]

    if sym.extends is not None:
        # Derived symbol
        parts.append(f"(extends {_q(sym.extends)})")
        for field in sort_symbol_fields(sym.fields):
            parts.append(_dump_property(field))
        parts.extend(_dump_dcm_fields(sym, by_name, is_root=False))
        parts.append(f"(embedded_fonts {_bool(sym.embedded_fonts)})")
        if sym.embedded_files:
            parts.append(_dump_embedded_files(sym.embedded_files))
        parts.append(")")
        return " ".join(parts)

    # Root symbol
    if sym.power_type is not None:
        parts.append(f"(power {sym.power_type.value})")

    if sym.body_styles is not None:
        if sym.body_styles == ["demorgan"]:
            parts.append("(body_styles demorgan)")
        else:
            style_tokens = " ".join(_q(s) for s in sym.body_styles)
            parts.append(f"(body_styles {style_tokens})")

    if not sym.show_pin_numbers:
        parts.append("(pin_numbers (hide yes))")

    # pin_names: write only if offset is non-default or show_pin_names is False
    default_offset = 0.508  # schIUScale.MilsToIU(DEFAULT_PIN_NAME_OFFSET) = 20 mils = 0.508 mm
    has_pin_name_offset = sym.pin_name_offset is not None and sym.pin_name_offset != default_offset
    if has_pin_name_offset or not sym.show_pin_names:
        pin_name_parts = ["(pin_names"]
        if has_pin_name_offset and sym.pin_name_offset is not None:
            pin_name_parts.append(f"(offset {_fmt(sym.pin_name_offset)})")
        if not sym.show_pin_names:
            pin_name_parts.append("(hide yes)")
        pin_name_parts.append(")")
        parts.append(" ".join(pin_name_parts))

    parts.append(f"(exclude_from_sim {_bool(sym.exclude_from_sim)})")
    parts.append(f"(in_bom {_bool(sym.in_bom)})")
    parts.append(f"(on_board {_bool(sym.on_board)})")
    parts.append(f"(in_pos_files {_bool(sym.in_pos_files)})")
    dup_jumpers = _bool(sym.duplicate_pin_numbers_are_jumpers)
    parts.append(f"(duplicate_pin_numbers_are_jumpers {dup_jumpers})")

    if sym.jumper_pin_groups:
        grp_parts = ["(jumper_pin_groups"]
        for grp in sym.jumper_pin_groups:
            grp_inner = " ".join(_q(p) for p in grp)
            grp_parts.append(f"({grp_inner} )")
        grp_parts.append(")")
        parts.append(" ".join(grp_parts))

    for field in sort_symbol_fields(sym.fields):
        parts.append(_dump_property(field))

    parts.extend(_dump_dcm_fields(sym, by_name, is_root=True))

    for unit in sort_units(sym.units):
        parts.append(_dump_unit(unit))

    parts.append(f"(embedded_fonts {_bool(sym.embedded_fonts)})")
    if sym.embedded_files:
        parts.append(_dump_embedded_files(sym.embedded_files))

    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


def dump_sym(lib: SymbolLib) -> str:
    """Emit a flat (unindented) S-expression string for *lib*."""
    parts = [
        f"(kicad_symbol_lib (version {lib.version})"
        f' (generator "{_esc(lib.generator)}")'
        f' (generator_version "{_esc(lib.generator_version)}")'
    ]
    by_name = _ParentIndex({sym.name: sym for sym in lib.symbols})
    for sym in sort_library_symbols(lib.symbols):
        parts.append(_dump_symbol(sym, by_name=by_name))
    parts.append(")")
    return " ".join(parts)


def write_sym(lib: SymbolLib) -> str:
    """Serialise *lib* to a canonical ``.kicad_sym`` string.

    Calls ``dump_sym`` then applies the Prettify post-pass with NORMAL mode
    (which is what KiCad 10.0 uses for sym files).
    """
    flat = dump_sym(lib)
    return prettify(flat, mode="NORMAL")
