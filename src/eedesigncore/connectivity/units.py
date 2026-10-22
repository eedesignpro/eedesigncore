"""Exact integer point arithmetic for the connectivity graph.

KiCad matches connection points by exact integer equality on internal units.
There is no tolerance and no snapping, so one nanometer of difference breaks a
connection (`connection_graph.cpp:1318`, audit
``connectivity-items-geometry-2026-07-28.md`` headline 1).

The schematic models store float millimeters. This module converts to integer
nanometers at graph-build time and every comparison downstream runs on integers.
The stored values never change. ADR-097 records why the model keeps floats.

The conversion is lossless for a file KiCad wrote. KiCad emits at most six
decimal places for a millimeter value, which is nanometer resolution, so the
scaled value lands on an integer.

Do NOT port the C++ ``VECTOR2::operator<``. It compares magnitude, not order, so
distinct points of equal length compare equal (`vector2d.h:577-581`). KiCad
sidesteps that by specializing ``std::less<VECTOR2I>`` to a lexicographic compare
(`vector2.cpp:22-28`). A Python tuple already orders lexicographically, which is
the behavior the graph needs (audit headlines 9 and 10).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias

# One millimeter in internal units. KiCad schematics use nanometers.
NM_PER_MM = 1_000_000

# An integer point in internal units. A plain tuple, so dict keys and sorting
# both behave lexicographically without a helper class.
IUPoint: TypeAlias = tuple[int, int]


def mm_to_iu(value: float) -> int:
    """Convert a millimeter value to integer nanometers.

    Uses ``Decimal`` on the repr of the float rather than ``round(value * 1e6)``.
    Binary floating point cannot hold a value such as 0.1 exactly, so scaling
    first and rounding after can land one unit away from the decimal the file
    actually stored. Going through the shortest exact decimal repr reproduces the
    digits KiCad wrote.
    """
    return int((Decimal(repr(float(value))) * NM_PER_MM).to_integral_value())


def iu_to_mm(value: int) -> float:
    """Convert integer nanometers back to a millimeter value."""
    return float(Decimal(value) / NM_PER_MM)


def point_to_iu(point: tuple[float, float]) -> IUPoint:
    """Convert a millimeter point to an exact integer point."""
    return (mm_to_iu(point[0]), mm_to_iu(point[1]))


def is_lossless(value: float) -> bool:
    """True when *value* survives the millimeter to integer round trip exactly.

    A file KiCad wrote always satisfies this. A caller who builds a model by hand
    can defeat it with more than nanometer resolution, and the graph would then
    compare a rounded point. R1 asserts this across the corpus.
    """
    return iu_to_mm(mm_to_iu(value)) == float(value)
