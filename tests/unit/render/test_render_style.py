"""Color, opacity, filtering and element identity.

No test here asserts a coordinate. Policy is open and geometry is frozen, so the
two sets of tests must be able to change independently (ADR-098).
"""

from __future__ import annotations

from eedesigncore.common.models import Stroke
from eedesigncore.common.pcb_family.shapes import PcbLine, PcbPoint
from eedesigncore.pcb.models import Board
from eedesigncore.render import (
    CliParityStyle,
    LayerStyle,
    ViewBox,
    render_board_svg,
)
from eedesigncore.render.traversal import iter_render_items

WINDOW = ViewBox(0.0, 0.0, 100.0, 100.0)


def one_item(layer: str = "F.SilkS", uuid: str | None = "u-1") -> Board:
    return Board(
        drawings=[
            PcbLine(
                start=(1.0, 1.0), end=(2.0, 2.0), layer=layer, id=uuid, stroke=Stroke(width=0.2)
            )
        ]
    )


def sole(board: Board, layer: str = "F.SilkS") -> object:
    (found,) = list(iter_render_items(board, layer))
    return found


# ---------------------------------------------------------------------------
# LayerStyle: the general tool
# ---------------------------------------------------------------------------


def test_layer_style_uses_the_color_the_caller_gave() -> None:
    style = LayerStyle(colors={"F.SilkS": "#123456"})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'stroke="#123456"' in page


def test_layer_style_keeps_a_partial_opacity() -> None:
    """Nothing here collapses an alpha to 1.0. That is the point of the split."""
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF"}, opacity={"F.SilkS": 0.35})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'stroke-opacity="0.3500"' in page


def test_layer_style_defaults_to_full_opacity() -> None:
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF"})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'stroke-opacity="1.0000"' in page


def test_a_layer_with_no_color_drops_its_items() -> None:
    """Color absence is a filter, so a caller selects content by naming layers."""
    style = LayerStyle(colors={})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert "<path" not in page


def test_a_default_color_catches_an_unnamed_layer() -> None:
    style = LayerStyle(colors={}, default_color="#00FF00")
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'stroke="#00FF00"' in page


def test_a_custom_policy_can_drop_one_item() -> None:
    """``style_for`` returning None is the whole filtering mechanism."""

    class DropEverything:
        emit_ids = False
        point_stroke_width_mm = 0.05

        def style_for(self, render_item: object) -> None:
            return None

    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=DropEverything())["F.SilkS"]
    assert "<path" not in page


# ---------------------------------------------------------------------------
# Element identity
# ---------------------------------------------------------------------------


def test_emit_ids_writes_the_item_uuid() -> None:
    """A live preview needs to know which element the user clicked."""
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF"}, emit_ids=True)
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'id="u-1"' in page


def test_emit_ids_off_writes_no_id() -> None:
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF"}, emit_ids=False)
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert "id=" not in page


def test_an_item_with_no_uuid_gets_no_id_attribute() -> None:
    style = LayerStyle(colors={"F.SilkS": "#FFFFFF"}, emit_ids=True)
    board = one_item(uuid=None)
    page = render_board_svg(board, ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert "id=" not in page


# ---------------------------------------------------------------------------
# CliParityStyle: the permanent oracle preset
# ---------------------------------------------------------------------------


def test_parity_forces_full_opacity() -> None:
    """``SVG_PLOTTER::StartPlot`` hard-codes opacity 1.0 (``SVG_plotter.cpp:818``)."""
    style = CliParityStyle(colors={"F.SilkS": "#FFFFFF"})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert 'stroke-opacity="1.0000"' in page


def test_parity_writes_no_ids() -> None:
    style = CliParityStyle(colors={"F.SilkS": "#FFFFFF"})
    page = render_board_svg(one_item(), ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    assert "id=" not in page


def test_parity_drops_the_point_marker() -> None:
    """KiCad's plot path holds no branch for the point item, so kicad-cli emits none.

    Drawing one here would make the oracle disagree over content KiCad never had.
    """
    board = Board(points=[PcbPoint(position=(5.0, 5.0), size=2.0, layer="F.Cu")])
    style = CliParityStyle(colors={"F.Cu": "#FFFFFF"})
    page = render_board_svg(board, ["F.Cu"], WINDOW, style=style)["F.Cu"]
    assert "<path" not in page
    assert "<circle" not in page


def test_the_general_tool_does_draw_the_point_marker() -> None:
    """The point is a place where this renderer goes past kicad-cli on purpose."""
    board = Board(points=[PcbPoint(position=(5.0, 5.0), size=2.0, layer="F.Cu")])
    style = LayerStyle(colors={"F.Cu": "#FFFFFF"})
    page = render_board_svg(board, ["F.Cu"], WINDOW, style=style)["F.Cu"]
    assert "<path" in page
    assert "<circle" in page


def test_a_point_takes_its_stroke_width_from_the_policy() -> None:
    """The marker width is not a board dimension, so policy owns it."""
    board = Board(points=[PcbPoint(position=(5.0, 5.0), size=2.0, layer="F.Cu")])
    style = LayerStyle(colors={"F.Cu": "#FFFFFF"}, point_stroke_width_mm=0.2)
    page = render_board_svg(board, ["F.Cu"], WINDOW, style=style)["F.Cu"]
    assert 'stroke-width="0.2000"' in page
