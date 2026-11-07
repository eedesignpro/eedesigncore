"""Schematic rule areas: the directive labels on each border and the items inside.

KiCad 10 ``SCH_RULE_AREA::RefreshContainedItemsAndDirectives``
(``sch_rule_area.cpp:230-296``):

* a directive label attaches when its connection point lies on the border,
  within 5 internal units;
* a wire or bus is inside when its segment touches the area;
* a pin or a net label is inside when its connection point is in the area or on
  its border;
* a symbol or a sheet is inside when its bounding box touches the area.

A rule area gives the net classes of its directives to the nets of the wires,
pins and labels inside (``CONNECTION_SUBGRAPH::GetNetclassesForDriver``,
``connection_graph.cpp:452-470``). It gives the component classes of its
directives to the symbols inside (``SCH_SYMBOL::GetComponentClassNames``,
``sch_symbol.cpp:3675-3699``) and to every symbol under a sheet inside
(``NETLIST_EXPORTER_XML::getSheetComponentClasses``, ``:1367-1413``).
"""

from __future__ import annotations

from dataclasses import dataclass

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.sch.models import LabelKind, LineType, Schematic

Point = tuple[float, float]

# 5 schematic internal units of 100 nm (``CollideEdge( point, nullptr, 5 )``).
DIRECTIVE_TOLERANCE_MM = 0.0005
# KiCad's default wire width, 6 mil, for a wire with no stroke width.
DEFAULT_WIRE_WIDTH_MM = 0.1524
NETCLASS_FIELDS = frozenset({"Netclass", "Net Class"})
COMPONENT_CLASS_FIELD = "Component Class"


@dataclass(frozen=True)
class RuleAreaScope:
    """One rule area of one sheet, with its directives and the items inside.

    Indexes point into the sheet's ``labels``, ``symbols``, ``sheets`` and
    ``lines`` lists.
    """

    index: int
    uuid: str | None
    polygon: tuple[Point, ...]
    directives: tuple[int, ...]
    netclasses: tuple[str, ...]
    component_classes: tuple[str, ...]
    labels: tuple[int, ...]
    lines: tuple[int, ...]
    symbols: tuple[int, ...]
    sheets: tuple[int, ...]

    def contains_point(self, point: Point) -> bool:
        """True when *point* is inside the area or on its border."""
        return point_in_polygon(point, self.polygon)


def _segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = dx * dx + dy * dy
    t = (
        0.0
        if length == 0
        else max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length))
    )
    x, y = start[0] + t * dx - point[0], start[1] + t * dy - point[1]
    return (x * x + y * y) ** 0.5


def _edges(polygon: tuple[Point, ...]) -> list[tuple[Point, Point]]:
    return [(polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))]


def on_border(point: Point, polygon: tuple[Point, ...], tolerance: float) -> bool:
    """True when *point* lies within *tolerance* of the polygon outline."""
    return any(_segment_distance(point, a, b) <= tolerance for a, b in _edges(polygon))


def point_in_polygon(point: Point, polygon: tuple[Point, ...], tolerance: float = 1e-9) -> bool:
    """True when *point* is inside *polygon* or on its border (even-odd rule)."""
    if len(polygon) < 3:
        return False
    if on_border(point, polygon, tolerance):
        return True
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in _edges(polygon):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_meet(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4):
        return True
    return (
        _segment_distance(a, c, d) == 0
        or _segment_distance(b, c, d) == 0
        or _segment_distance(c, a, b) == 0
        or _segment_distance(d, a, b) == 0
    )


def segment_touches(start: Point, end: Point, width: float, polygon: tuple[Point, ...]) -> bool:
    """True when a segment of *width* touches the area (``SHAPE_SEGMENT`` collide)."""
    if point_in_polygon(start, polygon) or point_in_polygon(end, polygon):
        return True
    half = width / 2
    for a, b in _edges(polygon):
        if _segments_meet(start, end, a, b):
            return True
        if (
            half > 0
            and min(
                _segment_distance(a, start, end),
                _segment_distance(b, start, end),
                _segment_distance(start, a, b),
                _segment_distance(end, a, b),
            )
            <= half
        ):
            return True
    return False


def box_touches(box: BoundingBox, polygon: tuple[Point, ...]) -> bool:
    """True when an axis-aligned box touches the area (``SHAPE_RECT`` collide)."""
    corners = (
        (box.min_x, box.min_y),
        (box.max_x, box.min_y),
        (box.max_x, box.max_y),
        (box.min_x, box.max_y),
    )
    if any(point_in_polygon(corner, polygon) for corner in corners):
        return True
    if any(box.min_x <= x <= box.max_x and box.min_y <= y <= box.max_y for x, y in polygon):
        return True
    return any(
        _segments_meet(c, d, a, b)
        for c, d in zip(corners, corners[1:] + corners[:1], strict=True)
        for a, b in _edges(polygon)
    )


def _symbol_box(sheet: Schematic, index: int) -> BoundingBox | None:
    from eedesigncore.sch.measurement import measure_schematic_item

    return measure_schematic_item(sheet.symbols[index], schematic=sheet).bounds


def _sheet_box(sheet: Schematic, index: int) -> BoundingBox:
    child = sheet.sheets[index]
    (x, y), (w, h) = child.position, child.size
    return BoundingBox(
        min_x=min(x, x + w), min_y=min(y, y + h), max_x=max(x, x + w), max_y=max(y, y + h)
    )


def rule_area_scopes(sheet: Schematic) -> list[RuleAreaScope]:
    """The directives and the contained items of every rule area on *sheet*.

    *sheet* should carry resolved text, so that directive field values are the
    shown values KiCad uses.
    """
    scopes: list[RuleAreaScope] = []
    symbol_boxes: dict[int, BoundingBox | None] = {}
    for area_index, area in enumerate(sheet.rule_areas):
        polygon = tuple((float(p[0]), float(p[1])) for p in area.shape.pts if isinstance(p, tuple))
        if len(polygon) > 1 and polygon[0] == polygon[-1]:
            polygon = polygon[:-1]
        if len(polygon) < 3:
            continue
        directives = tuple(
            index
            for index, label in enumerate(sheet.labels)
            if label.kind == LabelKind.netclass_flag
            and on_border(label.position, polygon, DIRECTIVE_TOLERANCE_MM)
        )
        netclasses: list[str] = []
        components: set[str] = set()
        for index in directives:
            for field in sheet.labels[index].fields:
                if field.name in NETCLASS_FIELDS and field.value:
                    netclasses.append(field.value)
                elif field.name == COMPONENT_CLASS_FIELD and field.value:
                    components.add(field.value)
        labels = tuple(
            index
            for index, label in enumerate(sheet.labels)
            if label.kind != LabelKind.netclass_flag and point_in_polygon(label.position, polygon)
        )
        lines = tuple(
            index
            for index, line in enumerate(sheet.lines)
            if line.line_type in (LineType.wire, LineType.bus)
            and segment_touches(
                line.start, line.end, line.stroke.width or DEFAULT_WIRE_WIDTH_MM, polygon
            )
        )
        symbols: list[int] = []
        for index in range(len(sheet.symbols)):
            if index not in symbol_boxes:
                symbol_boxes[index] = _symbol_box(sheet, index)
            box = symbol_boxes[index]
            if box is not None and box_touches(box, polygon):
                symbols.append(index)
        sheets = tuple(
            index
            for index in range(len(sheet.sheets))
            if box_touches(_sheet_box(sheet, index), polygon)
        )
        scopes.append(
            RuleAreaScope(
                index=area_index,
                uuid=area.shape.id,
                polygon=polygon,
                directives=directives,
                netclasses=tuple(sorted(set(netclasses))),
                component_classes=tuple(sorted(components)),
                labels=labels,
                lines=lines,
                symbols=tuple(symbols),
                sheets=sheets,
            )
        )
    return scopes


__all__ = [
    "RuleAreaScope",
    "box_touches",
    "point_in_polygon",
    "rule_area_scopes",
    "segment_touches",
]
