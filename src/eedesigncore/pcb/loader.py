"""Board loader: parsed S-expression tree → ``Board`` model.

T1 landed the header wrapper; T2 adds the header block loaders (general, paper,
title_block, layers, setup, properties, variants). Item streams
(footprints/tracks/zones/etc.) are added in T4+/T5+. Unknown tokens raise
``KicadParseError`` per the ADR-017 hard-error contract.
"""

from __future__ import annotations

from eedesigncore.common.layers import is_valid_layer_name
from eedesigncore.common.loaders import (
    bool_token,
    children,
    find_child,
    front_back_opt_bool,
    get_float,
    get_int,
    get_str,
    load_embedded_files,
    opt_bool_token,
    require_sym,
    sym_name,
)
from eedesigncore.common.loaders import load_paper as _load_paper
from eedesigncore.common.loaders import load_title_block as _load_title_block
from eedesigncore.common.models import EmbeddedFile, Property
from eedesigncore.common.pcb_family import (
    PcbDrawItem,
    PcbGroup,
    PcbPoint,
    Zone,
    ZoneLayerProperty,
)
from eedesigncore.common.pcb_family.loaders import (
    _load_barcode,
    _load_dimension,
    _load_drawing,
    _load_group,
    _load_image,
    _load_pcb_point,
    _load_pcb_text,
    _load_pts_list,
    _load_table,
    _load_zone,
)
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.loader import (
    _load_backdrill,
    _load_post_machining,
    _load_teardrop,
    load_mod,
)
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import (
    Board,
    BoardGeneral,
    BoardLayer,
    BoardStackup,
    BoardStackupLayer,
    BoardStackupSubLayer,
    BoardVariant,
    FrontBack,
    GenBool,
    GeneratorValue,
    GenNumber,
    GenPoint,
    GenPts,
    GenString,
    PcbGenerator,
    PcbTarget,
    PlotParams,
    Setup,
    TrackArc,
    TrackItem,
    TrackSegment,
    Via,
    ViaFrontBackOpt,
    ViaPadstack,
    ViaPadstackLayer,
)
from eedesigncore.sexpr.nodes import SExprNode, SExprNodeType

# Type-column tokens accepted in the (layers) table (A1 §2).
_LAYER_TYPE_TOKENS = frozenset(
    {"signal", "power", "mixed", "jumper", "auxiliary", "front", "back", "user"}
)

# pcbplotparams field classification (board-root report §2c-iii).
_PLOT_BOOL_FIELDS = frozenset(
    {
        "disableapertmacros",
        "usegerberextensions",
        "usegerberattributes",
        "usegerberadvancedattributes",
        "creategerberjobfile",
        "plotframeref",
        "useauxorigin",
        "pdf_front_fp_property_popups",
        "pdf_back_fp_property_popups",
        "pdf_metadata",
        "pdf_single_document",
        "dxfpolygonmode",
        "dxfimperialunits",
        "dxfusepcbnewfont",
        "psnegative",
        "psa4output",
        "plot_black_and_white",
        "sketchpadsonfab",
        "plotpadnumbers",
        "hidednponfab",
        "sketchdnponfab",
        "crossoutdnponfab",
        "subtractmaskfromsilk",
        "mirror",
    }
)
_PLOT_INT_FIELDS = frozenset(
    {"gerberprecision", "svgprecision", "mode", "outputformat", "drillshape", "scaleselection"}
)
_PLOT_FLOAT_FIELDS = frozenset({"dashed_line_dash_ratio", "dashed_line_gap_ratio"})
_PLOT_STR_FIELDS = frozenset({"layerselection", "plot_on_all_layers_selection", "outputdirectory"})


# ---------------------------------------------------------------------------
# Header blocks
# ---------------------------------------------------------------------------


def _load_general(node: SExprNode) -> BoardGeneral:
    thickness = 1.6
    legacy_teardrops = False
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "thickness":
            thickness = get_float(children(child)[0])
        elif cname == "legacy_teardrops":
            legacy_teardrops = bool_token(child)
        else:
            raise KicadParseError(f"Unknown token in general: {cname!r}")
    return BoardGeneral(thickness=thickness, legacy_teardrops=legacy_teardrops)


def _load_layer_row(node: SExprNode) -> BoardLayer:
    """Parse one ``(<ordinal> "<name>" <type> [user_name|hide])`` row (A1 §3, §4a).

    Layer rows begin with the integer ordinal (not a keyword symbol), so the full
    element list is used rather than ``children()`` (which strips element 0).
    """
    assert isinstance(node.value, list)
    ch = node.value
    if len(ch) < 3:
        raise KicadParseError("layers row missing ordinal/name/type")
    ordinal = get_int(ch[0])
    canonical_name = get_str(ch[1])
    layer_type = get_str(ch[2])
    if layer_type not in _LAYER_TYPE_TOKENS:
        raise KicadParseError(f"Unknown layer type token: {layer_type!r}")
    # Non-copper names are hard-validated against the canonical table (A1 §4b).
    if not is_valid_layer_name(canonical_name):
        raise KicadParseError(f"Unknown layer name: {canonical_name!r}")
    user_name = None
    if len(ch) > 3:
        extra = ch[3]
        # A1 §4a: one optional trailing token — a quoted user-name or bare `hide`
        # (the parser tolerates `hide`; the writer never emits it, so drop it).
        if not (extra.node_type == SExprNodeType.SYMBOL and extra.value == "hide"):
            user_name = get_str(extra)
    return BoardLayer(
        ordinal=ordinal,
        canonical_name=canonical_name,
        layer_type=layer_type,  # type: ignore[arg-type]
        user_name=user_name,
    )


def _load_layers(node: SExprNode) -> list[BoardLayer]:
    rows = []
    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        rows.append(_load_layer_row(child))
    return rows


def _load_stackup_layer(node: SExprNode) -> BoardStackupLayer:
    ch = children(node)
    if not ch:
        raise KicadParseError("stackup layer missing name")
    name = get_str(ch[0])
    layer_type = ""
    sublayers: list[BoardStackupSubLayer] = []
    cur: dict[str, object] = {}

    def _flush() -> None:
        sublayers.append(BoardStackupSubLayer(**cur))  # type: ignore[arg-type]

    for c in ch[1:]:
        if c.node_type == SExprNodeType.SYMBOL and c.value == "addsublayer":
            _flush()
            cur = {}
            continue
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "type":
            layer_type = get_str(cc[0])
        elif cname == "color":
            cur["color"] = get_str(cc[0])
        elif cname == "thickness":
            cur["thickness"] = get_float(cc[0])
            if len(cc) > 1 and cc[1].node_type == SExprNodeType.SYMBOL and cc[1].value == "locked":
                cur["thickness_locked"] = True
        elif cname == "material":
            cur["material"] = get_str(cc[0])
        elif cname == "epsilon_r":
            cur["epsilon_r"] = get_float(cc[0])
        elif cname == "loss_tangent":
            cur["loss_tangent"] = get_float(cc[0])
        else:
            raise KicadParseError(f"Unknown token in stackup layer: {cname!r}")
    _flush()
    return BoardStackupLayer(name=name, layer_type=layer_type, sublayers=sublayers)


def _load_stackup(node: SExprNode) -> BoardStackup:
    layers: list[BoardStackupLayer] = []
    copper_finish = None
    dielectric_constraints = False
    edge_connector = None
    edge_plating = False
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        if cname == "layer":
            layers.append(_load_stackup_layer(c))
        elif cname == "copper_finish":
            copper_finish = get_str(children(c)[0])
        elif cname == "dielectric_constraints":
            dielectric_constraints = bool_token(c)
        elif cname == "edge_connector":
            edge_connector = get_str(children(c)[0])
        elif cname == "edge_plating":
            edge_plating = bool_token(c)
        else:
            raise KicadParseError(f"Unknown token in stackup: {cname!r}")
    return BoardStackup(
        layers=layers,
        copper_finish=copper_finish,
        dielectric_constraints=dielectric_constraints,
        edge_connector=edge_connector,
        edge_plating=edge_plating,
    )


def _load_front_back(node: SExprNode, *, allow_legacy: bool = False) -> FrontBack:
    """Read a board setup ``(tenting|covering|plugging …)`` block.

    Board setup stores a plain bool, because KiCad collapses the optional with
    ``value_or( false )`` at ``pcb_io_kicad_sexpr_parser.cpp:2744-2745``. An
    unset side therefore reads as false HERE, which is not the same as the
    default that applies when the whole token is absent: that one lives on the
    model field and is TENTED (``board_design_settings.cpp:231-232``).
    """
    front, back = front_back_opt_bool(node, allow_legacy=allow_legacy)
    return FrontBack(front=bool(front), back=bool(back))


def _load_zone_layer_property(node: SExprNode) -> ZoneLayerProperty:
    layer = ""
    hatch_position = None
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        if cname == "layer":
            layer = get_str(children(c)[0])
        elif cname == "hatch_position":
            xy = find_child(c, "xy")
            if xy is not None:
                xyc = children(xy)
                hatch_position = (get_float(xyc[0]), get_float(xyc[1]))
        else:
            raise KicadParseError(f"Unknown token in zone_defaults property: {cname!r}")
    return ZoneLayerProperty(layer=layer, hatch_position=hatch_position)


def _load_plot_params(node: SExprNode) -> PlotParams:
    kwargs: dict[str, object] = {}
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname in _PLOT_BOOL_FIELDS:
            kwargs[cname] = bool_token(c)
        elif cname in _PLOT_INT_FIELDS:
            kwargs[cname] = get_int(cc[0])
        elif cname in _PLOT_FLOAT_FIELDS:
            kwargs[cname] = get_float(cc[0])
        elif cname in _PLOT_STR_FIELDS:
            kwargs[cname] = get_str(cc[0])
        else:
            # A2 contract: mirror KiCad's `default: skipCurrent()` (PP:810-813).
            # Legacy/unknown pcbplotparams children (plotreference/plotvalue/
            # plotfptext, svguseinch, excludeedgelayer, viasonmask, hpgl*,
            # pscolor, plotinvisibletext, etc.) are accepted-and-dropped on read.
            # KiCad 10.0.3 never emits them, so byte-identity is unaffected for
            # KiCad-written files; a legacy file carrying them drops them on
            # round-trip (documented as a known legacy-input divergence).
            continue
    return PlotParams(**kwargs)  # type: ignore[arg-type]


def _load_setup(node: SExprNode) -> Setup:
    kwargs: dict[str, object] = {}
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "stackup":
            kwargs["stackup"] = _load_stackup(c)
        elif cname == "pad_to_mask_clearance":
            kwargs["pad_to_mask_clearance"] = get_float(cc[0])
        elif cname == "solder_mask_min_width":
            kwargs["solder_mask_min_width"] = get_float(cc[0])
        elif cname == "pad_to_paste_clearance":
            kwargs["pad_to_paste_clearance"] = get_float(cc[0])
        elif cname == "pad_to_paste_clearance_ratio":
            kwargs["pad_to_paste_clearance_ratio"] = get_float(cc[0])
        elif cname == "allow_soldermask_bridges_in_footprints":
            kwargs["allow_soldermask_bridges_in_footprints"] = bool_token(c)
        elif cname == "tenting":
            kwargs["tenting"] = _load_front_back(c, allow_legacy=True)
        elif cname == "covering":
            kwargs["covering"] = _load_front_back(c)
        elif cname == "plugging":
            kwargs["plugging"] = _load_front_back(c)
        elif cname == "capping":
            kwargs["capping"] = bool_token(c)
        elif cname == "filling":
            kwargs["filling"] = bool_token(c)
        elif cname == "zone_defaults":
            kwargs["zone_defaults"] = [
                _load_zone_layer_property(p)
                for p in children(c)
                if p.node_type == SExprNodeType.LIST
            ]
        elif cname == "aux_axis_origin":
            kwargs["aux_axis_origin"] = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "grid_origin":
            kwargs["grid_origin"] = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "pcbplotparams":
            kwargs["pcbplotparams"] = _load_plot_params(c)
        else:
            raise KicadParseError(f"Unknown token in setup: {cname!r}")
    return Setup(**kwargs)  # type: ignore[arg-type]


def _load_board_property(node: SExprNode) -> Property:
    """Parse a board-root ``(property "<key>" "<value>")`` (§2d)."""
    ch = children(node)
    if len(ch) < 2:
        raise KicadParseError("board property missing key/value")
    return Property(name=get_str(ch[0]), value=get_str(ch[1]))


def _load_board_variant(node: SExprNode) -> BoardVariant:
    """Parse a board-level ``(variant (name …) [(description …)])`` (§2e)."""
    name = ""
    description = None
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "name":
            name = get_str(cc[0])
        elif cname == "description":
            description = get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in variant: {cname!r}")
    return BoardVariant(name=name, description=description)


def _load_variants(node: SExprNode) -> list[BoardVariant]:
    return [
        _load_board_variant(c)
        for c in children(node)
        if c.node_type == SExprNodeType.LIST and sym_name(c) == "variant"
    ]


# ---------------------------------------------------------------------------
# Tracks / arcs / vias (§4)
# ---------------------------------------------------------------------------


def _parse_item_net(node: SExprNode) -> str | None:
    """Parse an item ``(net …)`` reference, returning the name or None (A2).

    Accepts name-only ``(net "<name>")`` (v10), legacy code+name
    ``(net <code> "<name>")``, and legacy code-only ``(net <code>)`` (no name
    available -> None). A leading integer is a legacy code, consumed and ignored.
    """
    ch = children(node)
    if not ch:
        return None
    if len(ch) >= 2:
        return get_str(ch[1])
    first = ch[0]
    if first.node_type in (SExprNodeType.INTEGER, SExprNodeType.DOUBLE):
        return None
    return get_str(first)


def _load_via_front_back(node: SExprNode, *, allow_legacy: bool = False) -> ViaFrontBackOpt | None:
    """Parse a via ``(tenting|covering|plugging …)`` block.

    Returns None (block dropped) when both sides are unspecified, matching
    KiCad's ``has_value``-guarded emit (W:2739-2773). ``(tenting none)`` reaches
    that state through the legacy spelling, so it is written back as no block at
    all, which is what KiCad does with it too.
    """
    front, back = front_back_opt_bool(node, allow_legacy=allow_legacy)
    if front is None and back is None:
        return None
    return ViaFrontBackOpt(front=front, back=back)


def _load_track_layers(node: SExprNode) -> list[str]:
    return [get_str(c) for c in children(node)]


def _load_track_segment(node: SExprNode) -> TrackSegment:
    start = (0.0, 0.0)
    end = (0.0, 0.0)
    width = 0.0
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net = None
    uuid = None
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "start":
            start = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "end":
            end = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "width":
            width = get_float(cc[0])
        elif cname == "locked":
            locked = bool_token(c)
        elif cname == "layer":
            layer = get_str(cc[0])
        elif cname == "layers":
            layers = _load_track_layers(c)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(cc[0])
        elif cname == "net":
            net = _parse_item_net(c)
        elif cname == "uuid":
            uuid = get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in segment: {cname!r}")
    return TrackSegment(
        start=start,
        end=end,
        width=width,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net=net,
        uuid=uuid,
    )


def _load_track_arc(node: SExprNode) -> TrackArc:
    start = (0.0, 0.0)
    mid = (0.0, 0.0)
    end = (0.0, 0.0)
    width = 0.0
    locked = False
    layer = ""
    layers = None
    solder_mask_margin = None
    net = None
    uuid = None
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "start":
            start = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "mid":
            mid = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "end":
            end = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "width":
            width = get_float(cc[0])
        elif cname == "locked":
            locked = bool_token(c)
        elif cname == "layer":
            layer = get_str(cc[0])
        elif cname == "layers":
            layers = _load_track_layers(c)
        elif cname == "solder_mask_margin":
            solder_mask_margin = get_float(cc[0])
        elif cname == "net":
            net = _parse_item_net(c)
        elif cname == "uuid":
            uuid = get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in arc: {cname!r}")
    return TrackArc(
        start=start,
        mid=mid,
        end=end,
        width=width,
        locked=locked,
        layer=layer,
        layers=layers,
        solder_mask_margin=solder_mask_margin,
        net=net,
        uuid=uuid,
    )


def _load_via_padstack(node: SExprNode) -> ViaPadstack:
    """Parse a via ``(padstack …)`` block (parseViastack :7726-7820)."""
    mode: str = "front_inner_back"
    layers: list[ViaPadstackLayer] = []
    for c in children(node):
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        if cname == "mode":
            mode = str(children(c)[0].value)
            if mode not in ("front_inner_back", "custom"):
                raise KicadParseError(f"Unknown via padstack mode: {mode!r}")
        elif cname == "layer":
            lc = children(c)
            layer_name = get_str(lc[0])
            size = 0.0
            size_node = find_child(c, "size")
            if size_node is not None:
                size = get_float(children(size_node)[0])
            layers.append(ViaPadstackLayer(layer_name=layer_name, size=size))
        else:
            raise KicadParseError(f"Unknown token in via padstack: {cname!r}")
    return ViaPadstack(mode=mode, layers=layers)  # type: ignore[arg-type]


def _load_via(node: SExprNode) -> Via:
    via_type = "through"
    at = (0.0, 0.0)
    size = 0.0
    drill = 0.0
    layers = ("F.Cu", "B.Cu")
    kwargs: dict[str, object] = {}
    remove_unused = False
    keep_end = False
    start_end_only = False
    for c in children(node):
        # Bare via-type qualifier token (blind/buried/micro) before (at …).
        if c.node_type == SExprNodeType.SYMBOL and c.value in (
            "blind",
            "buried",
            "micro",
        ):
            via_type = str(c.value)
            continue
        if c.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(c)
        cc = children(c)
        if cname == "at":
            at = (get_float(cc[0]), get_float(cc[1]))
        elif cname == "size":
            size = get_float(cc[0])
        elif cname == "drill":
            drill = get_float(cc[0])
        elif cname == "backdrill":
            kwargs["backdrill"] = _load_backdrill(c)
        elif cname == "tertiary_drill":
            kwargs["tertiary_drill"] = _load_backdrill(c)
        elif cname == "front_post_machining":
            kwargs["front_post_machining"] = _load_post_machining(c)
        elif cname == "back_post_machining":
            kwargs["back_post_machining"] = _load_post_machining(c)
        elif cname == "layers":
            layers = (get_str(cc[0]), get_str(cc[1]))
        elif cname == "remove_unused_layers":
            remove_unused = bool_token(c)
        elif cname == "keep_end_layers":
            keep_end = bool_token(c)
        elif cname == "start_end_only":
            start_end_only = bool_token(c)
        elif cname == "locked":
            kwargs["locked"] = bool_token(c)
        elif cname == "free":
            kwargs["free"] = bool_token(c)
        elif cname == "zone_layer_connections":
            kwargs["zone_layer_connections"] = [get_str(x) for x in cc]
        elif cname == "tenting":
            kwargs["tenting"] = _load_via_front_back(c, allow_legacy=True)
        elif cname == "capping":
            kwargs["capping"] = opt_bool_token(c)
        elif cname == "covering":
            kwargs["covering"] = _load_via_front_back(c)
        elif cname == "plugging":
            kwargs["plugging"] = _load_via_front_back(c)
        elif cname == "filling":
            kwargs["filling"] = opt_bool_token(c)
        elif cname == "padstack":
            kwargs["padstack"] = _load_via_padstack(c)
        elif cname == "teardrops":
            kwargs["teardrops"] = _load_teardrop(c)
        elif cname == "net":
            kwargs["net"] = _parse_item_net(c)
        elif cname == "uuid":
            kwargs["uuid"] = get_str(cc[0])
        else:
            raise KicadParseError(f"Unknown token in via: {cname!r}")

    if start_end_only:
        kwargs["unconnected_layer_mode"] = "start_end_only"
    elif remove_unused:
        kwargs["unconnected_layer_mode"] = "remove_except_start_end" if keep_end else "remove_all"

    return Via(via_type=via_type, at=at, size=size, drill=drill, layers=layers, **kwargs)  # type: ignore[arg-type]


def _load_track_item(node: SExprNode, cname: str) -> TrackItem:
    if cname == "segment":
        return _load_track_segment(node)
    if cname == "arc":
        return _load_track_arc(node)
    return _load_via(node)


# ---------------------------------------------------------------------------
# Board drawings stream (§1 item 3) — T5 placeholder routing.
#
# The full board drawings stream (writer + refinements) lands in T6; this
# routes each drawing token to the shared ``pcb_family`` loader so parse-only
# (M1) can progress. Board shapes use the ``gr_`` prefix; the shared shape
# loaders require the ``fp_`` token name, so gr_* shape nodes are bridged by
# renaming the leading symbol before dispatch (the shape models are prefix
# agnostic — they carry geometry only). Non-shape drawings (table/dimension/
# image/barcode) share their board token name and dispatch directly.
# ---------------------------------------------------------------------------

_GR_TO_FP: dict[str, str] = {
    "gr_text": "fp_text",
    "gr_line": "fp_line",
    "gr_rect": "fp_rect",
    "gr_circle": "fp_circle",
    "gr_arc": "fp_arc",
    "gr_poly": "fp_poly",
    "gr_curve": "fp_curve",
    "gr_text_box": "fp_text_box",
}


def _rename_token(node: SExprNode, new_name: str) -> SExprNode:
    """Return a shallow copy of *node* with its leading symbol renamed."""
    assert isinstance(node.value, list)
    new_children = [SExprNode.symbol(new_name), *node.value[1:]]
    return SExprNode.make_list(new_children)


def _load_board_drawing(node: SExprNode, cname: str) -> PcbDrawItem:
    """Route a board drawing token to the shared ``pcb_family`` loader.

    Board ``gr_*`` shapes/text share the ``pcb_family`` models with footprint
    ``fp_*`` (ADR-020); the loader bridges the prefix by renaming the leading
    token. ``gr_text`` additionally has NO leading type field (unlike
    ``fp_text``), so it is parsed with ``has_type=False``
    (``pcb_io_kicad_sexpr.cpp:2277-2285``). The board writer re-emits the ``gr_``
    prefix via its ``prefix`` argument.
    """
    if cname == "gr_text":
        return _load_pcb_text(_rename_token(node, "fp_text"), has_type=False)
    if cname in _GR_TO_FP:
        return _load_drawing(_rename_token(node, _GR_TO_FP[cname]))
    if cname == "table":
        return _load_table(node)
    if cname == "dimension":
        return _load_dimension(node)
    if cname == "image":
        return _load_image(node)
    if cname == "barcode":
        return _load_barcode(node)
    raise KicadParseError(f"Unknown board drawing token: {cname!r}")


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


def _load_generator_value(child: SExprNode, key: str) -> GeneratorValue:
    """Dispatch a generator property value across the five A3 branches.

    Tolerates unknown KEYS (any *key* is accepted) but hard-errors unknown value
    FORMS -- exactly mirroring KiCad's parseGENERATOR default case (A3 §6,
    P:7083-7156): a bare non-yes/no symbol or an inner list other than
    ``xy``/``pts`` throws.
    """
    ch = children(child)
    if len(ch) < 1:
        raise KicadParseError(f"generator property {key!r} missing value")
    val = ch[0]
    if val.node_type == SExprNodeType.SYMBOL:
        if val.value == "yes":
            return GenBool(value=True)
        if val.value == "no":
            return GenBool(value=False)
        raise KicadParseError(
            f"generator property {key!r}: unexpected symbol value {val.value!r} "
            "(expected a number, string, yes/no, (xy …) or (pts …))"
        )
    if val.node_type in (SExprNodeType.INTEGER, SExprNodeType.DOUBLE):
        return GenNumber(value=get_float(val))
    if val.node_type == SExprNodeType.STRING:
        return GenString(value=get_str(val))
    if val.node_type == SExprNodeType.LIST:
        inner = sym_name(val)
        if inner == "xy":
            xyc = children(val)
            return GenPoint(value=(get_float(xyc[0]), get_float(xyc[1])))
        if inner == "pts":
            return GenPts(value=_load_pts_list(val))
        raise KicadParseError(
            f"generator property {key!r}: expected (xy …) or (pts …), got {inner!r}"
        )
    raise KicadParseError(f"generator property {key!r}: unsupported value form")


def _load_generator(node: SExprNode) -> PcbGenerator:
    """Parse a ``(generated …)`` block (§5 / A3 report)."""
    require_sym(node, "generated")

    uuid = None
    generator_type = "tuning_pattern"
    name = ""
    layer = ""
    locked = False
    properties: dict[str, GeneratorValue] = {}
    members: list[str] = []

    for child in children(node):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname in ("uuid", "id"):
            # `id` is the legacy pre-20231215 alias for `uuid` (A3 §1 / P:7040).
            uuid = get_str(children(child)[0])
        elif cname == "type":
            generator_type = get_str(children(child)[0])
        elif cname == "name":
            name = get_str(children(child)[0])
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "locked":
            locked = bool_token(child)
        elif cname == "members":
            members = [
                get_str(x)
                for x in children(child)
                if x.node_type in (SExprNodeType.STRING, SExprNodeType.SYMBOL)
            ]
        else:
            # Any other key is a property-bag entry (keys tolerant, forms strict).
            properties[cname] = _load_generator_value(child, cname)

    return PcbGenerator(
        uuid=uuid,
        generator_type=generator_type,
        name=name,
        layer=layer,
        locked=locked,
        properties=properties,
        members=members,
    )


def _load_target(node: SExprNode) -> PcbTarget:
    """Parse a ``(target …)`` marker (§6 / A3 §7)."""
    require_sym(node, "target")
    ch = children(node)
    if not ch:
        raise KicadParseError("target missing shape token")

    # Shape token: bare symbol `x` or `plus` (W:1163).
    shape = get_str(ch[0])
    if shape not in ("x", "plus"):
        raise KicadParseError(f"target: expected shape 'x' or 'plus', got {shape!r}")
    is_x = shape == "x"

    position = (0.0, 0.0)
    size = 0.0
    width = 0.0
    layer = ""
    uuid = None

    for child in ch[1:]:
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)
        if cname == "at":
            atc = children(child)
            position = (get_float(atc[0]), get_float(atc[1]))
        elif cname == "size":
            size = get_float(children(child)[0])
        elif cname == "width":
            width = get_float(children(child)[0])
        elif cname == "layer":
            layer = get_str(children(child)[0])
        elif cname == "uuid":
            uuid = get_str(children(child)[0])
        else:
            raise KicadParseError(f"Unknown token in target: {cname!r}")

    return PcbTarget(is_x=is_x, position=position, size=size, width=width, layer=layer, uuid=uuid)


def load_pcb(tree: SExprNode) -> Board:
    """Load a ``Board`` from a parsed S-expression tree.

    Raises:
        KicadParseError: if the tree is not a valid board.
    """
    require_sym(tree, "kicad_pcb")

    kwargs: dict[str, object] = {}
    properties: list[Property] = []
    tracks: list[TrackItem] = []
    footprints: list[Footprint] = []
    drawings: list[PcbDrawItem] = []
    points: list[PcbPoint] = []
    zones: list[Zone] = []
    groups: list[PcbGroup] = []
    generators: list[PcbGenerator] = []
    targets: list[PcbTarget] = []
    trailing_drawings: list[PcbDrawItem] = []
    embedded_files: list[EmbeddedFile] = []
    seen_embedded_fonts = False

    for child in children(tree):
        if child.node_type != SExprNodeType.LIST:
            continue
        cname = sym_name(child)

        # §1 item streams (order-independent on load; writer re-sorts).
        if cname in ("segment", "arc", "via"):
            tracks.append(_load_track_item(child, cname))
        elif cname in ("footprint", "module"):
            # `module` is the legacy alias for `footprint` (A2 Q6). The mod loader
            # already accepts board-context footprint tokens (uuid/at/path/nets).
            footprints.append(load_mod(_rename_token(child, "footprint")))
        elif cname in _GR_TO_FP or cname in ("table", "dimension", "image", "barcode"):
            # A drawing appearing after (embedded_fonts …) is preserved in a
            # trailing stream re-emitted in that same position (ADR-034).
            drawing = _load_board_drawing(child, cname)
            if seen_embedded_fonts:
                trailing_drawings.append(drawing)
            else:
                drawings.append(drawing)
        elif cname == "point":
            points.append(_load_pcb_point(child))
        elif cname == "zone":
            zones.append(_load_zone(child))
        elif cname == "group":
            groups.append(_load_group(child))
        elif cname == "generated":
            generators.append(_load_generator(child))
        elif cname == "target":
            targets.append(_load_target(child))
        # §8 header wrapper + §2 header blocks.
        elif cname == "version":
            kwargs["version"] = get_int(children(child)[0])
        elif cname == "generator":
            kwargs["generator"] = get_str(children(child)[0])
        elif cname == "generator_version":
            kwargs["generator_version"] = get_str(children(child)[0])
        elif cname == "general":
            kwargs["general"] = _load_general(child)
        elif cname == "paper":
            kwargs["paper"] = _load_paper(child)
        elif cname == "title_block":
            kwargs["title_block"] = _load_title_block(child)
        elif cname == "layers":
            kwargs["layers"] = _load_layers(child)
        elif cname == "setup":
            kwargs["setup"] = _load_setup(child)
        elif cname == "property":
            properties.append(_load_board_property(child))
        elif cname == "variants":
            kwargs["variants"] = _load_variants(child)
        elif cname == "embedded_fonts":
            kwargs["embedded_fonts"] = bool_token(child)
            seen_embedded_fonts = True
        elif cname == "embedded_files":
            embedded_files = load_embedded_files(child)
        elif cname in ("net", "net_class", "host"):
            # A2 legacy tolerance: accept-and-drop the legacy root net table
            # `(net <code> <name>)`, `(net_class …)`, and `(host …)` tags. The v10
            # writer never emits them; nets are synthesized by name from item refs.
            continue
        else:
            raise KicadParseError(f"Unknown token in kicad_pcb: {cname!r}")

    kwargs["properties"] = properties
    kwargs["tracks"] = tracks
    kwargs["footprints"] = footprints
    kwargs["drawings"] = drawings
    kwargs["points"] = points
    kwargs["zones"] = zones
    kwargs["groups"] = groups
    kwargs["generators"] = generators
    kwargs["targets"] = targets
    kwargs["trailing_drawings"] = trailing_drawings
    kwargs["embedded_files"] = embedded_files
    _fixup_embedded_data(footprints, embedded_files)
    return Board(**kwargs)  # type: ignore[arg-type]


def _fixup_embedded_data(footprints: list[Footprint], root_files: list[EmbeddedFile]) -> None:
    """Hydrate footprint embedded-file stubs from the board root by filename.

    KiCad analog: ``BOARD::FixupEmbeddedData`` (``board.cpp:1206``, invoked at
    end of board parse). The board root owns all embedded bytes (consolidated
    union); each footprint carries only a name/type/checksum stub (no data). This
    copies the base64 ``data`` from the matching root entry into each empty
    footprint stub so an extracted footprint (T10) materialises real bytes.

    Byte-identity is preserved: the footprint re-emits checksum-only in BOARD
    context (T7, ``write_data=False``), so the hydrated data is never written
    back at footprint scope, and the stub's checksum text is left exactly as read
    (it already matches the root entry for the same file).
    """
    if not root_files:
        return
    by_name = {f.name: f for f in root_files if f.data}
    for fp in footprints:
        for i, stub in enumerate(fp.embedded_files):
            if not stub.data and stub.name in by_name:
                fp.embedded_files[i] = stub.model_copy(update={"data": by_name[stub.name].data})
