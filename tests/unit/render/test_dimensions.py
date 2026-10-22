"""Dimension line work, pinned against KiCad's own plot.

A dimension is the one board item whose LINE work depends on its TEXT: KiCad cuts
a gap in the crossbar where the text sits (``pcb_dimension.cpp:994-1007``). So
this feature needed a font before it could be correct at all, and the numbers
below were measured from ``kicad-cli pcb export svg`` on a board holding one
dimension and nothing else.

The measured board: an aligned dimension from (20, 40) to (60, 40), height 10,
arrow length 1.27, extension offset 0.5, extension height 0.58642, text 1.5 mm at
0.3 mm thickness. KiCad plots segment lengths 1.27 x4 (the arrow heads), 10.086
x2 (the extension lines) and either 40.0 (crossbar clear of the text) or 11.736
x2 (crossbar knocked out).
"""

from __future__ import annotations

import math

import pytest

from eedesigncore import Board, CliParityStyle, ViewBox, render_board_svg
from eedesigncore.common.font import StrokeFont, TextBox, default_font
from eedesigncore.common.pcb_family.board_items import Dimension
from eedesigncore.render.dimensions import (
    arrow_segments,
    dimension_segments_mm,
    knocked_out,
)

_HEAD = """(kicad_pcb
\t(version 20240108)
\t(generator "pcbnew")
\t(general (thickness 1.6))
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(25 "Edge.Cuts" user)
\t\t(41 "Cmts.User" user)
\t)
\t(setup (pad_to_mask_clearance 0))
\t(net 0 "")
"""

ALIGNED = """\t(dimension
\t\t(type aligned)
\t\t(layer "Cmts.User")
\t\t(uuid "11111111-0000-4000-8000-000000000001")
\t\t(pts (xy 20 40) (xy 60 40))
\t\t(height 10)
\t\t(gr_text "%(text)s"
\t\t\t(at 40 50 0)
\t\t\t(layer "Cmts.User")
\t\t\t(uuid "11111111-0000-4000-8000-000000000002")
\t\t\t(effects (font (size 1.5 1.5) (thickness 0.3)))
\t\t)
\t\t(format (prefix "") (suffix "") (units 3) (units_format 1) (precision 4))
\t\t(style
\t\t\t(thickness 0.2)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 1)
\t\t\t(extension_height 0.58642)
\t\t\t(extension_offset 0.5)
\t\t)
\t)"""

ORTHOGONAL = """\t(dimension
\t\t(type orthogonal)
\t\t(layer "Cmts.User")
\t\t(uuid "11111111-0000-4000-8000-000000000004")
\t\t(pts (xy 20 40) (xy 60 55))
\t\t(height 12)
\t\t(orientation 0)
\t\t(gr_text ""
\t\t\t(at 40 60 0)
\t\t\t(layer "Cmts.User")
\t\t\t(uuid "11111111-0000-4000-8000-000000000005")
\t\t\t(effects (font (size 1.5 1.5) (thickness 0.3)))
\t\t)
\t\t(style
\t\t\t(thickness 0.2)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_height 0.58642)
\t\t\t(extension_offset 0.5)
\t\t)
\t)"""

BOARD_TEXT = """\t(gr_text "REF**"
\t\t(at 40 50 0)
\t\t(layer "Cmts.User")
\t\t(uuid "33333333-0000-4000-8000-000000000001")
\t\t(effects (font (size 1.5 1.5) (thickness 0.3)))
\t)"""

CENTER = """\t(dimension
\t\t(type center)
\t\t(layer "Cmts.User")
\t\t(uuid "11111111-0000-4000-8000-000000000003")
\t\t(pts (xy 30 30) (xy 35 35))
\t\t(style (thickness 0.2) (arrow_length 1.27) (text_position_mode 0))
\t)"""


def _board(block: str) -> Board:
    return Board.from_string(_HEAD + block + "\n)\n")


def _dimension(block: str) -> Dimension:
    dims = [d for d in _board(block).drawings if isinstance(d, Dimension)]
    assert dims, "the fixture holds no dimension"
    return dims[0]


def _lengths(block: str) -> list[float]:
    segments = dimension_segments_mm(_dimension(block))
    assert segments is not None
    return sorted(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segments)


def _close(values: list[float], expected: list[float]) -> bool:
    return len(values) == len(expected) and all(
        abs(a - b) <= 0.02 for a, b in zip(values, expected, strict=True)
    )


class TestAgainstKicadsOwnPlot:
    """Every expected list here was read out of a kicad-cli SVG export."""

    def test_an_aligned_dimension_knocks_the_text_out_of_its_crossbar(self) -> None:
        """The crossbar becomes TWO pieces, and this is the whole point of the font.

        40 mm of crossbar minus the text box leaves 11.736 either side. Getting
        that number needs the text measured with KiCad's own font, so a wrong
        font shows up here as a wrong gap.
        """
        lengths = _lengths(ALIGNED % {"text": "40.0000 mm"})
        assert _close(lengths, [1.27, 1.27, 1.27, 1.27, 10.086, 10.086, 11.736, 11.736])

    def test_a_crossbar_clear_of_the_text_survives_whole(self) -> None:
        """Empty text still has a box, and here it does not reach the crossbar."""
        lengths = _lengths(ALIGNED % {"text": ""})
        assert _close(lengths, [1.27, 1.27, 1.27, 1.27, 10.086, 10.086, 40.0])

    def test_an_orthogonal_dimension_has_two_UNEQUAL_extension_lines(self) -> None:
        """This is the character of the type, and it is easy to get wrong.

        The first extension runs the full height from the start point. The second
        runs only the residual gap between the end point and the crossbar, and it
        is anchored from the crossbar END (``:1219-1229``). Assuming symmetry
        gives 12.086 twice, and KiCad plots 12.086 and 3.086.
        """
        lengths = _lengths(ORTHOGONAL)
        assert _close(lengths, [1.27, 1.27, 1.27, 1.27, 3.086, 12.086, 40.0])

    def test_a_center_dimension_is_two_crossed_arms(self) -> None:
        """The one type that never touches the text box. Exact by construction."""
        lengths = _lengths(CENTER)
        assert _close(lengths, [14.142, 14.142])


class TestArrows:
    def test_an_arrow_is_two_strokes_of_the_arrow_length(self) -> None:
        segments = arrow_segments((0, 0), 0.0, 1_270_000, 0)
        assert len(segments) == 2
        for start, end in segments:
            assert math.hypot(end[0] - start[0], end[1] - start[1]) == pytest.approx(
                1_270_000, rel=1e-6
            )

    def test_an_inward_arrow_adds_a_tail(self) -> None:
        """``drawAnArrow`` draws the tail only when a length is given (``:358``)."""
        assert len(arrow_segments((0, 0), 0.0, 1_270_000, 2_540_000)) == 3


class TestKnockout:
    """``CollectKnockedOutSegments`` (``:114-148``) gives 0, 1 or 2 pieces."""

    SQUARE = ((-10, -10), (-10, 10), (10, 10), (10, -10))

    def test_a_segment_clear_of_the_box_survives_whole(self) -> None:
        segment = ((-100, 50), (100, 50))
        assert knocked_out(self.SQUARE, segment) == [segment]

    def test_a_segment_through_the_middle_becomes_two_pieces(self) -> None:
        pieces = knocked_out(self.SQUARE, ((-100, 0), (100, 0)))
        assert len(pieces) == 2
        assert pieces[0][0] == (-100, 0)
        assert pieces[1][1] == (100, 0)

    def test_a_segment_wholly_inside_the_box_disappears(self) -> None:
        assert knocked_out(self.SQUARE, ((-5, 0), (5, 0))) == []

    def test_a_segment_with_one_end_inside_becomes_one_piece(self) -> None:
        pieces = knocked_out(self.SQUARE, ((-100, 0), (0, 0)))
        assert len(pieces) == 1
        assert pieces[0][0] == (-100, 0)


class TestTheFont:
    def test_the_vendored_table_covers_printable_ascii(self) -> None:
        font = default_font()
        for char in (" ", "0", "9", "A", "z", "~", "."):
            assert font.glyph(char).advance > 0.0

    def test_a_character_outside_the_table_falls_back_to_a_question_mark(self) -> None:
        """``stroke_font.cpp:258-263`` does the same for a glyph it lacks."""
        font = default_font()
        assert font.glyph("\U0010ffff") == font.glyph("?")

    def test_the_space_advance_comes_from_the_first_glyph(self) -> None:
        """``stroke_font.cpp:216``. The space entry is ``JZ``, so 16 of 21 units.

        Reading the wrong entry here shifts every text box, because the space is
        also the glyph the table is indexed from.
        """
        assert default_font().space_advance == pytest.approx(16.0 / 21.0)

    def test_a_wider_string_gives_a_wider_box(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        narrow = font.text_box("1", size, 300_000)
        wide = font.text_box("1000", size, 300_000)
        assert wide.width > narrow.width
        assert wide.height == narrow.height


class TestTheCallerCanOverrideTheFont:
    """The operator's ruling: KiCad's font by default, a caller override allowed."""

    def test_a_wider_font_cuts_a_wider_gap(self) -> None:
        class Wide:
            """Reports every string as very wide, so it covers the whole crossbar."""

            def text_box(
                self,
                text: str,
                size: tuple[int, int],
                thickness: int,
                *,
                bold: bool = False,
                italic: bool = False,
            ) -> TextBox:
                return TextBox(x=0, y=0, width=100_000_000, height=size[1])

        dimension = _dimension(ALIGNED % {"text": "40.0000 mm"})
        default_segments = dimension_segments_mm(dimension)
        wide_segments = dimension_segments_mm(dimension, Wide())
        assert default_segments is not None
        assert wide_segments is not None
        # The crossbar is gone entirely, so two fewer segments reach the caller.
        assert len(wide_segments) == len(default_segments) - 2

    def test_the_default_is_the_stroke_font(self) -> None:
        dimension = _dimension(ALIGNED % {"text": "40.0000 mm"})
        assert dimension_segments_mm(dimension) == dimension_segments_mm(dimension, StrokeFont())


class TestText:
    """Drawn text, verified against ``kicad-cli pcb export svg``.

    The comparison used a pure TRANSLATION. kicad-cli's SVG does not flip y, and
    an earlier check that assumed a flip matched almost nothing and nearly sent
    this port chasing a defect that was in the check.

    Measured: 233 of 233 stroke segments matched to 0.003 mm across a dimension
    with text, plain text, rotated text and left-justified text.
    """

    def test_a_string_produces_one_run_per_pen_down_stroke(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        runs = font.text_strokes("12.7 mm", size, (40_000_000, 50_000_000), 300_000)
        # KiCad plots 11 paths and 48 segments for this string at this size.
        assert len(runs) == 11
        assert sum(len(run) - 1 for run in runs) == 48

    def test_the_drawn_text_is_the_size_the_effects_block_asks_for(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        runs = font.text_strokes("12.7 mm", size, (40_000_000, 50_000_000), 300_000)
        ys = [y for run in runs for _x, y in run]
        assert max(ys) - min(ys) == pytest.approx(1_500_000, abs=1000)

    def test_centred_text_straddles_its_anchor(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        runs = font.text_strokes("MMMM", size, (40_000_000, 50_000_000), 300_000)
        xs = [x for run in runs for x, _y in run]
        assert min(xs) < 40_000_000 < max(xs)

    def test_left_justified_text_starts_at_its_anchor(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        runs = font.text_strokes("MMMM", size, (40_000_000, 50_000_000), 300_000, halign="left")
        xs = [x for run in runs for x, _y in run]
        assert min(xs) >= 40_000_000

    def test_a_space_advances_without_drawing(self) -> None:
        font = default_font()
        size = (1_500_000, 1_500_000)
        with_space = font.text_strokes("A A", size, (0, 0), 300_000)
        without = font.text_strokes("AA", size, (0, 0), 300_000)
        assert len(with_space) == len(without)
        widest_with = max(x for run in with_space for x, _y in run)
        widest_without = max(x for run in without for x, _y in run)
        assert widest_with > widest_without


class TestThroughTheRenderer:
    def test_a_dimension_draws_its_line_work_AND_its_text(self) -> None:
        board = _board(ALIGNED % {"text": "40.0000 mm"})
        skipped: list = []
        pages = render_board_svg(
            board,
            ["Cmts.User"],
            ViewBox(min_x=0.0, min_y=0.0, width=100.0, height=100.0),
            style=CliParityStyle(colors={"Cmts.User": "#c83434"}),
            on_skip=skipped.append,
        )
        paths = [line for line in pages["Cmts.User"].splitlines() if "<path" in line]
        # 8 of line work, and one path per pen-down run of "40.0000 mm".
        assert len(paths) > 8
        assert skipped == []

    def test_board_text_draws(self) -> None:
        """``gr_text`` used to skip, so a silkscreen preview showed no labels."""
        board = Board.from_string(_HEAD + BOARD_TEXT + "\n)\n")
        skipped: list = []
        pages = render_board_svg(
            board,
            ["Cmts.User"],
            ViewBox(min_x=0.0, min_y=0.0, width=100.0, height=100.0),
            style=CliParityStyle(colors={"Cmts.User": "#c83434"}),
            on_skip=skipped.append,
        )
        assert pages["Cmts.User"].count("<path") == 12
        assert skipped == []
        # 0.3 mm is the effects thickness, not the 0.05 mm point-marker default.
        assert 'stroke-width="0.3000"' in pages["Cmts.User"]

    def test_a_dimension_draws_at_its_own_style_thickness(self) -> None:
        """A dimension has no ``(stroke …)``; its width is the style thickness.

        Falling through to the policy would draw it at the point-marker width of
        0.05 mm.
        """
        board = _board(ALIGNED % {"text": "40.0000 mm"})
        pages = render_board_svg(
            board,
            ["Cmts.User"],
            ViewBox(min_x=0.0, min_y=0.0, width=100.0, height=100.0),
            style=CliParityStyle(colors={"Cmts.User": "#c83434"}),
        )
        assert 'stroke-width="0.2000"' in pages["Cmts.User"]
