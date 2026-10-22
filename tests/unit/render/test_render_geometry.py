"""The geometry layer, and only the geometry layer.

No test in this file asserts a color, an opacity or an element id. Geometry is
frozen and policy is open, so a test that mixes them would fail every time
somebody changes a default. That is the split ADR-098 draws.
"""

from __future__ import annotations

import math

import pytest

from eedesigncore.common.models import PtsArc, Stroke, StrokeType
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
)
from eedesigncore.render.geometry import (
    arc_center,
    arc_flags,
    arc_path,
    format_mm,
    shapes_for,
    stroke_attrs,
)
from eedesigncore.render.traversal import identity_map


def only(item: object) -> tuple[str, dict[str, str]]:
    """Return the single shape of *item*, as a tag and an attribute mapping."""
    shapes = shapes_for(item, identity_map)
    assert shapes is not None
    assert len(shapes) == 1
    return shapes[0].tag, dict(shapes[0].attrs)


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


def test_format_mm_uses_four_places_by_default() -> None:
    assert format_mm(1.5) == "1.5000"


def test_format_mm_rounds_to_a_tenth_of_a_micron() -> None:
    assert format_mm(0.00004999) == "0.0000"
    assert format_mm(0.00005001) == "0.0001"


def test_format_mm_collapses_negative_zero() -> None:
    """Two boards that differ only in the sign of a zero must render the same."""
    assert format_mm(-0.0) == "0.0000"
    assert format_mm(-0.000001) == "0.0000"


def test_format_mm_honors_a_wider_precision() -> None:
    assert format_mm(1.23456789, 6) == "1.234568"


# ---------------------------------------------------------------------------
# Stroke width and dash patterns
# ---------------------------------------------------------------------------


def test_zero_width_gives_no_stroke() -> None:
    """A stored zero means an invisible shape, not a request for a default.

    ``SVG_plotter.cpp:249-251`` writes ``stroke:none``, and the board plot path
    passes the stored width through untouched
    (``plot_brditems_plotter.cpp:917``).
    """
    assert dict(stroke_attrs(Stroke(width=0.0))) == {"stroke": "none"}


def test_negative_width_gives_no_stroke() -> None:
    assert dict(stroke_attrs(Stroke(width=-1.0))) == {"stroke": "none"}


def test_solid_stroke_carries_no_dash_array() -> None:
    attrs = dict(stroke_attrs(Stroke(width=0.15, type=StrokeType.solid)))
    assert attrs == {"stroke-width": "0.1500"}


def test_default_stroke_type_carries_no_dash_array() -> None:
    attrs = dict(stroke_attrs(Stroke(width=0.15, type=StrokeType.default)))
    assert attrs == {"stroke-width": "0.1500"}


@pytest.mark.parametrize(
    ("stroke_type", "expected"),
    [
        (StrokeType.dash, "1.1000,0.4000"),
        (StrokeType.dot, "0.0200,0.4000"),
        (StrokeType.dash_dot, "1.1000,0.4000,0.0200,0.4000"),
        (StrokeType.dash_dot_dot, "1.1000,0.4000,0.0200,0.4000,0.0200,0.4000"),
    ],
)
def test_dash_patterns_follow_the_iso_ratios(stroke_type: StrokeType, expected: str) -> None:
    """Ratios 11, 4 and 0.2 times the width. ``render_settings.cpp:60-85``."""
    attrs = dict(stroke_attrs(Stroke(width=0.1, type=stroke_type)))
    assert attrs["stroke-dasharray"] == expected


# ---------------------------------------------------------------------------
# Line, rectangle, circle, polygon
# ---------------------------------------------------------------------------


def test_line_becomes_a_two_point_path() -> None:
    tag, attrs = only(PcbLine(start=(1.0, 2.0), end=(3.0, 4.5)))
    assert tag == "path"
    assert attrs["d"] == "M1.0000,2.0000 L3.0000,4.5000"


def test_rect_becomes_a_closed_four_corner_path() -> None:
    """A path, not an SVG rect element, because a turned host makes it a quad."""
    tag, attrs = only(PcbRect(start=(0.0, 0.0), end=(2.0, 1.0)))
    assert tag == "path"
    assert attrs["d"] == ("M0.0000,0.0000 L2.0000,0.0000 L2.0000,1.0000 L0.0000,1.0000 Z")


def test_rect_corners_stay_in_order_when_start_exceeds_end() -> None:
    _, attrs = only(PcbRect(start=(2.0, 1.0), end=(0.0, 0.0)))
    assert attrs["d"] == ("M2.0000,1.0000 L0.0000,1.0000 L0.0000,0.0000 L2.0000,0.0000 Z")


def test_circle_radius_comes_from_the_perimeter_point() -> None:
    tag, attrs = only(PcbCircle(center=(5.0, 5.0), end=(8.0, 9.0)))
    assert tag == "circle"
    assert attrs == {"cx": "5.0000", "cy": "5.0000", "r": "5.0000"}


def test_polygon_closes_itself() -> None:
    tag, attrs = only(PcbPoly(pts=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]))
    assert tag == "path"
    assert attrs["d"] == "M0.0000,0.0000 L1.0000,0.0000 L1.0000,1.0000 Z"


def test_polygon_keeps_an_arc_run_as_an_arc() -> None:
    """A pts chain interleaves plain points and whole arcs. Keep the curve."""
    poly = PcbPoly(
        pts=[
            (0.0, 0.0),
            PtsArc(start=(1.0, 0.0), mid=(2.0, 1.0), end=(1.0, 2.0)),
            (0.0, 2.0),
        ]
    )
    _, attrs = only(poly)
    assert " A1.0000 1.0000 0 " in attrs["d"]
    assert attrs["d"].endswith("Z")


def test_empty_polygon_draws_nothing() -> None:
    assert shapes_for(PcbPoly(pts=[]), identity_map) == []


# ---------------------------------------------------------------------------
# Arcs
# ---------------------------------------------------------------------------


#: The exact mid point of the quarter turn from (10, 0) to (0, 10).
QUARTER_MID = (math.sqrt(50.0), math.sqrt(50.0))


def test_arc_center_of_a_unit_quarter() -> None:
    center = arc_center((10.0, 0.0), QUARTER_MID, (0.0, 10.0))
    assert center == pytest.approx((0.0, 0.0), abs=1e-3)


def test_arc_emits_a_true_svg_arc_command_never_a_polyline() -> None:
    path = arc_path((10.0, 0.0), QUARTER_MID, (0.0, 10.0), 4)
    assert path == "M10.0000,0.0000 A10.0000 10.0000 0 0 1 0.0000,10.0000"
    assert " L" not in path


def test_arc_sweep_is_one_when_the_angle_grows() -> None:
    """Board and SVG both put y downward, so a growing angle is sweep 1."""
    center = (0.0, 0.0)
    large, sweep = arc_flags((10.0, 0.0), QUARTER_MID, (0.0, 10.0), center)
    assert (large, sweep) == (0, 1)


def test_arc_sweep_is_zero_when_the_angle_shrinks() -> None:
    center = (0.0, 0.0)
    large, sweep = arc_flags((0.0, 10.0), QUARTER_MID, (10.0, 0.0), center)
    assert (large, sweep) == (0, 0)


def test_arc_sets_the_large_flag_past_a_half_turn() -> None:
    """Three quarters of a circle, so the swept angle passes 180 degrees."""
    center = (0.0, 0.0)
    mid = (10.0 * math.cos(math.radians(135)), 10.0 * math.sin(math.radians(135)))
    large, sweep = arc_flags((10.0, 0.0), mid, (0.0, -10.0), center)
    assert (large, sweep) == (1, 1)


def test_arc_does_not_set_the_large_flag_on_a_half_turn() -> None:
    center = (0.0, 0.0)
    large, _ = arc_flags((10.0, 0.0), (0.0, 10.0), (-10.0, 0.0), center)
    assert large == 0


def test_collinear_arc_falls_back_to_a_segment() -> None:
    """Three points on one line put the center at infinity.

    A radius test cannot catch this. An earlier version emitted a radius of
    2.25e15 mm here. The bow of the curve is the safe measure.
    """
    path = arc_path((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), 4)
    assert path == "M0.0000,0.0000 L2.0000,0.0000"


def test_a_bow_below_the_tolerance_falls_back_to_a_segment() -> None:
    path = arc_path((0.0, 0.0), (1.0, 1e-9), (2.0, 0.0), 4)
    assert path == "M0.0000,0.0000 L2.0000,0.0000"


def test_a_bow_above_the_tolerance_stays_an_arc() -> None:
    path = arc_path((0.0, 0.0), (1.0, 1e-3), (2.0, 0.0), 4)
    assert " A" in path


def test_an_arc_whose_ends_meet_draws_two_half_turns() -> None:
    """One arc command cannot close a circle. It would sweep nothing."""
    path = arc_path((0.0, 0.0), (0.0, 10.0), (0.0, 0.0), 4)
    assert path == (
        "M0.0000,0.0000 A5.0000 5.0000 0 0 1 0.0000,10.0000 A5.0000 5.0000 0 0 1 0.0000,0.0000"
    )


def test_arc_item_round_trips_through_shapes_for() -> None:
    tag, attrs = only(PcbArc(start=(10.0, 0.0), mid=QUARTER_MID, end=(0.0, 10.0)))
    assert tag == "path"
    assert "A10.0000 10.0000 0 0 1 " in attrs["d"]


# ---------------------------------------------------------------------------
# The point marker
# ---------------------------------------------------------------------------


def test_point_draws_a_cross_inside_a_ring() -> None:
    """Half the size reaches each diagonal, a quarter gives the ring.

    ``pcb_painter.cpp:3143-3177``. KiCad's plot path never exports this item, so
    the shape comes from the on-screen painter.
    """
    shapes = shapes_for(PcbPoint(position=(10.0, 20.0), size=4.0), identity_map)
    assert shapes is not None
    assert [shape.tag for shape in shapes] == ["path", "circle"]

    cross = dict(shapes[0].attrs)["d"]
    assert cross == ("M8.0000,18.0000 L12.0000,22.0000 M12.0000,18.0000 L8.0000,22.0000")
    assert dict(shapes[1].attrs) == {"cx": "10.0000", "cy": "20.0000", "r": "1.0000"}


def test_point_of_zero_size_draws_nothing() -> None:
    assert shapes_for(PcbPoint(position=(1.0, 1.0), size=0.0), identity_map) == []


# ---------------------------------------------------------------------------
# The support boundary
# ---------------------------------------------------------------------------


def test_text_now_has_an_emitter() -> None:
    """Text used to have none, which left every label off a silkscreen preview.

    It draws with KiCad's built-in stroke font, verified against
    ``kicad-cli pcb export svg`` at 233 of 233 stroke segments.
    """
    shapes = shapes_for(PcbText(content="R1"), identity_map)
    assert shapes is not None
    assert len(shapes) > 0


def test_empty_text_draws_nothing_but_still_has_an_emitter() -> None:
    """None says no emitter exists. An empty list says the emitter drew nothing."""
    assert shapes_for(PcbText(content=""), identity_map) == []


def test_an_unknown_object_has_no_emitter() -> None:
    assert shapes_for(object(), identity_map) is None


def test_a_circle_narrower_than_its_stroke_becomes_a_solid_disc() -> None:
    """``SVG_plotter.cpp:454-462``: a ring that thin would draw a hole in a blob.

    The disc reaches the outer edge of the stroke, so the radius grows by half
    the width. A real board found this: a 0.006 mm circle stroked at 0.254 mm.
    """
    circle = PcbCircle(center=(0.0, 0.0), end=(0.006, 0.0), stroke=Stroke(width=0.254))
    shapes = shapes_for(circle, identity_map)
    assert shapes is not None
    assert shapes[0].filled is True
    assert dict(shapes[0].attrs)["r"] == "0.1330"


def test_a_circle_wider_than_its_stroke_stays_an_outline() -> None:
    circle = PcbCircle(center=(0.0, 0.0), end=(5.0, 0.0), stroke=Stroke(width=0.254))
    shapes = shapes_for(circle, identity_map)
    assert shapes is not None
    assert shapes[0].filled is False
    assert dict(shapes[0].attrs)["r"] == "5.0000"


def test_a_circle_with_no_stroke_stays_an_outline() -> None:
    """A zero width must not turn every circle into a disc."""
    circle = PcbCircle(center=(0.0, 0.0), end=(0.006, 0.0), stroke=Stroke(width=0.0))
    shapes = shapes_for(circle, identity_map)
    assert shapes is not None
    assert shapes[0].filled is False


# ---------------------------------------------------------------------------
# Degenerate arcs (defect report 2026-09-05, item 2)
# ---------------------------------------------------------------------------


def test_the_reported_collinear_arc_renders_as_a_segment() -> None:
    """The reproduction from the defect report: start (0,0) mid (0.5,0) end (1,0)."""
    tag, attrs = only(PcbArc(start=(0.0, 0.0), mid=(0.5, 0.0), end=(1.0, 0.0)))
    assert tag == "path"
    assert attrs["d"] == "M0.0000,0.0000 L1.0000,0.0000"


def test_a_bow_under_one_nanometer_renders_as_a_segment() -> None:
    """The board grid is one nanometer, so a smaller bow is not an arc at all.

    The guard MUST measure on the same grid the center calculation uses. An
    earlier version measured the bow in millimeters on the raw input, then
    computed the center from nanometer-rounded points. A bow of half a nanometer
    passed the first test and was straight by the second, so the center ran to
    infinity and rounding it raised OverflowError.
    """
    assert " A" not in arc_path((0.0, 0.0), (0.5, 5e-7), (1.0, 0.0), 4)


def test_a_bow_over_one_nanometer_stays_an_arc() -> None:
    assert " A" in arc_path((0.0, 0.0), (0.5, 5e-6), (1.0, 0.0), 4)


@pytest.mark.parametrize(
    ("start", "mid", "end"),
    [
        ((0.0, 0.0), (0.5, 0.0), (1.0, 0.0)),
        ((0.0, 0.0), (0.0, 0.5), (0.0, 1.0)),
        ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0)),
        ((1.0, 0.0), (0.5, 0.0), (0.0, 0.0)),
        ((0.0, 0.0), (2.0, 0.0), (1.0, 0.0)),
        ((0.0, 0.0), (0.0, 0.0), (1.0, 0.0)),
        ((0.0, 0.0), (1.0, 0.0), (1.0, 0.0)),
        ((-0.0007233, 0.0003199), (-0.0008389, 0.0006098), (-0.0008413, 0.0006149)),
    ],
)
def test_a_degenerate_arc_never_raises(
    start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]
) -> None:
    """An item the renderer cannot draw is an answer, never an exception.

    The last case is a real one found by search. It raised OverflowError.
    """
    assert arc_path(start, mid, end, 4)


def test_arc_center_says_why_it_cannot_answer() -> None:
    """The low-level helper still refuses, but with a sentence a reader can act on."""
    with pytest.raises(ValueError, match="fall on one line"):
        arc_center((0.0, 0.0), (0.5, 0.0), (1.0, 0.0))


def test_no_arc_in_a_wide_random_sweep_raises_or_runs_away() -> None:
    """A search over near-collinear arcs at six scales found 4 raises before the fix.

    It also produced 13353 radii above a kilometre, which is the same fault
    short of the overflow.
    """
    import random

    random.seed(7)
    for _ in range(20000):
        scale = random.choice([1e-3, 1e-2, 0.1, 1.0, 10.0, 100.0])
        start = (random.uniform(-scale, scale), random.uniform(-scale, scale))
        end = (random.uniform(-scale, scale), random.uniform(-scale, scale))
        along = random.uniform(0.0, 1.0)
        bow = random.choice([0.0, 1e-8, 1e-7, 2e-7, 5e-7])
        mid = (
            start[0] + (end[0] - start[0]) * along,
            start[1] + (end[1] - start[1]) * along + bow,
        )
        assert arc_path(start, mid, end, 4)
