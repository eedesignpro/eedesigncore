"""Unit tests for mod/writer.py — coverage for complex paths."""

from __future__ import annotations

from eedesigncore.common.models import (
    Fill,
    FillType,
    Property,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.mod import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbGroup,
    PcbPoly,
    PcbText,
    PcbTextBox,
)
from eedesigncore.mod.enums import PadProperty, PadShape, PadType
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintAttrs,
    Model3D,
    Pad,
    PrimArc,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimPoly,
    PrimRect,
    TeardropParams,
)
from eedesigncore.mod.writer import _dump_fill_bool, _fmt, write_mod


class TestFmtHelper:
    def test_zero_formats_as_zero(self) -> None:
        assert _fmt(0.0) == "0"

    def test_one_formats_as_one(self) -> None:
        assert _fmt(1.0) == "1"

    def test_decimal_formats_without_trailing_zeros(self) -> None:
        assert _fmt(0.25) == "0.25"

    def test_very_small_value(self) -> None:
        result = _fmt(0.00001)
        assert "0.00001" in result


class TestFillBool:
    def test_fill_none(self) -> None:
        assert _dump_fill_bool(Fill(type=FillType.none)) == "(fill no)"

    def test_fill_outline(self) -> None:
        assert _dump_fill_bool(Fill(type=FillType.outline)) == "(fill yes)"

    def test_fill_hatch(self) -> None:
        assert _dump_fill_bool(Fill(type=FillType.hatch)) == "(fill hatch)"

    def test_fill_reverse_hatch(self) -> None:
        assert _dump_fill_bool(Fill(type=FillType.reverse_hatch)) == "(fill reverse_hatch)"

    def test_fill_cross_hatch(self) -> None:
        assert _dump_fill_bool(Fill(type=FillType.cross_hatch)) == "(fill cross_hatch)"


class TestWritePcbText:
    def test_fp_text_angle_always_written(self) -> None:
        """fp_text angle is ALWAYS written, even when zero (ADR from source)."""
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="${REFERENCE}",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.Fab",
                )
            ],
        )
        result = write_mod(fp)
        # Angle 0 must appear
        assert "(at 0 0 0)" in result

    def test_fp_text_knockout_layer(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="test",
                    position=(0.0, 0.0),
                    angle=45.0,
                    layer="F.SilkS",
                    knockout=True,
                )
            ],
        )
        result = write_mod(fp)
        assert "knockout" in result

    def test_fp_text_unlocked(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="test",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.Fab",
                    unlocked=True,
                )
            ],
        )
        result = write_mod(fp)
        assert "(unlocked yes)" in result

    def test_fp_text_hidden_never_emits_hide(self) -> None:
        """GAP-003: a non-field fp_text NEVER emits (hide yes).

        KiCad guards the token with ``field && !IsVisible()`` (format(PCB_TEXT)
        :2300-2301), so plain user text carries no hide regardless of visibility.
        """
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="test",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.Fab",
                    visible=False,
                )
            ],
        )
        result = write_mod(fp)
        assert "(hide yes)" not in result

    def test_hidden_field_property_emits_hide(self) -> None:
        """GAP-003 counterpart: a hidden FIELD property DOES emit (hide yes)."""
        fp = Footprint(
            fp_id="Test",
            properties=[
                Property(
                    name="Reference",
                    value="REF**",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    visible=False,
                )
            ],
        )
        result = write_mod(fp)
        assert "(hide yes)" in result


class TestWriteFpDrawings:
    def test_fp_arc_emitted(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbArc(
                    start=(1.0, 0.0),
                    mid=(0.0, 1.0),
                    end=(-1.0, 0.0),
                    stroke=Stroke(width=0.1, type=StrokeType.solid),
                    layer="F.SilkS",
                )
            ],
        )
        result = write_mod(fp)
        assert "fp_arc" in result
        assert "(start 1 0)" in result
        assert "(mid 0 1)" in result

    def test_fp_circle_fill_always_written(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbCircle(
                    center=(0.0, 0.0),
                    end=(1.0, 0.0),
                    stroke=Stroke(width=0.1, type=StrokeType.solid),
                    fill=Fill(type=FillType.none),
                    layer="F.SilkS",
                )
            ],
        )
        result = write_mod(fp)
        assert "fp_circle" in result
        assert "(fill no)" in result

    def test_fp_poly_fill_yes(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbPoly(
                    pts=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)],
                    stroke=Stroke(width=0.1, type=StrokeType.solid),
                    fill=Fill(type=FillType.outline),
                    layer="F.SilkS",
                )
            ],
        )
        result = write_mod(fp)
        assert "(fill yes)" in result

    def test_fp_curve_emitted(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbCurve(
                    pts=[(0.0, 0.0), (0.5, 1.0), (1.0, 1.0), (1.5, 0.0)],
                    stroke=Stroke(width=0.1, type=StrokeType.solid),
                    layer="F.Cu",
                )
            ],
        )
        result = write_mod(fp)
        assert "fp_curve" in result

    def test_fp_text_box_emitted(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbTextBox(
                    content="hello",
                    start=(0.0, 0.0),
                    end=(10.0, 5.0),
                    margins=(0.1, 0.1, 0.1, 0.1),
                    layer="F.Fab",
                )
            ],
        )
        result = write_mod(fp)
        assert "fp_text_box" in result
        assert '"hello"' in result


class TestWritePad:
    def test_smd_pad_no_drill(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.5, 0.0),
                    size=(0.6, 0.8),
                    layers=["F.Cu", "F.Mask"],
                )
            ],
        )
        result = write_mod(fp)
        assert '(pad "1" smd rect' in result
        assert "(drill" not in result

    def test_thru_hole_pad_with_drill(self) -> None:
        fp = Footprint(
            fp_id="Test",
            attributes=FootprintAttrs(through_hole=True),
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.thru_hole,
                    shape=PadShape.circle,
                    position=(0.0, 0.0),
                    size=(1.7, 1.7),
                    drill=DrillInfo(width=1.0),
                    layers=["*.Cu", "*.Mask"],
                    remove_unused_layers=False,
                )
            ],
        )
        result = write_mod(fp)
        assert "(drill 1)" in result
        assert "(remove_unused_layers no)" in result

    def test_oval_drill(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.thru_hole,
                    shape=PadShape.oval,
                    position=(0.0, 0.0),
                    size=(1.0, 1.5),
                    drill=DrillInfo(oval=True, width=0.6, height=1.0),
                    layers=["*.Cu"],
                    remove_unused_layers=False,
                )
            ],
        )
        result = write_mod(fp)
        assert "(drill oval 0.6 1)" in result

    def test_pad_with_angle(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.0, 0.0),
                    angle=90.0,
                    size=(0.5, 1.0),
                    layers=["F.Cu"],
                )
            ],
        )
        result = write_mod(fp)
        assert "(at 0 0 90)" in result

    def test_pad_roundrect(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.roundrect,
                    position=(0.0, 0.0),
                    size=(0.5, 0.5),
                    layers=["F.Cu"],
                    roundrect_rratio=0.25,
                )
            ],
        )
        result = write_mod(fp)
        assert "(roundrect_rratio 0.25)" in result

    def test_pad_bga_property(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="A1",
                    pad_type=PadType.smd,
                    shape=PadShape.circle,
                    position=(0.0, 0.0),
                    size=(0.4, 0.4),
                    pad_property=PadProperty.bga,
                    layers=["F.Cu", "F.Mask"],
                )
            ],
        )
        result = write_mod(fp)
        assert "(property pad_prop_bga)" in result

    def test_pad_with_uuid(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.0, 0.0),
                    size=(0.5, 0.5),
                    layers=["F.Cu"],
                    id="test-uuid-1234",
                )
            ],
        )
        result = write_mod(fp)
        assert '(uuid "test-uuid-1234")' in result

    def test_pad_teardrop(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.0, 0.0),
                    size=(0.5, 0.5),
                    layers=["F.Cu"],
                    teardrop_params=TeardropParams(enabled=True),
                )
            ],
        )
        result = write_mod(fp)
        assert "teardrops" in result
        assert "(enabled yes)" in result

    def test_pad_solder_margins(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.0, 0.0),
                    size=(0.5, 0.5),
                    layers=["F.Cu"],
                    solder_mask_margin=0.05,
                    solder_paste_margin=-0.03,
                )
            ],
        )
        result = write_mod(fp)
        assert "(solder_mask_margin 0.05)" in result
        assert "(solder_paste_margin -0.03)" in result

    def test_pad_primitives(self) -> None:
        fp = Footprint(
            fp_id="Test",
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.custom,
                    position=(0.0, 0.0),
                    size=(0.5, 0.5),
                    layers=["F.Cu"],
                    custom_shape_clearance="outline",
                    custom_anchor="circle",
                    primitives=[
                        PrimLine(start=(0.0, 0.0), end=(1.0, 0.0), width=0.1),
                        PrimCircle(center=(0.0, 0.0), end=(0.5, 0.0), width=0.1, fill=True),
                        PrimArc(start=(0.0, 0.0), mid=(0.5, 0.5), end=(1.0, 0.0), width=0.1),
                        PrimCurve(pts=[(0.0, 0.0), (0.3, 0.3), (0.7, 0.3), (1.0, 0.0)], width=0.1),
                        PrimRect(start=(0.0, 0.0), end=(1.0, 1.0), width=0.1, fill=False),
                        PrimPoly(pts=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)], width=0.1, fill=True),
                    ],
                )
            ],
        )
        result = write_mod(fp)
        assert "(options" in result
        assert "gr_line" in result
        assert "gr_circle" in result
        assert "gr_arc" in result
        assert "gr_curve" in result
        assert "gr_rect" in result
        assert "gr_poly" in result


class TestWriteModel3D:
    def test_model_offset_scale_rotate(self) -> None:
        fp = Footprint(
            fp_id="Test",
            models=[
                Model3D(
                    path="${KICAD10_3DMODEL_DIR}/test.step",
                    offset=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0),
                    rotation=(0.0, 0.0, 0.0),
                )
            ],
        )
        result = write_mod(fp)
        assert "model" in result
        assert "(offset" in result
        assert "(scale" in result
        assert "(rotate" in result

    def test_model_hidden(self) -> None:
        fp = Footprint(
            fp_id="Test",
            models=[
                Model3D(
                    path="test.step",
                    visible=False,
                    offset=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0),
                    rotation=(0.0, 0.0, 0.0),
                )
            ],
        )
        result = write_mod(fp)
        assert "(hide yes)" in result

    def test_model_opacity(self) -> None:
        fp = Footprint(
            fp_id="Test",
            models=[
                Model3D(
                    path="test.step",
                    opacity=0.5,
                    offset=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0),
                    rotation=(0.0, 0.0, 0.0),
                )
            ],
        )
        result = write_mod(fp)
        assert "(opacity 0.5000)" in result


class TestWriteFootprintRoot:
    def test_duplicate_pad_numbers_always_written(self) -> None:
        fp = Footprint(fp_id="Test", duplicate_pad_numbers_are_jumpers=False)
        result = write_mod(fp)
        assert "(duplicate_pad_numbers_are_jumpers no)" in result

    def test_embedded_fonts_always_written(self) -> None:
        fp = Footprint(fp_id="Test", embedded_fonts=False)
        result = write_mod(fp)
        assert "(embedded_fonts no)" in result

    def test_generator_version_written_when_present(self) -> None:
        fp = Footprint(fp_id="Test", generator_version="10.0")
        result = write_mod(fp)
        assert '(generator_version "10.0")' in result

    def test_generator_version_omitted_when_none(self) -> None:
        fp = Footprint(fp_id="Test", generator_version=None)
        result = write_mod(fp)
        assert "generator_version" not in result

    def test_locked_written_when_true(self) -> None:
        fp = Footprint(fp_id="Test", locked=True)
        result = write_mod(fp)
        assert "(locked yes)" in result

    def test_locked_omitted_when_false(self) -> None:
        fp = Footprint(fp_id="Test", locked=False)
        result = write_mod(fp)
        assert "locked" not in result

    def test_descr_written_when_present(self) -> None:
        fp = Footprint(fp_id="Test", description="A test footprint")
        result = write_mod(fp)
        assert '(descr "A test footprint")' in result

    def test_tags_written_when_present(self) -> None:
        fp = Footprint(fp_id="Test", keywords="resistor smd")
        result = write_mod(fp)
        assert '(tags "resistor smd")' in result

    def test_attr_smd(self) -> None:
        fp = Footprint(fp_id="Test", attributes=FootprintAttrs(smd=True))
        result = write_mod(fp)
        assert "(attr smd)" in result

    def test_attr_through_hole(self) -> None:
        fp = Footprint(fp_id="Test", attributes=FootprintAttrs(through_hole=True))
        result = write_mod(fp)
        assert "(attr through_hole)" in result

    def test_attr_omitted_when_no_flags(self) -> None:
        fp = Footprint(fp_id="Test", attributes=FootprintAttrs())
        result = write_mod(fp)
        assert "(attr" not in result

    def test_property_with_layer(self) -> None:
        fp = Footprint(
            fp_id="Test",
            properties=[
                Property(
                    name="Reference",
                    value="REF**",
                    position=(0.0, -1.17),
                    angle=0.0,
                    layer="F.SilkS",
                    attributes=TextAttributes(
                        font_size=(1.0, 1.0),
                        font_thickness=0.15,
                    ),
                )
            ],
        )
        result = write_mod(fp)
        assert '(layer "F.SilkS")' in result

    def test_property_unlocked(self) -> None:
        fp = Footprint(
            fp_id="Test",
            properties=[
                Property(
                    name="Datasheet",
                    value="",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.Fab",
                    unlocked=True,
                    visible=False,
                )
            ],
        )
        result = write_mod(fp)
        assert "(unlocked yes)" in result

    def test_group_emitted(self) -> None:
        fp = Footprint(
            fp_id="Test",
            groups=[PcbGroup(name="mygroup", id="abc-123", members=["uuid1", "uuid2"])],
        )
        result = write_mod(fp)
        assert '"mygroup"' in result
        assert "(members" in result

    def test_fp_filters_emitted(self) -> None:
        fp = Footprint(fp_id="Test", fp_filters="R_*")
        result = write_mod(fp)
        assert "ki_fp_filters" in result

    def test_solder_margins(self) -> None:
        fp = Footprint(fp_id="Test", solder_mask_margin=0.1, clearance=0.2)
        result = write_mod(fp)
        assert "(solder_mask_margin 0.1)" in result
        assert "(clearance 0.2)" in result

    def test_private_layers(self) -> None:
        fp = Footprint(fp_id="Test", private_layers=["User.1", "User.2"])
        result = write_mod(fp)
        assert "(private_layers" in result
        assert '"User.1"' in result


class TestRenderCache:
    """Tests for render_cache emission on PcbText, PcbTextBox, and Property."""

    def test_fp_text_with_render_cache(self) -> None:
        from eedesigncore.common.models import RenderCache, RenderCachePolygon

        rc = RenderCache(
            resolved_text="REF**",
            angle=0.0,
            polygons=[RenderCachePolygon(pts=[(0.1, 0.2), (0.3, 0.4)])],
        )
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="${REFERENCE}",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="User.13",
                    render_cache=rc,
                )
            ],
        )
        result = write_mod(fp)
        assert "(render_cache" in result
        assert '"REF**"' in result
        assert "(polygon" in result
        assert "(pts" in result
        assert "(xy 0.1 0.2)" in result

    def test_fp_text_box_with_render_cache(self) -> None:
        from eedesigncore.common.models import RenderCache, RenderCachePolygon

        rc = RenderCache(
            resolved_text="box text",
            angle=45.0,
            polygons=[RenderCachePolygon(pts=[(0.0, 0.0), (1.0, 0.0)])],
        )
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbTextBox(
                    content="box text",
                    start=(0.0, 0.0),
                    end=(10.0, 5.0),
                    layer="F.Fab",
                    render_cache=rc,
                )
            ],
        )
        result = write_mod(fp)
        assert "(render_cache" in result
        assert '"box text"' in result

    def test_property_with_render_cache(self) -> None:
        from eedesigncore.common.models import RenderCache, RenderCachePolygon

        rc = RenderCache(
            resolved_text="REF**",
            angle=0.0,
            polygons=[RenderCachePolygon(pts=[(0.5, 0.5)])],
        )
        fp = Footprint(
            fp_id="Test",
            properties=[
                Property(
                    name="Reference",
                    value="REF**",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    render_cache=rc,
                )
            ],
        )
        result = write_mod(fp)
        assert "(render_cache" in result

    def test_fp_text_without_render_cache_omits_token(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="hello",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    render_cache=None,
                )
            ],
        )
        result = write_mod(fp)
        assert "render_cache" not in result


class TestFontFace:
    """Tests for font_face support in effects blocks."""

    def test_fp_text_with_font_face_emits_face_token(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="hello",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    attributes=TextAttributes(
                        font_face="Arial",
                        font_size=(0.16, 0.16),
                        font_thickness=0.1,
                    ),
                )
            ],
        )
        result = write_mod(fp)
        assert '(face "Arial")' in result

    def test_fp_text_without_font_face_omits_face_token(self) -> None:
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="hello",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    attributes=TextAttributes(font_size=(1.0, 1.0)),
                )
            ],
        )
        result = write_mod(fp)
        assert "(face" not in result

    def test_face_appears_before_size(self) -> None:
        """KiCad emits face before size in font block."""
        fp = Footprint(
            fp_id="Test",
            drawings=[
                PcbText(
                    text_type="user",
                    content="hello",
                    position=(0.0, 0.0),
                    angle=0.0,
                    layer="F.SilkS",
                    attributes=TextAttributes(
                        font_face="Arial",
                        font_size=(0.16, 0.16),
                    ),
                )
            ],
        )
        result = write_mod(fp)
        face_idx = result.index("(face")
        size_idx = result.index("(size")
        assert face_idx < size_idx, "face must appear before size in font block"
