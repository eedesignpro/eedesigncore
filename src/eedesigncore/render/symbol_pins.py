"""Symbol pin graphics with editor clock proportions and optional type labels."""

from __future__ import annotations

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.render.symbols import _SymbolDrawing

Point = tuple[float, float]
_DIRECTIONS: dict[float, Point] = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}
_TYPE_NAMES = {
    PinElectricalType.input: "Input",
    PinElectricalType.output: "Output",
    PinElectricalType.bidirectional: "Bidirectional",
    PinElectricalType.tri_state: "Tri-state",
    PinElectricalType.passive: "Passive",
    PinElectricalType.free: "Free",
    PinElectricalType.unspecified: "Unspecified",
    PinElectricalType.power_in: "Power input",
    PinElectricalType.power_out: "Power output",
    PinElectricalType.open_collector: "Open collector",
    PinElectricalType.open_emitter: "Open emitter",
    PinElectricalType.no_connect: "Unconnected",
}


def draw_pin(drawing: _SymbolDrawing, symbol: Symbol, pin: Pin) -> None:
    """Draw a pin, its shape markers, and its visible labels."""
    angle = pin.angle % 360
    if angle not in _DIRECTIONS:
        raise ValueError("Symbol pin angles must be multiples of 90 degrees.")
    dx, dy = _DIRECTIONS[angle]
    outer = (pin.position[0], -pin.position[1])
    body = (outer[0] + dx * pin.length, outer[1] + dy * pin.length)
    internal = (pin.name_text_size or pin.number_text_size) / 2
    external = pin.number_text_size / 2
    color = drawing.style.pins

    def point(along: float = 0, across: float = 0) -> Point:
        return (
            body[0] + dx * along + (across if dy else 0),
            body[1] + dy * along + (across if dx else 0),
        )

    shape = pin.shape
    if shape in (PinShape.inverted, PinShape.inverted_clock):
        drawing.circle(point(-external), external, color)
        drawing.line([point(-2 * external), outer], color)
    elif shape == PinShape.edge_clock_high:
        drawing.line(
            [point(across=internal), point(-2 * internal), point(across=-internal)], color
        )
        drawing.line([point(-2 * internal), outer], color)
    else:
        drawing.line([body, outer], color)
    if shape in (PinShape.clock, PinShape.inverted_clock, PinShape.clock_low):
        depth = internal * (2 if drawing.style.clock_style == "plot" else 1)
        drawing.line([point(across=internal), point(depth), point(across=-internal)], color)
    if shape in (PinShape.input_low, PinShape.clock_low):
        drawing.line([point(-2 * external), point(-2 * external, -2 * external), body], color)
    elif shape == PinShape.output_low:
        drawing.line([point(across=-2 * external), point(-2 * external)], color)
    elif shape == PinShape.non_logic:
        drawing.line(
            [(body[0] - external, body[1] - external), (body[0] + external, body[1] + external)],
            color,
        )
        drawing.line(
            [(body[0] - external, body[1] + external), (body[0] + external, body[1] - external)],
            color,
        )
    if pin.electrical_type == PinElectricalType.no_connect:
        radius = 0.381
        drawing.line(
            [(outer[0] - radius, outer[1] - radius), (outer[0] + radius, outer[1] + radius)], color
        )
        drawing.line(
            [(outer[0] - radius, outer[1] + radius), (outer[0] + radius, outer[1] - radius)], color
        )
    _draw_pin_text(drawing, symbol, pin, outer, body, dx, dy)
    if drawing.style.show_pin_types:
        _draw_pin_type(drawing, pin, outer, dx, dy)


def _draw_pin_type(drawing: _SymbolDrawing, pin: Pin, outer: Point, dx: float, dy: float) -> None:
    size = max(pin.name_text_size * 0.75, 0.7)
    thickness = size / 8
    # KiCad PIN_LAYOUT_CACHE::GetPinElectricalTypeInfo, without dangling-pin clearance.
    offset = 0.1016 + thickness / 2 + 0.381
    drawing.text(
        _TYPE_NAMES[pin.electrical_type],
        (outer[0] - dx * offset, outer[1] - dy * offset),
        0.0 if dx else 90.0,
        TextAttributes(font_size=(size, size), font_thickness=thickness),
        drawing.style.pin_types,
        halign="right" if dx > 0 or dy < 0 else "left",
        valign="center",
    )


def _draw_pin_text(
    drawing: _SymbolDrawing,
    symbol: Symbol,
    pin: Pin,
    outer: Point,
    body: Point,
    dx: float,
    dy: float,
) -> None:
    name = unescape_string(pin.name) if symbol.show_pin_names and pin.name_text_size else ""
    number = pin.number if symbol.show_pin_numbers and pin.number_text_size else ""
    if name == "~":
        name = ""
    if number == "~":
        number = ""
    inside = symbol.pin_name_offset if symbol.pin_name_offset is not None else 0.508
    pen = drawing.style.line_width_mm
    offset = 0.1016 + pen
    midpoint = ((outer[0] + body[0]) / 2, (outer[1] + body[1]) / 2)
    text_angle = 0.0 if dx else 90.0

    def text(
        value: str,
        size: float,
        position: Point,
        color: str,
        halign: str = "center",
        valign: str = "bottom",
    ) -> None:
        if value:
            from eedesigncore.render.stacked_pins import draw_stacked_number

            if value == number and draw_stacked_number(
                drawing, value, size, pin.length, position, text_angle, valign, color
            ):
                return
            drawing.text(
                value,
                position,
                text_angle,
                TextAttributes(font_size=(size, size), font_thickness=pen),
                color,
                halign=halign,
                valign=valign,
            )

    if inside:
        position = (body[0] + dx * inside, body[1] + dy * inside)
        text(
            name,
            pin.name_text_size,
            position,
            drawing.style.pin_names,
            "left" if dx > 0 or dy < 0 else "right",
            "center",
        )
        position = (midpoint[0], body[1] - offset) if dx else (body[0] - offset, midpoint[1])
        text(number, pin.number_text_size, position, drawing.style.pin_numbers)
    else:
        above = (midpoint[0], body[1] - offset) if dx else (body[0] - offset, midpoint[1])
        below = (midpoint[0], body[1] + offset) if dx else (body[0] + offset, midpoint[1])
        text(name, pin.name_text_size, above, drawing.style.pin_names)
        text(
            number,
            pin.number_text_size,
            below if name else above,
            drawing.style.pin_numbers,
            valign="top" if name else "bottom",
        )
