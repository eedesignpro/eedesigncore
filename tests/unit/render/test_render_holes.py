"""Holes: discovery, geometry, and the pseudo-layers that carry them.

A hole is not on a layer. KiCad draws drill marks as their own pass over
everything else, so a caller stacking per-layer documents needs holes as one more
document rather than as content of a copper layer.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    Board,
    BoardLayer,
    DrillInfo,
    Footprint,
    FootprintPlacement,
    LayerStyle,
    Pad,
    PadShape,
    PadType,
    Via,
    ViewBox,
    render_board_svg,
)
from eedesigncore.render import HOLES, HOLES_NON_PLATED, HOLES_PLATED
from eedesigncore.render.geometry import hole_shapes, oval_hole_path
from eedesigncore.render.traversal import iter_holes

WINDOW = ViewBox(0.0, 0.0, 100.0, 100.0)


def copper_board() -> Board:
    """A board with a two-layer copper stack, so a through via can span it."""
    return Board(
        layers=[
            BoardLayer(ordinal=0, canonical_name="F.Cu", layer_type="signal"),
            BoardLayer(ordinal=2, canonical_name="B.Cu", layer_type="signal"),
        ]
    )


def hole_pad(
    pad_type: PadType = PadType.thru_hole,
    drill: DrillInfo | None = None,
    angle: float = 0.0,
) -> Pad:
    return Pad(
        number="1",
        pad_type=pad_type,
        shape=PadShape.circle,
        position=(10.0, 20.0),
        angle=angle,
        size=(2.0, 2.0),
        drill=drill if drill is not None else DrillInfo(width=1.0),
        layers=["*.Cu"],
    )


def board_with_pad(**kwargs: object) -> Board:
    footprint = Footprint(fp_id="Test:FP", at=FootprintPlacement(position=(0.0, 0.0), angle=0.0))
    footprint.pads = [hole_pad(**kwargs)]  # type: ignore[arg-type]
    board = copper_board()
    board.footprints = [footprint]
    return board


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_a_through_hole_pad_gives_a_plated_hole() -> None:
    (hole,) = list(iter_holes(board_with_pad()))
    assert hole.plated is True
    assert hole.source == "pad"
    assert hole.position == (10.0, 20.0)
    assert (hole.width, hole.height) == (1.0, 1.0)


def test_a_non_plated_pad_gives_a_non_plated_hole() -> None:
    (hole,) = list(iter_holes(board_with_pad(pad_type=PadType.np_thru_hole)))
    assert hole.plated is False


def test_a_surface_mount_pad_has_no_hole() -> None:
    board = board_with_pad(pad_type=PadType.smd)
    board.footprints[0].pads[0].drill = None
    assert list(iter_holes(board)) == []


def test_a_pad_with_a_zero_drill_has_no_hole() -> None:
    """KiCad skips these too (``plot_brditems_plotter.cpp:1311-1312``)."""
    assert list(iter_holes(board_with_pad(drill=DrillInfo(width=0.0)))) == []


def test_a_via_gives_a_plated_hole() -> None:
    board = copper_board()
    board.tracks = [Via(at=(5.0, 6.0), size=0.8, drill=0.4, layers=("F.Cu", "B.Cu"))]
    (hole,) = list(iter_holes(board))
    assert hole.plated is True
    assert hole.source == "via"
    assert hole.position == (5.0, 6.0)
    assert (hole.width, hole.height) == (0.4, 0.4)


def test_a_via_with_no_drill_has_no_hole() -> None:
    board = copper_board()
    board.tracks = [Via(at=(5.0, 6.0), size=0.8, drill=0.0, layers=("F.Cu", "B.Cu"))]
    assert list(iter_holes(board)) == []


def test_a_footprint_placement_moves_a_pad_hole() -> None:
    """A pad position is footprint-local, so the placement carries it."""
    board = board_with_pad()
    board.footprints[0].at = FootprintPlacement(position=(100.0, 50.0), angle=90.0)
    (hole,) = list(iter_holes(board))
    assert hole.position == (120.0, 40.0)


def test_a_pad_angle_is_board_absolute_and_needs_no_further_turn() -> None:
    """The frame audit records the pad ANGLE as board-absolute already."""
    board = board_with_pad(angle=30.0)
    board.footprints[0].at = FootprintPlacement(position=(0.0, 0.0), angle=90.0)
    (hole,) = list(iter_holes(board))
    assert hole.angle == 30.0


def test_a_through_via_spans_every_copper_layer() -> None:
    board = copper_board()
    board.tracks = [Via(at=(5.0, 6.0), size=0.8, drill=0.4, layers=("F.Cu", "B.Cu"))]
    (hole,) = list(iter_holes(board))
    assert hole.touches("F.Cu")
    assert hole.touches("B.Cu")


def test_a_hole_reports_the_layers_it_passes_through() -> None:
    """A caller uses this to rebuild the per-layer subset kicad-cli plots."""
    (hole,) = list(iter_holes(board_with_pad()))
    assert hole.touches("F.Cu")
    assert not hole.touches("Edge.Cuts")


# ---------------------------------------------------------------------------
# The three pseudo-layers
# ---------------------------------------------------------------------------


def mixed_board() -> Board:
    plated = Footprint(fp_id="P:1", at=FootprintPlacement(position=(0.0, 0.0)))
    plated.pads = [hole_pad(pad_type=PadType.thru_hole)]
    bare = Footprint(fp_id="N:1", at=FootprintPlacement(position=(30.0, 0.0)))
    bare.pads = [hole_pad(pad_type=PadType.np_thru_hole)]
    board = copper_board()
    board.footprints = [plated, bare]
    board.tracks = [Via(at=(5.0, 6.0), size=0.8, drill=0.4, layers=("F.Cu", "B.Cu"))]
    return board


@pytest.mark.parametrize(
    ("layer", "expected"),
    [(HOLES, 3), (HOLES_PLATED, 2), (HOLES_NON_PLATED, 1)],
)
def test_each_pseudo_layer_selects_its_own_holes(layer: str, expected: int) -> None:
    assert len(list(iter_holes(mixed_board(), layer))) == expected


def test_a_hole_layer_returns_its_own_document() -> None:
    style = LayerStyle(colors={HOLES: "#000000"})
    pages = render_board_svg(mixed_board(), [HOLES], WINDOW, style=style)
    assert list(pages) == [HOLES]
    assert pages[HOLES].count("<circle") == 3


def test_hole_layers_mix_with_canonical_layers_in_one_call() -> None:
    style = LayerStyle(colors={HOLES: "#000000", "F.Cu": "#FF0000"})
    pages = render_board_svg(mixed_board(), ["F.Cu", HOLES], WINDOW, style=style)
    assert list(pages) == ["F.Cu", HOLES]
    assert pages[HOLES].count("<circle") == 3
    assert "<circle" not in pages["F.Cu"]


def test_a_policy_can_split_plated_from_non_plated_in_one_document() -> None:
    """The other way to tell them apart, for a caller who wants a single page."""
    from eedesigncore.render import ElementStyle

    class ByPlating:
        emit_ids = False
        point_stroke_width_mm = 0.05

        def style_for(self, render_item: object) -> ElementStyle:
            plated = render_item.item.plated  # type: ignore[attr-defined]
            return ElementStyle(stroke="#00FF00" if plated else "#FF0000")

    page = render_board_svg(mixed_board(), [HOLES], WINDOW, style=ByPlating())[HOLES]
    assert page.count('fill="#00FF00"') == 2
    assert page.count('fill="#FF0000"') == 1


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_a_round_hole_is_a_true_circle() -> None:
    (shape,) = hole_shapes((10.0, 20.0), 1.0, 1.0, 0.0)
    assert shape.tag == "circle"
    assert dict(shape.attrs) == {"cx": "10.0000", "cy": "20.0000", "r": "0.5000"}
    assert shape.filled is True


def test_a_slot_of_equal_sides_is_a_circle() -> None:
    """The two cases must not disagree about the same opening."""
    (shape,) = hole_shapes((0.0, 0.0), 1.0, 1.0, 0.0)
    assert shape.tag == "circle"


def test_a_hole_of_no_size_draws_nothing() -> None:
    assert hole_shapes((0.0, 0.0), 0.0, 0.0, 0.0) == []


def test_a_slot_is_two_sides_and_two_true_arcs() -> None:
    """Never polygonized. The caps are SVG arc commands."""
    path = oval_hole_path((0.0, 0.0), 1.6, 0.7, 0.0, 4)
    assert path.count("A0.3500 0.3500") == 2
    assert path.count("L") == 2
    assert path.endswith("Z")


def test_a_wide_slot_runs_along_x() -> None:
    path = oval_hole_path((10.0, 20.0), 1.6, 0.7, 0.0, 4)
    assert path.startswith("M9.5500,19.6500 L10.4500,19.6500")


def test_a_tall_slot_runs_along_y() -> None:
    path = oval_hole_path((10.0, 20.0), 0.7, 1.6, 0.0, 4)
    assert path.startswith("M10.3500,19.5500 L10.3500,20.4500")


def test_a_quarter_turn_makes_a_wide_slot_tall() -> None:
    """A turn is exact at a quadrant angle, so the two forms line up."""
    turned = oval_hole_path((10.0, 20.0), 1.6, 0.7, 90.0, 4)
    upright = oval_hole_path((10.0, 20.0), 0.7, 1.6, 0.0, 4)
    assert set(turned.replace("M", " ").replace("L", " ").split()) == set(
        upright.replace("M", " ").replace("L", " ").split()
    )


def test_a_slot_reaches_its_full_length() -> None:
    """Cap centres are half the straight run apart, and the caps add the rest."""
    from eedesigncore.render.compare import read_holes

    path = oval_hole_path((10.0, 20.0), 1.6, 0.7, 0.0, 4)
    (hole,) = read_holes(f'<svg><path d="{path}"/></svg>')

    _, x1, _y1, x2, _y2, radius = hole
    assert float(x2) - float(x1) == pytest.approx(0.9)
    assert float(radius) == pytest.approx(0.35)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_two_hole_renders_match_byte_for_byte() -> None:
    style = LayerStyle(colors={HOLES: "#000000"})
    board = mixed_board()
    first = render_board_svg(board, [HOLES], WINDOW, style=style)
    assert first == render_board_svg(board, [HOLES], WINDOW, style=style)


def test_rendering_holes_does_not_change_the_board() -> None:
    board = mixed_board()
    before = board.model_dump_json()
    render_board_svg(board, [HOLES], WINDOW, style=LayerStyle(colors={HOLES: "#000"}))
    assert board.model_dump_json() == before


def test_a_hole_carries_its_identity() -> None:
    board = board_with_pad()
    board.footprints[0].pads[0].id = "pad-uuid-1"
    style = LayerStyle(colors={HOLES: "#000000"}, emit_ids=True)
    page = render_board_svg(board, [HOLES], WINDOW, style=style)[HOLES]
    assert 'id="pad-uuid-1"' in page


def test_a_board_with_no_holes_still_returns_a_document() -> None:
    style = LayerStyle(colors={HOLES: "#000000"})
    page = render_board_svg(copper_board(), [HOLES], WINDOW, style=style)[HOLES]
    assert "</svg>" in page
    assert "<circle" not in page
