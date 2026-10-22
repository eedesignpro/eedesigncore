"""Footprint loader: S-expression tree → ``Footprint`` domain model."""

from __future__ import annotations

import contextlib
from enum import Enum
from typing import TypeVar, cast

from eedesigncore.common.arcs import normalize_arc_mm
from eedesigncore.common.loaders import (
    bool_token,
    children,
    elements,
    find_child,
    front_back_opt_bool,
    get_float,
    get_int,
    get_str,
    load_effects,
    load_embedded_file,
    require_sym,
    sym_name,
)
from eedesigncore.common.models import (
    EmbeddedFile,
    Property,
    PtsElement,
)
from eedesigncore.common.pcb_family import (
    BackdrillInfo,
    FootprintGateUnit,
    FootprintUnitInfo,
    JumperPadGroups,
    Padstack,
    PadstackLayer,
    PadstackMode,
    PcbDrawItem,
    PcbGroup,
    PcbPoint,
    PostMachining,
    PostMachiningMode,
    Stackup,
    StackupLayer,
    Variant,
    VariantField,
    Zone,
)
from eedesigncore.common.pcb_family.loaders import (
    _load_barcode,
    _load_dimension,
    _load_drawing,
    _load_group,
    _load_image,
    _load_pcb_point,
    _load_pts_list,
    _load_render_cache,
    _load_table,
    _load_zone,
)
from eedesigncore.common.pcb_family.ordering import PCB_IU_PER_MM
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.enums import (
    ChamferPosition,
    CustomAnchor,
    CustomShapeClearance,
    PadProperty,
    PadShape,
    PadType,
)
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintAttrs,
    FootprintPlacement,
    Model3D,
    Pad,
    PadPrimitive,
    PrimArc,
    PrimBbox,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimPoly,
    PrimRect,
    PrimVector,
    TeardropParams,
)
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# ---------------------------------------------------------------------------
# Property / Field
# ---------------------------------------------------------------------------


_EnumT = TypeVar("_EnumT", bound=Enum)

# ``Mils2IU( pcbIUScale, 30 )``, the PAD constructor's drill size (``pad.cpp:86``).
# It applies to an np_thru_hole pad that carries no (drill …) token.
_NPTH_DEFAULT_DRILL_MM = 30 * 0.0254


def _token_enum(enum_cls: type[_EnumT], token: str, what: str) -> _EnumT:
    """Convert a file token to *enum_cls*, or raise a clear parse error (GAP-021)."""
    try:
        return enum_cls(token)
    except ValueError as err:
        valid = ", ".join(str(m.value) for m in enum_cls)
        raise KicadParseError(f"Unknown {what}: {token!r}. Valid values: {valid}.") from err


def _load_property_with_uuid(node: SExprNode) -> Property:
    """Parse a ``property`` token in footprint context.

    Returns a ``Property`` with ``layer``, ``unlocked``, and ``id`` populated.
    """
    require_sym(node, "property")
    ch_all = children(node)

    if len(ch_all) < 2:
        raise KicadParseError("property missing name/value")

    name = get_str(ch_all[0])
    value = get_str(ch_all[1])

    position = (0.0, 0.0)
    angle = 0.0
    unlocked = False
    layer: str | None = None
    knockout = False
    visible = True
    uuid: str | None = None
    attributes = None
    render_cache = None

    for child in ch_all[2:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "at":
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
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        elif cname == "effects":
            attributes = load_effects(child)
        elif cname == "render_cache":
            render_cache = _load_render_cache(child)
        else:
            raise KicadParseError(f"Unknown token in property: {cname!r}")

    return Property(
        name=name,
        value=value,
        position=position,
        angle=angle,
        visible=visible,
        attributes=attributes,
        layer=layer,
        knockout=knockout,
        unlocked=unlocked,
        id=uuid,
        show_name=False,
        do_not_autoplace=False,
        render_cache=render_cache,
    )


# ---------------------------------------------------------------------------
# Pad primitives
# ---------------------------------------------------------------------------


def _load_pad_primitive(node: SExprNode) -> PadPrimitive:
    """Parse a single pad primitive (gr_line, gr_rect, gr_arc, etc.)."""
    name = sym_name(node)
    ch = children(node)

    if name == "gr_line":
        start = (0.0, 0.0)
        end = (0.0, 0.0)
        width = 0.0
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "start":
                sc = children(c)
                start = (get_float(sc[0]), get_float(sc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            elif cn == "width":
                width = get_float(children(c)[0])
            else:
                raise KicadParseError(f"Unknown token in gr_line: {cn!r}")
        return PrimLine(start=start, end=end, width=width)

    elif name == "gr_rect":
        start = (0.0, 0.0)
        end = (0.0, 0.0)
        corner_radius = None
        width = 0.0
        fill = False
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "start":
                sc = children(c)
                start = (get_float(sc[0]), get_float(sc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            elif cn == "radius":
                corner_radius = get_float(children(c)[0])
            elif cn == "width":
                width = get_float(children(c)[0])
            elif cn == "fill":
                fc = children(c)
                if fc and fc[0].node_type == SExprNodeType.SYMBOL:
                    fill = fc[0].value == "yes"
            else:
                raise KicadParseError(f"Unknown token in gr_rect: {cn!r}")
        return PrimRect(start=start, end=end, corner_radius=corner_radius, width=width, fill=fill)

    elif name == "gr_arc":
        start = (0.0, 0.0)
        mid = (0.0, 0.0)
        end = (0.0, 0.0)
        width = 0.0
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "start":
                sc = children(c)
                start = (get_float(sc[0]), get_float(sc[1]))
            elif cn == "mid":
                mc = children(c)
                mid = (get_float(mc[0]), get_float(mc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            elif cn == "width":
                width = get_float(children(c)[0])
            else:
                raise KicadParseError(f"Unknown token in gr_arc: {cn!r}")
        # A pad primitive is parsed by parsePCB_SHAPE too
        # (pcb_io_kicad_sexpr_parser.cpp:6297), so its winding normalises (ADR-067).
        start, mid, end = normalize_arc_mm(start, mid, end, iu_per_mm=PCB_IU_PER_MM)
        return PrimArc(start=start, mid=mid, end=end, width=width)

    elif name == "gr_circle":
        center = (0.0, 0.0)
        end = (0.0, 0.0)
        width = 0.0
        fill = False
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "center":
                cc = children(c)
                center = (get_float(cc[0]), get_float(cc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            elif cn == "width":
                width = get_float(children(c)[0])
            elif cn == "fill":
                fc = children(c)
                if fc and fc[0].node_type == SExprNodeType.SYMBOL:
                    fill = fc[0].value == "yes"
            else:
                raise KicadParseError(f"Unknown token in gr_circle: {cn!r}")
        return PrimCircle(center=center, end=end, width=width, fill=fill)

    elif name == "gr_curve":
        pts: list[PtsElement] = []
        width = 0.0
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "pts":
                pts = _load_pts_list(c)
            elif cn == "width":
                width = get_float(children(c)[0])
            else:
                raise KicadParseError(f"Unknown token in gr_curve: {cn!r}")
        return PrimCurve(pts=pts, width=width)

    elif name == "gr_poly":
        pts = []
        width = 0.0
        fill = False
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "pts":
                pts = _load_pts_list(c)
            elif cn == "width":
                width = get_float(children(c)[0])
            elif cn == "fill":
                fc = children(c)
                if fc and fc[0].node_type == SExprNodeType.SYMBOL:
                    fill = fc[0].value == "yes"
            else:
                raise KicadParseError(f"Unknown token in gr_poly: {cn!r}")
        return PrimPoly(pts=pts, width=width, fill=fill)

    elif name == "gr_vector":
        start = (0.0, 0.0)
        end = (0.0, 0.0)
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "start":
                sc = children(c)
                start = (get_float(sc[0]), get_float(sc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            else:
                raise KicadParseError(f"Unknown token in gr_vector: {cn!r}")
        return PrimVector(start=start, end=end)

    elif name == "gr_bbox":
        start = (0.0, 0.0)
        end = (0.0, 0.0)
        fill = False
        for c in ch:
            if c.node_type != SExprNodeType.LIST:
                continue
            cn = sym_name(c)
            if cn == "start":
                sc = children(c)
                start = (get_float(sc[0]), get_float(sc[1]))
            elif cn == "end":
                ec = children(c)
                end = (get_float(ec[0]), get_float(ec[1]))
            elif cn == "fill":
                fc = children(c)
                if fc and fc[0].node_type == SExprNodeType.SYMBOL:
                    fill = fc[0].value == "yes"
            else:
                raise KicadParseError(f"Unknown token in gr_bbox: {cn!r}")
        return PrimBbox(start=start, end=end, fill=fill)

    raise KicadParseError(f"Unknown pad primitive: {name!r}")


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


def _load_pad(node: SExprNode) -> Pad:
    """Parse a ``pad`` token."""
    require_sym(node, "pad")
    ch = children(node)

    if len(ch) < 3:
        raise KicadParseError("pad missing number, type, or shape")

    number = get_str(ch[0])

    type_str = str(ch[1].value)
    try:
        pad_type = PadType(type_str)
    except ValueError as err:
        raise KicadParseError(f"Unknown pad type: {type_str!r}") from err

    shape_str = str(ch[2].value)
    try:
        pad_shape = PadShape(shape_str)
    except ValueError as err:
        raise KicadParseError(f"Unknown pad shape: {shape_str!r}") from err

    position = (0.0, 0.0)
    angle = 0.0
    size = (1.0, 1.0)
    rect_delta = None
    drill: DrillInfo | None = None
    backdrill = None
    tertiary_drill = None
    front_post_machining = None
    back_post_machining = None
    pad_property = None
    pad_layers: list[str] = []
    remove_unused_layers: bool | None = None
    keep_end_layers: bool | None = None
    zone_layer_connections = None
    roundrect_rratio = None
    chamfer_ratio = None
    chamfer_positions = None
    net_code = None
    net_name = None
    pinfunction = None
    pintype = None
    die_length = None
    die_delay = None
    solder_mask_margin = None
    solder_paste_margin = None
    solder_paste_margin_ratio = None
    clearance = None
    zone_connect = None
    thermal_spoke_width = None
    thermal_spoke_angle = None
    thermal_gap = None
    custom_shape_clearance = None
    custom_anchor = None
    primitives = None
    teardrop_params = None
    tenting_front = None
    tenting_back = None
    uuid = None
    padstack = None

    for child in ch[3:]:
        if child.node_type == SExprNodeType.SYMBOL:
            # bare "locked" keyword on pad — accepted and discarded (§7.2)
            continue
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)

        if cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
            if len(atc) > 2:
                angle = get_float(atc[2])

        elif cname == "size":
            sc = children(child)
            size = (get_float(sc[0]), get_float(sc[1]))

        elif cname == "rect_delta":
            dc = children(child)
            rect_delta = (get_float(dc[0]), get_float(dc[1]))

        elif cname == "drill":
            drill = _load_drill(child)

        elif cname == "backdrill":
            backdrill = _load_backdrill(child)

        elif cname == "tertiary_drill":
            tertiary_drill = _load_backdrill(child)

        elif cname == "front_post_machining":
            front_post_machining = _load_post_machining(child)

        elif cname == "back_post_machining":
            back_post_machining = _load_post_machining(child)

        elif cname == "property":
            # Pad property token: (property pad_prop_bga) etc.
            prop_ch = children(child)
            if prop_ch:
                prop_str = str(prop_ch[0].value)
                with contextlib.suppress(ValueError):
                    pad_property = PadProperty(prop_str)

        elif cname == "layers":
            pad_layers = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]

        elif cname == "remove_unused_layers":
            remove_unused_layers = bool_token(child)

        elif cname == "keep_end_layers":
            keep_end_layers = bool_token(child)

        elif cname == "zone_layer_connections":
            zone_layer_connections = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]

        elif cname == "roundrect_rratio":
            roundrect_rratio = get_float(children(child)[0])

        elif cname == "chamfer_ratio":
            chamfer_ratio = get_float(children(child)[0])

        elif cname == "chamfer":
            positions = []
            for x in children(child):
                if x.node_type == SExprNodeType.SYMBOL:
                    positions.append(
                        _token_enum(ChamferPosition, str(x.value), "pad chamfer position")
                    )
            chamfer_positions = positions

        elif cname == "net":
            # (net code? "name") — code optional
            nc = children(child)
            if len(nc) >= 2:
                net_code = get_int(nc[0])
                net_name = get_str(nc[1])
            elif len(nc) == 1:
                net_name = get_str(nc[0])

        elif cname == "pinfunction":
            pinfunction = get_str(children(child)[0])

        elif cname == "pintype":
            pintype = get_str(children(child)[0])

        elif cname == "die_length":
            die_length = get_float(children(child)[0])

        elif cname == "die_delay":
            die_delay = get_float(children(child)[0])

        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])

        elif cname == "solder_paste_margin":
            solder_paste_margin = get_float(children(child)[0])

        elif cname == "solder_paste_margin_ratio":
            solder_paste_margin_ratio = get_float(children(child)[0])

        elif cname == "clearance":
            clearance = get_float(children(child)[0])

        elif cname == "zone_connect":
            zone_connect = get_int(children(child)[0])

        elif cname == "thermal_bridge_width":
            thermal_spoke_width = get_float(children(child)[0])

        elif cname == "thermal_width":
            # Legacy alias of thermal_bridge_width (parser 6170)
            thermal_spoke_width = get_float(children(child)[0])

        elif cname == "thermal_bridge_angle":
            thermal_spoke_angle = get_float(children(child)[0])

        elif cname == "thermal_gap":
            thermal_gap = get_float(children(child)[0])

        elif cname == "options":
            for oc in children(child):
                if oc.node_type != SExprNodeType.LIST:
                    continue
                oname = sym_name(oc)
                if oname == "clearance":
                    custom_shape_clearance = _token_enum(
                        CustomShapeClearance,
                        get_str(children(oc)[0]),
                        "pad custom-shape clearance",
                    )
                elif oname == "anchor":
                    custom_anchor = _token_enum(
                        CustomAnchor, get_str(children(oc)[0]), "pad custom anchor"
                    )
                else:
                    raise KicadParseError(f"Unknown token in pad options: {oname!r}")

        elif cname == "primitives":
            prims = []
            for pc in children(child):
                if pc.node_type != SExprNodeType.LIST:
                    continue
                prims.append(_load_pad_primitive(pc))
            primitives = prims

        elif cname == "teardrops":
            teardrop_params = _load_teardrop(child)

        elif cname == "tenting":
            # A pad keeps the OPTIONAL, unlike board setup: KiCad stores it in
            # has_solder_mask without collapsing it (parser.cpp:6340-6346). The
            # value is tri-state and the whole block also has a legacy bare-word
            # spelling, so both readers live in front_back_opt_bool.
            tenting_front, tenting_back = front_back_opt_bool(child, allow_legacy=True)

        elif cname == "uuid" or cname == "tstamp":
            uuid = get_str(children(child)[0])

        elif cname == "locked":
            pass  # pad lock is accepted and discarded (§7.2)

        elif cname == "padstack":
            padstack = _load_padstack(child)

        else:
            raise KicadParseError(f"Unknown token in pad: {cname!r}")

    if drill is None:
        # A hole pad that carries NO (drill …) token still has a drill in KiCad,
        # and the two hole types take different values. The pad-type switch sets
        # them before any token is read (``parser.cpp:5764-5791``):
        #
        #   thru_hole     SetDrillSize( VECTOR2I( 1, 1 ) ), so 1 nm. KiCad's own
        #                 comment says the token "is usually missing if 0 drill
        #                 size is specified" and that it emulates it "to avoid
        #                 errors", because a newer version cannot hold a 0 drill.
        #   np_thru_hole  no assignment, so the PAD constructor default of
        #                 30 mils stands (``pad.cpp:86``).
        #
        # Measured through `kicad-cli fp upgrade` on a drill-less pad of each
        # kind. KiCad writes (drill 0.000001) and (drill 0.762) respectively.
        if pad_type is PadType.thru_hole:
            drill = DrillInfo(width=1.0 / PCB_IU_PER_MM)
        elif pad_type is PadType.np_thru_hole:
            drill = DrillInfo(width=_NPTH_DEFAULT_DRILL_MM)

    return Pad(
        number=number,
        pad_type=pad_type,
        shape=pad_shape,
        position=position,
        angle=angle,
        size=size,
        rect_delta=rect_delta,
        drill=drill,
        backdrill=backdrill,
        tertiary_drill=tertiary_drill,
        front_post_machining=front_post_machining,
        back_post_machining=back_post_machining,
        pad_property=pad_property,
        layers=pad_layers,
        remove_unused_layers=remove_unused_layers,
        keep_end_layers=keep_end_layers,
        zone_layer_connections=zone_layer_connections,
        roundrect_rratio=roundrect_rratio,
        chamfer_ratio=chamfer_ratio,
        chamfer_positions=chamfer_positions,
        net_code=net_code,
        net_name=net_name,
        pinfunction=pinfunction,
        pintype=pintype,
        die_length=die_length,
        die_delay=die_delay,
        solder_mask_margin=solder_mask_margin,
        solder_paste_margin=solder_paste_margin,
        solder_paste_margin_ratio=solder_paste_margin_ratio,
        clearance=clearance,
        zone_connect=zone_connect,
        thermal_spoke_width=thermal_spoke_width,
        thermal_spoke_angle=thermal_spoke_angle,
        thermal_gap=thermal_gap,
        custom_shape_clearance=custom_shape_clearance,
        custom_anchor=custom_anchor,
        primitives=cast(list[PadPrimitive] | None, primitives),
        teardrop_params=teardrop_params,
        tenting_front=tenting_front,
        tenting_back=tenting_back,
        id=uuid,
        padstack=padstack,
    )


def _load_backdrill(node: SExprNode) -> BackdrillInfo:
    """Parse a ``(backdrill ...)`` or ``(tertiary_drill ...)`` token (§3.2)."""
    ch = children(node)
    size = 0.0
    start_layer = ""
    end_layer = ""
    for child in ch:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "size":
            size = get_float(children(child)[0])
        elif cname == "layers":
            lc = children(child)
            if len(lc) >= 1:
                start_layer = get_str(lc[0])
            if len(lc) >= 2:
                end_layer = get_str(lc[1])
        else:
            raise KicadParseError(f"Unknown token in backdrill: {cname!r}")
    return BackdrillInfo(size=size, start_layer=start_layer, end_layer=end_layer)


def _load_post_machining(node: SExprNode) -> PostMachining:
    """Parse a ``(front_post_machining ...)`` or ``(back_post_machining ...)`` (§2.4.2)."""
    ch = children(node)
    # First positional: mode symbol (counterbore or countersink)
    if not ch:
        raise KicadParseError("post_machining missing mode")
    mode_str = str(ch[0].value)
    try:
        mode = PostMachiningMode(mode_str)
    except ValueError as err:
        raise KicadParseError(f"Unknown post machining mode: {mode_str!r}") from err
    size = None
    depth = None
    angle = None
    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "size":
            size = get_float(children(child)[0])
        elif cname == "depth":
            depth = get_float(children(child)[0])
        elif cname == "angle":
            # store the disk token /10; the writer multiplies by 10 to restore
            # the exact bytes (÷10 load / ×10 write inverse pair, not degrees).
            angle = get_float(children(child)[0]) / 10.0
        else:
            raise KicadParseError(f"Unknown token in post_machining: {cname!r}")
    return PostMachining(mode=mode, size=size, depth=depth, angle=angle)


def _load_padstack_layer(node: SExprNode) -> PadstackLayer:
    """Parse a per-layer block inside ``(padstack ...)``.

    The KiCad parser uses a non-strict loop (continue on unknown) for padstack
    layers (parser 6905), so unknown tokens are silently skipped at the KiCad
    level. We remain strict per ADR-017.
    """
    require_sym(node, "layer")
    ch = children(node)
    if not ch:
        raise KicadParseError("padstack layer missing name")
    layer_name = get_str(ch[0])
    shape = None
    size = None
    offset = None
    rect_delta = None
    roundrect_rratio = None
    chamfer_ratio = None
    chamfer_positions: list[ChamferPosition] | None = None
    primitives = None
    custom_anchor = None
    thermal_bridge_angle = None
    thermal_gap = None
    thermal_bridge_width = None
    clearance = None
    zone_connect = None
    tenting_front = None
    tenting_back = None

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "shape":
            shape = _token_enum(PadShape, get_str(children(child)[0]), "padstack layer shape")
        elif cname == "size":
            sc = children(child)
            size = (get_float(sc[0]), get_float(sc[1]))
        elif cname == "offset":
            oc = children(child)
            offset = (get_float(oc[0]), get_float(oc[1]))
        elif cname == "rect_delta":
            dc = children(child)
            rect_delta = (get_float(dc[0]), get_float(dc[1]))
        elif cname == "roundrect_rratio":
            roundrect_rratio = get_float(children(child)[0])
        elif cname == "chamfer_ratio":
            chamfer_ratio = get_float(children(child)[0])
        elif cname == "chamfer":
            positions = []
            for x in children(child):
                if x.node_type == SExprNodeType.SYMBOL:
                    positions.append(
                        _token_enum(ChamferPosition, str(x.value), "padstack chamfer position")
                    )
            chamfer_positions = positions
        elif cname == "options":
            for oc2 in children(child):
                if oc2.node_type != SExprNodeType.LIST:
                    continue
                oname = sym_name(oc2)
                if oname == "anchor":
                    custom_anchor = _token_enum(
                        CustomAnchor, get_str(children(oc2)[0]), "padstack custom anchor"
                    )
                elif oname == "clearance":
                    pass  # parsed & discarded per KiCad source 6841
                else:
                    raise KicadParseError(f"Unknown token in padstack layer options: {oname!r}")
        elif cname == "primitives":
            prims = []
            for pc in children(child):
                if pc.node_type != SExprNodeType.LIST:
                    continue
                prims.append(_load_pad_primitive(pc))
            primitives = prims
        elif cname == "thermal_bridge_angle":
            thermal_bridge_angle = get_float(children(child)[0])
        elif cname == "thermal_gap":
            thermal_gap = get_float(children(child)[0])
        elif cname == "thermal_bridge_width":
            thermal_bridge_width = get_float(children(child)[0])
        elif cname == "clearance":
            clearance = get_float(children(child)[0])
        elif cname == "zone_connect":
            zone_connect = get_int(children(child)[0])
        elif cname == "tenting":
            # Same three-state reader as the pad, at parser.cpp:6799-6805.
            tenting_front, tenting_back = front_back_opt_bool(child, allow_legacy=True)
        else:
            raise KicadParseError(f"Unknown token in padstack layer: {cname!r}")

    return PadstackLayer(
        layer_name=layer_name,
        shape=shape,
        size=size,
        offset=offset,
        rect_delta=rect_delta,
        roundrect_rratio=roundrect_rratio,
        chamfer_ratio=chamfer_ratio,
        chamfer_positions=chamfer_positions,
        primitives=primitives,
        custom_anchor=custom_anchor,
        thermal_bridge_angle=thermal_bridge_angle,
        thermal_gap=thermal_gap,
        thermal_bridge_width=thermal_bridge_width,
        clearance=clearance,
        zone_connect=zone_connect,
        tenting_front=tenting_front,
        tenting_back=tenting_back,
    )


def _load_padstack(node: SExprNode) -> Padstack:
    """Parse a ``(padstack ...)`` block (§3.1)."""
    require_sym(node, "padstack")
    ch = children(node)
    mode = PadstackMode.front_inner_back
    layers: list[PadstackLayer] = []

    for child in ch:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "mode":
            mode_str = get_str(children(child)[0])
            try:
                mode = PadstackMode(mode_str)
            except ValueError as err:
                raise KicadParseError(f"Unknown padstack mode: {mode_str!r}") from err
        elif cname == "layer":
            layers.append(_load_padstack_layer(child))
        else:
            raise KicadParseError(f"Unknown token in padstack: {cname!r}")

    return Padstack(mode=mode, layers=layers)


# ---------------------------------------------------------------------------
# Variant loader
# ---------------------------------------------------------------------------


def _load_variant(node: SExprNode) -> Variant:
    """Parse a ``(variant ...)`` token (§2.12)."""
    require_sym(node, "variant")
    name = ""
    dnp = None
    exclude_from_bom = None
    exclude_from_pos_files = None
    fields: list[VariantField] = []

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "name":
            name = get_str(children(child)[0])
        elif cname == "dnp":
            dnp = bool_token(child)
        elif cname == "exclude_from_bom":
            exclude_from_bom = bool_token(child)
        elif cname == "exclude_from_pos_files":
            exclude_from_pos_files = bool_token(child)
        elif cname == "field":
            fc = children(child)
            field_name = ""
            field_value = ""
            for fc2 in fc:
                if fc2.node_type == SExprNodeType.LIST:
                    fn = sym_name(fc2)
                    if fn == "name":
                        field_name = get_str(children(fc2)[0])
                    elif fn == "value":
                        field_value = get_str(children(fc2)[0])
                    else:
                        raise KicadParseError(f"Unknown token in variant field: {fn!r}")
            fields.append(VariantField(name=field_name, value=field_value))
        else:
            raise KicadParseError(f"Unknown token in variant: {cname!r}")

    return Variant(
        name=name,
        dnp=dnp,
        exclude_from_bom=exclude_from_bom,
        exclude_from_pos_files=exclude_from_pos_files,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Footprint stackup loader
# ---------------------------------------------------------------------------


def _load_stackup(node: SExprNode) -> Stackup:
    """Parse a ``(stackup ...)`` block inside a footprint (§2.1 #24)."""
    require_sym(node, "stackup")
    layers: list[StackupLayer] = []
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "layer":
            layers.append(StackupLayer(name=get_str(children(child)[0])))
        else:
            raise KicadParseError(f"Unknown token in stackup: {cname!r}")
    return Stackup(layers=layers)


# ---------------------------------------------------------------------------
# Footprint unit info loader
# ---------------------------------------------------------------------------


def _load_unit_info(node: SExprNode) -> FootprintUnitInfo:
    """Parse a ``(units ...)`` gate-swap metadata block (§2.14)."""
    require_sym(node, "units")
    units: list[FootprintGateUnit] = []
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "unit":
            unit_name = ""
            pins: list[str] = []
            for uc in children(child):
                if uc.node_type != SExprNodeType.LIST:
                    continue
                un = sym_name(uc)
                if un == "name":
                    unit_name = get_str(children(uc)[0])
                elif un == "pins":
                    pins = [
                        get_str(x)
                        for x in children(uc)
                        if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
                    ]
                else:
                    raise KicadParseError(f"Unknown token in unit info unit: {un!r}")
            units.append(FootprintGateUnit(name=unit_name, pins=pins))
        else:
            raise KicadParseError(f"Unknown token in units: {cname!r}")
    return FootprintUnitInfo(units=units)


def _load_drill(node: SExprNode) -> DrillInfo:
    """Parse a ``(drill ...)`` token."""
    require_sym(node, "drill")
    ch = children(node)

    oval = False
    width = 0.0
    height = None
    offset = None

    idx = 0

    # Optional "oval" keyword
    if ch and ch[0].node_type == SExprNodeType.SYMBOL and ch[0].value == "oval":
        oval = True
        idx += 1

    # Numeric values (width, optionally height)
    numeric_vals = []
    while idx < len(ch) and ch[idx].node_type in (SExprNodeType.DOUBLE, SExprNodeType.INTEGER):
        numeric_vals.append(get_float(ch[idx]))
        idx += 1

    if numeric_vals:
        width = numeric_vals[0]
    if len(numeric_vals) >= 2:
        height = numeric_vals[1]

    # Remaining sub-lists
    for child in ch[idx:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "offset":
            oc = children(child)
            offset = (get_float(oc[0]), get_float(oc[1]))

    return DrillInfo(oval=oval, width=width, height=height, offset=offset)


def _load_teardrop(node: SExprNode) -> TeardropParams:
    """Parse a ``teardrops`` token."""
    require_sym(node, "teardrops")
    best_length_ratio = 0.5
    max_length = 1.0
    best_width_ratio = 0.5
    max_width = 1.0
    curved_edges = False
    filter_ratio = 0.75
    enabled = False
    allow_two_segments = True
    prefer_zone_connections = False

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "best_length_ratio":
            best_length_ratio = get_float(children(child)[0])
        elif cname == "max_length":
            max_length = get_float(children(child)[0])
        elif cname == "best_width_ratio":
            best_width_ratio = get_float(children(child)[0])
        elif cname == "max_width":
            max_width = get_float(children(child)[0])
        elif cname == "curve_points":
            # Legacy spelling, so marked in KiCad's own source
            # (``parser.cpp:711-715``). A segment COUNT stands in for the flag:
            # ``m_CurvedEdges = parseInt(…) > 0``. Never written back, because
            # the writer emits ``curved_edges`` instead.
            curved_edges = get_int(children(child)[0]) > 0
        elif cname == "curved_edges":
            curved_edges = bool_token(child)
        elif cname == "filter_ratio":
            filter_ratio = get_float(children(child)[0])
        elif cname == "enabled":
            enabled = bool_token(child)
        elif cname == "allow_two_segments":
            allow_two_segments = bool_token(child)
        elif cname == "prefer_zone_connections":
            prefer_zone_connections = bool_token(child)
        else:
            raise KicadParseError(f"Unknown token in teardrops: {cname!r}")

    return TeardropParams(
        best_length_ratio=best_length_ratio,
        max_length=max_length,
        best_width_ratio=best_width_ratio,
        max_width=max_width,
        curved_edges=curved_edges,
        filter_ratio=filter_ratio,
        enabled=enabled,
        allow_two_segments=allow_two_segments,
        prefer_zone_connections=prefer_zone_connections,
    )


# ---------------------------------------------------------------------------
# 3D Model
# ---------------------------------------------------------------------------


def _load_model(node: SExprNode) -> Model3D:
    """Parse a ``model`` token."""
    require_sym(node, "model")
    ch = children(node)

    if not ch:
        raise KicadParseError("model missing path")

    path = get_str(ch[0])
    visible = True
    opacity = None
    offset = (0.0, 0.0, 0.0)
    scale = (1.0, 1.0, 1.0)
    rotation = (0.0, 0.0, 0.0)

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "hide":
            visible = not bool_token(child)
        elif cname == "opacity":
            opacity = get_float(children(child)[0])
        elif cname == "offset":
            xyz_node = find_child(child, "xyz")
            if xyz_node is not None:
                xc = children(xyz_node)
                offset = (get_float(xc[0]), get_float(xc[1]), get_float(xc[2]))
        elif cname == "scale":
            xyz_node = find_child(child, "xyz")
            if xyz_node is not None:
                xc = children(xyz_node)
                scale = (get_float(xc[0]), get_float(xc[1]), get_float(xc[2]))
        elif cname == "rotate":
            xyz_node = find_child(child, "xyz")
            if xyz_node is not None:
                xc = children(xyz_node)
                rotation = (get_float(xc[0]), get_float(xc[1]), get_float(xc[2]))
        else:
            raise KicadParseError(f"Unknown token in model: {cname!r}")

    return Model3D(
        path=path, visible=visible, opacity=opacity, offset=offset, scale=scale, rotation=rotation
    )


# ---------------------------------------------------------------------------
# Root loader
# ---------------------------------------------------------------------------


def load_mod(tree: SExprNode) -> Footprint:
    """Load a ``Footprint`` from a parsed S-expression tree.

    Args:
        tree: The root ``SExprNode`` as returned by ``parse()``.

    Returns:
        A fully populated ``Footprint`` instance.

    Raises:
        KicadParseError: if the tree is not a valid footprint.
    """
    require_sym(tree, "footprint")
    ch = children(tree)

    if not ch:
        raise KicadParseError("footprint missing fp_id")

    fp_id = get_str(ch[0])

    version = 0
    generator = ""
    generator_version = None
    locked = False
    placed = False
    layer = "F.Cu"
    uuid = None
    at = None
    description = None
    keywords = None
    properties: list[Property] = []
    component_classes = None
    fp_filters = None
    path = None
    sheetname = None
    sheetfile = None
    unit_info = None
    solder_mask_margin = None
    solder_paste_margin = None
    solder_paste_margin_ratio = None
    clearance = None
    zone_connect = None
    attributes = FootprintAttrs()
    stackup = None
    private_layers = None
    net_tie_pad_groups = None
    duplicate_pad_numbers_are_jumpers = False
    jumper_pad_groups: JumperPadGroups | None = None
    drawings: list[PcbDrawItem] = []
    points: list[PcbPoint] = []
    pads: list[Pad] = []
    zones: list[Zone] = []
    groups: list[PcbGroup] = []
    variants: list[Variant] = []
    embedded_fonts = False
    embedded_files: list[EmbeddedFile] = []
    models: list[Model3D] = []

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            # Check for bare "locked" symbol
            if child.node_type == SExprNodeType.SYMBOL and child.value == "locked":
                locked = True
            continue
        cname = sym_name(child)

        if cname == "version":
            version = get_int(children(child)[0])
        elif cname == "generator":
            generator = get_str(children(child)[0])
        elif cname == "generator_version":
            generator_version = get_str(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "placed":
            placed = bool_token(child)
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid":
            uuid = get_str(children(child)[0])  # board-context footprint uuid (GAP-033)
        elif cname == "at":
            atc = children(child)
            at = FootprintPlacement(
                position=(get_float(atc[0]), get_float(atc[1])),
                angle=get_float(atc[2]) if len(atc) > 2 else 0.0,
            )
        elif cname == "descr":
            description = get_str(children(child)[0])
        elif cname == "tags":
            keywords = get_str(children(child)[0])
        elif cname == "property":
            # T9: route ki_fp_filters property to fp_filters field (§8.3 #11)
            prop = _load_property_with_uuid(child)
            if prop.name == "ki_fp_filters":
                fp_filters = prop.value
            else:
                properties.append(prop)
        elif cname == "component_classes":
            classes = []
            for cc in children(child):
                if cc.node_type == SExprNodeType.LIST and sym_name(cc) == "class":
                    cc_ch = children(cc)
                    if cc_ch:
                        classes.append(get_str(cc_ch[0]))
            from eedesigncore.common.pcb_family.footprint_scoped import (
                ComponentClasses as CC,
            )

            component_classes = CC(classes=classes)
        elif cname == "path":
            path = get_str(children(child)[0])
        elif cname == "sheetname":
            sheetname = get_str(children(child)[0])
        elif cname == "sheetfile":
            sheetfile = get_str(children(child)[0])
        elif cname == "units":
            unit_info = _load_unit_info(child)
        elif cname == "attr":
            attributes = _load_attrs(child)
        elif cname == "stackup":
            stackup = _load_stackup(child)
        elif cname == "duplicate_pad_numbers_are_jumpers":
            duplicate_pad_numbers_are_jumpers = bool_token(child)
        elif cname == "jumper_pad_groups":
            # A group is a bare list of pad numbers, ``("1" "2")``. It carries
            # no keyword, so every element is data. Reading it with the
            # keyword-skipping helper dropped the first pad of every group.
            groups_list: JumperPadGroups = []
            for grp in children(child):
                if grp.node_type == SExprNodeType.LIST:
                    groups_list.append(
                        [
                            get_str(x)
                            for x in elements(grp)
                            if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
                        ]
                    )
            jumper_pad_groups = groups_list
        elif cname == "private_layers":
            private_layers = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]
        elif cname == "net_tie_pad_groups":
            net_tie_pad_groups = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]
        elif cname in (
            "fp_text",
            "fp_line",
            "fp_rect",
            "fp_circle",
            "fp_arc",
            "fp_poly",
            "fp_curve",
            "fp_text_box",
        ):
            drawings.append(_load_drawing(child))
        elif cname == "point":
            points.append(_load_pcb_point(child))
        elif cname == "pad":
            pads.append(_load_pad(child))
        elif cname == "zone":
            zones.append(_load_zone(child))
        elif cname == "group":
            groups.append(_load_group(child))
        elif cname == "variant":
            variants.append(_load_variant(child))
        elif cname == "table":
            drawings.append(_load_table(child))
        elif cname == "image":
            drawings.append(_load_image(child))
        elif cname == "barcode":
            drawings.append(_load_barcode(child))
        elif cname == "dimension":
            drawings.append(_load_dimension(child))
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(children(child)[0])
        elif cname == "solder_paste_margin":
            solder_paste_margin = get_float(children(child)[0])
        elif cname == "solder_paste_margin_ratio":
            solder_paste_margin_ratio = get_float(children(child)[0])
        elif cname == "clearance":
            clearance = get_float(children(child)[0])
        elif cname == "zone_connect":
            zone_connect = get_int(children(child)[0])
        elif cname == "embedded_fonts":
            embedded_fonts = bool_token(child)
        elif cname == "embedded_files":
            for fc in children(child):
                if fc.node_type == SExprNodeType.LIST and sym_name(fc) == "file":
                    embedded_files.append(load_embedded_file(fc))
        elif cname == "model":
            models.append(_load_model(child))
        # Legacy / parser-only tokens (§2.1)
        elif cname in (
            "module",
            "tedit",
            "tstamp",
            "autoplace_cost90",
            "autoplace_cost180",
            "thermal_width",
            "thermal_gap",
            "solder_paste_ratio",
        ):
            pass  # accepted and discarded

        else:
            raise KicadParseError(f"Unknown token in footprint: {cname!r}")

    fp = Footprint(
        fp_id=fp_id,
        version=version,
        generator=generator,
        generator_version=generator_version,
        locked=locked,
        placed=placed,
        layer=layer,
        uuid=uuid,
        at=at,
        description=description,
        keywords=keywords,
        properties=properties,
        component_classes=component_classes,
        fp_filters=fp_filters,
        path=path,
        sheetname=sheetname,
        sheetfile=sheetfile,
        unit_info=unit_info,
        solder_mask_margin=solder_mask_margin,
        solder_paste_margin=solder_paste_margin,
        solder_paste_margin_ratio=solder_paste_margin_ratio,
        clearance=clearance,
        zone_connect=zone_connect,
        attributes=attributes,
        stackup=stackup,
        private_layers=private_layers,
        net_tie_pad_groups=net_tie_pad_groups,
        duplicate_pad_numbers_are_jumpers=duplicate_pad_numbers_are_jumpers,
        jumper_pad_groups=jumper_pad_groups,
        drawings=drawings,
        points=points,
        pads=pads,
        zones=zones,
        groups=groups,
        variants=variants,
        embedded_fonts=embedded_fonts,
        embedded_files=embedded_files,
        models=models,
    )

    return fp


def _load_attrs(node: SExprNode) -> FootprintAttrs:
    """Parse an ``(attr ...)`` token."""
    require_sym(node, "attr")
    smd = False
    through_hole = False
    board_only = False
    exclude_from_pos_files = False
    exclude_from_bom = False
    allow_missing_courtyard = False
    dnp = False
    allow_soldermask_bridges = False

    for child in children(node):
        if child.node_type == SExprNodeType.SYMBOL:
            val = str(child.value)
            if val == "smd":
                smd = True
            elif val == "through_hole":
                through_hole = True
            elif val == "board_only":
                board_only = True
            elif val == "exclude_from_pos_files":
                exclude_from_pos_files = True
            elif val == "exclude_from_bom":
                exclude_from_bom = True
            elif val == "allow_missing_courtyard":
                allow_missing_courtyard = True
            elif val == "dnp":
                dnp = True
            elif val == "allow_soldermask_bridges":
                allow_soldermask_bridges = True
            else:
                raise KicadParseError(f"Unknown attr flag: {val!r}")
        elif child.node_type == SExprNodeType.LIST:
            raise KicadParseError(f"Unexpected list in attr: {sym_name(child)!r}")

    return FootprintAttrs(
        smd=smd,
        through_hole=through_hole,
        board_only=board_only,
        exclude_from_pos_files=exclude_from_pos_files,
        exclude_from_bom=exclude_from_bom,
        allow_missing_courtyard=allow_missing_courtyard,
        dnp=dnp,
        allow_soldermask_bridges=allow_soldermask_bridges,
    )
