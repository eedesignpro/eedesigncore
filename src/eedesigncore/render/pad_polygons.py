"""Integer polygon union and offset for pad apertures.

Curves use a one-micron chord tolerance. Polygon offsets preserve holes and
disconnected regions. Offset vertices need not match KiCad's Clipper2 vertices.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import pyclipper  # type: ignore[import-untyped]

from eedesigncore.common.kimath import kiround
from eedesigncore.common.pcb_family.enums import CustomAnchor
from eedesigncore.common.pcb_family.poly_outline import arc_to_polyline_iu, polygon_outline_iu
from eedesigncore.common.polygon import round_chamfered_rect_polygon
from eedesigncore.common.transform import point_to_iu
from eedesigncore.mod.models import (
    PrimArc,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimPoly,
    PrimRect,
)
from eedesigncore.mod.pad_geometry import PadLayerGeometry

Point = tuple[int, int]
Paths = list[list[Point]]
ERROR_IU = 1000
MAX_COORDINATE_IU = 2**31 - 1


def _check_range(paths: Sequence[Sequence[Point]], distance: float = 0) -> None:
    if not math.isfinite(distance) or abs(distance) > MAX_COORDINATE_IU:
        raise ValueError("The pad aperture clearance exceeds KiCad's coordinate range.")
    if any(
        abs(value) + abs(distance) > MAX_COORDINATE_IU
        for path in paths
        for point in path
        for value in point
    ):
        raise ValueError("The pad aperture exceeds KiCad's coordinate range.")


def union_polygons(paths: Paths) -> Paths:
    """Merge filled rings while retaining their holes."""
    paths = [path for path in paths if len(path) >= 3]
    if not paths:
        return []
    _check_range(paths)
    union = pyclipper.Pyclipper()
    union.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    return cast(Paths, union.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO))


def offset_polygons(paths: Paths, margin: int) -> Paths:
    """Offset closed rings. Outer rings and holes must have opposite winding."""
    if not paths or margin == 0:
        return paths
    _check_range(paths, margin)
    offset = pyclipper.PyclipperOffset(2.0, ERROR_IU)
    offset.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return cast(Paths, offset.Execute(margin))


def _circle(center: Point, radius: float) -> list[Point]:
    if radius <= 0:
        return []
    _check_range([[center]], radius)
    step = 2 * math.acos(max(-1.0, 1 - ERROR_IU / radius))
    count = max(8, math.ceil(2 * math.pi / step))
    return [
        (
            kiround(center[0] + radius * math.cos(i * 2 * math.pi / count)),
            kiround(center[1] + radius * math.sin(i * 2 * math.pi / count)),
        )
        for i in range(count)
    ]


def _stroke(points: Sequence[Point], width: int, *, closed: bool = False) -> Paths:
    if width <= 0 or not points:
        return []
    _check_range([points], width / 2)
    if len(set(points)) == 1:
        return [_circle(points[0], width / 2)]
    offset = pyclipper.PyclipperOffset(2.0, ERROR_IU)
    offset.AddPath(
        points, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDLINE if closed else pyclipper.ET_OPENROUND
    )
    return cast(Paths, offset.Execute(width / 2))


def _curve(points: list[Point]) -> list[Point]:
    """Sample a cubic with a step bounded by its control polygon length."""
    a, b, c, d = points
    _check_range([points])
    length = math.dist(a, b) + math.dist(b, c) + math.dist(c, d)
    count = max(2, math.ceil(math.sqrt(max(1, length) / ERROR_IU) * 4))
    return [
        (
            kiround(
                (1 - t) ** 3 * a[0]
                + 3 * (1 - t) ** 2 * t * b[0]
                + 3 * (1 - t) * t * t * c[0]
                + t**3 * d[0]
            ),
            kiround(
                (1 - t) ** 3 * a[1]
                + 3 * (1 - t) ** 2 * t * b[1]
                + 3 * (1 - t) * t * t * c[1]
                + t**3 * d[1]
            ),
        )
        for t in (i / count for i in range(count + 1))
    ]


def custom_polygons(geometry: PadLayerGeometry) -> Paths:
    """Merge the anchor and primitives in pad-local integer coordinates."""
    paths: Paths = []
    size = point_to_iu(geometry.size)
    if min(size) > 0:
        if geometry.custom_anchor is CustomAnchor.rect:
            x, y = int(size[0] / 2), int(size[1] / 2)
            paths.append([(-x, -y), (x, -y), (x, y), (-x, y)])
        else:
            paths.append(_circle((0, 0), size[0] / 2))

    for primitive in geometry.primitives:
        width = kiround(getattr(primitive, "width", 0.0) * 1e6)
        if isinstance(primitive, PrimCircle):
            center, end = point_to_iu(primitive.center), point_to_iu(primitive.end)
            radius = math.dist(center, end)
            outer = _circle(center, radius + width / 2)
            if outer:
                paths.append(outer)
            if not primitive.fill:
                inner = _circle(center, radius - width / 2)
                if inner:
                    paths.append(list(reversed(inner)))
        elif isinstance(primitive, PrimLine):
            paths.extend(
                _stroke([point_to_iu(primitive.start), point_to_iu(primitive.end)], width)
            )
        elif isinstance(primitive, PrimArc):
            points = arc_to_polyline_iu(
                point_to_iu(primitive.start),
                point_to_iu(primitive.mid),
                point_to_iu(primitive.end),
                max_error=ERROR_IU,
            )
            paths.extend(_stroke(points, width))
        elif isinstance(primitive, PrimCurve):
            points = polygon_outline_iu(list(primitive.pts), point_to_iu) or []
            if points is not None and len(points) == 4:
                paths.extend(_stroke(_curve(points), width))
        elif isinstance(primitive, PrimRect | PrimPoly):
            if isinstance(primitive, PrimRect):
                x1, y1 = point_to_iu(primitive.start)
                x2, y2 = point_to_iu(primitive.end)
                points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                if primitive.corner_radius:
                    radius = kiround(primitive.corner_radius * 1e6)
                    points = round_chamfered_rect_polygon(
                        (abs(x2 - x1), abs(y2 - y1)),
                        corner_radius=max(0, min(radius, abs(x2 - x1) // 2, abs(y2 - y1) // 2)),
                        chamfer_ratio=0,
                        chamfer_corners=0,
                        angle_degrees=0,
                        position=(int((x1 + x2) / 2), int((y1 + y2) / 2)),
                        error=ERROR_IU,
                    )
            else:
                points = polygon_outline_iu(list(primitive.pts), point_to_iu) or []
            if len(points) < 3:
                continue
            _check_range([points], width / 2)
            if not pyclipper.Orientation(points):
                points.reverse()
            if primitive.fill:
                paths.append(points)
            paths.extend(_stroke(points, width, closed=True))

    return union_polygons(paths)
