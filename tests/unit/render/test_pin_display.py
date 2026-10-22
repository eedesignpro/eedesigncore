"""Editor pin geometry and optional electrical type labels."""

from unittest.mock import Mock

import pytest

from eedesigncore import (
    Pin,
    PinAlternate,
    PinElectricalType,
    PlacedSymbolPin,
    SchematicStyle,
    SymbolStyle,
    render_schematic_svg,
    render_symbol_svg,
)
from tests.unit.render.test_library_svg import WINDOW, elements, symbol_with
from tests.unit.render.test_schematic_svg import schematic_with


@pytest.mark.parametrize("shape", ["clock", "inverted_clock", "clock_low"])
@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (0, "M2.5400,0.6350 L3.1750,0.0000 L2.5400,-0.6350"),
        (90, "M0.6350,-2.5400 L0.0000,-3.1750 L-0.6350,-2.5400"),
        (180, "M-2.5400,0.6350 L-3.1750,0.0000 L-2.5400,-0.6350"),
        (270, "M0.6350,2.5400 L0.0000,3.1750 L-0.6350,2.5400"),
    ],
)
def test_editor_clock_triangle(shape: str, angle: int, expected: str) -> None:
    pin = Pin(electrical_type="input", shape=shape, angle=angle)
    assert expected in [item.get("d") for item in elements(symbol_with(pin))]


@pytest.mark.parametrize(
    ("kind", "label"),
    list(
        zip(
            PinElectricalType,
            [
                "Input",
                "Output",
                "Bidirectional",
                "Tri-state",
                "Passive",
                "Free",
                "Unspecified",
                "Power input",
                "Power output",
                "Open collector",
                "Open emitter",
                "Unconnected",
            ],
            strict=True,
        )
    ),
)
def test_optional_type_labels(kind: PinElectricalType, label: str) -> None:
    symbol = symbol_with(Pin(electrical_type=kind, shape="line"))
    before = symbol.model_dump()
    font = Mock()
    font.text_strokes.return_value = [[(0, 0), (1000000, 0)]]
    render_symbol_svg(symbol, view_box=WINDOW, font=font)
    font.text_strokes.assert_not_called()
    svg = render_symbol_svg(
        symbol,
        view_box=WINDOW,
        font=font,
        style=SymbolStyle(show_pin_types=True, pin_types="#123456"),
    )
    assert font.text_strokes.call_args.args[0] == label
    assert 'stroke="#123456"' in svg
    assert symbol.model_dump() == before


@pytest.mark.parametrize(
    ("angle", "position", "text_angle", "align"),
    [
        (0, (-526350, 0), 0, "right"),
        (90, (0, 526350), 90, "right"),
        (180, (526350, 0), 0, "left"),
        (270, (0, -526350), 90, "left"),
    ],
)
def test_type_text_minimum_size_and_orientation(angle, position, text_angle, align) -> None:
    font = Mock()
    font.text_strokes.return_value = []
    symbol = symbol_with(Pin(electrical_type="input", shape="line", angle=angle, name_text_size=0))
    render_symbol_svg(symbol, view_box=WINDOW, font=font, style=SymbolStyle(show_pin_types=True))
    call = font.text_strokes.call_args
    assert call.args[1:4] == ((700000, 700000), position, 87500)
    assert call.kwargs["angle"] == text_angle
    assert call.kwargs["halign"] == align


def test_hidden_type_and_schematic_alternate() -> None:
    font = Mock()
    font.text_strokes.return_value = []
    pin = Pin(electrical_type="input", shape="line", visible=False)
    symbol = symbol_with(pin)
    style = SymbolStyle(show_pin_types=True)
    render_symbol_svg(symbol, view_box=WINDOW, font=font, style=style)
    font.text_strokes.assert_not_called()
    render_symbol_svg(symbol, view_box=WINDOW, font=font, style=style, show_hidden_pins=True)
    assert font.text_strokes.call_args.args[0] == "Input"
    pin.visible = True
    pin.number = "1"
    pin.alternates = [PinAlternate(name="ALT", electrical_type="output", shape="clock")]
    schematic = schematic_with(pin, angle=90, mirror="x")
    schematic.symbols[0].pins = [PlacedSymbolPin(number="1", alternate="ALT")]
    before = schematic.model_dump()
    font.reset_mock()
    render_schematic_svg(
        schematic,
        view_box=WINDOW,
        font=font,
        style=SchematicStyle(symbol=style),
    )
    labels = [call.args[0] for call in font.text_strokes.call_args_list]
    assert "Output" in labels
    assert "Input" not in labels
    assert schematic.model_dump() == before
