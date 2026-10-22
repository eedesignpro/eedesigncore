"""Unit tests for mod/loader.py."""

from pathlib import Path

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.enums import PadProperty, PadShape, PadType
from eedesigncore.mod.models import Footprint

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "mod"
FIXTURE_1 = FIXTURE_DIR / "R_0402_1005Metric.kicad_mod"
_BGA_FNAME = "BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD.kicad_mod"
FIXTURE_5 = FIXTURE_DIR / _BGA_FNAME


class TestParseFixture1:
    """Tests for R_0402_1005Metric.kicad_mod (SMD, no UUIDs)."""

    def setup_method(self) -> None:
        self.fp = Footprint.from_file(FIXTURE_1)

    def test_fp_id(self) -> None:
        assert self.fp.fp_id == "R_0402_1005Metric"

    def test_layer(self) -> None:
        assert self.fp.layer == "F.Cu"

    def test_version(self) -> None:
        assert self.fp.version == 20260206

    def test_generator(self) -> None:
        assert self.fp.generator == "kicad-footprint-generator"

    def test_generator_version_absent(self) -> None:
        assert self.fp.generator_version is None

    def test_smd_attr(self) -> None:
        assert self.fp.attributes.smd is True
        assert self.fp.attributes.through_hole is False

    def test_pad_count(self) -> None:
        assert len(self.fp.pads) == 2

    def test_pad_types_smd(self) -> None:
        for pad in self.fp.pads:
            assert pad.pad_type == PadType.smd

    def test_pad_shape_roundrect(self) -> None:
        for pad in self.fp.pads:
            assert pad.shape == PadShape.roundrect

    def test_pad_roundrect_rratio(self) -> None:
        for pad in self.fp.pads:
            assert pad.roundrect_rratio == 0.25

    def test_pad_layers(self) -> None:
        for pad in self.fp.pads:
            assert "F.Cu" in pad.layers
            assert "F.Mask" in pad.layers
            assert "F.Paste" in pad.layers

    def test_pad_no_uuid(self) -> None:
        for pad in self.fp.pads:
            assert pad.id is None

    def test_model_path(self) -> None:
        assert len(self.fp.models) == 1
        assert "R_0402_1005Metric.step" in self.fp.models[0].path

    def test_duplicate_pad_numbers_are_jumpers_false(self) -> None:
        assert self.fp.duplicate_pad_numbers_are_jumpers is False

    def test_embedded_fonts_false(self) -> None:
        assert self.fp.embedded_fonts is False

    def test_properties_count(self) -> None:
        # Should have Reference, Value, KiLib_Generator
        assert len(self.fp.properties) == 3

    def test_property_layer(self) -> None:
        ref = self.fp.properties[0]
        assert ref.name == "Reference"
        assert ref.layer == "F.SilkS"


class TestParseFixture5:
    """Tests for BGA fixture (pcbnew-saved, all UUIDs present)."""

    def setup_method(self) -> None:
        self.fp = Footprint.from_file(FIXTURE_5)

    def test_fp_id(self) -> None:
        assert "BGA-100" in self.fp.fp_id

    def test_generator_version(self) -> None:
        assert self.fp.generator_version == "10.0"

    def test_pad_count(self) -> None:
        assert len(self.fp.pads) == 100

    def test_pad_uuids_present(self) -> None:
        for pad in self.fp.pads:
            assert pad.id is not None
            assert len(pad.id) > 0

    def test_pad_property_bga(self) -> None:
        for pad in self.fp.pads:
            assert pad.pad_property == PadProperty.bga

    def test_property_uuids_present(self) -> None:
        # BGA fixture has UUIDs on all properties
        for prop in self.fp.properties:
            assert prop.id is not None

    def test_property_unlocked(self) -> None:
        # Datasheet and Description properties should have unlocked=True
        unlocked_props = [p for p in self.fp.properties if p.unlocked]
        assert len(unlocked_props) >= 2


class TestParseTHT:
    """Tests for THT footprint with drill tokens."""

    def setup_method(self) -> None:
        fixture = FIXTURE_DIR / "PinHeader_1x02_P2.54mm_Vertical.kicad_mod"
        self.fp = Footprint.from_file(fixture)

    def test_through_hole_attr(self) -> None:
        assert self.fp.attributes.through_hole is True

    def test_pad_count(self) -> None:
        assert len(self.fp.pads) == 2

    def test_pad_drill(self) -> None:
        for pad in self.fp.pads:
            assert pad.drill is not None
            assert pad.drill.width == 1.0

    def test_remove_unused_layers(self) -> None:
        for pad in self.fp.pads:
            assert pad.remove_unused_layers is not None
            assert pad.remove_unused_layers is False


class TestParseRoundRect:
    """Tests parsing a roundrect pad shape."""

    def test_roundrect_shape(self) -> None:
        text = (
            '(footprint "TestRR"\n'
            "  (version 20260206)\n"
            '  (generator "test")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            '  (pad "1" smd roundrect\n'
            "    (at 0 0)\n"
            "    (size 0.5 0.5)\n"
            '    (layers "F.Cu" "F.Mask")\n'
            "    (roundrect_rratio 0.25)\n"
            "  )\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = Footprint.from_string(text)
        assert len(fp.pads) == 1
        assert fp.pads[0].shape == PadShape.roundrect
        assert fp.pads[0].roundrect_rratio == 0.25


class TestParseError:
    """Tests that KicadParseError is raised for invalid input."""

    def test_missing_fp_id_raises(self) -> None:
        with pytest.raises(KicadParseError):
            Footprint.from_string("(footprint)")

    def test_wrong_root_token_raises(self) -> None:
        with pytest.raises(KicadParseError):
            Footprint.from_string("(kicad_pcb (version 1))")


_RC_FNAME = "TEST_POINT_MINI_BLK_render_cache.kicad_mod"
RENDER_CACHE_FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "mod" / _RC_FNAME


class TestParseRenderCacheFixture:
    """Tests for the render_cache fixture."""

    def setup_method(self) -> None:
        self.fp = Footprint.from_file(RENDER_CACHE_FIXTURE)

    def test_fp_id(self) -> None:
        assert self.fp.fp_id == "TEST POINT MINI_BLK"

    def test_generator_pcbnew(self) -> None:
        assert self.fp.generator == "pcbnew"
        assert self.fp.generator_version == "10.0"

    def test_has_render_cache_on_fp_text(self) -> None:
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in self.fp.drawings if isinstance(d, PcbText)]
        texts_with_cache = [t for t in fp_texts if t.render_cache is not None]
        assert len(texts_with_cache) >= 1

    def test_render_cache_resolved_text(self) -> None:
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in self.fp.drawings if isinstance(d, PcbText)]
        cache_texts = [t for t in fp_texts if t.render_cache is not None]
        rc = cache_texts[0].render_cache
        assert rc is not None
        assert rc.resolved_text == "REF**"
        assert rc.angle == 0.0

    def test_render_cache_has_polygons(self) -> None:
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in self.fp.drawings if isinstance(d, PcbText)]
        cache_texts = [t for t in fp_texts if t.render_cache is not None]
        rc = cache_texts[0].render_cache
        assert rc is not None
        assert len(rc.polygons) == 5  # fixture has 5 polygons for "REF**"

    def test_render_cache_polygon_pts(self) -> None:
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in self.fp.drawings if isinstance(d, PcbText)]
        cache_texts = [t for t in fp_texts if t.render_cache is not None]
        rc = cache_texts[0].render_cache
        assert rc is not None
        poly = rc.polygons[0]
        assert len(poly.pts) > 0
        # First point from fixture
        assert poly.pts[0] == pytest.approx((0.103033, -0.160129))

    def test_font_face_arial_loaded(self) -> None:
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in self.fp.drawings if isinstance(d, PcbText)]
        arial_texts = [
            t for t in fp_texts if t.attributes is not None and t.attributes.font_face == "Arial"
        ]
        assert len(arial_texts) >= 1


class TestLoadRenderCacheFromString:
    """Parse render_cache directly from a minimal S-expression string."""

    def test_fp_text_render_cache_parsed(self) -> None:
        text = (
            '(footprint "RC_Test"\n'
            "  (version 20260206)\n"
            '  (generator "pcbnew")\n'
            '  (layer "F.Cu")\n'
            "  (duplicate_pad_numbers_are_jumpers no)\n"
            '  (fp_text user "${REFERENCE}"\n'
            "    (at 0 0 0)\n"
            '    (layer "User.13")\n'
            "    (effects\n"
            "      (font\n"
            '        (face "Arial")\n'
            "        (size 0.16002 0.16002)\n"
            "        (thickness 0.1016)\n"
            "      )\n"
            "    )\n"
            '    (render_cache "REF**" 0\n'
            "      (polygon\n"
            "        (pts\n"
            "          (xy 0.1 0.2) (xy 0.3 0.4)\n"
            "        )\n"
            "      )\n"
            "    )\n"
            "  )\n"
            "  (embedded_fonts no)\n"
            ")\n"
        )
        fp = Footprint.from_string(text)
        from eedesigncore.mod import PcbText

        fp_texts = [d for d in fp.drawings if isinstance(d, PcbText)]
        assert len(fp_texts) == 1
        t = fp_texts[0]

        # font_face
        assert t.attributes is not None
        assert t.attributes.font_face == "Arial"

        # render_cache
        assert t.render_cache is not None
        assert t.render_cache.resolved_text == "REF**"
        assert t.render_cache.angle == 0.0
        assert len(t.render_cache.polygons) == 1
        assert t.render_cache.polygons[0].pts[0] == pytest.approx((0.1, 0.2))
