"""Schematic loader: parsed S-expression tree → ``Schematic`` model.

Follows KiCad's ``Format( SCH_SHEET* )`` root skeleton (sch-root-grammar report
Part 1) in reverse. Unknown tokens raise ``KicadParseError`` per the ADR-017
hard-error contract; no token is silently dropped.

Iteration-10 phasing: T1 lands header / paper / title_block / ``(lib_symbols)`` /
embedded blocks; T2 adds the placed ``(symbol …)`` item; T3a/T3b add the sch item
stream; T4 adds ``(sheet …)`` nodes and the modeled root ``(sheet_instances)``
block. The root block and each sub-sheet's per-sheet ``(instances)`` block are two
distinct grammars (A1 report §1 vs §3), loaded by two separate readers.
"""

from __future__ import annotations

from eedesigncore.common.loaders import (
    bool_token as _bool_token,
)
from eedesigncore.common.loaders import (
    children as _children,
)
from eedesigncore.common.loaders import (
    get_float as _get_float,
)
from eedesigncore.common.loaders import (
    get_int as _get_int,
)
from eedesigncore.common.loaders import (
    get_str as _get_str,
)
from eedesigncore.common.loaders import (
    load_color as _load_color,
)
from eedesigncore.common.loaders import (
    load_effects as _load_effects,
)
from eedesigncore.common.loaders import (
    load_embedded_files as _load_embedded_files,
)
from eedesigncore.common.loaders import (
    load_fill as _load_fill,
)
from eedesigncore.common.loaders import (
    load_paper as _load_paper,
)
from eedesigncore.common.loaders import (
    load_stroke as _load_stroke,
)
from eedesigncore.common.loaders import (
    load_title_block as _load_title_block,
)
from eedesigncore.common.loaders import (
    require_sym as _require_sym,
)
from eedesigncore.common.loaders import (
    sym_name as _sym_name,
)
from eedesigncore.common.models import Color, Fill, Property, Stroke
from eedesigncore.common.sch_family.loaders import (
    _load_arc,
    _load_bezier,
    _load_circle,
    _load_polyline,
    _load_pts,
    _load_rect,
)
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sch.models import (
    BusEntry,
    GraphicShape,
    Group,
    Image,
    InstancePath,
    InstanceProject,
    InstanceVariant,
    InstanceVariantField,
    Junction,
    Label,
    LabelKind,
    Line,
    LineType,
    NoConnect,
    PlacedSymbol,
    PlacedSymbolPin,
    RootInstancePath,
    RuleArea,
    Schematic,
    SchTable,
    SchTableCell,
    SchText,
    SchTextBox,
    Sheet,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
    SheetPin,
    SheetVariant,
    SheetVariantField,
)
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType
from eedesigncore.sym.loader import _load_property, _load_symbol

# ---------------------------------------------------------------------------
# Placed SCH_SYMBOL instance (item stream)
# ---------------------------------------------------------------------------


def _load_placed_pin(node: SExprNode) -> PlacedSymbolPin:
    """Parse ``(pin "<number>" (uuid …) [(alternate "…")])`` (saveSymbol :824-841)."""
    _require_sym(node, "pin")
    ch = _children(node)
    if not ch:
        raise KicadParseError("placed symbol pin missing number")
    number = _get_str(ch[0])
    uuid = None
    alternate = None
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "uuid":
            uuid = _get_str(_children(child)[0])
        elif cname == "alternate":
            alternate = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in placed symbol pin: {cname!r}")
    return PlacedSymbolPin(number=number, uuid=uuid, alternate=alternate)


def _load_instance_variant(node: SExprNode) -> InstanceVariant:
    """Parse a placed-symbol ``(variant (name …) …)`` (saveSymbol :949-977).

    The variant is a nested LIST, NOT a bare string: ``(variant (name "02")
    (on_board yes) …)`` (parser.cpp:3382-3474). Each optional bool is stored as the
    literal on-disk value (``None`` if absent) so it re-emits verbatim.
    """
    _require_sym(node, "variant")
    name = ""
    dnp = None
    exclude_from_sim = None
    in_bom = None
    on_board = None
    in_pos_files = None
    fields: list[InstanceVariantField] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "name":
            name = _get_str(cc[0])
        elif cname == "dnp":
            dnp = _bool_token(child)
        elif cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "in_pos_files":
            in_pos_files = _bool_token(child)
        elif cname == "field":
            fname = ""
            fvalue = ""
            for f in cc:
                if f.node_type != SExprNodeType.LIST:
                    continue
                if _sym_name(f) == "name":
                    fname = _get_str(_children(f)[0])
                elif _sym_name(f) == "value":
                    fvalue = _get_str(_children(f)[0])
            fields.append(InstanceVariantField(name=fname, value=fvalue))
        else:
            raise KicadParseError(f"Unknown token in instance variant: {cname!r}")
    return InstanceVariant(
        name=name,
        dnp=dnp,
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        in_pos_files=in_pos_files,
        fields=fields,
    )


def _load_instance_path(node: SExprNode) -> InstancePath:
    """Parse ``(path "/uuid…" (reference "R1") (unit N) [(variant …)…])``."""
    _require_sym(node, "path")
    ch = _children(node)
    if not ch:
        raise KicadParseError("instance path missing path string")
    path = _get_str(ch[0])
    reference = ""
    unit = 1
    variants: list[InstanceVariant] = []
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "reference":
            reference = _get_str(_children(child)[0])
        elif cname == "unit":
            unit = _get_int(_children(child)[0])
        elif cname == "variant":
            variants.append(_load_instance_variant(child))
        else:
            raise KicadParseError(f"Unknown token in instance path: {cname!r}")
    return InstancePath(path=path, reference=reference, unit=unit, variants=variants)


def _load_instances(node: SExprNode) -> list[InstanceProject]:
    """Parse ``(instances (project "name" (path …) …) …)`` (saveSymbol :843-987)."""
    _require_sym(node, "instances")
    projects: list[InstanceProject] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname != "project":
            raise KicadParseError(f"Unknown token in instances: {cname!r}")
        pch = _children(child)
        if not pch:
            raise KicadParseError("instances project missing name")
        name = _get_str(pch[0])
        paths = [
            _load_instance_path(p)
            for p in pch[1:]
            if p.node_type == SExprNodeType.LIST and _sym_name(p) == "path"
        ]
        projects.append(InstanceProject(name=name, paths=paths))
    return projects


def _load_placed_symbol(node: SExprNode) -> PlacedSymbol:
    """Parse a placed ``(symbol …)`` instance (saveSymbol :706-989, Part 2b)."""
    _require_sym(node, "symbol")

    lib_name = None
    lib_id = ""
    position = (0.0, 0.0)
    angle = 0.0
    mirror = None
    unit = 1
    body_style = 1
    exclude_from_sim = False
    in_bom = True
    on_board = True
    in_pos_files = True
    dnp = False
    fields_autoplaced = False
    uuid = None
    fields = []
    pins = []
    instances: list[InstanceProject] = []

    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "lib_name":
            lib_name = _get_str(cc[0])
        elif cname == "lib_id":
            lib_id = _get_str(cc[0])
        elif cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "mirror":
            mirror = str(cc[0].value)
        elif cname == "unit":
            unit = _get_int(cc[0])
        elif cname == "body_style":
            body_style = _get_int(cc[0])
        elif cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "in_pos_files":
            in_pos_files = _bool_token(child)
        elif cname == "dnp":
            dnp = _bool_token(child)
        elif cname == "fields_autoplaced":
            fields_autoplaced = _bool_token(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "property":
            fields.append(_load_property(child))
        elif cname == "pin":
            pins.append(_load_placed_pin(child))
        elif cname == "instances":
            instances = _load_instances(child)
        else:
            raise KicadParseError(f"Unknown token in placed symbol: {cname!r}")

    return PlacedSymbol(
        lib_name=lib_name,
        lib_id=lib_id,
        position=position,
        angle=angle,
        mirror=mirror,
        unit=unit,
        body_style=body_style,
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        in_pos_files=in_pos_files,
        dnp=dnp,
        fields_autoplaced=fields_autoplaced,
        uuid=uuid,
        fields=fields,
        pins=pins,
        instances=instances,
    )


# ---------------------------------------------------------------------------
# High-frequency sch items (T3a)
# ---------------------------------------------------------------------------


def _load_junction(node: SExprNode) -> Junction:
    """Parse ``(junction (at X Y) (diameter D) (color R G B A) (uuid …))``."""
    _require_sym(node, "junction")
    position = (0.0, 0.0)
    diameter = 0.0
    color = None
    uuid = None
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "diameter":
            diameter = _get_float(cc[0])
        elif cname == "color":
            color = _load_color(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in junction: {cname!r}")
    j = Junction(position=position, diameter=diameter, uuid=uuid)
    if color is not None:
        j.color = color
    return j


def _load_no_connect(node: SExprNode) -> NoConnect:
    """Parse ``(no_connect (at X Y) (uuid …))``."""
    _require_sym(node, "no_connect")
    position = (0.0, 0.0)
    uuid = None
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in no_connect: {cname!r}")
    return NoConnect(position=position, uuid=uuid)


def _load_bus_entry(node: SExprNode) -> BusEntry:
    """Parse ``(bus_entry (at X Y) (size W H) <stroke> (uuid …))``."""
    _require_sym(node, "bus_entry")
    position = (0.0, 0.0)
    size = (0.0, 0.0)
    stroke = Stroke()
    uuid = None
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "size":
            size = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in bus_entry: {cname!r}")
    return BusEntry(position=position, size=size, stroke=stroke, uuid=uuid)


def _load_line(node: SExprNode, line_type: LineType) -> Line:
    """Parse a ``(wire|bus …)`` 2-point line (``parseLine``)."""
    pts = (0.0, 0.0)
    end = (0.0, 0.0)
    stroke = Stroke()
    uuid = None
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "pts":
            xy = _load_pts(child)
            if len(xy) >= 2:
                pts, end = xy[0], xy[1]
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in {line_type.value}: {cname!r}")
    return Line(line_type=line_type, start=pts, end=end, stroke=stroke, uuid=uuid)


def _load_polyline_item(node: SExprNode) -> Line | GraphicShape:
    """Disambiguate a root ``(polyline …)`` by point count (parser.cpp:2929-2955).

    Two points -> a ``SCH_LINE`` on ``LAYER_NOTES`` (``Line``); more than two ->
    a graphic ``SchPolyline`` polygon. Matches KiCad's ``parseSchPolyLine`` split.
    """
    pts_node = None
    for child in _children(node):
        if child.node_type == SExprNodeType.LIST and _sym_name(child) == "pts":
            pts_node = child
            break
    npts = len(_load_pts(pts_node)) if pts_node is not None else 0
    if npts == 2:
        return _load_line(node, LineType.polyline)
    return _load_polyline(node)


def _load_label(node: SExprNode, kind: LabelKind) -> Label:
    """Parse a ``(label|global_label|hierarchical_label|netclass_flag …)`` (saveText)."""
    ch = _children(node)
    if not ch:
        raise KicadParseError(f"{kind.value} missing text")
    content = _get_str(ch[0])
    length = None
    shape = None
    position = (0.0, 0.0)
    angle = 0.0
    fields_autoplaced = False
    attributes = None
    uuid = None
    fields = []
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "length":
            length = _get_float(cc[0])
        elif cname == "shape":
            shape = _get_str(cc[0])
        elif cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "fields_autoplaced":
            fields_autoplaced = _bool_token(child)
        elif cname == "effects":
            attributes = _load_effects(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "property":
            fields.append(_load_property(child))
        elif cname == "iref":
            continue  # legacy intersheet-refs coords; superseded by property field
        else:
            raise KicadParseError(f"Unknown token in {kind.value}: {cname!r}")
    return Label(
        kind=kind,
        content=content,
        length=length,
        shape=shape,
        position=position,
        angle=angle,
        fields_autoplaced=fields_autoplaced,
        attributes=attributes,
        uuid=uuid,
        fields=fields,
    )


def _load_text(node: SExprNode) -> SchText:
    """Parse a plain ``(text "…" (exclude_from_sim b) (at …) <effects> (uuid …))``."""
    _require_sym(node, "text")
    ch = _children(node)
    if not ch:
        raise KicadParseError("text missing content")
    content = _get_str(ch[0])
    exclude_from_sim = False
    position = (0.0, 0.0)
    angle = 0.0
    attributes = None
    uuid = None
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "effects":
            attributes = _load_effects(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in text: {cname!r}")
    return SchText(
        content=content,
        exclude_from_sim=exclude_from_sim,
        position=position,
        angle=angle,
        attributes=attributes,
        uuid=uuid,
    )


def _load_text_box(node: SExprNode) -> SchTextBox:
    """Parse a schematic ``(text_box …)`` (``parseSchTextBoxContent`` :4801)."""
    _require_sym(node, "text_box")
    ch = _children(node)
    if not ch:
        raise KicadParseError("text_box missing content")
    content = _get_str(ch[0])
    exclude_from_sim = False
    position = (0.0, 0.0)
    angle = 0.0
    size = (0.0, 0.0)
    margins = (0.0, 0.0, 0.0, 0.0)
    stroke = Stroke()
    fill = Fill()
    attributes = None
    uuid = None
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "size":
            size = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "margins":
            margins = (
                _get_float(cc[0]),
                _get_float(cc[1]),
                _get_float(cc[2]),
                _get_float(cc[3]),
            )
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "effects":
            attributes = _load_effects(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in text_box: {cname!r}")
    return SchTextBox(
        content=content,
        exclude_from_sim=exclude_from_sim,
        position=position,
        angle=angle,
        size=size,
        margins=margins,
        stroke=stroke,
        fill=fill,
        attributes=attributes,
        uuid=uuid,
    )


# ---------------------------------------------------------------------------
# Lower-frequency sch items (T3b)
# ---------------------------------------------------------------------------


def _load_rule_area(node: SExprNode) -> RuleArea:
    """Parse ``(rule_area <4 bools> <polyline>)`` (``parseSchRuleArea`` :4466)."""
    _require_sym(node, "rule_area")
    exclude_from_sim = False
    in_bom = True
    on_board = True
    dnp = False
    shape = None
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "dnp":
            dnp = _bool_token(child)
        elif cname == "polyline":
            shape = _load_polyline(child)
        else:
            raise KicadParseError(f"Unknown token in rule_area: {cname!r}")
    if shape is None:
        raise KicadParseError("rule_area missing polyline")
    return RuleArea(
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        dnp=dnp,
        shape=shape,
    )


def _load_image(node: SExprNode) -> Image:
    """Parse ``(image (at X Y) [(scale g)] (uuid …) (data "…" …))`` (``parseImage`` :3593)."""
    _require_sym(node, "image")
    position = (0.0, 0.0)
    scale = None
    uuid = None
    data = ""
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "scale":
            scale = _get_float(cc[0])
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "data":
            data = "".join(
                _get_str(c)
                for c in cc
                if c.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            )
        else:
            raise KicadParseError(f"Unknown token in image: {cname!r}")
    return Image(position=position, scale=scale, uuid=uuid, data=data)


def _load_group(node: SExprNode) -> Group:
    """Parse ``(group "name" (uuid …) [(locked yes)] [(lib_id …)] (members …))`` (:5144)."""
    _require_sym(node, "group")
    ch = _children(node)
    name = ""
    idx = 0
    if ch and ch[0].node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL):
        name = _get_str(ch[0])
        idx = 1
    uuid = None
    locked = False
    lib_id = None
    members: list[str] = []
    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "locked":
            locked = _bool_token(child)
        elif cname == "lib_id":
            lib_id = _get_str(cc[0])
        elif cname == "members":
            members = [_get_str(m) for m in cc]
        else:
            raise KicadParseError(f"Unknown token in group: {cname!r}")
    return Group(name=name, uuid=uuid, locked=locked, lib_id=lib_id, members=members)


def _load_table_cell(node: SExprNode) -> SchTableCell:
    """Parse a ``(table_cell …)`` (``parseSchTextBoxContent`` + span :4858-4863)."""
    _require_sym(node, "table_cell")
    ch = _children(node)
    if not ch:
        raise KicadParseError("table_cell missing content")
    content = _get_str(ch[0])
    exclude_from_sim = False
    position = (0.0, 0.0)
    angle = 0.0
    size = (0.0, 0.0)
    margins = (0.0, 0.0, 0.0, 0.0)
    span = (1, 1)
    fill = Fill()
    attributes = None
    uuid = None
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "size":
            size = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "margins":
            margins = (
                _get_float(cc[0]),
                _get_float(cc[1]),
                _get_float(cc[2]),
                _get_float(cc[3]),
            )
        elif cname == "span":
            span = (_get_int(cc[0]), _get_int(cc[1]))
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "effects":
            attributes = _load_effects(child)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in table_cell: {cname!r}")
    return SchTableCell(
        content=content,
        exclude_from_sim=exclude_from_sim,
        position=position,
        angle=angle,
        size=size,
        margins=margins,
        span=span,
        fill=fill,
        attributes=attributes,
        uuid=uuid,
    )


def _load_table(node: SExprNode) -> SchTable:
    """Parse ``(table …)`` (``parseSchTable`` :4932)."""
    _require_sym(node, "table")
    column_count = 0
    border_external = False
    border_header = False
    border_stroke = Stroke()
    separators_rows = False
    separators_cols = False
    separators_stroke = Stroke()
    column_widths: list[float] = []
    row_heights: list[float] = []
    uuid = None
    cells: list[SchTableCell] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "column_count":
            column_count = _get_int(cc[0])
        elif cname == "border":
            for b in cc:
                if b.node_type != SExprNodeType.LIST:
                    continue
                bname = _sym_name(b)
                if bname == "external":
                    border_external = _bool_token(b)
                elif bname == "header":
                    border_header = _bool_token(b)
                elif bname == "stroke":
                    border_stroke = _load_stroke(b)
                else:
                    raise KicadParseError(f"Unknown token in table border: {bname!r}")
        elif cname == "separators":
            for s in cc:
                if s.node_type != SExprNodeType.LIST:
                    continue
                sname = _sym_name(s)
                if sname == "rows":
                    separators_rows = _bool_token(s)
                elif sname == "cols":
                    separators_cols = _bool_token(s)
                elif sname == "stroke":
                    separators_stroke = _load_stroke(s)
                else:
                    raise KicadParseError(f"Unknown token in table separators: {sname!r}")
        elif cname == "column_widths":
            column_widths = [_get_float(w) for w in cc]
        elif cname == "row_heights":
            row_heights = [_get_float(h) for h in cc]
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "cells":
            cells = [
                _load_table_cell(c)
                for c in cc
                if c.node_type == SExprNodeType.LIST and _sym_name(c) == "table_cell"
            ]
        else:
            raise KicadParseError(f"Unknown token in table: {cname!r}")
    return SchTable(
        column_count=column_count,
        border_external=border_external,
        border_header=border_header,
        border_stroke=border_stroke,
        separators_rows=separators_rows,
        separators_cols=separators_cols,
        separators_stroke=separators_stroke,
        column_widths=column_widths,
        row_heights=row_heights,
        uuid=uuid,
        cells=cells,
    )


# ---------------------------------------------------------------------------
# Sheet node + sheet-instance blocks (T4)
# ---------------------------------------------------------------------------


def _load_sheet_pin(node: SExprNode) -> SheetPin:
    """Parse ``(pin "name" <shape> (at X Y ANGLE) (uuid …) <effects>)`` (saveSheet :1109)."""
    _require_sym(node, "pin")
    ch = _children(node)
    if len(ch) < 2:
        raise KicadParseError("sheet pin missing name or shape")
    name = _get_str(ch[0])
    shape = _get_str(ch[1])
    position = (0.0, 0.0)
    angle = 0.0
    uuid = None
    attributes = None
    for child in ch[2:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
            if len(cc) > 2:
                angle = _get_float(cc[2])
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "effects":
            attributes = _load_effects(child)
        else:
            raise KicadParseError(f"Unknown token in sheet pin: {cname!r}")
    return SheetPin(
        name=name,
        shape=shape,
        position=position,
        angle=angle,
        uuid=uuid,
        attributes=attributes,
    )


def _load_sheet_variant(node: SExprNode) -> SheetVariant:
    """Parse a per-sheet instance ``(variant (name …) …)`` (saveSheet :1202-1225).

    Accepts the parser-tolerant ``on_board``/``in_pos_files`` bools even though the
    10.0.3 writer never emits them (A1 §4b/§4c). ``in_bom`` is stored as the literal
    on-disk value (no ExcludedFromBOM inversion in this layer).
    """
    _require_sym(node, "variant")
    name = ""
    dnp = None
    exclude_from_sim = None
    in_bom = None
    on_board = None
    in_pos_files = None
    fields: list[SheetVariantField] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "name":
            name = _get_str(cc[0])
        elif cname == "dnp":
            dnp = _bool_token(child)
        elif cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "in_pos_files":
            in_pos_files = _bool_token(child)
        elif cname == "field":
            fname = ""
            fvalue = ""
            for f in cc:
                if f.node_type != SExprNodeType.LIST:
                    continue
                if _sym_name(f) == "name":
                    fname = _get_str(_children(f)[0])
                elif _sym_name(f) == "value":
                    fvalue = _get_str(_children(f)[0])
            fields.append(SheetVariantField(name=fname, value=fvalue))
        else:
            raise KicadParseError(f"Unknown token in sheet variant: {cname!r}")
    return SheetVariant(
        name=name,
        dnp=dnp,
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        in_pos_files=in_pos_files,
        fields=fields,
    )


def _load_sheet_instance_path(node: SExprNode) -> SheetInstancePath:
    """Parse a per-sheet ``(path "<full>" (page "<n>") [variant…])`` (saveSheet :1196)."""
    _require_sym(node, "path")
    ch = _children(node)
    if not ch:
        raise KicadParseError("sheet instance path missing path string")
    path = _get_str(ch[0])
    page = ""
    variants: list[SheetVariant] = []
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "page":
            page = _get_str(_children(child)[0])
        elif cname == "variant":
            variants.append(_load_sheet_variant(child))
        else:
            raise KicadParseError(f"Unknown token in sheet instance path: {cname!r}")
    return SheetInstancePath(path=path, page=page, variants=variants)


def _load_sheet_instances(node: SExprNode) -> list[SheetInstanceProject]:
    """Parse a sheet's per-sheet ``(instances (project "name" (path …) …) …)`` (:1140-1237)."""
    _require_sym(node, "instances")
    projects: list[SheetInstanceProject] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname != "project":
            raise KicadParseError(f"Unknown token in sheet instances: {cname!r}")
        pch = _children(child)
        if not pch:
            raise KicadParseError("sheet instances project missing name")
        pname = _get_str(pch[0])
        paths = [
            _load_sheet_instance_path(p)
            for p in pch[1:]
            if p.node_type == SExprNodeType.LIST and _sym_name(p) == "path"
        ]
        projects.append(SheetInstanceProject(name=pname, paths=paths))
    return projects


def _load_sheet(node: SExprNode) -> Sheet:
    """Parse a ``(sheet …)`` sub-sheet node (``saveSheet`` :1069-1241)."""
    _require_sym(node, "sheet")
    position = (0.0, 0.0)
    size = (0.0, 0.0)
    exclude_from_sim = False
    in_bom = True
    on_board = True
    dnp = False
    fields_autoplaced = False
    stroke = Stroke()
    fill_color = Color(r=0, g=0, b=0, a=0.0)
    uuid = None
    fields: list[Property] = []
    pins: list[SheetPin] = []
    instances: list[SheetInstanceProject] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "at":
            position = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "size":
            size = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "exclude_from_sim":
            exclude_from_sim = _bool_token(child)
        elif cname == "in_bom":
            in_bom = _bool_token(child)
        elif cname == "on_board":
            on_board = _bool_token(child)
        elif cname == "dnp":
            dnp = _bool_token(child)
        elif cname == "fields_autoplaced":
            fields_autoplaced = _bool_token(child)
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            for f in cc:
                if f.node_type == SExprNodeType.LIST and _sym_name(f) == "color":
                    fill_color = _load_color(f)
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "property":
            fields.append(_load_property(child))
        elif cname == "pin":
            pins.append(_load_sheet_pin(child))
        elif cname == "instances":
            instances = _load_sheet_instances(child)
        else:
            raise KicadParseError(f"Unknown token in sheet: {cname!r}")
    return Sheet(
        position=position,
        size=size,
        exclude_from_sim=exclude_from_sim,
        in_bom=in_bom,
        on_board=on_board,
        dnp=dnp,
        fields_autoplaced=fields_autoplaced,
        stroke=stroke,
        fill_color=fill_color,
        uuid=uuid,
        fields=fields,
        pins=pins,
        instances=instances,
    )


def _load_root_sheet_instances(node: SExprNode) -> SheetInstances:
    """Parse the root ``(sheet_instances (path "<p>" (page "<n>")) …)`` (``saveInstances`` :1646).

    Flat list of ``(path (page))`` pairs; accepts ONLY ``path`` children
    (parser.cpp:2609), and inside each only ``page`` (parser.cpp:2588).
    """
    _require_sym(node, "sheet_instances")
    paths: list[RootInstancePath] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname != "path":
            raise KicadParseError(f"Unknown token in sheet_instances: {cname!r}")
        pch = _children(child)
        if not pch:
            raise KicadParseError("sheet_instances path missing path string")
        path = _get_str(pch[0])
        page = ""
        for gc in pch[1:]:
            if gc.node_type != SExprNodeType.LIST:
                continue
            if _sym_name(gc) == "page":
                page = _get_str(_children(gc)[0])
            else:
                raise KicadParseError(f"Unknown token in sheet_instances path: {_sym_name(gc)!r}")
        paths.append(RootInstancePath(path=path, page=page))
    return SheetInstances(paths=paths)


# ---------------------------------------------------------------------------
# lib_symbols block
# ---------------------------------------------------------------------------


def _load_lib_symbols(node: SExprNode) -> list[Symbol]:
    """Parse ``(lib_symbols (symbol "Lib:Name" …) …)``.

    Each entry reuses the shared ``.kicad_sym`` symbol parser; the top-level
    ``Lib:Name`` qualification is stored verbatim in ``Symbol.name`` (per-unit
    child names stay bare), so no context flag is needed on the read path.
    """
    _require_sym(node, "lib_symbols")
    symbols: list[Symbol] = []
    for child in _children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname != "symbol":
            raise KicadParseError(f"Unknown token in lib_symbols: {cname!r}")
        symbols.append(_load_symbol(child))
    return symbols


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


def load_sch(tree: SExprNode) -> Schematic:
    """Load a ``Schematic`` from a parsed S-expression tree.

    Raises:
        KicadParseError: if the tree is not a valid schematic, or carries a
            root token not yet modeled at this iteration phase (no silent drop).
    """
    _require_sym(tree, "kicad_sch")

    version = 20260306
    generator = "eeschema"
    generator_version = "10.0"
    uuid = None
    paper = None
    title_block = None
    lib_symbols: list[Symbol] = []
    symbols: list[PlacedSymbol] = []
    graphic_shapes: list[GraphicShape] = []
    texts: list[SchText] = []
    text_boxes: list[SchTextBox] = []
    junctions: list[Junction] = []
    no_connects: list[NoConnect] = []
    bus_entries: list[BusEntry] = []
    lines: list[Line] = []
    labels: list[Label] = []
    images: list[Image] = []
    tables: list[SchTable] = []
    rule_areas: list[RuleArea] = []
    groups: list[Group] = []
    sheets: list[Sheet] = []
    sheet_instances = None
    embedded_fonts = None
    embedded_files = []

    for child in _children(tree):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        cc = _children(child)
        if cname == "version":
            version = _get_int(cc[0])
        elif cname == "generator":
            generator = _get_str(cc[0])
        elif cname == "generator_version":
            generator_version = _get_str(cc[0])
        elif cname == "uuid":
            uuid = _get_str(cc[0])
        elif cname == "paper":
            paper = _load_paper(child)
        elif cname == "title_block":
            title_block = _load_title_block(child)
        elif cname == "lib_symbols":
            lib_symbols = _load_lib_symbols(child)
        elif cname == "symbol":
            symbols.append(_load_placed_symbol(child))
        elif cname == "junction":
            junctions.append(_load_junction(child))
        elif cname == "no_connect":
            no_connects.append(_load_no_connect(child))
        elif cname == "bus_entry":
            bus_entries.append(_load_bus_entry(child))
        elif cname == "wire":
            lines.append(_load_line(child, LineType.wire))
        elif cname == "bus":
            lines.append(_load_line(child, LineType.bus))
        elif cname == "polyline":
            item = _load_polyline_item(child)
            if isinstance(item, Line):
                lines.append(item)
            else:
                graphic_shapes.append(item)
        elif cname == "arc":
            graphic_shapes.append(_load_arc(child, invert_y=False))
        elif cname == "circle":
            graphic_shapes.append(_load_circle(child))
        elif cname == "rectangle":
            graphic_shapes.append(_load_rect(child))
        elif cname == "bezier":
            graphic_shapes.append(_load_bezier(child))
        elif cname == "text":
            texts.append(_load_text(child))
        elif cname == "label":
            labels.append(_load_label(child, LabelKind.local))
        elif cname == "global_label":
            labels.append(_load_label(child, LabelKind.global_))
        elif cname == "hierarchical_label":
            labels.append(_load_label(child, LabelKind.hierarchical))
        elif cname in ("netclass_flag", "directive_label"):
            labels.append(_load_label(child, LabelKind.netclass_flag))
        elif cname == "text_box":
            text_boxes.append(_load_text_box(child))
        elif cname == "rule_area":
            rule_areas.append(_load_rule_area(child))
        elif cname == "image":
            images.append(_load_image(child))
        elif cname == "group":
            groups.append(_load_group(child))
        elif cname == "table":
            tables.append(_load_table(child))
        elif cname == "sheet":
            sheets.append(_load_sheet(child))
        elif cname == "sheet_instances":
            sheet_instances = _load_root_sheet_instances(child)
        elif cname == "embedded_fonts":
            embedded_fonts = _bool_token(child)
        elif cname == "embedded_files":
            embedded_files = _load_embedded_files(child)
        elif cname in ("symbol_instances", "bus_alias"):
            # Read and dropped, which is what KiCad does with both.
            #
            # `symbol_instances` is the KiCad 6 shared instance block
            # (``sch_io_kicad_sexpr_parser.cpp:2618``). Its contents migrate into
            # each symbol's own ``(instances …)`` on load.
            #
            # `bus_alias` is read into the SCH_SCREEN (``:5080-5106``).
            #
            # Neither token has a writer. The only string literal holding either
            # name in the whole KiCad tree is the parser's own Expecting()
            # message at ``:3057`` and ``:3059``. Measured: `kicad-cli sch
            # upgrade` on a file carrying one emits zero occurrences of it.
            # Preserving either would therefore make our output differ from
            # KiCad's, so dropping is the byte-faithful choice.
            pass
        else:
            raise KicadParseError(f"Unknown token in kicad_sch: {cname!r}")

    return Schematic(
        version=version,
        generator=generator,
        generator_version=generator_version,
        uuid=uuid,
        paper=paper,
        title_block=title_block,
        lib_symbols=lib_symbols,
        symbols=symbols,
        graphic_shapes=graphic_shapes,
        texts=texts,
        text_boxes=text_boxes,
        junctions=junctions,
        no_connects=no_connects,
        bus_entries=bus_entries,
        lines=lines,
        labels=labels,
        images=images,
        tables=tables,
        rule_areas=rule_areas,
        groups=groups,
        sheets=sheets,
        sheet_instances=sheet_instances,
        embedded_fonts=embedded_fonts,
        embedded_files=embedded_files,
    )
