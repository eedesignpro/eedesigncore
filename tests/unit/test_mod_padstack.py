"""Unit tests for padstack (per-layer geometry, backdrill, post-machining)
round-trips in mod loader/writer (iter-6 coverage).
"""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint

_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
"""


def _make(extra: str) -> str:
    return _HEAD + extra + "\n)"


def _round_trip(sexpr: str) -> Footprint:
    fp = Footprint.from_string(sexpr)
    out = fp.to_string()
    return Footprint.from_string(out)


# ---------------------------------------------------------------------------
# Padstack (front_inner_back mode with two layers)
# ---------------------------------------------------------------------------

_PADSTACK_FIB = _make(
    """\
\t(pad "1" smd circle
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu" "F.Mask" "F.Paste")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(layer "F.Cu"
\t\t\t\t(shape roundrect)
\t\t\t\t(size 0.8 0.8)
\t\t\t\t(offset 0 0)
\t\t\t\t(roundrect_rratio 0.25)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(shape rect)
\t\t\t\t(size 0.9 0.9)
\t\t\t)
\t\t)
\t)
"""
)


class TestPadstackFrontInnerBack:
    def test_padstack_parsed(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FIB)
        pad = fp.pads[0]
        assert pad.padstack is not None

    def test_padstack_mode(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FIB)
        from eedesigncore.common.pcb_family.footprint_scoped import PadstackMode

        assert fp.pads[0].padstack is not None
        assert fp.pads[0].padstack.mode == PadstackMode.front_inner_back

    def test_padstack_layers(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FIB)
        ps = fp.pads[0].padstack
        assert ps is not None
        assert len(ps.layers) == 2

    def test_padstack_layer0_shape(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FIB)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.layer_name == "F.Cu"
        assert l0.shape == "roundrect"
        assert l0.size == pytest.approx((0.8, 0.8))
        assert l0.roundrect_rratio == pytest.approx(0.25)

    def test_padstack_layer1_shape(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FIB)
        ps = fp.pads[0].padstack
        assert ps is not None
        l1 = ps.layers[1]
        assert l1.layer_name == "B.Cu"
        assert l1.shape == "rect"
        assert l1.size == pytest.approx((0.9, 0.9))

    def test_padstack_round_trip(self) -> None:
        fp1 = Footprint.from_string(_PADSTACK_FIB)
        fp2 = _round_trip(_PADSTACK_FIB)
        ps1 = fp1.pads[0].padstack
        ps2 = fp2.pads[0].padstack
        assert ps1 is not None
        assert ps2 is not None
        assert ps1.mode == ps2.mode
        assert len(ps1.layers) == len(ps2.layers)
        assert ps1.layers[0].layer_name == ps2.layers[0].layer_name
        assert ps1.layers[0].shape == ps2.layers[0].shape


# ---------------------------------------------------------------------------
# Padstack layer with chamfer, rect_delta, thermal, clearance, zone_connect
# ---------------------------------------------------------------------------

_PADSTACK_FULL_LAYER = _make(
    """\
\t(pad "1" thru_hole rect
\t\t(at 0 0)
\t\t(size 2 2)
\t\t(drill 1.0)
\t\t(layers "*.Cu" "*.Mask")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(layer "F.Cu"
\t\t\t\t(shape roundrect)
\t\t\t\t(size 2 2)
\t\t\t\t(rect_delta 0.1 0)
\t\t\t\t(chamfer_ratio 0.25)
\t\t\t\t(chamfer top_left top_right)
\t\t\t\t(thermal_bridge_angle 45)
\t\t\t\t(thermal_gap 0.5)
\t\t\t\t(thermal_bridge_width 0.5)
\t\t\t\t(clearance 0.1)
\t\t\t\t(zone_connect 2)
\t\t\t\t(tenting
\t\t\t\t\t(front yes)
\t\t\t\t\t(back no)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
"""
)


class TestPadstackFullLayer:
    def test_rect_delta(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.rect_delta == pytest.approx((0.1, 0.0))

    def test_chamfer_ratio(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.chamfer_ratio == pytest.approx(0.25)

    def test_chamfer_positions(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.chamfer_positions is not None
        assert "top_left" in l0.chamfer_positions
        assert "top_right" in l0.chamfer_positions

    def test_thermal_fields(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.thermal_bridge_angle == pytest.approx(45.0)
        assert l0.thermal_gap == pytest.approx(0.5)
        assert l0.thermal_bridge_width == pytest.approx(0.5)

    def test_clearance_and_zone_connect(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.clearance == pytest.approx(0.1)
        assert l0.zone_connect == 2

    def test_tenting(self) -> None:
        fp = Footprint.from_string(_PADSTACK_FULL_LAYER)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.tenting_front is True
        assert l0.tenting_back is False

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_PADSTACK_FULL_LAYER)
        fp2 = _round_trip(_PADSTACK_FULL_LAYER)
        ps1 = fp1.pads[0].padstack
        ps2 = fp2.pads[0].padstack
        assert ps1 is not None
        assert ps2 is not None
        assert ps1.layers[0].rect_delta == ps2.layers[0].rect_delta
        assert ps1.layers[0].chamfer_positions == ps2.layers[0].chamfer_positions
        assert ps1.layers[0].zone_connect == ps2.layers[0].zone_connect
        assert ps1.layers[0].tenting_front == ps2.layers[0].tenting_front


# ---------------------------------------------------------------------------
# Padstack custom anchor + offset
# ---------------------------------------------------------------------------

_PADSTACK_CUSTOM_ANCHOR = _make(
    """\
\t(pad "1" smd custom
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(layer "F.Cu"
\t\t\t\t(shape custom)
\t\t\t\t(size 1 1)
\t\t\t\t(offset 0.1 0.2)
\t\t\t\t(options (anchor circle))
\t\t\t)
\t\t)
\t)
"""
)


class TestPadstackCustomAnchor:
    def test_custom_anchor(self) -> None:
        fp = Footprint.from_string(_PADSTACK_CUSTOM_ANCHOR)
        ps = fp.pads[0].padstack
        assert ps is not None
        l0 = ps.layers[0]
        assert l0.custom_anchor == "circle"
        assert l0.offset == pytest.approx((0.1, 0.2))

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_PADSTACK_CUSTOM_ANCHOR)
        fp2 = _round_trip(_PADSTACK_CUSTOM_ANCHOR)
        ps1 = fp1.pads[0].padstack
        ps2 = fp2.pads[0].padstack
        assert ps1 is not None
        assert ps2 is not None
        assert ps1.layers[0].custom_anchor == ps2.layers[0].custom_anchor


# ---------------------------------------------------------------------------
# Backdrill
# ---------------------------------------------------------------------------

_BACKDRILL_PAD = _make(
    """\
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size 2 2)
\t\t(drill 1.0)
\t\t(layers "*.Cu" "*.Mask")
\t\t(backdrill
\t\t\t(size 1.2)
\t\t\t(layers "B.Cu" "In1.Cu")
\t\t)
\t)
"""
)


class TestBackdrill:
    def test_backdrill_parsed(self) -> None:
        fp = Footprint.from_string(_BACKDRILL_PAD)
        pad = fp.pads[0]
        assert pad.backdrill is not None
        assert pad.backdrill.size == pytest.approx(1.2)
        assert pad.backdrill.start_layer == "B.Cu"
        assert pad.backdrill.end_layer == "In1.Cu"

    def test_backdrill_round_trip(self) -> None:
        fp1 = Footprint.from_string(_BACKDRILL_PAD)
        fp2 = _round_trip(_BACKDRILL_PAD)
        assert fp1.pads[0].backdrill is not None
        assert fp2.pads[0].backdrill is not None
        assert fp1.pads[0].backdrill.size == fp2.pads[0].backdrill.size
        assert fp1.pads[0].backdrill.start_layer == fp2.pads[0].backdrill.start_layer

    def test_backdrill_bogus_token_raises(self) -> None:
        bad = _make(
            """\
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size 2 2)
\t\t(drill 1.0)
\t\t(layers "*.Cu")
\t\t(backdrill
\t\t\t(size 1.2)
\t\t\t(layers "B.Cu" "In1.Cu")
\t\t\t(bogus_backdrill_token 1)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in backdrill"):
            Footprint.from_string(bad)


# ---------------------------------------------------------------------------
# Post-machining (counterbore and countersink)
# ---------------------------------------------------------------------------

_POST_MACHINING_PAD = _make(
    """\
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size 3 3)
\t\t(drill 1.5)
\t\t(layers "*.Cu" "*.Mask")
\t\t(front_post_machining counterbore
\t\t\t(size 2.5)
\t\t\t(depth 1.0)
\t\t)
\t\t(back_post_machining countersink
\t\t\t(size 2.8)
\t\t\t(angle 900)
\t\t)
\t)
"""
)


class TestPostMachining:
    def test_front_post_machining(self) -> None:
        fp = Footprint.from_string(_POST_MACHINING_PAD)
        pad = fp.pads[0]
        assert pad.front_post_machining is not None
        from eedesigncore.common.pcb_family.footprint_scoped import PostMachiningMode

        assert pad.front_post_machining.mode == PostMachiningMode.counterbore
        assert pad.front_post_machining.size == pytest.approx(2.5)
        assert pad.front_post_machining.depth == pytest.approx(1.0)

    def test_back_post_machining(self) -> None:
        fp = Footprint.from_string(_POST_MACHINING_PAD)
        pad = fp.pads[0]
        assert pad.back_post_machining is not None
        from eedesigncore.common.pcb_family.footprint_scoped import PostMachiningMode

        assert pad.back_post_machining.mode == PostMachiningMode.countersink
        assert pad.back_post_machining.size == pytest.approx(2.8)
        # angle is stored ÷10 on read: 900 → 90.0
        assert pad.back_post_machining.angle == pytest.approx(90.0)

    def test_post_machining_round_trips(self) -> None:
        """front/back_post_machining must round-trip (BUG-001 fixed).

        format(PAD*) emits these for any pad when set; the writer now does too.
        """
        from eedesigncore.common.pcb_family.footprint_scoped import PostMachiningMode

        fp2 = _round_trip(_POST_MACHINING_PAD)
        front = fp2.pads[0].front_post_machining
        back = fp2.pads[0].back_post_machining
        assert front is not None
        assert front.mode == PostMachiningMode.counterbore
        assert front.size == pytest.approx(2.5)
        assert front.depth == pytest.approx(1.0)
        assert back is not None
        assert back.mode == PostMachiningMode.countersink
        assert back.angle == pytest.approx(90.0)

    def test_post_machining_emitted_tokens(self) -> None:
        """The emitted text carries both blocks; angle written ×10 (900)."""
        out = Footprint.from_string(_POST_MACHINING_PAD).to_string()
        assert "(front_post_machining counterbore" in out
        assert "(back_post_machining countersink" in out
        assert "(angle 900)" in out

    def test_post_machining_bogus_token_raises(self) -> None:
        bad = _make(
            """\
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size 3 3)
\t\t(drill 1.5)
\t\t(layers "*.Cu")
\t\t(front_post_machining counterbore
\t\t\t(size 2.5)
\t\t\t(bogus_post_token 1)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in post_machining"):
            Footprint.from_string(bad)

    def test_bogus_padstack_token_raises(self) -> None:
        bad = _make(
            """\
\t(pad "1" smd rect
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(bogus_padstack_token 1)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in padstack"):
            Footprint.from_string(bad)

    def test_bogus_padstack_layer_token_raises(self) -> None:
        bad = _make(
            """\
\t(pad "1" smd rect
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(layer "F.Cu"
\t\t\t\t(shape rect)
\t\t\t\t(size 1 1)
\t\t\t\t(bogus_layer_token 1)
\t\t\t)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in padstack layer"):
            Footprint.from_string(bad)

    def test_bogus_tenting_token_raises(self) -> None:
        bad = _make(
            """\
\t(pad "1" smd rect
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu")
\t\t(padstack
\t\t\t(mode front_inner_back)
\t\t\t(layer "F.Cu"
\t\t\t\t(shape rect)
\t\t\t\t(size 1 1)
\t\t\t\t(tenting
\t\t\t\t\t(bogus_tenting_side yes)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
"""
        )
        # The message now mirrors KiCad's own Expecting( "front or back" )
        # (pcb_io_kicad_sexpr_parser.cpp:7715), because one shared reader
        # serves every tenting site.
        with pytest.raises(KicadParseError, match="Expected front or back"):
            Footprint.from_string(bad)
