"""Clipped hatch fills with KiCad's spacing and global hatch origin."""

from __future__ import annotations

import math

import pyclipper  # type: ignore[import-untyped]

from eedesigncore.common.models import FillType
from eedesigncore.common.pcb_family.shapes import PcbCircle, PcbPoly, PcbRect
from eedesigncore.common.sch_family.shapes import SchCircle, SchPolyline, SchRect
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.filled_geometry import Polygon, difference, polygon_shape
from eedesigncore.render.geometry import PointMap, SvgShape

HATCHES = (FillType.hatch, FillType.reverse_hatch, FillType.cross_hatch)
HatchItem = PcbCircle | PcbPoly | PcbRect | SchCircle | SchPolyline | SchRect


def outline(item: HatchItem) -> Polygon:
    points: list[tuple[float, float]]
    if isinstance(item, PcbCircle | SchCircle):
        x, y = item.center
        radius = item.radius if isinstance(item, SchCircle) else math.dist(item.center, item.end)
        count = (
            max(16, math.ceil(math.pi / math.acos(max(-1, 1 - 0.005 / radius)))) if radius else 16
        )
        points = [
            (
                x + radius * math.cos(i * math.tau / count),
                y + radius * math.sin(i * math.tau / count),
            )
            for i in range(count)
        ]
    elif isinstance(item, PcbRect | SchRect):
        left, right = sorted((item.start[0], item.end[0]))
        top, bottom = sorted((item.start[1], item.end[1]))
        radius = min(max(item.corner_radius or 0, 0), (right - left) / 2, (bottom - top) / 2)
        if radius:
            count = max(4, math.ceil(math.pi / 2 / math.acos(max(-1, 1 - 0.005 / radius))))
            points = [
                (
                    x + radius * math.cos((corner + step / count) * math.pi / 2),
                    y + radius * math.sin((corner + step / count) * math.pi / 2),
                )
                for corner, (x, y) in enumerate(
                    (
                        (right - radius, bottom - radius),
                        (left + radius, bottom - radius),
                        (left + radius, top + radius),
                        (right - radius, top + radius),
                    )
                )
                for step in range(count + 1)
            ]
        else:
            points = [(left, top), (right, top), (right, bottom), (left, bottom)]
    else:
        from eedesigncore.common.pcb_family.poly_outline import polygon_outline_iu

        return polygon_outline_iu(list(item.pts), point_to_iu) or []
    return [point_to_iu(point) for point in points]


def hatch_shapes(
    item: HatchItem, to_board: PointMap, precision: int, width: float, *, schematic: bool = False
) -> list[SvgShape]:
    if width <= 0:
        return []
    from eedesigncore.render.traversal import identity_map

    polygon = [point_to_iu(to_board((to_mm(x), to_mm(y)))) for x, y in outline(item)]
    if len(polygon) < 3 or not pyclipper.Area(polygon):
        return []
    line_width = point_to_iu((width / 2 if schematic else width, 0))[0]
    left, right = min(p[0] for p in polygon), max(p[0] for p in polygon)
    top, bottom = min(p[1] for p in polygon), max(p[1] for p in polygon)
    spacing = max(
        line_width * (40 if schematic else 10), math.ceil(max(right - left, bottom - top) / 100), 1
    )
    if item.fill.type == FillType.cross_hatch:
        cosine = math.sqrt(0.5)
        rotated = [(round((x - y) * cosine), round((x + y) * cosine)) for x, y in polygon]
        left, right = min(p[0] for p in rotated), max(p[0] for p in rotated)
        top, bottom = min(p[1] for p in rotated), max(p[1] for p in rotated)
        holes = []
        size = spacing - line_width
        for x in range(math.trunc(left / spacing) * spacing - spacing, right + 1, spacing):
            for y in range(math.trunc(top / spacing) * spacing - spacing, bottom + 1, spacing):
                holes.append([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])
        polygons = [
            [(round((x + y) * cosine), round((y - x) * cosine)) for x, y in path]
            for path in difference([rotated], holes)
        ]
    else:
        slope = -1 if item.fill.type == FillType.hatch else 1
        intercepts = [y - slope * x for x, y in polygon]
        lines = [
            [(left - 1, slope * (left - 1) + a), (right + 1, slope * (right + 1) + a)]
            for a in range(
                math.trunc(min(intercepts) / spacing) * spacing, max(intercepts), spacing
            )
        ]
        if not lines:
            return []
        engine = pyclipper.Pyclipper()
        engine.AddPath(polygon, pyclipper.PT_CLIP, True)
        engine.AddPaths(lines, pyclipper.PT_SUBJECT, False)
        clipped = pyclipper.OpenPathsFromPolyTree(
            engine.Execute2(
                pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
            )
        )
        polygons = []
        for path in clipped:
            if len(path) < 2:
                continue
            offset = pyclipper.PyclipperOffset(arc_tolerance=500)
            offset.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
            polygons.extend(offset.Execute(line_width / 2))
    return [polygon_shape(polygons, identity_map, precision)] if polygons else []
