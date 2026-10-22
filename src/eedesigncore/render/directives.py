"""Schematic net-class directive symbols and their saved fields."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import Stroke, TextAttributes
from eedesigncore.render.geometry import SvgShape
from eedesigncore.render.text_boxes import text_pen
from eedesigncore.sch.models import Label

if TYPE_CHECKING:
    from eedesigncore.render.symbols import _SymbolDrawing


def draw_directive(drawing: _SymbolDrawing, label: Label, color: str) -> None:
    from eedesigncore.render.symbols import _color

    if label.angle % 90:
        raise ValueError("Directive angles must be multiples of 90 degrees.")
    attrs = label.attributes or TextAttributes()
    pen = text_pen(attrs, drawing.style.line_width_mm)
    color, opacity = _color(attrs.color, color)
    size = 0.508
    length = 2.54 if label.length is None else label.length
    shape = label.shape or "round"
    spin = label.angle % 180
    if attrs.justify_h.value == "right":
        spin += 180
    radians = math.radians((spin + 180) % 360)

    def point(x: float, y: float) -> tuple[float, float]:
        return label.position[0] + x * math.cos(radians) + y * math.sin(radians), label.position[
            1
        ] - x * math.sin(radians) + y * math.cos(radians)

    if shape in ("round", "dot"):
        drawing.measurement_role = "body:stem"
        radius = size * (0.7 if shape == "dot" else 1)
        start, end = point(0, 0), point(0, length - radius)
        drawing.element(
            SvgShape("path", (("d", f"M{drawing.point(start)} L{drawing.point(end)}"),)),
            color=color,
            opacity=opacity,
            stroke=Stroke(width=pen),
        )
        center = point(0, length)
        drawing.measurement_role = "body:flag"
        drawing.element(
            SvgShape(
                "circle",
                (
                    ("cx", drawing.number(center[0])),
                    ("cy", drawing.number(center[1])),
                    ("r", drawing.number(radius)),
                ),
            ),
            color=color,
            opacity=opacity,
            stroke=Stroke(width=-1 if shape == "dot" else pen),
            fill=color if shape == "dot" else "none",
        )
    else:
        if shape == "diamond":
            points = [
                (0, 0),
                (0, length - size),
                (-2 * size, length),
                (0, length + size),
                (2 * size, length),
                (0, length - size),
                (0, 0),
            ]
        elif shape == "rectangle":
            size *= 0.8
            points = [
                (0, 0),
                (0, length - size),
                (-2 * size, length - size),
                (-2 * size, length + size),
                (2 * size, length + size),
                (2 * size, length - size),
                (0, length - size),
                (0, 0),
            ]
        else:
            raise ValueError(f"Unknown directive shape: {shape}.")
        data = " ".join(
            f"{'M' if index == 0 else 'L'}{drawing.point(point(*p))}"
            for index, p in enumerate(points)
        )
        drawing.element(
            SvgShape("path", (("d", data),)),
            color=color,
            opacity=opacity,
            stroke=Stroke(width=pen),
        )
    drawing.measurement_role = "body"
    for field_index, field in enumerate(label.fields):
        if field.visible and not field.private:
            previous_role = drawing.measurement_role
            drawing.measurement_role = f"field:{field_index}:{field.name}"
            value = f"{field.name}: {field.value}" if field.show_name else field.value
            drawing.text(
                unescape_string(value), field.position, field.angle, field.attributes, color
            )
            drawing.measurement_role = previous_role
