"""The rounded and chamfered rectangle approximation.

Every number in this file was taken from a ``kicad-cli pcb export svg`` run on a
board built for the purpose, not from this code's own output. The vertex lists in
:data:`KICAD_ROUNDRECT_5x3_R075` and its neighbours are what KiCad wrote.
"""

from __future__ import annotations

import math

import pytest

from eedesigncore.common.polygon import (
    MAX_COORDINATE_IU,
    MIN_SEGMENTS_PER_CORNER_CIRCLE,
    PLOTTER_ARC_HIGH_DEF_IU,
    RECT_CHAMFER_BOTTOM_LEFT,
    RECT_CHAMFER_BOTTOM_RIGHT,
    RECT_CHAMFER_TOP_LEFT,
    RECT_CHAMFER_TOP_RIGHT,
    RoundedCorner,
    arc_to_segment_count,
    cdiv,
    corner_list_to_polygon,
    perpendicular,
    reflect_point,
    rescale,
    resize,
    round_chamfered_rect_polygon,
)

IU = 1_000_000
ALL_CHAMFERS = (
    RECT_CHAMFER_TOP_LEFT
    | RECT_CHAMFER_TOP_RIGHT
    | RECT_CHAMFER_BOTTOM_LEFT
    | RECT_CHAMFER_BOTTOM_RIGHT
)

#: What ``kicad-cli`` wrote for a 5.0 x 3.0 mm roundrect pad at ratio 0.25.
#:
#: Centred on (90, 50) mm on the probe board. Only the first eight vertices are
#: kept: they are one whole corner, which is what pins the arc down. The full
#: polygon is 32 vertices and the count is asserted separately.
KICAD_ROUNDRECT_5x3_R075 = [
    (87.5000, 49.2500),
    (87.5155, 49.0983),
    (87.5703, 48.9330),
    (87.6617, 48.7848),
    (87.7848, 48.6617),
    (87.9330, 48.5703),
    (88.0983, 48.5155),
    (88.2500, 48.5000),
]

#: Vertex counts ``kicad-cli`` produced, per probe case.
#:
#: ``roundrect-50`` is the interesting one. Four corners of nine vertices is 36
#: before the arcs meet; a radius of half the shorter side gives the straight
#: edges zero length, so three joins collapse and 48 becomes 45.
KICAD_VERTEX_COUNTS = {
    "roundrect-5x3-r0.25": 32,
    "roundrect-4x4-r0.50": 45,
    "roundrect-5x3-r0.20-rot35": 28,
    "chamfer-5x3-one-corner": 23,
    "chamfer-4x4-all-corners": 8,
}


def mm(polygon: list[tuple[int, int]]) -> list[tuple[float, float]]:
    return [(x / IU, y / IU) for x, y in polygon]


def roundrect(
    size_mm: tuple[float, float],
    ratio: float,
    *,
    chamfer_ratio: float = 0.0,
    chamfer_corners: int = 0,
    angle: float = 0.0,
    centre_mm: tuple[float, float] = (0.0, 0.0),
) -> list[tuple[float, float]]:
    size = (round(size_mm[0] * IU), round(size_mm[1] * IU))
    radius = round(min(size) * ratio)
    return mm(
        round_chamfered_rect_polygon(
            size,
            corner_radius=radius,
            chamfer_ratio=chamfer_ratio,
            chamfer_corners=chamfer_corners,
            angle_degrees=angle,
            position=(round(centre_mm[0] * IU), round(centre_mm[1] * IU)),
            error=PLOTTER_ARC_HIGH_DEF_IU,
        )
    )


# ---------------------------------------------------------------------------
# Against kicad-cli
# ---------------------------------------------------------------------------


def test_one_corner_matches_the_vertices_kicad_cli_wrote() -> None:
    """The arc is not approximately right; it is right to the written digit."""
    ours = roundrect((5.0, 3.0), 0.25, centre_mm=(90.0, 50.0))
    for index, expected in enumerate(KICAD_ROUNDRECT_5x3_R075):
        gap = math.dist(ours[index], expected)
        assert gap < 0.0001, (
            f"vertex {index}: {ours[index]} is {gap * 1000:.4f} um from {expected}"
        )


@pytest.mark.parametrize(
    ("case", "expected"),
    sorted(KICAD_VERTEX_COUNTS.items()),
)
def test_vertex_count_matches_kicad_cli(case: str, expected: int) -> None:
    """A count that drifts means the segment arithmetic drifted."""
    built = {
        "roundrect-5x3-r0.25": lambda: roundrect((5.0, 3.0), 0.25),
        "roundrect-4x4-r0.50": lambda: roundrect((4.0, 4.0), 0.50),
        "roundrect-5x3-r0.20-rot35": lambda: roundrect((5.0, 3.0), 0.20, angle=35.0),
        "chamfer-5x3-one-corner": lambda: roundrect(
            (5.0, 3.0), 0.20, chamfer_ratio=0.3, chamfer_corners=RECT_CHAMFER_TOP_LEFT
        ),
        "chamfer-4x4-all-corners": lambda: roundrect(
            (4.0, 4.0), 0.15, chamfer_ratio=0.25, chamfer_corners=ALL_CHAMFERS
        ),
    }[case]
    assert len(built()) == expected


def test_the_duplicate_drop_is_what_gives_45_not_48() -> None:
    """Name the mechanism, so a future change to it fails here and not elsewhere.

    ``SHAPE_LINE_CHAIN::Append`` drops a point equal to the one before it. It
    never compares the first against the last, so a fully circular pad keeps one
    duplicate of the four and loses three.
    """
    polygon = roundrect((4.0, 4.0), 0.50)
    assert len(polygon) == 45

    consecutive_duplicates = sum(1 for a, b in zip(polygon, polygon[1:], strict=False) if a == b)
    assert consecutive_duplicates == 0, "Append must leave no consecutive duplicate"
    assert polygon[0] == polygon[-1], (
        "the wrap-around duplicate must SURVIVE, because Append never compares "
        "the first point against the last"
    )


def test_all_four_chamfers_give_an_octagon() -> None:
    """A chamfered corner loses its radius, so four chamfers leave eight corners."""
    assert len(roundrect((4.0, 4.0), 0.15, chamfer_ratio=0.25, chamfer_corners=ALL_CHAMFERS)) == 8


# ---------------------------------------------------------------------------
# The integer primitives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [(7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3), (0, 5, 0)],
)
def test_cdiv_truncates_toward_zero(numerator: int, denominator: int, expected: int) -> None:
    """Python's ``//`` floors. C++ truncates. Half a centred pad is negative."""
    assert cdiv(numerator, denominator) == expected
    if numerator * denominator < 0:
        assert cdiv(numerator, denominator) != numerator // denominator


def test_rescale_rounds_to_nearest() -> None:
    """``util.cpp:66-76`` biases the dividend, so 2/3 rounds up rather than down."""
    assert rescale(2, 1, 3) == 1
    assert rescale(1, 1, 3) == 0
    assert rescale(-2, 1, 3) == -1


def test_resize_takes_the_exact_diagonal_for_a_45_degree_vector() -> None:
    """``vector2d.h:393-396`` branches on equal components, and the branch shows.

    The exact ``1/sqrt(2)`` differs from the integer-rescaled square root, so a
    45-degree vector and a 44-degree one do not resize by the same rule.
    """
    exact = resize((1000, 1000), 1000)
    assert exact == (707, 707)
    assert exact[0] == round(1000 * math.sqrt(0.5))


def test_resize_of_a_zero_vector_is_zero() -> None:
    assert resize((0, 0), 5000) == (0, 0)


def test_perpendicular_turns_a_quarter_turn() -> None:
    assert perpendicular((3, 4)) == (-4, 3)


def test_reflect_point_mirrors_across_an_infinite_line() -> None:
    """The corner arc's end point is the start reflected across the bisector."""
    assert reflect_point((0, 0), (10, 10), (10, 0)) == (0, 10)


def test_reflect_point_on_a_degenerate_segment_returns_the_point() -> None:
    assert reflect_point((5, 5), (5, 5), (9, 1)) == (9, 1)


def test_arc_to_segment_count_is_clamped_at_both_ends() -> None:
    """A tiny radius still gets 8 per turn, and the floor is 2 segments."""
    assert arc_to_segment_count(1, 5080, 360.0) == 8
    assert arc_to_segment_count(0, 5080, 360.0) == 8
    assert arc_to_segment_count(1_000_000, 5080, 1.0) == 2


def test_a_radius_under_half_the_error_does_not_raise() -> None:
    """The NaN branch of KiCad's ``acos``, which Python raises on instead.

    ``radius < error / 2`` sends ``1 - error/radius`` below -1. C++ ``acos``
    returns NaN, and ``std::min( clamp, NaN )`` returns the clamp, so KiCad ends
    up with 8 segments per turn. Python raises unless the branch is explicit, so
    a pad with a corner radius under 2.54 microns would crash the renderer.
    """
    assert arc_to_segment_count(2539, PLOTTER_ARC_HIGH_DEF_IU, 360.0) == 8
    assert arc_to_segment_count(1, PLOTTER_ARC_HIGH_DEF_IU, 360.0) == 8

    # Reached through the public entry point too: a 4 micron corner radius.
    polygon = round_chamfered_rect_polygon(
        (100_000, 100_000), corner_radius=4_000, error=PLOTTER_ARC_HIGH_DEF_IU
    )
    assert len(polygon) >= 8


def test_the_plot_error_constant_is_two_decimils() -> None:
    """``GetPlotterArcHighDef`` is ``m_IUsPerDecimil * 2``, and a decimil is 2540 nm."""
    assert PLOTTER_ARC_HIGH_DEF_IU == 2540 * 2


# ---------------------------------------------------------------------------
# corner_list_to_polygon on its own
# ---------------------------------------------------------------------------


def test_a_sharp_corner_list_passes_through_unchanged() -> None:
    """Radius zero everywhere means the polygon IS the corner list."""
    corners = [
        RoundedCorner((-1000, -1000)),
        RoundedCorner((1000, -1000)),
        RoundedCorner((1000, 1000)),
        RoundedCorner((-1000, 1000)),
    ]
    assert corner_list_to_polygon(corners, PLOTTER_ARC_HIGH_DEF_IU) == [
        corner.position for corner in corners
    ]


def test_a_corner_never_gets_fewer_than_four_segments() -> None:
    """16 per full turn is the floor, so a 90-degree corner gets at least four.

    ``convert_basic_shapes_to_polygon.cpp:291`` sets that floor UNDER whatever
    :func:`arc_to_segment_count` returns, so a very small radius is still a curve
    rather than a single cut corner.
    """
    polygon = roundrect((0.4, 0.4), 0.25)
    assert MIN_SEGMENTS_PER_CORNER_CIRCLE == 16
    assert len(polygon) >= 4 * 4


def test_a_zero_ratio_gives_a_plain_rectangle() -> None:
    assert roundrect((5.0, 3.0), 0.0) == [(-2.5, -1.5), (2.5, -1.5), (2.5, 1.5), (-2.5, 1.5)]


def test_the_outline_is_built_then_turned_then_moved() -> None:
    """The order matters: rounding happens about the origin, not about *position*.

    Turning a polygon that was built at its final position would round the
    corners in the board frame and land on different nanometres.
    """
    turned_then_moved = roundrect((5.0, 3.0), 0.25, angle=35.0, centre_mm=(90.0, 50.0))
    at_origin = roundrect((5.0, 3.0), 0.25, angle=35.0)
    for placed, local in zip(turned_then_moved, at_origin, strict=True):
        assert placed[0] == pytest.approx(local[0] + 90.0, abs=1e-9)
        assert placed[1] == pytest.approx(local[1] + 50.0, abs=1e-9)


# ---------------------------------------------------------------------------
# A hostile file must not hang the renderer
# ---------------------------------------------------------------------------


def test_the_segment_count_is_bounded() -> None:
    """A pad size no KiCad file can hold must not ask for unbounded work.

    KiCad keeps every coordinate in a C++ int, so a radius past
    ``MAX_COORDINATE_IU`` cannot come out of a real file. Without the clamp the
    segment count rises with the radius: a radius of 1e19 internal units asked
    for 94 million segments per corner, out of a file a few hundred bytes long.
    """
    at_limit = arc_to_segment_count(MAX_COORDINATE_IU, PLOTTER_ARC_HIGH_DEF_IU, 360.0)
    for radius in (10**12, 10**19, 10**25, 10**40):
        assert arc_to_segment_count(radius, PLOTTER_ARC_HIGH_DEF_IU, 360.0) == at_limit
    assert at_limit < 2000, f"{at_limit} segments per turn is too many to be a bound"


def test_a_radius_past_the_coordinate_limit_does_not_raise() -> None:
    """The second failure the clamp closes.

    Past about 1e20 internal units, ``error / radius`` falls below the double
    precision step at 1.0. Then ``1.0 - relative_error`` IS 1.0, ``acos``
    returns 0.0, and dividing by the increment raised ``ZeroDivisionError``.
    """
    for radius in (10**20, 10**25, 10**40):
        assert arc_to_segment_count(radius, 1, 360.0) > 0


def test_the_clamp_changes_no_representable_radius() -> None:
    """Exactness inside KiCad's own range is the thing the clamp must not cost."""
    for radius in (1, 750_000, 2_000_000, MAX_COORDINATE_IU):
        expected = _unclamped_segment_count(radius, PLOTTER_ARC_HIGH_DEF_IU, 360.0)
        assert arc_to_segment_count(radius, PLOTTER_ARC_HIGH_DEF_IU, 360.0) == expected


def _unclamped_segment_count(radius: int, error_max: int, angle: float) -> int:
    """The pre-clamp arithmetic, kept here to prove the clamp is a no-op below it.

    The small-radius branch is copied too. It is a separate guard with its own
    test, and leaving it out would make this helper raise where the real function
    does not.
    """
    radius = max(1, radius)
    error_max = max(1, error_max)
    argument = 1.0 - error_max / radius
    if -1.0 <= argument <= 1.0:
        increment = min(360.0 / 8, 180 / math.pi * math.acos(argument) * 2)
    else:
        increment = 360.0 / 8
    return max(round(abs(angle) / increment), 2)


def test_a_huge_pad_renders_quickly_and_small() -> None:
    """The attack, end to end, from a file rather than from a function call.

    A board this size is not valid, but a parser that accepts it must not let it
    consume the process.
    """
    import time

    from eedesigncore import BoardContent, LayerStyle, ViewBox, render_board_svg
    from eedesigncore.pcb.models import Board

    text = (
        '(kicad_pcb (version 20241229) (generator "x") (generator_version "9.0")\n'
        ' (footprint "t:t" (layer "F.Cu") (uuid "00000000-0000-4000-8000-000000000001")\n'
        "  (at 10 10)\n"
        '  (pad "1" smd roundrect (at 0 0) (size 100000000000000.0 100000000000000.0)\n'
        '   (layers "F.Cu") (roundrect_rratio 0.5)\n'
        '   (uuid "00000000-0000-4000-8000-000000000002"))))'
    )
    board = Board.from_string(text)

    started = time.perf_counter()
    svg = render_board_svg(
        board,
        ["F.Cu"],
        ViewBox(0.0, 0.0, 50.0, 50.0),
        style=LayerStyle(colors={"F.Cu": "#FFFFFF"}),
        content=frozenset({BoardContent.pads}),
    )["F.Cu"]
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"the render took {elapsed:.1f} s"
    assert len(svg) < 1_000_000, f"the render wrote {len(svg):,} bytes"
