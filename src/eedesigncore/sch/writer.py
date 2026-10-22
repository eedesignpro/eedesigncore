"""Schematic writer: ``Schematic`` -> flat S-expression string -> Prettify.

Follows KiCad's ``Format( SCH_SHEET* )`` root emission order
(``sch_io_kicad_sexpr.cpp:400-541``, sch-root-grammar report Part 1). The item
stream is emitted SORTED by ``(KICAD_T type-enum, uuid)`` (Part 3), not encounter
order; ``paper``/``title_block`` reuse the cross-app emitters shared with the
board writer.
"""

from __future__ import annotations

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.ordering import (
    kiid_path_elements as _kiid_path_elements,
)
from eedesigncore.common.sch_family.ordering import (
    kiid_path_sort_key as _kiid_path_sort_key,
)
from eedesigncore.common.sch_family.ordering import (
    sort_schematic_lib_symbols as _sort_schematic_lib_symbols,
)
from eedesigncore.common.sch_family.ordering import (
    sort_symbol_fields as _sort_symbol_fields,
)
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.writers import (
    dump_bool as _bool,
)
from eedesigncore.common.writers import (
    dump_color as _dump_color,
)
from eedesigncore.common.writers import (
    dump_effects as _dump_effects,
)
from eedesigncore.common.writers import (
    dump_embedded_files as _dump_embedded_files,
)
from eedesigncore.common.writers import (
    dump_field_effects as _dump_field_effects,
)
from eedesigncore.common.writers import (
    dump_fill_sch as _dump_fill,
)
from eedesigncore.common.writers import (
    dump_paper as _dump_paper,
)
from eedesigncore.common.writers import (
    dump_stroke as _dump_stroke,
)
from eedesigncore.common.writers import (
    dump_title_block as _dump_title_block,
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
from eedesigncore.common.writers import (
    title_block_has_content as _title_block_has_content,
)
from eedesigncore.sch.models import (
    BusEntry,
    GraphicShape,
    Group,
    Image,
    InstanceProject,
    InstanceVariant,
    Junction,
    Label,
    LabelKind,
    Line,
    NoConnect,
    PlacedSymbol,
    PlacedSymbolPin,
    RuleArea,
    Schematic,
    SchTable,
    SchTableCell,
    SchText,
    SchTextBox,
    Sheet,
    SheetInstanceProject,
    SheetInstances,
    SheetPin,
    SheetVariant,
)
from eedesigncore.sexpr.writer import prettify

# Base64 line width KiCad uses for the image (data …) block (kicad_io_utils.cpp:63).
_MIME_BASE64_LENGTH = 76

# ---------------------------------------------------------------------------
# Item-stream sort ranks: KICAD_T enum ordinals (core/typeinfo.h:152-179).
# The root item multiset is keyed on (Type() ascending, m_Uuid ascending)
# (sch-root-grammar Part 3). Only relative order matters. SCH_BUS_BUS_ENTRY_T
# (166) is not modeled distinctly (ADR-039); bus-bus entries emit as bus Lines.
# ---------------------------------------------------------------------------
_RANK_SHAPE = 0  # SCH_SHAPE_T
_RANK_TEXT = 1  # SCH_TEXT_T
_RANK_TEXTBOX = 2  # SCH_TEXTBOX_T
_RANK_JUNCTION = 3  # SCH_JUNCTION_T
_RANK_NO_CONNECT = 4  # SCH_NO_CONNECT_T
_RANK_BUS_ENTRY = 5  # SCH_BUS_WIRE_ENTRY_T
_RANK_LINE = 6  # SCH_LINE_T
_RANK_BITMAP = 7  # SCH_BITMAP_T
_RANK_TABLE = 8  # SCH_TABLE_T
_RANK_LABEL = 9  # SCH_LABEL_T
_RANK_GLOBAL_LABEL = 10  # SCH_GLOBAL_LABEL_T
_RANK_HIER_LABEL = 11  # SCH_HIER_LABEL_T
_RANK_RULE_AREA = 12  # SCH_RULE_AREA_T
_RANK_NETCLASS_FLAG = 13  # SCH_DIRECTIVE_LABEL_T
_RANK_SYMBOL = 14  # SCH_SYMBOL_T
_RANK_GROUP = 15  # SCH_GROUP_T
_RANK_SHEET = 16  # SCH_SHEET_T (typeinfo.h:179, after SCH_GROUP_T)

# ---------------------------------------------------------------------------
# Placed SCH_SYMBOL instance (item stream)
# ---------------------------------------------------------------------------


def _project_root_key(project: InstanceProject) -> tuple[str, ...]:
    """Return the root-sheet KIID a project block is keyed on.

    KiCad groups placed-symbol instances into a ``std::map<KIID, …>`` keyed on
    the FIRST KIID of each path (``sch_io_kicad_sexpr.cpp:845,907-913``), so the
    blocks emit by root sheet KIID ascending, NOT by the project name the file
    prints (``:927-932``). The key is the root of the block's smallest path,
    which is the same KIID for every path KiCad would put in one block.

    A block with no path has no key at all. It sorts first and, because the sort
    is stable, keeps its stored position among other keyless blocks.
    """
    roots = [_kiid_path_elements(path.path)[:1] for path in project.paths]
    return min((root for root in roots if root), default=())


def _dump_instances(projects: list[InstanceProject]) -> str:
    """Emit ``(instances (project "name" (path "/uuid" (reference "R1") …)))``.

    Grammar per embed-context Part 2b (saveSymbol :843-987): projects then per
    path ``(reference)(unit)`` with optional ``(variant …)``.

    Both levels emit SORTED (sweep section 3.3). Project blocks sort by root
    sheet KIID (:845,916) and the paths inside one block sort by ``KIID_PATH``
    (:921-925). Both sorts are the identity on a KiCad-written file.

    KiCad also REGROUPS instances whose root KIIDs differ. This writer never
    regroups, because the project NAME of a new block comes from runtime state
    that the file does not carry (ADR-063).
    """
    parts = ["(instances"]
    for project in sorted(projects, key=_project_root_key):
        proj_parts = [f"(project {_q(project.name)}"]
        for p in sorted(project.paths, key=lambda item: _kiid_path_sort_key(item.path)):
            path_parts = [
                f"(path {_q(p.path)}",
                f"(reference {_q(p.reference)})",
                f"(unit {p.unit})",
            ]
            for variant in p.variants:
                path_parts.append(_dump_instance_variant(variant))
            path_parts.append(")")
            proj_parts.append(" ".join(path_parts))
        proj_parts.append(")")
        parts.append(" ".join(proj_parts))
    parts.append(")")
    return " ".join(parts)


def _dump_instance_variant(variant: InstanceVariant) -> str:
    """Emit a placed-symbol ``(variant (name …) …)`` (saveSymbol :949-977).

    Only the optional bools present on the model are emitted (KiCad writes only
    those that differ from the symbol's own attributes; ``None`` means the file did
    not carry them). Unlike the sheet variant, ``on_board``/``in_pos_files`` ARE
    emitted here (:964-968). Emit order: name, dnp, exclude_from_sim, in_bom,
    on_board, in_pos_files, fields.
    """
    parts = [f"(variant (name {_q(variant.name)})"]
    if variant.dnp is not None:
        parts.append(f"(dnp {_bool(variant.dnp)})")
    if variant.exclude_from_sim is not None:
        parts.append(f"(exclude_from_sim {_bool(variant.exclude_from_sim)})")
    if variant.in_bom is not None:
        parts.append(f"(in_bom {_bool(variant.in_bom)})")
    if variant.on_board is not None:
        parts.append(f"(on_board {_bool(variant.on_board)})")
    if variant.in_pos_files is not None:
        parts.append(f"(in_pos_files {_bool(variant.in_pos_files)})")
    for field in variant.fields:
        parts.append(f"(field (name {_q(field.name)}) (value {_q(field.value)}))")
    parts.append(")")
    return " ".join(parts)


def _dump_placed_property(prop: Property) -> str:
    """Emit a placed-instance ``(property …)`` (``SCH_IO_KICAD_SEXPR::saveField``).

    Distinct from the ``.kicad_sym`` lib-cache field order: here ``(hide yes)`` is
    emitted (iff hidden) BEFORE ``(show_name)``/``(do_not_autoplace)`` (:1014-1019),
    and the angle uses ``FormatAngle`` (:1012). Effects follow when present.
    """
    private_tok = "private " if prop.private else ""
    parts = [
        f"(property {private_tok}{_q(prop.name)} {_q(prop.value)}"
        f" (at {_fmt(prop.position[0])} {_fmt(prop.position[1])} {_fmt_angle(prop.angle)})"
    ]
    if not prop.visible:
        parts.append("(hide yes)")
    parts.append(f"(show_name {_bool(prop.show_name)})")
    parts.append(f"(do_not_autoplace {_bool(prop.do_not_autoplace)})")
    if prop.attributes is not None:
        parts.append(_dump_field_effects(prop.attributes))
    parts.append(")")
    return " ".join(parts)


def _dump_placed_pin(pin: PlacedSymbolPin) -> str:
    parts = [f"(pin {_q(pin.number)}"]
    if pin.uuid is not None:
        parts.append(f"(uuid {_q(pin.uuid)})")
    if pin.alternate is not None:
        parts.append(f"(alternate {_q(pin.alternate)})")
    parts.append(")")
    return " ".join(parts)


def _dump_placed_symbol(sym: PlacedSymbol) -> str:
    """Emit a placed ``(symbol …)`` instance node (saveSymbol :706-989).

    Field/emit order (embed-context Part 2b): optional ``(lib_name)``, ``(lib_id)``,
    ``(at x y angle)``, optional ``(mirror x|y)``, ``(unit)``/``(body_style)``, the
    five instance bools, optional ``(fields_autoplaced yes)``, ``(uuid)``, fields,
    per-pin blocks, then ``(instances …)``.

    Fields emit in ordinal order, because ``SCH_SYMBOL::GetFields`` sorts on
    ``GetOrdinal()`` (``sch_symbol.cpp:1321-1325``), the same rule the library
    symbol writer already uses. Pins do NOT sort: ``GetRawPins`` hands the writer
    the raw vector (``sch_symbol.h:668``), so stored order is correct there
    (sweep section 3.3).
    """
    parts = ["(symbol"]
    if sym.lib_name is not None:
        parts.append(f"(lib_name {_q(sym.lib_name)})")
    parts.append(f"(lib_id {_q(sym.lib_id)})")
    parts.append(f"(at {_fmt(sym.position[0])} {_fmt(sym.position[1])} {_fmt_angle(sym.angle)})")
    if sym.mirror is not None:
        parts.append(f"(mirror {sym.mirror})")
    parts.append(f"(unit {sym.unit})")
    parts.append(f"(body_style {sym.body_style})")
    parts.append(f"(exclude_from_sim {_bool(sym.exclude_from_sim)})")
    parts.append(f"(in_bom {_bool(sym.in_bom)})")
    parts.append(f"(on_board {_bool(sym.on_board)})")
    parts.append(f"(in_pos_files {_bool(sym.in_pos_files)})")
    parts.append(f"(dnp {_bool(sym.dnp)})")
    if sym.fields_autoplaced:
        parts.append("(fields_autoplaced yes)")
    if sym.uuid is not None:
        parts.append(f"(uuid {_q(sym.uuid)})")
    for field in _sort_symbol_fields(sym.fields):
        parts.append(_dump_placed_property(field))
    for pin in sym.pins:
        parts.append(_dump_placed_pin(pin))
    if sym.instances:
        parts.append(_dump_instances(sym.instances))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# High-frequency sch items (T3a)
# ---------------------------------------------------------------------------


def _dump_junction(j: Junction) -> str:
    """Emit ``(junction (at X Y) (diameter D) (color R G B A) (uuid …))`` (:1248-1261)."""
    parts = [
        f"(junction (at {_fmt(j.position[0])} {_fmt(j.position[1])})"
        f" (diameter {_fmt(j.diameter)}) {_dump_color(j.color)}"
    ]
    if j.uuid is not None:
        parts.append(f"(uuid {_q(j.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_no_connect(nc: NoConnect) -> str:
    """Emit ``(no_connect (at X Y) (uuid …))`` (:1269-1276)."""
    parts = [f"(no_connect (at {_fmt(nc.position[0])} {_fmt(nc.position[1])})"]
    if nc.uuid is not None:
        parts.append(f"(uuid {_q(nc.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_bus_entry(be: BusEntry) -> str:
    """Emit ``(bus_entry (at X Y) (size W H) <stroke> (uuid …))`` (:1294-1306)."""
    parts = [
        f"(bus_entry (at {_fmt(be.position[0])} {_fmt(be.position[1])})"
        f" (size {_fmt(be.size[0])} {_fmt(be.size[1])})"
    ]
    parts.append(_dump_stroke(be.stroke))
    if be.uuid is not None:
        parts.append(f"(uuid {_q(be.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_line(line: Line) -> str:
    """Emit ``(wire|bus|polyline (pts (xy)(xy)) <stroke> (uuid …))`` (:1381-1394)."""
    parts = [
        f"({line.line_type.value} (pts"
        f" (xy {_fmt(line.start[0])} {_fmt(line.start[1])})"
        f" (xy {_fmt(line.end[0])} {_fmt(line.end[1])}))"
    ]
    parts.append(_dump_stroke(line.stroke))
    if line.uuid is not None:
        parts.append(f"(uuid {_q(line.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_graphic_shape(shape: GraphicShape) -> str:
    """Dispatch a root graphic shape to the shared sch_family emitter (saveShape)."""
    from eedesigncore.sym.writer import (
        _dump_arc,
        _dump_bezier,
        _dump_circle,
        _dump_polyline,
        _dump_rect,
    )

    if isinstance(shape, SchArc):
        return _dump_arc(shape)
    if isinstance(shape, SchCircle):
        return _dump_circle(shape)
    if isinstance(shape, SchRect):
        return _dump_rect(shape)
    if isinstance(shape, SchBezier):
        return _dump_bezier(shape)
    if isinstance(shape, SchPolyline):
        return _dump_polyline(shape)
    raise TypeError(f"Unknown graphic shape type: {type(shape).__name__}")


def _dump_text(text: SchText) -> str:
    """Emit ``(text "…" (exclude_from_sim b) (at X Y A) <effects> (uuid …))`` (:1405-1468)."""
    parts = [f"(text {_q(text.content)}"]
    parts.append(f"(exclude_from_sim {_bool(text.exclude_from_sim)})")
    parts.append(
        f"(at {_fmt(text.position[0])} {_fmt(text.position[1])} {_fmt_angle(text.angle)})"
    )
    if text.attributes is not None:
        parts.append(_dump_effects(text.attributes))
    if text.uuid is not None:
        parts.append(f"(uuid {_q(text.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_label(label: Label) -> str:
    """Emit a label of any kind (``saveText`` :1405-1466).

    Order: token+text, ``(length)`` iff netclass_flag, ``(shape)`` iff
    global/hier/netclass, ``(at)``, ``(fields_autoplaced yes)`` iff set, effects,
    uuid, then per-field ``(property …)`` blocks.
    """
    parts = [f"({label.kind.value} {_q(label.content)}"]
    if label.kind is LabelKind.netclass_flag and label.length is not None:
        parts.append(f"(length {_fmt(label.length)})")
    if label.shape is not None:
        parts.append(f"(shape {label.shape})")
    parts.append(
        f"(at {_fmt(label.position[0])} {_fmt(label.position[1])} {_fmt_angle(label.angle)})"
    )
    if label.fields_autoplaced:
        parts.append("(fields_autoplaced yes)")
    if label.attributes is not None:
        parts.append(_dump_effects(label.attributes))
    if label.uuid is not None:
        parts.append(f"(uuid {_q(label.uuid)})")
    for field in label.fields:
        parts.append(_dump_placed_property(field))
    parts.append(")")
    return " ".join(parts)


def _dump_text_box(tb: SchTextBox) -> str:
    """Emit a schematic ``(text_box …)`` (``saveTextBox`` :1476-1505)."""
    parts = [f"(text_box {_q(tb.content)}"]
    parts.append(f"(exclude_from_sim {_bool(tb.exclude_from_sim)})")
    parts.append(
        f"(at {_fmt(tb.position[0])} {_fmt(tb.position[1])} {_fmt_angle(tb.angle)})"
        f" (size {_fmt(tb.size[0])} {_fmt(tb.size[1])})"
        f" (margins {_fmt(tb.margins[0])} {_fmt(tb.margins[1])}"
        f" {_fmt(tb.margins[2])} {_fmt(tb.margins[3])})"
    )
    parts.append(_dump_stroke(tb.stroke))
    parts.append(_dump_fill(tb.fill))
    if tb.attributes is not None:
        parts.append(_dump_effects(tb.attributes))
    if tb.uuid is not None:
        parts.append(f"(uuid {_q(tb.uuid)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Lower-frequency sch items (T3b)
# ---------------------------------------------------------------------------


def _dump_rule_area(ra: RuleArea) -> str:
    """Emit ``(rule_area <4 bools> <polyline>)`` (``saveRuleArea`` :1351-1360)."""
    from eedesigncore.sym.writer import _dump_polyline

    parts = [
        "(rule_area",
        f"(exclude_from_sim {_bool(ra.exclude_from_sim)})",
        f"(in_bom {_bool(ra.in_bom)})",
        f"(on_board {_bool(ra.on_board)})",
        f"(dnp {_bool(ra.dnp)})",
        _dump_polyline(ra.shape),
        ")",
    ]
    return " ".join(parts)


def _dump_image(img: Image) -> str:
    """Emit ``(image (at X Y) [(scale g)] (uuid …) (data …))`` (``saveBitmap`` :1042-1065).

    ``scale`` is emitted (``{:g}``) only when != 1.0. The ``(data …)`` block mirrors
    ``FormatStreamData``: 76-char base64 chunks, each a quoted string, separated by
    newlines that the Prettify pass reflows into indented continuation lines.
    """
    parts = [f"(image (at {_fmt(img.position[0])} {_fmt(img.position[1])})"]
    if img.scale is not None and img.scale != 1.0:
        parts.append(f"(scale {img.scale:g})")
    if img.uuid is not None:
        parts.append(f"(uuid {_q(img.uuid)})")
    chunks = [
        img.data[i : i + _MIME_BASE64_LENGTH] for i in range(0, len(img.data), _MIME_BASE64_LENGTH)
    ]
    data_block = "(data" + "".join(f'\n"{c}"' for c in chunks) + ")"
    parts.append(data_block)
    parts.append(")")
    return " ".join(parts)


def _dump_group(group: Group) -> str:
    """Emit ``(group "name" (uuid …) [(locked yes)] [(lib_id …)] (members …))`` (:1619-1642).

    Member uuids are sorted ascending (:1634).
    """
    parts = [f"(group {_q(group.name)}"]
    if group.uuid is not None:
        parts.append(f"(uuid {_q(group.uuid)})")
    if group.locked:
        parts.append("(locked yes)")
    if group.lib_id is not None:
        parts.append(f"(lib_id {_q(group.lib_id)})")
    member_tokens = " ".join(_q(m) for m in sorted(group.members))
    parts.append(f"(members {member_tokens})" if member_tokens else "(members)")
    parts.append(")")
    return " ".join(parts)


def _dump_table_cell(cell: SchTableCell) -> str:
    """Emit a ``(table_cell …)`` (``saveTextBox`` for a cell: span, NO stroke, :1476-1505)."""
    parts = [f"(table_cell {_q(cell.content)}"]
    parts.append(f"(exclude_from_sim {_bool(cell.exclude_from_sim)})")
    parts.append(
        f"(at {_fmt(cell.position[0])} {_fmt(cell.position[1])} {_fmt_angle(cell.angle)})"
        f" (size {_fmt(cell.size[0])} {_fmt(cell.size[1])})"
        f" (margins {_fmt(cell.margins[0])} {_fmt(cell.margins[1])}"
        f" {_fmt(cell.margins[2])} {_fmt(cell.margins[3])})"
    )
    parts.append(f"(span {cell.span[0]} {cell.span[1]})")
    parts.append(_dump_fill(cell.fill))
    if cell.attributes is not None:
        parts.append(_dump_effects(cell.attributes))
    if cell.uuid is not None:
        parts.append(f"(uuid {_q(cell.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_table(table: SchTable) -> str:
    """Emit ``(table …)`` (``saveTable`` :1558-1606)."""
    parts = [f"(table (column_count {table.column_count})"]

    border = ["(border", f"(external {_bool(table.border_external)})"]
    border.append(f"(header {_bool(table.border_header)})")
    if table.border_external or table.border_header:
        border.append(_dump_stroke(table.border_stroke))
    border.append(")")
    parts.append(" ".join(border))

    seps = ["(separators", f"(rows {_bool(table.separators_rows)})"]
    seps.append(f"(cols {_bool(table.separators_cols)})")
    if table.separators_rows or table.separators_cols:
        seps.append(_dump_stroke(table.separators_stroke))
    seps.append(")")
    parts.append(" ".join(seps))

    widths = " ".join(_fmt(w) for w in table.column_widths)
    parts.append(f"(column_widths {widths})" if widths else "(column_widths)")
    heights = " ".join(_fmt(h) for h in table.row_heights)
    parts.append(f"(row_heights {heights})" if heights else "(row_heights)")

    if table.uuid is not None:
        parts.append(f"(uuid {_q(table.uuid)})")

    cell_block = ["(cells"]
    for cell in table.cells:
        cell_block.append(_dump_table_cell(cell))
    cell_block.append(")")
    parts.append(" ".join(cell_block))

    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Sheet node + sheet-instance blocks (T4)
# ---------------------------------------------------------------------------


def _dump_sheet_pin(pin: SheetPin) -> str:
    """Emit ``(pin "name" <shape> (at X Y ANGLE) (uuid …) <effects>)`` (saveSheet :1109-1125)."""
    parts = [
        f"(pin {_q(pin.name)} {pin.shape}"
        f" (at {_fmt(pin.position[0])} {_fmt(pin.position[1])} {_fmt_angle(pin.angle)})"
    ]
    if pin.uuid is not None:
        parts.append(f"(uuid {_q(pin.uuid)})")
    if pin.attributes is not None:
        parts.append(_dump_effects(pin.attributes))
    parts.append(")")
    return " ".join(parts)


def _dump_sheet_variant(variant: SheetVariant) -> str:
    """Emit a per-sheet ``(variant (name …) …)`` (saveSheet :1202-1225).

    Only the ``dnp``/``exclude_from_sim``/``in_bom`` bools present on the model are
    emitted (KiCad emits only those that differ from the sheet's own attributes;
    ``None`` means the file did not carry them). ``on_board``/``in_pos_files`` are
    never emitted by the 10.0.3 writer (§4c) even if parsed. Fields are emitted in
    stored (name-sorted) order.
    """
    parts = [f"(variant (name {_q(variant.name)})"]
    if variant.dnp is not None:
        parts.append(f"(dnp {_bool(variant.dnp)})")
    if variant.exclude_from_sim is not None:
        parts.append(f"(exclude_from_sim {_bool(variant.exclude_from_sim)})")
    if variant.in_bom is not None:
        parts.append(f"(in_bom {_bool(variant.in_bom)})")
    for field in variant.fields:
        parts.append(f"(field (name {_q(field.name)}) (value {_q(field.value)}))")
    parts.append(")")
    return " ".join(parts)


def _dump_per_sheet_instances(projects: list[SheetInstanceProject]) -> str:
    """Emit a sheet's per-sheet ``(instances (project "name" (path …)))`` (:1140-1237).

    Distinct from the root ``(sheet_instances)`` block: wrapped in ``(project …)``
    clauses, full KIID path (NO ``"/"`` fallback), may carry ``(variant …)``.
    Project/path/variant order is preserved verbatim (A1 open-question 2).
    """
    parts = ["(instances"]
    for project in projects:
        proj_parts = [f"(project {_q(project.name)}"]
        for p in project.paths:
            path_parts = [f"(path {_q(p.path)} (page {_q(p.page)})"]
            for variant in p.variants:
                path_parts.append(_dump_sheet_variant(variant))
            path_parts.append(")")
            proj_parts.append(" ".join(path_parts))
        proj_parts.append(")")
        parts.append(" ".join(proj_parts))
    parts.append(")")
    return " ".join(parts)


def _dump_sheet(sheet: Sheet) -> str:
    """Emit a ``(sheet …)`` sub-sheet node (``saveSheet`` :1069-1241).

    Order: ``(at)``, ``(size)``, the four bools, optional ``(fields_autoplaced yes)``,
    ``<stroke>`` (border), ``(fill (color …))`` (background), ``(uuid)``, fields, pins,
    then the optional per-sheet ``(instances …)`` block.
    """
    parts = [
        f"(sheet (at {_fmt(sheet.position[0])} {_fmt(sheet.position[1])})"
        f" (size {_fmt(sheet.size[0])} {_fmt(sheet.size[1])})"
    ]
    parts.append(f"(exclude_from_sim {_bool(sheet.exclude_from_sim)})")
    parts.append(f"(in_bom {_bool(sheet.in_bom)})")
    parts.append(f"(on_board {_bool(sheet.on_board)})")
    parts.append(f"(dnp {_bool(sheet.dnp)})")
    if sheet.fields_autoplaced:
        parts.append("(fields_autoplaced yes)")
    parts.append(_dump_stroke(sheet.stroke))
    parts.append(f"(fill {_dump_color(sheet.fill_color)})")
    if sheet.uuid is not None:
        parts.append(f"(uuid {_q(sheet.uuid)})")
    for field in sheet.fields:
        parts.append(_dump_placed_property(field))
    for pin in sheet.pins:
        parts.append(_dump_sheet_pin(pin))
    if sheet.instances:
        parts.append(_dump_per_sheet_instances(sheet.instances))
    parts.append(")")
    return " ".join(parts)


def _dump_root_sheet_instances(instances: SheetInstances) -> str:
    """Emit the root ``(sheet_instances (path "<p>" (page "<n>")) …)`` (``saveInstances`` :1646).

    Flat ``(path (page))`` list, both values quoted; NO project wrapper, NO variants.
    """
    parts = ["(sheet_instances"]
    for p in instances.paths:
        parts.append(f"(path {_q(p.path)} (page {_q(p.page)}))")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# lib_symbols block
# ---------------------------------------------------------------------------


def _dump_lib_symbols(sch: Schematic) -> str:
    """Emit the ``(lib_symbols …)`` block (always open/close, even if empty).

    Each entry reuses the shared ``.kicad_sym`` symbol serializer; the only
    schematic-context delta is the top-level ``Lib:Name`` qualification, which is
    carried verbatim in ``Symbol.name`` (ADR-037). Per-unit child names stay bare.

    Entries emit by lib id ascending, because KiCad holds them in a map keyed on
    ``LIB_ID::Format()`` (``sch_screen.cpp:1452``, sweep section 3.2). The sort is
    the identity on a KiCad-written file, so it changes nothing for a round trip.

    No library map is threaded, so no symbol here inherits keywords or footprint
    filters from a parent. KiCad performs NO parent resolution inside a
    ``lib_symbols`` block: the parser builds a throwaway local map
    (``sch_io_kicad_sexpr_parser.cpp:2856-2882``), ``updateParentSymbolLinks`` is
    never called on this path, and ``SCH_SYMBOL::SetLibSymbol`` rejects a non-root
    symbol outright (``sch_symbol.cpp:253-255``). Every entry KiCad writes here is
    already flattened, so the fallback would have nothing to mirror (audit 16.1.5,
    16.1.7 divergence D5).
    """
    from eedesigncore.sym.writer import _dump_symbol

    parts = ["(lib_symbols"]
    for sym in _sort_schematic_lib_symbols(sch.lib_symbols):
        parts.append(_dump_symbol(sym))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Item stream (sorted by KICAD_T type-enum then uuid, sch-root-grammar Part 3)
# ---------------------------------------------------------------------------

_LABEL_RANK = {
    LabelKind.local: _RANK_LABEL,
    LabelKind.global_: _RANK_GLOBAL_LABEL,
    LabelKind.hierarchical: _RANK_HIER_LABEL,
    LabelKind.netclass_flag: _RANK_NETCLASS_FLAG,
}


def _dump_item_stream(sch: Schematic) -> list[str]:
    """Emit the schematic item stream sorted by ``(type-enum rank, uuid)``.

    KiCad builds a ``std::multiset<SCH_ITEM*, cmp>`` keyed on ``(Type() asc,
    m_Uuid asc)`` (Part 3), NOT encounter order. We collect each modeled item as
    ``(rank, uuid, text)`` and sort identically; the boost-uuid ``operator<``
    (kiid.h:129) is a byte-wise compare that equals a canonical-uuid string
    compare, so a plain string sort of the uuid text matches.
    """
    items: list[tuple[int, str, str]] = []

    for shape in sch.graphic_shapes:
        items.append((_RANK_SHAPE, shape.id or "", _dump_graphic_shape(shape)))
    for text in sch.texts:
        items.append((_RANK_TEXT, text.uuid or "", _dump_text(text)))
    for tb in sch.text_boxes:
        items.append((_RANK_TEXTBOX, tb.uuid or "", _dump_text_box(tb)))
    for junction in sch.junctions:
        items.append((_RANK_JUNCTION, junction.uuid or "", _dump_junction(junction)))
    for nc in sch.no_connects:
        items.append((_RANK_NO_CONNECT, nc.uuid or "", _dump_no_connect(nc)))
    for be in sch.bus_entries:
        items.append((_RANK_BUS_ENTRY, be.uuid or "", _dump_bus_entry(be)))
    for line in sch.lines:
        items.append((_RANK_LINE, line.uuid or "", _dump_line(line)))
    for label in sch.labels:
        items.append((_LABEL_RANK[label.kind], label.uuid or "", _dump_label(label)))
    for img in sch.images:
        items.append((_RANK_BITMAP, img.uuid or "", _dump_image(img)))
    for table in sch.tables:
        items.append((_RANK_TABLE, table.uuid or "", _dump_table(table)))
    for ra in sch.rule_areas:
        items.append((_RANK_RULE_AREA, ra.shape.id or "", _dump_rule_area(ra)))
    for sym in sch.symbols:
        items.append((_RANK_SYMBOL, sym.uuid or "", _dump_placed_symbol(sym)))
    for group in sch.groups:
        # Empty groups are never written (saveGroup :1616-1617).
        if group.members:
            items.append((_RANK_GROUP, group.uuid or "", _dump_group(group)))
    for sheet in sch.sheets:
        items.append((_RANK_SHEET, sheet.uuid or "", _dump_sheet(sheet)))

    items.sort(key=lambda t: (t[0], t[1]))
    return [text for _, _, text in items]


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


def dump_sch(sch: Schematic) -> str:
    """Emit a flat (unindented) S-expression string for *sch* (Part 1 order)."""
    parts = [
        f"(kicad_sch (version {sch.version})"
        f' (generator "{_esc(sch.generator)}")'
        f' (generator_version "{_esc(sch.generator_version)}")'
    ]
    if sch.uuid is not None:
        parts.append(f"(uuid {_q(sch.uuid)})")
    if sch.paper is not None:
        parts.append(_dump_paper(sch.paper))
    if sch.title_block is not None and _title_block_has_content(sch.title_block):
        parts.append(_dump_title_block(sch.title_block))

    parts.append(_dump_lib_symbols(sch))

    parts.extend(_dump_item_stream(sch))

    if sch.sheet_instances is not None:
        parts.append(_dump_root_sheet_instances(sch.sheet_instances))

    if sch.embedded_fonts is not None:
        parts.append(f"(embedded_fonts {_bool(sch.embedded_fonts)})")
    if sch.embedded_files:
        parts.append(_dump_embedded_files(sch.embedded_files))

    parts.append(")")
    return " ".join(parts)


def write_sch(sch: Schematic) -> str:
    """Serialise *sch* to a canonical ``.kicad_sch`` string (NORMAL prettify)."""
    return prettify(dump_sch(sch), mode="NORMAL")
