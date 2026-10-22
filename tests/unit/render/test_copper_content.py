"""Copper content in a render: tracks, vias and zones.

The renderer drew only drawings until now, so a preview of a routed board showed
no copper at all. These streams are opt-in per call, because copper is far larger
than the drawings: one corpus board holds 3188 tracks against 160 footprints.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    ALL_CONTENT,
    DEFAULT_CONTENT,
    Board,
    BoardContent,
    CliParityStyle,
    ViewBox,
    render_board_svg,
)
from eedesigncore.render.traversal import iter_render_items, render_items_by_layer, via_layers

_BOARD = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6)) (paper "A4")
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (2 "B.Cu" signal) (44 "Edge.Cuts" user))
  ;; KiCad orders copper rows F.Cu, inner, B.Cu, and In1.Cu is ordinal 4.
  ;; Ordinal 1 is F.Mask, so a made-up number here silently drops the layer.
  (setup (pad_to_mask_clearance 0))
  (net 0 "") (net 1 "N1")
  (gr_line (start 0 0) (end 1 1) (layer "Edge.Cuts")
    (stroke (width 0.1) (type solid)) (uuid "g1"))
  (segment (start 0 0) (end 10 0) (width 0.25) (layer "F.Cu") (net 1) (uuid "s1"))
  (arc (start 0 5) (mid 5 7) (end 10 5) (width 0.4) (layer "F.Cu") (net 1) (uuid "a1"))
  (via (at 3 3) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1) (uuid "v1"))
  (zone (net 1) (net_name "N1") (layer "F.Cu") (uuid "z1") (hatch edge 0.5)
    (connect_pads (clearance 0.2)) (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 0 10) (xy 10 10) (xy 10 20) (xy 0 20)))
    (filled_polygon (layer "F.Cu") (pts (xy 1 11) (xy 9 11) (xy 9 19) (xy 1 19))))
)"""

_UNFILLED = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6)) (paper "A4")
  (layers (0 "F.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0))
  (net 0 "") (net 1 "N1")
  (zone (net 1) (net_name "N1") (layer "F.Cu") (uuid "z1") (hatch edge 0.5)
    (connect_pads (clearance 0.2)) (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 0 10) (xy 10 10) (xy 10 20) (xy 0 20))))
)"""

COPPER = ["F.Cu", "In1.Cu", "B.Cu"]
COLORS = {"F.Cu": "#c83434", "In1.Cu": "#3434c8", "B.Cu": "#34c834", "Edge.Cuts": "#d0d2cd"}


@pytest.fixture
def board() -> Board:
    return Board.from_string(_BOARD)


@pytest.fixture
def view_box() -> ViewBox:
    return ViewBox(min_x=-1.0, min_y=-1.0, width=30.0, height=30.0)


def _pages(board: Board, view_box: ViewBox, **kwargs: object) -> dict[str, str]:
    style = CliParityStyle(colors=COLORS)
    return render_board_svg(board, COPPER, view_box, style=style, **kwargs)  # type: ignore[arg-type]


def _elements(page: str) -> list[str]:
    return [
        line.strip() for line in page.splitlines() if line.strip().startswith(("<path", "<circle"))
    ]


class TestTheDefaultIsUnchanged:
    """Copper must not arrive by surprise. The cli-parity oracle depends on it."""

    def test_the_default_draws_no_copper(self, board: Board, view_box: ViewBox) -> None:
        pages = _pages(board, view_box)
        assert _elements(pages["F.Cu"]) == []

    def test_the_default_names_drawings_only(self) -> None:
        assert frozenset({BoardContent.drawings}) == DEFAULT_CONTENT

    def test_all_content_names_every_stream(self) -> None:
        assert frozenset(BoardContent) == ALL_CONTENT


class TestTracks:
    def test_a_segment_draws_at_its_own_width(self, board: Board, view_box: ViewBox) -> None:
        """A track carries a bare ``width``, not a ``(stroke …)`` block.

        Reading it from the policy instead would draw every track at the point
        marker width of 0.05 mm.
        """
        pages = _pages(board, view_box, content={BoardContent.tracks})
        segment = next(e for e in _elements(pages["F.Cu"]) if "L10.0000,0.0000" in e)
        assert 'stroke-width="0.2500"' in segment

    def test_a_track_arc_stays_a_curve(self, board: Board, view_box: ViewBox) -> None:
        """KiCad never polygonizes an arc, so neither does this renderer."""
        pages = _pages(board, view_box, content={BoardContent.tracks})
        arc = next(e for e in _elements(pages["F.Cu"]) if " A" in e)
        assert 'stroke-width="0.4000"' in arc

    def test_a_track_is_absent_from_a_layer_it_does_not_name(
        self, board: Board, view_box: ViewBox
    ) -> None:
        pages = _pages(board, view_box, content={BoardContent.tracks})
        assert not any("L10.0000,0.0000" in e for e in _elements(pages["B.Cu"]))


class TestVias:
    def test_a_via_is_a_solid_disc_of_half_its_size(self, board: Board, view_box: ViewBox) -> None:
        pages = _pages(board, view_box, content={BoardContent.tracks})
        disc = next(e for e in _elements(pages["F.Cu"]) if e.startswith("<circle"))
        assert 'r="0.4000"' in disc
        assert 'fill="#c83434"' in disc
        assert 'stroke="none"' in disc

    def test_a_via_reaches_every_layer_in_its_SPAN(self, board: Board, view_box: ViewBox) -> None:
        """The stored pair is a span, not a list.

        ``(layers "F.Cu" "B.Cu")`` names two layers and reaches three. Matching
        the names alone would drop the via from every inner layer, and a preview
        of In1.Cu would show a hole where copper is.
        """
        pages = _pages(board, view_box, content={BoardContent.tracks})
        for layer in COPPER:
            assert any(e.startswith("<circle") for e in _elements(pages[layer])), layer

    def test_a_blind_via_reaches_only_its_stack_range(self, board: Board) -> None:
        """``via_layer_seq`` ports ``PCB_VIA::GetLayerSet``.

        A blind or buried via covers the range between its two drill layers. A
        THROUGH via covers every copper layer the board enables, which is the
        case the test above pins.
        """
        blind = Board.from_string(
            _BOARD.replace(
                '(via (at 3 3) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu")',
                '(via blind (at 3 3) (size 0.8) (drill 0.4) (layers "F.Cu" "In1.Cu")',
            )
        )
        via = next(v for v in blind.tracks if type(v).__name__ == "Via")
        assert via_layers(via, blind) == ("F.Cu", "In1.Cu")


class TestZones:
    def test_a_filled_zone_draws_its_FILL_not_its_outline(
        self, board: Board, view_box: ViewBox
    ) -> None:
        """The fill is the copper. The outline is only the region asked for."""
        pages = _pages(board, view_box, content={BoardContent.zones})
        elements = _elements(pages["F.Cu"])
        assert len(elements) == 1
        assert "M1.0000,11.0000" in elements[0]  # the filled polygon
        assert "M0.0000,10.0000" not in elements[0]  # the outline
        assert 'stroke="none"' in elements[0]

    def test_an_unfilled_zone_draws_its_outline_as_a_stroke(self, view_box: ViewBox) -> None:
        """A stroked outline cannot be mistaken for poured copper."""
        pages = _pages(Board.from_string(_UNFILLED), view_box, content={BoardContent.zones})
        elements = _elements(pages["F.Cu"])
        assert len(elements) == 1
        assert "M0.0000,10.0000" in elements[0]
        assert 'stroke="#c83434"' in elements[0]

    def test_a_fill_belonging_to_another_layer_is_not_drawn(
        self, board: Board, view_box: ViewBox
    ) -> None:
        """A multi-layer zone stores one filled polygon per layer.

        Handing back another layer's islands would put copper where there is
        none. The zone here names F.Cu only, so B.Cu must stay empty.
        """
        pages = _pages(board, view_box, content={BoardContent.zones})
        assert _elements(pages["B.Cu"]) == []


class TestTheTwoWalkersAgree:
    """``render_items_by_layer`` is the fast path and must match the slow one."""

    @pytest.mark.parametrize(
        "content",
        [
            {BoardContent.drawings},
            {BoardContent.tracks},
            {BoardContent.zones},
            {BoardContent.tracks, BoardContent.zones},
        ],
    )
    def test_both_walkers_select_the_same_items(
        self, board: Board, content: set[BoardContent]
    ) -> None:
        for layer in COPPER:
            slow = [id(item.item) for item in iter_render_items(board, layer, content)]
            grouped = render_items_by_layer(board, [layer], content)[layer]
            fast = [id(item.item) for item in grouped]
            assert slow == fast, layer
