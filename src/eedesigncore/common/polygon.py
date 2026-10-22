"""Turn a rounded or chamfered rectangle into the polygon KiCad plots.

KiCad does not draw a rounded rectangle as arcs. Every plot path except Gerber
converts the shape to a polygon first and emits straight segments, so a roundrect
pad in a ``kicad-cli`` SVG is a 32-vertex path rather than four lines and four
arcs. A renderer that draws true arcs would be geometrically *better* and would
still disagree with KiCad on every vertex, which costs the parity oracle its
only pad check. So this module ports the approximation instead of improving on
it.

Sources, all at KiCad 10.0.3:

- ``TransformRoundChamferedRectToPolygon``, ``convert_basic_shapes_to_polygon.cpp
  :459-522``. Builds four corners, splits a chamfered one into two, then hands
  the list to ``CornerListToPolygon``.
- ``CornerListToPolygon``, same file ``:246-369``. Walks the corners and replaces
  each one with an arc of straight segments.
- ``SHAPE_LINE_CHAIN::Append``, ``shape_line_chain.h:415-430``. Drops a point
  equal to the one before it. This is not a detail: a pad whose corner radius is
  half its shorter side has zero-length edges, so three joins collapse and the
  vertex count falls from 48 to 45.

Only the ``ERROR_INSIDE`` branch is ported, with ``aInflate == 0``. That is the
pair the pad plot path uses (``PS_plotter.cpp:158-168``). ``ERROR_OUTSIDE`` and a
non-zero inflate belong to the solder-mask and paste layers, which grow a pad by
its mask expansion; nothing here needs them yet, and a half-ported branch is
worse than an absent one.

Every step runs in integer internal units, as it does in KiCad, because the
result is compared against KiCad's own output to the written digit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from eedesigncore.common.kimath import kiround, rotate_point_iu

#: Minimum segments KiCad allows around a full circle.
#:
#: ``geometry_utils.cpp:40``. It clamps ``arc_increment`` rather than the segment
#: count, so a very small radius still gets 8 segments per turn.
MIN_SEGMENTS_PER_CIRCLE = 8

#: Minimum segments per turn that ``CornerListToPolygon`` enforces on a corner.
#:
#: ``convert_basic_shapes_to_polygon.cpp:291``, quoted there as "Ensure 16+
#: segments per 360deg". It is a floor UNDER the value
#: :func:`arc_to_segment_count` returns, not a replacement for it.
MIN_SEGMENTS_PER_CORNER_CIRCLE = 16

#: The arc error the plot path allows, in internal units.
#:
#: ``PLOTTER::GetPlotterArcHighDef`` is ``m_IUsPerDecimil * 2``
#: (``plotter.h:274``), and a board plot sets ``m_IUsPerDecimil`` to
#: ``pcbIUScale.IU_PER_MILS / 10``, which is 2540. So the error is 5080 nm.
#:
#: Measured, not just read: this value reproduces the vertex COUNT and every
#: vertex POSITION of five ``kicad-cli`` roundrect and chamfered-rect exports,
#: to within 0.063 nanometres. A neighbouring value does not — 4064 gives a
#: 36-vertex polygon where KiCad gives 32.
PLOTTER_ARC_HIGH_DEF_IU = 5080

#: The largest coordinate a KiCad file can carry, in internal units.
#:
#: KiCad holds every coordinate in a C++ ``int``, through ``VECTOR2I``. A radius
#: past this value cannot come out of a KiCad file, because KiCad could not have
#: written it and could not read it back.
#:
#: :func:`arc_to_segment_count` clamps to it. That clamp changes NO result any
#: real file can produce, and it bounds two failures a crafted file can otherwise
#: reach:
#:
#: 1. The segment count rises with the radius, without limit. A pad 1e13 mm wide
#:    asks for tens of millions of segments per corner, which is a denial of
#:    service on processor time and on memory, out of a file a few hundred bytes
#:    long.
#: 2. Past about 1e20 internal units, ``error / radius`` falls below the double
#:    precision step at 1.0. Then ``1.0 - relative_error`` IS 1.0, ``acos``
#:    returns 0.0, and the division by ``arc_increment`` raises
#:    ``ZeroDivisionError``.
#:
#: At this clamp the worst case is about 1444 segments per turn at the plot
#: error, and about 103 thousand at an error of 1 internal unit.
MAX_COORDINATE_IU = 2**31 - 1

#: Bit per chamfered corner, as ``eda_shape.h`` numbers them.
RECT_CHAMFER_TOP_LEFT = 1
RECT_CHAMFER_TOP_RIGHT = 2
RECT_CHAMFER_BOTTOM_LEFT = 4
RECT_CHAMFER_BOTTOM_RIGHT = 8

Point2I = tuple[int, int]


def cdiv(numerator: int, denominator: int) -> int:
    """Divide as C++ does: truncate toward zero.

    Python's ``//`` floors, which differs from C++ on every negative quotient.
    Half the coordinates in a centred pad polygon are negative, so the two
    disagree constantly.
    """
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def rescale(numerator: int, value: int, denominator: int) -> int:
    """``rescale`` from ``libs/kimath/src/math/util.cpp:66-76``.

    ``numerator * value / denominator``, rounded to nearest by biasing the
    dividend before a truncating divide. The product is held at full width, as
    the int64 specialisation does with a 128-bit intermediate.
    """
    product = numerator * value
    half = cdiv(denominator, 2)
    bias = -half if (product < 0) != (denominator < 0) else half
    return cdiv(product + bias, denominator)


def resize(vector: Point2I, new_length: int) -> Point2I:
    """``VECTOR2<int>::Resize``, ``vector2d.h:383-418``.

    The equal-components branch is not an optimisation. It takes the exact
    ``1/sqrt(2)`` rather than the integer-rescaled square root, so a 45-degree
    vector resizes differently from a 44-degree one.
    """
    x, y = vector
    if x == 0 and y == 0:
        return (0, 0)

    if abs(x) == abs(y):
        new_x = new_y = abs(new_length) * math.sqrt(0.5)
    else:
        x_sq = x * x
        y_sq = y * y
        length_sq = x_sq + y_sq
        new_length_sq = new_length * new_length
        new_x = math.sqrt(rescale(new_length_sq, x_sq, length_sq))
        new_y = math.sqrt(rescale(new_length_sq, y_sq, length_sq))

    sign = (new_length > 0) - (new_length < 0)
    return (
        (-kiround(new_x) if x < 0 else kiround(new_x)) * sign,
        (-kiround(new_y) if y < 0 else kiround(new_y)) * sign,
    )


def perpendicular(vector: Point2I) -> Point2I:
    """``VECTOR2<T>::Perpendicular``, ``vector2d.h:314-318``."""
    return (-vector[1], vector[0])


def reflect_point(start: Point2I, end: Point2I, point: Point2I) -> Point2I:
    """``SEG( start, end ).ReflectPoint( point )``, ``seg.cpp:664-682``.

    The line through *start* and *end* is treated as infinite.
    """
    delta = (end[0] - start[0], end[1] - start[1])
    length_sq = delta[0] * delta[0] + delta[1] * delta[1]
    if not length_sq:
        return point

    projection = delta[0] * (point[0] - start[0]) + delta[1] * (point[1] - start[1])
    mirror = (
        start[0] + rescale(projection, delta[0], length_sq),
        start[1] + rescale(projection, delta[1], length_sq),
    )
    return (2 * mirror[0] - point[0], 2 * mirror[1] - point[1])


def arc_to_segment_count(radius: int, error_max: int, arc_angle_degrees: float) -> int:
    """``GetArcToSegmentCount``, ``geometry_utils.cpp:42-65``.

    *error_max* is the greatest distance allowed between the middle of a segment
    and the true circle.
    """
    radius = min(max(1, radius), MAX_COORDINATE_IU)
    error_max = max(1, error_max)
    relative_error = error_max / radius
    argument = 1.0 - relative_error

    if -1.0 <= argument <= 1.0:
        arc_increment = 180 / math.pi * math.acos(argument) * 2
        arc_increment = min(360.0 / MIN_SEGMENTS_PER_CIRCLE, arc_increment)
    else:
        # A radius under half the allowed error sends the argument below -1.
        # C++ ``acos`` returns NaN rather than raising, and ``std::min(a, b)``
        # returns ``b < a ? b : a``, so a NaN second argument yields the FIRST:
        # the clamp. Python's ``math.acos`` raises instead, so the branch has to
        # be explicit. Without it a pad with a corner radius under 2.54 microns
        # crashes the renderer where KiCad draws an octagon.
        arc_increment = 360.0 / MIN_SEGMENTS_PER_CIRCLE

    return max(kiround(abs(arc_angle_degrees) / arc_increment), 2)


@dataclass(frozen=True)
class RoundedCorner:
    """One corner of an outline: where it sits and how far it is rounded.

    A radius of zero is a sharp corner, which is what a chamfered corner becomes
    after the chamfer splits it into two.
    """

    position: Point2I
    radius: int = 0


def corner_list_to_polygon(corners: list[RoundedCorner], error: int) -> list[Point2I]:
    """``CornerListToPolygon`` with ``ERROR_INSIDE`` and ``aInflate == 0``.

    Source: ``convert_basic_shapes_to_polygon.cpp:246-369``. The corners must run
    clockwise and must describe a convex outline, which is what the caller below
    builds.

    Appending drops a point equal to the previous one, because
    ``SHAPE_LINE_CHAIN::Append`` does. The FIRST point is never compared against
    the last, since that comparison is a wrap-around the chain does not make, so
    a fully circular pad keeps one duplicate and loses three.
    """
    out: list[Point2I] = []

    def append(point: Point2I) -> None:
        if not out or out[-1] != point:
            out.append(point)

    count = len(corners)
    incoming = (
        corners[0].position[0] - corners[-1].position[0],
        corners[0].position[1] - corners[-1].position[1],
    )

    for index in range(count):
        corner = corners[index]
        following = corners[(index + 1) % count].position
        outgoing = (following[0] - corner.position[0], following[1] - corner.position[1])

        if corner.radius == 0:
            append(corner.position)
            incoming = outgoing
            continue

        if (incoming[0] == 0 and outgoing[1] == 0) or (incoming[1] == 0 and outgoing[0] == 0):
            end_angle = 90.0
            tangent_half = 1.0
        else:
            dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
            norms = math.hypot(*incoming) * math.hypot(*outgoing)
            angle = math.acos(dot / norms)
            tangent_half = math.tan((math.pi - angle) / 2)
            end_angle = math.degrees(angle)

        segments = max(
            MIN_SEGMENTS_PER_CORNER_CIRCLE, arc_to_segment_count(corner.radius, error, 360.0)
        )
        angle_delta = 360.0 / segments

        # Make the first and last segment of the arc the same size, so a corner
        # never ends with a sliver.
        last_segment = end_angle
        if last_segment > 0.0:
            while last_segment > angle_delta:
                last_segment -= angle_delta
        else:
            while last_segment < -angle_delta:
                last_segment += angle_delta
        angle_position = angle_delta if last_segment == 0.0 else (angle_delta + last_segment) / 2

        transition = (corner.radius / tangent_half) if tangent_half > 0 else 0.0
        shift = resize(incoming, kiround(transition))
        arc_start = (corner.position[0] - shift[0], corner.position[1] - shift[1])
        radial = resize(perpendicular(incoming), corner.radius)
        arc_center = (arc_start[0] + radial[0], arc_start[1] + radial[1])
        arc_end = reflect_point(corner.position, arc_center, arc_start)
        origin = (arc_start[0] - arc_center[0], arc_start[1] - arc_center[1])

        append(arc_start)
        while angle_position < end_angle:
            turned = rotate_point_iu(origin, -angle_position)
            append((turned[0] + arc_center[0], turned[1] + arc_center[1]))
            angle_position += angle_delta
        append(arc_end)

        incoming = outgoing

    return out


def round_chamfered_rect_polygon(
    size: Point2I,
    *,
    corner_radius: int = 0,
    chamfer_ratio: float = 0.0,
    chamfer_corners: int = 0,
    angle_degrees: float = 0.0,
    position: Point2I = (0, 0),
    error: int = PLOTTER_ARC_HIGH_DEF_IU,
) -> list[Point2I]:
    """``TransformRoundChamferedRectToPolygon`` with ``aInflate == 0``.

    Source: ``convert_basic_shapes_to_polygon.cpp:459-522``. *size* is the full
    width and height; the corners sit at half of each. The outline is built about
    the origin, turned, then moved onto *position*, which is the order KiCad uses
    and therefore the order the rounding follows.

    A chamfered corner loses its radius and becomes two sharp corners. KiCad
    measures the chamfer against the SHORTER side, so the same ratio cuts the same
    length off a long pad as off a square one.
    """
    half = (cdiv(size[0], 2), cdiv(size[1], 2))
    corners = [
        RoundedCorner((-half[0], -half[1]), corner_radius),
        RoundedCorner((half[0], -half[1]), corner_radius),
        RoundedCorner((half[0], half[1]), corner_radius),
        RoundedCorner((-half[0], half[1]), corner_radius),
    ]

    if chamfer_corners:
        corners = _apply_chamfers(corners, size, chamfer_ratio, chamfer_corners)

    polygon = corner_list_to_polygon(corners, error)

    if angle_degrees:
        polygon = [rotate_point_iu(point, angle_degrees) for point in polygon]

    return [(point[0] + position[0], point[1] + position[1]) for point in polygon]


def _apply_chamfers(
    corners: list[RoundedCorner], size: Point2I, chamfer_ratio: float, chamfer_corners: int
) -> list[RoundedCorner]:
    """Split every chamfered corner into two sharp ones.

    ``convert_basic_shapes_to_polygon.cpp:485-514``. The corner list runs
    top-left, top-right, bottom-right, bottom-left, and the chamfer bits do NOT
    follow that order, which is why ``chamfer_id`` below is a lookup rather than a
    shift. The ``sign`` table walks an 8-step cycle so one expression serves all
    four corners.
    """
    shorter_side = min(size[0], size[1])
    chamfer = max(0, kiround(chamfer_ratio * shorter_side))
    chamfer_id = (
        RECT_CHAMFER_TOP_LEFT,
        RECT_CHAMFER_TOP_RIGHT,
        RECT_CHAMFER_BOTTOM_RIGHT,
        RECT_CHAMFER_BOTTOM_LEFT,
    )
    sign = (0, 1, -1, 0, 0, -1, 1, 0)

    out = list(corners)
    position = 0
    for which in range(4):
        if not (chamfer_corners & chamfer_id[which]):
            position += 1
            continue

        out[position] = RoundedCorner(out[position].position, 0)
        if chamfer == 0:
            position += 1
            continue

        out.insert(position + 1, out[position])
        first = out[position].position
        out[position] = RoundedCorner(
            (
                first[0] + sign[(2 * which) & 7] * chamfer,
                first[1] + sign[(2 * which - 2) & 7] * chamfer,
            )
        )
        second = out[position + 1].position
        out[position + 1] = RoundedCorner(
            (
                second[0] + sign[(2 * which + 1) & 7] * chamfer,
                second[1] + sign[(2 * which - 1) & 7] * chamfer,
            )
        )
        position += 2

    if bin(chamfer_corners).count("1") > 1 and 2 * chamfer >= shorter_side:
        out = _remove_duplicate_corners(out)

    return out


def _remove_duplicate_corners(corners: list[RoundedCorner]) -> list[RoundedCorner]:
    """``CornerListRemoveDuplicates``, ``convert_basic_shapes_to_polygon.cpp:372-383``.

    Reached only when neighbouring chamfers meet, which happens once the chamfer
    covers half the shorter side.

    The walk runs BACKWARD and seeds itself from corner ZERO, not from the last
    corner. That is what closes the ring: the final comparison is against the
    first position, so a duplicate spanning the wrap is removed too. Walking
    forward keeps it.
    """
    out = list(corners)
    previous = out[0].position
    for index in range(len(out) - 1, -1, -1):
        if out[index].position == previous:
            del out[index]
        else:
            previous = out[index].position
    return out


__all__ = [
    "MAX_COORDINATE_IU",
    "MIN_SEGMENTS_PER_CIRCLE",
    "MIN_SEGMENTS_PER_CORNER_CIRCLE",
    "PLOTTER_ARC_HIGH_DEF_IU",
    "RECT_CHAMFER_BOTTOM_LEFT",
    "RECT_CHAMFER_BOTTOM_RIGHT",
    "RECT_CHAMFER_TOP_LEFT",
    "RECT_CHAMFER_TOP_RIGHT",
    "Point2I",
    "RoundedCorner",
    "arc_to_segment_count",
    "cdiv",
    "corner_list_to_polygon",
    "perpendicular",
    "reflect_point",
    "rescale",
    "resize",
    "round_chamfered_rect_polygon",
]
