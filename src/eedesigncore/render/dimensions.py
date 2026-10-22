"""Dimension line work, rebuilt the way KiCad rebuilds it.

A board file stores a dimension's measured points, its height and its style. It
does NOT store the lines. KiCad computes them in ``updateGeometry``, one
implementation per type (``pcbnew/pcb_dimension.cpp``), and the text takes part:
four of the five types cut a gap in their own line work where the text sits.

That is why this module needs a font. :mod:`eedesigncore.common.font` supplies
KiCad's own, and a caller may pass another.

Every function works in integer internal units and rounds through
:func:`kiround`, as the C++ does.

Source map:

    PCB_DIM_ALIGNED::updateGeometry      :951-1023
    PCB_DIM_ORTHOGONAL::updateGeometry   :1182-1268
    PCB_DIM_LEADER::updateGeometry       :1446-1520
    PCB_DIM_RADIAL::updateGeometry       :1661-1710
    PCB_DIM_CENTER::updateGeometry       :1830-1852
    drawAnArrow                          :356-374
    CollectKnockedOutSegments            :114-148
    segPolyIntersection                  :65-88
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from eedesigncore.common.font import TextBox, TextMetrics, default_font, effective_pen_width
from eedesigncore.common.kimath import kiround
from eedesigncore.common.pcb_family.board_items import Dimension, DimensionType
from eedesigncore.common.transform import point_to_iu, to_mm

PointIU = tuple[int, int]
SegmentIU = tuple[PointIU, PointIU]

#: Half-angle of an arrow head, ``pcb_dimension.cpp:54``.
ARROW_ANGLE_DEG = 27.5

#: An inward arrow grows a tail this many times the arrow length, ``:52``.
INWARD_ARROW_RATIO = 2

#: ``DIM_TEXT_POSITION``, ``pcb_dimension.h:61-66``. The file stores the ordinal.
TEXT_POSITION_OUTSIDE = 0
TEXT_POSITION_INLINE = 1
TEXT_POSITION_MANUAL = 2

#: ``DIM_ARROW_DIRECTION``. Inward is 1 in the file, matching the writer.
ARROW_DIRECTION_INWARD = "inward"


# ---------------------------------------------------------------------------
# Vector helpers, mirroring the VECTOR2I operations the C++ uses
# ---------------------------------------------------------------------------


def _resize(vector: PointIU, length: int) -> PointIU:
    """``VECTOR2I::Resize``: same direction, the given length, integer result."""
    current = math.hypot(float(vector[0]), float(vector[1]))
    if current == 0.0:
        return (0, 0)
    scale = length / current
    return (kiround(vector[0] * scale), kiround(vector[1] * scale))


def _add(a: PointIU, b: PointIU) -> PointIU:
    return (a[0] + b[0], a[1] + b[1])


def _subtract(a: PointIU, b: PointIU) -> PointIU:
    return (a[0] - b[0], a[1] - b[1])


def _rotate(vector: PointIU, degrees: float) -> PointIU:
    """``RotatePoint`` about the origin, by a POSITIVE angle in KiCad's sense.

    KiCad's y axis grows downward, and ``RotatePoint`` turns clockwise on screen
    for a positive angle. The signs here match ``trigo.cpp``.
    """
    radians = math.radians(degrees)
    sin, cos = math.sin(radians), math.cos(radians)
    return (
        kiround(vector[0] * cos + vector[1] * sin),
        kiround(-vector[0] * sin + vector[1] * cos),
    )


def _angle_of(vector: PointIU) -> float:
    """``EDA_ANGLE( const VECTOR2I& )`` in degrees, over the full circle."""
    return math.degrees(math.atan2(float(vector[1]), float(vector[0])))


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


# ---------------------------------------------------------------------------
# Knockout: remove the part of a segment the text box covers
# ---------------------------------------------------------------------------


def _seg_intersection(
    polygon: tuple[PointIU, ...], segment: SegmentIU, from_start: bool = True
) -> PointIU | None:
    """``segPolyIntersection`` (``:65-88``): first crossing from one end.

    Returns None when that end sits INSIDE the polygon, because then the whole
    run from that end is covered.
    """
    start = segment[0] if from_start else segment[1]
    endpoint = segment[1] if from_start else segment[0]

    if _contains(polygon, start):
        return None

    best = endpoint
    for index in range(len(polygon)):
        edge = (polygon[index], polygon[(index + 1) % len(polygon)])
        crossing = _segment_crossing(edge, segment)
        if crossing is None:
            continue
        if _squared(crossing, start) < _squared(best, start):
            best = crossing

    return None if best == start else best


def _squared(a: PointIU, b: PointIU) -> int:
    dx, dy = a[0] - b[0], a[1] - b[1]
    return dx * dx + dy * dy


def _segment_crossing(first: SegmentIU, second: SegmentIU) -> PointIU | None:
    """``SEG::Intersect`` for two closed segments, or None."""
    (x1, y1), (x2, y2) = first
    (x3, y3), (x4, y4) = second
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    denominator = d1x * d2y - d1y * d2x
    if denominator == 0:
        return None
    ex, ey = x3 - x1, y3 - y1
    t = (ex * d2y - ey * d2x) / denominator
    u = (ex * d1y - ey * d1x) / denominator
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    return (kiround(x1 + t * d1x), kiround(y1 + t * d1y))


def _contains(polygon: tuple[PointIU, ...], point: PointIU) -> bool:
    """``SHAPE_POLY_SET::Contains``, by the crossing-number rule."""
    inside = False
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        if (ay > point[1]) != (by > point[1]):
            span = by - ay
            if span:
                crossing = ax + (point[1] - ay) * (bx - ax) / span
                if point[0] < crossing:
                    inside = not inside
    return inside


def knocked_out(polygon: tuple[PointIU, ...], segment: SegmentIU) -> list[SegmentIU]:
    """``CollectKnockedOutSegments`` (``:114-148``): 0, 1 or 2 pieces.

    A crossbar with the text in its middle becomes two pieces. One with the text
    over an end becomes one. One fully covered becomes none. One clear of the text
    survives whole.
    """
    contains_a = _contains(polygon, segment[0])
    contains_b = _contains(polygon, segment[1])
    end_a = _seg_intersection(polygon, segment, True)
    end_b = _seg_intersection(polygon, segment, False)

    out: list[SegmentIU] = []
    if end_a is not None:
        out.append((segment[0], end_a))

    if end_b is not None:
        can_add = True
        if end_a is not None and (
            (end_b == segment[0] and end_a == segment[1])
            or (end_a == end_b and segment[0] == segment[1])
        ):
            can_add = False
        if can_add:
            out.append((end_b, segment[1]))

    if not contains_a and not contains_b and end_a is None and end_b is None:
        out.append(segment)

    return out


# ---------------------------------------------------------------------------
# Arrows
# ---------------------------------------------------------------------------


def arrow_segments(
    start: PointIU, angle_deg: float, arrow_length: int, tail: int
) -> list[SegmentIU]:
    """``drawAnArrow`` (``:356-374``): two head strokes, plus a tail when asked."""
    out: list[SegmentIU] = []
    if tail:
        end = _rotate((tail, 0), -angle_deg)
        out.append((start, (start[0] + end[0], start[1] + end[1])))

    for offset in (ARROW_ANGLE_DEG, -ARROW_ANGLE_DEG):
        head = _rotate((arrow_length, 0), -angle_deg + offset)
        out.append((start, (start[0] + head[0], start[1] + head[1])))
    return out


# ---------------------------------------------------------------------------
# The text box, placed and turned the way the dimension places it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacedText:
    """The text box of a dimension, as a polygon in board internal units."""

    polygon: tuple[PointIU, ...]
    center: PointIU
    box: TextBox


def _text_polygon(
    dimension: Dimension,
    position: PointIU,
    angle_deg: float,
    font: TextMetrics,
    inflate_y_sign: int,
) -> PlacedText | None:
    """Build the collision polygon KiCad collides the line work against.

    ``PCB_DIM_ALIGNED`` inflates by ``(GetTextWidth() / 2, -pen)`` at ``:994``,
    and every other type inflates by ``(GetTextWidth() / 2, +pen)``. The sign is
    the only difference, so it arrives as *inflate_y_sign*.

    The box is built at the origin, centred on the text position, then turned by
    the text angle about its own centre (``:1002``).
    """
    text = dimension.text
    if text is None or not text.content:
        return None

    attributes = text.attributes
    # 1.27 mm is KiCad's default text size, and a dimension whose effects block
    # omits the size really does draw at that size.
    size_mm = (1.27, 1.27)
    thickness_mm: float | None = None
    bold = italic = False
    if attributes is not None:
        if attributes.font_size:
            size_mm = attributes.font_size
        thickness_mm = attributes.font_thickness
        bold = bool(attributes.bold)
        italic = bool(attributes.italic)

    size = point_to_iu(size_mm)
    thickness = point_to_iu((thickness_mm or 0.0, 0.0))[0]
    pen = effective_pen_width(thickness, size, bold=bold)

    box = font.text_box(text.content, size, thickness, bold=bold, italic=italic)

    # KiCad's box is placed by justification; a dimension's text is centred on
    # both axes (``PCB_DIMENSION_BASE::updateText`` sets CENTER/CENTER), so the
    # placed box is the unplaced one centred on the text position.
    half_w, half_h = box.width // 2, box.height // 2
    placed = TextBox(
        x=position[0] - half_w, y=position[1] - half_h, width=box.width, height=box.height
    )
    grown = placed.inflate(size[0] // 2, inflate_y_sign * pen)

    center = (grown.x + grown.width // 2, grown.y + grown.height // 2)
    corners = tuple(
        (
            center[0] + _rotate((corner[0] - center[0], corner[1] - center[1]), angle_deg)[0],
            center[1] + _rotate((corner[0] - center[0], corner[1] - center[1]), angle_deg)[1],
        )
        for corner in grown.corners()
    )
    return PlacedText(polygon=corners, center=center, box=grown)


# ---------------------------------------------------------------------------
# Per-type geometry
# ---------------------------------------------------------------------------


def _points_iu(dimension: Dimension) -> list[PointIU]:
    return [point_to_iu(p) for p in dimension.pts if isinstance(p, tuple) and len(p) == 2]


def _style(dimension: Dimension) -> tuple[int, int, int]:
    """Return arrow length, extension offset and extension height, in units."""
    style = dimension.style
    arrow = point_to_iu((style.arrow_length, 0.0))[0]
    offset = point_to_iu((style.extension_offset, 0.0))[0]
    height = point_to_iu(((style.extension_height or 0.0), 0.0))[0]
    return arrow, offset, height


def _aligned(dimension: Dimension, font: TextMetrics, orthogonal: bool) -> list[SegmentIU] | None:
    """``PCB_DIM_ALIGNED`` (``:951-1023``) and ``PCB_DIM_ORTHOGONAL`` (``:1182``).

    Orthogonal differs in how the crossbar direction is chosen: it projects onto
    one axis rather than running perpendicular to the measured span. Everything
    after that, including the knockout, is shared.
    """
    points = _points_iu(dimension)
    if len(points) < 2:
        return None
    start, end = points[0], points[1]
    arrow_length, extension_offset, extension_height = _style(dimension)
    height = point_to_iu(((dimension.height or 0.0), 0.0))[0]

    span = (end[0] - start[0], end[1] - start[1])

    if orthogonal:
        # ``:1194-1229``. The two extensions are NOT symmetric, and that is the
        # whole character of this type. The first runs the full height from the
        # start point. The second runs only the residual gap between the end
        # point and the crossbar, and it is anchored from the crossbar END and
        # pulled back by the extension HEIGHT rather than the offset.
        horizontal = (dimension.orientation or 0) == 0
        extension = (0, height) if horizontal else (height, 0)

        reach = abs(height) - extension_offset + extension_height
        base = _add(start, _resize(extension, extension_offset))
        segments = [(base, _add(base, _resize(extension, reach)))]

        crossbar_offset = _resize(extension, height)
        signed = _sign(height)
        crossbar_offset = (crossbar_offset[0] * signed, crossbar_offset[1] * signed)
        crossbar_start = _add(start, crossbar_offset)
        crossbar_end = (end[0], crossbar_start[1]) if horizontal else (crossbar_start[0], end[1])

        residual = (0, end[1] - crossbar_end[1]) if horizontal else (end[0] - crossbar_end[0], 0)
        residual_reach = (
            kiround(math.hypot(float(residual[0]), float(residual[1])))
            - extension_offset
            + extension_height
        )
        second = _subtract(crossbar_end, _resize(residual, extension_height))
        segments.append((second, _add(second, _resize(residual, residual_reach))))
    else:
        # ``:964-995``: the extension is perpendicular to the span, on the side
        # the sign of `height` names.
        extension = (-span[1], span[0]) if height > 0 else (span[1], -span[0])

        reach = abs(height) - extension_offset + extension_height
        segments = []
        for anchor in (start, end):
            offset = _resize(extension, extension_offset)
            base = (anchor[0] + offset[0], anchor[1] + offset[1])
            grow = _resize(extension, reach)
            segments.append((base, (base[0] + grow[0], base[1] + grow[1])))

        crossbar_offset = _resize(extension, height)
        signed = _sign(height)
        crossbar_offset = (crossbar_offset[0] * signed, crossbar_offset[1] * signed)
        crossbar_start = (start[0] + crossbar_offset[0], start[1] + crossbar_offset[1])
        crossbar_end = (end[0] + crossbar_offset[0], end[1] + crossbar_offset[1])

    crossbar = (crossbar_start, crossbar_end)
    # Aligned inflates the box by a NEGATIVE pen on y (``:994``); every other
    # type inflates by a positive one (``:1236``, ``:1459``, ``:1687``).
    inflate_sign = 1 if orthogonal else -1
    placed = _placed_for_aligned(dimension, crossbar, font, inflate_sign)
    if placed is None:
        segments.append(crossbar)
    else:
        segments.extend(knocked_out(placed.polygon, crossbar))

    # Aligned turns its arrows by the MEASURED span (``:1067-1077``) and
    # orthogonal by the CROSSBAR (``:1251-1263``). On an orthogonal dimension the
    # two differ whenever the span is not already axis-aligned, which is the
    # normal case, so using the span would splay both arrow heads.
    if orthogonal:
        arrow_angle = _angle_of(_subtract(crossbar_end, crossbar_start))
    else:
        arrow_angle = _angle_of(span)

    inward = (dimension.style.arrow_direction or "").lower() == ARROW_DIRECTION_INWARD
    if inward:
        tail = arrow_length * INWARD_ARROW_RATIO
        segments += arrow_segments(crossbar_start, arrow_angle + 180.0, arrow_length, tail)
        segments += arrow_segments(crossbar_end, arrow_angle, arrow_length, tail)
    else:
        segments += arrow_segments(crossbar_start, arrow_angle, arrow_length, 0)
        segments += arrow_segments(crossbar_end, arrow_angle + 180.0, arrow_length, 0)

    return segments


def _placed_for_aligned(
    dimension: Dimension, crossbar: SegmentIU, font: TextMetrics, inflate_y_sign: int
) -> PlacedText | None:
    """Place the text box for an aligned or orthogonal dimension.

    The text position comes from the file when the mode is MANUAL, and otherwise
    from the crossbar: OUTSIDE puts it beside the crossbar centre and INLINE puts
    it on the centre (``:1026-1052``).
    """
    text = dimension.text
    if text is None:
        return None

    mode = dimension.style.text_position_mode
    half = ((crossbar[1][0] - crossbar[0][0]) // 2, (crossbar[1][1] - crossbar[0][1]) // 2)

    if mode == TEXT_POSITION_INLINE:
        position = (crossbar[0][0] + half[0], crossbar[0][1] + half[1])
    else:
        # OUTSIDE and MANUAL both end up with a stored text position: MANUAL
        # because the user set it, OUTSIDE because KiCad wrote back what it
        # computed. Trusting the file is exact for both and needs no font.
        position = point_to_iu(text.position)

    angle = text.angle if dimension.style.keep_text_aligned or text.angle else 0.0
    return _text_polygon(dimension, position, angle, font, inflate_y_sign)


def _center(dimension: Dimension) -> list[SegmentIU] | None:
    """``PCB_DIM_CENTER::updateGeometry`` (``:1830-1852``).

    A cross of two arms. This is the ONE type that never touches the text box,
    and it carries no text at all.
    """
    points = _points_iu(dimension)
    if len(points) < 2:
        return None
    center, end = points[0], points[1]
    arm = (end[0] - center[0], end[1] - center[1])
    turned = _rotate(arm, -90.0)
    return [
        ((center[0] - arm[0], center[1] - arm[1]), (center[0] + arm[0], center[1] + arm[1])),
        (
            (center[0] - turned[0], center[1] - turned[1]),
            (center[0] + turned[0], center[1] + turned[1]),
        ),
    ]


def _radial(dimension: Dimension, font: TextMetrics) -> list[SegmentIU] | None:
    """``PCB_DIM_RADIAL::updateGeometry`` (``:1661-1710``).

    Two legs, both knocked out by the text: the arrow leg from the centre to the
    measured point, and the text leg from that point to the text.
    """
    points = _points_iu(dimension)
    if len(points) < 2:
        return None
    center, end = points[0], points[1]
    arrow_length, _offset, _height = _style(dimension)
    leader = point_to_iu(((dimension.leader_length or 0.0), 0.0))[0]

    radial = (end[0] - center[0], end[1] - center[1])
    text = dimension.text
    if text is None:
        return [(center, end)]

    text_pos = point_to_iu(text.position)
    placed = _text_polygon(dimension, text_pos, text.angle, font, 1)

    arrow_leg = (center, end)
    text_leg = (end, text_pos)
    segments: list[SegmentIU] = []
    if placed is None:
        segments.extend([arrow_leg, text_leg])
    else:
        segments.extend(knocked_out(placed.polygon, arrow_leg))
        segments.extend(knocked_out(placed.polygon, text_leg))

    if leader:
        segments += arrow_segments(end, _angle_of(radial), arrow_length, 0)
    else:
        segments += arrow_segments(end, _angle_of(radial), arrow_length, 0)
    return segments


def _leader(dimension: Dimension, font: TextMetrics) -> list[SegmentIU] | None:
    """``PCB_DIM_LEADER::updateGeometry`` (``:1446-1520``).

    One line from the measured point to the text, with an arrow at the point.
    The line stops where the text box begins.
    """
    points = _points_iu(dimension)
    if len(points) < 2:
        return None
    start, end = points[0], points[1]
    arrow_length, extension_offset, _height = _style(dimension)

    first = (end[0] - start[0], end[1] - start[1])
    offset = _resize(first, extension_offset)
    origin = (start[0] + offset[0], start[1] + offset[1])

    text = dimension.text
    placed = (
        _text_polygon(dimension, point_to_iu(text.position), text.angle, font, 2)
        if text is not None
        else None
    )

    arrow_leg = (start, end)
    segments: list[SegmentIU] = []
    if placed is None:
        segments.append((origin, end))
    else:
        pieces = knocked_out(placed.polygon, arrow_leg)
        # ``:1487-1490``: the drawn line runs from `start` (plus the offset) to
        # the first crossing, and falls back to `end` when there is none.
        stop = pieces[0][1] if pieces else end
        segments.append((origin, stop))

    segments += arrow_segments(origin, _angle_of(first), arrow_length, 0)

    if placed is not None and text is not None and text.content:
        text_leg = (end, point_to_iu(text.position))
        pieces = knocked_out(placed.polygon, text_leg)
        if pieces:
            segments.append(pieces[0])
    return segments


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def dimension_segments_mm(
    dimension: Dimension, font: TextMetrics | None = None
) -> list[tuple[tuple[float, float], tuple[float, float]]] | None:
    """Return the line work of *dimension* in millimetres, or None.

    None means the type has no port yet, which the caller reports as a skip.

    *font* measures the text, which decides where the line work is cut. The
    default is KiCad's own stroke font, which is what a board's text uses unless
    it names an installed face this library cannot read.
    """
    metrics = font if font is not None else default_font()

    if dimension.dim_type in (DimensionType.aligned, DimensionType.orthogonal):
        segments = _aligned(dimension, metrics, dimension.dim_type is DimensionType.orthogonal)
    elif dimension.dim_type is DimensionType.center:
        segments = _center(dimension)
    elif dimension.dim_type is DimensionType.radial:
        segments = _radial(dimension, metrics)
    elif dimension.dim_type is DimensionType.leader:
        segments = _leader(dimension, metrics)
    else:
        return None

    if segments is None:
        return None
    return [
        ((to_mm(a[0]), to_mm(a[1])), (to_mm(b[0]), to_mm(b[1]))) for a, b in segments if a != b
    ]


__all__ = [
    "ARROW_ANGLE_DEG",
    "INWARD_ARROW_RATIO",
    "TEXT_POSITION_INLINE",
    "TEXT_POSITION_MANUAL",
    "TEXT_POSITION_OUTSIDE",
    "PlacedText",
    "arrow_segments",
    "dimension_segments_mm",
    "knocked_out",
]
