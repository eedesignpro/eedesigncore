"""Unit tests for common Pydantic v2 models."""

import base64

import pytest
from pydantic import ValidationError

from eedesigncore.common.models import (
    Color,
    EmbeddedFile,
    Fill,
    FillType,
    JustifyH,
    JustifyV,
    Property,
    Stroke,
    StrokeType,
    TextAttributes,
)


class TestColor:
    def test_valid_color(self) -> None:
        c = Color(r=255, g=128, b=0, a=1.0)
        assert c.r == 255
        assert c.a == 1.0

    def test_r_too_high_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=256, g=0, b=0, a=0.0)

    def test_g_too_high_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=0, g=256, b=0, a=0.0)

    def test_b_too_high_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=0, g=0, b=256, a=0.0)

    def test_negative_r_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=-1, g=0, b=0, a=0.0)

    def test_alpha_too_high_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=0, g=0, b=0, a=1.1)

    def test_alpha_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            Color(r=0, g=0, b=0, a=-0.1)

    def test_zero_color(self) -> None:
        c = Color(r=0, g=0, b=0, a=0.0)
        assert c.r == 0


class TestStroke:
    def test_default_construction(self) -> None:
        s = Stroke()
        assert s.width == 0.0
        assert s.type == StrokeType.default
        assert s.color is None

    def test_explicit_stroke(self) -> None:
        c = Color(r=255, g=0, b=0, a=1.0)
        s = Stroke(width=0.1, type=StrokeType.solid, color=c)
        assert s.width == pytest.approx(0.1)
        assert s.type == StrokeType.solid
        assert s.color is not None
        assert s.color.r == 255


class TestFill:
    def test_default_construction(self) -> None:
        f = Fill()
        assert f.type == FillType.none
        assert f.color is None

    def test_color_fill(self) -> None:
        c = Color(r=0, g=128, b=255, a=0.5)
        f = Fill(type=FillType.color, color=c)
        assert f.type == FillType.color


class TestTextAttributes:
    def test_default_justify_center(self) -> None:
        ta = TextAttributes()
        assert ta.justify_h == JustifyH.center
        assert ta.justify_v == JustifyV.center
        assert ta.mirror is False
        assert ta.visible is True

    def test_left_justify(self) -> None:
        ta = TextAttributes(justify_h=JustifyH.left)
        assert ta.justify_h == JustifyH.left

    def test_top_justify(self) -> None:
        ta = TextAttributes(justify_v=JustifyV.top)
        assert ta.justify_v == JustifyV.top

    def test_mirror_flag(self) -> None:
        ta = TextAttributes(mirror=True)
        assert ta.mirror is True

    def test_hidden_text(self) -> None:
        ta = TextAttributes(visible=False)
        assert ta.visible is False


class TestProperty:
    def test_basic_property(self) -> None:
        p = Property(name="Reference", value="R1")
        assert p.name == "Reference"
        assert p.value == "R1"
        assert p.private is False
        assert p.visible is True

    def test_private_property(self) -> None:
        p = Property(name="ki_keywords", value="resistor", private=True, visible=False)
        assert p.private is True
        assert p.visible is False

    def test_with_attributes(self) -> None:
        ta = TextAttributes(font_size=(1.27, 1.27))
        p = Property(name="Value", value="R", attributes=ta)
        assert p.attributes is not None
        assert p.attributes.font_size == (1.27, 1.27)

    def test_position_and_angle(self) -> None:
        p = Property(name="Footprint", value="", position=(1.0, 2.0), angle=90.0)
        assert p.position == (1.0, 2.0)
        assert p.angle == pytest.approx(90.0)


class TestEmbeddedFile:
    def test_embedded_file_invalid_base64(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddedFile(name="test", file_type="datafile", data="!!!invalid!!!", checksum="abc")

    def test_embedded_file_valid_base64(self) -> None:
        data = base64.b64encode(b"hello world").decode()
        ef = EmbeddedFile(name="test", file_type="datafile", data=data, checksum="abc")
        assert ef.data == data

    def test_embedded_file_empty_data_ok(self) -> None:
        ef = EmbeddedFile(name="test", file_type="datafile", data="", checksum="abc")
        assert ef.data == ""

    def test_embedded_file_with_padding(self) -> None:
        # Verify that '=' padding characters are accepted
        data = base64.b64encode(b"ab").decode()  # produces "YWI="
        ef = EmbeddedFile(name="test", file_type="datafile", data=data, checksum="abc")
        assert ef.data == data
