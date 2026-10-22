"""Board millimeters to SVG shape data. FROZEN (ADR-098).

This layer maps geometry and nothing else. It applies no color, no opacity, no
filtering and no element identity. Those belong to ``render/style.py``.

The freeze is not decoration. The ``kicad-cli`` parity oracle measures exactly
this layer. If a policy default ever leaks in here, the oracle dies the first
time somebody changes that default. Add a capability above this layer instead.

Source facts, from ``common/plotters/SVG_plotter.cpp`` at KiCad 10.0.3:

- Coordinates are millimeters at 4 decimal places (``m_precision = 4``, line
  176). The reason is recorded at lines 106-111: six digits broke
  ``stroke-width`` for very small non-zero values. The resolution is 0.1 micron.
- An arc is a true SVG ``A`` command (lines 575-585). KiCad never polygonizes an
  arc, and neither does this module.
- A stroke width of zero or less becomes ``stroke:none`` (lines 249-251). The
  board plot path passes the stored width through untouched
  (``plot_brditems_plotter.cpp:917``), so a stored zero really does mean an
  invisible shape. It is NOT a request for a default width.
- Dash, dot and dash-dot stroke types emit ``stroke-dasharray`` (lines 265-290).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from eedesigncore.common.kimath import calc_arc_center_double, rotate_point_iu
from eedesigncore.common.models import (
    Property,
    PtsArc,
    PtsElement,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.common.pcb_family.board_items import Dimension, Zone
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
)
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.mod.models import Pad
from eedesigncore.pcb.models import TrackArc, TrackSegment, Via
from eedesigncore.render.pads import pad_outline
from eedesigncore.render.vias import (
    DEFAULT_VIA_DIAMETER,
    ViaDiameter,
    via_diameter_mm,
)

Point = tuple[float, float]
PointMap = Callable[[Point], Point]

#: Decimal places for every millimeter value. ``SVG_plotter.cpp:176``.
DEFAULT_PRECISION = 4

# Dash pattern ratios, resolved from ``common/render_settings.cpp:60-85`` with
# ``correction = 1.0``. Each ratio multiplies the stroke width.
#   dash = max(m_dashLengthRatio - correction, 1.0) = max(12 - 1, 1) = 11
#   gap  = max(m_gapLengthRatio  + correction, 1.0) = max(3 + 1, 1)  = 4
#   dot  = max(1.0 - correction, 0.2)               = 0.2
DASH_RATIO = 11.0
GAP_RATIO = 4.0
DOT_RATIO = 0.2

#: Attribute pairs of one SVG element, in emission order.
Attrs = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SvgShape:
    """One SVG element carrying geometry attributes only.

    ``filled`` says the shape is a solid area rather than a stroked outline. The
    geometry layer decides that, because the rule that sets it reads board
    numbers. The color of the area is still the policy layer's choice.
    """

    tag: str
    attrs: Attrs
    filled: bool = False
    #: Stroke width in millimeters, when the SHAPE rather than the item owns it.
    #:
    #: An oval pad is the case that needs this. KiCad draws it as one stroked
    #: segment whose width is the pad's minor axis (``PS_plotter.cpp:93-116``), and
    #: a padstack can give the same pad a different minor axis per layer. So the
    #: width belongs to the shape, not to the item, and ``document.width_attrs``
    #: reads it here first.
    stroke_width_mm: float | None = None


def format_mm(value: float, precision: int = DEFAULT_PRECISION) -> str:
    """Format one millimeter value at *precision* decimal places.

    Negative zero collapses to zero. Two boards that differ only in the sign of a
    zero must not produce different bytes, and no renderer draws them apart.
    """
    text = f"{value:.{precision}f}"
    if text.lstrip("-").strip("0.") == "":
        return text.lstrip("-")
    return text


def stroke_attrs(
    stroke: Stroke,
    precision: int = DEFAULT_PRECISION,
    *,
    dash_ratio: float = 12.0,
    gap_ratio: float = 3.0,
) -> Attrs:
    """Return the stroke width and dash pattern of *stroke*.

    A width of zero or less gives ``stroke: none``, mirroring
    ``SVG_plotter.cpp:249-251``. The shape still reaches the document, so a
    caller that hunts elements by identity can still find it.
    Ratios are the saved, uncorrected board values. Round-cap correction and
    minimum lengths follow KiCad render settings.
    """
    if not all(math.isfinite(value) for value in (dash_ratio, gap_ratio)):
        raise ValueError("Dash and gap ratios must be finite.")
    width = stroke.width
    if width <= 0.0:
        return (("stroke", "none"),)

    pairs: list[tuple[str, str]] = [("stroke-width", format_mm(width, precision))]
    pattern = _dash_pattern(stroke.type, width, dash_ratio, gap_ratio)
    if pattern:
        pairs.append(
            ("stroke-dasharray", ",".join(format_mm(part, precision) for part in pattern))
        )
    return tuple(pairs)


def _dash_pattern(
    stroke_type: StrokeType, width: float, dash_ratio: float = 12.0, gap_ratio: float = 3.0
) -> list[float]:
    """Return the dash lengths of *stroke_type*. ``SVG_plotter.cpp:265-290``."""
    dash = max(dash_ratio - 1.0, 1.0) * width
    gap = max(gap_ratio + 1.0, 1.0) * width
    dot = DOT_RATIO * width
    if stroke_type == StrokeType.dash:
        return [dash, gap]
    if stroke_type == StrokeType.dot:
        return [dot, gap]
    if stroke_type == StrokeType.dash_dot:
        return [dash, gap, dot, gap]
    if stroke_type == StrokeType.dash_dot_dot:
        return [dash, gap, dot, gap, dot, gap]
    return []


# ---------------------------------------------------------------------------
# Path builders
# ---------------------------------------------------------------------------


def _pt(point: Point, precision: int) -> str:
    return f"{format_mm(point[0], precision)},{format_mm(point[1], precision)}"


def arc_center(start: Point, mid: Point, end: Point) -> Point:
    """Return the center of the arc through *start*, *mid* and *end*.

    The work goes through KiCad's own ``CalcArcCenter``. Its uncertainty
    arithmetic and its round-number snap both move the result, so a plain
    circumcenter would put the curve in a slightly different place.

    Raises:
        ValueError: when the three points fall on one line. A straight run has
            no center, and the arithmetic runs off to infinity rather than
            saying so. Call :func:`arc_geometry` to get ``None`` instead of an
            exception; the renderer always does.
    """
    start_iu, mid_iu, end_iu = point_to_iu(start), point_to_iu(mid), point_to_iu(end)
    center = (
        None
        if is_straight(start_iu, mid_iu, end_iu)
        else _center_or_none(start_iu, mid_iu, end_iu)
    )
    if center is None:
        raise ValueError(
            f"the arc through {start}, {mid} and {end} has no center, because the "
            "three points fall on one line to within a nanometer. Use arc_geometry "
            "to test for this instead of catching an error."
        )
    return center


def is_straight(
    start_iu: tuple[int, int], mid_iu: tuple[int, int], end_iu: tuple[int, int]
) -> bool:
    """True when three points on the nanometer grid do not bow into an arc.

    This is the single test both :func:`arc_center` and :func:`arc_geometry` ask,
    so the two can never disagree about one arc. They did once, and the
    disagreement is what produced ``OverflowError``.

    Collinear points do NOT reliably send ``CalcArcCenter`` to infinity. The
    reported case ``(0,0) (0.5,0) (1,0)`` returns a finite center 2.25e15 mm
    away. A finite answer is not a right answer, so straightness is measured, not
    inferred from the arithmetic failing.
    """
    chord = math.hypot(end_iu[0] - start_iu[0], end_iu[1] - start_iu[1])
    if chord < 1.0:
        return True
    cross = (mid_iu[0] - start_iu[0]) * (end_iu[1] - start_iu[1]) - (mid_iu[1] - start_iu[1]) * (
        end_iu[0] - start_iu[0]
    )
    return abs(cross) / chord <= STRAIGHT_TOLERANCE_IU


def _center_or_none(
    start_iu: tuple[int, int], mid_iu: tuple[int, int], end_iu: tuple[int, int]
) -> Point | None:
    """Return the arc center in millimeters, or ``None`` when it runs to infinity."""
    center_iu = calc_arc_center_double(start_iu, mid_iu, end_iu)
    if not (math.isfinite(center_iu[0]) and math.isfinite(center_iu[1])):
        return None
    return (to_mm(round(center_iu[0])), to_mm(round(center_iu[1])))


def arc_flags(start: Point, mid: Point, end: Point, center: Point) -> tuple[int, int]:
    """Return the large-arc flag and the sweep flag of an arc.

    Board coordinates put y downward, and so does SVG user space. A sweep flag of
    1 therefore means the angle grows from start to end.

    KiCad sorts the two endpoints by angle and always writes a sweep flag of 0
    (``SVG_plotter.cpp:539-540``). This module keeps the stored start-to-end
    direction instead, because the contract calls for verbatim geometry. The two
    forms draw the same curve: an arc from A to B with sweep 0 is the arc from B
    to A with sweep 1.
    """
    cross = (mid[0] - start[0]) * (end[1] - mid[1]) - (mid[1] - start[1]) * (end[0] - mid[0])
    sweep = 1 if cross > 0 else 0

    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    swept = (end_angle - start_angle) if sweep else (start_angle - end_angle)
    swept %= math.tau
    return (1 if swept > math.pi else 0, sweep)


#: Smallest bow, in internal units, that still counts as an arc. One unit is one
#: nanometer, which is the finest a board file can hold.
#:
#: The test MUST run on the same grid the center calculation uses. An earlier
#: version measured the bow in millimeters on the raw input and then computed the
#: center from nanometer-rounded points. A curve that rose half a nanometer
#: passed the first test and was straight by the second, so the center ran to
#: infinity and ``round`` raised ``OverflowError``. Between those two limits it
#: produced radii of 1e15 mm and larger. One grid, one answer.
STRAIGHT_TOLERANCE_IU = 1.0

#: :data:`STRAIGHT_TOLERANCE_IU` stated in millimeters.
STRAIGHT_TOLERANCE_MM = 1e-6


def arc_geometry(start: Point, mid: Point, end: Point) -> tuple[Point, float] | None:
    """Return the center and radius of an arc, or ``None`` when it is straight.

    ``None`` means the caller must draw a segment. This function never raises. An
    item the renderer cannot draw goes to the caller's skip report, so a
    degenerate arc must be an answer and not an exception.

    Straightness comes from :func:`is_straight`, which both this function and
    :func:`arc_center` ask, so the two can never disagree about one arc.
    """
    start_iu = point_to_iu(start)
    mid_iu = point_to_iu(mid)
    end_iu = point_to_iu(end)

    if is_straight(start_iu, mid_iu, end_iu):
        return None

    center = _center_or_none(start_iu, mid_iu, end_iu)
    if center is None:
        return None

    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    if radius <= 0.0 or not math.isfinite(radius):
        return None
    return (center, radius)


def _arc_command(
    start: Point, mid: Point, end: Point, radius: float, center: Point, precision: int
) -> str:
    large_arc, sweep = arc_flags(start, mid, end, center)
    radius_text = format_mm(radius, precision)
    return f"A{radius_text} {radius_text} 0 {large_arc} {sweep} {_pt(end, precision)}"


def _full_circle_path(start: Point, mid: Point, precision: int) -> str:
    """Draw an arc whose two ends meet, as two half turns.

    One ``A`` command cannot close a circle, because a command that starts and
    ends on one point sweeps nothing.
    """
    radius = math.hypot(mid[0] - start[0], mid[1] - start[1]) / 2.0
    if radius <= 0.0:
        return ""
    radius_text = format_mm(radius, precision)
    return (
        f"M{_pt(start, precision)} "
        f"A{radius_text} {radius_text} 0 0 1 {_pt(mid, precision)} "
        f"A{radius_text} {radius_text} 0 0 1 {_pt(start, precision)}"
    )


def arc_path(start: Point, mid: Point, end: Point, precision: int) -> str:
    """Return the ``d`` attribute of an arc, as a true SVG ``A`` command.

    An arc whose three points fall on one line becomes a straight segment. An arc
    whose two ends meet becomes two half turns.
    """
    if start == end:
        return _full_circle_path(start, mid, precision)

    geometry = arc_geometry(start, mid, end)
    if geometry is None:
        return f"M{_pt(start, precision)} L{_pt(end, precision)}"

    center, radius = geometry
    return f"M{_pt(start, precision)} {_arc_command(start, mid, end, radius, center, precision)}"


def chain_path(
    points: Sequence[PtsElement], to_board: PointMap, precision: int, *, closed: bool
) -> str:
    """Return the ``d`` attribute of a point chain.

    A chain holds plain points and whole arcs interleaved, because KiCad writes
    one ``(arc …)`` per arc run of a line chain. An arc element keeps its curve.
    """
    parts: list[str] = []
    for index, element in enumerate(points):
        if isinstance(element, PtsArc):
            start = to_board(element.start)
            mid = to_board(element.mid)
            end = to_board(element.end)
            if index == 0:
                parts.append(f"M{_pt(start, precision)}")
            else:
                parts.append(f"L{_pt(start, precision)}")
            geometry = arc_geometry(start, mid, end)
            if geometry is None:
                parts.append(f"L{_pt(end, precision)}")
            else:
                center, radius = geometry
                parts.append(_arc_command(start, mid, end, radius, center, precision))
        else:
            mapped = to_board(element)
            parts.append(f"{'M' if index == 0 else 'L'}{_pt(mapped, precision)}")

    if not parts:
        return ""
    if closed:
        parts.append("Z")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Per-item emitters
# ---------------------------------------------------------------------------


def _line_shapes(item: PcbLine, to_board: PointMap, precision: int) -> list[SvgShape]:
    start, end = to_board(item.start), to_board(item.end)
    return [SvgShape("path", (("d", f"M{_pt(start, precision)} L{_pt(end, precision)}"),))]


def rectangle_chain(item: PcbRect) -> list[PtsElement]:
    """Return local rectangle edges with clamped analytic corner arcs."""
    (x0, y0), (x1, y1) = item.start, item.end
    radius = item.corner_radius or 0.0
    if not math.isfinite(radius):
        raise ValueError("Rectangle corner radius must be finite.")
    radius = max(0.0, min(radius, abs(x1 - x0) / 2, abs(y1 - y0) / 2))
    if radius == 0:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    diagonal = radius / math.sqrt(2)
    return [
        PtsArc(
            start=(right - radius, top),
            mid=(right - radius + diagonal, top + radius - diagonal),
            end=(right, top + radius),
        ),
        PtsArc(
            start=(right, bottom - radius),
            mid=(right - radius + diagonal, bottom - radius + diagonal),
            end=(right - radius, bottom),
        ),
        PtsArc(
            start=(left + radius, bottom),
            mid=(left + radius - diagonal, bottom - radius + diagonal),
            end=(left, bottom - radius),
        ),
        PtsArc(
            start=(left, top + radius),
            mid=(left + radius - diagonal, top + radius - diagonal),
            end=(left + radius, top),
        ),
    ]


def _rect_shapes(item: PcbRect, to_board: PointMap, precision: int) -> list[SvgShape]:
    """Emit a rectangle as a closed path.

    Lines and analytic corner arcs share the host transform. The same local
    chain supplies selected drawing bounds.
    """
    return [
        SvgShape(
            "path", (("d", chain_path(rectangle_chain(item), to_board, precision, closed=True)),)
        )
    ]


def _circle_shapes(item: PcbCircle, to_board: PointMap, precision: int) -> list[SvgShape]:
    """Emit a circle. The stored ``end`` is a point on the perimeter.

    A circle narrower than its own stroke becomes a solid disc that reaches the
    outer edge of that stroke: the radius grows by half the width
    (``SVG_plotter.cpp:454-462``). KiCad makes the switch because a ring drawn at
    that size is a blob anyway, and a stroked ring would draw a hole through it.
    """
    center = to_board(item.center)
    edge = to_board(item.end)
    radius = math.hypot(edge[0] - center[0], edge[1] - center[1])

    width = item.stroke.width
    filled = width > 0.0 and radius * 2.0 < width
    if filled:
        radius += width / 2.0

    return [
        SvgShape(
            "circle",
            (
                ("cx", format_mm(center[0], precision)),
                ("cy", format_mm(center[1], precision)),
                ("r", format_mm(radius, precision)),
            ),
            filled=filled,
        )
    ]


def _arc_shapes(item: PcbArc, to_board: PointMap, precision: int) -> list[SvgShape]:
    start, mid, end = to_board(item.start), to_board(item.mid), to_board(item.end)
    return [SvgShape("path", (("d", arc_path(start, mid, end, precision)),))]


def _poly_shapes(item: PcbPoly, to_board: PointMap, precision: int) -> list[SvgShape]:
    data = chain_path(item.pts, to_board, precision, closed=True)
    return [SvgShape("path", (("d", data),))] if data else []


def _point_shapes(item: PcbPoint, precision: int) -> list[SvgShape]:
    """Emit the point marker: a cross of two diagonals inside a ring.

    KiCad's plot path never exports this item, so ``kicad-cli`` produces nothing
    here. The shape comes from the on-screen painter instead
    (``pcb_painter.cpp:3143-3177``): half the stored size gives the reach of each
    diagonal, and a quarter of it gives the ring radius.

    The stored position is a BOARD coordinate even when a footprint hosts the
    item, so no host transform reaches this function.
    """
    center_x, center_y = item.position
    reach = item.size / 2.0
    ring = item.size / 4.0
    if reach <= 0.0:
        return []

    def corner(dx: float, dy: float) -> str:
        return _pt((center_x + dx, center_y + dy), precision)

    return [
        SvgShape(
            "path",
            (
                (
                    "d",
                    f"M{corner(-reach, -reach)} L{corner(reach, reach)} "
                    f"M{corner(reach, -reach)} L{corner(-reach, reach)}",
                ),
            ),
        ),
        SvgShape(
            "circle",
            (
                ("cx", format_mm(center_x, precision)),
                ("cy", format_mm(center_y, precision)),
                ("r", format_mm(ring, precision)),
            ),
        ),
    ]


def _track_segment_shapes(
    item: TrackSegment, to_board: PointMap, precision: int
) -> list[SvgShape]:
    """Emit a copper track segment as a stroked line.

    The width is board data on the item itself rather than inside a ``(stroke …)``
    block, so :func:`eedesigncore.render.document.width_attrs` reads it there.
    The wrapper group already carries round caps, which is how a real track ends.
    """
    start, end = to_board(item.start), to_board(item.end)
    return [SvgShape("path", (("d", f"M{_pt(start, precision)} L{_pt(end, precision)}"),))]


def _track_arc_shapes(item: TrackArc, to_board: PointMap, precision: int) -> list[SvgShape]:
    """Emit a curved copper track as a stroked arc."""
    start, mid, end = to_board(item.start), to_board(item.mid), to_board(item.end)
    return [SvgShape("path", (("d", arc_path(start, mid, end, precision)),))]


def _via_shapes(
    item: Via,
    to_board: PointMap,
    precision: int,
    layer: str | None,
    via_diameter: ViaDiameter = DEFAULT_VIA_DIAMETER,
) -> list[SvgShape]:
    """Emit a via as its annular ring, a solid disc.

    The diameter comes from the via's PADSTACK, which can give a different width
    per layer. :func:`eedesigncore.render.vias.via_diameter_mm` holds that rule
    and the caller's choice between the two answers.

    The hole is not drawn. A preview shows where copper IS, and a via's hole is
    plated, so on a copper layer the ring reads as solid. Drawing the barrel
    would also need the drill of every padstack layer, which a preview does not
    have a use for yet.
    """
    center = to_board(item.at)
    diameter = via_diameter_mm(item, layer or "", via_diameter)
    if diameter <= 0.0:
        return []
    return [
        SvgShape(
            "circle",
            (
                ("cx", format_mm(center[0], precision)),
                ("cy", format_mm(center[1], precision)),
                ("r", format_mm(diameter / 2.0, precision)),
            ),
            filled=True,
        )
    ]


def _zone_shapes(
    item: Zone, to_board: PointMap, precision: int, layer: str | None
) -> list[SvgShape]:
    """Emit a zone's FILLED copper for *layer*, or its outline when unfilled.

    A filled zone stores one ``(filled_polygon …)`` per layer, each carrying its
    own ``(layer …)``, so a multi-layer zone must be filtered to the layer being
    drawn. Handing back another layer's islands would put copper where there is
    none.

    An unfilled zone has only its outline. That is drawn as a stroked path so a
    preview shows the region a caller asked to see, and it cannot be mistaken for
    poured copper.
    """
    fills = [
        poly
        for poly in item.filled_polygons
        if layer is None or poly.layer == layer or not poly.layer
    ]
    if fills:
        shapes: list[SvgShape] = []
        for filled in fills:
            data = chain_path(filled.pts, to_board, precision, closed=True)
            if data:
                shapes.append(SvgShape("path", (("d", data),), filled=True))
        return shapes

    outlines: list[SvgShape] = []
    for outline in item.polygons:
        data = chain_path(outline.pts, to_board, precision, closed=True)
        if data:
            outlines.append(SvgShape("path", (("d", data),)))
    return outlines


def _pad_shapes(
    item: Pad,
    to_board: PointMap,
    precision: int,
    layer: str | None,
    margin: tuple[float, float] | None = None,
) -> list[SvgShape]:
    """Emit the copper of one pad on *layer*.

    A pad needs both of the things this function is handed. The POSITION is
    footprint-relative, so it needs the host map; the SHAPE is per-layer, because
    a padstack gives one pad a different shape on the front, the inner layers and
    the back.

    Without a layer there is nothing to resolve, so the pad's own top-level shape
    is used. That is the normal-padstack answer and the right default.

    :mod:`eedesigncore.render.pads` holds every rule. This function only turns
    the outline it returns into SVG.
    """
    outline = pad_outline(item, layer or "", to_board, margin=margin)
    shapes: list[SvgShape] = []

    if outline.compound_polygons:
        paths = []
        for polygon in outline.compound_polygons:
            if len(polygon) >= 3:
                paths.append(
                    f"M{_pt(polygon[0], precision)} "
                    + " ".join(f"L{_pt(point, precision)}" for point in polygon[1:])
                    + " Z"
                )
        if paths:
            shapes.append(
                SvgShape("path", (("d", " ".join(paths)), ("fill-rule", "evenodd")), filled=True)
            )

    for polygon in outline.polygons:
        if len(polygon) < 2:
            continue
        head = f"M{_pt(polygon[0], precision)}"
        tail = " ".join(f"L{_pt(point, precision)}" for point in polygon[1:])
        shapes.append(SvgShape("path", (("d", f"{head} {tail} Z"),), filled=True))

    for centre, radius in outline.discs:
        if radius <= 0.0:
            continue
        shapes.append(
            SvgShape(
                "circle",
                (
                    ("cx", format_mm(centre[0], precision)),
                    ("cy", format_mm(centre[1], precision)),
                    ("r", format_mm(radius, precision)),
                ),
                filled=True,
            )
        )

    for start, end, width in outline.strokes:
        shapes.append(
            SvgShape(
                "path",
                (("d", f"M{_pt(start, precision)} L{_pt(end, precision)}"),),
                stroke_width_mm=width,
            )
        )

    for start, mid, end, width in outline.arcs:
        shapes.append(
            SvgShape(
                "path",
                (("d", arc_path(start, mid, end, precision)),),
                stroke_width_mm=width,
            )
        )

    return shapes


def text_shapes(
    content: str,
    position: Point,
    angle: float,
    attributes: object | None,
    to_board: PointMap,
    precision: int,
    font: object | None = None,
    *,
    halign: str | None = None,
    valign: str | None = None,
) -> list[SvgShape]:
    """Emit stroke-font text as one path per pen-down run.

    KiCad plots text as strokes of the same pen, one path per run, and this
    reproduces those runs exactly. Verified against ``kicad-cli pcb export svg``
    on a board carrying one text item: 48 of 48 stroke segments matched to
    0.002 mm.

    *attributes* carries the size, pen, justification, mirror and slant. It is
    ``None`` on an item that never had an effects block, which draws at KiCad's
    1.27 mm default.
    """
    from eedesigncore.common.font import default_font

    if not content:
        return []
    from eedesigncore.render.fonts import OutlineFont

    if isinstance(font, OutlineFont):
        attrs = attributes if isinstance(attributes, TextAttributes) else TextAttributes()
        return [
            font.svg_shape(
                content,
                position,
                angle,
                attrs,
                precision,
                halign=halign,
                valign=valign,
                to_board=to_board,
            )
        ]
    if getattr(attributes, "font_face", None):
        raise ValueError("Named plot font requires an explicit loaded face.")
    metrics = font if font is not None else default_font()
    strokes = getattr(metrics, "text_strokes", None)
    if not callable(strokes):
        raise ValueError("Visible plot text requires a font with text_strokes.")

    size_mm = (1.27, 1.27)
    thickness_mm: float | None = None
    italic = mirror = False
    from_attrs_h = from_attrs_v = "center"
    if attributes is not None:
        if getattr(attributes, "font_size", None):
            size_mm = attributes.font_size  # type: ignore[attr-defined]
        thickness_mm = getattr(attributes, "font_thickness", None)
        italic = bool(getattr(attributes, "italic", False))
        mirror = bool(getattr(attributes, "mirror", False))
        from_attrs_h = getattr(getattr(attributes, "justify_h", None), "value", "center")
        from_attrs_v = getattr(getattr(attributes, "justify_v", None), "value", "center")
    halign = halign if halign is not None else from_attrs_h
    valign = valign if valign is not None else from_attrs_v

    size = point_to_iu(size_mm)
    thickness = point_to_iu((thickness_mm or 0.0, 0.0))[0]
    if thickness <= 1:
        from eedesigncore.common.font import effective_pen_width

        thickness = effective_pen_width(thickness, size)

    runs = strokes(
        content,
        size,
        point_to_iu(position),
        thickness,
        angle=angle,
        mirror=mirror,
        italic=italic,
        halign=halign,
        valign=valign,
    )

    out: list[SvgShape] = []
    for run in runs:
        points = [to_board((to_mm(x), to_mm(y))) for x, y in run]
        if len(points) < 2:
            continue
        steps = " ".join(
            f"{'M' if index == 0 else 'L'}{_pt(point, precision)}"
            for index, point in enumerate(points)
        )
        out.append(SvgShape("path", (("d", steps),)))
    return out


def _pcb_text_shapes(
    item: PcbText, to_board: PointMap, precision: int, font: object | None
) -> list[SvgShape]:
    """Emit a ``gr_text`` or ``fp_text`` item."""
    if not item.visible:
        return []
    return text_shapes(
        item.content, item.position, item.angle, item.attributes, to_board, precision, font
    )


def _field_shapes(
    item: Property, to_board: PointMap, precision: int, font: object | None
) -> list[SvgShape]:
    """Emit a footprint field, such as a reference designator.

    A hidden field draws nothing. A reference designator is the most visible
    thing on a silkscreen, so it was also the most visible absence.
    """
    if not item.visible:
        return []
    return text_shapes(
        item.value, item.position, item.angle, item.attributes, to_board, precision, font
    )


def _dimension_shapes(
    item: Dimension, to_board: PointMap, precision: int, font: object | None
) -> list[SvgShape] | None:
    """Emit a dimension's line work and its measurement text.

    The geometry comes from :mod:`eedesigncore.render.dimensions`, which rebuilds
    what KiCad computes in ``updateGeometry``.
    """
    from eedesigncore.render.dimensions import dimension_segments_mm

    segments = dimension_segments_mm(item, font)  # type: ignore[arg-type]
    if segments is None:
        return None
    out: list[SvgShape] = []
    for start, end in segments:
        a, b = to_board(start), to_board(end)
        out.append(SvgShape("path", (("d", f"M{_pt(a, precision)} L{_pt(b, precision)}"),)))

    # The measurement itself. KiCad centres a dimension's text on both axes
    # (``PCB_DIMENSION_BASE::updateText``) whatever the effects block says, so
    # the justification is fixed here rather than read from the attributes.
    text = item.text
    if text is not None and text.content and text.visible:
        out.extend(
            text_shapes(
                text.content,
                text.position,
                text.angle,
                text.attributes,
                to_board,
                precision,
                font,
                halign="center",
                valign="center",
            )
        )
    return out


def shapes_for(
    item: object,
    to_board: PointMap,
    precision: int = DEFAULT_PRECISION,
    layer: str | None = None,
    font: object | None = None,
    via_diameter: ViaDiameter = DEFAULT_VIA_DIAMETER,
    *,
    pad_margin: tuple[float, float] | None = None,
) -> list[SvgShape] | None:
    """Return the SVG shapes of *item*, or ``None`` when this layer cannot draw it.

    ``None`` and an empty list mean different things. ``None`` says the item type
    has no emitter, which the caller reports as a skip. An empty list says the
    emitter ran and the item draws nothing, such as a point of zero size.

    *layer* is the layer being drawn. Only a zone needs it, because a zone stores
    its fill per layer.
    """
    if isinstance(item, PcbLine):
        return _line_shapes(item, to_board, precision)
    if isinstance(item, PcbRect):
        return _rect_shapes(item, to_board, precision)
    if isinstance(item, PcbCircle):
        return _circle_shapes(item, to_board, precision)
    if isinstance(item, PcbArc):
        return _arc_shapes(item, to_board, precision)
    if isinstance(item, PcbPoly):
        return _poly_shapes(item, to_board, precision)
    if isinstance(item, PcbPoint):
        return _point_shapes(item, precision)
    if isinstance(item, TrackSegment):
        return _track_segment_shapes(item, to_board, precision)
    if isinstance(item, TrackArc):
        return _track_arc_shapes(item, to_board, precision)
    if isinstance(item, Via):
        return _via_shapes(item, to_board, precision, layer, via_diameter)
    if isinstance(item, Pad):
        return _pad_shapes(item, to_board, precision, layer, pad_margin)
    if isinstance(item, Zone):
        return _zone_shapes(item, to_board, precision, layer)
    if isinstance(item, Dimension):
        return _dimension_shapes(item, to_board, precision, font)
    if isinstance(item, PcbText):
        return _pcb_text_shapes(item, to_board, precision, font)
    if isinstance(item, Property):
        return _field_shapes(item, to_board, precision, font)
    return None


__all__ = [
    "DASH_RATIO",
    "DEFAULT_PRECISION",
    "DOT_RATIO",
    "GAP_RATIO",
    "Attrs",
    "Point",
    "PointMap",
    "STRAIGHT_TOLERANCE_IU",
    "STRAIGHT_TOLERANCE_MM",
    "SvgShape",
    "arc_center",
    "arc_flags",
    "arc_geometry",
    "arc_path",
    "chain_path",
    "format_mm",
    "hole_shapes",
    "is_straight",
    "oval_hole_path",
    "shapes_for",
    "stroke_attrs",
]


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------


def _rotated(point: Point, angle: float, about: Point) -> Point:
    """Turn *point* about *about* by *angle* degrees, on the nanometer grid.

    The arithmetic runs in internal units, as KiCad's does, so a quarter turn is
    exact and a hole lands on a coordinate KiCad could have written.
    """
    origin = point_to_iu(about)
    local = (point_to_iu(point)[0] - origin[0], point_to_iu(point)[1] - origin[1])
    turned = rotate_point_iu(local, angle)
    return (to_mm(turned[0] + origin[0]), to_mm(turned[1] + origin[1]))


def oval_hole_path(
    center: Point, width: float, height: float, angle: float, precision: int
) -> str:
    """Return the outline of a slot, as two straight sides and two true arcs.

    A slot is a rectangle capped by a half circle at each end. The caps are SVG
    ``A`` commands, never polygons, so the curve survives at any zoom.

    The short dimension gives the cap radius. The long one gives the straight
    run, which is the difference between the two.
    """
    if width >= height:
        radius = height / 2.0
        reach = (width - height) / 2.0
        corners = [
            (-reach, -radius),
            (reach, -radius),
            (reach, radius),
            (-reach, radius),
        ]
    else:
        radius = width / 2.0
        reach = (height - width) / 2.0
        corners = [
            (radius, -reach),
            (radius, reach),
            (-radius, reach),
            (-radius, -reach),
        ]

    placed = [_rotated((center[0] + x, center[1] + y), angle, center) for x, y in corners]
    radius_text = format_mm(radius, precision)
    arc = f"A{radius_text} {radius_text} 0 0 1"
    return (
        f"M{_pt(placed[0], precision)} "
        f"L{_pt(placed[1], precision)} "
        f"{arc} {_pt(placed[2], precision)} "
        f"L{_pt(placed[3], precision)} "
        f"{arc} {_pt(placed[0], precision)} Z"
    )


def hole_shapes(
    center: Point,
    width: float,
    height: float,
    angle: float,
    precision: int = DEFAULT_PRECISION,
) -> list[SvgShape]:
    """Return the shape of one hole, as a solid area.

    A hole is solid because KiCad plots a drill mark solid, to knock a real
    opening out of the pad beneath it (``plot_brditems_plotter.cpp:1279-1288``).
    The color of that area is still the policy layer's choice.

    A hole whose two dimensions match is a circle, whatever the file called it. A
    slot of zero length is a circle too, so the two cases cannot disagree.
    """
    if width <= 0.0 or height <= 0.0:
        return []

    if width == height:
        return [
            SvgShape(
                "circle",
                (
                    ("cx", format_mm(center[0], precision)),
                    ("cy", format_mm(center[1], precision)),
                    ("r", format_mm(width / 2.0, precision)),
                ),
                filled=True,
            )
        ]

    return [
        SvgShape(
            "path",
            (("d", oval_hole_path(center, width, height, angle, precision)),),
            filled=True,
        )
    ]
