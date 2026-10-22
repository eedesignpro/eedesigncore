"""The bounding-box accumulator and the arc extent it needs.

Extents accumulate in internal units and convert to millimetres once, at the
end, so a box never drifts through repeated float addition.

The arc extent is the reason this module exists. The three stored points of an
arc are not its extremes: an arc that crosses an axis reaches the radius on that
axis, past every stored point. :func:`arc_extreme_points_iu` returns the stored
points plus every axis crossing inside the sweep, which makes the box tight.
"""

from __future__ import annotations

import math

from eedesigncore.common.base import KicadModel
from eedesigncore.common.kimath import calc_arc_center_iu, kiround
from eedesigncore.common.transform import Point, PointIU, to_mm


class BoundingBox(KicadModel):
    """An axis-aligned box in millimetres.

    ``min_y`` is the SMALLEST stored y value. KiCad board y grows downwards, so
    ``min_y`` is the top edge on screen. The box states coordinates, not compass
    directions.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        """Extent along x, in millimetres."""
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        """Extent along y, in millimetres."""
        return self.max_y - self.min_y

    @property
    def center(self) -> Point:
        """The midpoint of the box, in millimetres."""
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    @property
    def size(self) -> Point:
        """Width and height, in millimetres."""
        return (self.width, self.height)

    def merged(self, other: BoundingBox) -> BoundingBox:
        """Return the smallest box that holds both this box and *other*."""
        return BoundingBox(
            min_x=min(self.min_x, other.min_x),
            min_y=min(self.min_y, other.min_y),
            max_x=max(self.max_x, other.max_x),
            max_y=max(self.max_y, other.max_y),
        )


class Extent:
    """A mutable min/max accumulator in internal units."""

    __slots__ = ("_empty", "_max_x", "_max_y", "_min_x", "_min_y")

    def __init__(self) -> None:
        self._min_x = 0
        self._min_y = 0
        self._max_x = 0
        self._max_y = 0
        self._empty = True

    @property
    def is_empty(self) -> bool:
        """True when nothing has been added yet."""
        return self._empty

    def add(self, point: PointIU, pad: int = 0) -> None:
        """Add *point*, grown by *pad* internal units on every side."""
        x, y = point
        if self._empty:
            self._min_x, self._max_x = x - pad, x + pad
            self._min_y, self._max_y = y - pad, y + pad
            self._empty = False
            return
        self._min_x = min(self._min_x, x - pad)
        self._min_y = min(self._min_y, y - pad)
        self._max_x = max(self._max_x, x + pad)
        self._max_y = max(self._max_y, y + pad)

    def to_bbox(self) -> BoundingBox | None:
        """Return the accumulated box in millimetres, or ``None`` when empty."""
        if self._empty:
            return None
        return BoundingBox(
            min_x=to_mm(self._min_x),
            min_y=to_mm(self._min_y),
            max_x=to_mm(self._max_x),
            max_y=to_mm(self._max_y),
        )


def _angle_of(point: PointIU, center: PointIU) -> float:
    """The angle of *point* about *center*, in degrees within ``[0, 360)``."""
    return math.degrees(math.atan2(point[1] - center[1], point[0] - center[0])) % 360.0


def arc_extreme_points_iu(start: PointIU, mid: PointIU, end: PointIU) -> list[PointIU]:
    """Return the points that bound an arc: its three stored points, plus crossings.

    An arc reaches its radius wherever it crosses an axis through its centre, so
    every crossing inside the sweep is an extreme of the box. A degenerate arc,
    whose three points give no usable centre or a zero radius, returns only the
    stored points.
    """
    points = [start, mid, end]
    center = calc_arc_center_iu(start, mid, end)
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    if radius <= 0.0:
        return points

    from_start = _angle_of(start, center)
    to_mid = (_angle_of(mid, center) - from_start) % 360.0
    to_end = (_angle_of(end, center) - from_start) % 360.0
    # The winding that passes through the mid point is the real one. A closed arc
    # (start on end) sweeps the whole circle.
    counterclockwise = to_mid <= to_end
    span = to_end if counterclockwise else 360.0 - to_end
    if start == end:
        span = 360.0

    for axis_angle in (0.0, 90.0, 180.0, 270.0):
        offset = (axis_angle - from_start) % 360.0
        if not counterclockwise:
            offset = (360.0 - offset) % 360.0
        if offset <= span:
            radians = math.radians(axis_angle)
            points.append(
                (
                    center[0] + kiround(radius * math.cos(radians)),
                    center[1] + kiround(radius * math.sin(radians)),
                )
            )
    return points


__all__ = ["BoundingBox", "Extent", "arc_extreme_points_iu"]
