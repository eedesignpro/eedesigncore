"""Place symbol graphics without rotating or mirroring readable labels."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.font import default_font, effective_pen_width
from eedesigncore.common.models import JustifyH, JustifyV, TextAttributes
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition as _resolve_symbol
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.connectivity.geometry import Transform, apply, symbol_transform
from eedesigncore.render.schematic_metadata import DrawingMetadata
from eedesigncore.render.symbol_pins import draw_pin
from eedesigncore.render.symbols import Graphic, _SymbolDrawing
from eedesigncore.sch.models import PlacedSymbol

Point = tuple[float, float]


def map_graphic(item: Graphic, point: Callable[[Point], Point]) -> Graphic:
    """Copy a graphic into the symbol renderer's upward-Y coordinates."""
    if isinstance(item, SchCircle):
        return item.model_copy(update={"center": point(item.center)})
    if isinstance(item, SchArc):
        return item.model_copy(
            update={
                "start": point(item.start),
                "mid": point(item.mid),
                "end": point(item.end),
            }
        )
    if isinstance(item, SchRect):
        return item.model_copy(update={"start": point(item.start), "end": point(item.end)})
    return item.model_copy(update={"pts": [point(p) for p in item.pts]})


def _text_orientation(
    angle: float,
    attributes: TextAttributes | None,
    transform: Transform,
) -> tuple[float, TextAttributes]:
    radians = math.radians(angle)
    baseline = apply(transform, (math.cos(radians), -math.sin(radians)))
    vertical = abs(baseline[1]) > abs(baseline[0])
    attrs = (attributes or TextAttributes()).model_copy()
    reverse_h = baseline[1] > 0 if vertical else baseline[0] < 0
    determinant = transform[0] * transform[3] - transform[1] * transform[2]
    reverse_v = determinant < 0 and ((angle % 180 == 0) == (transform[0] > 0))
    if reverse_h:
        attrs.justify_h = {JustifyH.left: JustifyH.right, JustifyH.right: JustifyH.left}.get(
            attrs.justify_h,
            attrs.justify_h,
        )
    if reverse_v:
        attrs.justify_v = {JustifyV.top: JustifyV.bottom, JustifyV.bottom: JustifyV.top}.get(
            attrs.justify_v,
            attrs.justify_v,
        )
    return (90.0 if vertical else 0.0), attrs


def draw_placed_symbol(
    drawing: _SymbolDrawing,
    placed: PlacedSymbol,
    definitions: Mapping[str, Symbol],
    *,
    show_hidden_pins: bool,
    dnp_color: str = "#ff0000",
    metadata: DrawingMetadata | None = None,
    symbol_index: int = 0,
) -> None:
    foreground_start, background_start = len(drawing.foreground), len(drawing.background)
    definition = definitions.get(placed.lib_name or "") or definitions.get(placed.lib_id)
    if definition is None:
        raise ValueError(f"The schematic has no embedded symbol definition for {placed.lib_id}.")
    source, _ = _resolve_symbol(definition, definitions)
    if placed.angle % 90 or placed.mirror not in (None, "x", "y"):
        raise ValueError("A schematic symbol needs a cardinal angle and a valid mirror axis.")
    transform = symbol_transform(placed.angle, placed.mirror)

    def sheet(point: Point) -> Point:
        x, y = apply(transform, (point[0], -point[1]))
        return x + placed.position[0], y + placed.position[1]

    def graphic_point(point: Point) -> Point:
        x, y = sheet(point)
        return x, -y

    units = {entry.unit_index for entry in source.units if entry.unit_index}
    bodies = {entry.body_style_index for entry in source.units if entry.body_style_index}
    if placed.unit not in (units or {1}) or placed.body_style not in (bodies or {1}):
        raise ValueError("The selected symbol unit or body style is absent.")
    selected = [
        entry
        for entry in source.units
        if entry.unit_index in (0, placed.unit)
        and entry.body_style_index in (0, placed.body_style)
    ]
    if not selected and source.units:
        raise ValueError(f"The selected symbol unit or body style is absent: {placed.lib_id}.")
    alternates = {pin.number: pin.alternate for pin in placed.pins if pin.alternate}
    pin_index = -1
    body_boxes = []
    for entry in selected:
        for item_index, item in enumerate(entry.items):
            drawing.measurement_role = (
                f"body:{entry.unit_index}:{entry.body_style_index}:{item_index}"
            )
            if isinstance(item, Pin):
                pin_index += 1
            item_start = len(drawing.foreground), len(drawing.background)
            box_start = len(drawing.measured_bounds or [])
            if getattr(item, "private", False):
                continue
            if isinstance(item, SchArc | SchBezier | SchCircle | SchPolyline | SchRect):
                drawing.graphic(map_graphic(item, graphic_point))
            elif isinstance(item, Pin):
                if not item.visible and not show_hidden_pins:
                    continue
                radians = math.radians(item.angle)
                direction = apply(transform, (math.cos(radians), -math.sin(radians)))
                angle = round(math.degrees(math.atan2(-direction[1], direction[0]))) % 360
                pin = item.model_copy(
                    update={"position": graphic_point(item.position), "angle": angle}
                )
                if item.number in alternates:
                    name = alternates[item.number]
                    alternate = next((a for a in item.alternates if a.name == name), None)
                    if alternate is None:
                        raise ValueError(f"The selected alternate pin is absent: {name}.")
                    pin.name, pin.shape, pin.electrical_type = (
                        alternate.name,
                        alternate.shape,
                        alternate.electrical_type,
                    )
                drawing.measurement_role = f"pin:{pin_index}:{item.number}"
                draw_pin(drawing, source, pin)
                drawing.measurement_role = "body"
                if metadata is not None:
                    key = f"pin:{symbol_index}:{pin_index}"
                    metadata.group(key, drawing.foreground, drawing.background, item_start)
                    metadata.record(key, (drawing.measured_bounds or [])[box_start:])
            elif isinstance(item, SymTextBox):
                from eedesigncore.render.text_boxes import draw_symbol_text_box

                box_angle = (90.0 - item.angle) if transform[1] else item.angle
                draw_symbol_text_box(
                    drawing,
                    item,
                    sheet(item.position),
                    sheet((item.position[0] + item.size[0], item.position[1] + item.size[1])),
                    box_angle,
                )
            elif isinstance(item, SymText):
                text_angle, attrs = _text_orientation(
                    item.angle_degrees, item.attributes, transform
                )
                drawing.text(
                    unescape_string(item.content),
                    sheet(item.position),
                    text_angle,
                    attrs,
                    drawing.style.text,
                )
            else:
                raise ValueError(f"Schematic SVG does not support symbol {type(item).__name__}.")
            if not isinstance(item, Pin):
                body_boxes.extend((drawing.measured_bounds or [])[box_start:])
    if metadata is not None:
        metadata.record(f"symbol:{symbol_index}", body_boxes, body=True)
    drawing.measurement_role = "body"
    for field_index, field in enumerate(placed.fields):
        if field.visible and not field.private:
            drawing.measurement_role = f"field:{field_index}:{field.name}"
            value = unescape_string(
                f"{field.name}: {field.value}" if field.show_name else field.value
            )
            attrs = field.attributes or TextAttributes()
            center = _field_center(drawing, value, attrs)
            radians = math.radians(field.angle)
            offset = apply(
                transform,
                (
                    center[0] * math.cos(radians) + center[1] * math.sin(radians),
                    -center[0] * math.sin(radians) + center[1] * math.cos(radians),
                ),
            )
            drawing.text(
                value,
                (field.position[0] + offset[0], field.position[1] + offset[1]),
                (field.angle + (90 if transform[1] else 0)) % 180,
                attrs,
                drawing.style.fields,
                halign="center",
                valign="center",
            )
            drawing.measurement_role = "body"

    if placed.dnp:
        from eedesigncore.render.dnp import dim_elements, draw_mark, symbol_bounds

        drawing.foreground[foreground_start:] = dim_elements(drawing.foreground[foreground_start:])
        drawing.background[background_start:] = dim_elements(drawing.background[background_start:])
        body, whole = symbol_bounds(
            [item for entry in selected for item in entry.items], sheet, drawing.font
        )
        draw_mark(drawing, body, whole, dnp_color)


def _field_center(drawing: _SymbolDrawing, text: str, attrs: TextAttributes) -> Point:
    if (
        attrs.justify_h == JustifyH.center
        and attrs.justify_v == JustifyV.center
        and not attrs.italic
    ):
        return (0.0, 0.0)
    font = drawing.font or default_font()
    size = point_to_iu(attrs.font_size)
    pen = effective_pen_width(
        point_to_iu((attrs.font_thickness or 0, 0))[0], size, bold=attrs.bold
    )
    measure = getattr(font, "text_box", None)
    if callable(measure):
        boxes = [
            measure(line, size, pen, bold=attrs.bold, italic=attrs.italic)
            for line in text.split("\n")
        ]
        width, height = max(to_mm(box.width) for box in boxes), to_mm(boxes[0].height)
    else:
        runs = font.text_strokes(text, size, (0, 0), pen, italic=attrs.italic)
        points = [p for run in runs for p in run]
        if not points:
            return (0.0, 0.0)
        width = to_mm(max(p[0] for p in points) - min(p[0] for p in points) + pen)
        height = to_mm(max(p[1] for p in points) - min(p[1] for p in points) + pen)
    italic = attrs.font_size[1] / 8 if attrs.italic else 0
    x = {
        JustifyH.left: width / 2,
        JustifyH.center: italic / 2,
        JustifyH.right: -width / 2 + italic,
    }[attrs.justify_h]
    if attrs.mirror and attrs.justify_h != JustifyH.center:
        x = width / 2 if attrs.justify_h == JustifyH.right else -width / 2 + italic
    fudge = height * 0.17 / 1.17
    height += (len(text.split("\n")) - 1) * attrs.font_size[1] * 1.68 * 0.9583
    y = {
        JustifyV.top: height / 2 - fudge,
        JustifyV.center: 0.0,
        JustifyV.bottom: -height / 2 + fudge,
    }[attrs.justify_v]
    return x, y
