"""Footprint writer: ``Footprint`` → flat S-expression string → Prettify.

Token ordering follows ``pcb_io_kicad_sexpr.cpp`` format(FOOTPRINT*) exactly.

Sibling order is canonical too. ``format(FOOTPRINT*)`` builds five sorted
``std::set`` containers before it emits anything (``:1423-1434``), so this writer
applies the same comparators through ``common/pcb_family/ordering.py``. Sorting is
always on: sorted input is a fixed point of the sort, so a KiCad-written file
still round-trips byte-identically (ADR-057).
"""

from __future__ import annotations

from collections.abc import Sequence

from eedesigncore.common.context import SaveContext
from eedesigncore.common.models import (
    EmbeddedFile,
    Property,
    PtsArc,
    PtsElement,
    RenderCache,
)
from eedesigncore.common.pcb_family import (
    BackdrillInfo,
    Barcode,
    Dimension,
    DimensionFormat,
    DimensionStyle,
    DimensionText,
    Padstack,
    PadstackLayer,
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
    PostMachining,
    ReferenceImage,
    Table,
    TableCell,
    Variant,
    Zone,
    ZoneFill,
    ZoneKeepout,
    ZonePlacement,
)
from eedesigncore.common.pcb_family.enums import PadType
from eedesigncore.common.pcb_family.ordering import (
    FootprintFrame,
    footprint_member_layer_sets,
    sort_footprint_drawings,
    sort_footprint_properties,
    sort_group_members,
    sort_groups,
    sort_pads,
    sort_pcb_points,
    sort_variant_fields,
    sort_variants,
    sort_zones,
)
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
    dump_fill_pcb as _dump_fill_bool,
)
from eedesigncore.common.writers import (
    dump_stroke as _dump_stroke,
)
from eedesigncore.common.writers import (
    fmt_angle as _fmt_angle,
)
from eedesigncore.common.writers import (
    fmt_double as _fmt_double,
)
from eedesigncore.common.writers import (
    fmt_iu as _fmt,
)
from eedesigncore.common.writers import (
    q as _q,
)
from eedesigncore.mod.enums import CustomAnchor, CustomShapeClearance
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    Model3D,
    Pad,
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
from eedesigncore.sexpr.writer import prettify

# ---------------------------------------------------------------------------
# render_cache
# ---------------------------------------------------------------------------


def _dump_render_cache(rc: RenderCache) -> str:
    """Emit a (render_cache "resolved_text" angle (polygon (pts (xy x y) ...)) ...) token.

    Token order from eda_text.cpp: resolved_text, angle, then polygon blocks.
    """
    parts = [f"(render_cache {_q(rc.resolved_text)} {_fmt_angle(rc.angle)}"]
    for poly in rc.polygons:
        parts.append(f"(polygon {_dump_pts(poly.pts)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Property (PCB_FIELD)
# ---------------------------------------------------------------------------


def _dump_fp_property(prop: Property) -> str:
    """Emit a ``property`` token in footprint context.

    Token ordering (from pcb_io_kicad_sexpr.cpp format(PCB_FIELD*) via
    format(PCB_TEXT*)):
      (property "name" "value" (at x y angle) (unlocked yes)? (layer "l" [knockout])
       (hide yes)? (uuid "...")? (effects ...))
    """
    angle_str = _fmt_angle(prop.angle)
    parts = [
        f"(property {_q(prop.name)} {_q(prop.value)}"
        f" (at {_fmt(prop.position[0])} {_fmt(prop.position[1])} {angle_str})"
    ]

    # unlocked (inverted IsKeepUpright)
    if prop.unlocked:
        parts.append("(unlocked yes)")

    # layer
    if prop.layer:
        if prop.knockout:
            parts.append(f"(layer {_q(prop.layer)} knockout)")
        else:
            parts.append(f"(layer {_q(prop.layer)})")

    if not prop.visible:
        parts.append("(hide yes)")

    if prop.id is not None:
        parts.append(f"(uuid {_q(prop.id)})")

    # format(PCB_TEXT*) always calls EDA_TEXT::Format for a field, which always
    # prints (effects …) (pcb_io_kicad_sexpr.cpp:2309, eda_text.cpp:1061-1119).
    parts.append(_dump_field_effects(prop.attributes))

    if prop.render_cache is not None:
        parts.append(_dump_render_cache(prop.render_cache))

    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Drawing items
# ---------------------------------------------------------------------------


def _dump_fp_text(item: PcbText, prefix: str = "fp") -> str:
    """Emit an ``fp_text`` (footprint) or ``gr_text`` (board) token.

    Token order: (<prefix>_text [type] "content" (locked yes)? (at x y angle)
                  (unlocked yes)? (layer "name" [knockout])
                  (uuid "...")? (effects ...))

    The ``prefix`` (``fp``/``gr``) is a writer concern, not a model field
    (ADR-020): the same ``PcbText`` emits ``fp_text`` in a footprint and
    ``gr_text`` at board root. Board ``gr_text`` carries NO leading type field
    and NO ``(unlocked …)`` (``pcb_io_kicad_sexpr.cpp:2277-2296``: type stays
    empty and ``unlocked`` is gated on ``parentFP``).

    Note: angle is ALWAYS written (source line 2293). A non-field text NEVER
    emits ``(hide yes)`` -- KiCad guards that token with ``field && !IsVisible()``
    (format(PCB_TEXT) :2300-2301), so only PCB_FIELD properties carry it (GAP-003).
    """
    if prefix == "fp":
        parts = [f"(fp_text {item.text_type} {_q(item.content)}"]
    else:
        parts = [f"({prefix}_text {_q(item.content)}"]

    if item.locked:
        parts.append("(locked yes)")

    # angle is ALWAYS written
    angle_str = _fmt_angle(item.angle)
    parts.append(f"(at {_fmt(item.position[0])} {_fmt(item.position[1])} {angle_str})")

    if item.unlocked and prefix == "fp":
        parts.append("(unlocked yes)")

    if item.knockout:
        parts.append(f"(layer {_q(item.layer)} knockout)")
    else:
        parts.append(f"(layer {_q(item.layer)})")

    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")

    if item.attributes is not None:
        parts.append(_dump_effects(item.attributes))

    if item.render_cache is not None:
        parts.append(_dump_render_cache(item.render_cache))

    parts.append(")")
    return " ".join(parts)


def _dump_shape_layer(layer: str, layers: list[str] | None) -> str:
    """Emit (layers ...) for a multi-layer shape, else (layer ...).

    Mirrors pcb_io_kicad_sexpr.cpp:1094-1097 (count()>1 ? formatLayers :
    formatLayer). ``layers`` holds the layer-name tokens exactly as parsed.
    """
    if layers is not None:
        return f"(layers {' '.join(_q(lyr) for lyr in layers)})"
    return f"(layer {_q(layer)})"


def _dump_fp_line(item: PcbLine, prefix: str = "fp") -> str:
    parts = [
        f"({prefix}_line"
        f" (start {_fmt(item.start[0])} {_fmt(item.start[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
    ]
    parts.append(_dump_stroke(item.stroke))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_fp_rect(item: PcbRect, prefix: str = "fp") -> str:
    """Emit an ``fp_rect``/``gr_rect`` token.

    The ``(radius …)`` token appears only for a POSITIVE corner radius, matching
    ``if( aShape->GetCornerRadius() > 0 )`` at pcb_io_kicad_sexpr.cpp:1011
    (GAP-023). A model that sets ``corner_radius=0.0`` emits no radius.
    """
    parts = [
        f"({prefix}_rect"
        f" (start {_fmt(item.start[0])} {_fmt(item.start[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
    ]
    if item.corner_radius is not None and item.corner_radius > 0:
        parts.append(f"(radius {_fmt(item.corner_radius)})")
    parts.append(_dump_stroke(item.stroke))
    # fill is ALWAYS written
    parts.append(_dump_fill_bool(item.fill))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_fp_circle(item: PcbCircle, prefix: str = "fp") -> str:
    parts = [
        f"({prefix}_circle"
        f" (center {_fmt(item.center[0])} {_fmt(item.center[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
    ]
    parts.append(_dump_stroke(item.stroke))
    # fill is ALWAYS written
    parts.append(_dump_fill_bool(item.fill))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_fp_arc(item: PcbArc, prefix: str = "fp") -> str:
    parts = [
        f"({prefix}_arc"
        f" (start {_fmt(item.start[0])} {_fmt(item.start[1])})"
        f" (mid {_fmt(item.mid[0])} {_fmt(item.mid[1])})"
        f" (end {_fmt(item.end[0])} {_fmt(item.end[1])})"
    ]
    parts.append(_dump_stroke(item.stroke))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_pts(pts: list[PtsElement]) -> str:
    parts = []
    for elem in pts:
        if isinstance(elem, PtsArc):
            parts.append(
                f"(arc (start {_fmt(elem.start[0])} {_fmt(elem.start[1])})"
                f" (mid {_fmt(elem.mid[0])} {_fmt(elem.mid[1])})"
                f" (end {_fmt(elem.end[0])} {_fmt(elem.end[1])}))"
            )
        else:
            parts.append(f"(xy {_fmt(elem[0])} {_fmt(elem[1])})")
    return f"(pts {' '.join(parts)})"


def _dump_fp_poly(item: PcbPoly, prefix: str = "fp") -> str:
    parts = [f"({prefix}_poly {_dump_pts(item.pts)}"]
    parts.append(_dump_stroke(item.stroke))
    # fill is ALWAYS written
    parts.append(_dump_fill_bool(item.fill))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_fp_curve(item: PcbCurve, prefix: str = "fp") -> str:
    parts = [f"({prefix}_curve {_dump_pts(item.pts)}"]
    parts.append(_dump_stroke(item.stroke))
    if item.locked:
        parts.append("(locked yes)")
    parts.append(_dump_shape_layer(item.layer, item.layers))
    if item.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(item.solder_mask_margin)})")
    if item.net_name is not None:
        parts.append(f"(net {_q(item.net_name)})")
    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")
    parts.append(")")
    return " ".join(parts)


def _dump_fp_text_box(item: PcbTextBox, prefix: str = "fp") -> str:
    parts = [f"({prefix}_text_box {_q(item.content)}"]

    if item.locked:
        parts.append("(locked yes)")

    if item.start is not None and item.end is not None:
        parts.append(f"(start {_fmt(item.start[0])} {_fmt(item.start[1])})")
        parts.append(f"(end {_fmt(item.end[0])} {_fmt(item.end[1])})")
    elif item.pts:
        parts.append(_dump_pts(item.pts))

    m = item.margins
    parts.append(f"(margins {_fmt(m[0])} {_fmt(m[1])} {_fmt(m[2])} {_fmt(m[3])})")

    if item.angle is not None:
        parts.append(f"(angle {_fmt_angle(item.angle)})")

    parts.append(f"(layer {_q(item.layer)})")

    if item.id is not None:
        parts.append(f"(uuid {_q(item.id)})")

    if item.attributes is not None:
        parts.append(_dump_effects(item.attributes))

    parts.append(f"(border {_bool(item.border_enabled)})")
    parts.append(_dump_stroke(item.stroke))
    parts.append(f"(knockout {_bool(item.knockout)})")

    if item.render_cache is not None:
        parts.append(_dump_render_cache(item.render_cache))

    parts.append(")")
    return " ".join(parts)


def _dump_drawing(item: object) -> str:
    if isinstance(item, PcbText):
        return _dump_fp_text(item)
    elif isinstance(item, PcbLine):
        return _dump_fp_line(item)
    elif isinstance(item, PcbRect):
        return _dump_fp_rect(item)
    elif isinstance(item, PcbCircle):
        return _dump_fp_circle(item)
    elif isinstance(item, PcbArc):
        return _dump_fp_arc(item)
    elif isinstance(item, PcbPoly):
        return _dump_fp_poly(item)
    elif isinstance(item, PcbCurve):
        return _dump_fp_curve(item)
    elif isinstance(item, PcbTextBox):
        return _dump_fp_text_box(item)
    # GAP-001: dimension/table/image/barcode share the ordered drawings stream
    elif isinstance(item, Dimension):
        return _dump_dimension(item)
    elif isinstance(item, Table):
        return _dump_table(item)
    elif isinstance(item, ReferenceImage):
        return _dump_image(item)
    elif isinstance(item, Barcode):
        return _dump_barcode(item)
    return ""


# ---------------------------------------------------------------------------
# Pad primitives
# ---------------------------------------------------------------------------


def _dump_primitives(primitives: Sequence[object]) -> str:
    parts = ["(primitives"]
    for prim in primitives:
        if isinstance(prim, PrimLine):
            s = (
                f"(gr_line"
                f" (start {_fmt(prim.start[0])} {_fmt(prim.start[1])})"
                f" (end {_fmt(prim.end[0])} {_fmt(prim.end[1])})"
                f" (width {_fmt(prim.width)})"
                f")"
            )
            parts.append(s)
        elif isinstance(prim, PrimRect):
            sx = _fmt(prim.start[0])
            sy = _fmt(prim.start[1])
            ex = _fmt(prim.end[0])
            ey = _fmt(prim.end[1])
            s = f"(gr_rect (start {sx} {sy}) (end {ex} {ey})"
            # Radius only when POSITIVE, per format(PAD*) :1979-1983 (GAP-023).
            if prim.corner_radius is not None and prim.corner_radius > 0:
                s += f" (radius {_fmt(prim.corner_radius)})"
            s += f" (width {_fmt(prim.width)}) (fill {_bool(prim.fill)})"
            s += ")"
            parts.append(s)
        elif isinstance(prim, PrimArc):
            s = (
                f"(gr_arc"
                f" (start {_fmt(prim.start[0])} {_fmt(prim.start[1])})"
                f" (mid {_fmt(prim.mid[0])} {_fmt(prim.mid[1])})"
                f" (end {_fmt(prim.end[0])} {_fmt(prim.end[1])})"
                f" (width {_fmt(prim.width)})"
                f")"
            )
            parts.append(s)
        elif isinstance(prim, PrimCircle):
            s = (
                f"(gr_circle"
                f" (center {_fmt(prim.center[0])} {_fmt(prim.center[1])})"
                f" (end {_fmt(prim.end[0])} {_fmt(prim.end[1])})"
                f" (width {_fmt(prim.width)})"
                f" (fill {_bool(prim.fill)})"
                f")"
            )
            parts.append(s)
        elif isinstance(prim, PrimCurve):
            s = f"(gr_curve {_dump_pts(prim.pts)} (width {_fmt(prim.width)}))"
            parts.append(s)
        elif isinstance(prim, PrimPoly):
            s = (
                f"(gr_poly {_dump_pts(prim.pts)}"
                f" (width {_fmt(prim.width)}) (fill {_bool(prim.fill)}))"
            )
            parts.append(s)
        elif isinstance(prim, PrimVector):
            # Proxy SEGMENT (GAP-010): no width, no fill (format(PAD*) :1954).
            s = (
                f"(gr_vector"
                f" (start {_fmt(prim.start[0])} {_fmt(prim.start[1])})"
                f" (end {_fmt(prim.end[0])} {_fmt(prim.end[1])})"
                f")"
            )
            parts.append(s)
        elif isinstance(prim, PrimBbox):
            # Proxy RECTANGLE (GAP-010): no width, fill emitted (:1969, :2028).
            s = (
                f"(gr_bbox"
                f" (start {_fmt(prim.start[0])} {_fmt(prim.start[1])})"
                f" (end {_fmt(prim.end[0])} {_fmt(prim.end[1])})"
                f" (fill {_bool(prim.fill)})"
                f")"
            )
            parts.append(s)
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


def _dump_pad(pad: Pad, *, context: SaveContext = SaveContext.LIBRARY) -> str:
    """Emit a ``pad`` token.

    Token ordering follows pcb_io_kicad_sexpr.cpp format(PAD*) exactly. In board
    context (``CTL`` bit clear for ``CTL_OMIT_PAD_NETS``) the net/pinfunction/
    pintype tokens are emitted after the corner-radius block (:1847-1859);
    library context omits them.
    """
    # Head: (pad "number" type shape
    parts = [f"(pad {_q(pad.number)} {pad.pad_type.value} {pad.shape.value}"]

    # (at x y [angle])
    angle_str = _fmt_angle(pad.angle)
    if pad.angle != 0.0:
        parts.append(f"(at {_fmt(pad.position[0])} {_fmt(pad.position[1])} {angle_str})")
    else:
        parts.append(f"(at {_fmt(pad.position[0])} {_fmt(pad.position[1])})")

    # (size w h)
    parts.append(f"(size {_fmt(pad.size[0])} {_fmt(pad.size[1])})")

    # (rect_delta dx dy)
    if pad.rect_delta is not None:
        parts.append(f"(rect_delta {_fmt(pad.rect_delta[0])} {_fmt(pad.rect_delta[1])})")

    # (drill ...)
    if pad.drill is not None:
        parts.append(_dump_drill(pad.drill))

    # (backdrill ...) — §2.4 #5
    if pad.backdrill is not None:
        parts.append(_dump_backdrill(pad.backdrill, "backdrill"))

    # (tertiary_drill ...) — §2.4 #6
    if pad.tertiary_drill is not None:
        parts.append(_dump_backdrill(pad.tertiary_drill, "tertiary_drill"))

    # (front_post_machining ...) / (back_post_machining ...) — §2.4 #7-8.
    # format(PAD*) (writer 1774-1775) emits these for any pad when set, in both
    # board and library context; emission order is after the drills.
    if pad.front_post_machining is not None:
        parts.append(_dump_post_machining(pad.front_post_machining, "front_post_machining"))
    if pad.back_post_machining is not None:
        parts.append(_dump_post_machining(pad.back_post_machining, "back_post_machining"))

    # (property pad_prop_...)
    if pad.pad_property is not None:
        parts.append(f"(property {pad.pad_property.value})")

    # (layers "..." ...)
    if pad.layers:
        layers_str = " ".join(_q(lyr) for lyr in pad.layers)
        parts.append(f"(layers {layers_str})")

    # (remove_unused_layers yes|no) — written for every PTH pad and no other
    # (format(PAD*) :1793-1795). A pad read from a file older than the token
    # holds None, which is KiCad's default, false.
    if pad.pad_type is PadType.thru_hole:
        parts.append(f"(remove_unused_layers {_bool(bool(pad.remove_unused_layers))})")

    # (keep_end_layers yes|no) — only when remove_unused_layers is True
    if pad.keep_end_layers is not None and pad.remove_unused_layers:
        parts.append(f"(keep_end_layers {_bool(pad.keep_end_layers)})")

    # (zone_layer_connections "L1" "L2" …) — board context only, PTH pad with
    # remove_unused_layers set. KiCad's format(PAD*) :1791-1802 emits this only
    # when board != nullptr (BOARD save context), inside the remove_unused block,
    # after keep_end_layers. The list holds the layers force-flashed
    # (ZLO_FORCE_FLASHED); it is commonly empty -> `(zone_layer_connections)`.
    if context is SaveContext.BOARD and pad.remove_unused_layers:
        zlc = " ".join(_q(lyr) for lyr in (pad.zone_layer_connections or []))
        parts.append(f"(zone_layer_connections {zlc})" if zlc else "(zone_layer_connections)")

    # (roundrect_rratio r)
    if pad.roundrect_rratio is not None:
        parts.append(f"(roundrect_rratio {_fmt_double(pad.roundrect_rratio)})")

    # (chamfer_ratio r) + (chamfer ...)
    if pad.chamfer_ratio is not None:
        parts.append(f"(chamfer_ratio {_fmt_double(pad.chamfer_ratio)})")
    if pad.chamfer_positions:
        parts.append("(chamfer " + " ".join(c.value for c in pad.chamfer_positions) + ")")

    # (net "name") / (pinfunction) / (pintype) — board context only
    # (!CTL_OMIT_PAD_NETS, format(PAD*) :1847-1859). v10 emits net by NAME
    # (no code); an unconnected pad (net_name None) omits the token.
    if context is SaveContext.BOARD:
        if pad.net_name is not None:
            parts.append(f"(net {_q(pad.net_name)})")
        if pad.pinfunction is not None:
            parts.append(f"(pinfunction {_q(pad.pinfunction)})")
        if pad.pintype is not None:
            parts.append(f"(pintype {_q(pad.pintype)})")

    # die_length / die_delay
    if pad.die_length is not None:
        parts.append(f"(die_length {_fmt(pad.die_length)})")
    if pad.die_delay is not None:
        parts.append(f"(die_delay {_fmt(pad.die_delay)})")

    # solder margins
    if pad.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(pad.solder_mask_margin)})")
    if pad.solder_paste_margin is not None:
        parts.append(f"(solder_paste_margin {_fmt(pad.solder_paste_margin)})")
    if pad.solder_paste_margin_ratio is not None:
        parts.append(f"(solder_paste_margin_ratio {_fmt_double(pad.solder_paste_margin_ratio)})")

    # clearance / zone_connect
    if pad.clearance is not None:
        parts.append(f"(clearance {_fmt(pad.clearance)})")
    if pad.zone_connect is not None:
        parts.append(f"(zone_connect {pad.zone_connect})")

    # thermal
    if pad.thermal_spoke_width is not None:
        parts.append(f"(thermal_bridge_width {_fmt(pad.thermal_spoke_width)})")
    if pad.thermal_spoke_angle is not None:
        parts.append(f"(thermal_bridge_angle {_fmt_angle(pad.thermal_spoke_angle)})")
    if pad.thermal_gap is not None:
        parts.append(f"(thermal_gap {_fmt(pad.thermal_gap)})")

    # custom shape options + primitives
    if pad.shape.value == "custom":
        clearance = pad.custom_shape_clearance or CustomShapeClearance.outline
        anchor = pad.custom_anchor or CustomAnchor.circle
        parts.append(f"(options (clearance {clearance.value}) (anchor {anchor.value}))")
        if pad.primitives:
            parts.append(_dump_primitives(pad.primitives))

    # teardrop params
    if pad.teardrop_params is not None:
        parts.append(_dump_teardrop(pad.teardrop_params))

    # tenting
    if pad.tenting_front is not None or pad.tenting_back is not None:
        tent_parts = ["(tenting"]
        if pad.tenting_front is not None:
            tent_parts.append(f"(front {_bool(pad.tenting_front)})")
        if pad.tenting_back is not None:
            tent_parts.append(f"(back {_bool(pad.tenting_back)})")
        tent_parts.append(")")
        parts.append(" ".join(tent_parts))

    # uuid
    if pad.id is not None:
        parts.append(f"(uuid {_q(pad.id)})")

    # (padstack ...) — §2.4 #34; only when mode != NORMAL
    if pad.padstack is not None:
        parts.append(_dump_padstack(pad.padstack))

    parts.append(")")
    return " ".join(parts)


def _dump_drill(drill: DrillInfo) -> str:
    """Emit a ``(drill ...)`` token."""
    parts = ["(drill"]
    if drill.oval:
        parts.append("oval")
    if drill.width > 0:
        parts.append(_fmt(drill.width))
    if drill.height is not None and drill.height != drill.width:
        parts.append(_fmt(drill.height))
    if drill.offset is not None:
        parts.append(f"(offset {_fmt(drill.offset[0])} {_fmt(drill.offset[1])})")
    parts.append(")")
    return " ".join(parts)


def _dump_teardrop(td: TeardropParams) -> str:
    parts = [
        f"(teardrops"
        f" (best_length_ratio {_fmt_double(td.best_length_ratio)})"
        f" (max_length {_fmt(td.max_length)})"
        f" (best_width_ratio {_fmt_double(td.best_width_ratio)})"
        f" (max_width {_fmt(td.max_width)})"
        f" (curved_edges {_bool(td.curved_edges)})"
        f" (filter_ratio {_fmt_double(td.filter_ratio)})"
        f" (enabled {_bool(td.enabled)})"
        f" (allow_two_segments {_bool(td.allow_two_segments)})"
        f" (prefer_zone_connections {_bool(td.prefer_zone_connections)})"
        f")"
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 3D model
# ---------------------------------------------------------------------------


def _dump_model(model: Model3D) -> str:
    parts = [f"(model {_q(model.path)}"]
    if not model.visible:
        parts.append("(hide yes)")
    if model.opacity is not None:
        # Source: fmt::format("(opacity {:.4f})", opacity)
        parts.append(f"(opacity {model.opacity:.4f})")
    o = model.offset
    s = model.scale
    r = model.rotation
    parts.append(f"(offset (xyz {_fmt_double(o[0])} {_fmt_double(o[1])} {_fmt_double(o[2])}))")
    parts.append(f"(scale (xyz {_fmt_double(s[0])} {_fmt_double(s[1])} {_fmt_double(s[2])}))")
    parts.append(f"(rotate (xyz {_fmt_double(r[0])} {_fmt_double(r[1])} {_fmt_double(r[2])}))")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# PcbGroup
# ---------------------------------------------------------------------------


def _dump_group(group: PcbGroup) -> str:
    parts = [f"(group {_q(group.name)}"]
    if group.id is not None:
        parts.append(f"(uuid {_q(group.id)})")
    if group.locked:
        parts.append("(locked yes)")
    if group.lib_id is not None:
        parts.append(f"(lib_id {_q(group.lib_id)})")
    if group.members:
        # format(PCB_GROUP*) sorts the member UUID strings before emission
        # (pcb_io_kicad_sexpr.cpp:2495).
        members_str = " ".join(_q(m) for m in sort_group_members(group.members))
        parts.append(f"(members {members_str})")
    parts.append(")")
    return " ".join(parts)


def _dump_embedded_files(files: list[EmbeddedFile], *, write_data: bool = True) -> str:
    return _dump_embedded_files_common(files, write_data=write_data)


# ---------------------------------------------------------------------------
# Padstack
# ---------------------------------------------------------------------------


def _dump_padstack_layer(layer: PadstackLayer) -> str:
    """Emit a per-layer block inside a (padstack ...) (§3.1)."""
    parts = [f"(layer {_q(layer.layer_name)}"]
    if layer.shape is not None:
        parts.append(f"(shape {layer.shape.value})")
    if layer.size is not None:
        parts.append(f"(size {_fmt(layer.size[0])} {_fmt(layer.size[1])})")
    if layer.rect_delta is not None:
        parts.append(f"(rect_delta {_fmt(layer.rect_delta[0])} {_fmt(layer.rect_delta[1])})")
    if layer.offset is not None:
        parts.append(f"(offset {_fmt(layer.offset[0])} {_fmt(layer.offset[1])})")
    if layer.roundrect_rratio is not None:
        parts.append(f"(roundrect_rratio {_fmt_double(layer.roundrect_rratio)})")
    if layer.chamfer_ratio is not None:
        parts.append(f"(chamfer_ratio {_fmt_double(layer.chamfer_ratio)})")
    if layer.chamfer_positions:
        parts.append("(chamfer " + " ".join(c.value for c in layer.chamfer_positions) + ")")
    if layer.custom_anchor is not None:
        parts.append(f"(options (anchor {layer.custom_anchor.value}))")
    if layer.primitives:
        parts.append(_dump_primitives(layer.primitives))
    if layer.thermal_bridge_angle is not None:
        parts.append(f"(thermal_bridge_angle {_fmt_angle(layer.thermal_bridge_angle)})")
    if layer.thermal_gap is not None:
        parts.append(f"(thermal_gap {_fmt(layer.thermal_gap)})")
    if layer.thermal_bridge_width is not None:
        parts.append(f"(thermal_bridge_width {_fmt(layer.thermal_bridge_width)})")
    if layer.clearance is not None:
        parts.append(f"(clearance {_fmt(layer.clearance)})")
    if layer.zone_connect is not None:
        parts.append(f"(zone_connect {layer.zone_connect})")
    if layer.tenting_front is not None or layer.tenting_back is not None:
        tent_parts = ["(tenting"]
        if layer.tenting_front is not None:
            tent_parts.append(f"(front {_bool(layer.tenting_front)})")
        if layer.tenting_back is not None:
            tent_parts.append(f"(back {_bool(layer.tenting_back)})")
        tent_parts.append(")")
        parts.append(" ".join(tent_parts))
    parts.append(")")
    return " ".join(parts)


def _dump_padstack(ps: Padstack) -> str:
    """Emit a (padstack ...) block (§3)."""
    parts = [f"(padstack (mode {ps.mode.value})"]
    for layer in ps.layers:
        parts.append(_dump_padstack_layer(layer))
    parts.append(")")
    return " ".join(parts)


def _dump_backdrill(bd: BackdrillInfo, token: str) -> str:
    """Emit a (backdrill ...) or (tertiary_drill ...) block (§3.2)."""
    return f"({token} (size {_fmt(bd.size)}) (layers {_q(bd.start_layer)} {_q(bd.end_layer)}))"


def _dump_post_machining(pm: PostMachining, token: str) -> str:
    """Emit a (front_post_machining ...) or (back_post_machining ...) block (§2.4.2)."""
    parts = [f"({token} {pm.mode.value}"]
    if pm.size is not None and pm.size > 0:
        parts.append(f"(size {_fmt(pm.size)})")
    if pm.depth is not None and pm.depth > 0:
        parts.append(f"(depth {_fmt(pm.depth)})")
    if pm.angle is not None and pm.angle != 0:
        # angle holds the disk value /10; multiply by 10 to restore the exact
        # on-disk token (inverse of the loader's /10). NOT a degrees->disk scale.
        # KiCad emits via FormatDouble2Str (pcb_io_kicad_sexpr.cpp:1769) -> fmt_double.
        parts.append(f"(angle {_fmt_double(pm.angle * 10.0)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


def _dump_zone_keepout(k: ZoneKeepout) -> str:
    def _allowed(v: bool) -> str:
        return "allowed" if v else "not_allowed"

    parts = ["(keepout"]
    parts.append(f"(tracks {_allowed(k.tracks)})")
    parts.append(f"(vias {_allowed(k.vias)})")
    parts.append(f"(pads {_allowed(k.pads)})")
    parts.append(f"(copperpour {_allowed(k.copper_pour)})")
    parts.append(f"(footprints {_allowed(k.footprints)})")
    if k.zone_placement is not None:
        parts.append(f"(zone_placement {_allowed(k.zone_placement)})")
    parts.append(")")
    return " ".join(parts)


def _dump_zone_placement(p: ZonePlacement) -> str:
    # KiCad always writes (enabled yes|no) then the source-type token
    # (sheetname|component_class|group "source"), even when source is empty.
    parts = [
        "(placement",
        f"(enabled {_bool(p.enabled)})",
        f"({p.source_type} {_q(p.source)})",
        ")",
    ]
    return " ".join(parts)


def _dump_zone_fill(f: ZoneFill, *, is_teardrop_area: bool = False) -> str:
    parts = ["(fill"]
    if f.filled:
        parts.append("yes")
    if f.mode is not None:
        parts.append(f"(mode {f.mode})")
    # KiCad suppresses thermal_gap/thermal_bridge_width for teardrop areas
    # (format(ZONE) pcb_io_kicad_sexpr.cpp:2992-2997, GAP-013).
    if not is_teardrop_area:
        parts.append(f"(thermal_gap {_fmt(f.thermal_gap)})")
        parts.append(f"(thermal_bridge_width {_fmt(f.thermal_bridge_width)})")
    if f.smoothing is not None:
        parts.append(f"(smoothing {f.smoothing})")
    if f.radius is not None:
        parts.append(f"(radius {_fmt(f.radius)})")
    parts.append(f"(island_removal_mode {f.island_removal_mode})")
    if f.island_area_min is not None:
        parts.append(f"(island_area_min {_fmt(f.island_area_min)})")
    if f.hatch_thickness is not None:
        parts.append(f"(hatch_thickness {_fmt(f.hatch_thickness)})")
    if f.hatch_gap is not None:
        parts.append(f"(hatch_gap {_fmt(f.hatch_gap)})")
    if f.hatch_orientation is not None:
        parts.append(f"(hatch_orientation {_fmt_double(f.hatch_orientation)})")
    if f.hatch_smoothing_level is not None:
        parts.append(f"(hatch_smoothing_level {f.hatch_smoothing_level})")
    if f.hatch_smoothing_value is not None:
        parts.append(f"(hatch_smoothing_value {_fmt_double(f.hatch_smoothing_value)})")
    if f.hatch_border_algorithm is not None:
        parts.append(f"(hatch_border_algorithm {f.hatch_border_algorithm})")
    if f.hatch_min_hole_area is not None:
        parts.append(f"(hatch_min_hole_area {_fmt_double(f.hatch_min_hole_area)})")
    parts.append(")")
    return " ".join(parts)


def _dump_zone(zone: Zone) -> str:
    """Emit a (zone ...) token (§2.7)."""
    parts = ["(zone"]
    # v10 emits the zone net by NAME in a single (net "<name>") token
    # (format(ZONE) pcb_io_kicad_sexpr.cpp:2864-2868). A legacy integer code
    # (zone.net set) preserves the older two-token (net <code>)(net_name "…")
    # form for round-trip; a name-only zone (net None, net_name set) emits the
    # v10 (net "<name>") form.
    if zone.net is not None:
        parts.append(f"(net {zone.net})")
        if zone.net_name is not None:
            parts.append(f"(net_name {_q(zone.net_name)})")
    elif zone.net_name is not None:
        parts.append(f"(net {_q(zone.net_name)})")
    if zone.locked:
        parts.append("(locked yes)")
    if zone.layers is not None:
        layers_str = " ".join(_q(ln) for ln in zone.layers)
        parts.append(f"(layers {layers_str})")
    elif zone.layer is not None:
        parts.append(f"(layer {_q(zone.layer)})")
    if zone.id is not None:
        parts.append(f"(uuid {_q(zone.id)})")
    if zone.name is not None:
        parts.append(f"(name {_q(zone.name)})")
    parts.append(f"(hatch {zone.hatch_style} {_fmt(zone.hatch_pitch)})")
    if zone.priority is not None:
        parts.append(f"(priority {zone.priority})")
    if zone.teardrop_type is not None:
        parts.append(f"(attr (teardrop (type {zone.teardrop_type})))")
    # connect_pads
    cp_parts = ["(connect_pads"]
    if zone.connect_pads_mode is not None:
        cp_parts.append(zone.connect_pads_mode)
    cp_parts.append(f"(clearance {_fmt(zone.connect_pads_clearance)})")
    cp_parts.append(")")
    parts.append(" ".join(cp_parts))
    if zone.min_thickness is not None:
        parts.append(f"(min_thickness {_fmt(zone.min_thickness)})")
    if zone.filled_areas_thickness is not None:
        parts.append(f"(filled_areas_thickness {_bool(zone.filled_areas_thickness)})")
    if zone.keepout is not None:
        parts.append(_dump_zone_keepout(zone.keepout))
    if zone.placement is not None:
        parts.append(_dump_zone_placement(zone.placement))
    parts.append(_dump_zone_fill(zone.fill, is_teardrop_area=zone.teardrop_type is not None))
    for lp in zone.layer_properties:
        # KiCad emits the block only when a hatching offset is present
        # (ZONE_LAYER_PROPERTIES::hatching_offset, :3094-3110).
        if lp.hatch_position is None:
            continue
        parts.append(
            f"(property (layer {_q(lp.layer)})"
            f" (hatch_position (xy {_fmt(lp.hatch_position[0])} {_fmt(lp.hatch_position[1])})))"
        )
    for poly in zone.polygons:
        parts.append(f"(polygon {_dump_pts(poly.pts)})")
    for fpoly in zone.filled_polygons:
        fp_parts = [f"(filled_polygon (layer {_q(fpoly.layer)})"]
        if fpoly.island:
            fp_parts.append("(island yes)")
        fp_parts.append(_dump_pts(fpoly.pts))
        fp_parts.append(")")
        parts.append(" ".join(fp_parts))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# ReferenceImage
# ---------------------------------------------------------------------------


def _dump_stream_data(data: str) -> str:
    """Emit ``(data "chunk" ...)`` with quoted base64 in <=76-char chunks.

    Mirrors KiCad ``FormatStreamData`` (kicad_io_utils.cpp:54-73): the base64
    payload is split into MIME 76-char lines, each emitted as its own quoted
    token. Prettify then wraps the tokens onto separate indented lines. Empty
    data yields a bare ``(data)``.
    """
    if not data:
        return "(data)"
    chunks = [data[i : i + 76] for i in range(0, len(data), 76)]
    return "(data " + " ".join(f'"{c}"' for c in chunks) + ")"


def _dump_image(img: ReferenceImage) -> str:
    """Emit an (image ...) token (§2.6)."""
    parts = [f"(image (at {_fmt(img.position[0])} {_fmt(img.position[1])})"]
    parts.append(f"(layer {_q(img.layer)})")
    if img.scale is not None:
        parts.append(f"(scale {img.scale:g})")
    if img.locked:
        parts.append("(locked yes)")
    parts.append(_dump_stream_data(img.data))
    if img.id is not None:
        parts.append(f"(uuid {_q(img.id)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Barcode
# ---------------------------------------------------------------------------


def _dump_barcode(bc: Barcode) -> str:
    """Emit a (barcode ...) token (§2.9)."""
    parts = ["(barcode"]
    if bc.locked:
        parts.append("(locked yes)")
    angle_str = _fmt_angle(bc.angle)
    parts.append(f"(at {_fmt(bc.position[0])} {_fmt(bc.position[1])} {angle_str})")
    parts.append(f"(layer {_q(bc.layer)})")
    parts.append(f"(size {_fmt(bc.size[0])} {_fmt(bc.size[1])})")
    parts.append(f"(text {_q(bc.text)})")
    parts.append(f"(text_height {_fmt(bc.text_height)})")
    parts.append(f"(type {bc.barcode_type.value})")
    if bc.ecc_level is not None:
        parts.append(f"(ecc_level {bc.ecc_level.value})")
    parts.append(f"(hide {_bool(not bc.show_text)})")
    parts.append(f"(knockout {_bool(bc.knockout)})")
    if bc.margins is not None:
        parts.append(f"(margins {_fmt(bc.margins[0])} {_fmt(bc.margins[1])})")
    if bc.id is not None:
        parts.append(f"(uuid {_q(bc.id)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def _dump_table_cell(cell: TableCell) -> str:
    """Emit a (table_cell ...) token via the PCB_TEXTBOX cell path (§2.10).

    Order matches format(PCB_TEXTBOX) (pcb_io_kicad_sexpr.cpp:2319-2388):
    content, locked?, start/end, margins, span?, angle?, layer?, uuid?, effects.
    """
    parts = [f"(table_cell {_q(cell.content)}"]
    if cell.locked:
        parts.append("(locked yes)")
    parts.append(
        f"(start {_fmt(cell.start[0])} {_fmt(cell.start[1])})"
        f" (end {_fmt(cell.end[0])} {_fmt(cell.end[1])})"
    )
    parts.append(
        f"(margins {_fmt(cell.margins[0])} {_fmt(cell.margins[1])}"
        f" {_fmt(cell.margins[2])} {_fmt(cell.margins[3])})"
    )
    if cell.span is not None:
        parts.append(f"(span {cell.span[0]} {cell.span[1]})")
    if cell.angle:
        parts.append(f"(angle {_fmt_angle(cell.angle)})")
    if cell.layer:
        parts.append(f"(layer {_q(cell.layer)})")
    if cell.id is not None:
        parts.append(f"(uuid {_q(cell.id)})")
    if cell.attributes is not None:
        parts.append(_dump_effects(cell.attributes))
    parts.append(")")
    return " ".join(parts)


def _dump_table(table: Table) -> str:
    """Emit a (table ...) token (§2.10)."""
    parts = [f"(table (column_count {table.column_count})"]
    if table.id is not None:
        parts.append(f"(uuid {_q(table.id)})")
    if table.locked:
        parts.append("(locked yes)")
    if table.layer:
        parts.append(f"(layer {_q(table.layer)})")
    # border — always emit
    b = table.border
    b_parts = ["(border", f"(external {_bool(b.external)})", f"(header {_bool(b.header)})"]
    if b.stroke is not None:
        b_parts.append(_dump_stroke(b.stroke))
    b_parts.append(")")
    parts.append(" ".join(b_parts))
    # separators — always emit
    s = table.separators
    s_parts = ["(separators", f"(rows {_bool(s.rows)})", f"(cols {_bool(s.cols)})"]
    if s.stroke is not None:
        s_parts.append(_dump_stroke(s.stroke))
    s_parts.append(")")
    parts.append(" ".join(s_parts))
    if table.column_widths:
        widths_str = " ".join(_fmt(w) for w in table.column_widths)
        parts.append(f"(column_widths {widths_str})")
    if table.row_heights:
        heights_str = " ".join(_fmt(h) for h in table.row_heights)
        parts.append(f"(row_heights {heights_str})")
    if table.cells:
        cells_str = " ".join(_dump_table_cell(c) for c in table.cells)
        parts.append(f"(cells {cells_str})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Dimension
# ---------------------------------------------------------------------------


def _dump_dimension_text(dt: DimensionText) -> str:
    """Emit a gr_text block inside a dimension."""
    parts = [f"(gr_text {_q(dt.content)}"]
    if dt.locked:
        parts.append("(locked yes)")
    angle_str = _fmt_angle(dt.angle)
    parts.append(f"(at {_fmt(dt.position[0])} {_fmt(dt.position[1])} {angle_str})")
    if dt.unlocked:
        parts.append("(unlocked yes)")
    if dt.knockout:
        parts.append(f"(layer {_q(dt.layer)} knockout)")
    elif dt.layer:
        parts.append(f"(layer {_q(dt.layer)})")
    if not dt.visible:
        parts.append("(hide yes)")
    if dt.id is not None:
        parts.append(f"(uuid {_q(dt.id)})")
    if dt.attributes is not None:
        parts.append(_dump_effects(dt.attributes))
    if dt.render_cache is not None:
        parts.append(_dump_render_cache(dt.render_cache))  # type: ignore[arg-type]
    parts.append(")")
    return " ".join(parts)


def _dump_dimension_style(s: DimensionStyle) -> str:
    parts = [
        "(style",
        f"(thickness {_fmt(s.thickness)})",
        f"(arrow_length {_fmt(s.arrow_length)})",
        f"(text_position_mode {s.text_position_mode})",
    ]
    if s.arrow_direction is not None:
        parts.append(f"(arrow_direction {s.arrow_direction.value})")
    if s.extension_height is not None:
        parts.append(f"(extension_height {_fmt(s.extension_height)})")
    if s.text_frame is not None:
        parts.append(f"(text_frame {s.text_frame})")
    parts.append(f"(extension_offset {_fmt(s.extension_offset)})")
    if s.keep_text_aligned:
        parts.append("(keep_text_aligned yes)")
    parts.append(")")
    return " ".join(parts)


def _dump_dimension_format(f: DimensionFormat) -> str:
    parts = [
        "(format",
        f"(prefix {_q(f.prefix)})",
        f"(suffix {_q(f.suffix)})",
        f"(units {f.units})",
        f"(units_format {f.units_format})",
        f"(precision {f.precision})",
    ]
    if f.override_value is not None:
        parts.append(f"(override_value {_q(f.override_value)})")
    if f.suppress_zeroes:
        parts.append("(suppress_zeroes yes)")
    parts.append(")")
    return " ".join(parts)


def _dump_dimension(dim: Dimension) -> str:
    """Emit a (dimension ...) token (§2.8)."""
    parts = [f"(dimension (type {dim.dim_type.value})"]
    if dim.locked:
        parts.append("(locked yes)")
    parts.append(f"(layer {_q(dim.layer)})")
    if dim.id is not None:
        parts.append(f"(uuid {_q(dim.id)})")
    if dim.pts:
        parts.append(_dump_pts(dim.pts))
    if dim.height is not None:
        parts.append(f"(height {_fmt(dim.height)})")
    if dim.leader_length is not None:
        parts.append(f"(leader_length {_fmt(dim.leader_length)})")
    if dim.orientation is not None:
        parts.append(f"(orientation {dim.orientation})")
    if dim.dim_format is not None:
        parts.append(_dump_dimension_format(dim.dim_format))
    parts.append(_dump_dimension_style(dim.style))
    if dim.text is not None:
        parts.append(_dump_dimension_text(dim.text))
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# PcbPoint
# ---------------------------------------------------------------------------


def _dump_pcb_point(pt: PcbPoint) -> str:
    """Emit a (point ...) token (§2.5)."""
    parts = [
        f"(point (at {_fmt(pt.position[0])} {_fmt(pt.position[1])})"
        f" (size {_fmt(pt.size)})"
        f" (layer {_q(pt.layer)})"
    ]
    if pt.id is not None:
        parts.append(f"(uuid {_q(pt.id)})")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Variant
# ---------------------------------------------------------------------------


def _dump_variant(v: Variant) -> str:
    """Emit a (variant ...) token (§2.12)."""
    parts = [f"(variant (name {_q(v.name)})"]
    if v.dnp is not None:
        parts.append(f"(dnp {_bool(v.dnp)})")
    if v.exclude_from_bom is not None:
        parts.append(f"(exclude_from_bom {_bool(v.exclude_from_bom)})")
    if v.exclude_from_pos_files is not None:
        parts.append(f"(exclude_from_pos_files {_bool(v.exclude_from_pos_files)})")
    for field in sort_variant_fields(v.fields):
        parts.append(f"(field (name {_q(field.name)}) (value {_q(field.value)}))")
    parts.append(")")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Footprint root
# ---------------------------------------------------------------------------


def dump_mod(fp: Footprint, *, context: SaveContext = SaveContext.LIBRARY) -> str:
    """Emit a flat (unindented) S-expression string for *fp*.

    Token ordering follows pcb_io_kicad_sexpr.cpp format(FOOTPRINT*) exactly
    for library context (CTL_OMIT_UUIDS | CTL_OMIT_AT | CTL_OMIT_PATH |
    CTL_OMIT_PAD_NETS | CTL_OMIT_LIBNAME).

    *context* selects the CTL analog (``common/context.py``). ``LIBRARY``
    (``CTL_FOR_LIBRARY``) is the byte-identity-preserving default for standalone
    ``.kicad_mod`` files. ``BOARD`` (``CTL_FOR_BOARD``) is a footprint embedded in
    a ``.kicad_pcb``: it omits the version/generator block, and emits pad/zone
    net + pinfunction/pintype, deferring embedded-file data to the board root
    (checksum-only). ``SCHEMATIC`` is not valid for a footprint and raises.

    The footprint name is emitted verbatim from ``fp.fp_id`` in both contexts:
    library files store the bare item name and board files store the full
    ``lib:name`` FPID, each round-tripping as read (the extract/inject transforms
    in T10 own the name rewrite).
    """
    if context is SaveContext.SCHEMATIC:
        raise NotImplementedError(
            "SaveContext.SCHEMATIC is not valid for a footprint; use LIBRARY or BOARD."
        )
    is_board = context is SaveContext.BOARD
    parts = [f"(footprint {_q(fp.fp_id)}"]

    # Header: version, generator, generator_version — §2.1 #1-3.
    # Omitted in board context (CTL_OMIT_FOOTPRINT_VERSION, format(FOOTPRINT*)
    # :1201-1206): a board-embedded footprint inherits the board file version.
    if not is_board:
        parts.append(f"(version {fp.version})")
        parts.append(f"(generator {_q(fp.generator)})")
        if fp.generator_version is not None:
            parts.append(f"(generator_version {_q(fp.generator_version)})")

    # locked (conditional) — §2.1 #4
    if fp.locked:
        parts.append("(locked yes)")

    # placed (conditional) — §2.1 #5
    if fp.placed:
        parts.append("(placed yes)")

    # layer (always) — §2.1 #6
    parts.append(f"(layer {_q(fp.layer)})")

    # footprint-level uuid — §2.1 #6b / GAP-033; board context only
    # (!CTL_OMIT_UUIDS, format(FOOTPRINT*) :1216-1217). Emitted only when present.
    if fp.uuid is not None:
        parts.append(f"(uuid {_q(fp.uuid)})")

    # footprint placement (at X Y [rot]) — §2.1 #7 / GAP-033; board context only
    # (!CTL_OMIT_AT, :1219-1226). rot suppressed when zero.
    if fp.at is not None:
        at_x = _fmt(fp.at.position[0])
        at_y = _fmt(fp.at.position[1])
        if fp.at.angle != 0.0:
            parts.append(f"(at {at_x} {at_y} {_fmt_angle(fp.at.angle)})")
        else:
            parts.append(f"(at {at_x} {at_y})")

    # descr / tags (conditional) — §2.1 #9-10
    if fp.description:
        parts.append(f"(descr {_q(fp.description)})")
    if fp.keywords:
        parts.append(f"(tags {_q(fp.keywords)})")

    # Properties (PCB_FIELDs) — §2.1 #11. The FOOTPRINT constructor pre-creates
    # Reference, Value, Datasheet and Description in that order and the parser
    # appends user fields after them (footprint.cpp:105-117, parser :5167-5182).
    for prop in sort_footprint_properties(fp.properties):
        parts.append(_dump_fp_property(prop))

    # component_classes — §2.1 #12
    if fp.component_classes is not None and fp.component_classes.classes:
        cc_parts = ["(component_classes"]
        for cls in fp.component_classes.classes:
            cc_parts.append(f"(class {_q(cls)})")
        cc_parts.append(")")
        parts.append(" ".join(cc_parts))

    # fp_filters — §2.1 #13; emitted as (property ki_fp_filters "...")
    if fp.fp_filters:
        parts.append(f"(property ki_fp_filters {_q(fp.fp_filters)})")

    # path / sheetname / sheetfile — §2.1 #14-16
    if fp.path is not None:
        parts.append(f"(path {_q(fp.path)})")
    if fp.sheetname is not None:
        parts.append(f"(sheetname {_q(fp.sheetname)})")
    if fp.sheetfile is not None:
        parts.append(f"(sheetfile {_q(fp.sheetfile)})")

    # unit_info (gate-swap) — §2.1 #17
    if fp.unit_info is not None and fp.unit_info.units:
        u_parts = ["(units"]
        for unit in fp.unit_info.units:
            pins_str = " ".join(_q(p) for p in unit.pins)
            u_parts.append(f"(unit (name {_q(unit.name)}) (pins {pins_str}))")
        u_parts.append(")")
        parts.append(" ".join(u_parts))

    # Footprint-level margin overrides (all conditional) — §2.1 #18-22
    if fp.solder_mask_margin is not None:
        parts.append(f"(solder_mask_margin {_fmt(fp.solder_mask_margin)})")
    if fp.solder_paste_margin is not None:
        parts.append(f"(solder_paste_margin {_fmt(fp.solder_paste_margin)})")
    if fp.solder_paste_margin_ratio is not None:
        parts.append(f"(solder_paste_margin_ratio {_fmt_double(fp.solder_paste_margin_ratio)})")
    if fp.clearance is not None:
        parts.append(f"(clearance {_fmt(fp.clearance)})")
    if fp.zone_connect is not None:
        parts.append(f"(zone_connect {fp.zone_connect})")

    # attr block (conditional — only when any flag set) — §2.1 #23
    if fp.attributes.has_any():
        attr_parts = ["(attr"]
        a = fp.attributes
        if a.smd:
            attr_parts.append("smd")
        if a.through_hole:
            attr_parts.append("through_hole")
        if a.board_only:
            attr_parts.append("board_only")
        if a.exclude_from_pos_files:
            attr_parts.append("exclude_from_pos_files")
        if a.exclude_from_bom:
            attr_parts.append("exclude_from_bom")
        if a.allow_missing_courtyard:
            attr_parts.append("allow_missing_courtyard")
        if a.dnp:
            attr_parts.append("dnp")
        if a.allow_soldermask_bridges:
            attr_parts.append("allow_soldermask_bridges")
        attr_parts.append(")")
        parts.append(" ".join(attr_parts))

    # stackup — §2.1 #24
    if fp.stackup is not None and fp.stackup.layers:
        st_parts = ["(stackup"]
        for sl in fp.stackup.layers:
            st_parts.append(f"(layer {_q(sl.name)})")
        st_parts.append(")")
        parts.append(" ".join(st_parts))

    # private_layers (conditional) — §2.1 #25
    if fp.private_layers:
        layers_str = " ".join(_q(lyr) for lyr in fp.private_layers)
        parts.append(f"(private_layers {layers_str})")

    # net_tie_pad_groups (conditional) — §2.1 #26
    if fp.net_tie_pad_groups:
        groups_str = " ".join(_q(g) for g in fp.net_tie_pad_groups)
        parts.append(f"(net_tie_pad_groups {groups_str})")

    # duplicate_pad_numbers_are_jumpers — UNCONDITIONAL — §2.1 #27
    dup_jmp = _bool(fp.duplicate_pad_numbers_are_jumpers)
    parts.append(f"(duplicate_pad_numbers_are_jumpers {dup_jmp})")

    # jumper_pad_groups — §2.1 #28
    if fp.jumper_pad_groups:
        jg_parts = ["(jumper_pad_groups"]
        for grp in fp.jumper_pad_groups:
            grp_inner = " ".join(_q(p) for p in grp)
            jg_parts.append(f"({grp_inner})")
        jg_parts.append(")")
        parts.append(" ".join(jg_parts))

    # FOOTPRINT::cmp_drawings, cmp_zones and cmp_points compare BOARD coordinates,
    # while the file stores footprint-relative ones. This frame rebuilds them
    # (pcb_io_kicad_sexpr.cpp:466-476); it is the identity for a library file.
    frame = FootprintFrame(fp.at.position, fp.at.angle) if fp.at is not None else FootprintFrame()

    # Drawing items (all fp_text, fp_line, fp_rect, etc.) — §2.1 #31
    for item in sort_footprint_drawings(fp.drawings, frame):
        s = _dump_drawing(item)
        if s:
            parts.append(s)

    # PCB_POINT items — §2.1 #32
    for pt in sort_pcb_points(fp.points):
        parts.append(_dump_pcb_point(pt))

    # Pads — §2.1 #33
    for pad in sort_pads(fp.pads):
        parts.append(_dump_pad(pad, context=context))

    # Zones — §2.1 #34
    for zone in sort_zones(fp.zones):
        parts.append(_dump_zone(zone))

    # Groups — §2.1 #35. A group's layer key is the union of its members' layer
    # sets (pcb_group.cpp:329-337), so the members resolve through an index first.
    for group in sort_groups(fp.groups, footprint_member_layer_sets(fp)):
        parts.append(_dump_group(group))

    # Variants — §2.1 #36
    for variant in sort_variants(fp.variants):
        parts.append(_dump_variant(variant))

    # embedded_fonts — UNCONDITIONAL — §2.1 #37
    parts.append(f"(embedded_fonts {_bool(fp.embedded_fonts)})")

    # embedded_files (conditional) — §2.1 #38. In board context the footprint
    # emits checksum-only stubs (aWriteData=false, format(FOOTPRINT*) :1497); the
    # board root consolidates the data (T9). In library context data is inline.
    if fp.embedded_files:
        parts.append(_dump_embedded_files(fp.embedded_files, write_data=not is_board))

    # 3D models — §2.1 #39
    for model in fp.models:
        parts.append(_dump_model(model))

    parts.append(")")
    return " ".join(parts)


def write_mod(fp: Footprint, *, context: SaveContext = SaveContext.LIBRARY) -> str:
    """Serialise *fp* to a canonical ``.kicad_mod`` string.

    Calls ``dump_mod`` then applies the Prettify post-pass with NORMAL mode
    (confirmed by source: FP_CACHE::Save() uses PRETTIFIED_FILE_OUTPUTFORMATTER
    with no mode argument = FORMAT_MODE::NORMAL).

    *context* threads through to ``dump_mod`` (T18 scaffold; only
    ``SaveContext.LIBRARY`` is implemented this iteration).
    """
    flat = dump_mod(fp, context=context)
    return prettify(flat, mode="NORMAL")
