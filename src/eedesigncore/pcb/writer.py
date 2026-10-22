"""Board writer: ``Board`` → flat S-expression string → Prettify.

Token order follows ``pcb_io_kicad_sexpr.cpp`` ``format(const BOARD*)`` (§1),
``formatHeader`` (§2) and ``FormatBoardToFormatter`` (§8) exactly, per the
board-root-grammar and A1 layer-table reports. Boards use NORMAL prettify mode
(FormatBoardToFormatter uses PRETTIFIED_FILE_OUTPUTFORMATTER with no mode
argument = FORMAT_MODE::NORMAL, W:337).

T3 emits the full root skeleton for the header blocks (T2 models) plus the
always-emitted ``(embedded_fonts …)`` tail. Item streams
(footprints/drawings/points/tracks/zones/groups/generators/targets) and the
embedded-files block are not yet modeled; they emit nothing while empty and
raise ``NotImplementedError`` if populated (their writers land in T4/T7/T8/T9),
so data is never silently dropped.

Sibling order is canonical too. ``format(const BOARD*)`` builds seven sorted
``std::set`` containers before it emits anything (``:803-816``), so this writer
applies the same comparators through ``pcb/ordering.py``. Sorting is always on:
sorted input is a fixed point of the sort, so a KiCad-written board still
round-trips byte-identically (ADR-057).
"""

from __future__ import annotations

from eedesigncore.common.context import SaveContext
from eedesigncore.common.models import PtsArc
from eedesigncore.common.pcb_family import (
    Barcode,
    Dimension,
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbDrawItem,
    PcbLine,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
    ReferenceImage,
    Table,
    ZoneLayerProperty,
)
from eedesigncore.common.pcb_family.ordering import sort_pcb_points
from eedesigncore.common.writers import dump_bool as _bool
from eedesigncore.common.writers import dump_paper as _dump_paper
from eedesigncore.common.writers import dump_title_block as _dump_title_block
from eedesigncore.common.writers import fmt_double as _fmt_double
from eedesigncore.common.writers import fmt_iu as _fmt
from eedesigncore.common.writers import q as _q
from eedesigncore.common.writers import title_block_has_content as _title_block_has_content
from eedesigncore.mod.writer import (
    _dump_backdrill,
    _dump_barcode,
    _dump_dimension,
    _dump_embedded_files,
    _dump_fp_arc,
    _dump_fp_circle,
    _dump_fp_curve,
    _dump_fp_line,
    _dump_fp_poly,
    _dump_fp_rect,
    _dump_fp_text,
    _dump_fp_text_box,
    _dump_group,
    _dump_image,
    _dump_pcb_point,
    _dump_post_machining,
    _dump_table,
    _dump_teardrop,
    _dump_zone,
    dump_mod,
)
from eedesigncore.pcb.models import (
    Board,
    BoardGeneral,
    BoardLayer,
    BoardStackup,
    BoardStackupLayer,
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
)
from eedesigncore.pcb.ordering import (
    board_member_layer_sets,
    derive_net_codes,
    sort_board_drawings,
    sort_board_footprints,
    sort_board_generators,
    sort_board_groups,
    sort_board_targets,
    sort_board_tracks,
    sort_board_zones,
)
from eedesigncore.sexpr.writer import prettify


def _dump_general(g: BoardGeneral) -> str:
    parts = ["(general"]
    parts.append(f"(thickness {_fmt(g.thickness)})")
    parts.append(f"(legacy_teardrops {_bool(g.legacy_teardrops)})")
    parts.append(")")
    return " ".join(parts)


def _dump_layer_row(row: BoardLayer) -> str:
    # `(<ordinal> "<name>" <type> [<user_name>])`; type token is UNQUOTED (A1 §3).
    base = f"({row.ordinal} {_q(row.canonical_name)} {row.layer_type}"
    if row.user_name is not None:
        base += f" {_q(row.user_name)}"
    return base + ")"


def _dump_layers(layers: list[BoardLayer]) -> str:
    parts = ["(layers"]
    for row in layers:
        parts.append(_dump_layer_row(row))
    parts.append(")")
    return " ".join(parts)


def _dump_stackup_layer(layer: BoardStackupLayer) -> str:
    parts = [f"(layer {_q(layer.name)}"]
    parts.append(f"(type {_q(layer.layer_type)})")
    for idx, sub in enumerate(layer.sublayers):
        if idx > 0:
            parts.append("addsublayer")
        if sub.color is not None:
            parts.append(f"(color {_q(sub.color)})")
        if sub.thickness is not None:
            locked = " locked" if sub.thickness_locked else ""
            parts.append(f"(thickness {_fmt(sub.thickness)}{locked})")
        if sub.material is not None:
            parts.append(f"(material {_q(sub.material)})")
        if sub.epsilon_r is not None:
            parts.append(f"(epsilon_r {_fmt_double(sub.epsilon_r)})")
        if sub.loss_tangent is not None:
            parts.append(f"(loss_tangent {_fmt_double(sub.loss_tangent)})")
    parts.append(")")
    return " ".join(parts)


def _dump_stackup(st: BoardStackup) -> str:
    parts = ["(stackup"]
    for layer in st.layers:
        parts.append(_dump_stackup_layer(layer))
    if st.copper_finish is not None:
        parts.append(f"(copper_finish {_q(st.copper_finish)})")
    parts.append(f"(dielectric_constraints {_bool(st.dielectric_constraints)})")
    if st.edge_connector is not None:
        parts.append(f"(edge_connector {st.edge_connector})")
    if st.edge_plating:
        parts.append("(edge_plating yes)")
    parts.append(")")
    return " ".join(parts)


def _dump_front_back(token: str, fb: FrontBack) -> str:
    return f"({token} (front {_bool(fb.front)}) (back {_bool(fb.back)}))"


def _dump_zone_layer_property(zlp: ZoneLayerProperty) -> str:
    parts = ["(property"]
    parts.append(f"(layer {_q(zlp.layer)})")
    if zlp.hatch_position is not None:
        x, y = zlp.hatch_position
        parts.append(f"(hatch_position (xy {_fmt(x)} {_fmt(y)}))")
    parts.append(")")
    return " ".join(parts)


def _dump_plot_params(pp: PlotParams) -> str:
    parts = ["(pcbplotparams"]
    parts.append(f"(layerselection {pp.layerselection})")
    parts.append(f"(plot_on_all_layers_selection {pp.plot_on_all_layers_selection})")
    parts.append(f"(disableapertmacros {_bool(pp.disableapertmacros)})")
    parts.append(f"(usegerberextensions {_bool(pp.usegerberextensions)})")
    parts.append(f"(usegerberattributes {_bool(pp.usegerberattributes)})")
    parts.append(f"(usegerberadvancedattributes {_bool(pp.usegerberadvancedattributes)})")
    parts.append(f"(creategerberjobfile {_bool(pp.creategerberjobfile)})")
    if pp.gerberprecision is not None:
        parts.append(f"(gerberprecision {pp.gerberprecision})")
    parts.append(f"(dashed_line_dash_ratio {_fmt_double(pp.dashed_line_dash_ratio)})")
    parts.append(f"(dashed_line_gap_ratio {_fmt_double(pp.dashed_line_gap_ratio)})")
    parts.append(f"(svgprecision {pp.svgprecision})")
    parts.append(f"(plotframeref {_bool(pp.plotframeref)})")
    parts.append(f"(mode {pp.mode})")
    parts.append(f"(useauxorigin {_bool(pp.useauxorigin)})")
    parts.append(f"(pdf_front_fp_property_popups {_bool(pp.pdf_front_fp_property_popups)})")
    parts.append(f"(pdf_back_fp_property_popups {_bool(pp.pdf_back_fp_property_popups)})")
    parts.append(f"(pdf_metadata {_bool(pp.pdf_metadata)})")
    parts.append(f"(pdf_single_document {_bool(pp.pdf_single_document)})")
    parts.append(f"(dxfpolygonmode {_bool(pp.dxfpolygonmode)})")
    parts.append(f"(dxfimperialunits {_bool(pp.dxfimperialunits)})")
    parts.append(f"(dxfusepcbnewfont {_bool(pp.dxfusepcbnewfont)})")
    parts.append(f"(psnegative {_bool(pp.psnegative)})")
    parts.append(f"(psa4output {_bool(pp.psa4output)})")
    parts.append(f"(plot_black_and_white {_bool(pp.plot_black_and_white)})")
    parts.append(f"(sketchpadsonfab {_bool(pp.sketchpadsonfab)})")
    parts.append(f"(plotpadnumbers {_bool(pp.plotpadnumbers)})")
    parts.append(f"(hidednponfab {_bool(pp.hidednponfab)})")
    parts.append(f"(sketchdnponfab {_bool(pp.sketchdnponfab)})")
    parts.append(f"(crossoutdnponfab {_bool(pp.crossoutdnponfab)})")
    parts.append(f"(subtractmaskfromsilk {_bool(pp.subtractmaskfromsilk)})")
    parts.append(f"(outputformat {pp.outputformat})")
    parts.append(f"(mirror {_bool(pp.mirror)})")
    parts.append(f"(drillshape {pp.drillshape})")
    parts.append(f"(scaleselection {pp.scaleselection})")
    parts.append(f"(outputdirectory {_q(pp.outputdirectory)})")
    parts.append(")")
    return " ".join(parts)


def _dump_setup(s: Setup) -> str:
    parts = ["(setup"]
    if s.stackup is not None:
        parts.append(_dump_stackup(s.stackup))
    parts.append(f"(pad_to_mask_clearance {_fmt(s.pad_to_mask_clearance)})")
    if s.solder_mask_min_width is not None:
        parts.append(f"(solder_mask_min_width {_fmt(s.solder_mask_min_width)})")
    if s.pad_to_paste_clearance is not None:
        parts.append(f"(pad_to_paste_clearance {_fmt(s.pad_to_paste_clearance)})")
    if s.pad_to_paste_clearance_ratio is not None:
        parts.append(
            f"(pad_to_paste_clearance_ratio {_fmt_double(s.pad_to_paste_clearance_ratio)})"
        )
    bridges = _bool(s.allow_soldermask_bridges_in_footprints)
    parts.append(f"(allow_soldermask_bridges_in_footprints {bridges})")
    parts.append(_dump_front_back("tenting", s.tenting))
    parts.append(_dump_front_back("covering", s.covering))
    parts.append(_dump_front_back("plugging", s.plugging))
    parts.append(f"(capping {_bool(s.capping)})")
    parts.append(f"(filling {_bool(s.filling)})")
    # zone_defaults is emitted whenever the token was present, even when empty:
    # KiCad emits `(zone_defaults ...)` when m_ZoneLayerProperties is non-empty
    # but each per-layer format() is a no-op unless hatching_offset is set
    # (pcb_io_kicad_sexpr.cpp:606-614, 3097-3099), yielding a bare
    # `(zone_defaults)`. None = token absent; [] = present-but-empty.
    if s.zone_defaults is not None:
        if s.zone_defaults:
            zd_parts = ["(zone_defaults"]
            for zlp in s.zone_defaults:
                zd_parts.append(_dump_zone_layer_property(zlp))
            zd_parts.append(")")
            parts.append(" ".join(zd_parts))
        else:
            parts.append("(zone_defaults)")
    if s.aux_axis_origin is not None:
        ax, ay = s.aux_axis_origin
        parts.append(f"(aux_axis_origin {_fmt(ax)} {_fmt(ay)})")
    if s.grid_origin is not None:
        gx, gy = s.grid_origin
        parts.append(f"(grid_origin {_fmt(gx)} {_fmt(gy)})")
    parts.append(_dump_plot_params(s.pcbplotparams))
    parts.append(")")
    return " ".join(parts)


def _dump_variants(variants: list[BoardVariant]) -> str:
    parts = ["(variants"]
    for v in variants:
        v_parts = [f"(variant (name {_q(v.name)})"]
        if v.description:
            v_parts.append(f"(description {_q(v.description)})")
        v_parts.append(")")
        parts.append(" ".join(v_parts))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tracks / arcs / vias (§4)
# ---------------------------------------------------------------------------


def _dump_track_layer(layer: str, layers: list[str] | None) -> str:
    """Emit ``(layers …)`` for a multi-layer track, else ``(layer …)`` (W:2840-2843)."""
    if layers is not None:
        return f"(layers {' '.join(_q(lyr) for lyr in layers)})"
    return f"(layer {_q(layer)})"


def _dump_segment(item: TrackSegment) -> str:
    parts = [
        f"(segment (start {_fmt(item.start[0])} {_fmt(item.start[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
        f" (width {_fmt(item.width)})"
    ]
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_track_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net is not None:
        parts.append(f"(net {_q(item.net)})")
    if item.uuid is not None:
        parts.append(f"(uuid {_q(item.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_arc(item: TrackArc) -> str:
    parts = [
        f"(arc (start {_fmt(item.start[0])} {_fmt(item.start[1])})"
        f" (mid {_fmt(item.mid[0])} {_fmt(item.mid[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
        f" (width {_fmt(item.width)})"
    ]
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_track_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net is not None:
        parts.append(f"(net {_q(item.net)})")
    if item.uuid is not None:
        parts.append(f"(uuid {_q(item.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _opt_bool(value: bool | None) -> str:
    """Render a tri-state opt-bool as ``yes``/``no``/``none`` (FormatOptBool)."""
    if value is None:
        return "none"
    return "yes" if value else "no"


def _dump_via_front_back(token: str, fb: ViaFrontBackOpt) -> str:
    return f"({token} (front {_opt_bool(fb.front)}) (back {_opt_bool(fb.back)}))"


def _dump_via_padstack(ps: ViaPadstack) -> str:
    parts = ["(padstack", f"(mode {ps.mode})"]
    for lyr in ps.layers:
        parts.append(f"(layer {_q(lyr.layer_name)} (size {_fmt(lyr.size)}))")
    parts.append(")")
    return " ".join(parts)


def _dump_via(item: Via) -> str:
    parts = ["(via"]
    if item.via_type != "through":
        parts.append(item.via_type)
    parts.append(f"(at {_fmt(item.at[0])} {_fmt(item.at[1])})")
    parts.append(f"(size {_fmt(item.size)})")
    parts.append(f"(drill {_fmt(item.drill)})")
    if item.backdrill is not None:
        parts.append(_dump_backdrill(item.backdrill, "backdrill"))
    if item.tertiary_drill is not None:
        parts.append(_dump_backdrill(item.tertiary_drill, "tertiary_drill"))
    if item.front_post_machining is not None:
        parts.append(_dump_post_machining(item.front_post_machining, "front_post_machining"))
    if item.back_post_machining is not None:
        parts.append(_dump_post_machining(item.back_post_machining, "back_post_machining"))
    parts.append(f"(layers {_q(item.layers[0])} {_q(item.layers[1])})")
    if item.unconnected_layer_mode == "remove_all":
        parts.append("(remove_unused_layers yes)")
        parts.append("(keep_end_layers no)")
    elif item.unconnected_layer_mode == "remove_except_start_end":
        parts.append("(remove_unused_layers yes)")
        parts.append("(keep_end_layers yes)")
    elif item.unconnected_layer_mode == "start_end_only":
        parts.append("(start_end_only yes)")
    if item.locked:
        parts.append("(locked yes)")
    if item.free:
        parts.append("(free yes)")
    if item.zone_layer_connections is not None:
        zlc = " ".join(_q(lyr) for lyr in item.zone_layer_connections)
        parts.append(f"(zone_layer_connections {zlc})" if zlc else "(zone_layer_connections)")
    if item.tenting is not None:
        parts.append(_dump_via_front_back("tenting", item.tenting))
    if item.capping is not None:
        parts.append(f"(capping {_opt_bool(item.capping)})")
    if item.covering is not None:
        parts.append(_dump_via_front_back("covering", item.covering))
    if item.plugging is not None:
        parts.append(_dump_via_front_back("plugging", item.plugging))
    if item.filling is not None:
        parts.append(f"(filling {_opt_bool(item.filling)})")
    if item.padstack is not None:
        parts.append(_dump_via_padstack(item.padstack))
    if item.teardrops is not None:
        parts.append(_dump_teardrop(item.teardrops))
    if item.net is not None:
        parts.append(f"(net {_q(item.net)})")
    if item.uuid is not None:
        parts.append(f"(uuid {_q(item.uuid)})")
    parts.append(")")
    return " ".join(parts)


def _dump_track_item(item: TrackItem) -> str:
    if isinstance(item, Via):
        return _dump_via(item)
    if isinstance(item, TrackArc):
        return _dump_arc(item)
    return _dump_segment(item)


def _dump_board_drawing(item: PcbDrawItem) -> str:
    """Emit a board-scope drawing item (§1 item 3).

    Board shapes/text reuse the shared ``pcb_family`` writers with the ``gr``
    prefix (ADR-020: prefix is a writer argument, not a model field). The same
    ``PcbLine`` model emits ``fp_line`` via the mod writer and ``gr_line`` here.
    Dimension/table/image/barcode are prefix-independent tokens (identical in
    both scopes) and are reused verbatim.
    """
    if isinstance(item, PcbText):
        return _dump_fp_text(item, "gr")
    if isinstance(item, PcbLine):
        return _dump_fp_line(item, "gr")
    if isinstance(item, PcbRect):
        return _dump_fp_rect(item, "gr")
    if isinstance(item, PcbCircle):
        return _dump_fp_circle(item, "gr")
    if isinstance(item, PcbArc):
        return _dump_fp_arc(item, "gr")
    if isinstance(item, PcbPoly):
        return _dump_fp_poly(item, "gr")
    if isinstance(item, PcbCurve):
        return _dump_fp_curve(item, "gr")
    if isinstance(item, PcbTextBox):
        return _dump_fp_text_box(item, "gr")
    if isinstance(item, Dimension):
        return _dump_dimension(item)
    if isinstance(item, Table):
        return _dump_table(item)
    if isinstance(item, ReferenceImage):
        return _dump_image(item)
    if isinstance(item, Barcode):
        return _dump_barcode(item)
    raise NotImplementedError(f"unhandled board drawing item: {type(item).__name__}")


def _dump_generator_value(key: str, val: GeneratorValue) -> str:
    """Emit one property-bag entry per its A3 type branch.

    Numbers use pure ``{:.10g}`` (unquoted, no small-value branch -- KiCad's
    ``fmt::format("{:.10g}", val)``, W:2535-2541); point/pts use ``fmt_iu``
    (formatInternalUnits, mm); bool via yes/no; string quoted.
    """
    if isinstance(val, GenNumber):
        return f"({key} {val.value:.10g})"
    if isinstance(val, GenBool):
        return f"({key} {_bool(val.value)})"
    if isinstance(val, GenPoint):
        return f"({key} (xy {_fmt(val.value[0])} {_fmt(val.value[1])}))"
    if isinstance(val, GenPts):
        elems = []
        for elem in val.value:
            if isinstance(elem, PtsArc):
                elems.append(
                    f"(arc (start {_fmt(elem.start[0])} {_fmt(elem.start[1])})"
                    f" (mid {_fmt(elem.mid[0])} {_fmt(elem.mid[1])})"
                    f" (end {_fmt(elem.end[0])} {_fmt(elem.end[1])}))"
                )
            else:
                elems.append(f"(xy {_fmt(elem[0])} {_fmt(elem[1])})")
        return f"({key} (pts {' '.join(elems)}))"
    if isinstance(val, GenString):
        return f"({key} {_q(val.value)})"
    raise NotImplementedError(f"unhandled generator value: {type(val).__name__}")


def _dump_generator(gen: PcbGenerator) -> str | None:
    """Emit a ``(generated …)`` block (§5 / A3 report), or ``None`` for a ghost.

    A ``tuning_pattern`` generator with no members is a ghost -- KiCad returns
    without emitting (W:2510-2514); we mirror by returning ``None``. Properties
    are emitted sorted-by-key (std::map order, A3 §2); members sorted ascending
    by uuid string (A3 §4). Type is a bare symbol; name/layer quoted.
    """
    if gen.generator_type == "tuning_pattern" and not gen.members:
        return None

    parts = ["(generated"]
    if gen.uuid is not None:
        parts.append(f"(uuid {_q(gen.uuid)})")
    parts.append(f"(type {gen.generator_type})")
    parts.append(f"(name {_q(gen.name)})")
    parts.append(f"(layer {_q(gen.layer)})")
    if gen.locked:
        parts.append("(locked yes)")
    for key in sorted(gen.properties):
        parts.append(_dump_generator_value(key, gen.properties[key]))
    members = " ".join(_q(m) for m in sorted(gen.members))
    parts.append(f"(members {members})" if members else "(members)")
    parts.append(")")
    return " ".join(parts)


def _dump_target(tgt: PcbTarget) -> str:
    """Emit a ``(target …)`` marker (§6 / A3 §7)."""
    shape = "x" if tgt.is_x else "plus"
    parts = [
        f"(target {shape}"
        f" (at {_fmt(tgt.position[0])} {_fmt(tgt.position[1])})"
        f" (size {_fmt(tgt.size)})"
    ]
    if tgt.width != 0:
        parts.append(f"(width {_fmt(tgt.width)})")
    parts.append(f"(layer {_q(tgt.layer)})")
    if tgt.uuid is not None:
        parts.append(f"(uuid {_q(tgt.uuid)})")
    parts.append(")")
    return " ".join(parts)


def dump_board(board: Board) -> str:
    """Emit a flat (unindented) S-expression string for *board* (§1/§2/§8 order)."""
    # §8 file wrapper
    parts = ["(kicad_pcb"]
    parts.append(f"(version {board.version})")
    parts.append(f"(generator {_q(board.generator)})")
    if board.generator_version is not None:
        parts.append(f"(generator_version {_q(board.generator_version)})")

    # §2 formatHeader: general -> paper -> title_block -> layers -> setup ->
    # properties -> variants.
    if board.general is not None:
        parts.append(_dump_general(board.general))
    if board.paper is not None:
        parts.append(_dump_paper(board.paper))
    if board.title_block is not None and _title_block_has_content(board.title_block):
        parts.append(_dump_title_block(board.title_block))
    if board.layers:
        parts.append(_dump_layers(board.layers))
    if board.setup is not None:
        parts.append(_dump_setup(board.setup))
    # Board-root properties are map-sorted by key (§2d).
    for prop in sorted(board.properties, key=lambda p: p.name):
        parts.append(f"(property {_q(prop.name)} {_q(prop.value)})")
    if board.variants:
        parts.append(_dump_variants(board.variants))

    # Every item stream below is SORTED, mirroring the seven std::set containers
    # format(BOARD*) builds at :803-816. The group and generator keys are a union
    # over their members' layer sets (pcb_group.cpp:329-337), so the members
    # resolve through one index, and the track key is a net CODE that KiCad
    # assigns in parse order (netinfo_list.cpp:149-176).
    layer_sets = board_member_layer_sets(board)
    net_codes = derive_net_codes(board)

    # §1 item 2: footprints (T7), before drawings (format(BOARD*) :820-821).
    # Each footprint serializes in BOARD context (full lib:name FPID, no
    # version block, fp uuid/at, pad/zone nets, embedded-data deferred).
    for footprint in sort_board_footprints(board.footprints):
        parts.append(dump_mod(footprint, context=SaveContext.BOARD))

    # §1 item 3: drawings (shapes/text/dimension/table/image/barcode) (T6).
    for drawing in sort_board_drawings(board.drawings):
        parts.append(_dump_board_drawing(drawing))

    # §6 targets — a PCB_TARGET is a BOARD drawing (§1 item 3 dispatch). Modeled
    # as a distinct stream. PCB_TARGET_T is 24, above every other drawing type in
    # KICAD_T, so emitting the stream here IS the sorted position (sweep OQ-A).
    for target in sort_board_targets(board.targets):
        parts.append(_dump_target(target))

    # §1 item 4: points (T6).
    for point in sort_pcb_points(board.points):
        parts.append(_dump_pcb_point(point))

    # §1 item 6: tracks/arcs/vias (T4).
    for track in sort_board_tracks(board.tracks, net_codes):
        parts.append(_dump_track_item(track))

    # §1 item 7: zones (T6). A board zone goes through BOARD_ITEM::ptr_cmp, NOT
    # through FOOTPRINT::cmp_zones (format(BOARD*) :811-812).
    for zone in sort_board_zones(board.zones):
        parts.append(_dump_zone(zone))

    # §1 item 8: groups (T6).
    for group in sort_board_groups(board.groups, layer_sets):
        parts.append(_dump_group(group))

    # §1 item 9: generators (T8). Ghost tuning_pattern generators (no members)
    # emit nothing (A3 §1).
    for generator in sort_board_generators(board.generators, layer_sets):
        emitted = _dump_generator(generator)
        if emitted is not None:
            parts.append(emitted)

    # §7 root tail: (embedded_fonts …) always emitted (format(BOARD*) :865-866),
    # then the consolidated (embedded_files …) block WITH inline data
    # (aWriteData = CTL_FOR_BOARD set, :868-869). The board root physically owns
    # all embedded bytes; footprints emitted checksum-only stubs above (T7). The
    # block is re-emitted from ``board.embedded_files`` exactly as read
    # (consolidation is a read-time union preserved verbatim for byte-identity).
    parts.append(f"(embedded_fonts {_bool(board.embedded_fonts)})")
    # Drawings that appeared after (embedded_fonts …) in the source are re-emitted
    # here, in that same trailing position (ADR-034), before embedded_files.
    for drawing in sort_board_drawings(board.trailing_drawings):
        parts.append(_dump_board_drawing(drawing))
    if board.embedded_files:
        parts.append(_dump_embedded_files(board.embedded_files, write_data=True))

    parts.append(")")
    return " ".join(parts)


def write_board(board: Board) -> str:
    """Serialise *board* to a canonical ``.kicad_pcb`` string (NORMAL prettify)."""
    return prettify(dump_board(board), mode="NORMAL")


# Public aliases mirroring the mod/sym dump_*/write_* naming convention.
dump_pcb = dump_board
write_pcb = write_board
