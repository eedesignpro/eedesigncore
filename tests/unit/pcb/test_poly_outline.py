"""The polygon outline KiCad holds, rebuilt from the file.

``FOOTPRINT::cmp_drawings`` sorts polygons on vertex COUNT, then on each vertex
(``pcbnew/footprint.cpp:4358-4371``). An ``(arc …)`` in the chain becomes an
interpolated run before it reaches that comparator, so the file element count is
not the vertex count.

ADR-061 assumed the run could not be rebuilt, so the comparator tied every
polygon carrying an arc and let a UUID decide. The 2026-09-04 polygon vertex
audit disproved that. These tests pin the rebuild to the numbers the audit
predicts and to KiCad's measured output.
"""

from __future__ import annotations

from eedesigncore.common.models import PtsArc
from eedesigncore.common.pcb_family.ordering import point_iu
from eedesigncore.common.pcb_family.poly_outline import (
    arc_to_polyline_iu,
    arc_to_segment_count,
    central_angle,
    circle_to_end_segment_delta_radius,
    is_ccw,
    polygon_outline_iu,
)

MM = 1_000_000  # PCB internal units per millimetre

# ---------------------------------------------------------------------------
# The interpolation, against the audit's worked example
# ---------------------------------------------------------------------------


def test_quarter_circle_of_one_millimetre_yields_twenty_points() -> None:
    """Audit worked example: radius 1 mm, sweep 90 degrees, run length 20."""
    run = arc_to_polyline_iu((MM, 0), (707107, 707107), (0, MM))
    assert len(run) == 20


def test_segment_count_for_that_arc_is_eighteen() -> None:
    assert arc_to_segment_count(MM, 1000, 90.0) == 18


def test_effective_error_rounds_half_away_from_zero() -> None:
    """The true value is 952.69, so KiROUND gives 953, not 952."""
    assert circle_to_end_segment_delta_radius(MM, 72) == 953


def test_the_run_starts_and_ends_on_the_given_points() -> None:
    """Both ends must sit exactly on the arc, so neither is interpolated."""
    start, mid, end = (MM, 0), (707107, 707107), (0, MM)
    run = arc_to_polyline_iu(start, mid, end)
    assert run[0] == start
    assert run[-1] == end


def test_a_tiny_arc_degenerates_to_two_points() -> None:
    """Below half the max error the arc becomes one segment."""
    run = arc_to_polyline_iu((0, 0), (100, 10), (200, 0))
    assert run == [(0, 0), (200, 0)]


# ---------------------------------------------------------------------------
# Winding and sweep
# ---------------------------------------------------------------------------


def test_winding_follows_all_three_points() -> None:
    """Two arcs share a centre and a pair of ends, so the mid decides."""
    assert is_ccw((MM, 0), (707107, 707107), (0, MM)) is not is_ccw(
        (MM, 0), (707107, -707107), (0, MM)
    )


def test_equal_ends_mean_a_full_circle() -> None:
    """``SHAPE_ARC::GetCentralAngle`` returns 360, not 0 (``shape_arc.cpp:950``)."""
    assert central_angle((MM, 0), (-MM, 0), (MM, 0), (0, 0)) == 360.0


# ---------------------------------------------------------------------------
# Outline assembly
# ---------------------------------------------------------------------------


def test_a_repeated_point_is_dropped() -> None:
    """Duplicate suppression compares the previous point only."""
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    outline = polygon_outline_iu(list(pts), point_iu)
    assert outline == [(0, 0), (MM, 0), (MM, MM)]


def test_a_point_matching_the_first_but_not_the_last_is_kept() -> None:
    """The closed-chain merge runs on the arc path only, never on plain points."""
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
    outline = polygon_outline_iu(list(pts), point_iu)
    assert outline is not None
    assert len(outline) == 4


def test_an_unknown_element_gives_no_outline() -> None:
    """A count from a partly understood chain would order polygons on a guess."""
    assert polygon_outline_iu(["not a point"], point_iu) is None


def test_an_arc_expands_the_vertex_count_well_past_its_element_count() -> None:
    """One arc element is not one vertex. That was the whole defect."""
    arc = PtsArc(start=(1.0, 0.0), mid=(0.707107, 0.707107), end=(0.0, 1.0))
    outline = polygon_outline_iu([arc], point_iu)
    assert outline is not None
    assert len(outline) == 20
