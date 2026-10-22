"""Shared-child PCB-family loaders (Tier C: board + footprint).

Parsers for the graphic payloads reachable from BOTH ``format(BOARD)`` and
``format(FOOTPRINT)`` (arch-plan section 6): shapes/text/textbox, group, zone
(and sub-blocks), dimension (and sub-blocks), table (and cells), reference
image, barcode, and pcb_point. Extracted verbatim from ``mod/loader.py`` so the
iteration-9 ``pcb/loader.py`` reuses them rather than copying (ADR-015).

``common/`` never imports a format sub-package; formats import from here.
"""

from __future__ import annotations

from typing import Literal, cast

from eedesigncore.common.arcs import normalize_arc_mm
from eedesigncore.common.loaders import (
    bool_token,
    children,
    find_child,
    get_float,
    get_int,
    get_str,
    load_effects,
    load_fill,
    load_stroke,
    require_sym,
    sym_name,
)
from eedesigncore.common.models import (
    Fill,
    PtsArc,
    PtsElement,
    RenderCache,
    RenderCachePolygon,
    Stroke,
)
from eedesigncore.common.pcb_family.board_items import (
    ArrowDirection,
    Barcode,
    BarcodeEcc,
    BarcodeKind,
    Dimension,
    DimensionFormat,
    DimensionStyle,
    DimensionText,
    DimensionType,
    ReferenceImage,
    Table,
    TableBorder,
    TableCell,
    TableSeparators,
    Zone,
    ZoneFill,
    ZoneFilledPolygon,
    ZoneKeepout,
    ZoneLayerProperty,
    ZoneOutlinePolygon,
    ZonePlacement,
)
from eedesigncore.common.pcb_family.ordering import PCB_IU_PER_MM
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbDrawItem,
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
)
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# ---------------------------------------------------------------------------
# Zone bare-symbol value validators (GAP-028): the legal value set for each
# token is fixed by the KiCad zone writer (pcb_io_kicad_sexpr.cpp:2862-3046).
# Reject anything outside it as a parse error and narrow to the Literal type.
# ---------------------------------------------------------------------------


def _as_hatch_style(v: str) -> Literal["none", "edge", "full"]:
    if v not in ("none", "edge", "full"):
        raise KicadParseError(f"Invalid zone hatch style: {v!r}")
    return cast('Literal["none", "edge", "full"]', v)


def _as_teardrop_type(v: str) -> Literal["padvia", "track_end"]:
    if v not in ("padvia", "track_end"):
        raise KicadParseError(f"Invalid zone teardrop type: {v!r}")
    return cast('Literal["padvia", "track_end"]', v)


def _as_connect_pads_mode(v: str) -> Literal["thru_hole_only", "yes", "no"]:
    if v not in ("thru_hole_only", "yes", "no"):
        raise KicadParseError(f"Invalid zone connect_pads mode: {v!r}")
    return cast('Literal["thru_hole_only", "yes", "no"]', v)


def _as_smoothing(v: str) -> Literal["chamfer", "fillet"]:
    if v not in ("chamfer", "fillet"):
        raise KicadParseError(f"Invalid zone smoothing: {v!r}")
    return cast('Literal["chamfer", "fillet"]', v)


def _as_hatch_border_algorithm(v: str) -> Literal["hatch_thickness", "min_thickness"]:
    if v not in ("hatch_thickness", "min_thickness"):
        raise KicadParseError(f"Invalid zone hatch_border_algorithm: {v!r}")
    return cast('Literal["hatch_thickness", "min_thickness"]', v)


# ---------------------------------------------------------------------------
# render_cache
# ---------------------------------------------------------------------------


def _load_render_cache(node: SExprNode) -> RenderCache:
    """Parse a (render_cache "text" angle (polygon ...) ...) node."""
    ch = children(node)
    resolved_text = ""
    angle = 0.0
    polygons: list[RenderCachePolygon] = []

    idx = 0
    if idx < len(ch) and ch[idx].node_type == SExprNodeType.STRING:
        resolved_text = get_str(ch[idx])
        idx += 1
    if idx < len(ch) and ch[idx].node_type in (SExprNodeType.DOUBLE, SExprNodeType.INTEGER):
        angle = get_float(ch[idx])
        idx += 1

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "polygon":
            pts: list[PtsElement] = []
            for sub in children(child):
                if sub.node_type == SExprNodeType.LIST and sym_name(sub) == "pts":
                    pts = _load_pts_list(sub)
            polygons.append(RenderCachePolygon(pts=pts))
        else:
            raise KicadParseError(f"Unknown token in render_cache: {cname!r}")

    return RenderCache(resolved_text=resolved_text, angle=angle, polygons=polygons)


# ---------------------------------------------------------------------------
# Drawing items
# ---------------------------------------------------------------------------


def _load_pts_arc(node: SExprNode) -> PtsArc:
    """Parse an ``(arc (start x y) (mid x y) (end x y))`` element of a pts chain."""
    start = mid = end = None
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        cc = children(child)
        if cname == "start":
            start = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "mid":
            mid = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "end":
            end = (get_float(cc[0]), get_float(cc[1]))
    if start is None or mid is None or end is None:
        raise KicadParseError("pts arc requires start, mid and end")
    return PtsArc(start=start, mid=mid, end=end)


def _load_pts_list(node: SExprNode) -> list[PtsElement]:
    """Parse a ``(pts (xy x y) | (arc …) ...)`` block (formatPolyPts)."""
    pts: list[PtsElement] = []
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "xy":
            xyc = children(child)
            pts.append((get_float(xyc[0]), get_float(xyc[1])))
        elif cname == "arc":
            pts.append(_load_pts_arc(child))
    return pts


def _load_layer_list(node: SExprNode) -> list[str]:
    """Parse a multi-layer ``(layers "L1" "L2" ...)`` block on a shape.

    Layer name tokens (incl. wildcards like ``*.Cu``) are preserved verbatim so
    the writer round-trips whatever KiCad emitted; the LSET wildcard collapsing
    is KiCad's write-time concern (pcb_io_kicad_sexpr.cpp formatLayers).
    """
    return [
        get_str(x)
        for x in children(node)
        if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
    ]


def _load_pcb_text(node: SExprNode, *, has_type: bool = True) -> PcbText:
    """Parse an ``fp_text`` / ``gr_text`` token.

    Footprint ``fp_text`` carries a leading type field
    ``(fp_text <type> "<text>" …)`` (``has_type=True``); board ``gr_text`` has no
    type field ``(gr_text "<text>" …)`` (``has_type=False``,
    ``pcb_io_kicad_sexpr.cpp:2277-2285`` -- the ``gr`` prefix leaves ``type``
    empty). The board loader renames ``gr_text`` -> ``fp_text`` before calling
    with ``has_type=False``; the board writer re-elides the type via ``prefix``.
    """
    require_sym(node, "fp_text")
    ch = children(node)

    if has_type:
        if len(ch) < 2:
            raise KicadParseError("fp_text missing type and/or content")
        text_type = get_str(ch[0])
        content = get_str(ch[1])
        rest = ch[2:]
    else:
        if len(ch) < 1:
            raise KicadParseError("gr_text missing content")
        text_type = ""
        content = get_str(ch[0])
        rest = ch[1:]

    locked = False
    position = (0.0, 0.0)
    angle = 0.0
    unlocked = False
    layer = ""
    knockout = False
    visible = True
    uuid = None
    attributes = None
    render_cache = None

    for child in rest:
        if child.node_type == SExprNodeType.SYMBOL:
            # bare "locked" keyword (older format)
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "locked":
            locked = bool_token(child)
        elif cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
            if len(atc) > 2:
                angle = get_float(atc[2])
        elif cname == "unlocked":
            unlocked = bool_token(child)
        elif cname == "layer":
            ch2 = children(child)
            layer = get_str(ch2[0])
            # Check for knockout keyword
            for x in ch2[1:]:
                if x.node_type == SExprNodeType.SYMBOL and x.value == "knockout":
                    knockout = True
        elif cname == "hide":
            visible = not bool_token(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        elif cname == "effects":
            attributes = load_effects(child)
        elif cname == "render_cache":
            render_cache = _load_render_cache(child)
        else:
            raise KicadParseError(f"Unknown token in fp_text: {cname!r}")

    return PcbText(
        text_type=text_type,
        content=content,
        position=position,
        angle=angle,
        layer=layer,
        knockout=knockout,
        unlocked=unlocked,
        locked=locked,
        visible=visible,
        id=uuid,
        attributes=attributes,
        render_cache=render_cache,
    )


def _shape_net_name(node: SExprNode) -> str | None:
    """Read a shape's ``(net …)`` reference, returning the NAME or None.

    ``parseNet`` (``pcb_io_kicad_sexpr_parser.cpp:296-330``) accepts a name or a
    legacy pre-10.0 netcode, and every shape parser routes through it. Reading
    the value as a name only made a board carrying a bare code fail to load.

    Three forms, matching ``pcb/loader.py::_parse_item_net`` so a shape and a
    track agree:

    - ``(net "<name>")``      the v10 form
    - ``(net <code> "<name>")`` legacy code plus name, the name wins
    - ``(net <code>)``        legacy code alone, so no name is available

    The last form returns None under contract A2. KiCad instead resolves the
    code against the board net table and writes the resolved NAME back, so our
    output drops a net this library cannot name. That is only reachable in a
    pre-10.0 file, and the corpus gate grades against a KiCad fixed point where
    every code is already resolved.
    """
    kids = children(node)
    if not kids:
        return None
    if len(kids) >= 2:
        return get_str(kids[1])
    first = kids[0]
    if first.node_type in (SExprNodeType.INTEGER, SExprNodeType.DOUBLE):
        return None
    return get_str(first)


def _load_pcb_line(node: SExprNode) -> PcbLine:
    """Parse an ``fp_line`` token."""
    require_sym(node, "fp_line")
    start = (0.0, 0.0)
    end = (0.0, 0.0)
    stroke = Stroke()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "start":
            sc = children(child)
            start = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_line: {cname!r}")

    return PcbLine(
        start=start,
        end=end,
        stroke=stroke,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_rect(node: SExprNode) -> PcbRect:
    """Parse an ``fp_rect`` token."""
    require_sym(node, "fp_rect")
    start = (0.0, 0.0)
    end = (0.0, 0.0)
    corner_radius = None
    stroke = Stroke()
    fill = Fill()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "start":
            sc = children(child)
            start = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "radius":
            corner_radius = get_float(children(child)[0])
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "fill":
            fill = load_fill(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_rect: {cname!r}")

    return PcbRect(
        start=start,
        end=end,
        corner_radius=corner_radius,
        stroke=stroke,
        fill=fill,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_circle(node: SExprNode) -> PcbCircle:
    """Parse an ``fp_circle`` token."""
    require_sym(node, "fp_circle")
    center = (0.0, 0.0)
    end = (0.0, 0.0)
    stroke = Stroke()
    fill = Fill()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "center":
            cc = children(child)
            center = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "fill":
            fill = load_fill(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_circle: {cname!r}")

    return PcbCircle(
        center=center,
        end=end,
        stroke=stroke,
        fill=fill,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_arc(node: SExprNode) -> PcbArc:
    """Parse an ``fp_arc`` token."""
    require_sym(node, "fp_arc")
    start = (0.0, 0.0)
    mid = (0.0, 0.0)
    end = (0.0, 0.0)
    stroke = Stroke()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "start":
            sc = children(child)
            start = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "mid":
            mc = children(child)
            mid = (get_float(mc[0]), get_float(mc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_arc: {cname!r}")

    # parsePCB_SHAPE routes every gr_arc and fp_arc through SetArcGeometry
    # (pcb_io_kicad_sexpr_parser.cpp:3281), which normalises the winding (ADR-067).
    start, mid, end = normalize_arc_mm(start, mid, end, iu_per_mm=PCB_IU_PER_MM)
    return PcbArc(
        start=start,
        mid=mid,
        end=end,
        stroke=stroke,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_poly(node: SExprNode) -> PcbPoly:
    """Parse an ``fp_poly`` token."""
    require_sym(node, "fp_poly")
    pts: list[PtsElement] = []
    stroke = Stroke()
    fill = Fill()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "pts":
            pts = _load_pts_list(child)
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "fill":
            fill = load_fill(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_poly: {cname!r}")

    return PcbPoly(
        pts=pts,
        stroke=stroke,
        fill=fill,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_curve(node: SExprNode) -> PcbCurve:
    """Parse an ``fp_curve`` token."""
    require_sym(node, "fp_curve")
    pts: list[PtsElement] = []
    stroke = Stroke()
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net_name = None
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "pts":
            pts = _load_pts_list(child)
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = _load_layer_list(child)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "net":
            net_name = _shape_net_name(child)
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in fp_curve: {cname!r}")

    return PcbCurve(
        pts=pts,
        stroke=stroke,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net_name=net_name,
        id=uuid,
    )


def _load_pcb_text_box(node: SExprNode) -> PcbTextBox:
    """Parse an ``fp_text_box`` token."""
    require_sym(node, "fp_text_box")
    ch = children(node)

    if not ch:
        raise KicadParseError("fp_text_box missing content")

    content = get_str(ch[0])
    locked = False
    start = None
    end = None
    pts: list[PtsElement] = []
    margins = (0.0, 0.0, 0.0, 0.0)
    angle = None
    layer = ""
    uuid = None
    attributes = None
    border_enabled = False
    stroke = Stroke()
    knockout = False
    render_cache = None

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "locked":
            locked = bool_token(child)
        elif cname == "start":
            sc = children(child)
            start = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "pts":
            pts = _load_pts_list(child)
        elif cname == "margins":
            mc = children(child)
            margins = (
                get_float(mc[0]),
                get_float(mc[1]),
                get_float(mc[2]),
                get_float(mc[3]),
            )
        elif cname == "angle":
            angle = get_float(children(child)[0])
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        elif cname == "effects":
            attributes = load_effects(child)
        elif cname == "border":
            border_enabled = bool_token(child)
        elif cname == "stroke":
            stroke = load_stroke(child)
        elif cname == "knockout":
            knockout = bool_token(child)
        elif cname == "render_cache":
            render_cache = _load_render_cache(child)
        else:
            raise KicadParseError(f"Unknown token in fp_text_box: {cname!r}")

    return PcbTextBox(
        content=content,
        locked=locked,
        start=start,
        end=end,
        pts=pts,
        margins=margins,
        angle=angle,
        layer=layer,
        id=uuid,
        attributes=attributes,
        border_enabled=border_enabled,
        stroke=stroke,
        knockout=knockout,
        render_cache=render_cache,
    )


def _load_drawing(node: SExprNode) -> PcbDrawItem:
    """Dispatch on token name and return the appropriate drawing item."""
    name = sym_name(node)
    if name == "fp_text":
        return _load_pcb_text(node)
    elif name == "fp_line":
        return _load_pcb_line(node)
    elif name == "fp_rect":
        return _load_pcb_rect(node)
    elif name == "fp_circle":
        return _load_pcb_circle(node)
    elif name == "fp_arc":
        return _load_pcb_arc(node)
    elif name == "fp_poly":
        return _load_pcb_poly(node)
    elif name == "fp_curve":
        return _load_pcb_curve(node)
    elif name == "fp_text_box":
        return _load_pcb_text_box(node)
    raise KicadParseError(f"Unknown drawing item: {name!r}")


# ---------------------------------------------------------------------------
# Zone loader
# ---------------------------------------------------------------------------


def _load_pts_list_from(node: SExprNode) -> list[PtsElement]:
    """Parse a ``(pts …)`` block — alias of _load_pts_list (kept for callers)."""
    return _load_pts_list(node)


def _load_zone_keepout(node: SExprNode) -> ZoneKeepout:
    """Parse a ``(keepout ...)`` block inside a zone."""
    require_sym(node, "keepout")
    tracks = True
    vias = True
    pads = True
    copper_pour = True
    footprints = True
    zone_placement = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "tracks":
            tracks = get_str(children(child)[0]) == "allowed"
        elif cname == "vias":
            vias = get_str(children(child)[0]) == "allowed"
        elif cname == "pads":
            pads = get_str(children(child)[0]) == "allowed"
        elif cname == "copperpour":
            copper_pour = get_str(children(child)[0]) == "allowed"
        elif cname == "footprints":
            footprints = get_str(children(child)[0]) == "allowed"
        elif cname == "zone_placement":
            zone_placement = get_str(children(child)[0]) == "allowed"
        else:
            raise KicadParseError(f"Unknown token in zone keepout: {cname!r}")

    return ZoneKeepout(
        tracks=tracks,
        vias=vias,
        pads=pads,
        copper_pour=copper_pour,
        footprints=footprints,
        zone_placement=zone_placement,
    )


def _load_zone_placement(node: SExprNode) -> ZonePlacement:
    """Parse a ``(placement ...)`` block inside a zone."""
    require_sym(node, "placement")
    enabled = False
    source_type: Literal["sheetname", "component_class", "group"] = "sheetname"
    source = ""
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "enabled":
            enabled = bool_token(child)
        elif cname == "sheetname":
            source_type = "sheetname"
            source = get_str(children(child)[0])
        elif cname == "component_class":
            source_type = "component_class"
            source = get_str(children(child)[0])
        elif cname == "group":
            source_type = "group"
            source = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in zone placement: {cname!r}")
    return ZonePlacement(enabled=enabled, source_type=source_type, source=source)


def _load_zone_fill(node: SExprNode) -> ZoneFill:
    """Parse a ``(fill ...)`` block inside a zone."""
    require_sym(node, "fill")
    ch = children(node)

    filled = False
    mode = None
    thermal_gap = 0.0
    thermal_bridge_width = 0.0
    smoothing: Literal["chamfer", "fillet"] | None = None
    radius = None
    island_removal_mode = 0
    island_area_min = None
    hatch_thickness = None
    hatch_gap = None
    hatch_orientation = None
    hatch_smoothing_level = None
    hatch_smoothing_value = None
    hatch_border_algorithm: Literal["hatch_thickness", "min_thickness"] | None = None
    hatch_min_hole_area = None

    # Check for bare "yes" positional
    idx = 0
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "yes":
        filled = True
        idx = 1

    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "mode":
            mode = get_str(children(child)[0])
        elif cname == "thermal_gap":
            thermal_gap = get_float(children(child)[0])
        elif cname == "thermal_bridge_width":
            thermal_bridge_width = get_float(children(child)[0])
        elif cname == "smoothing":
            smoothing = _as_smoothing(get_str(children(child)[0]))
        elif cname == "radius":
            radius = get_float(children(child)[0])
        elif cname == "island_removal_mode":
            island_removal_mode = get_int(children(child)[0])
        elif cname == "island_area_min":
            island_area_min = get_float(children(child)[0])
        elif cname == "hatch_thickness":
            hatch_thickness = get_float(children(child)[0])
        elif cname == "hatch_gap":
            hatch_gap = get_float(children(child)[0])
        elif cname == "hatch_orientation":
            hatch_orientation = get_float(children(child)[0])
        elif cname == "hatch_smoothing_level":
            hatch_smoothing_level = get_int(children(child)[0])
        elif cname == "hatch_smoothing_value":
            hatch_smoothing_value = get_float(children(child)[0])
        elif cname == "hatch_border_algorithm":
            hatch_border_algorithm = _as_hatch_border_algorithm(get_str(children(child)[0]))
        elif cname == "hatch_min_hole_area":
            hatch_min_hole_area = get_float(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in zone fill: {cname!r}")

    return ZoneFill(
        filled=filled,
        mode=mode,
        thermal_gap=thermal_gap,
        thermal_bridge_width=thermal_bridge_width,
        smoothing=smoothing,
        radius=radius,
        island_removal_mode=island_removal_mode,
        island_area_min=island_area_min,
        hatch_thickness=hatch_thickness,
        hatch_gap=hatch_gap,
        hatch_orientation=hatch_orientation,
        hatch_smoothing_level=hatch_smoothing_level,
        hatch_smoothing_value=hatch_smoothing_value,
        hatch_border_algorithm=hatch_border_algorithm,
        hatch_min_hole_area=hatch_min_hole_area,
    )


def _load_zone(node: SExprNode) -> Zone:
    """Parse a ``(zone ...)`` token (§2.7)."""
    require_sym(node, "zone")
    net = None
    net_name = None
    locked = False
    layer: str | None = None
    layers: list[str] | None = None
    uuid = None
    name = None
    hatch_style: Literal["none", "edge", "full"] = "none"
    hatch_pitch = 0.508
    priority = None
    teardrop_type: Literal["padvia", "track_end"] | None = None
    connect_pads_mode: Literal["thru_hole_only", "yes", "no"] | None = None
    connect_pads_clearance = 0.0
    min_thickness = None
    filled_areas_thickness = None
    keepout = None
    placement = None
    fill = ZoneFill()
    layer_properties: list[ZoneLayerProperty] = []
    polygons: list[ZoneOutlinePolygon] = []
    filled_polygons: list[ZoneFilledPolygon] = []

    for child in children(node):
        if child.node_type == SExprNodeType.SYMBOL:
            if child.value == "locked":
                locked = True
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "net":
            # v10 zones reference their net by NAME (`(net "<name>")`, A2
            # Headline 1); legacy files use a numeric code, optionally followed
            # by the name (`(net <code> ["<name>"]`). Accept all three; a leading
            # integer is a legacy code preserved in ``net`` for round-trip.
            nc = children(child)
            first = nc[0]
            if first.node_type in (SExprNodeType.INTEGER, SExprNodeType.DOUBLE):
                net = get_int(first)
                if len(nc) > 1:
                    net_name = get_str(nc[1])
            else:
                net_name = get_str(first)
        elif cname == "net_name":
            net_name = get_str(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "layers":
            layers = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        elif cname == "name":
            name = get_str(children(child)[0])
        elif cname == "hatch":
            hc = children(child)
            hatch_style = _as_hatch_style(get_str(hc[0]))
            if len(hc) > 1:
                hatch_pitch = get_float(hc[1])
        elif cname == "priority":
            priority = get_int(children(child)[0])
        elif cname == "attr":
            # teardrop zone: (attr (teardrop (type padvia|track_end)))
            for ac in children(child):
                if ac.node_type == SExprNodeType.LIST and sym_name(ac) == "teardrop":
                    type_node = find_child(ac, "type")
                    if type_node is not None:
                        teardrop_type = _as_teardrop_type(get_str(children(type_node)[0]))
        elif cname == "connect_pads":
            # (connect_pads [mode] (clearance IU))
            cpch = children(child)
            for cp in cpch:
                if cp.node_type == SExprNodeType.SYMBOL:
                    connect_pads_mode = _as_connect_pads_mode(str(cp.value))
                elif cp.node_type == SExprNodeType.LIST:
                    cpn = sym_name(cp)
                    if cpn == "clearance":
                        connect_pads_clearance = get_float(children(cp)[0])
                    else:
                        raise KicadParseError(f"Unknown token in connect_pads: {cpn!r}")
        elif cname == "min_thickness":
            min_thickness = get_float(children(child)[0])
        elif cname == "filled_areas_thickness":
            filled_areas_thickness = bool_token(child)
        elif cname == "keepout":
            keepout = _load_zone_keepout(child)
        elif cname == "placement":
            placement = _load_zone_placement(child)
        elif cname == "fill":
            fill = _load_zone_fill(child)
        elif cname == "property":
            # per-layer property: (property (layer "...") (hatch_position (xy X Y)))
            prop_layer = ""
            hatch_position: tuple[float, float] | None = None
            for sub in children(child):
                if sub.node_type != SExprNodeType.LIST:
                    continue
                sname = sym_name(sub)
                if sname == "layer":
                    prop_layer = get_str(children(sub)[0])
                elif sname == "hatch_position":
                    xy = find_child(sub, "xy")
                    if xy is not None:
                        xyc = children(xy)
                        hatch_position = (get_float(xyc[0]), get_float(xyc[1]))
                else:
                    raise KicadParseError(f"Unknown token in zone property: {sname!r}")
            layer_properties.append(
                ZoneLayerProperty(layer=prop_layer, hatch_position=hatch_position)
            )
        elif cname == "polygon":
            # outline polygon
            pts_node = find_child(child, "pts")
            pts = _load_pts_list_from(pts_node) if pts_node is not None else []
            if pts:  # zone with 0 corners dropped per §8.3 #12
                polygons.append(ZoneOutlinePolygon(pts=pts))
        elif cname == "filled_polygon":
            # filled polygon
            fp_layer = ""
            fp_island = False
            fp_pts: list[PtsElement] = []
            for fpc in children(child):
                if fpc.node_type != SExprNodeType.LIST:
                    continue
                fpn = sym_name(fpc)
                if fpn == "layer":
                    fp_layer = get_str(children(fpc)[0])
                elif fpn == "island":
                    fp_island = True
                elif fpn == "pts":
                    fp_pts = _load_pts_list_from(fpc)
                else:
                    raise KicadParseError(f"Unknown token in filled_polygon: {fpn!r}")
            filled_polygons.append(ZoneFilledPolygon(layer=fp_layer, island=fp_island, pts=fp_pts))
        else:
            raise KicadParseError(f"Unknown token in zone: {cname!r}")

    return Zone(
        net=net,
        net_name=net_name,
        locked=locked,
        layer=layer,
        layers=layers,
        id=uuid,
        name=name,
        hatch_style=hatch_style,
        hatch_pitch=hatch_pitch,
        priority=priority,
        teardrop_type=teardrop_type,
        connect_pads_mode=connect_pads_mode,
        connect_pads_clearance=connect_pads_clearance,
        min_thickness=min_thickness,
        filled_areas_thickness=filled_areas_thickness,
        keepout=keepout,
        placement=placement,
        fill=fill,
        layer_properties=layer_properties,
        polygons=polygons,
        filled_polygons=filled_polygons,
    )


# ---------------------------------------------------------------------------
# ReferenceImage loader
# ---------------------------------------------------------------------------


def _load_image(node: SExprNode) -> ReferenceImage:
    """Parse an ``(image ...)`` token (§2.6)."""
    require_sym(node, "image")
    position = (0.0, 0.0)
    layer = ""
    scale = None
    locked = False
    data = ""
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "scale":
            scale = get_float(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "data":
            # Accept KiCad's quoted 76-char base64 chunks (concatenated) as well
            # as the legacy single pipe-delimited blob (parser strips the pipes
            # to one STRING). Both round-trip to the same base64 payload.
            data = "".join(
                get_str(c) for c in children(child) if c.node_type == SExprNodeType.STRING
            )
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in image: {cname!r}")

    return ReferenceImage(
        position=position, layer=layer, scale=scale, locked=locked, data=data, id=uuid
    )


# ---------------------------------------------------------------------------
# Barcode loader
# ---------------------------------------------------------------------------


def _load_barcode(node: SExprNode) -> Barcode:
    """Parse a ``(barcode ...)`` token (§2.9)."""
    require_sym(node, "barcode")
    locked = False
    position = (0.0, 0.0)
    angle = 0.0
    layer = ""
    size = (0.0, 0.0)
    text = ""
    text_height = 0.0
    barcode_type = BarcodeKind.qr
    ecc_level = None
    show_text = True
    knockout = False
    margins = None
    uuid = None

    for child in children(node):
        if child.node_type == SExprNodeType.SYMBOL:
            if child.value == "locked":
                locked = True
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "locked":
            locked = bool_token(child)
        elif cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
            if len(atc) > 2:
                angle = get_float(atc[2])
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "size":
            sc = children(child)
            size = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "text":
            text = get_str(children(child)[0])
        elif cname == "text_height":
            text_height = get_float(children(child)[0])
        elif cname == "type":
            type_str = get_str(children(child)[0])
            try:
                barcode_type = BarcodeKind(type_str)
            except ValueError as err:
                raise KicadParseError(f"Unknown barcode type: {type_str!r}") from err
        elif cname == "ecc_level":
            ecc_str = get_str(children(child)[0])
            try:
                ecc_level = BarcodeEcc(ecc_str)
            except ValueError as err:
                raise KicadParseError(f"Unknown barcode ecc level: {ecc_str!r}") from err
        elif cname == "hide":
            show_text = not bool_token(child)
        elif cname == "knockout":
            knockout = bool_token(child)
        elif cname == "margins":
            mc = children(child)
            margins = (get_float(mc[0]), get_float(mc[1]))
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in barcode: {cname!r}")

    return Barcode(
        locked=locked,
        position=position,
        angle=angle,
        layer=layer,
        size=size,
        text=text,
        text_height=text_height,
        barcode_type=barcode_type,
        ecc_level=ecc_level,
        show_text=show_text,
        knockout=knockout,
        margins=margins,
        id=uuid,
    )


# ---------------------------------------------------------------------------
# Dimension loader
# ---------------------------------------------------------------------------


def _load_dimension_text(node: SExprNode) -> DimensionText:
    """Parse a ``(gr_text ...)`` inside a dimension."""
    require_sym(node, "gr_text")
    ch = children(node)
    content = get_str(ch[0]) if ch else ""
    locked = False
    position = (0.0, 0.0)
    angle = 0.0
    layer = ""
    knockout = False
    unlocked = False
    visible = True
    uuid = None
    attributes = None
    render_cache = None

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "locked":
            locked = bool_token(child)
        elif cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
            if len(atc) > 2:
                angle = get_float(atc[2])
        elif cname == "unlocked":
            unlocked = bool_token(child)
        elif cname == "layer":
            ch2 = children(child)
            layer = get_str(ch2[0])
            for x in ch2[1:]:
                if x.node_type == SExprNodeType.SYMBOL and x.value == "knockout":
                    knockout = True
        elif cname == "hide":
            visible = not bool_token(child)
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        elif cname == "effects":
            attributes = load_effects(child)
        elif cname == "render_cache":
            render_cache = _load_render_cache(child)
        else:
            raise KicadParseError(f"Unknown token in dimension gr_text: {cname!r}")

    return DimensionText(
        content=content,
        locked=locked,
        position=position,
        angle=angle,
        layer=layer,
        knockout=knockout,
        unlocked=unlocked,
        visible=visible,
        id=uuid,
        attributes=attributes,
        render_cache=render_cache,
    )


def _load_dimension(node: SExprNode) -> Dimension:
    """Parse a ``(dimension ...)`` token (§2.8)."""
    require_sym(node, "dimension")
    dim_type = DimensionType.aligned
    locked = False
    layer = ""
    uuid = None
    pts: list[PtsElement] = []
    height = None
    leader_length = None
    orientation = None
    dim_format = None
    style = DimensionStyle()
    text = None

    for child in children(node):
        if child.node_type == SExprNodeType.SYMBOL:
            if child.value == "locked":
                locked = True
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "type":
            type_str = get_str(children(child)[0])
            try:
                dim_type = DimensionType(type_str)
            except ValueError as err:
                raise KicadParseError(f"Unknown dimension type: {type_str!r}") from err
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        elif cname == "pts":
            for xy_node in children(child):
                if xy_node.node_type == SExprNodeType.LIST and sym_name(xy_node) == "xy":
                    xyc = children(xy_node)
                    pts.append((get_float(xyc[0]), get_float(xyc[1])))
        elif cname == "height":
            height = get_float(children(child)[0])
        elif cname == "leader_length":
            leader_length = get_float(children(child)[0])
        elif cname == "orientation":
            orientation = get_int(children(child)[0])
        elif cname == "format":
            prefix = ""
            suffix = ""
            units = 3
            units_format = 1
            precision = 4
            override_value = None
            suppress_zeroes = False
            for fc in children(child):
                if fc.node_type != SExprNodeType.LIST:
                    continue
                fn = sym_name(fc)
                if fn == "prefix":
                    prefix = get_str(children(fc)[0])
                elif fn == "suffix":
                    suffix = get_str(children(fc)[0])
                elif fn == "units":
                    units = get_int(children(fc)[0])
                elif fn == "units_format":
                    units_format = get_int(children(fc)[0])
                elif fn == "precision":
                    precision = get_int(children(fc)[0])
                elif fn == "override_value":
                    override_value = get_str(children(fc)[0])
                elif fn == "suppress_zeroes":
                    suppress_zeroes = bool_token(fc)
                else:
                    raise KicadParseError(f"Unknown token in dimension format: {fn!r}")
            dim_format = DimensionFormat(
                prefix=prefix,
                suffix=suffix,
                units=units,
                units_format=units_format,
                precision=precision,
                override_value=override_value,
                suppress_zeroes=suppress_zeroes,
            )
        elif cname == "style":
            thickness = 0.0
            arrow_length = 0.0
            text_position_mode = 0
            arrow_direction = None
            extension_height = None
            text_frame = None
            extension_offset = 0.0
            keep_text_aligned = False
            for sc in children(child):
                if sc.node_type != SExprNodeType.LIST:
                    continue
                sn = sym_name(sc)
                if sn == "thickness":
                    thickness = get_float(children(sc)[0])
                elif sn == "arrow_length":
                    arrow_length = get_float(children(sc)[0])
                elif sn == "text_position_mode":
                    text_position_mode = get_int(children(sc)[0])
                elif sn == "arrow_direction":
                    ad_str = get_str(children(sc)[0])
                    try:
                        arrow_direction = ArrowDirection(ad_str)
                    except ValueError as err:
                        raise KicadParseError(f"Unknown arrow direction: {ad_str!r}") from err
                elif sn == "extension_height":
                    extension_height = get_float(children(sc)[0])
                elif sn == "text_frame":
                    text_frame = get_int(children(sc)[0])
                elif sn == "extension_offset":
                    extension_offset = get_float(children(sc)[0])
                elif sn == "keep_text_aligned":
                    keep_text_aligned = bool_token(sc)
                else:
                    raise KicadParseError(f"Unknown token in dimension style: {sn!r}")
            style = DimensionStyle(
                thickness=thickness,
                arrow_length=arrow_length,
                text_position_mode=text_position_mode,
                arrow_direction=arrow_direction,
                extension_height=extension_height,
                text_frame=text_frame,
                extension_offset=extension_offset,
                keep_text_aligned=keep_text_aligned,
            )
        elif cname == "gr_text":
            text = _load_dimension_text(child)
        # Legacy tokens — read-only, never written by v10
        elif cname in (
            "feature1",
            "feature2",
            "crossbar",
            "arrow1a",
            "arrow1b",
            "arrow2a",
            "arrow2b",
            "width",
        ):
            pass  # legacy dimension tokens: accept and discard
        else:
            raise KicadParseError(f"Unknown token in dimension: {cname!r}")

    return Dimension(
        dim_type=dim_type,
        locked=locked,
        layer=layer,
        id=uuid,
        pts=pts,
        height=height,
        leader_length=leader_length,
        orientation=orientation,
        dim_format=dim_format,
        style=style,
        text=text,
    )


# ---------------------------------------------------------------------------
# Table loader
# ---------------------------------------------------------------------------


def _load_table_cell(node: SExprNode) -> TableCell:
    """Parse a ``(table_cell ...)`` token (§2.10)."""
    require_sym(node, "table_cell")
    ch = children(node)
    content = get_str(ch[0]) if ch else ""
    start = (0.0, 0.0)
    end = (0.0, 0.0)
    angle = 0.0
    margins = (0.0, 0.0, 0.0, 0.0)
    span = None
    locked = False
    layer = ""
    uuid = None
    attributes = None

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "start":
            sc = children(child)
            start = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "end":
            ec = children(child)
            end = (get_float(ec[0]), get_float(ec[1]))
        elif cname == "angle":
            angle = get_float(children(child)[0])
        elif cname == "margins":
            mc = children(child)
            margins = (get_float(mc[0]), get_float(mc[1]), get_float(mc[2]), get_float(mc[3]))
        elif cname == "span":
            sc = children(child)
            span = (get_int(sc[0]), get_int(sc[1]))
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        elif cname == "effects":
            attributes = load_effects(child)
        else:
            raise KicadParseError(f"Unknown token in table_cell: {cname!r}")

    return TableCell(
        content=content,
        start=start,
        end=end,
        angle=angle,
        margins=margins,
        span=span,
        locked=locked,
        layer=layer,
        id=uuid,
        attributes=attributes,
    )


def _load_table(node: SExprNode) -> Table:
    """Parse a ``(table ...)`` token (§2.10)."""
    require_sym(node, "table")
    column_count = 0
    uuid = None
    locked = False
    layer = ""
    border = TableBorder()
    separators = TableSeparators()
    column_widths: list[float] = []
    row_heights: list[float] = []
    cells: list[TableCell] = []

    for child in children(node):
        if child.node_type == SExprNodeType.SYMBOL:
            if child.value == "locked":
                locked = True
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "column_count":
            column_count = get_int(children(child)[0])
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "border":
            ext = True
            hdr = False
            bstroke = None
            for bc in children(child):
                if bc.node_type != SExprNodeType.LIST:
                    continue
                bn = sym_name(bc)
                if bn == "external":
                    ext = bool_token(bc)
                elif bn == "header":
                    hdr = bool_token(bc)
                elif bn == "stroke":
                    bstroke = load_stroke(bc)
                else:
                    raise KicadParseError(f"Unknown token in table border: {bn!r}")
            border = TableBorder(external=ext, header=hdr, stroke=bstroke)
        elif cname == "separators":
            rows = True
            cols = True
            sstroke = None
            for sc in children(child):
                if sc.node_type != SExprNodeType.LIST:
                    continue
                sn = sym_name(sc)
                if sn == "rows":
                    rows = bool_token(sc)
                elif sn == "cols":
                    cols = bool_token(sc)
                elif sn == "stroke":
                    sstroke = load_stroke(sc)
                else:
                    raise KicadParseError(f"Unknown token in table separators: {sn!r}")
            separators = TableSeparators(rows=rows, cols=cols, stroke=sstroke)
        elif cname == "column_widths":
            column_widths = [
                get_float(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.DOUBLE, SExprNodeType.INTEGER)
            ]
        elif cname == "row_heights":
            row_heights = [
                get_float(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.DOUBLE, SExprNodeType.INTEGER)
            ]
        elif cname == "cells":
            for cc in children(child):
                if cc.node_type == SExprNodeType.LIST and sym_name(cc) == "table_cell":
                    cells.append(_load_table_cell(cc))
        else:
            raise KicadParseError(f"Unknown token in table: {cname!r}")

    return Table(
        column_count=column_count,
        id=uuid,
        locked=locked,
        layer=layer,
        border=border,
        separators=separators,
        column_widths=column_widths,
        row_heights=row_heights,
        cells=cells,
    )


# ---------------------------------------------------------------------------
# PcbPoint loader
# ---------------------------------------------------------------------------


def _load_pcb_point(node: SExprNode) -> PcbPoint:
    """Parse a ``(point ...)`` token (§2.5)."""
    require_sym(node, "point")
    position = (0.0, 0.0)
    size = 0.0
    layer = ""
    uuid = None

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
        elif cname == "size":
            size = get_float(children(child)[0])
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in point: {cname!r}")

    return PcbPoint(position=position, size=size, layer=layer, id=uuid)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


def _load_group(node: SExprNode) -> PcbGroup:
    """Parse a ``group`` token."""
    require_sym(node, "group")
    ch = children(node)

    if not ch:
        raise KicadParseError("group missing name")

    name = get_str(ch[0])
    uuid = None
    locked = False
    lib_id = None
    members: list[str] = []

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "uuid":
            uuid = get_str(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "lib_id":
            lib_id = get_str(children(child)[0])
        elif cname == "members":
            members = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]
        else:
            raise KicadParseError(f"Unknown token in group: {cname!r}")

    return PcbGroup(name=name, id=uuid, locked=locked, lib_id=lib_id, members=members)
