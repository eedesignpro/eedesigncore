"""T6 — board drawings stream (gr_ prefix) + shared-model prefix parity.

Covers the board-scope drawings/points/zones/groups writers and the ``gr_text``
no-type loader. Each synthetic board is emitted to its canonical (prettified)
form, then that canonical form is asserted to round-trip byte-identically -- the
fidelity guarantee for KiCad-authored files (ADR-004). A dedicated parity test
asserts the SAME ``PcbLine`` model emits ``fp_line`` via the mod writer and
``gr_line`` via the board writer (ADR-020: prefix is a writer argument).
"""

from __future__ import annotations

from eedesigncore.common.models import Stroke
from eedesigncore.common.pcb_family import PcbLine, PcbText
from eedesigncore.mod.writer import _dump_fp_line, _dump_fp_text
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.writer import _dump_board_drawing

_HEADER = '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
_TAIL = " (embedded_fonts no))"


def _canonical(body: str) -> str:
    """Parse a flat board, return its prettified (canonical) emission."""
    return Board.from_string(_HEADER + body + _TAIL).to_string()


def _assert_byte_identical_round_trip(body: str, token: str) -> None:
    canonical = _canonical(body)
    assert token in canonical
    # The canonical form is what KiCad writes; it must round-trip to itself.
    assert Board.from_string(canonical).to_string() == canonical


class TestBoardDrawingsRoundTrip:
    def test_gr_line(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_line (start 0 0) (end 1 0) (stroke (width 0.1) (type solid))"
            ' (layer "F.SilkS") (uuid "l1"))',
            "(gr_line",
        )

    def test_gr_rect(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_rect (start 0 0) (end 2 2) (stroke (width 0.1) (type solid))"
            ' (fill no) (layer "F.SilkS") (uuid "r1"))',
            "(gr_rect",
        )

    def test_gr_circle(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_circle (center 0 0) (end 1 0) (stroke (width 0.1) (type solid))"
            ' (fill no) (layer "F.SilkS") (uuid "c1"))',
            "(gr_circle",
        )

    def test_gr_arc(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_arc (start 0 0) (mid 1 1) (end 2 0) (stroke (width 0.1) (type solid))"
            ' (layer "F.SilkS") (uuid "a1"))',
            "(gr_arc",
        )

    def test_gr_poly(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0.1) (type solid))"
            ' (fill yes) (layer "F.Cu") (uuid "p1"))',
            "(gr_poly",
        )

    def test_gr_poly_with_arc_in_pts(self) -> None:
        # formatPolyPts (:487-519): a (pts …) chain may interleave (arc …) with
        # (xy …). Arc round-trips byte-identically alongside plain points.
        _assert_byte_identical_round_trip(
            " (gr_poly (pts (xy 0 0) (arc (start 1 0) (mid 1.5 0.5) (end 1 1))"
            ' (xy 0 1)) (stroke (width 0.1) (type solid)) (fill yes) (layer "F.Cu")'
            ' (uuid "pa1"))',
            "(start 1 0)",
        )

    def test_gr_poly_pure_xy_unchanged(self) -> None:
        # Byte-neutrality: a pure-xy chain is unaffected by the arc-union change.
        canonical = _canonical(
            " (gr_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0.1) (type solid))"
            ' (fill yes) (layer "F.Cu") (uuid "px1"))'
        )
        assert "(pts\n\t\t\t(xy 0 0) (xy 1 0) (xy 1 1)\n\t\t)" in canonical

    def test_zone_filled_polygon_island_yes(self) -> None:
        # KiCad FormatBool emits (island yes), not a bare (island) (:3080-3081).
        _assert_byte_identical_round_trip(
            ' (zone (net 0) (net_name "") (layers "F.Cu") (name "z")'
            " (hatch edge 0.5) (connect_pads (clearance 0.5)) (min_thickness 0.25)"
            " (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5) (island_removal_mode 0))"
            " (polygon (pts (xy 0 0) (xy 5 0) (xy 5 5)))"
            ' (filled_polygon (layer "F.Cu") (island yes)'
            " (pts (xy 0 0) (xy 5 0) (xy 5 5))))",
            "(island yes)",
        )

    def test_gr_curve(self) -> None:
        _assert_byte_identical_round_trip(
            " (gr_curve (pts (xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0))"
            ' (stroke (width 0.1) (type solid)) (layer "F.SilkS") (uuid "cv1"))',
            "(gr_curve",
        )

    def test_gr_text_no_type_field(self) -> None:
        _assert_byte_identical_round_trip(
            ' (gr_text "HELLO" (at 1 2 0) (layer "F.SilkS") (uuid "t1")'
            " (effects (font (size 1 1) (thickness 0.15))))",
            '(gr_text "HELLO"',
        )

    def test_gr_text_box(self) -> None:
        _assert_byte_identical_round_trip(
            ' (gr_text_box "BOX" (start 0 0) (end 5 3) (margins 0 0 0 0) (layer "F.SilkS")'
            ' (uuid "tb1") (effects (font (size 1 1) (thickness 0.15))) (border yes)'
            " (stroke (width 0.1) (type solid)) (knockout no))",
            "(gr_text_box",
        )

    def test_dimension(self) -> None:
        _assert_byte_identical_round_trip(
            ' (dimension (type aligned) (layer "Dwgs.User") (uuid "d1")'
            " (pts (xy 0 0) (xy 10 0)) (height 2)"
            " (style (thickness 0.15) (arrow_length 1.27) (text_position_mode 0)"
            " (extension_height 0.58) (extension_offset 0))"
            ' (format (prefix "") (suffix "") (units 3) (units_format 1) (precision 4))'
            ' (gr_text "10.0 mm" (at 5 -1 0) (layer "Dwgs.User") (uuid "dt1")'
            " (effects (font (size 1 1) (thickness 0.15)))))",
            "(dimension",
        )

    def test_point(self) -> None:
        _assert_byte_identical_round_trip(
            ' (point (at 3 3) (size 0.5) (layer "F.Cu") (uuid "pt1"))',
            "(point",
        )

    def test_zone_net_by_name(self) -> None:
        _assert_byte_identical_round_trip(
            ' (zone (net "GND") (layer "F.Cu") (uuid "z1") (hatch edge 0.5)'
            " (connect_pads (clearance 0.5)) (min_thickness 0.25)"
            " (fill (thermal_gap 0.5) (thermal_bridge_width 0.5) (island_removal_mode 0))"
            " (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10))))",
            '(net "GND")',
        )

    def test_teardrop_area_zone_omits_thermal_tokens(self) -> None:
        # GAP-013: format(ZONE) :2992-2997 suppresses thermal_gap/
        # thermal_bridge_width for teardrop areas. island_removal_mode still
        # emits unconditionally (:3020).
        canonical = _canonical(
            ' (zone (net "GND") (layer "F.Cu") (uuid "td1") (hatch edge 0.5)'
            " (attr (teardrop (type padvia)))"
            " (connect_pads (clearance 0.5)) (min_thickness 0.25)"
            " (fill yes (island_removal_mode 1))"
            " (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10))))"
        )
        assert "(type padvia)" in canonical
        assert "thermal_gap" not in canonical
        assert "thermal_bridge_width" not in canonical
        assert "(island_removal_mode 1)" in canonical
        assert Board.from_string(canonical).to_string() == canonical

    def test_non_teardrop_zone_keeps_thermal_tokens(self) -> None:
        canonical = _canonical(
            ' (zone (net "GND") (layer "F.Cu") (uuid "z2") (hatch edge 0.5)'
            " (connect_pads (clearance 0.5)) (min_thickness 0.25)"
            " (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5)"
            " (island_removal_mode 0))"
            " (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10))))"
        )
        assert "(thermal_gap 0.5)" in canonical
        assert "(thermal_bridge_width 0.5)" in canonical

    def test_group(self) -> None:
        _assert_byte_identical_round_trip(
            ' (group "grp" (uuid "g1") (members "l1" "r1"))',
            "(group",
        )

    def test_drawings_emit_in_the_kicad_type_rank(self) -> None:
        """``BOARD::cmp_drawings`` keys on ``Type()`` first (``board.cpp:3428-3429``).

        ``PCB_SHAPE_T`` is 5 and ``PCB_TEXT_T`` is 9 (``typeinfo.h:88, 92``), so a
        text emits after every shape on the same layer whatever the stored order.
        """
        body = (
            ' (gr_text "A" (at 0 0 0) (layer "F.SilkS") (uuid "t1")'
            " (effects (font (size 1 1) (thickness 0.15))))"
            " (gr_line (start 0 0) (end 1 0) (stroke (width 0.1) (type solid))"
            ' (layer "F.SilkS") (uuid "l1"))'
        )
        canonical = _canonical(body)
        assert canonical.index("(gr_line") < canonical.index('(gr_text "A"')

    def test_trailing_drawing_after_embedded_fonts(self) -> None:
        # ADR-034: a drawing that appears AFTER (embedded_fonts …) in the source
        # (e.g. a fab-template gr_text_box) is preserved in that trailing position,
        # not hoisted into the normal drawings stream before groups.
        text = (
            _HEADER + " (gr_line (start 0 0) (end 1 0) (stroke (width 0.1) (type solid))"
            ' (layer "F.SilkS") (uuid "l1"))'
            ' (group "" (uuid "g1") (members "l1"))'
            " (embedded_fonts no)"
            " (gr_line (start 5 5) (end 6 6) (stroke (width 0.1) (type solid))"
            ' (layer "User.42") (uuid "trail1")))'
        )
        board = Board.from_string(text)
        assert len(board.drawings) == 1
        assert len(board.trailing_drawings) == 1
        assert board.trailing_drawings[0].id == "trail1"
        canonical = board.to_string()
        # The trailing line comes AFTER embedded_fonts; the group before it.
        assert canonical.index("(group") < canonical.index("(embedded_fonts")
        assert canonical.index("(embedded_fonts") < canonical.index('(uuid "trail1")')
        assert Board.from_string(canonical).to_string() == canonical


class TestPrefixParity:
    def test_same_line_model_emits_fp_and_gr(self) -> None:
        line = PcbLine(
            start=(0.0, 0.0),
            end=(1.0, 0.0),
            stroke=Stroke(width=0.1, type="solid"),
            layer="F.SilkS",
            id="l1",
        )
        fp = _dump_fp_line(line)
        gr = _dump_fp_line(line, "gr")
        assert fp.startswith("(fp_line")
        assert gr.startswith("(gr_line")
        # The two differ ONLY in the leading token prefix.
        assert fp[len("(fp_line") :] == gr[len("(gr_line") :]

    def test_board_dispatcher_uses_gr_prefix(self) -> None:
        line = PcbLine(start=(0.0, 0.0), end=(1.0, 0.0), id="l1")
        assert _dump_board_drawing(line).startswith("(gr_line")

    def test_gr_text_omits_type_field(self) -> None:
        # A board gr_text emits no leading type; fp_text emits the type token.
        text = PcbText(text_type="user", content="X", id="t1")
        fp = _dump_fp_text(text)
        gr = _dump_fp_text(text, "gr")
        assert fp.startswith("(fp_text user ")
        assert gr.startswith('(gr_text "X"')
