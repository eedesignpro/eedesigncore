"""Do-not-populate shading and marks from saved schematic flags."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from eedesigncore.common.bbox import BoundingBox, arc_extreme_points_iu
from eedesigncore.common.font import default_font
from eedesigncore.common.models import Stroke, TextAttributes
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.geometry import SvgShape
from eedesigncore.render.text_boxes import text_pen

if TYPE_CHECKING:
    from eedesigncore.render.symbols import _SymbolDrawing

Point = tuple[float, float]


def bounds(points: Iterable[Point], pad: float = 0) -> BoundingBox:
    points = list(points)
    return BoundingBox(
        min_x=min(p[0] for p in points) - pad,
        min_y=min(p[1] for p in points) - pad,
        max_x=max(p[0] for p in points) + pad,
        max_y=max(p[1] for p in points) + pad,
    )


def dim_elements(elements: list[str]) -> list[str]:
    def dim(match: re.Match[str]) -> str:
        rgb = [int(match[1][i : i + 2], 16) for i in (0, 2, 4)]
        gray = int((min(rgb) + max(rgb)) / 4 + 127.5)
        return f'="#{gray:02x}{gray:02x}{gray:02x}"'

    return [re.sub(r'="#([0-9a-fA-F]{6})"', dim, element) for element in elements]


def draw_mark(drawing: _SymbolDrawing, body: BoundingBox, whole: BoundingBox, color: str) -> None:
    x = max(body.min_x - whole.min_x, whole.max_x - body.max_x)
    y = max(body.min_y - whole.min_y, whole.max_y - body.max_y)
    x = max(x * 0.6, y * 0.3)
    y = max(y * 0.6, x * 0.3)
    left, right, top, bottom = body.min_x - x, body.max_x + x, body.min_y - y, body.max_y + y
    for start, end in (((left, top), (right, bottom)), ((right, top), (left, bottom))):
        drawing.element(
            SvgShape("path", (("d", f"M{drawing.point(start)} L{drawing.point(end)}"),)),
            color=color,
            opacity=0.85,
            stroke=Stroke(width=0.4572),
        )


def text_bounds(
    content: str, position: Point, angle: float, attrs: TextAttributes, font: object | None
) -> BoundingBox:
    measure = getattr(font or default_font(), "text_box", None)
    size = point_to_iu(attrs.font_size)
    pen = point_to_iu((text_pen(attrs), 0))[0]
    if not callable(measure):
        raise ValueError("Do-not-populate marks require font text_box measurements.")
    box = measure(content, size, pen, italic=attrs.italic, bold=attrs.bold)
    width, height = to_mm(box.width), to_mm(box.height)
    left = {"left": 0, "center": -width / 2, "right": -width}[attrs.justify_h.value]
    fudge = height * 0.17 / 1.17
    top = {"top": -fudge, "center": -height / 2, "bottom": -height + fudge}[attrs.justify_v.value]
    radians = math.radians(angle)
    return bounds(
        (
            position[0] + x * math.cos(radians) + y * math.sin(radians),
            position[1] - x * math.sin(radians) + y * math.cos(radians),
        )
        for x, y in (
            (left, top),
            (left + width, top),
            (left + width, top + height),
            (left, top + height),
        )
    )


def symbol_bounds(
    items: list[object], transform: Callable[[Point], Point], font: object | None
) -> tuple[BoundingBox, BoundingBox]:
    body: BoundingBox | None = None
    full: BoundingBox | None = None
    for item in items:
        if getattr(item, "private", False):
            continue
        points: list[Point] = []
        pad = 0.0
        if isinstance(item, Pin):
            if not item.visible:
                continue
            radians = math.radians(item.angle)
            dx, dy = math.cos(radians), math.sin(radians)
            root = (item.position[0] + dx * item.length, item.position[1] + dy * item.length)
            current = bounds([transform(root)])
            body = current if body is None else body.merged(current)
            internal = (item.name_text_size or item.number_text_size) / 2
            external = item.number_text_size / 2
            local: list[Point] = [(-item.length, 0), (0, 0)]
            shape = item.shape.value
            if shape in ("inverted", "inverted_clock"):
                local.extend([(-external * 2, -external), (0, external)])
            if shape in ("clock", "inverted_clock", "clock_low", "edge_clock_high"):
                local.extend([(0, -internal), (internal, internal)])
            if shape in ("input_low", "clock_low", "edge_clock_high"):
                local.extend([(-2 * external, -2 * external), (0, 0)])
            if shape == "non_logic":
                local.extend([(-external, -external), (external, external)])
            if item.electrical_type.value not in ("no_connect", "not_connected"):
                local.extend([(-item.length - 0.381, -0.381), (-item.length + 0.381, 0.381)])
            points = [(root[0] + x * dx - y * dy, root[1] + x * dy + y * dx) for x, y in local]
            current = bounds([transform(p) for p in points], 0.0001)
            full = current if full is None else full.merged(current)
            continue
        if isinstance(item, SchRect):
            points = [
                item.start,
                item.end,
                (item.start[0], item.end[1]),
                (item.end[0], item.start[1]),
            ]
        elif isinstance(item, SchCircle):
            x, y = item.center
            points = [(x - item.radius, y - item.radius), (x + item.radius, y + item.radius)]
        elif isinstance(item, SchArc):
            points = [
                (to_mm(x), to_mm(y))
                for x, y in arc_extreme_points_iu(
                    point_to_iu(item.start), point_to_iu(item.mid), point_to_iu(item.end)
                )
            ]
        elif isinstance(item, SchBezier):
            for i in range(257):
                t = i / 256
                weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t**3)
                points.append(
                    (
                        sum(p[0] * w for p, w in zip(item.pts, weights, strict=True)),
                        sum(p[1] * w for p, w in zip(item.pts, weights, strict=True)),
                    )
                )
        elif isinstance(item, SchPolyline):
            points = list(item.pts)
        elif isinstance(item, SymTextBox):
            x, y = item.position
            points = [(x, y), (x + item.size[0], y + item.size[1])]
        elif isinstance(item, SymText):
            box = text_bounds(
                item.content,
                (item.position[0], -item.position[1]),
                item.angle_degrees,
                item.attributes or TextAttributes(),
                font,
            )
            points = [(box.min_x, -box.min_y), (box.max_x, -box.max_y)]
        if isinstance(item, SchArc | SchBezier | SchCircle | SchPolyline | SchRect | SymTextBox):
            pad = max(0, item.stroke.width) / 2
        if points:
            current = bounds([transform(p) for p in points], pad)
            body = current if body is None else body.merged(current)
            full = current if full is None else full.merged(current)
    if body is None:
        body = bounds([transform((0, 0))])
    return body, body if full is None else body.merged(full)
