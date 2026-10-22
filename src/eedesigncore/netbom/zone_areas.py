"""Small zone-area summaries derived from saved polygon coordinates."""

from __future__ import annotations

import math

from pydantic import JsonValue

from eedesigncore.common.layers import expand_layer_names, is_copper_layer_name, is_layer_wildcard
from eedesigncore.common.models import PtsElement
from eedesigncore.common.pcb_family.board_items import Zone


def _polygon_area(points: list[PtsElement]) -> float | None:
    if len(points) < 3 or len(points) > 1000000 or any(not isinstance(p, tuple) for p in points):
        return None
    vertices = [p for p in points if isinstance(p, tuple)]
    if any(not math.isfinite(v) for point in vertices for v in point):
        return None
    x, y = vertices[0]
    try:
        area = (
            abs(
                math.fsum(
                    (a[0] - x) * (b[1] - y) - (b[0] - x) * (a[1] - y)
                    for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True)
                )
            )
            / 2
        )
    except (OverflowError, ValueError):
        return None
    return area if math.isfinite(area) and area > 0 else None


def _area_sum(values: list[float]) -> float:
    try:
        return math.fsum(values)
    except OverflowError as error:
        raise ValueError("Zone area exceeds the numeric range.") from error


def zone_area_estimates(zone: Zone, board_layers: list[str]) -> dict[str, JsonValue]:
    """Summarize saved linear polygons without filling or merging copper."""
    selected = zone.layers or ([zone.layer] if zone.layer else [])
    layers: set[str] = set()
    unknown_layer = False
    for name in selected:
        if is_layer_wildcard(name) and not board_layers:
            unknown_layer = True
            continue
        layers.update(
            layer
            for layer in expand_layer_names([name])
            if is_copper_layer_name(layer)
            and (not is_layer_wildcard(name) or layer in board_layers)
        )
    fallback_layer = next(iter(layers)) if len(layers) == 1 and not unknown_layer else ""
    filled: dict[str, list[float | None]] = {}
    for polygon in zone.filled_polygons:
        layer = polygon.layer or fallback_layer
        if not layer:
            unknown_layer = True
        elif is_copper_layer_name(layer):
            layers.add(layer)
        else:
            continue
        filled.setdefault(layer, []).append(_polygon_area(polygon.pts))
    outlines = [_polygon_area(polygon.pts) for polygon in zone.polygons]
    outline = None
    if outlines and all(value is not None for value in outlines):
        areas = [value for value in outlines if value is not None]
        outline = areas[0] - _area_sum(areas[1:])
        if not math.isfinite(outline) or outline < 0:
            outline = None
    rows: list[JsonValue] = []
    for layer in sorted(layers | ({""} if unknown_layer or not selected else set())):
        values = filled.get(layer, [])
        known = _area_sum([value for value in values if value is not None])
        complete = bool(values) and all(value is not None for value in values)
        rows.append(
            {
                "layer": layer or None,
                "saved_fill_area_mm2": known if complete else None,
                "known_saved_fill_area_mm2": known,
                "filled_polygon_count": len(values),
                "unsupported_polygon_count": sum(value is None for value in values),
                "fill_status": "available"
                if complete
                else "unsupported_geometry"
                if values
                else "not_saved",
            }
        )
    return {
        "method": "saved-linear-zone-polygons-v1",
        "units": "mm2",
        "outline_area_mm2": outline,
        "outline_status": "available"
        if outline is not None
        else "unsupported_geometry"
        if outlines
        else "not_saved",
        "layers": rows,
    }
