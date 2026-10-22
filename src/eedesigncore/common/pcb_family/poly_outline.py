"""Rebuild the polygon outline KiCad holds in memory for an ``fp_poly``.

``FOOTPRINT::cmp_drawings`` sorts two polygons on their vertex COUNT, then on
each vertex in turn (``pcbnew/footprint.cpp:4358-4371``). Those vertices are not
the ``(pts …)`` elements from the file. An ``(arc …)`` element becomes an
interpolated run of many points before it ever reaches the comparator.

ADR-061 assumed that run could not be reproduced from the file, so the save
order comparator tied every polygon holding an arc. It then fell through to a
UUID, which orders two unlike polygons wrongly. The 2026-09-04 polygon vertex
audit disproved the assumption: the interpolation error is a compile-time
constant, so the run IS derivable.

Constants, all verified in source:

- ``ARC_HIGH_DEF_MM`` is 0.005 mm, so ``ARC_HIGH_DEF`` is 5000 IU
  (``include/base_units.h:119,129``).
- ``getArcPolygonizationMaxError`` divides that by five, giving 1000 IU
  (``libs/kimath/src/geometry/shape_line_chain.cpp:60-65``).
- ``MIN_SEGCOUNT_FOR_CIRCLE`` is 8, which caps the arc increment at 45 degrees
  (``libs/kimath/src/geometry/geometry_utils.cpp:40,58``).

One documented approximation. ``SEG::Distance`` returns a rounded int, and this
port compares a double against the same threshold. The two disagree only when a
mid point sits within one internal unit of the 500 unit degeneracy threshold.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from eedesigncore.common.arcs import PointIU
from eedesigncore.common.kimath import calc_arc_center_iu, int32, kiround

# ``getArcPolygonizationMaxError()``: ARC_HIGH_DEF / 5.
POLY_MAX_ERROR_IU = 1000

# ``MIN_SEGCOUNT_FOR_CIRCLE``, which caps the arc increment at 360 / 8.
MIN_SEGCOUNT_FOR_CIRCLE = 8


def _angle_of(delta_x: float, delta_y: float) -> float:
    """``EDA_ANGLE( const VECTOR2D& )`` (``geometry/eda_angle.h:72-109``).

    The exact branches matter. They return a decimal degree value the general
    ``atan2`` branch would only approximate.
    """
    if delta_x == 0.0 and delta_y == 0.0:
        return 0.0
    if delta_y == 0.0:
        return 0.0 if delta_x >= 0.0 else -180.0
    if delta_x == 0.0:
        return 90.0 if delta_y >= 0.0 else -90.0
    if delta_x == delta_y:
        return 45.0 if delta_x >= 0.0 else -135.0
    if delta_x == -delta_y:
        return -45.0 if delta_x >= 0.0 else 135.0
    return math.degrees(math.atan2(delta_y, delta_x))


def _normalize(degrees: float) -> float:
    """``EDA_ANGLE::Normalize``: bring an angle into ``[0, 360)``."""
    value = degrees
    while value < -0.0:
        value += 360.0
    while value >= 360.0:
        value -= 360.0
    return value


def is_ccw(start: PointIU, mid: PointIU, end: PointIU) -> bool:
    """``SHAPE_ARC::IsCCW`` (``libs/kimath/include/geometry/shape_arc.h:314-321``).

    The cross product runs in 64-bit, so it does not wrap the way an int
    subtraction would.
    """
    v1x, v1y = end[0] - mid[0], end[1] - mid[1]
    v2x, v2y = start[0] - mid[0], start[1] - mid[1]
    return (v1x * v2y - v1y * v2x) > 0


def central_angle(start: PointIU, mid: PointIU, end: PointIU, center: PointIU) -> float:
    """``SHAPE_ARC::GetCentralAngle`` (``shape_arc.cpp:945-975``).

    Equal ends mean a full circle, not a zero sweep. The winding needs all three
    points, because two different arcs share a centre and a pair of ends.
    """
    if start == end:
        return 360.0
    angle = _angle_of(end[0] - center[0], end[1] - center[1]) - _angle_of(
        start[0] - center[0], start[1] - center[1]
    )
    if is_ccw(start, mid, end):
        if angle < 0.0:
            angle += 360.0
    elif angle > 0.0:
        angle -= 360.0
    return angle


def arc_to_segment_count(radius_iu: int, error_max: int, arc_angle: float) -> int:
    """``GetArcToSegmentCount`` (``geometry_utils.cpp:42-64``).

    The radius arrives as a C++ ``int``, so the caller truncates its double.
    """
    radius = max(1, radius_iu)
    error = max(1, error_max)
    rel_error = error / radius
    arc_increment = 180.0 / math.pi * math.acos(1.0 - rel_error) * 2
    arc_increment = min(360.0 / MIN_SEGCOUNT_FOR_CIRCLE, arc_increment)
    seg_count = kiround(abs(arc_angle) / arc_increment)
    return max(seg_count, 2)


def circle_to_end_segment_delta_radius(radius_iu: int, seg_count: int) -> int:
    """``CircleToEndSegmentDeltaRadius`` (``geometry_utils.cpp:67-83``)."""
    count = 3 if seg_count <= 2 else seg_count
    alpha = math.pi / count
    return kiround(abs(radius_iu * (1 - 1 / math.cos(alpha))))


def _point_to_segment_distance(a: PointIU, b: PointIU, p: PointIU) -> float:
    """Distance from *p* to segment *a*-*b*, as ``SEG::Distance`` measures it."""
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    px, py = float(p[0]), float(p[1])
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def arc_to_polyline_iu(
    start: PointIU,
    mid: PointIU,
    end: PointIU,
    width_iu: int = 0,
    max_error: int = POLY_MAX_ERROR_IU,
) -> list[PointIU]:
    """``SHAPE_ARC::ConvertToPolyline`` (``shape_arc.cpp:981-1045``).

    Returns the interpolated run, start and end included, with a point dropped
    when it repeats the point before it.
    """
    center = calc_arc_center_iu(start, mid, end)
    radius = math.hypot(float(start[0] - center[0]), float(start[1] - center[1]))
    start_angle = _normalize(_angle_of(start[0] - center[0], start[1] - center[1]))
    sweep = central_angle(start, mid, end, center)

    half_max_error = max(1.0, max_error / 2.0)
    external_radius = radius + (width_iu / 2.0)

    if (
        external_radius < half_max_error
        or _point_to_segment_distance(start, end, mid) < half_max_error
    ):
        # A very rare case. One segment carries the whole arc.
        count = 0
        effective_error: float = external_radius
    else:
        count = arc_to_segment_count(int(external_radius), max_error, sweep)
        seg360 = int(count * 360.0 / abs(sweep))
        effective_error = circle_to_end_segment_delta_radius(int(external_radius), seg360)

    # Both ends must land exactly on the arc, so the error is split either side.
    radius += effective_error / 2
    steps = count * 2

    run: list[PointIU] = [start]
    for i in range(1, steps, 2):
        angle = start_angle + (sweep * i) / steps if steps else start_angle
        radians = math.radians(angle)
        point = (
            kiround(center[0] + radius * math.cos(radians)),
            kiround(center[1] + radius * math.sin(radians)),
        )
        if point != run[-1]:
            run.append(point)
    if end != run[-1]:
        run.append(end)
    return run


def polygon_outline_iu(
    elements: list[object], to_iu: Callable[[tuple[float, float]], PointIU]
) -> list[PointIU] | None:
    """Rebuild the vertex list KiCad would hold for one ``(pts …)`` chain.

    *elements* is the chain in file order. *to_iu* converts one file point to
    board internal units, so a footprint-owned shape can pass its own transform.

    Returns None when an element is not a point or an arc, because a count from
    a partly understood chain would order two polygons on a guess.
    """
    from eedesigncore.common.models import PtsArc

    outline: list[PointIU] = []
    for element in elements:
        if isinstance(element, PtsArc):
            run = arc_to_polyline_iu(to_iu(element.start), to_iu(element.mid), to_iu(element.end))
            # Append( const SHAPE_LINE_CHAIN& ) drops a leading duplicate
            # (``shape_line_chain.cpp:1576-1582``).
            if outline and run and run[0] == outline[-1]:
                run = run[1:]
            outline.extend(run)
            # The outline is closed, so a first/last repeat loses the last point
            # (``shape_line_chain.cpp:218-235``). This merge runs on the arc
            # path only, which is why a trailing plain point keeps its twin.
            if len(outline) > 1 and outline[0] == outline[-1]:
                outline.pop()
        elif isinstance(element, tuple) and len(element) == 2:
            point = to_iu(element)
            # Duplicate suppression compares the last point only
            # (``shape_line_chain.h:538-549``).
            if not outline or point != outline[-1]:
                outline.append(point)
        else:
            return None
    return outline


def wrapped_point(point: PointIU) -> PointIU:
    """Wrap a point into the signed 32-bit range a ``VECTOR2I`` holds."""
    return (int32(point[0]), int32(point[1]))
