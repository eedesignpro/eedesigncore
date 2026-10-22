"""Unit tests for mod/models.py."""

from eedesigncore.mod import PcbText
from eedesigncore.mod.enums import PadShape, PadType
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintAttrs,
    Model3D,
    Pad,
)


class TestFootprintAttrs:
    def test_default_construction(self) -> None:
        attrs = FootprintAttrs()
        assert not attrs.smd
        assert not attrs.through_hole
        assert not attrs.board_only
        assert not attrs.exclude_from_pos_files
        assert not attrs.exclude_from_bom
        assert not attrs.allow_missing_courtyard
        assert not attrs.dnp
        assert not attrs.allow_soldermask_bridges

    def test_has_any_false_by_default(self) -> None:
        assert not FootprintAttrs().has_any()

    def test_has_any_true_when_smd(self) -> None:
        assert FootprintAttrs(smd=True).has_any()

    def test_has_any_true_when_through_hole(self) -> None:
        assert FootprintAttrs(through_hole=True).has_any()


class TestPad:
    def test_minimal_construction(self) -> None:
        """A pad needs layers only; every other field defaults (GAP-018)."""
        pad = Pad(layers=["F.Cu"])
        assert pad.number == ""
        assert pad.pad_type == PadType.smd
        assert pad.shape == PadShape.rect
        assert pad.position == (0.0, 0.0)
        assert pad.angle == 0.0
        assert pad.size == (1.0, 1.0)
        assert pad.drill is None
        assert pad.pad_property is None
        assert pad.layers == ["F.Cu"]
        assert pad.remove_unused_layers is None
        assert pad.id is None

    def test_roundrect_pad(self) -> None:
        pad = Pad(
            number="1",
            pad_type=PadType.smd,
            shape=PadShape.roundrect,
            size=(0.54, 0.64),
            roundrect_rratio=0.25,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        )
        assert pad.roundrect_rratio == 0.25
        assert "F.Cu" in pad.layers


class TestModel3D:
    def test_default_construction(self) -> None:
        m = Model3D()
        assert m.path == ""
        assert m.visible is True
        assert m.opacity is None
        assert m.offset == (0.0, 0.0, 0.0)
        assert m.scale == (1.0, 1.0, 1.0)
        assert m.rotation == (0.0, 0.0, 0.0)

    def test_field_round_trip(self) -> None:
        m = Model3D(
            path="${KICAD10_3DMODEL_DIR}/R.step",
            visible=True,
            opacity=None,
            offset=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0),
        )
        assert m.path == "${KICAD10_3DMODEL_DIR}/R.step"


class TestFootprint:
    def test_default_construction(self) -> None:
        fp = Footprint()
        assert fp.fp_id == ""
        assert fp.layer == "F.Cu"
        assert fp.version == 20260206
        assert fp.duplicate_pad_numbers_are_jumpers is False
        assert fp.embedded_fonts is False
        assert fp.properties == []
        assert fp.pads == []
        assert fp.drawings == []
        assert fp.models == []

    def test_from_string_basic(self) -> None:
        text = (
            '(footprint "TestFP"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = Footprint.from_string(text)
        assert fp.fp_id == "TestFP"
        assert fp.layer == "F.Cu"
        assert fp.duplicate_pad_numbers_are_jumpers is False


class TestDrillInfo:
    def test_round_drill(self) -> None:
        d = DrillInfo(width=1.0)
        assert not d.oval
        assert d.width == 1.0
        assert d.height is None
        assert d.offset is None

    def test_oval_drill(self) -> None:
        d = DrillInfo(oval=True, width=0.6, height=1.0)
        assert d.oval is True
        assert d.width == 0.6
        assert d.height == 1.0


class TestPcbText:
    def test_default_construction(self) -> None:
        t = PcbText()
        assert t.text_type == "user"
        assert t.angle == 0.0
        assert t.visible is True
        assert not t.knockout
        assert not t.unlocked
