"""Port of the KiCad arc winding normalisation that runs at LOAD time.

``EDA_SHAPE::SetArcGeometry`` (``common/eda_shape.cpp:1180-1205``) recomputes the
arc centre from the three stored points, recomputes the mid point from the centre,
and SWAPS start with end when the input winding disagrees with the internal
winding. Every S-expression parser that builds an ``EDA_SHAPE`` arc calls it:

* ``sch_io_kicad_sexpr_parser.cpp:1357`` - a library symbol arc
* ``sch_io_kicad_sexpr_parser.cpp:4338`` - a schematic arc
* ``pcb_io_kicad_sexpr_parser.cpp:3281`` - ``gr_arc``, ``fp_arc`` and the
  ``gr_arc`` primitive of a custom pad, all through ``parsePCB_SHAPE``

Two arc kinds do NOT pass through it, so they are not normalised anywhere in this
library: a track ``(arc …)``, which is a ``PCB_ARC`` and takes its three points
through plain setters (``pcb_io_kicad_sexpr_parser.cpp:7195-7215``), and an
``(arc …)`` element inside a ``(pts …)`` chain, which becomes a ``SHAPE_ARC``
inside a ``SHAPE_LINE_CHAIN``.

The writers emit ``GetStart()``, ``GetArcMid()`` and ``GetEnd()``
(``sch_io_kicad_sexpr_common.cpp:232-236``, ``pcb_io_kicad_sexpr.cpp:1024-1027``),
so the swap reaches the file. A KiCad-written arc is already normalised, which
makes this port the identity on every KiCad-written file. It only changes an arc
whose winding a KiCad resave would rewrite anyway (ADR-067).
"""

from __future__ import annotations

import math

from eedesigncore.common.kimath import (
    calc_arc_center_iu,
    int32,
    kiround,
    kiround_clamped,
    rotate_point_about_iu,
)

Point = tuple[float, float]
PointIU = tuple[int, int]


# Both helpers moved to ``common/kimath.py`` when the transform work needed
# them (iteration 14). The names stay, so the callers below read unchanged.
_kiround_int = kiround_clamped
_rotate_about_iu = rotate_point_about_iu


def _angle_of_vector(delta_x: float, delta_y: float) -> float:
    """Port of ``EDA_ANGLE( const VECTOR2D& )`` (``geometry/eda_angle.h:72-109``).

    The five exact branches matter: they return a decimal degree value that the
    general ``atan2`` branch would only approximate, and the quadrant branches of
    ``RotatePoint`` then take their exact path.
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


def arc_angle_degrees(start: PointIU, end: PointIU, center: PointIU) -> float:
    """Port of ``EDA_SHAPE::GetArcAngle`` (``common/eda_shape.cpp:1219-1227``).

    ``CalcArcAngles`` (``:1128-1141``) raises the end angle above the start angle,
    so the result is the sweep of the internal winding and never negative.
    """
    # CalcArcAngles builds each radial with a VECTOR2I subtraction, so both wrap.
    start_angle = _angle_of_vector(int32(start[0] - center[0]), int32(start[1] - center[1]))
    end_angle = _angle_of_vector(int32(end[0] - center[0]), int32(end[1] - center[1]))
    if end_angle == start_angle:
        end_angle = start_angle + 360.0
    while end_angle < start_angle:
        end_angle += 360.0
    return end_angle - start_angle


def arc_mid_iu(start: PointIU, end: PointIU, center: PointIU) -> PointIU:
    """Port of the CALCULATED branch of ``EDA_SHAPE::GetArcMid`` (``:1115-1125``).

    The mid point is the start rotated about the centre by half the negated arc
    angle. The caller decides when the cached branch applies.
    """
    angle = arc_angle_degrees(start, end, center)
    return _rotate_about_iu(start, center, -angle / 2.0)


def _squared_norm(point: PointIU) -> float:
    """``VECTOR2D::SquaredEuclideanNorm``, evaluated in double as C++ does."""
    x, y = float(point[0]), float(point[1])
    return x * x + y * y


def arc_winding_swaps(start: PointIU, mid: PointIU, end: PointIU) -> bool:
    """True when ``SetArcGeometry`` would swap the ends of this arc.

    Source: ``common/eda_shape.cpp:1194-1205``. The calculated mid lands on the
    far side of the arc when the input winding disagrees, so KiCad measures the
    calculated mid against the given mid and against the centre.
    """
    center = calc_arc_center_iu(start, mid, end)
    zeroed_cache_hit = start == (0, 0) and end == (0, 0) and center == (0, 0)
    # SetArcGeometry clears m_arcMidData before it calls GetArcMid, so the cached
    # branch fires only for an arc that is degenerate at the origin.
    new_mid = (0, 0) if zeroed_cache_hit else arc_mid_iu(start, end, center)
    # Both differences are VECTOR2I subtractions before the VECTOR2D promotion,
    # so both wrap. A degenerate arc, whose centre sits on the int rail, needs it.
    to_mid = (int32(new_mid[0] - mid[0]), int32(new_mid[1] - mid[1]))
    to_center = (int32(new_mid[0] - center[0]), int32(new_mid[1] - center[1]))
    return _squared_norm(to_mid) > _squared_norm(to_center)


def normalize_arc_iu(
    start: PointIU, mid: PointIU, end: PointIU
) -> tuple[PointIU, PointIU, PointIU]:
    """Return the arc as ``SetArcGeometry`` would store it, then write it back out.

    An arc whose winding already agrees is returned unchanged. An arc whose
    winding disagrees comes back with its ends swapped and its mid recomputed,
    because the swap invalidates the mid cache and the writer calls ``GetArcMid``
    (``common/eda_shape.cpp:1119-1120``).
    """
    if not arc_winding_swaps(start, mid, end):
        return (start, mid, end)
    center = calc_arc_center_iu(start, mid, end)
    if start == end:
        # The cache keys still match after the swap, so KiCad emits the stored mid.
        return (end, mid, start)
    return (end, arc_mid_iu(end, start, center), start)


def normalize_arc_mm(
    start: Point,
    mid: Point,
    end: Point,
    *,
    iu_per_mm: float,
    invert_y: bool = False,
) -> tuple[Point, Point, Point]:
    """Apply :func:`normalize_arc_iu` to three millimetre points from a file.

    *invert_y* is required for a LIBRARY SYMBOL arc, whose file Y is the negated
    internal Y (``sch_io_kicad_sexpr_lib_cache.cpp:619-621``). A mirror reverses
    winding, so the flag decides the outcome rather than decorating it.

    An arc that needs no swap comes back as the exact float values that were
    passed in, so a round trip of a KiCad-written file never re-renders a
    coordinate. Only the recomputed mid of a swapped arc is converted back.
    """
    sign = -1.0 if invert_y else 1.0

    def to_iu(point: Point) -> PointIU:
        return (kiround(point[0] * iu_per_mm), kiround(sign * point[1] * iu_per_mm))

    def to_mm(point: PointIU) -> Point:
        return (point[0] / iu_per_mm, sign * point[1] / iu_per_mm)

    start_iu, mid_iu, end_iu = to_iu(start), to_iu(mid), to_iu(end)
    if not arc_winding_swaps(start_iu, mid_iu, end_iu):
        return (start, mid, end)
    _, new_mid_iu, _ = normalize_arc_iu(start_iu, mid_iu, end_iu)
    new_mid = mid if new_mid_iu == mid_iu else to_mm(new_mid_iu)
    return (end, new_mid, start)
