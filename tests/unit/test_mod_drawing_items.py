"""Unit tests for mod drawing items (fp_line, fp_rect, fp_circle, fp_arc,
fp_poly, fp_curve, fp_text_box) and pad primitives — loader + writer coverage
(iter-6).
"""

from __future__ import annotations

import pytest

from eedesigncore.common.models import FillType, StrokeType
from eedesigncore.mod import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbLine,
    PcbPoly,
    PcbRect,
    PcbTextBox,
)
from eedesigncore.mod.enums import PadShape, PadType
from eedesigncore.mod.models import (
    Footprint,
    Pad,
    PrimArc,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimPoly,
    PrimRect,
)

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
# fp_line round-trip
# ---------------------------------------------------------------------------

_FP_LINE = _make(
    """\
\t(fp_line
\t\t(start 0.5 0.5)
\t\t(end 2.5 0.5)
\t\t(stroke (width 0.12) (type solid))
\t\t(locked yes)
\t\t(layer "F.SilkS")
\t\t(solder_mask_margin 0.05)
\t\t(net "GND")
\t\t(uuid "line-uuid-1")
\t)
"""
)


class TestPcbLine:
    def test_fp_line_parses(self) -> None:
        fp = Footprint.from_string(_FP_LINE)
        lines = [d for d in fp.drawings if isinstance(d, PcbLine)]
        assert len(lines) == 1
        ln = lines[0]
        assert ln.start == pytest.approx((0.5, 0.5))
        assert ln.end == pytest.approx((2.5, 0.5))
        assert ln.stroke.width == pytest.approx(0.12)
        assert ln.stroke.type == StrokeType.solid
        assert ln.locked is True
        assert ln.layer == "F.SilkS"
        assert ln.solder_mask_margin == pytest.approx(0.05)
        assert ln.net_name == "GND"
        assert ln.id == "line-uuid-1"

    def test_fp_line_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_LINE)
        fp2 = _round_trip(_FP_LINE)
        l1 = [d for d in fp1.drawings if isinstance(d, PcbLine)][0]
        l2 = [d for d in fp2.drawings if isinstance(d, PcbLine)][0]
        assert l1.start == l2.start
        assert l1.end == l2.end
        assert l1.locked == l2.locked
        assert l1.layer == l2.layer

    def test_fp_line_in_output(self) -> None:
        fp = Footprint.from_string(_FP_LINE)
        out = fp.to_string()
        assert "(fp_line" in out
        assert "(locked yes)" in out


# ---------------------------------------------------------------------------
# fp_rect round-trip
# ---------------------------------------------------------------------------

_FP_RECT = _make(
    """\
\t(fp_rect
\t\t(start 0 0)
\t\t(end 5 3)
\t\t(radius 0.5)
\t\t(stroke (width 0.05) (type default))
\t\t(fill yes)
\t\t(locked yes)
\t\t(layer "F.Cu")
\t\t(solder_mask_margin 0.1)
\t\t(uuid "rect-uuid-1")
\t)
"""
)


class TestPcbRect:
    def test_fp_rect_parses(self) -> None:
        fp = Footprint.from_string(_FP_RECT)
        rects = [d for d in fp.drawings if isinstance(d, PcbRect)]
        assert len(rects) == 1
        r = rects[0]
        assert r.start == pytest.approx((0.0, 0.0))
        assert r.end == pytest.approx((5.0, 3.0))
        assert r.corner_radius == pytest.approx(0.5)
        assert r.fill.type == FillType.outline
        assert r.locked is True
        assert r.solder_mask_margin == pytest.approx(0.1)

    def test_fp_rect_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_RECT)
        fp2 = _round_trip(_FP_RECT)
        r1 = [d for d in fp1.drawings if isinstance(d, PcbRect)][0]
        r2 = [d for d in fp2.drawings if isinstance(d, PcbRect)][0]
        assert r1.corner_radius == pytest.approx(r2.corner_radius)  # type: ignore[arg-type]
        assert r1.fill.type == r2.fill.type


# ---------------------------------------------------------------------------
# fp_circle round-trip
# ---------------------------------------------------------------------------

_FP_CIRCLE = _make(
    """\
\t(fp_circle
\t\t(center 1 1)
\t\t(end 3 1)
\t\t(stroke (width 0.1) (type default))
\t\t(fill no)
\t\t(layer "F.Cu")
\t\t(net "VCC")
\t\t(uuid "circle-uuid-1")
\t)
"""
)


class TestPcbCircle:
    def test_fp_circle_parses(self) -> None:
        fp = Footprint.from_string(_FP_CIRCLE)
        circles = [d for d in fp.drawings if isinstance(d, PcbCircle)]
        assert len(circles) == 1
        c = circles[0]
        assert c.center == pytest.approx((1.0, 1.0))
        assert c.end == pytest.approx((3.0, 1.0))
        assert c.net_name == "VCC"
        assert c.id == "circle-uuid-1"

    def test_fp_circle_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_CIRCLE)
        fp2 = _round_trip(_FP_CIRCLE)
        c1 = [d for d in fp1.drawings if isinstance(d, PcbCircle)][0]
        c2 = [d for d in fp2.drawings if isinstance(d, PcbCircle)][0]
        assert c1.center == c2.center
        assert c1.net_name == c2.net_name


# ---------------------------------------------------------------------------
# fp_arc round-trip
# ---------------------------------------------------------------------------

_FP_ARC = _make(
    """\
\t(fp_arc
\t\t(start 0 0)
\t\t(mid 1.27 -1.27)
\t\t(end 2.54 0)
\t\t(stroke (width 0.1) (type default))
\t\t(locked yes)
\t\t(layer "B.Cu")
\t\t(solder_mask_margin 0.02)
\t\t(uuid "arc-uuid-1")
\t)
"""
)


class TestPcbArc:
    def test_fp_arc_parses(self) -> None:
        fp = Footprint.from_string(_FP_ARC)
        arcs = [d for d in fp.drawings if isinstance(d, PcbArc)]
        assert len(arcs) == 1
        a = arcs[0]
        assert a.start == pytest.approx((0.0, 0.0))
        assert a.mid == pytest.approx((1.27, -1.27))
        assert a.end == pytest.approx((2.54, 0.0))
        assert a.locked is True
        assert a.layer == "B.Cu"
        assert a.solder_mask_margin == pytest.approx(0.02)

    def test_fp_arc_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_ARC)
        fp2 = _round_trip(_FP_ARC)
        a1 = [d for d in fp1.drawings if isinstance(d, PcbArc)][0]
        a2 = [d for d in fp2.drawings if isinstance(d, PcbArc)][0]
        assert a1.start == a2.start
        assert a1.locked == a2.locked


# ---------------------------------------------------------------------------
# fp_poly round-trip
# ---------------------------------------------------------------------------

_FP_POLY = _make(
    """\
\t(fp_poly
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 2 0)
\t\t\t(xy 1 2)
\t\t)
\t\t(stroke (width 0.05) (type default))
\t\t(fill yes)
\t\t(locked yes)
\t\t(layer "F.Cu")
\t\t(solder_mask_margin 0.1)
\t\t(uuid "poly-uuid-1")
\t)
"""
)


class TestPcbPoly:
    def test_fp_poly_parses(self) -> None:
        fp = Footprint.from_string(_FP_POLY)
        polys = [d for d in fp.drawings if isinstance(d, PcbPoly)]
        assert len(polys) == 1
        p = polys[0]
        assert len(p.pts) == 3
        assert p.fill.type == FillType.outline
        assert p.locked is True
        assert p.solder_mask_margin == pytest.approx(0.1)

    def test_fp_poly_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_POLY)
        fp2 = _round_trip(_FP_POLY)
        p1 = [d for d in fp1.drawings if isinstance(d, PcbPoly)][0]
        p2 = [d for d in fp2.drawings if isinstance(d, PcbPoly)][0]
        assert p1.pts == p2.pts
        assert p1.fill.type == p2.fill.type


# ---------------------------------------------------------------------------
# fp_curve round-trip
# ---------------------------------------------------------------------------

_FP_CURVE = _make(
    """\
\t(fp_curve
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 1 2)
\t\t\t(xy 2 2)
\t\t\t(xy 3 0)
\t\t)
\t\t(stroke (width 0.1) (type default))
\t\t(locked yes)
\t\t(layer "F.Cu")
\t\t(uuid "curve-uuid-1")
\t)
"""
)


class TestPcbCurve:
    def test_fp_curve_parses(self) -> None:
        fp = Footprint.from_string(_FP_CURVE)
        curves = [d for d in fp.drawings if isinstance(d, PcbCurve)]
        assert len(curves) == 1
        c = curves[0]
        assert len(c.pts) == 4
        assert c.locked is True
        assert c.id == "curve-uuid-1"

    def test_fp_curve_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_CURVE)
        fp2 = _round_trip(_FP_CURVE)
        c1 = [d for d in fp1.drawings if isinstance(d, PcbCurve)][0]
        c2 = [d for d in fp2.drawings if isinstance(d, PcbCurve)][0]
        assert c1.pts == c2.pts
        assert c1.locked == c2.locked


# ---------------------------------------------------------------------------
# fp_text_box round-trip (start/end form)
# ---------------------------------------------------------------------------

_FP_TEXT_BOX = _make(
    """\
\t(fp_text_box "Box label"
\t\t(locked yes)
\t\t(start 1 1)
\t\t(end 5 3)
\t\t(margins 0.5 0.5 0.5 0.5)
\t\t(angle 15)
\t\t(layer "F.Fab")
\t\t(uuid "tb-uuid-1")
\t\t(effects (font (size 1.27 1.27)))
\t\t(border yes)
\t\t(stroke (width 0.05) (type default))
\t\t(knockout no)
\t)
"""
)


class TestPcbTextBox:
    def test_fp_text_box_parses(self) -> None:
        fp = Footprint.from_string(_FP_TEXT_BOX)
        tbs = [d for d in fp.drawings if isinstance(d, PcbTextBox)]
        assert len(tbs) == 1
        tb = tbs[0]
        assert tb.content == "Box label"
        assert tb.locked is True
        assert tb.start == pytest.approx((1.0, 1.0))
        assert tb.end == pytest.approx((5.0, 3.0))
        assert tb.angle == pytest.approx(15.0)
        assert tb.layer == "F.Fab"
        assert tb.id == "tb-uuid-1"
        assert tb.border_enabled is True
        assert tb.attributes is not None

    def test_fp_text_box_round_trip(self) -> None:
        fp1 = Footprint.from_string(_FP_TEXT_BOX)
        fp2 = _round_trip(_FP_TEXT_BOX)
        tb1 = [d for d in fp1.drawings if isinstance(d, PcbTextBox)][0]
        tb2 = [d for d in fp2.drawings if isinstance(d, PcbTextBox)][0]
        assert tb1.content == tb2.content
        assert tb1.start == tb2.start
        assert tb1.end == tb2.end
        assert tb1.angle == tb2.angle
        assert tb1.border_enabled == tb2.border_enabled

    def test_fp_text_box_pts_form(self) -> None:
        """PcbTextBox with pts instead of start/end."""
        sexpr = _make(
            """\
\t(fp_text_box "poly box"
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 4 0)
\t\t\t(xy 4 2)
\t\t\t(xy 0 2)
\t\t)
\t\t(margins 0 0 0 0)
\t\t(layer "F.Cu")
\t\t(border no)
\t\t(stroke (width 0) (type default))
\t\t(knockout no)
\t)
"""
        )
        fp = Footprint.from_string(sexpr)
        tbs = [d for d in fp.drawings if isinstance(d, PcbTextBox)]
        assert len(tbs) == 1
        assert len(tbs[0].pts) == 4
        assert tbs[0].start is None

    def test_fp_text_box_knockout_in_output(self) -> None:
        """knockout=True emits (knockout yes) in writer output."""
        from eedesigncore.mod.writer import write_mod

        fp = Footprint(
            fp_id="TEST",
            drawings=[
                PcbTextBox(
                    content="kk",
                    layer="F.Cu",
                    start=(0.0, 0.0),
                    end=(2.0, 1.0),
                    knockout=True,
                )
            ],
        )
        out = write_mod(fp)
        assert "(knockout yes)" in out


# ---------------------------------------------------------------------------
# Pad primitives: PrimLine, PrimRect, PrimArc, PrimCircle, PrimCurve, PrimPoly
# ---------------------------------------------------------------------------

_PRIMITIVES_PAD = _make(
    """\
\t(pad "1" smd custom
\t\t(at 0 0)
\t\t(size 1 1)
\t\t(layers "F.Cu")
\t\t(primitives
\t\t\t(gr_line (start -0.5 0) (end 0.5 0) (width 0.1))
\t\t\t(gr_rect (start -0.5 -0.5) (end 0.5 0.5) (radius 0.1) (width 0.05) (fill yes))
\t\t\t(gr_arc (start -0.3 0) (mid 0 -0.3) (end 0.3 0) (width 0.08))
\t\t\t(gr_circle (center 0 0) (end 0.5 0) (width 0.05) (fill yes))
\t\t\t(gr_curve (pts (xy 0 0) (xy 1 0) (xy 1 1) (xy 0 1)) (width 0.1))
\t\t\t(gr_poly (pts (xy 0 0) (xy 1 0) (xy 0.5 1)) (width 0.05) (fill yes))
\t\t)
\t)
"""
)


class TestPadPrimitives:
    def test_primitives_parse_count(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        assert len(pad.primitives) == 6

    def test_prim_line(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[0]
        assert isinstance(prim, PrimLine)
        assert prim.start == pytest.approx((-0.5, 0.0))
        assert prim.end == pytest.approx((0.5, 0.0))
        assert prim.width == pytest.approx(0.1)

    def test_prim_rect(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[1]
        assert isinstance(prim, PrimRect)
        assert prim.corner_radius == pytest.approx(0.1)
        assert prim.fill is True

    def test_prim_arc(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[2]
        assert isinstance(prim, PrimArc)
        assert prim.start == pytest.approx((-0.3, 0.0))
        assert prim.width == pytest.approx(0.08)

    def test_prim_circle(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[3]
        assert isinstance(prim, PrimCircle)
        assert prim.fill is True

    def test_prim_curve(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[4]
        assert isinstance(prim, PrimCurve)
        assert len(prim.pts) == 4

    def test_prim_poly(self) -> None:
        fp = Footprint.from_string(_PRIMITIVES_PAD)
        pad = fp.pads[0]
        assert pad.primitives is not None
        prim = pad.primitives[5]
        assert isinstance(prim, PrimPoly)
        assert len(prim.pts) == 3
        assert prim.fill is True

    def test_primitives_round_trip(self) -> None:
        fp1 = Footprint.from_string(_PRIMITIVES_PAD)
        fp2 = _round_trip(_PRIMITIVES_PAD)
        assert fp1.pads[0].primitives is not None
        assert fp2.pads[0].primitives is not None
        assert len(fp1.pads[0].primitives) == len(fp2.pads[0].primitives)

    def test_primitives_from_model(self) -> None:
        """Construct primitives from model and verify writer output."""
        from eedesigncore.mod.writer import write_mod

        fp = Footprint(
            fp_id="T",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.custom,
                    position=(0.0, 0.0),
                    size=(1.0, 1.0),
                    layers=["F.Cu"],
                    primitives=[
                        PrimLine(start=(-0.5, 0.0), end=(0.5, 0.0), width=0.1),
                        PrimCircle(center=(0.0, 0.0), end=(0.5, 0.0), width=0.05, fill=True),
                        PrimArc(
                            start=(-0.3, 0.0),
                            mid=(0.0, 0.3),
                            end=(0.3, 0.0),
                            width=0.08,
                        ),
                        PrimPoly(
                            pts=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)],
                            width=0.05,
                            fill=True,
                        ),
                        PrimRect(
                            start=(-0.5, -0.5),
                            end=(0.5, 0.5),
                            width=0.05,
                            fill=False,
                        ),
                        PrimCurve(
                            pts=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
                            width=0.1,
                        ),
                    ],
                )
            ],
        )
        out = write_mod(fp)
        assert "(gr_line" in out
        assert "(gr_circle" in out
        assert "(gr_arc" in out
        assert "(gr_poly" in out
        assert "(gr_rect" in out
        assert "(gr_curve" in out


# ---------------------------------------------------------------------------
# 3D Model, Group, Teardrop writer coverage
# ---------------------------------------------------------------------------

_MODEL_3D = _make(
    """\
\t(model "${KICAD6_3DMODEL_DIR}/Resistors_SMD.3dshapes/R_0402_1005Metric.step"
\t\t(hide yes)
\t\t(opacity 0.7)
\t\t(offset (xyz 0 0 0))
\t\t(scale (xyz 1 1 1))
\t\t(rotate (xyz 0 0 90))
\t)
"""
)


class TestModel3D:
    def test_model_parses(self) -> None:
        fp = Footprint.from_string(_MODEL_3D)
        assert len(fp.models) == 1
        m = fp.models[0]
        assert "R_0402" in m.path
        assert m.visible is False
        assert m.opacity == pytest.approx(0.7)
        assert m.rotation == pytest.approx((0.0, 0.0, 90.0))

    def test_model_round_trip(self) -> None:
        fp1 = Footprint.from_string(_MODEL_3D)
        fp2 = _round_trip(_MODEL_3D)
        assert fp1.models[0].path == fp2.models[0].path
        assert fp1.models[0].visible == fp2.models[0].visible
        assert fp1.models[0].rotation == pytest.approx(fp2.models[0].rotation)

    def test_model_in_output(self) -> None:
        fp = Footprint.from_string(_MODEL_3D)
        out = fp.to_string()
        assert "(model" in out
        assert "(hide yes)" in out
        assert "(opacity" in out


_GROUP = _make(
    """\
\t(group "mygroup"
\t\t(uuid "grp-uuid-1")
\t\t(locked yes)
\t\t(members
\t\t\t"item-uuid-1" "item-uuid-2"
\t\t)
\t)
"""
)


class TestGroup:
    def test_group_parses(self) -> None:
        fp = Footprint.from_string(_GROUP)
        assert len(fp.groups) == 1
        g = fp.groups[0]
        assert g.name == "mygroup"
        assert g.id == "grp-uuid-1"
        assert g.locked is True
        assert "item-uuid-1" in g.members
        assert "item-uuid-2" in g.members

    def test_group_round_trip(self) -> None:
        fp1 = Footprint.from_string(_GROUP)
        fp2 = _round_trip(_GROUP)
        assert fp1.groups[0].name == fp2.groups[0].name
        assert fp1.groups[0].members == fp2.groups[0].members


_TEARDROP_PAD = _make(
    """\
\t(pad "1" thru_hole circle
\t\t(at 0 0)
\t\t(size 2 2)
\t\t(drill 1.0)
\t\t(layers "*.Cu" "*.Mask")
\t\t(teardrops
\t\t\t(best_length_ratio 0.5)
\t\t\t(max_length 1.0)
\t\t\t(best_width_ratio 0.5)
\t\t\t(max_width 1.0)
\t\t\t(curved_edges no)
\t\t\t(filter_ratio 0.75)
\t\t\t(enabled yes)
\t\t\t(allow_two_segments no)
\t\t\t(prefer_zone_connections yes)
\t\t)
\t)
"""
)


class TestTeardrops:
    def test_teardrops_parse(self) -> None:
        fp = Footprint.from_string(_TEARDROP_PAD)
        pad = fp.pads[0]
        assert pad.teardrop_params is not None
        assert pad.teardrop_params.enabled is True
        assert pad.teardrop_params.allow_two_segments is False
        assert pad.teardrop_params.prefer_zone_connections is True

    def test_teardrops_round_trip(self) -> None:
        fp1 = Footprint.from_string(_TEARDROP_PAD)
        fp2 = _round_trip(_TEARDROP_PAD)
        assert fp1.pads[0].teardrop_params is not None
        assert fp2.pads[0].teardrop_params is not None
        assert fp1.pads[0].teardrop_params.enabled == fp2.pads[0].teardrop_params.enabled
        assert (
            fp1.pads[0].teardrop_params.allow_two_segments
            == fp2.pads[0].teardrop_params.allow_two_segments
        )

    def test_teardrops_in_output(self) -> None:
        fp = Footprint.from_string(_TEARDROP_PAD)
        out = fp.to_string()
        assert "(teardrops" in out
        assert "(enabled yes)" in out
