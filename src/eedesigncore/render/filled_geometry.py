"""Filled stroke outlines and polygon differences in integer nanometers."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Sequence

import pyclipper  # type: ignore[import-untyped]

from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.geometry import Point, PointMap, SvgShape, format_mm

Polygon = list[tuple[int, int]]


def _distance_to_segment(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length2 = dx * dx + dy * dy
    t = (
        max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2))
        if length2
        else 0.0
    )
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _midpoint(left: Point, right: Point) -> Point:
    return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)


def filled_polygons(shapes: Sequence[SvgShape], tolerance_mm: float = 0.001) -> list[Polygon]:
    """Flatten filled glyph curves for subtraction with a bounded chord error."""
    if not math.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError("Filled contour tolerance must be finite and positive.")
    recording = importlib.import_module("fontTools.pens.recordingPen")
    curves = importlib.import_module("fontTools.pens.qu2cuPen")
    parse_path = importlib.import_module("fontTools.svgLib.path").parse_path
    polygons: list[Polygon] = []
    count = 0
    for shape in shapes:
        target = recording.RecordingPen()
        parse_path(
            dict(shape.attrs)["d"],
            curves.Qu2CuPen(target, max_err=tolerance_mm / 4, all_cubic=True),
        )
        points: list[Point] = []
        for operation, values in target.value:
            if operation == "moveTo":
                points = [tuple(values[0])]
            elif operation == "lineTo":
                points.append(tuple(values[0]))
            elif operation == "curveTo":
                pending = [(points[-1], tuple(values[0]), tuple(values[1]), tuple(values[2]), 0)]
                while pending:
                    start, a, b, end, depth = pending.pop()
                    if (
                        max(
                            _distance_to_segment(a, start, end),
                            _distance_to_segment(b, start, end),
                        )
                        <= tolerance_mm * 0.75
                    ):
                        points.append(end)
                        count += 1
                        if count > 250000:
                            raise ValueError("Filled contour exceeds the subdivision limit.")
                        continue
                    if depth >= 20:
                        raise ValueError("Filled contour cannot meet the chord tolerance.")

                    sa, ab, be = _midpoint(start, a), _midpoint(a, b), _midpoint(b, end)
                    left, right = _midpoint(sa, ab), _midpoint(ab, be)
                    center = _midpoint(left, right)
                    pending.append((center, right, be, end, depth + 1))
                    pending.append((start, sa, left, center, depth + 1))
            elif operation == "closePath":
                if len(points) >= 3:
                    polygons.append([point_to_iu(point) for point in points])
                points = []
            else:
                raise ValueError("Filled glyph geometry must contain closed contours.")
    return polygons


def stroke_polygons(shapes: Sequence[SvgShape]) -> list[Polygon]:
    polygons = []
    for shape in shapes:
        width = shape.stroke_width_mm or 0
        if width <= 0:
            continue
        data = dict(shape.attrs).get("d", "")
        numbers = [
            float(value)
            for value in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", data)
        ]
        points = [
            point_to_iu((numbers[index], numbers[index + 1]))
            for index in range(0, len(numbers), 2)
        ]
        if len(points) < 2:
            continue
        offset = pyclipper.PyclipperOffset(arc_tolerance=500)
        offset.AddPath(points, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
        polygons.extend(offset.Execute(point_to_iu((width / 2, 0))[0]))
    return polygons


def difference(subject: list[Polygon], clip: list[Polygon]) -> list[Polygon]:
    engine = pyclipper.Pyclipper()
    if not subject:
        return []
    engine.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    if clip:
        engine.AddPaths(clip, pyclipper.PT_CLIP, True)
    return [
        [(int(x), int(y)) for x, y in path]
        for path in engine.Execute(
            pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
        )
    ]


def polygon_shape(polygons: list[Polygon], to_board: PointMap, precision: int) -> SvgShape:
    pieces = []
    for polygon in polygons:
        points = [to_board((to_mm(p[0]), to_mm(p[1]))) for p in polygon]
        pieces.append(
            " ".join(
                f"{'M' if index == 0 else 'L'}{format_mm(p[0], precision)},"
                f"{format_mm(p[1], precision)}"
                for index, p in enumerate(points)
            )
            + " Z"
        )
    return SvgShape("path", (("d", " ".join(pieces)), ("fill-rule", "nonzero")), filled=True)


def rectangle_polygon(start: Point, end: Point) -> Polygon:
    return [
        point_to_iu(start),
        point_to_iu((end[0], start[1])),
        point_to_iu(end),
        point_to_iu((start[0], end[1])),
    ]
