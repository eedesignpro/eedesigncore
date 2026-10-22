"""SCH-family symbol draw-item loaders (the ``Symbol`` sub-grammar).

Parsers for the items inside a symbol unit: shapes (arc/circle/rectangle/bezier/
polyline), pin, text, text_box, and the unit dispatch. Moved verbatim from
``sym/loader.py`` so the iteration-10 ``sch/loader.py`` reuses them rather than
copying (ADR-015). ``common/`` never imports a format sub-package; formats
import from here.
"""

from __future__ import annotations

import re

from eedesigncore.common.arcs import normalize_arc_mm
from eedesigncore.common.loaders import bool_token as _bool_token
from eedesigncore.common.loaders import children as _children
from eedesigncore.common.loaders import find_child as _find_child
from eedesigncore.common.loaders import get_float as _get_float
from eedesigncore.common.loaders import get_str as _get_str
from eedesigncore.common.loaders import load_effects as _load_effects
from eedesigncore.common.loaders import load_fill as _load_fill
from eedesigncore.common.loaders import load_stroke as _load_stroke
from eedesigncore.common.loaders import require_sym as _require_sym
from eedesigncore.common.loaders import sym_name as _sym_name
from eedesigncore.common.models import Fill, Stroke
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.ordering import SCH_IU_PER_MM
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import SymbolUnit
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# ---------------------------------------------------------------------------
# Graphic shapes
# ---------------------------------------------------------------------------


def _load_pts(node: SExprNode) -> list[tuple[float, float]]:
    """Parse a ``(pts (xy x y) ...)`` block."""
    pts = []
    for child in _children(node):
        if child.node_type == SExprNodeType.LIST and _sym_name(child) == "xy":
            xyc = _children(child)
            pts.append((_get_float(xyc[0]), _get_float(xyc[1])))
    return pts


def _load_arc(node: SExprNode, *, invert_y: bool) -> SchArc:
    """Parse an ``(arc …)`` node and normalise its winding as KiCad does at load.

    *invert_y* is True for a library symbol arc, whose file Y is the negated
    internal Y, and False for a schematic arc (``sch_io_kicad_sexpr.cpp:1317-1318``
    passes ``aInvertY`` false, ``sch_io_kicad_sexpr_lib_cache.cpp:612`` passes
    true). See :mod:`eedesigncore.common.arcs` and ADR-067.
    """
    _require_sym(node, "arc")
    ch = _children(node)
    private = False
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    start = (0.0, 0.0)
    mid = (0.0, 0.0)
    end = (0.0, 0.0)
    stroke = Stroke()
    fill = Fill()
    uuid = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "start":
            sc = _children(child)
            start = (_get_float(sc[0]), _get_float(sc[1]))
        elif cname == "mid":
            mc = _children(child)
            mid = (_get_float(mc[0]), _get_float(mc[1]))
        elif cname == "end":
            ec = _children(child)
            end = (_get_float(ec[0]), _get_float(ec[1]))
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in arc: {cname!r}")

    start, mid, end = normalize_arc_mm(start, mid, end, iu_per_mm=SCH_IU_PER_MM, invert_y=invert_y)
    return SchArc(
        private=private, start=start, mid=mid, end=end, stroke=stroke, fill=fill, id=uuid
    )


def _load_circle(node: SExprNode) -> SchCircle:
    _require_sym(node, "circle")
    ch = _children(node)
    private = False
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    center = (0.0, 0.0)
    radius = 0.0
    stroke = Stroke()
    fill = Fill()
    uuid = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "center":
            cc = _children(child)
            center = (_get_float(cc[0]), _get_float(cc[1]))
        elif cname == "radius":
            radius = _get_float(_children(child)[0])
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in circle: {cname!r}")

    return SchCircle(
        private=private, center=center, radius=radius, stroke=stroke, fill=fill, id=uuid
    )


def _load_rect(node: SExprNode) -> SchRect:
    _require_sym(node, "rectangle")
    ch = _children(node)
    private = False
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    start = (0.0, 0.0)
    end = (0.0, 0.0)
    corner_radius = None
    stroke = Stroke()
    fill = Fill()
    uuid = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "start":
            sc = _children(child)
            start = (_get_float(sc[0]), _get_float(sc[1]))
        elif cname == "end":
            ec = _children(child)
            end = (_get_float(ec[0]), _get_float(ec[1]))
        elif cname == "radius":
            corner_radius = _get_float(_children(child)[0])
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in rectangle: {cname!r}")

    return SchRect(
        private=private,
        start=start,
        end=end,
        corner_radius=corner_radius,
        stroke=stroke,
        fill=fill,
        id=uuid,
    )


def _load_bezier(node: SExprNode) -> SchBezier:
    _require_sym(node, "bezier")
    ch = _children(node)
    private = False
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    pts: list[tuple[float, float]] = []
    stroke = Stroke()
    fill = Fill()
    uuid = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "pts":
            pts = _load_pts(child)
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in bezier: {cname!r}")

    return SchBezier(private=private, pts=pts, stroke=stroke, fill=fill, id=uuid)


def _load_polyline(node: SExprNode) -> SchPolyline:
    _require_sym(node, "polyline")
    ch = _children(node)
    private = False
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    pts: list[tuple[float, float]] = []
    stroke = Stroke()
    fill = Fill()
    uuid = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "pts":
            pts = _load_pts(child)
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "uuid":
            uuid = _get_str(_children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in polyline: {cname!r}")

    return SchPolyline(private=private, pts=pts, stroke=stroke, fill=fill, id=uuid)


# ---------------------------------------------------------------------------
# Text items
# ---------------------------------------------------------------------------


def _load_sym_text(node: SExprNode) -> SymText:
    _require_sym(node, "text")
    ch = _children(node)
    idx = 0
    private = False

    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    if idx >= len(ch):
        raise KicadParseError("text missing content")
    content = _get_str(ch[idx])
    idx += 1

    position = (0.0, 0.0)
    angle = 0
    attributes = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "at":
            atc = _children(child)
            position = (_get_float(atc[0]), _get_float(atc[1]))
            if len(atc) > 2:
                # Angle in tenths of degrees stored as integer
                angle = int(_get_float(atc[2]))
        elif cname == "effects":
            attributes = _load_effects(child)
        else:
            raise KicadParseError(f"Unknown token in sym text: {cname!r}")

    return SymText(
        private=private, content=content, position=position, angle=angle, attributes=attributes
    )


def _load_sym_text_box(node: SExprNode) -> SymTextBox:
    _require_sym(node, "text_box")
    ch = _children(node)
    idx = 0
    private = False

    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "private":
        private = True
        idx += 1

    if idx >= len(ch):
        raise KicadParseError("text_box missing content")
    content = _get_str(ch[idx])
    idx += 1

    position = (0.0, 0.0)
    angle = 0.0
    size = (0.0, 0.0)
    margins = (0.0, 0.0, 0.0, 0.0)
    stroke = Stroke()
    fill = Fill()
    attributes = None

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "at":
            atc = _children(child)
            position = (_get_float(atc[0]), _get_float(atc[1]))
            if len(atc) > 2:
                angle = _get_float(atc[2])
        elif cname == "size":
            sc = _children(child)
            size = (_get_float(sc[0]), _get_float(sc[1]))
        elif cname == "margins":
            mc = _children(child)
            margins = (
                _get_float(mc[0]),
                _get_float(mc[1]),
                _get_float(mc[2]),
                _get_float(mc[3]),
            )
        elif cname == "stroke":
            stroke = _load_stroke(child)
        elif cname == "fill":
            fill = _load_fill(child)
        elif cname == "effects":
            attributes = _load_effects(child)
        else:
            raise KicadParseError(f"Unknown token in sym text_box: {cname!r}")

    return SymTextBox(
        private=private,
        content=content,
        position=position,
        angle=angle,
        size=size,
        margins=margins,
        stroke=stroke,
        fill=fill,
        attributes=attributes,
    )


# ---------------------------------------------------------------------------
# Pin
# ---------------------------------------------------------------------------


def _load_pin(node: SExprNode) -> Pin:
    _require_sym(node, "pin")
    ch = _children(node)

    if len(ch) < 2:
        raise KicadParseError("pin missing electrical_type and/or shape")

    et_str = str(ch[0].value)
    shape_str = str(ch[1].value)

    try:
        electrical_type = PinElectricalType(et_str)
    except ValueError as err:
        raise KicadParseError(f"Unknown pin electrical type: {et_str!r}") from err

    try:
        shape = PinShape(shape_str)
    except ValueError as err:
        raise KicadParseError(f"Unknown pin shape: {shape_str!r}") from err

    position = (0.0, 0.0)
    angle = 0.0
    length = 2.54
    visible = True
    name = ""
    name_text_size = 1.27
    number = ""
    number_text_size = 1.27
    alternates: list[PinAlternate] = []

    for child in ch[2:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "at":
            atc = _children(child)
            position = (_get_float(atc[0]), _get_float(atc[1]))
            if len(atc) > 2:
                angle = _get_float(atc[2])
        elif cname == "length":
            length = _get_float(_children(child)[0])
        elif cname == "hide":
            visible = not _bool_token(child)
        elif cname == "name":
            nc = _children(child)
            name = _get_str(nc[0])
            eff = _find_child(child, "effects")
            if eff is not None:
                font = _find_child(eff, "font")
                if font is not None:
                    size_node = _find_child(font, "size")
                    if size_node is not None:
                        sc = _children(size_node)
                        name_text_size = _get_float(sc[0])
        elif cname == "number":
            nc = _children(child)
            number = _get_str(nc[0])
            eff = _find_child(child, "effects")
            if eff is not None:
                font = _find_child(eff, "font")
                if font is not None:
                    size_node = _find_child(font, "size")
                    if size_node is not None:
                        sc = _children(size_node)
                        number_text_size = _get_float(sc[0])
        elif cname == "alternate":
            ac = _children(child)
            alt_name = _get_str(ac[0])
            alt_et_str = str(ac[1].value)
            alt_shape_str = str(ac[2].value)
            try:
                alt_et = PinElectricalType(alt_et_str)
            except ValueError as err:
                raise KicadParseError(
                    f"Unknown pin electrical type in alternate: {alt_et_str!r}"
                ) from err
            try:
                alt_shape = PinShape(alt_shape_str)
            except ValueError as err:
                raise KicadParseError(
                    f"Unknown pin shape in alternate: {alt_shape_str!r}"
                ) from err
            alternates.append(PinAlternate(name=alt_name, electrical_type=alt_et, shape=alt_shape))
        else:
            raise KicadParseError(f"Unknown token in pin: {cname!r}")

    return Pin(
        electrical_type=electrical_type,
        shape=shape,
        position=position,
        angle=angle,
        length=length,
        visible=visible,
        name=name,
        name_text_size=name_text_size,
        number=number,
        number_text_size=number_text_size,
        alternates=alternates,
    )


# ---------------------------------------------------------------------------
# Unit sub-symbol
# ---------------------------------------------------------------------------

# Trailing "_<unit>_<body style>" of a unit sub-symbol name, e.g. "_1_1", "_0_1",
# "_2_0". Index 0 means "common to all", per KiCad.
_UNIT_SUFFIX_RE = re.compile(r"_(\d+)_(\d+)$")


def _load_unit(node: SExprNode) -> SymbolUnit:
    """Parse a unit sub-symbol ``(symbol "name_u_b" ...)``.

    The two indices come from the trailing ``_<unit>_<body style>`` of the unit
    name itself. Do not subtract the parent name. The parent name is qualified
    ("Lib:Name") in schematic context and bare in library context, so a
    subtraction reads 0 for every schematic unit (UF-003). KiCad rejects a unit
    name whose suffix is not two integers
    (``sch_io_kicad_sexpr_parser.cpp:509-531``), so this raises too.
    """
    _require_sym(node, "symbol")
    ch = _children(node)
    name = _get_str(ch[0])

    match = _UNIT_SUFFIX_RE.search(name)
    if match is None:
        raise KicadParseError(
            f"Invalid symbol unit name suffix: {name!r}. "
            f"A unit name must end with _<unit>_<body style>."
        )
    unit_index = int(match.group(1))
    body_style_index = int(match.group(2))

    unit_display_name = None
    items: list[object] = []

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = _sym_name(child)
        if cname == "unit_name":
            unit_display_name = _get_str(_children(child)[0])
        elif cname == "arc":
            items.append(_load_arc(child, invert_y=True))
        elif cname == "circle":
            items.append(_load_circle(child))
        elif cname == "rectangle":
            items.append(_load_rect(child))
        elif cname == "bezier":
            items.append(_load_bezier(child))
        elif cname == "polyline":
            items.append(_load_polyline(child))
        elif cname == "pin":
            items.append(_load_pin(child))
        elif cname == "text":
            items.append(_load_sym_text(child))
        elif cname == "text_box":
            items.append(_load_sym_text_box(child))
        else:
            raise KicadParseError(f"Unknown token in symbol unit: {cname!r}")

    return SymbolUnit(
        name=name,
        unit_index=unit_index,
        body_style_index=body_style_index,
        unit_display_name=unit_display_name,
        items=items,
    )
