"""Unit tests for complex loader paths — increases coverage."""

from __future__ import annotations

from eedesigncore.mod import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbPoly,
    PcbText,
    PcbTextBox,
)
from eedesigncore.mod.enums import PadShape
from eedesigncore.mod.models import Footprint


def _parse(text: str) -> Footprint:
    return Footprint.from_string(text)


def _wrap(items: str) -> str:
    return (
        '(footprint "Test"\n'
        "  (version 20260206)\n"
        '  (generator "test")\n'
        '  (layer "F.Cu")\n'
        "  (duplicate_pad_numbers_are_jumpers no)\n"
        f"  {items}\n"
        "  (embedded_fonts no)\n"
        ")\n"
    )


class TestPcbArcLoader:
    def test_fp_arc_loads(self) -> None:
        text = _wrap(
            "(fp_arc\n"
            "    (start -1 0)\n"
            "    (mid 0 -1)\n"
            "    (end 1 0)\n"
            "    (stroke (width 0.1) (type solid))\n"
            '    (layer "F.SilkS")\n'
            ")"
        )
        fp = _parse(text)
        arcs = [d for d in fp.drawings if isinstance(d, PcbArc)]
        assert len(arcs) == 1
        assert arcs[0].start == (-1.0, 0.0)
        # The winding of this arc agrees with the KiCad convention, so
        # SetArcGeometry leaves it alone at load (ADR-067).
        assert arcs[0].mid == (0.0, -1.0)
        assert arcs[0].end == (1.0, 0.0)
        assert arcs[0].layer == "F.SilkS"

    def test_fp_arc_with_uuid(self) -> None:
        text = _wrap(
            "(fp_arc\n"
            "    (start 0 0) (mid 1 1) (end 2 0)\n"
            "    (stroke (width 0.1) (type solid))\n"
            '    (layer "F.Cu")\n'
            '    (uuid "test-arc-uuid")\n'
            ")"
        )
        fp = _parse(text)
        arcs = [d for d in fp.drawings if isinstance(d, PcbArc)]
        assert arcs[0].id == "test-arc-uuid"


class TestPcbCircleLoader:
    def test_fp_circle_loads(self) -> None:
        text = _wrap(
            "(fp_circle\n"
            "    (center 0 0)\n"
            "    (end 1 0)\n"
            "    (stroke (width 0.1) (type solid))\n"
            "    (fill no)\n"
            '    (layer "F.CrtYd")\n'
            ")"
        )
        fp = _parse(text)
        circles = [d for d in fp.drawings if isinstance(d, PcbCircle)]
        assert len(circles) == 1
        assert circles[0].center == (0.0, 0.0)
        assert circles[0].end == (1.0, 0.0)

    def test_fp_circle_fill_yes(self) -> None:
        from eedesigncore.common.models import FillType

        text = _wrap(
            "(fp_circle\n"
            "    (center 0 0) (end 1 0)\n"
            "    (stroke (width 0.1) (type solid))\n"
            "    (fill yes)\n"
            '    (layer "F.SilkS")\n'
            ")"
        )
        fp = _parse(text)
        circles = [d for d in fp.drawings if isinstance(d, PcbCircle)]
        assert circles[0].fill.type == FillType.outline


class TestPcbCurveLoader:
    def test_fp_curve_loads(self) -> None:
        text = _wrap(
            "(fp_curve\n"
            "    (pts (xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0))\n"
            "    (stroke (width 0.1) (type solid))\n"
            '    (layer "F.Cu")\n'
            ")"
        )
        fp = _parse(text)
        curves = [d for d in fp.drawings if isinstance(d, PcbCurve)]
        assert len(curves) == 1
        assert len(curves[0].pts) == 4


class TestPcbPolyLoader:
    def test_fp_poly_with_fill_yes(self) -> None:
        from eedesigncore.common.models import FillType

        text = _wrap(
            "(fp_poly\n"
            "    (pts (xy 0 0) (xy 1 0) (xy 0.5 1))\n"
            "    (stroke (width 0.1) (type solid))\n"
            "    (fill yes)\n"
            '    (layer "F.SilkS")\n'
            ")"
        )
        fp = _parse(text)
        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert len(polys) == 1
        assert polys[0].fill.type == FillType.outline

    def test_fp_poly_locked(self) -> None:
        text = _wrap(
            "(fp_poly\n"
            "    (pts (xy 0 0) (xy 1 0))\n"
            "    (stroke (width 0.1) (type solid))\n"
            "    (fill no)\n"
            "    (locked yes)\n"
            '    (layer "F.Cu")\n'
            ")"
        )
        fp = _parse(text)
        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert polys[0].locked is True


class TestPcbTextLoader:
    def test_fp_text_user(self) -> None:
        text = _wrap(
            '(fp_text user "${REFERENCE}"\n'
            "    (at 0 0 0)\n"
            '    (layer "F.Fab")\n'
            "    (effects (font (size 1 1) (thickness 0.15)))\n"
            ")"
        )
        fp = _parse(text)
        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert len(texts) == 1
        assert texts[0].text_type == "user"
        assert texts[0].content == "${REFERENCE}"
        assert texts[0].angle == 0.0

    def test_fp_text_knockout(self) -> None:
        text = _wrap(
            '(fp_text user "test"\n'
            "    (at 0 0 45)\n"
            '    (layer "F.SilkS" knockout)\n'
            "    (effects (font (size 1 1)))\n"
            ")"
        )
        fp = _parse(text)
        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert texts[0].knockout is True
        assert texts[0].angle == 45.0

    def test_fp_text_unlocked(self) -> None:
        text = _wrap(
            '(fp_text user "test"\n    (at 0 0 0)\n    (unlocked yes)\n    (layer "F.Fab")\n)'
        )
        fp = _parse(text)
        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert texts[0].unlocked is True

    def test_fp_text_hidden(self) -> None:
        text = _wrap(
            '(fp_text user "test"\n    (at 0 0 0)\n    (layer "F.Fab")\n    (hide yes)\n)'
        )
        fp = _parse(text)
        texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert texts[0].visible is False


class TestPcbTextBoxLoader:
    def test_fp_text_box_rectangle(self) -> None:
        text = _wrap(
            '(fp_text_box "hello"\n'
            "    (start 0 0)\n"
            "    (end 10 5)\n"
            "    (margins 0.1 0.1 0.1 0.1)\n"
            '    (layer "F.Fab")\n'
            "    (border no)\n"
            "    (stroke (width 0.1) (type solid))\n"
            "    (knockout no)\n"
            ")"
        )
        fp = _parse(text)
        boxes = [d for d in fp.drawings if isinstance(d, PcbTextBox)]
        assert len(boxes) == 1
        assert boxes[0].content == "hello"
        assert boxes[0].start == (0.0, 0.0)
        assert boxes[0].end == (10.0, 5.0)
        assert boxes[0].border_enabled is False

    def test_fp_text_box_with_angle(self) -> None:
        text = _wrap(
            '(fp_text_box "rotated"\n'
            "    (start 0 0) (end 5 3)\n"
            "    (margins 0 0 0 0)\n"
            "    (angle 45)\n"
            '    (layer "F.Fab")\n'
            "    (border yes)\n"
            "    (stroke (width 0.05) (type solid))\n"
            "    (knockout no)\n"
            ")"
        )
        fp = _parse(text)
        boxes = [d for d in fp.drawings if isinstance(d, PcbTextBox)]
        assert boxes[0].angle == 45.0


class TestPadLoader:
    def test_thru_hole_pad_with_keep_end_layers(self) -> None:
        text = _wrap(
            '(pad "1" thru_hole circle\n'
            "    (at 0 0)\n"
            "    (size 1.7 1.7)\n"
            "    (drill 1)\n"
            '    (layers "*.Cu" "*.Mask")\n'
            "    (remove_unused_layers yes)\n"
            "    (keep_end_layers yes)\n"
            ")"
        )
        fp = _parse(text)
        assert len(fp.pads) == 1
        assert fp.pads[0].remove_unused_layers is True
        assert fp.pads[0].keep_end_layers is True

    def test_pad_with_rect_delta(self) -> None:
        text = _wrap(
            '(pad "1" smd trapezoid\n'
            "    (at 0 0)\n"
            "    (size 1 1)\n"
            "    (rect_delta 0.2 0)\n"
            '    (layers "F.Cu")\n'
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].rect_delta == (0.2, 0.0)

    def test_pad_with_chamfer(self) -> None:
        text = _wrap(
            '(pad "1" smd roundrect\n'
            "    (at 0 0)\n"
            "    (size 1 1)\n"
            '    (layers "F.Cu")\n'
            "    (roundrect_rratio 0.25)\n"
            "    (chamfer_ratio 0.3)\n"
            "    (chamfer top_left top_right)\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].chamfer_ratio == 0.3
        assert fp.pads[0].chamfer_positions is not None
        assert "top_left" in fp.pads[0].chamfer_positions

    def test_pad_with_teardrop(self) -> None:
        text = _wrap(
            '(pad "1" smd rect\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu")\n'
            "    (teardrops\n"
            "        (best_length_ratio 0.5) (max_length 1)\n"
            "        (best_width_ratio 0.5) (max_width 1)\n"
            "        (curved_edges no) (filter_ratio 0.75)\n"
            "        (enabled yes) (allow_two_segments yes)\n"
            "        (prefer_zone_connections no)\n"
            "    )\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].teardrop_params is not None
        assert fp.pads[0].teardrop_params.enabled is True

    def test_pad_with_thermal(self) -> None:
        text = _wrap(
            '(pad "1" smd rect\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu")\n'
            "    (thermal_bridge_width 0.3)\n"
            "    (thermal_bridge_angle 45)\n"
            "    (thermal_gap 0.2)\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].thermal_spoke_width == 0.3
        assert fp.pads[0].thermal_spoke_angle == 45.0
        assert fp.pads[0].thermal_gap == 0.2

    def test_oval_drill_with_offset(self) -> None:
        text = _wrap(
            '(pad "1" thru_hole oval\n'
            "    (at 0 0)\n"
            "    (size 1.5 1)\n"
            "    (drill oval 0.6 1 (offset 0.2 0))\n"
            '    (layers "*.Cu")\n'
            "    (remove_unused_layers no)\n"
            ")"
        )
        fp = _parse(text)
        d = fp.pads[0].drill
        assert d is not None
        assert d.oval is True
        assert d.width == 0.6
        assert d.height == 1.0
        assert d.offset == (0.2, 0.0)

    def test_pad_tenting(self) -> None:
        text = _wrap(
            '(pad "1" smd rect\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu")\n'
            "    (tenting (front yes) (back no))\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].tenting_front is True
        assert fp.pads[0].tenting_back is False

    def test_pad_with_die_length(self) -> None:
        text = _wrap(
            '(pad "1" smd rect\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu")\n'
            "    (die_length 0.5)\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].die_length == 0.5

    def test_pad_custom_shape(self) -> None:
        text = _wrap(
            '(pad "1" smd custom\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu")\n'
            "    (options (clearance outline) (anchor circle))\n"
            "    (primitives\n"
            "        (gr_line (start 0 0) (end 1 0) (width 0.1))\n"
            "        (gr_circle (center 0 0) (end 0.5 0) (width 0.1) (fill no))\n"
            "        (gr_arc (start 0 0) (mid 0.5 0.5) (end 1 0) (width 0.1))\n"
            "        (gr_curve (pts (xy 0 0) (xy 0.3 0.3) (xy 0.7 0.3) (xy 1 0)) (width 0.1))\n"
            "        (gr_rect (start 0 0) (end 1 1) (width 0.1) (fill no))\n"
            "        (gr_poly (pts (xy 0 0) (xy 1 0) (xy 0.5 1)) (width 0.1) (fill yes))\n"
            "    )\n"
            ")"
        )
        fp = _parse(text)
        assert fp.pads[0].shape == PadShape.custom
        assert fp.pads[0].custom_shape_clearance == "outline"
        assert fp.pads[0].custom_anchor == "circle"
        assert fp.pads[0].primitives is not None
        assert len(fp.pads[0].primitives) == 6


class TestFootprintLoaderDetails:
    def test_footprint_with_private_layers(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            '  (private_layers "User.1" "User.2")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = _parse(text)
        assert fp.private_layers == ["User.1", "User.2"]

    def test_footprint_with_net_tie_pad_groups(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            '  (net_tie_pad_groups "1" "2")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = _parse(text)
        assert fp.net_tie_pad_groups == ["1", "2"]

    def test_footprint_with_group(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            '  (group "mygroup"\n'
            '      (uuid "grp-uuid")\n'
            '      (members "m1" "m2")\n'
            "  )\n"
            ")\n"
        )
        fp = _parse(text)
        assert len(fp.groups) == 1
        assert fp.groups[0].name == "mygroup"
        assert fp.groups[0].id == "grp-uuid"
        assert fp.groups[0].members == ["m1", "m2"]

    def test_footprint_with_solder_margins(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (solder_mask_margin 0.1)\n"
            "  (solder_paste_margin -0.05)\n"
            "  (solder_paste_margin_ratio 0.25)\n"
            "  (clearance 0.2)\n"
            "  (zone_connect 1)\n"
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = _parse(text)
        assert fp.solder_mask_margin == 0.1
        assert fp.solder_paste_margin == -0.05
        assert fp.clearance == 0.2
        assert fp.zone_connect == 1

    def test_footprint_with_model(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            '  (model "test.step"\n'
            "      (offset (xyz 0 0 0))\n"
            "      (scale (xyz 1 1 1))\n"
            "      (rotate (xyz 0 0 0))\n"
            "  )\n"
            ")\n"
        )
        fp = _parse(text)
        assert len(fp.models) == 1
        assert fp.models[0].path == "test.step"

    def test_footprint_with_hidden_model(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            '  (model "test.step"\n'
            "      (hide yes)\n"
            "      (offset (xyz 0 0 0))\n"
            "      (scale (xyz 1 1 1))\n"
            "      (rotate (xyz 0 0 0))\n"
            "  )\n"
            ")\n"
        )
        fp = _parse(text)
        assert fp.models[0].visible is False

    def test_fp_filters(self) -> None:
        text = (
            '(footprint "Test"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            '  (property ki_fp_filters "R_*")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        # fp_filters is stored as a property — test it parses without error
        fp = _parse(text)
        assert fp is not None
