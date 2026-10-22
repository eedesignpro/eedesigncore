"""The internal-unit point kernel that every board transform runs on.

A KiCad coordinate on disk is an integer count of internal units, printed as
millimetres. A transform that adds millimetre floats therefore invents digits
that KiCad would never write, and the next KiCad save moves the coordinate back.
Every function here converts to internal units with :func:`kiround`, moves the
integer, and converts back, so a transformed coordinate is exactly a coordinate
KiCad could have written.

The kernel knows nothing about board items. The per-item dispatch lives in
``common/pcb_family/transform.py``; the board and footprint entry points live in
``pcb/transform.py``.
"""

from __future__ import annotations

from typing import Literal

from eedesigncore.common.kimath import (
    kiround,
    normalize_angle,
    rotate_point_about_iu,
)

# Internal units per millimetre in the PCB family (``pcbIUScale``, 1 IU = 1 nm).
PCB_IU_PER_MM = 1e6

Point = tuple[float, float]

#: The coordinate a mirror negates. ``"x"`` is KiCad's left-right flip and
#: ``"y"`` is its top-bottom flip. See :class:`Mirror`.
MirrorAxis = Literal["x", "y"]

#: KiCad's own name for a flip direction, in KiCad's own words.
FlipDirection = Literal["left_right", "top_bottom"]

#: The coordinate each flip direction negates.
FLIP_AXIS: dict[str, MirrorAxis] = {"left_right": "x", "top_bottom": "y"}
PointIU = tuple[int, int]


def to_iu(value: float) -> int:
    """Convert a millimetre value to internal units, as KiCad's parser does."""
    return kiround(value * PCB_IU_PER_MM)


def to_mm(value: int) -> float:
    """Convert an internal-unit value back to millimetres, as the writer does."""
    return value / PCB_IU_PER_MM


def point_to_iu(point: Point) -> PointIU:
    """Convert a millimetre point to an internal-unit point."""
    return (to_iu(point[0]), to_iu(point[1]))


def point_to_mm(point: PointIU) -> Point:
    """Convert an internal-unit point back to a millimetre point."""
    return (to_mm(point[0]), to_mm(point[1]))


class Translation:
    """A rigid move by a fixed offset, held in internal units.

    The offset converts to internal units ONCE, at construction. Every point the
    translation touches then moves by the same integer pair, so the operation is
    exact and repeatable: translating by ``d`` and then by ``-d`` returns the
    original coordinates bit for bit.
    """

    __slots__ = ("_dx", "_dy")

    def __init__(self, delta: Point) -> None:
        self._dx = to_iu(delta[0])
        self._dy = to_iu(delta[1])

    @property
    def delta_iu(self) -> PointIU:
        """The offset in internal units."""
        return (self._dx, self._dy)

    @property
    def is_identity(self) -> bool:
        """True when the offset is zero and no coordinate can change."""
        return self._dx == 0 and self._dy == 0

    def __call__(self, point: Point) -> Point:
        """Return *point* moved by the offset."""
        if self.is_identity:
            return point
        x, y = point_to_iu(point)
        return point_to_mm((x + self._dx, y + self._dy))

    def __repr__(self) -> str:
        return f"Translation(delta_iu={self.delta_iu!r})"


class Mirror:
    """A mirror of ONE coordinate about *center*, held in internal units.

    ``axis`` names the coordinate that CHANGES, not the line it reflects in.
    ``"x"`` negates the x coordinate about ``center.x`` and is what KiCad calls
    a left-right flip. ``"y"`` negates the y coordinate about ``center.y`` and
    is what KiCad calls a top-bottom flip.

    KiCad's own naming is the trap this wording avoids. ``FLIP_DIRECTION``
    orders ``LEFT_RIGHT`` first (``include/core/mirror.h:26-30``), so
    ``LEFT_RIGHT`` casts to ``false``, and the settings key that stores it is
    named ``editing.flip_left_right`` with ``false`` meaning LEFT_RIGHT
    (``pcbnew/pcbnew_settings.cpp:151-153``). The name reads as the opposite of
    its value.

    The centre converts to internal units ONCE, at construction, and the mirror
    is the integer form ``MIRROR( value, ref )`` uses
    (``include/core/mirror.h:40-44``): ``2 * ref - value``. So a mirror applied
    twice about one centre returns the original coordinates bit for bit.
    """

    __slots__ = ("_center_iu", "axis", "center")

    def __init__(self, axis: MirrorAxis, center: Point = (0.0, 0.0)) -> None:
        self.axis = axis
        self.center = center
        self._center_iu = point_to_iu(center)

    @property
    def is_identity(self) -> bool:
        """Never true. A mirror is its own inverse and it always changes winding."""
        return False

    def __call__(self, point: Point) -> Point:
        """Return *point* with the named coordinate mirrored about the centre."""
        x, y = point_to_iu(point)
        cx, cy = self._center_iu
        if self.axis == "x":
            return point_to_mm((2 * cx - x, y))
        return point_to_mm((x, 2 * cy - y))

    def __repr__(self) -> str:
        return f"Mirror(axis={self.axis!r}, center={self.center!r})"


class Rotation:
    """A turn of *angle* degrees about *center*, held in internal units.

    The centre converts to internal units ONCE, at construction, and every
    point takes ``RotatePoint( VECTOR2I&, centre, EDA_ANGLE )``
    (``libs/kimath/src/trigo.cpp:229-278``). A quarter turn takes the exact
    branch of that function, so it never picks up a rounding error; any other
    angle takes the same trigonometric branch KiCad takes, with the same
    rounding.

    :attr:`angle` is the delta an ABSOLUTE angle field takes. Which fields those
    are is a per-item-type question, answered in
    ``common/pcb_family/transform.py`` and evidenced in
    ``context/audit/footprint-transform-frame-2026-09-02.md``.
    """

    __slots__ = ("_center_iu", "angle", "center")

    def __init__(self, angle: float, center: Point = (0.0, 0.0)) -> None:
        self.angle = angle
        self.center = center
        self._center_iu = point_to_iu(center)

    @property
    def is_identity(self) -> bool:
        """True when the turn is a whole number of full circles."""
        return self.angle % 360.0 == 0.0

    @property
    def is_cardinal(self) -> bool:
        """True when the turn is a multiple of 90 degrees (``EDA_ANGLE::IsCardinal``)."""
        return self.angle % 90.0 == 0.0

    def turn(self, absolute_angle: float) -> float:
        """Return an absolute angle field turned and normalised to ``[0, 360)``.

        This is what ``PCB_TEXT::Rotate`` does to a text angle
        (``pcbnew/pcb_text.cpp:445-454``).
        """
        return normalize_angle(absolute_angle + self.angle)

    def __call__(self, point: Point) -> Point:
        """Return *point* turned about the centre."""
        if self.is_identity:
            return point
        return point_to_mm(rotate_point_about_iu(point_to_iu(point), self._center_iu, self.angle))

    def __repr__(self) -> str:
        return f"Rotation(angle={self.angle!r}, center={self.center!r})"


__all__ = [
    "PCB_IU_PER_MM",
    "Point",
    "PointIU",
    "Rotation",
    "Translation",
    "point_to_iu",
    "point_to_mm",
    "to_iu",
    "to_mm",
]
