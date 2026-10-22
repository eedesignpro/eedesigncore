"""One loaded schematic sheet to SVG, without following file references."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import Fill, FillType, JustifyH, Stroke, TextAttributes
from eedesigncore.common.sch_family.shapes import SchRect
from eedesigncore.common.transform import point_to_iu
from eedesigncore.render.document import ViewBox, build_document
from eedesigncore.render.geometry import DEFAULT_PRECISION, SvgShape
from eedesigncore.render.images import image_shape
from eedesigncore.render.schematic_labels import draw_label
from eedesigncore.render.schematic_metadata import DrawingMetadata
from eedesigncore.render.schematic_symbols import draw_placed_symbol, map_graphic
from eedesigncore.render.symbols import SymbolFont, SymbolStyle, _color, _SymbolDrawing
from eedesigncore.render.tables import draw_schematic_table
from eedesigncore.render.text_boxes import draw_symbol_text_box
from eedesigncore.render.traversal import identity_map
from eedesigncore.sch.models import Label, LabelKind, LineType, Schematic
from eedesigncore.text_resolution import TextResolutionContext, resolve_document_text


@dataclass(frozen=True)
class SchematicStyle:
    """Schematic colors and drawing defaults, in millimeters."""

    symbol: SymbolStyle = field(default_factory=SymbolStyle)
    wire: str = "#008400"
    bus: str = "#000084"
    junction: str = "#008400"
    no_connect: str = "#000084"
    text: str = "#006484"
    graphics: str = "#840000"
    local_label: str = "#000000"
    netclass_flag: str = "#484848"
    global_label: str = "#840000"
    hierarchical_label: str = "#848400"
    sheet: str = "#840084"
    dnp: str = "#ff0000"
    sheet_background: str = "none"
    wire_width_mm: float = 0.1524
    bus_width_mm: float = 0.3048
    junction_diameter_mm: float = 0.9144
    no_connect_size_mm: float = 1.2192
    text_offset_ratio: float = 0.15
    label_size_ratio: float = 0.375

    def __post_init__(self) -> None:
        for name in (
            "wire_width_mm",
            "bus_width_mm",
            "junction_diameter_mm",
            "no_connect_size_mm",
            "text_offset_ratio",
            "label_size_ratio",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative.")


_DEFAULT_STYLE = SchematicStyle()


def _render_schematic_svg(
    schematic: Schematic,
    *,
    view_box: ViewBox,
    style: SchematicStyle = _DEFAULT_STYLE,
    font: SymbolFont | None = None,
    show_hidden_pins: bool = False,
    precision: int = DEFAULT_PRECISION,
    symbol_ids: dict[int, str] | None = None,
    sheet_ids: dict[int, str] | None = None,
    text_context: TextResolutionContext | None = None,
    metadata: DrawingMetadata | None = None,
) -> str:
    """Return SVG text for one loaded schematic sheet without changing it.

    The supplied window uses millimeters with Y downward. Embedded symbol
    definitions supply placed graphics. Placed fields already use sheet
    coordinates and are not transformed again. Font control covers all text.

    No files, project settings, or child sheets are loaded. Stored fields and
    labels remain literal unless *text_context* supplies resolution inputs.
    Drawing sheets and automatic fitting are outside this operation. Unsupported
    visible item categories raise ValueError instead of disappearing.
    """
    if text_context is not None:
        schematic = resolve_document_text(schematic, text_context).document
    definitions = {symbol.name: symbol for symbol in schematic.lib_symbols}
    if len(definitions) != len(schematic.lib_symbols):
        raise ValueError("The schematic contains duplicate embedded symbol names.")
    drawing = _SymbolDrawing(style.symbol, precision, font)
    if metadata is not None:
        drawing.measured_bounds = []
        drawing.measured_parts = metadata.parts
    graphics = _SymbolDrawing(replace(style.symbol, body=style.graphics), precision, font)
    graphics.measured_bounds = drawing.measured_bounds
    graphics.measured_parts = drawing.measured_parts
    for image in schematic.images:
        drawing.element(
            image_shape(image.data, image.position, image.scale, identity_map, precision),
            color="none",
            background=True,
        )
    for area in schematic.rule_areas:
        points = list(area.shape.pts)
        if points and points[0] != points[-1]:
            points.append(points[0])
        graphics.graphic(
            map_graphic(area.shape.model_copy(update={"pts": points}), lambda p: (p[0], -p[1]))
        )
    for item in schematic.graphic_shapes:
        graphics.graphic(map_graphic(item, lambda p: (p[0], -p[1])))
    drawing.background.extend(graphics.background)
    drawing.foreground.extend(graphics.foreground)
    text_boxes = _SymbolDrawing(replace(style.symbol, body=style.text), precision, font)
    text_boxes.measured_bounds = drawing.measured_bounds
    text_boxes.measured_parts = drawing.measured_parts
    for text_box in schematic.text_boxes:
        draw_symbol_text_box(
            text_boxes,
            text_box,
            text_box.position,
            (text_box.position[0] + text_box.size[0], text_box.position[1] + text_box.size[1]),
            text_box.angle,
        )
    for table in schematic.tables:
        draw_schematic_table(text_boxes, table)
    drawing.background.extend(text_boxes.background)
    drawing.foreground.extend(text_boxes.foreground)
    for sheet_index, sheet in enumerate(schematic.sheets):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        foreground_start, background_start = len(drawing.foreground), len(drawing.background)
        box = _SymbolDrawing(
            replace(style.symbol, body=style.sheet, background=style.sheet_background),
            precision,
            font,
        )
        if metadata is not None:
            box.measured_bounds = drawing.measured_bounds
            box.measured_parts = drawing.measured_parts
        box.measurement_role = "container"
        x, y = sheet.position
        width, height = sheet.size
        box.graphic(
            SchRect(
                start=(x, -y),
                end=(x + width, -y - height),
                stroke=sheet.stroke,
                fill=Fill(type=FillType.color, color=sheet.fill_color)
                if sheet.fill_color.a
                else Fill(type=FillType.background),
            )
        )
        drawing.background.extend(box.background)
        drawing.foreground.extend(box.foreground)
        for field_index, prop in enumerate(sheet.fields):
            if prop.visible:
                drawing.measurement_role = f"field:{field_index}:{prop.name}"
                drawing.text(
                    unescape_string(
                        f"{prop.name}: {prop.value}"
                        if prop.show_name
                        else f"File: {prop.value}"
                        if prop.name == "Sheetfile"
                        else prop.value
                    ),
                    prop.position,
                    prop.angle,
                    prop.attributes,
                    style.sheet,
                )
                drawing.measurement_role = "body"
        for pin_index, pin in enumerate(sheet.pins):
            drawing.measurement_role = f"pin:{pin_index}:{pin.name}"
            pin_start = len(drawing.foreground), len(drawing.background)
            pin_box_start = len(drawing.measured_bounds or [])
            shape = {"input": "output", "output": "input"}.get(pin.shape, pin.shape)
            draw_label(
                drawing,
                Label(
                    kind=LabelKind.hierarchical,
                    content=pin.name,
                    shape=shape,
                    position=pin.position,
                    angle=(pin.angle + 180) % 360,
                    attributes=pin.attributes
                    or TextAttributes(
                        justify_h=JustifyH.right if pin.angle in (0, 90) else JustifyH.left
                    ),
                ),
                style.sheet,
                text_offset_ratio=style.text_offset_ratio,
                label_size_ratio=style.label_size_ratio,
            )
            drawing.measurement_role = "body"
            if metadata is not None:
                key = f"sheet_pin:{sheet_index}:{pin_index}"
                metadata.group(key, drawing.foreground, drawing.background, pin_start)
                metadata.record(key, (drawing.measured_bounds or [])[pin_box_start:])
        if sheet.dnp:
            from eedesigncore.render.dnp import bounds, dim_elements, draw_mark, text_bounds

            drawing.foreground[foreground_start:] = dim_elements(
                drawing.foreground[foreground_start:]
            )
            drawing.background[background_start:] = dim_elements(
                drawing.background[background_start:]
            )
            body = bounds(
                [(x, y), (x + width, y + height)],
                max(0, sheet.stroke.width or style.symbol.line_width_mm) / 2,
            )
            whole = body
            for prop in sheet.fields:
                whole = whole.merged(
                    text_bounds(
                        f"File: {prop.value}" if prop.name == "Sheetfile" else prop.value,
                        prop.position,
                        prop.angle,
                        prop.attributes or TextAttributes(),
                        font,
                    )
                )
            draw_mark(drawing, body, whole, style.dnp)
        if sheet_ids is not None and sheet_index in sheet_ids:
            from eedesigncore.render.document import escape_attr

            identity = escape_attr(sheet_ids[sheet_index])
            for elements, start in (
                (drawing.foreground, foreground_start),
                (drawing.background, background_start),
            ):
                if len(elements) > start:
                    elements[start:] = [
                        f'<g data-item-id="{identity}">' + "".join(elements[start:]) + "</g>"
                    ]
        if metadata is not None:
            metadata.group(
                f"sheet:{sheet_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(
                f"sheet:{sheet_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    for symbol_index, placed in enumerate(schematic.symbols):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        foreground_start = len(drawing.foreground)
        background_start = len(drawing.background)
        draw_placed_symbol(
            drawing,
            placed,
            definitions,
            show_hidden_pins=show_hidden_pins,
            dnp_color=style.dnp,
            metadata=metadata,
            symbol_index=symbol_index,
        )
        if symbol_ids is not None and symbol_index in symbol_ids:
            from eedesigncore.render.document import escape_attr

            identity = escape_attr(symbol_ids[symbol_index])
            for elements, start in (
                (drawing.foreground, foreground_start),
                (drawing.background, background_start),
            ):
                if len(elements) > start:
                    elements[start:] = [
                        f'<g data-item-id="{identity}">' + "".join(elements[start:]) + "</g>"
                    ]
        if metadata is not None:
            metadata.group(
                f"symbol:{symbol_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(
                f"symbol:{symbol_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    for line_index, line in enumerate(schematic.lines):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        bus = line.line_type == LineType.bus
        color, opacity = _color(
            line.stroke.color,
            style.bus
            if bus
            else style.graphics
            if line.line_type == LineType.polyline
            else style.wire,
        )
        width = line.stroke.width or (
            style.bus_width_mm
            if bus
            else style.symbol.line_width_mm
            if line.line_type == LineType.polyline
            else style.wire_width_mm
        )
        drawing.element(
            SvgShape("path", (("d", f"M{drawing.point(line.start)} L{drawing.point(line.end)}"),)),
            color=color,
            opacity=opacity,
            stroke=line.stroke.model_copy(update={"width": width}),
        )
        if metadata is not None:
            metadata.group(
                f"line:{line_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(f"line:{line_index}", (drawing.measured_bounds or [])[measured_start:])

    for entry_index, entry in enumerate(schematic.bus_entries):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        x, y = entry.position
        end = x + entry.size[0], y + entry.size[1]
        color, opacity = _color(entry.stroke.color, style.wire)
        drawing.element(
            SvgShape("path", (("d", f"M{drawing.point(entry.position)} L{drawing.point(end)}"),)),
            color=color,
            opacity=opacity,
            stroke=entry.stroke.model_copy(
                update={"width": entry.stroke.width or style.wire_width_mm}
            ),
        )
        if metadata is not None:
            metadata.group(
                f"bus_entry:{entry_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(
                f"bus_entry:{entry_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    for junction_index, junction in enumerate(schematic.junctions):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        diameter = junction.diameter or style.junction_diameter_mm
        color, opacity = _color(junction.color if junction.color.a else None, style.junction)
        drawing.element(
            SvgShape(
                "circle",
                (
                    ("cx", drawing.number(junction.position[0])),
                    ("cy", drawing.number(junction.position[1])),
                    ("r", drawing.number(diameter / 2)),
                ),
            ),
            color="none",
            fill=color,
            opacity=opacity,
            stroke=Stroke(width=-1),
        )
        if metadata is not None:
            metadata.group(
                f"junction:{junction_index}",
                drawing.foreground,
                drawing.background,
                metadata_start,
            )
            metadata.record(
                f"junction:{junction_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    for mark_index, mark in enumerate(schematic.no_connects):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        x, y = mark.position
        half = style.no_connect_size_mm / 2
        drawing.line([(x - half, y - half), (x + half, y + half)], style.no_connect)
        drawing.line([(x - half, y + half), (x + half, y - half)], style.no_connect)
        if metadata is not None:
            metadata.group(
                f"no_connect:{mark_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(
                f"no_connect:{mark_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    for text in schematic.texts:
        attrs = text.attributes or TextAttributes()
        lines = unescape_string(text.content).split("\n")
        pitch = attrs.font_size[1] * 1.68 * 0.9583
        from eedesigncore.render.fonts import OutlineFont

        outline_adjust = 0.0
        if isinstance(font, OutlineFont):
            selected_font = font.for_style(attrs.bold, attrs.italic)
            pitch = selected_font.line_pitch(attrs.font_size[1])
            outline_adjust = (
                selected_font.text_box(
                    lines[0],
                    point_to_iu(attrs.font_size),
                    0,
                    bold=attrs.bold,
                    italic=attrs.italic,
                ).height
                / 1e6
                - attrs.font_size[1]
            ) * 0.4
        shift = {"top": 0.0, "center": 0.5, "bottom": 1.0}[attrs.justify_v.value]
        angle = text.angle % 180
        radians = math.radians(angle)
        for index, content in enumerate(lines):
            offset = (index - (len(lines) - 1) * shift) * pitch
            position = (
                text.position[0] + (offset - outline_adjust) * math.sin(radians),
                text.position[1] + (offset - outline_adjust) * math.cos(radians) - 0.25,
            )
            drawing.text(content, position, angle, attrs, style.text)
    colors = {
        LabelKind.local: style.local_label,
        LabelKind.global_: style.global_label,
        LabelKind.hierarchical: style.hierarchical_label,
        LabelKind.netclass_flag: style.netclass_flag,
    }
    for label_index, label in enumerate(schematic.labels):
        metadata_start = len(drawing.foreground), len(drawing.background)
        measured_start = len(drawing.measured_bounds or [])
        draw_label(
            drawing,
            label,
            colors[label.kind],
            text_offset_ratio=style.text_offset_ratio,
            label_size_ratio=style.label_size_ratio,
        )
        if metadata is not None:
            metadata.group(
                f"label:{label_index}", drawing.foreground, drawing.background, metadata_start
            )
            metadata.record(
                f"label:{label_index}", (drawing.measured_bounds or [])[measured_start:]
            )

    return build_document(view_box, drawing.background + drawing.foreground, precision)


__all__ = ["SchematicStyle", "render_schematic_svg"]


def render_schematic_svg(
    schematic: Schematic,
    *,
    view_box: ViewBox,
    style: SchematicStyle = _DEFAULT_STYLE,
    font: SymbolFont | None = None,
    show_hidden_pins: bool = False,
    precision: int = DEFAULT_PRECISION,
    symbol_ids: dict[int, str] | None = None,
    sheet_ids: dict[int, str] | None = None,
    text_context: TextResolutionContext | None = None,
) -> str:
    """Draw one loaded sheet in millimeters, with X rightward and Y downward."""
    return _render_schematic_svg(
        schematic,
        view_box=view_box,
        style=style,
        font=font,
        show_hidden_pins=show_hidden_pins,
        precision=precision,
        symbol_ids=symbol_ids,
        sheet_ids=sheet_ids,
        text_context=text_context,
    )
