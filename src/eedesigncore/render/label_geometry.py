"""Global-label border geometry shared by schematics and neutral diagrams."""

from __future__ import annotations

import math
from typing import Literal


def global_label_outline(
    *,
    text_width_mm: float,
    text_height_mm: float,
    shape: Literal["input", "output", "bidirectional", "tri_state", "passive"],
    anchor: tuple[float, float] = (0.0, 0.0),
    body_direction: Literal["left", "right", "top", "bottom"] = "right",
    pen_width_mm: float = 0.1524,
    label_size_ratio: float = 0.375,
) -> tuple[tuple[float, float], ...]:
    """Return a closed global-label border in millimeters, with Y downward.

    The caller supplies measured text width and nominal text height. The anchor
    is the native electrical connection point. Body direction selects the side
    of that anchor occupied by the text, not electrical flow. Native input has
    a point at the anchor; output has a point at the far end. No label, board,
    font, net inference, placement, or file access is required.
    """
    if shape not in ("input", "output", "bidirectional", "tri_state", "passive"):
        raise ValueError("Unsupported global-label border shape.")
    angles = {"right": 0, "top": 90, "left": 180, "bottom": 270}
    if body_direction not in angles:
        raise ValueError("The label body direction must be left, right, top, or bottom.")
    values = (*anchor, text_width_mm, text_height_mm, pen_width_mm, label_size_ratio)
    if len(anchor) != 2 or not all(math.isfinite(value) for value in values):
        raise ValueError("Label geometry requires a finite XY anchor and finite dimensions.")
    if text_width_mm < 0 or text_height_mm <= 0 or pen_width_mm < 0 or label_size_ratio < 0:
        raise ValueError("Label dimensions must be nonnegative, with a positive text height.")
    margin = label_size_ratio * text_height_mm
    half = text_height_mm / 2 + margin
    x = text_width_mm + 2 * margin + pen_width_mm + 0.0003
    y = half + pen_width_mm + 0.0003
    points = [(0.0, 0.0), (0.0, -y), (-x, -y), (-x, 0.0), (-x, y), (0.0, y)]
    if shape in ("input", "bidirectional", "tri_state"):
        points[0] = (half, 0.0)
        points = [(px - half, py) for px, py in points]
    if shape in ("output", "bidirectional", "tri_state"):
        points[3] = (points[3][0] - half, 0.0)
    points.append(points[0])
    radians = math.radians(angles[body_direction] - 180)
    cos, sin = math.cos(radians), math.sin(radians)
    outline = tuple(
        (anchor[0] + px * cos + py * sin, anchor[1] - px * sin + py * cos) for px, py in points
    )
    if not all(math.isfinite(value) for point in outline for value in point):
        raise ValueError("Label dimensions exceed the finite coordinate range.")
    return outline
