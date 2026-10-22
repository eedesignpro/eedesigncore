"""Ports of the KiCad ``libs/kimath`` primitives that save-order comparators need.

Both family ordering modules read the same three primitives, so they live here
rather than in either family package.

* :func:`kiround` is KiCad ``KiROUND`` (``libs/kimath/include/math/util.h``).
* :func:`int32` reproduces the wrap of a C++ ``int`` subtraction. Several KiCad
  comparators return the DIFFERENCE of two coordinates, and a difference wider
  than 32 bits reverses the verdict.
* :func:`calc_arc_center_iu` is ``CalcArcCenter( VECTOR2I … )``
  (``libs/kimath/src/trigo.cpp:369-552``), which supplies the position key of an
  arc in both families.
* :func:`rotate_point_iu` is ``RotatePoint( VECTOR2I&, EDA_ANGLE )``
  (``libs/kimath/src/trigo.cpp:229-264``), which the PCB family needs to rebuild
  the board coordinates of a footprint child.
"""

from __future__ import annotations

import math
import sys

_M_SQRT1_2 = math.sqrt(0.5)
_DBL_EPSILON = sys.float_info.epsilon

# ``CalcArcCenter( VECTOR2I … )`` clamps to this range before it rounds
# (``libs/kimath/src/trigo.cpp:534-552``).
_INT_MIN_CLAMP = -(2**31) + 100
_INT_MAX_CLAMP = 2**31 - 1 - 100

# The plain signed 32-bit range, which ``KiROUND<int>`` clamps to.
_INT_MIN = -(2**31)
_INT_MAX = 2**31 - 1


def int32(value: int) -> int:
    """Wrap *value* into the signed 32-bit range, as a C++ ``int`` subtraction does.

    Reachable in practice: ``CalcArcCenter`` clamps a near-collinear arc centre to
    the ``int`` rail, and the corpus symbol ``HCPL-0611-500E`` sorts on the
    wrapped result (iteration 11.5 T1).
    """
    return ((value + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def kiround(value: float) -> int:
    """Round half away from zero, as KiCad ``KiROUND`` does."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def rotate_point_iu(point: tuple[int, int], angle_degrees: float) -> tuple[int, int]:
    """Rotate *point* about the origin, as ``RotatePoint( VECTOR2I&, EDA_ANGLE )`` does.

    Source: ``libs/kimath/src/trigo.cpp:229-264``. The four quadrant angles take
    an exact branch, so a 90-degree footprint rotation never picks up a rounding
    error. The general branch rounds with ``KiROUND``.
    """
    angle = math.fmod(angle_degrees, 360.0)
    if angle < 0:
        angle += 360.0

    x, y = point
    if angle == 0.0:
        return (x, y)
    if angle == 90.0:
        return (y, -x)
    if angle == 180.0:
        return (-x, -y)
    if angle == 270.0:
        return (-y, x)

    radians = math.radians(angle)
    sinus = math.sin(radians)
    cosinus = math.cos(radians)
    return (kiround(y * sinus + x * cosinus), kiround(y * cosinus - x * sinus))


def _fdiv(numerator: float, denominator: float) -> float:
    """Divide with IEEE semantics, so a zero denominator yields infinity or NaN.

    The C++ original relies on that behaviour. Python raises instead.
    """
    if denominator == 0.0:
        if numerator == 0.0 or math.isnan(numerator):
            return math.nan
        return math.copysign(math.inf, numerator) * math.copysign(1.0, denominator)
    return numerator / denominator


def _sq_ratio(numerator: float, denominator: float) -> float:
    """Evaluate the C++ idiom ``num / den * num / den`` in the same order."""
    return _fdiv(_fdiv(numerator, denominator) * numerator, denominator)


def _cfloor(value: float) -> float:
    """Floor with C++ semantics, which pass infinity and NaN through."""
    if math.isinf(value) or math.isnan(value):
        return value
    return float(math.floor(value))


def calc_arc_center_double(
    start: tuple[int, int], mid: tuple[int, int], end: tuple[int, int]
) -> tuple[float, float]:
    """Port of ``CalcArcCenter( VECTOR2D, VECTOR2D, VECTOR2D )``.

    Source: ``libs/kimath/src/trigo.cpp:369-530``. The uncertainty arithmetic and
    the "nice round number" snap both change the result by up to 100 internal
    units, so both are reproduced rather than approximated.
    """
    start_x, start_y = float(start[0]), float(start[1])
    mid_x, mid_y = float(mid[0]), float(mid[1])
    end_x, end_y = float(end[0]), float(end[1])

    if (
        max(start_x, mid_x, end_x) - min(start_x, mid_x, end_x) < 5.0
        and max(start_y, mid_y, end_y) - min(start_y, mid_y, end_y) < 5.0
    ):
        return ((start_x + mid_x + end_x) / 3.0, (start_y + mid_y + end_y) / 3.0)

    y_delta_21 = mid_y - start_y
    x_delta_21 = mid_x - start_x
    y_delta_32 = end_y - mid_y
    x_delta_32 = end_x - mid_x

    if (x_delta_21 == 0.0 and y_delta_32 == 0.0) or (y_delta_21 == 0.0 and x_delta_32 == 0.0):
        return ((start_x + end_x) / 2.0, (start_y + end_y) / 2.0)

    if x_delta_21 == 0.0:
        x_delta_21 = _DBL_EPSILON
    if x_delta_32 == 0.0:
        x_delta_32 = -_DBL_EPSILON

    a_slope = y_delta_21 / x_delta_21
    b_slope = y_delta_32 / x_delta_32

    da_slope = a_slope * math.sqrt(_fdiv(0.5, y_delta_21) ** 2 + _fdiv(0.5, x_delta_21) ** 2)
    db_slope = b_slope * math.sqrt(_fdiv(0.5, y_delta_32) ** 2 + _fdiv(0.5, x_delta_32) ** 2)

    if a_slope == b_slope:
        if start == end:
            return ((start_x + mid_x) / 2.0, (start_y + mid_y) / 2.0)
        a_slope += _DBL_EPSILON
        b_slope -= _DBL_EPSILON

    if a_slope == 0.0:
        a_slope = 1e-10
    if b_slope == 0.0:
        b_slope = 1e-10

    ab_slope_start_end_y = a_slope * b_slope * (start_y - end_y)
    d_ab_slope_start_end_y = ab_slope_start_end_y * math.sqrt(
        _sq_ratio(da_slope, a_slope)
        + _sq_ratio(db_slope, b_slope)
        + _sq_ratio(_M_SQRT1_2, start_y - end_y)
    )

    b_slope_start_mid_x = b_slope * (start_x + mid_x)
    d_b_slope_start_mid_x = b_slope_start_mid_x * math.sqrt(
        _sq_ratio(db_slope, b_slope) + _sq_ratio(_M_SQRT1_2, start_x + mid_x)
    )

    a_slope_mid_end_x = a_slope * (mid_x + end_x)
    d_a_slope_mid_end_x = a_slope_mid_end_x * math.sqrt(
        _sq_ratio(da_slope, a_slope) + _sq_ratio(_M_SQRT1_2, mid_x + end_x)
    )

    twice_ba_slope_diff = 2 * (b_slope - a_slope)
    d_twice_ba_slope_diff = 2 * math.sqrt(db_slope * db_slope + da_slope * da_slope)

    center_numerator_x = ab_slope_start_end_y + b_slope_start_mid_x - a_slope_mid_end_x
    d_center_numerator_x = math.sqrt(
        d_ab_slope_start_end_y * d_ab_slope_start_end_y
        + d_b_slope_start_mid_x * d_b_slope_start_mid_x
        + d_a_slope_mid_end_x * d_a_slope_mid_end_x
    )

    center_x = _fdiv(center_numerator_x, twice_ba_slope_diff)
    d_center_x = center_x * math.sqrt(
        _sq_ratio(d_center_numerator_x, center_numerator_x)
        + _sq_ratio(d_twice_ba_slope_diff, twice_ba_slope_diff)
    )

    center_numerator_y = (start_x + mid_x) / 2.0 - center_x
    d_center_numerator_y = math.sqrt(1.0 / 8.0 + d_center_x * d_center_x)

    center_first_term = _fdiv(center_numerator_y, a_slope)
    d_center_first_term_y = center_first_term * math.sqrt(
        _sq_ratio(d_center_numerator_y, center_numerator_y) + _sq_ratio(da_slope, a_slope)
    )

    center_y = center_first_term + (start_y + mid_y) / 2.0
    d_center_y = math.sqrt(d_center_first_term_y * d_center_first_term_y + 1.0 / 8.0)

    rounded_100_x = _cfloor((center_x + 50.0) / 100.0) * 100.0
    rounded_100_y = _cfloor((center_y + 50.0) / 100.0) * 100.0
    rounded_10_x = _cfloor((center_x + 5.0) / 10.0) * 10.0
    rounded_10_y = _cfloor((center_y + 5.0) / 10.0) * 10.0

    if abs(rounded_100_x - center_x) < d_center_x and abs(rounded_100_y - center_y) < d_center_y:
        return (rounded_100_x, rounded_100_y)
    if abs(rounded_10_x - center_x) < d_center_x and abs(rounded_10_y - center_y) < d_center_y:
        return (rounded_10_x, rounded_10_y)
    return (center_x, center_y)


def _clamp_to_int(value: float) -> int:
    """Clamp then round, as ``CalcArcCenter( VECTOR2I … )`` does (``trigo.cpp:534-552``).

    A NaN centre means degenerate geometry that no KiCad-written arc carries, so
    it maps to zero rather than raising.
    """
    if math.isnan(value):
        return 0
    clamped = min(max(value, float(_INT_MIN_CLAMP)), float(_INT_MAX_CLAMP))
    return kiround(clamped)


def calc_arc_center_iu(
    start: tuple[int, int], mid: tuple[int, int], end: tuple[int, int]
) -> tuple[int, int]:
    """Return the integer arc centre of the arc through *start*, *mid* and *end*."""
    center_x, center_y = calc_arc_center_double(start, mid, end)
    return (_clamp_to_int(center_x), _clamp_to_int(center_y))


def kiround_clamped(value: float) -> int:
    """``KiROUND<int>``: round half away from zero, then CLAMP to the int range.

    Source: ``libs/kimath/include/math/util.h:102-128``. The clamp is load
    bearing: a degenerate arc puts its centre on the int rail, and a point
    rotated about that centre lands far outside the range.
    """
    return min(max(kiround(value), _INT_MIN), _INT_MAX)


def rotate_point_about_iu(
    point: tuple[int, int], center: tuple[int, int], angle_degrees: float
) -> tuple[int, int]:
    """``RotatePoint( VECTOR2I&, const VECTOR2I& centre, const EDA_ANGLE& )``.

    Source: ``libs/kimath/src/trigo.cpp:229-278``. Every step is ``int``
    arithmetic, so the offset subtraction and the re-centring addition WRAP.
    A degenerate arc reaches both, which is why this port does not reuse
    :func:`rotate_point_iu`.
    """
    offset_x = int32(point[0] - center[0])
    offset_y = int32(point[1] - center[1])

    angle = math.fmod(angle_degrees, 360.0)
    if angle < 0:
        angle += 360.0

    if angle == 0.0:
        rotated = (offset_x, offset_y)
    elif angle == 90.0:
        rotated = (offset_y, int32(-offset_x))
    elif angle == 180.0:
        rotated = (int32(-offset_x), int32(-offset_y))
    elif angle == 270.0:
        rotated = (int32(-offset_y), offset_x)
    else:
        radians = math.radians(angle)
        sinus = math.sin(radians)
        cosinus = math.cos(radians)
        rotated = (
            kiround_clamped(offset_y * sinus + offset_x * cosinus),
            kiround_clamped(offset_y * cosinus - offset_x * sinus),
        )

    return (int32(rotated[0] + center[0]), int32(rotated[1] + center[1]))


def normalize_angle(degrees: float) -> float:
    """``EDA_ANGLE::Normalize``: bring an angle into ``[0, 360)``.

    Source: ``libs/kimath/include/geometry/eda_angle.h:229-238``. This is the
    normalisation a text angle takes on rotation.
    """
    value = degrees
    while value < -0.0:
        value += 360.0
    while value >= 360.0:
        value -= 360.0
    return value


def normalize_angle_180(degrees: float) -> float:
    """``EDA_ANGLE::Normalize180``: bring an angle into ``(-180, 180]``.

    Source: ``libs/kimath/include/geometry/eda_angle.h:268-277``. This is the
    normalisation a FOOTPRINT orientation takes (``footprint.cpp:3105``).
    """
    value = degrees
    while value <= -180.0:
        value += 360.0
    while value > 180.0:
        value -= 360.0
    return value
