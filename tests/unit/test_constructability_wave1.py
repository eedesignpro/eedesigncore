"""Unit tests for constructability wave 1 (iteration-11 T3, plan R5).

Covers GAP-015 (cross-field ``model_validator``s), GAP-018 (the pad layers
guard), GAP-020 (mandatory-field factories) and GAP-023 (the rect radius guard).
Every rejection below matches an unconditional emit in the KiCad writer, and a
scan of 30006 corpus files found no file that trips any of them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eedesigncore.common.pcb_family import Padstack, PadstackLayer, PcbCurve, PcbRect
from eedesigncore.common.sch_family.shapes import SchBezier, SchRect
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.mod.enums import PadShape, PadType
from eedesigncore.mod.models import DrillInfo, Footprint, Pad, PrimCurve
from eedesigncore.mod.writer import write_mod
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import write_sym

_PTS4 = [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (3.0, 1.0)]


# ---------------------------------------------------------------------------
# GAP-018 — pad layers guard
# ---------------------------------------------------------------------------


class TestPadLayersGuard:
    def test_bare_pad_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one layer"):
            Pad()

    def test_empty_layers_list_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one layer"):
            Pad(number="1", layers=[])

    def test_one_layer_is_enough(self) -> None:
        assert Pad(layers=["F.Cu"]).layers == ["F.Cu"]


# ---------------------------------------------------------------------------
# GAP-015 — cross-field invariants
# ---------------------------------------------------------------------------


class TestPadCrossFieldInvariants:
    @pytest.mark.parametrize("pad_type", [PadType.thru_hole, PadType.np_thru_hole])
    def test_through_hole_without_drill_is_rejected(self, pad_type: PadType) -> None:
        with pytest.raises(ValidationError, match="needs a drill"):
            Pad(pad_type=pad_type, layers=["*.Cu"])

    def test_through_hole_with_drill_is_accepted(self) -> None:
        pad = Pad(pad_type=PadType.thru_hole, layers=["*.Cu"], drill=DrillInfo(width=0.8))
        assert pad.drill is not None

    def test_smd_without_drill_is_accepted(self) -> None:
        assert Pad(pad_type=PadType.smd, layers=["F.Cu"]).drill is None

    def test_roundrect_without_rratio_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="needs roundrect_rratio"):
            Pad(shape=PadShape.roundrect, layers=["F.Cu"])

    def test_roundrect_with_rratio_is_accepted(self) -> None:
        pad = Pad(shape=PadShape.roundrect, layers=["F.Cu"], roundrect_rratio=0.25)
        assert pad.roundrect_rratio == 0.25

    def test_custom_shape_without_primitives_is_accepted(self) -> None:
        """KiCad prints a bare ``(primitives)``, so the audit rule does not hold (ADR-048)."""
        pad = Pad(shape=PadShape.custom, layers=["F.Cu"])
        assert pad.primitives is None

    def test_remove_unused_layers_on_smd_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="thru_hole pad only"):
            Pad(pad_type=PadType.smd, layers=["F.Cu"], remove_unused_layers=True)

    def test_remove_unused_layers_on_thru_hole_is_accepted(self) -> None:
        pad = Pad(
            pad_type=PadType.thru_hole,
            layers=["*.Cu"],
            drill=DrillInfo(width=0.8),
            remove_unused_layers=False,
        )
        assert pad.remove_unused_layers is False


class TestPadstackLayersGuard:
    def test_empty_padstack_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one PadstackLayer"):
            Padstack()

    def test_one_layer_is_enough(self) -> None:
        assert len(Padstack(layers=[PadstackLayer(layer_name="F.Cu")]).layers) == 1


class TestCurveControlPointCount:
    @pytest.mark.parametrize("count", [0, 1, 3, 5])
    def test_pcb_curve_rejects_other_counts(self, count: int) -> None:
        with pytest.raises(ValidationError, match="exactly 4 control points"):
            PcbCurve(pts=_PTS4[:count] if count <= 4 else [*_PTS4, (4.0, 2.0)])

    def test_pcb_curve_accepts_four(self) -> None:
        assert len(PcbCurve(pts=_PTS4).pts) == 4

    def test_prim_curve_rejects_three(self) -> None:
        with pytest.raises(ValidationError, match="exactly 4 control points"):
            PrimCurve(pts=_PTS4[:3])

    def test_prim_curve_accepts_four(self) -> None:
        assert len(PrimCurve(pts=_PTS4, width=0.1).pts) == 4

    def test_sch_bezier_rejects_two(self) -> None:
        with pytest.raises(ValidationError, match="exactly 4 control points"):
            SchBezier(pts=_PTS4[:2])

    def test_sch_bezier_accepts_four(self) -> None:
        assert len(SchBezier(pts=_PTS4).pts) == 4


# ---------------------------------------------------------------------------
# GAP-020 — mandatory-field factories
# ---------------------------------------------------------------------------


class TestFootprintFactory:
    def test_bare_footprint_still_builds_without_fields(self) -> None:
        """No rejecting validator ships. The factory is the fix (plan risk row)."""
        assert Footprint().properties == []

    def test_new_seeds_reference_and_value(self) -> None:
        fp = Footprint.new("R_0603_1608Metric")
        assert [p.name for p in fp.properties] == ["Reference", "Value"]

    def test_new_uses_kicad_defaults(self) -> None:
        fp = Footprint.new("R_0603_1608Metric")
        ref, val = fp.properties
        assert (ref.value, ref.layer, ref.visible) == ("REF**", "F.SilkS", True)
        assert (val.value, val.layer, val.visible) == ("R_0603_1608Metric", "F.Fab", True)

    def test_new_strips_the_library_nickname_from_the_value(self) -> None:
        assert Footprint.new("Resistor_SMD:R_0603").properties[1].value == "R_0603"

    def test_new_accepts_explicit_reference_and_value(self) -> None:
        fp = Footprint.new("X", reference="C**", value="10uF")
        assert (fp.properties[0].value, fp.properties[1].value) == ("C**", "10uF")

    def test_new_writes_both_fields(self) -> None:
        text = write_mod(Footprint.new("R_0603"))
        assert '(property "Reference" "REF**"' in text
        assert '(property "Value" "R_0603"' in text


class TestSymbolFactory:
    def test_bare_symbol_still_builds_without_fields(self) -> None:
        assert Symbol(name="R").fields == []

    def test_new_seeds_the_five_mandatory_fields(self) -> None:
        sym = Symbol.new("R")
        assert [f.name for f in sym.fields] == [
            "Reference",
            "Value",
            "Footprint",
            "Datasheet",
            "Description",
        ]

    def test_new_matches_lib_symbol_visibility(self) -> None:
        sym = Symbol.new("R")
        assert [f.visible for f in sym.fields] == [True, True, False, False, False]

    def test_new_defaults_value_to_the_symbol_name(self) -> None:
        assert Symbol.new("R").fields[1].value == "R"

    def test_new_accepts_every_field(self) -> None:
        sym = Symbol.new(
            "C",
            reference="C",
            value="100nF",
            footprint="Capacitor_SMD:C_0402",
            datasheet="~",
            description="Unpolarized capacitor",
        )
        assert [f.value for f in sym.fields] == [
            "C",
            "100nF",
            "Capacitor_SMD:C_0402",
            "~",
            "Unpolarized capacitor",
        ]

    def test_new_writes_the_fields(self) -> None:
        text = write_sym(SymbolLib(symbols=[Symbol.new("R")]))
        assert '(property "Reference" "U"' in text
        assert '(property "Description" ""' in text


# ---------------------------------------------------------------------------
# GAP-023 — rect radius guard
# ---------------------------------------------------------------------------


def _sym_with_rect(rect: SchRect) -> str:
    sym = Symbol.new("Rect")
    sym.units = [SymbolUnit(name="Rect_1_1", items=[rect])]
    return write_sym(SymbolLib(symbols=[sym]))


class TestRectRadiusGuard:
    def test_sch_rect_omits_a_zero_radius(self) -> None:
        assert "(radius" not in _sym_with_rect(SchRect(corner_radius=0.0))

    def test_sch_rect_emits_a_positive_radius(self) -> None:
        assert "(radius 0.5)" in _sym_with_rect(SchRect(corner_radius=0.5))

    def test_sch_rect_omits_an_absent_radius(self) -> None:
        assert "(radius" not in _sym_with_rect(SchRect())

    def test_fp_rect_omits_a_zero_radius(self) -> None:
        fp = Footprint.new("R")
        fp.drawings = [PcbRect(corner_radius=0.0)]
        assert "(radius" not in write_mod(fp)

    def test_fp_rect_emits_a_positive_radius(self) -> None:
        fp = Footprint.new("R")
        fp.drawings = [PcbRect(corner_radius=0.25)]
        assert "(radius 0.25)" in write_mod(fp)

    def test_pad_gr_rect_primitive_omits_a_zero_radius(self) -> None:
        from eedesigncore.mod.models import PrimRect

        fp = Footprint.new("R")
        fp.pads = [
            Pad(
                shape=PadShape.custom,
                layers=["F.Cu"],
                primitives=[PrimRect(end=(1.0, 1.0), corner_radius=0.0, width=0.1)],
            )
        ]
        assert "(radius" not in write_mod(fp)

    def test_pad_gr_rect_primitive_emits_a_positive_radius(self) -> None:
        from eedesigncore.mod.models import PrimRect

        fp = Footprint.new("R")
        fp.pads = [
            Pad(
                shape=PadShape.custom,
                layers=["F.Cu"],
                primitives=[PrimRect(end=(1.0, 1.0), corner_radius=0.3, width=0.1)],
            )
        ]
        assert "(radius 0.3)" in write_mod(fp)
