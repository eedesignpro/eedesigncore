"""Saved PCB text, including multiline and knockout lettering."""

from __future__ import annotations

import math
from dataclasses import replace

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import Property, PtsArc, TextAttributes
from eedesigncore.common.pcb_family.shapes import PcbText
from eedesigncore.common.transform import point_to_iu
from eedesigncore.render.filled_geometry import (
    difference,
    filled_polygons,
    polygon_shape,
    stroke_polygons,
)
from eedesigncore.render.fonts import OutlineFont
from eedesigncore.render.geometry import PointMap, SvgShape, text_shapes
from eedesigncore.render.text_boxes import line_positions, text_pen
from eedesigncore.render.traversal import identity_map


def saved_text_shapes(item: PcbText | Property, resolved_text: str) -> list[SvgShape] | None:
    """Read saved board-coordinate glyphs from caller-certified unchanged text.

    The caller must retain the saved position, size, mirror, style, and footprint
    transform. The cache does not store enough metadata to verify those fields.
    A changed text or angle, empty cache, or knockout item cannot use this path.
    """
    attrs, cache = item.attributes, item.render_cache
    if (
        attrs is None
        or not attrs.font_face
        or cache is None
        or not cache.polygons
        or item.knockout
        or cache.resolved_text != unescape_string(resolved_text)
        or not math.isclose((cache.angle - item.angle) % 360, 0, abs_tol=1e-6)
    ):
        return None
    if not item.visible or not attrs.visible:
        return []
    polygons = []
    for polygon in cache.polygons:
        if len(polygon.pts) < 3 or any(isinstance(point, PtsArc) for point in polygon.pts):
            return None
        polygons.append(
            [point_to_iu(point) for point in polygon.pts if not isinstance(point, PtsArc)]
        )
    return [polygon_shape(polygons, identity_map, 6)]


def pcb_text_shapes(
    item: PcbText | Property,
    to_board: PointMap,
    precision: int,
    font: object | None,
    *,
    use_saved_cache: bool = False,
) -> list[SvgShape]:
    if use_saved_cache:
        cached = saved_text_shapes(item, item.content if isinstance(item, PcbText) else item.value)
        if cached is not None:
            return cached
    item = item.model_copy(update={"position": to_board(item.position)})
    to_board = identity_map
    attrs = item.attributes or TextAttributes()
    if not item.visible or not attrs.visible:
        return []
    content = unescape_string(item.content if isinstance(item, PcbText) else item.value)
    if content and attrs.font_face and not isinstance(font, OutlineFont):
        raise ValueError(f"Named plot font requires an explicit loaded face: {attrs.font_face}")
    pen = text_pen(attrs)
    attrs = attrs.model_copy(update={"font_thickness": pen})
    lines = content.split("\n")
    angle = 0.0 if item.knockout else item.angle
    shapes: list[SvgShape] = []
    for line, anchor in zip(
        lines, line_positions(lines, item.position, angle, attrs, font), strict=True
    ):
        shapes.extend(
            replace(shape, stroke_width_mm=pen)
            for shape in text_shapes(
                line,
                anchor,
                angle,
                attrs,
                identity_map if item.knockout else to_board,
                precision,
                font,
            )
        )
    if not item.knockout:
        return shapes
    polygons = (
        filled_polygons(shapes) if isinstance(font, OutlineFont) else stroke_polygons(shapes)
    )
    if not polygons:
        return []
    margin = point_to_iu((max(pen / 2, attrs.font_size[1] / 9), 0))[0]
    left = min(p[0] for poly in polygons for p in poly) - margin
    right = max(p[0] for poly in polygons for p in poly) + margin
    top = min(p[1] for poly in polygons for p in poly) - margin
    bottom = max(p[1] for poly in polygons for p in poly) + margin
    polygons = difference([[(left, top), (right, top), (right, bottom), (left, bottom)]], polygons)
    radians = math.radians(item.angle)

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point[0] - item.position[0], point[1] - item.position[1]
        return to_board(
            (
                item.position[0] + x * math.cos(radians) + y * math.sin(radians),
                item.position[1] - x * math.sin(radians) + y * math.cos(radians),
            )
        )

    return [polygon_shape(polygons, transform, precision)]
