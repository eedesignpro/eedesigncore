"""Text box layout from KiCad SCH_TEXTBOX, PCB_TEXTBOX, and FONT::LinebreakText."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.font import default_font, effective_pen_width
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.pcb_family.shapes import PcbTextBox
from eedesigncore.common.sch_family.shapes import SchRect
from eedesigncore.common.sch_family.text import SymTextBox
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.geometry import Point, PointMap, SvgShape, format_mm, text_shapes

if TYPE_CHECKING:
    from eedesigncore.render.symbols import _SymbolDrawing
    from eedesigncore.sch.models import SchTextBox


def text_pen(attributes: TextAttributes, default: float = 0.0) -> float:
    thickness = attributes.font_thickness or (0.0 if attributes.bold else default)
    return to_mm(
        effective_pen_width(
            point_to_iu((thickness, 0))[0], point_to_iu(attributes.font_size), bold=attributes.bold
        )
    )


def wrapped_lines(
    content: str, width: float, attributes: TextAttributes, pen: float, font: object | None
) -> list[str]:
    metrics = font if font is not None else default_font()
    advance = getattr(metrics, "advance", None)
    if not callable(advance):
        raise ValueError("Text box fonts must supply advance(text, size) in integer nanometers.")
    if not callable(getattr(metrics, "text_strokes", None)):
        raise ValueError("Text box fonts must supply text_strokes for visible text.")
    select = getattr(metrics, "for_style", None)
    if callable(select):
        metrics = select(attributes.bold, attributes.italic)
        advance = getattr(metrics, "advance", None)
    if not callable(advance):
        raise ValueError("The selected font style must supply advance(text, size).")
    size = point_to_iu(attributes.font_size)
    limit = point_to_iu((width - pen, 0))[0]
    space = int(advance(" ", size))
    result: list[str] = []
    paragraphs = unescape_string(content).split("\n")
    if paragraphs[-1] == "":
        paragraphs.pop()
    for paragraph in paragraphs:
        line = ""
        length = 0
        pending = ""
        bury = False
        from eedesigncore.common.font.markup import words

        for word in words(paragraph):
            word_width = int(advance(word.rstrip() or word, size))
            if length + len(pending) * space + word_width > limit and pending:
                result.append(line)
                line, length, pending, bury = "", 0, "", True
            if word == " ":
                pending += word
            else:
                if not bury:
                    line += pending
                    length += len(pending) * space
                bury = False
                line += word[:-1] if word.endswith(" ") else word
                pending = " " if word.endswith(" ") else ""
                length += word_width
        result.append(line)
    return result


def line_positions(
    lines: list[str],
    anchor: Point,
    angle: float,
    attributes: TextAttributes,
    font: object | None = None,
) -> list[Point]:
    pitch = to_mm(point_to_iu((attributes.font_size[1] * 1.68 * 0.9583, 0))[0])
    select = getattr(font, "for_style", None)
    metrics = select(attributes.bold, attributes.italic) if callable(select) else font
    get_pitch = getattr(metrics, "line_pitch", None)
    if callable(get_pitch):
        pitch = float(get_pitch(attributes.font_size[1]))
    shift = {"top": 0.0, "center": 0.5, "bottom": 1.0}[attributes.justify_v.value]
    radians = math.radians(angle)
    return [
        (
            anchor[0] + (i - (len(lines) - 1) * shift) * pitch * math.sin(radians),
            anchor[1] + (i - (len(lines) - 1) * shift) * pitch * math.cos(radians),
        )
        for i in range(len(lines))
    ]


def draw_symbol_text_box(
    drawing: _SymbolDrawing, item: SymTextBox | SchTextBox, start: Point, end: Point, angle: float
) -> None:
    drawing.graphic(
        SchRect(
            start=(start[0], -start[1]), end=(end[0], -end[1]), stroke=item.stroke, fill=item.fill
        )
    )
    attrs = item.attributes or TextAttributes()
    if not attrs.visible or not item.content:
        return
    left, right = sorted((start[0], end[0]))
    top, bottom = sorted((start[1], end[1]))
    ml, mt, mr, mb = item.margins
    h, v = attrs.justify_h.value, attrs.justify_v.value
    if angle % 180 == 90:
        anchor = (
            {"top": left + ml, "center": (left + right) / 2, "bottom": right - mr}[v],
            {"left": bottom - mb, "center": (top + bottom) / 2, "right": top + mt}[h],
        )
        width = bottom - top - mt - mb
    else:
        anchor = (
            {"left": left + ml, "center": (left + right) / 2, "right": right - mr}[h],
            {"top": top + mt, "center": (top + bottom) / 2, "bottom": bottom - mb}[v],
        )
        width = right - left - ml - mr
    lines = wrapped_lines(
        item.content, width, attrs, text_pen(attrs, drawing.style.line_width_mm), drawing.font
    )
    for line, position in zip(
        lines, line_positions(lines, anchor, angle, attrs, drawing.font), strict=True
    ):
        drawing.text(line, position, angle, attrs, drawing.style.body)


def pcb_text_box_shapes(
    item: PcbTextBox, to_board: PointMap, precision: int, font: object | None
) -> list[SvgShape] | None:
    if item.start is not None and item.end is not None:
        left, right = sorted((item.start[0], item.end[0]))
        top, bottom = sorted((item.start[1], item.end[1]))
        corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    else:
        corners = [p for p in item.pts if isinstance(p, tuple)]
        if len(corners) != 4 or len(item.pts) != 4:
            return None
    angle = (item.angle or 0.0) % 360
    if angle % 90 == 0:
        left, right = min(p[0] for p in corners), max(p[0] for p in corners)
        top, bottom = min(p[1] for p in corners), max(p[1] for p in corners)
        corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
        shift = (-int(angle / 90)) % 4
        corners = corners[shift:] + corners[:shift]
    else:
        min_x, max_x = min(corners, key=lambda p: p[0]), max(corners, key=lambda p: p[0])
        min_y, max_y = min(corners, key=lambda p: p[1]), max(corners, key=lambda p: p[1])
        corners = [min_x, min_y, max_x, max_y]
        shift = (-int(angle / 90)) % 4
        corners = corners[shift:] + corners[:shift]
    shapes: list[SvgShape] = []
    if item.border_enabled:
        points = [to_board(p) for p in corners]
        data = (
            " ".join(
                f"{'M' if i == 0 else 'L'}{format_mm(p[0], precision)},"
                f"{format_mm(p[1], precision)}"
                for i, p in enumerate(points)
            )
            + " Z"
        )
        shapes.append(SvgShape("path", (("d", data),)))
    attrs = item.attributes or TextAttributes()
    if not attrs.visible or not item.content:
        return shapes
    from eedesigncore.render.fonts import OutlineFont

    h = {"left": 0.0, "center": 0.5, "right": 1.0}[attrs.justify_h.value]
    v = {"top": 0.0, "center": 0.5, "bottom": 1.0}[attrs.justify_v.value]
    if attrs.mirror:
        h = 1 - h
    ml, mt, mr, mb = item.margins
    dx = ml if h == 0 else -mr if h == 1 else 0
    dy = mt if v == 0 else -mb if v == 1 else 0
    radians = math.radians(angle)
    anchor = (
        corners[0][0]
        + h * (corners[1][0] - corners[0][0])
        + v * (corners[3][0] - corners[0][0])
        + dx * math.cos(radians)
        + dy * math.sin(radians),
        corners[0][1]
        + h * (corners[1][1] - corners[0][1])
        + v * (corners[3][1] - corners[0][1])
        - dx * math.sin(radians)
        + dy * math.cos(radians),
    )
    width = math.dist(corners[0], corners[1]) - (ml + mr if angle % 180 == 0 else mt + mb)
    pen = text_pen(attrs)
    lines = wrapped_lines(item.content, width, attrs, attrs.font_thickness or 0.0, font)
    attrs = attrs.model_copy(update={"font_thickness": pen})
    border_count = len(shapes)
    for line, position in zip(
        lines, line_positions(lines, anchor, angle, attrs, font), strict=True
    ):
        shapes.extend(
            replace(shape, stroke_width_mm=pen)
            for shape in text_shapes(line, position, angle, attrs, to_board, precision, font)
        )
    if item.knockout:
        import pyclipper  # type: ignore[import-untyped]

        from eedesigncore.render.filled_geometry import (
            difference,
            filled_polygons,
            polygon_shape,
            stroke_polygons,
        )
        from eedesigncore.render.traversal import identity_map

        polygon = [point_to_iu(to_board(point)) for point in corners]
        background = [polygon]
        if item.border_enabled and item.stroke.width > 0:
            offset = pyclipper.PyclipperOffset(arc_tolerance=500)
            offset.AddPath(polygon, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
            background = offset.Execute(point_to_iu((item.stroke.width / 2, 0))[0])
        shapes = [
            polygon_shape(
                difference(
                    background,
                    filled_polygons(shapes[border_count:])
                    if isinstance(font, OutlineFont)
                    else stroke_polygons(shapes[border_count:]),
                ),
                identity_map,
                precision,
            )
        ] + shapes[:border_count]
    return shapes
