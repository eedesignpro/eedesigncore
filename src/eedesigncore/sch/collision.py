"""Collection clearance checks using measured parts and Clipper segment clipping."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import pyclipper  # type: ignore[import-untyped]

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.sch.measurement import Point, SchematicMeasurement, SchematicPartBounds

_SCALE = 1_000_000


@dataclass(frozen=True)
class SchematicCollision:
    first_item: str
    first_part: str
    second_item: str | None
    second_part: str | None
    kind: str
    region: BoundingBox


def _expanded(box: BoundingBox, distance: float) -> BoundingBox:
    return BoundingBox(
        min_x=box.min_x - distance,
        min_y=box.min_y - distance,
        max_x=box.max_x + distance,
        max_y=box.max_y + distance,
    )


def _intersection(a: BoundingBox, b: BoundingBox) -> BoundingBox | None:
    left, top = max(a.min_x, b.min_x), max(a.min_y, b.min_y)
    right, bottom = min(a.max_x, b.max_x), min(a.max_y, b.max_y)
    if right <= left or bottom <= top:
        return None
    return BoundingBox(min_x=left, min_y=top, max_x=right, max_y=bottom)


def _polygon(box: BoundingBox) -> list[tuple[int, int]]:
    return [
        (round(x * _SCALE), round(y * _SCALE))
        for x, y in (
            (box.min_x, box.min_y),
            (box.max_x, box.min_y),
            (box.max_x, box.max_y),
            (box.min_x, box.max_y),
        )
    ]


def _segment_clip(part: SchematicPartBounds, box: BoundingBox, clearance: float) -> list[Point]:
    if part.segment is None:
        return []
    region = _expanded(box, part.stroke_mm / 2 + clearance + 0.0001)
    path = [(round(x * _SCALE), round(y * _SCALE)) for x, y in part.segment]
    if path[0] == path[1]:
        x, y = part.segment[0]
        return (
            [(x, y)]
            if region.min_x <= x <= region.max_x and region.min_y <= y <= region.max_y
            else []
        )
    engine = pyclipper.Pyclipper()
    engine.AddPath(_polygon(region), pyclipper.PT_CLIP, True)
    engine.AddPath(path, pyclipper.PT_SUBJECT, False)
    paths = cast(
        list[list[tuple[int, int]]],
        pyclipper.OpenPathsFromPolyTree(
            engine.Execute2(
                pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
            )
        ),
    )
    return [(x / _SCALE, y / _SCALE) for path in paths for x, y in path]


def _on_segment(point: Point, segment: tuple[Point, Point]) -> bool:
    return (
        abs(math.dist(segment[0], point) + math.dist(point, segment[1]) - math.dist(*segment))
        < 0.00001
    )


def _segment_polygon(part: SchematicPartBounds, extra: float) -> list[list[tuple[int, int]]]:
    assert part.segment is not None
    path = [(round(x * _SCALE), round(y * _SCALE)) for x, y in part.segment]
    radius = max(0.0001, part.stroke_mm / 2 + extra)
    if path[0] == path[1]:
        x, y = part.segment[0]
        return [
            _polygon(
                BoundingBox(min_x=x - radius, min_y=y - radius, max_x=x + radius, max_y=y + radius)
            )
        ]
    offset = pyclipper.PyclipperOffset(arc_tolerance=100)
    offset.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    return cast(list[list[tuple[int, int]]], offset.Execute(radius * _SCALE))


def _segments_intersect(a: SchematicPartBounds, b: SchematicPartBounds, clearance: float) -> bool:
    engine = pyclipper.Pyclipper()
    engine.AddPaths(_segment_polygon(a, clearance), pyclipper.PT_SUBJECT, True)
    engine.AddPaths(_segment_polygon(b, 0), pyclipper.PT_CLIP, True)
    return bool(engine.Execute(pyclipper.CT_INTERSECTION))


def _allowed_contact(
    line: SchematicPartBounds, other: SchematicPartBounds, points: list[Point], clearance: float
) -> bool:
    if line.segment is None or not points:
        return False
    radius = (line.stroke_mm + other.stroke_mm) / 2 + clearance + 0.001
    for point in other.contacts:
        if not _on_segment(point, line.segment):
            continue
        if all(math.dist(p, point) <= radius for p in points):
            return True
    return False


def _internal_pair(
    first: SchematicMeasurement, a: SchematicPartBounds, b: SchematicPartBounds
) -> bool:
    if "field:" in a.name or "field:" in b.name:
        return a.name != b.name
    if first.kind == "Sheet" and a.name.startswith("pin:") and b.name.startswith("pin:"):
        return a.name.split(":")[1] != b.name.split(":")[1]
    return False


def find_schematic_overlaps(
    items: Iterable[SchematicMeasurement],
    *,
    clearance_mm: float = 0.0,
    page_bounds: BoundingBox | None = None,
    allow_incomplete: bool = False,
) -> tuple[SchematicCollision, ...]:
    """Check visible parts, wires, clearance, and optional page limits.

    Wire crossings are allowed. A wire contact exception applies only to the
    small intersecting region at a real connection, never to the entire wire.
    Sheet interiors are containers. The operation does not move any item.
    """
    if not math.isfinite(clearance_mm) or not 0 <= clearance_mm <= 1e9:
        raise ValueError("Clearance must be finite and nonnegative.")
    measurements = tuple(items)
    if page_bounds is not None and (
        any(not math.isfinite(v) for v in page_bounds.model_dump().values())
        or page_bounds.width < 0
        or page_bounds.height < 0
    ):
        raise ValueError("Page bounds must be finite and ordered.")
    identities = [item.item_id for item in measurements]
    if len(set(identities)) != len(identities):
        raise ValueError("Collection measurements need distinct item identifiers.")
    if not allow_incomplete and any(not item.complete for item in measurements):
        raise ValueError("Collision checks require complete measurements. Inspect diagnostics.")
    flattened = [(item, part) for item in measurements for part in item.parts if part.obstacle]
    for _, part in flattened:
        if part.bounds.width < 0 or part.bounds.height < 0:
            raise ValueError("Part bounds must be ordered.")
        if not math.isfinite(part.stroke_mm) or not 0 <= part.stroke_mm <= 1e9:
            raise ValueError("Part stroke widths must be finite and nonnegative.")
        coordinates = [*part.bounds.model_dump().values()]
        coordinates.extend(value for point in part.contacts for value in point)
        if part.segment is not None:
            coordinates.extend(value for point in part.segment for value in point)
        if any(not math.isfinite(value) or abs(value) > 1e9 for value in coordinates):
            raise ValueError("Collision coordinates exceed the supported finite range.")
    flattened.sort(key=lambda pair: pair[1].bounds.min_x)
    conflicts = []
    electrical_lines = {"wire", "bus", "BusEntry"}
    for index, (first, a) in enumerate(flattened):
        if page_bounds is not None:
            inner = _expanded(page_bounds, -clearance_mm)
            if (
                a.bounds.min_x < inner.min_x
                or a.bounds.min_y < inner.min_y
                or a.bounds.max_x > inner.max_x
                or a.bounds.max_y > inner.max_y
            ):
                conflicts.append(
                    SchematicCollision(first.item_id, a.name, None, None, "outside_page", a.bounds)
                )
        for second, b in flattened[index + 1 :]:
            if b.bounds.min_x > a.bounds.max_x + clearance_mm:
                break
            if first.item_id == second.item_id and not _internal_pair(first, a, b):
                continue
            if first.kind in electrical_lines and second.kind in electrical_lines:
                continue
            region = _intersection(_expanded(a.bounds, clearance_mm), b.bounds)
            if region is None:
                continue
            if a.segment is not None or b.segment is not None:
                line, other = (a, b) if a.segment is not None else (b, a)
                points = _segment_clip(line, other.bounds, clearance_mm)
                if not points or _allowed_contact(line, other, points, clearance_mm):
                    continue
                if other.segment is not None and not _segments_intersect(
                    line, other, clearance_mm
                ):
                    continue
            elif (first.kind == "NoConnect" or second.kind == "NoConnect") and any(
                math.dist(p, q) < 0.00001 for p in a.contacts for q in b.contacts
            ):
                continue
            kind = "overlap" if _intersection(a.bounds, b.bounds) else "clearance"
            conflicts.append(
                SchematicCollision(first.item_id, a.name, second.item_id, b.name, kind, region)
            )
    return tuple(conflicts)
