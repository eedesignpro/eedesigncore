"""Bounds and source groups collected during schematic drawing."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.render.document import escape_attr
from eedesigncore.render.geometry import SvgShape


def shape_bounds(shape: SvgShape, width: float) -> BoundingBox | None:
    """Enclose emitted geometry, including strokes and Bezier control hulls."""
    attrs = dict(shape.attrs)
    points: list[tuple[float, float]] = []
    if shape.tag == "circle":
        x, y, r = (float(attrs[key]) for key in ("cx", "cy", "r"))
        points = [(x - r, y - r), (x + r, y + r)]
    elif shape.tag in {"rect", "image"}:
        x, y, w, h = (float(attrs.get(key, "0")) for key in ("x", "y", "width", "height"))
        points = [(x, y), (x + w, y + h)]
    elif shape.tag == "path":
        tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", attrs["d"])
        index = 0
        current = (0.0, 0.0)
        origin = current
        command = ""
        relative = False
        while index < len(tokens):
            if tokens[index].isalpha():
                token = tokens[index]
                relative = token.islower()
                command = token.upper()
                index += 1
                if command == "Z":
                    points.extend((current, origin))
                    current = origin
                    command = ""
                    continue
            count = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "C": 6, "A": 7}.get(command)
            if count is None or index + count > len(tokens):
                raise ValueError("Unsupported drawing path in bounds calculation.")
            values = [float(value) for value in tokens[index : index + count]]
            index += count
            if command in {"H", "V"}:
                coordinate = values[0]
                axis = 0 if command == "H" else 1
                if relative:
                    coordinate += current[axis]
                end = (coordinate, current[1]) if axis == 0 else (current[0], coordinate)
                points.extend((current, end))
                current = end
                continue
            if relative:
                coordinate_start = 5 if command == "A" else 0
                for i in range(coordinate_start, len(values), 2):
                    values[i] += current[0]
                    values[i + 1] += current[1]
            end = (values[-2], values[-1])
            if command == "A":
                rx, ry, rotation, large, sweep, _, _ = values
                if abs(rx - ry) > 1e-6 or rotation != 0:
                    raise ValueError("Bounds require circular schematic arcs.")
                dx, dy = end[0] - current[0], end[1] - current[1]
                distance = math.hypot(dx, dy)
                radius = max(abs(rx), distance / 2)
                if distance:
                    offset = math.sqrt(max(0, radius * radius - distance * distance / 4))
                    sign = -1 if bool(large) == bool(sweep) else 1
                    cx = (current[0] + end[0]) / 2 + sign * (-dy / distance) * offset
                    cy = (current[1] + end[1]) / 2 + sign * (dx / distance) * offset
                    start_angle = math.atan2(current[1] - cy, current[0] - cx)
                    end_angle = math.atan2(end[1] - cy, end[0] - cx)
                    direction = 1 if sweep else -1
                    span = (direction * (end_angle - start_angle)) % math.tau
                    for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
                        if (direction * (angle - start_angle)) % math.tau <= span + 1e-9:
                            points.append(
                                (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
                            )
            else:
                points.extend(zip(values[::2], values[1::2], strict=True))
            points.extend((current, end)) if command != "M" else points.append(end)
            current = end
            if command == "M":
                origin = end
                command = "L"
    if not points:
        return None
    if shape.tag == "image" and "transform" in attrs:
        matrix_values = attrs["transform"].removeprefix("matrix(").removesuffix(")").split()
        a, b, c, d, e, f = map(float, matrix_values)
        left, top = points[0]
        right, bottom = points[1]
        points = [
            (a * x + c * y + e, b * x + d * y + f)
            for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
        ]
    pad = max(width, 0) / 2 + 0.0001
    return BoundingBox(
        min_x=min(x for x, _ in points) - pad,
        min_y=min(y for _, y in points) - pad,
        max_x=max(x for x, _ in points) + pad,
        max_y=max(y for _, y in points) + pad,
    )


@dataclass
class DrawingMetadata:
    parts: list[tuple[str, BoundingBox]] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    bounds: dict[str, BoundingBox] = field(default_factory=dict)
    bodies: dict[str, BoundingBox] = field(default_factory=dict)

    def group(
        self, key: str, foreground: list[str], background: list[str], start: tuple[int, int]
    ) -> None:
        identity = self.ids.get(key)
        if identity is None:
            return
        for elements, offset in ((foreground, start[0]), (background, start[1])):
            if len(elements) > offset:
                elements[offset:] = [
                    f'<g data-item-id="{escape_attr(identity)}">'
                    + "".join(elements[offset:])
                    + "</g>"
                ]

    def record(self, key: str, boxes: list[BoundingBox], *, body: bool = False) -> None:
        identity = self.ids.get(key)
        if identity is None or not boxes:
            return
        result = boxes[0]
        for box in boxes[1:]:
            result = result.merged(box)
        (self.bodies if body else self.bounds)[identity] = result
