"""Constructs KiCad accepts that this library used to reject or misread.

Every case here came from loading KiCad's own ``qa/data`` tree, which is built
to hold edge cases. Before these fixes 29 of the 119 modern-format boards there
failed to load; after them, none do.

Each test names the C++ site that decides the behaviour. A test that only pins
our current output would not catch a divergence, so the expected value is the
one measured from KiCad 10.0.x, either by reading the parser or by running
``kicad-cli`` on a minimal file.
"""

from __future__ import annotations

import pytest

from eedesigncore import Board, Footprint, Schematic
from eedesigncore import parse as sexpr_parse
from eedesigncore.common.loaders import get_float, get_int, get_str
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.enums import PadType

# ---------------------------------------------------------------------------
# Numeric atoms: dsnlexer.cpp:491-493, and parseDouble at :864-878
# ---------------------------------------------------------------------------


def _atom(text: str):  # noqa: ANN202 - an SExprNode, kept local to these tests
    return sexpr_parse(f"(a {text})").value[1]


class TestNumericAtoms:
    """KiCad's ``isNumber`` allows an exponent with no dot, and a leading plus."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("-1e-06", -1e-06),  # the atom that made variants.kicad_pcb fail
            ("1e6", 1e6),
            ("1E6", 1e6),
            ("+5", 5.0),
            ("+1.5", 1.5),
            ("-0.5", -0.5),  # already worked, guarded against regression
        ],
    )
    def test_a_kicad_number_reaches_get_float(self, text: str, expected: float) -> None:
        assert get_float(_atom(text)) == expected

    def test_a_kicad_integer_reaches_get_int(self) -> None:
        assert get_int(_atom("+5")) == 5

    def test_a_non_number_still_raises(self) -> None:
        with pytest.raises(KicadParseError, match="Expected number"):
            get_float(_atom("solid"))

    def test_a_legacy_tstamp_stays_a_string(self) -> None:
        """The reason the lexer is NOT widened to match KiCad's regex.

        ``586432E5`` is a pre-6.0 timestamp identifier, and KiCad's own
        ``isNumber`` calls it a number. Four corpus files carry one. Classifying
        it as a float would write back ``5.86432e+12`` and destroy it, so the
        node keeps its type and only the numeric ACCESSORS widen.
        """
        assert get_str(_atom("586432E5")) == "586432E5"

    def test_a_shape_coordinate_in_exponent_form_round_trips(self) -> None:
        board = Board.from_string(
            '(kicad_pcb (version 20240108) (generator "pcbnew")\n'
            '  (general (thickness 1.6)) (paper "A4")\n'
            '  (layers (0 "F.Cu" signal) (44 "Edge.Cuts" user))\n'
            "  (setup (pad_to_mask_clearance 0))\n"
            '  (gr_line (start -1e-06 0) (end 1 1) (layer "Edge.Cuts")\n'
            '    (stroke (width 0) (type solid)) (uuid "a")))'
        )
        assert board.drawings[0].start == (-1e-06, 0.0)


# ---------------------------------------------------------------------------
# Protection values: parseOptBool :245-259, parseFrontBackOptBool :7658-7723
# ---------------------------------------------------------------------------

_PAD = (
    '(footprint "T" (layer "F.Cu") (uuid "a")'
    ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") %s (uuid "b")))'
)


class TestTentingValues:
    @pytest.mark.parametrize(
        ("block", "front", "back"),
        [
            # The modern nested spelling, with the third token KiCad allows.
            ("(tenting (front yes) (back no))", True, False),
            ("(tenting (front none) (back none))", None, None),
            ("(tenting (front no) (back none))", False, None),
            # The legacy bare-word spelling. These used to load as None/None
            # with no error, so a tented pad silently became untented.
            ("(tenting front back)", True, True),
            ("(tenting front)", True, None),
            ("(tenting back)", None, True),
            ("(tenting none)", None, None),
        ],
    )
    def test_a_pad_reads_every_spelling(
        self, block: str, front: bool | None, back: bool | None
    ) -> None:
        pad = Footprint.from_string(_PAD % block).pads[0]
        assert pad.tenting_front is front
        assert pad.tenting_back is back

    def test_a_bogus_side_still_raises(self) -> None:
        with pytest.raises(KicadParseError, match="Expected front or back"):
            Footprint.from_string(_PAD % "(tenting (sideways yes))")

    def test_a_bogus_bare_word_still_raises(self) -> None:
        with pytest.raises(KicadParseError, match="Expected front, back or none"):
            Footprint.from_string(_PAD % "(tenting sideways)")


def _board(setup_body: str) -> Board:
    return Board.from_string(
        '(kicad_pcb (version 20240108) (generator "pcbnew")\n'
        '  (general (thickness 1.6)) (paper "A4")\n'
        '  (layers (0 "F.Cu" signal) (44 "Edge.Cuts" user))\n'
        f"  (setup {setup_body}))"
    )


class TestBoardSetupProtection:
    def test_an_absent_tenting_token_means_TENTED(self) -> None:
        """``board_design_settings.cpp:231-232`` defaults both sides to true.

        139 boards in KiCad's qa tree carry no ``(tenting …)``. Reading them as
        untented and writing that back exposes every via on the board, and no
        error says so. Measured against ``kicad-cli pcb upgrade``, which writes
        ``(front yes) (back yes)`` for such a board.
        """
        setup = _board("(pad_to_mask_clearance 0)").setup
        assert setup.tenting.front is True
        assert setup.tenting.back is True

    def test_the_other_four_default_to_off(self) -> None:
        """``:234-242``. Only tenting differs from a bare FrontBack()."""
        setup = _board("(pad_to_mask_clearance 0)").setup
        assert (setup.covering.front, setup.covering.back) == (False, False)
        assert (setup.plugging.front, setup.plugging.back) == (False, False)
        assert setup.capping is False
        assert setup.filling is False

    def test_a_present_token_with_one_side_collapses_the_other_to_false(self) -> None:
        """Board setup uses ``value_or( false )`` at ``:2744-2745``."""
        setup = _board("(pad_to_mask_clearance 0) (tenting (front yes))").setup
        assert (setup.tenting.front, setup.tenting.back) == (True, False)

    def test_the_legacy_bare_word_form_sets_both_sides(self) -> None:
        setup = _board("(pad_to_mask_clearance 0) (tenting front back)").setup
        assert (setup.tenting.front, setup.tenting.back) == (True, True)

    def test_covering_rejects_the_bare_word_form(self) -> None:
        """Only tenting passes ``aAllowLegacyFormat``; ``:2750`` does not."""
        with pytest.raises(KicadParseError, match="got a bare word"):
            _board("(pad_to_mask_clearance 0) (covering front back)")


# ---------------------------------------------------------------------------
# A shape's net reference: parseNet :296-330
# ---------------------------------------------------------------------------

_SHAPE_BOARD = (
    '(kicad_pcb (version 20240108) (generator "pcbnew")\n'
    '  (general (thickness 1.6)) (paper "A4")\n'
    '  (layers (0 "F.Cu" signal) (44 "Edge.Cuts" user))\n'
    "  (setup (pad_to_mask_clearance 0))\n"
    '  (net 0 "") (net 1 "Net1")\n'
    '  (gr_line (start 0 0) (end 1 1) (layer "F.Cu")\n'
    '    (stroke (width 0.2) (type solid)) %s (uuid "a")))'
)


class TestShapeNetReference:
    @pytest.mark.parametrize(
        ("block", "expected"),
        [
            ('(net "Net1")', "Net1"),  # the v10 spelling
            ('(net 1 "Net1")', "Net1"),  # legacy code plus name
            ("(net 1)", None),  # legacy code alone, no name available
        ],
    )
    def test_a_shape_accepts_all_three_spellings(self, block: str, expected: str | None) -> None:
        shape = Board.from_string(_SHAPE_BOARD % block).drawings[0]
        assert shape.net_name == expected

    def test_a_quoted_numeral_stays_a_NAME(self) -> None:
        """A net may legitimately be called "1". The quoting is the discriminator."""
        shape = Board.from_string(_SHAPE_BOARD % '(net "1")').drawings[0]
        assert shape.net_name == "1"


# ---------------------------------------------------------------------------
# Legacy tokens that KiCad reads and never writes
# ---------------------------------------------------------------------------


class TestLegacyTeardropCurvePoints:
    """``parser.cpp:711-715``: a segment COUNT stands in for the curved flag."""

    @pytest.mark.parametrize(("count", "curved"), [(5, True), (1, True), (0, False)])
    def test_curve_points_maps_onto_curved_edges(self, count: int, curved: bool) -> None:
        fp = Footprint.from_string(
            '(footprint "T" (layer "F.Cu") (uuid "a")'
            ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")'
            f" (teardrops (curve_points {count}))"
            ' (uuid "b")))'
        )
        assert fp.pads[0].teardrop_params is not None
        assert fp.pads[0].teardrop_params.curved_edges is curved

    def test_the_token_is_not_written_back(self) -> None:
        """KiCad's writer emits ``curved_edges``, never ``curve_points``."""
        fp = Footprint.from_string(
            '(footprint "T" (layer "F.Cu") (uuid "a")'
            ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")'
            ' (teardrops (curve_points 5)) (uuid "b")))'
        )
        assert "curve_points" not in fp.to_string()


_SCH = (
    '(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")\n'
    '  (uuid "0e6f0e0b-0000-4000-8000-000000000000")\n'
    '  (paper "A4")\n'
    "  %s\n"
    "  (embedded_fonts no))"
)


class TestSchematicReadOnlyRootTokens:
    """Both are read into KiCad and then dropped, so we drop them too."""

    @pytest.mark.parametrize(
        "block",
        [
            '(bus_alias "MEM" (members "A0" "A1"))',
            '(symbol_instances (path "/00000000-0000-0000-0000-000000000000"'
            ' (reference "R1") (unit 1)))',
        ],
    )
    def test_the_token_loads_and_is_not_written_back(self, block: str) -> None:
        sch = Schematic.from_string(_SCH % block)
        out = sch.to_string()
        token = block[1:].split(" ")[0]
        assert token not in out

    def test_an_unknown_root_token_still_raises(self) -> None:
        with pytest.raises(KicadParseError, match="Unknown token in kicad_sch"):
            Schematic.from_string(_SCH % "(not_a_real_block 1)")


# ---------------------------------------------------------------------------
# A hole pad with no drill token: parser.cpp:5764-5791, pad.cpp:86
# ---------------------------------------------------------------------------


class TestDrilllessHolePad:
    @pytest.mark.parametrize(
        ("kind", "width"),
        [
            # KiCad emulates the missing token with a 1 nm drill, because a
            # newer version cannot hold a drill of zero.
            (PadType.thru_hole, 1e-06),
            # No assignment for NPTH, so the PAD constructor's 30 mils stands.
            (PadType.np_thru_hole, 0.762),
        ],
    )
    def test_the_drill_takes_kicads_default(self, kind: PadType, width: float) -> None:
        fp = Footprint.from_string(
            '(footprint "T" (layer "F.Cu") (uuid "a")'
            f' (pad "1" {kind.value} circle (at 0 0) (size 1 1)'
            ' (layers "*.Cu" "*.Mask") (uuid "b")))'
        )
        pad = fp.pads[0]
        assert pad.drill is not None
        assert pad.drill.width == pytest.approx(width)

    @pytest.mark.parametrize(
        ("kind", "text"),
        [(PadType.thru_hole, "(drill 0.000001)"), (PadType.np_thru_hole, "(drill 0.762)")],
    )
    def test_the_default_is_written_the_way_kicad_writes_it(
        self, kind: PadType, text: str
    ) -> None:
        """Measured with ``kicad-cli fp upgrade`` on this exact input."""
        fp = Footprint.from_string(
            '(footprint "T" (layer "F.Cu") (uuid "a")'
            f' (pad "1" {kind.value} circle (at 0 0) (size 1 1)'
            ' (layers "*.Cu" "*.Mask") (uuid "b")))'
        )
        assert text in fp.to_string()

    def test_a_surface_pad_keeps_no_drill(self) -> None:
        fp = Footprint.from_string(
            '(footprint "T" (layer "F.Cu") (uuid "a")'
            ' (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (uuid "b")))'
        )
        assert fp.pads[0].drill is None
