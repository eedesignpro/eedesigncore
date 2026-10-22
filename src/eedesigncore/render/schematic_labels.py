"""Local, global, and hierarchical schematic label drawing."""

from __future__ import annotations

import math
from typing import Literal, cast

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.font import default_font, effective_pen_width
from eedesigncore.common.models import JustifyH, Stroke, TextAttributes
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.geometry import SvgShape
from eedesigncore.render.label_geometry import global_label_outline
from eedesigncore.render.symbols import _color, _SymbolDrawing
from eedesigncore.sch.models import Label, LabelKind

_HIERARCHICAL = {
    "input": [(0, 0), (-1, -1), (-2, -1), (-2, 1), (-1, 1), (0, 0)],
    "output": [(-2, 0), (-1, 1), (0, 1), (0, -1), (-1, -1), (-2, 0)],
    "bidirectional": [(0, 0), (-1, -1), (-2, 0), (-1, 1), (0, 0)],
    "tri_state": [(0, 0), (-1, -1), (-2, 0), (-1, 1), (0, 0)],
    "passive": [(0, -1), (-2, -1), (-2, 1), (0, 1), (0, -1)],
}


def draw_label(
    drawing: _SymbolDrawing,
    label: Label,
    color: str,
    *,
    text_offset_ratio: float,
    label_size_ratio: float,
) -> None:
    if label.angle % 90:
        raise ValueError("Schematic label angles must be multiples of 90 degrees.")
    if label.kind == LabelKind.netclass_flag:
        from eedesigncore.render.directives import draw_directive

        draw_directive(drawing, label, color)
        return
    content = unescape_string(label.content)
    attrs = label.attributes or TextAttributes()
    width, height = attrs.font_size
    pen = to_mm(
        effective_pen_width(
            point_to_iu((attrs.font_thickness or 0, 0))[0],
            point_to_iu(attrs.font_size),
            bold=attrs.bold,
        )
    )
    angle = int(label.angle) % 180
    if attrs.justify_h == JustifyH.right:
        angle += 180
    horizontal = angle in (0, 180)
    dx, dy = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[angle]
    position = label.position
    offset = text_offset_ratio * height
    valign = "center"
    if label.kind == LabelKind.local:
        position = (
            position[0] - (0 if horizontal else offset + pen),
            position[1] - (offset + pen if horizontal else 0),
        )
        valign = "bottom"
    else:
        shape = label.shape or (
            "input" if label.kind == LabelKind.hierarchical else "bidirectional"
        )
        if shape not in _HIERARCHICAL:
            raise ValueError(f"Unknown schematic label shape: {shape}.")
        if label.kind == LabelKind.hierarchical:
            position = (position[0] + dx * (offset + width), position[1] + dy * (offset + width))
            points = [(x * height / 2, y * height / 2) for x, y in _HIERARCHICAL[shape]]
        else:
            margin = label_size_ratio * height
            text_width = _text_width(drawing, content, attrs, pen)
            incoming = shape in ("input", "bidirectional", "tri_state")
            # The existing transform below maps this anchor-left border onto the sheet.
            points = list(
                global_label_outline(
                    text_width_mm=text_width,
                    text_height_mm=height,
                    shape=cast(
                        Literal["input", "output", "bidirectional", "tri_state", "passive"], shape
                    ),
                    body_direction="left",
                    pen_width_mm=pen,
                    label_size_ratio=label_size_ratio,
                )
            )
            distance = margin + (height * 0.75 if incoming else 0)
            position = (
                position[0] + dx * distance + (0 if horizontal else height * 0.0715),
                position[1] + dy * distance + (height * 0.0715 if horizontal else 0),
            )
        radians = math.radians(angle - 180)
        cos, sin = math.cos(radians), math.sin(radians)
        outline = [
            (label.position[0] + x * cos + y * sin, label.position[1] - x * sin + y * cos)
            for x, y in points
        ]
        outline_color, opacity = _color(attrs.color, color)
        outline_width = to_mm(
            effective_pen_width(
                point_to_iu(
                    (attrs.font_thickness or (0 if attrs.bold else drawing.style.line_width_mm), 0)
                )[0],
                point_to_iu(attrs.font_size),
                bold=attrs.bold,
            )
        )
        data = " ".join(
            f"{'M' if index == 0 else 'L'}{drawing.point(point)}"
            for index, point in enumerate(outline)
        )
        drawing.element(
            SvgShape("path", (("d", data),)),
            color=outline_color,
            opacity=opacity,
            stroke=Stroke(width=outline_width),
        )
    drawing.text(
        content,
        position,
        0 if horizontal else 90,
        attrs,
        color,
        halign=attrs.justify_h.value if label.attributes else "left",
        valign=attrs.justify_v.value if label.attributes else valign,
    )
    for field_index, field in enumerate(label.fields):
        if field.visible and not field.private:
            previous_role = drawing.measurement_role
            drawing.measurement_role = f"{previous_role}:field:{field_index}:{field.name}"
            field_position = field.position
            if label.kind == LabelKind.global_:
                field_position = (
                    field.position[0] + position[0] - label.position[0],
                    field.position[1] + position[1] - label.position[1],
                )
            value = f"{field.name}: {field.value}" if field.show_name else field.value
            drawing.text(
                unescape_string(value), field_position, field.angle, field.attributes, color
            )
            drawing.measurement_role = previous_role


def _text_width(drawing: _SymbolDrawing, text: str, attrs: TextAttributes, pen: float) -> float:
    size = point_to_iu(attrs.font_size)
    thickness = point_to_iu((pen, 0))[0]
    font = drawing.font or default_font()
    measure = getattr(font, "text_box", None)
    if callable(measure):
        return to_mm(measure(text, size, thickness, bold=attrs.bold, italic=attrs.italic).width)
    runs = font.text_strokes(text, size, (0, 0), thickness, halign="left", italic=attrs.italic)
    xs = [point[0] for run in runs for point in run]
    return to_mm(max(xs) - min(xs)) + pen if xs else 0.0
