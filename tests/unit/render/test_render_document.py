"""The document: the window, determinism, and the support boundary."""

from __future__ import annotations

import re

import pytest

from eedesigncore.common.models import Stroke
from eedesigncore.common.pcb_family.shapes import (
    PcbCircle,
    PcbLine,
    PcbRect,
    PcbText,
)
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import Board
from eedesigncore.render import (
    LayerStyle,
    RenderItem,
    ViewBox,
    render_board_svg,
)

WINDOW = ViewBox(10.0, 20.0, 100.0, 80.0)
COLORS = {"F.SilkS": "#FFFFFF", "B.SilkS": "#AAAAAA", "Edge.Cuts": "#D0D000"}


def sample_board() -> Board:
    footprint = Footprint(
        fp_id="Test:FP", at=FootprintPlacement(position=(50.0, 50.0), angle=90.0)
    )
    footprint.drawings = [
        PcbLine(start=(1.0, 0.0), end=(2.0, 0.0), layer="F.SilkS", id="fp-line"),
        PcbCircle(center=(0.0, 0.0), end=(1.0, 0.0), layer="F.SilkS", id="fp-circle"),
    ]
    return Board(
        drawings=[
            PcbRect(start=(0.0, 0.0), end=(30.0, 20.0), layer="Edge.Cuts", id="outline"),
            PcbText(content="R1", layer="F.SilkS", id="label"),
        ],
        footprints=[footprint],
    )


def render(board: Board | None = None, **kwargs: object) -> dict[str, str]:
    style = LayerStyle(colors=COLORS)
    return render_board_svg(
        board if board is not None else sample_board(),
        ["F.SilkS", "Edge.Cuts"],
        WINDOW,
        style=style,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_renders_of_one_board_match_byte_for_byte() -> None:
    """Determinism is the invariant for SVG, in place of byte-identity (ADR-098)."""
    assert render() == render()


def test_the_document_carries_no_date() -> None:
    """KiCad writes the current time into a title (``SVG_plotter.cpp:806``).

    That line would make two renders differ, so this renderer writes none.
    """
    page = render()["F.SilkS"]
    assert "<title" not in page
    assert not re.search(r"\d{4}-\d{2}-\d{2}", page)


def test_the_board_is_not_changed_by_a_render() -> None:
    board = sample_board()
    before = board.model_dump_json()
    render(board)
    assert board.model_dump_json() == before


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_the_window_is_the_one_the_caller_gave() -> None:
    """The renderer never crops to content. The caller aligns several renders."""
    page = render()["F.SilkS"]
    assert 'viewBox="10.0000 20.0000 100.0000 80.0000"' in page


def test_the_page_size_restates_the_window_in_millimeters() -> None:
    page = render()["F.SilkS"]
    assert 'width="100.0000mm"' in page
    assert 'height="80.0000mm"' in page


def test_an_empty_layer_still_returns_a_document() -> None:
    """A caller stacking layers needs one page per name, even an empty one."""
    pages = render_board_svg(Board(), ["F.SilkS"], WINDOW, style=LayerStyle(colors=COLORS))
    assert "</svg>" in pages["F.SilkS"]
    assert "<path" not in pages["F.SilkS"]


def test_caps_and_joins_sit_on_the_wrapper_group() -> None:
    """KiCad puts them there too (``SVG_plotter.cpp:830``), so no element repeats them."""
    page = render()["F.SilkS"]
    assert "stroke-linecap:round; stroke-linejoin:round;" in page
    assert page.count("stroke-linecap") == 1


# ---------------------------------------------------------------------------
# The result mapping
# ---------------------------------------------------------------------------


def test_one_document_comes_back_per_requested_layer() -> None:
    pages = render()
    assert list(pages) == ["F.SilkS", "Edge.Cuts"]


def test_a_repeated_layer_name_yields_one_entry() -> None:
    pages = render_board_svg(
        sample_board(),
        ["F.SilkS", "F.SilkS"],
        WINDOW,
        style=LayerStyle(colors=COLORS),
    )
    assert list(pages) == ["F.SilkS"]


def test_each_layer_holds_only_its_own_content() -> None:
    pages = render()
    assert 'id="outline"' in pages["Edge.Cuts"]
    assert 'id="outline"' not in pages["F.SilkS"]
    assert 'id="fp-line"' in pages["F.SilkS"]


# ---------------------------------------------------------------------------
# The support boundary
# ---------------------------------------------------------------------------


def test_text_now_draws_and_is_not_reported_as_skipped() -> None:
    """Text was out of scope until the stroke font landed. Now it draws.

    ``on_skip`` therefore hears nothing for it, and the element carries the
    item's uuid like any other.
    """
    skipped: list[RenderItem] = []
    pages = render(on_skip=skipped.append)

    assert 'id="label"' in pages["F.SilkS"]
    assert skipped == []


def test_an_unsupported_item_needs_no_reporter() -> None:
    assert "</svg>" in render()["F.SilkS"]


def test_a_footprint_shape_lands_in_board_coordinates() -> None:
    """A quarter turn about the origin, then the move to (50, 50)."""
    page = render()["F.SilkS"]
    assert "M50.0000,49.0000 L50.0000,48.0000" in page


def test_a_zero_width_shape_still_reaches_the_page() -> None:
    """It draws nothing, but a caller hunting by identity must still find it."""
    board = Board(
        drawings=[
            PcbLine(
                start=(0.0, 0.0),
                end=(1.0, 1.0),
                layer="F.SilkS",
                stroke=Stroke(width=0.0),
                id="invisible",
            )
        ]
    )
    page = render_board_svg(board, ["F.SilkS"], WINDOW, style=LayerStyle(colors=COLORS))["F.SilkS"]
    assert 'id="invisible"' in page
    assert 'stroke="none"' in page


@pytest.mark.parametrize("precision", [2, 4, 6])
def test_precision_reaches_every_millimeter_value(precision: int) -> None:
    page = render_board_svg(
        sample_board(),
        ["Edge.Cuts"],
        WINDOW,
        style=LayerStyle(colors=COLORS),
        precision=precision,
    )["Edge.Cuts"]
    assert f'viewBox="{10.0:.{precision}f}' in page
    assert f"M{0.0:.{precision}f},{0.0:.{precision}f}" in page
