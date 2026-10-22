"""Stacked pin number labels and their braces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from eedesigncore.common.font import default_font
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.transform import point_to_iu, to_mm

if TYPE_CHECKING:
    from eedesigncore.render.symbols import _SymbolDrawing


def draw_stacked_number(
    drawing: _SymbolDrawing,
    number: str,
    size: float,
    length: float,
    position: tuple[float, float],
    angle: float,
    valign: str,
    color: str,
) -> bool:
    if not number.startswith("[") or not number.endswith("]"):
        return False
    font = drawing.font or default_font()
    measure = getattr(font, "string_boundary_limits", None)
    if not callable(measure):
        measure_box = getattr(font, "text_box", None)
        if not callable(measure_box):
            raise ValueError("Stacked pin labels require font text_box measurements.")

        def width(text: str) -> float:
            assert callable(measure_box)
            return to_mm(
                measure_box(text, point_to_iu((size, size)), point_to_iu((size / 8, 0))[0]).width
            )
    else:

        def width(text: str) -> float:
            assert callable(measure)
            return to_mm(
                measure(text, point_to_iu((size, size)), point_to_iu((size / 8, 0))[0])[0]
            )

    if width(number) <= max(length, 1.27):
        return False
    lines = [line.strip() for line in number[1:-1].split(",")]
    if len(lines) < 2:
        return False
    pitch = size * 1.3
    span = (len(lines) - 1) * pitch
    x, y = position
    vertical = angle % 180 == 90
    for i, line in enumerate(lines):
        anchor = (x - span + i * pitch, y) if vertical else (x, y - span + i * pitch)
        drawing.text(
            line,
            anchor,
            angle,
            TextAttributes(font_size=(size, size), font_thickness=drawing.style.line_width_mm),
            color,
            halign="center",
            valign=valign,
        )
    brace_width = size / 3
    distance = max(width(line) for line in lines) / 2 + brace_width
    for sign in (-1, 1):
        if vertical:
            start = (x - span - 2 * brace_width, y + sign * distance)
            end = (x + brace_width, y + sign * distance)
        else:
            start = (x + sign * distance, y - span - 2 * brace_width)
            end = (x + sign * distance, y + brace_width)
        offset = sign * brace_width
        mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        points = [
            start,
            (
                start[0] + (0 if vertical else offset / 2),
                start[1] + (offset / 2 if vertical else 0),
            ),
            (mid[0] + (0 if vertical else offset), mid[1] + (offset if vertical else 0)),
            (end[0] + (0 if vertical else offset / 2), end[1] + (offset / 2 if vertical else 0)),
            end,
        ]
        drawing.line(points, color)
    return True
