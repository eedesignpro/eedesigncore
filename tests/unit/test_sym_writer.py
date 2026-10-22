"""Unit tests for the symbol library writer."""

from eedesigncore.common.models import Color, Fill, FillType, Stroke, StrokeType, TextAttributes
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc as Arc,
)
from eedesigncore.common.sch_family.shapes import (
    SchBezier as Bezier,
)
from eedesigncore.common.sch_family.shapes import (
    SchCircle as Circle,
)
from eedesigncore.common.sch_family.shapes import (
    SchPolyline as Polyline,
)
from eedesigncore.common.sch_family.shapes import (
    SchRect as Rect,
)
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.sym.enums import PinElectricalType, PinShape
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import (
    _dump_arc,
    _dump_bezier,
    _dump_circle,
    _dump_fill,
    _dump_pin,
    _dump_polyline,
    _dump_rect,
    _dump_stroke,
    _dump_sym_text,
    _dump_sym_text_box,
    _fmt,
    dump_sym,
    write_sym,
)


class TestFmtFloat:
    def test_zero_formats_as_0(self) -> None:
        assert _fmt(0.0) == "0"

    def test_integer_float_no_decimal(self) -> None:
        assert _fmt(1.0) == "1"

    def test_float_with_decimal(self) -> None:
        assert _fmt(1.27) == "1.27"

    def test_negative_float(self) -> None:
        assert _fmt(-3.81) == "-3.81"

    def test_very_small_value(self) -> None:
        result = _fmt(0.0001)
        # Should not use scientific notation
        assert "e" not in result.lower()

    def test_very_small_nonzero(self) -> None:
        result = _fmt(0.00005)
        assert "e" not in result.lower()


class TestDumpStroke:
    def test_default_stroke(self) -> None:
        s = Stroke()
        result = _dump_stroke(s)
        assert result == "(stroke (width 0) (type default))"

    def test_stroke_with_color(self) -> None:
        c = Color(r=255, g=0, b=0, a=1.0)
        s = Stroke(width=0.1, type=StrokeType.solid, color=c)
        result = _dump_stroke(s)
        assert "(color 255 0 0 1)" in result
        assert "(type solid)" in result


class TestDumpFill:
    def test_none_fill(self) -> None:
        f = Fill()
        assert _dump_fill(f) == "(fill (type none))"

    def test_outline_fill(self) -> None:
        f = Fill(type=FillType.outline)
        assert _dump_fill(f) == "(fill (type outline))"

    def test_color_fill_with_color(self) -> None:
        c = Color(r=0, g=128, b=255, a=0.5)
        f = Fill(type=FillType.color, color=c)
        result = _dump_fill(f)
        assert "(type color)" in result
        assert "(color 0 128 255" in result

    def test_hatch_fill_no_color(self) -> None:
        f = Fill(type=FillType.hatch)
        result = _dump_fill(f)
        assert "(type hatch)" in result


class TestDumpShapes:
    def test_arc(self) -> None:
        arc = Arc(start=(0.0, 1.0), mid=(1.0, 0.0), end=(2.0, 1.0))
        result = _dump_arc(arc)
        assert "(arc" in result
        assert "(start 0 1)" in result
        assert "(mid 1 0)" in result
        assert "(end 2 1)" in result

    def test_arc_private(self) -> None:
        arc = Arc(private=True, start=(0.0, 0.0), mid=(1.0, 0.0), end=(2.0, 0.0))
        result = _dump_arc(arc)
        assert "private" in result

    def test_arc_with_uuid(self) -> None:
        arc = Arc(id="test-uuid", start=(0.0, 0.0), mid=(0.5, 0.5), end=(1.0, 0.0))
        result = _dump_arc(arc)
        assert '(uuid "test-uuid")' in result

    def test_circle(self) -> None:
        circle = Circle(center=(0.0, 0.0), radius=2.0)
        result = _dump_circle(circle)
        assert "(circle" in result
        assert "(center 0 0)" in result
        assert "(radius 2)" in result

    def test_rect(self) -> None:
        rect = Rect(start=(-1.0, -1.0), end=(1.0, 1.0))
        result = _dump_rect(rect)
        assert "(rectangle" in result
        assert "(start -1 -1)" in result
        assert "(end 1 1)" in result

    def test_rect_with_corner_radius(self) -> None:
        rect = Rect(start=(0.0, 0.0), end=(2.0, 2.0), corner_radius=0.5)
        result = _dump_rect(rect)
        assert "(radius 0.5)" in result

    def test_bezier(self) -> None:
        pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0)]
        bezier = Bezier(pts=pts)
        result = _dump_bezier(bezier)
        assert "(bezier" in result
        assert "(pts" in result
        assert "(xy 0 0)" in result

    def test_polyline(self) -> None:
        pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]
        poly = Polyline(pts=pts)
        result = _dump_polyline(poly)
        assert "(polyline" in result
        assert "(pts" in result


class TestDumpPin:
    def test_basic_pin(self) -> None:
        pin = Pin(
            electrical_type=PinElectricalType.passive,
            shape=PinShape.line,
            position=(0.0, 0.0),
            angle=0.0,
            length=2.54,
            name="~",
            number="1",
        )
        result = _dump_pin(pin)
        assert "(pin passive line" in result
        assert "(length 2.54)" in result

    def test_hidden_pin(self) -> None:
        pin = Pin(
            electrical_type=PinElectricalType.power_in,
            shape=PinShape.line,
            visible=False,
        )
        result = _dump_pin(pin)
        assert "(hide yes)" in result

    def test_pin_with_alternate(self) -> None:
        alt = PinAlternate(
            name="ADC0",
            electrical_type=PinElectricalType.input,
            shape=PinShape.line,
        )
        pin = Pin(
            electrical_type=PinElectricalType.bidirectional,
            shape=PinShape.line,
            alternates=[alt],
        )
        result = _dump_pin(pin)
        assert '(alternate "ADC0" input line)' in result


class TestDumpText:
    def test_sym_text(self) -> None:
        t = SymText(content="Hello", position=(1.0, 2.0), angle=0)
        result = _dump_sym_text(t)
        assert '(text "Hello"' in result
        assert "(at 1 2 0)" in result

    def test_sym_text_private(self) -> None:
        t = SymText(content="X", private=True)
        result = _dump_sym_text(t)
        assert "private" in result

    def test_sym_text_with_effects(self) -> None:
        ta = TextAttributes(font_size=(2.0, 2.0))
        t = SymText(content="A", attributes=ta)
        result = _dump_sym_text(t)
        assert "(effects" in result

    def test_sym_text_box(self) -> None:
        tb = SymTextBox(
            content="Box",
            position=(0.0, 0.0),
            size=(10.0, 5.0),
            margins=(0.5, 0.5, 0.5, 0.5),
        )
        result = _dump_sym_text_box(tb)
        assert '(text_box "Box"' in result
        assert "(size 10 5)" in result
        assert "(margins 0.5 0.5 0.5 0.5)" in result


class TestDumpLibrary:
    def _make_simple_lib(self) -> SymbolLib:
        """Create a minimal library for round-trip testing."""
        ta = TextAttributes(font_size=(1.27, 1.27))
        ref_field = __import__("eedesigncore.common.models", fromlist=["Property"]).Property(
            name="Reference", value="R", attributes=ta
        )
        val_field = __import__("eedesigncore.common.models", fromlist=["Property"]).Property(
            name="Value", value="R", attributes=ta
        )
        sym = Symbol(
            name="R",
            exclude_from_sim=False,
            in_bom=True,
            on_board=True,
            in_pos_files=True,
            duplicate_pin_numbers_are_jumpers=False,
            fields=[ref_field, val_field],
            units=[],
        )
        return SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[sym],
        )

    def test_dump_sym_produces_string(self) -> None:
        lib = self._make_simple_lib()
        result = dump_sym(lib)
        assert "kicad_symbol_lib" in result
        assert "version 20251024" in result

    def test_write_sym_ends_with_newline(self) -> None:
        lib = self._make_simple_lib()
        result = write_sym(lib)
        assert result.endswith("\n")

    def test_derived_symbol_written(self) -> None:
        ta = TextAttributes(font_size=(1.27, 1.27))
        from eedesigncore.common.models import Property

        root_sym = Symbol(
            name="R_base",
            exclude_from_sim=False,
            in_bom=True,
            on_board=True,
            in_pos_files=True,
            duplicate_pin_numbers_are_jumpers=False,
            fields=[
                Property(name="Reference", value="R", attributes=ta),
                Property(name="Value", value="R_base", attributes=ta),
            ],
            units=[],
        )
        derived_sym = Symbol(
            name="R_small",
            extends="R_base",
            fields=[Property(name="Value", value="R_small", attributes=ta)],
        )
        lib = SymbolLib(
            version=20251024,
            generator="kicad_symbol_editor",
            generator_version="10.0",
            symbols=[root_sym, derived_sym],
        )
        result = write_sym(lib)
        assert '(extends "R_base")' in result

    def test_symbol_with_power_type(self) -> None:
        from eedesigncore.common.models import Property

        ta = TextAttributes(font_size=(1.27, 1.27))
        sym = Symbol(
            name="+3V3",
            power_type="global",
            exclude_from_sim=False,
            in_bom=True,
            on_board=True,
            in_pos_files=True,
            duplicate_pin_numbers_are_jumpers=False,
            fields=[
                Property(name="Reference", value="#PWR", attributes=ta),
                Property(name="Value", value="+3V3", attributes=ta),
            ],
            units=[],
        )
        lib = SymbolLib(version=20251024, generator="test", generator_version="1.0", symbols=[sym])
        result = write_sym(lib)
        assert "(power global)" in result

    def test_symbol_with_hidden_pin_numbers(self) -> None:
        from eedesigncore.common.models import Property

        ta = TextAttributes(font_size=(1.27, 1.27))
        sym = Symbol(
            name="MySym",
            show_pin_numbers=False,
            exclude_from_sim=False,
            in_bom=True,
            on_board=True,
            in_pos_files=True,
            duplicate_pin_numbers_are_jumpers=False,
            fields=[
                Property(name="Reference", value="U", attributes=ta),
                Property(name="Value", value="MySym", attributes=ta),
            ],
            units=[],
        )
        lib = SymbolLib(version=20251024, generator="test", generator_version="1.0", symbols=[sym])
        result = write_sym(lib)
        assert "pin_numbers" in result
        assert "(hide yes)" in result

    def test_roundtrip_from_parsed(self) -> None:
        """Parse and re-serialise a small embedded lib, should match."""
        text = """\
(kicad_symbol_lib
\t(version 20251024)
\t(generator "kicad_symbol_editor")
\t(generator_version "10.0")
\t(symbol "PWR_FLAG"
\t\t(power global)
\t\t(pin_numbers
\t\t\t(hide yes)
\t\t)
\t\t(pin_names
\t\t\t(offset 0)
\t\t\t(hide yes)
\t\t)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers no)
\t\t(property "Reference" "#FLG"
\t\t\t(at 0 -3.81 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(hide yes)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "PWR_FLAG"
\t\t\t(at 0 3.81 0)
\t\t\t(show_name no)
\t\t\t(do_not_autoplace no)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(symbol "PWR_FLAG_0_1"
\t\t\t(pin power_out line
\t\t\t\t(at 0 0 90)
\t\t\t\t(length 0)
\t\t\t\t(name "pwr"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(embedded_fonts no)
\t)
)
"""
        lib = SymbolLib.from_string(text)
        result = lib.to_string()
        assert result == text
