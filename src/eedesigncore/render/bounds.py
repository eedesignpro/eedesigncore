"""Measure selected plot content without an intermediate drawing file."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from eedesigncore.common.bbox import BoundingBox, Extent, arc_extreme_points_iu
from eedesigncore.common.layers import item_layers
from eedesigncore.common.models import Property, PtsArc, PtsElement, TextAttributes
from eedesigncore.common.pcb_family.board_items import Dimension, Table, Zone
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
)
from eedesigncore.common.pcb_family.transform import BOARD_FRAME_TYPES
from eedesigncore.common.text_variables import has_reference, resolve
from eedesigncore.common.transform import point_to_iu, to_iu
from eedesigncore.mod.models import Pad
from eedesigncore.mod.pad_geometry import pad_layer_geometry
from eedesigncore.pcb.models import Board, TrackArc, TrackSegment, Via
from eedesigncore.render.apertures import pad_aperture_margin
from eedesigncore.render.board_text import pcb_text_shapes, saved_text_shapes
from eedesigncore.render.dimensions import dimension_segments_mm
from eedesigncore.render.fonts import OutlineFont
from eedesigncore.render.geometry import PointMap, SvgShape, rectangle_chain
from eedesigncore.render.pads import pad_outline
from eedesigncore.render.text_boxes import pcb_text_box_shapes
from eedesigncore.render.traversal import (
    KICAD_CONTENT,
    RenderItem,
    footprint_point_map,
    identity_map,
    is_rule_area,
    render_items_by_layer,
    via_layers,
)
from eedesigncore.render.vias import DEFAULT_VIA_DIAMETER, via_diameter_mm


class UnsupportedPlotContent(ValueError):
    """Selected content cannot be measured with the supplied plot context."""


@dataclass(frozen=True)
class PlotBoundsPolicy:
    """Select the measured ink and the footprint text visibility."""

    stroke_mode: Literal["dxf_centerline", "visible_ink"] = "visible_ink"
    hide_footprint_values: bool = False
    hide_footprint_references: bool = False
    actual_drill_marks: bool = True


@dataclass(frozen=True)
class PlotFontRequest:
    """One named font face and style required by selected visible text."""

    family: str
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class PlotTextContext:
    """Supply project variables without access to files or machine settings."""

    variables: Mapping[str, str] = field(default_factory=dict)
    fonts: Mapping[PlotFontRequest | str, OutlineFont] = field(default_factory=dict)
    use_saved_text_cache: bool = False


def plot_font_requirements(
    board: Board,
    layers: Collection[str],
    *,
    policy: PlotBoundsPolicy,
    text_context: PlotTextContext | None = None,
) -> tuple[PlotFontRequest, ...]:
    """List selected visible named font styles without loading any fonts."""
    result: set[PlotFontRequest] = set()
    for entry in _selected_items(board, layers):
        item = entry.item
        if isinstance(item, Table):
            for cell in item.cells:
                attrs = cell.attributes
                if (
                    attrs is not None
                    and attrs.visible
                    and attrs.font_face
                    and cell.content
                    and min(cell.span or (1, 1)) > 0
                ):
                    result.add(PlotFontRequest(attrs.font_face, attrs.bold, attrs.italic))
            continue
        if isinstance(item, Dimension):
            item = item.text
        attrs = getattr(item, "attributes", None)
        if attrs is None or not attrs.visible or not getattr(item, "visible", True):
            continue
        if not attrs.font_face:
            continue
        raw = getattr(item, "content", getattr(item, "value", ""))
        if not raw:
            continue
        kind = item.name.lower() if isinstance(item, Property) else getattr(item, "text_type", "")
        if entry.footprint is not None:
            if policy.hide_footprint_values and kind == "value":
                continue
            if policy.hide_footprint_references and kind == "reference":
                continue
        if (
            text_context is not None
            and text_context.use_saved_text_cache
            and isinstance(item, PcbText | Property)
            and saved_text_shapes(item, _shown(entry, raw, text_context)) is not None
        ):
            continue
        result.add(PlotFontRequest(attrs.font_face, attrs.bold, attrs.italic))
    return tuple(
        sorted(result, key=lambda request: (request.family, request.bold, request.italic))
    )


def _chain(
    extent: Extent, points: Collection[PtsElement], mapping: PointMap, pad: int = 0
) -> None:
    for point in points:
        if isinstance(point, PtsArc):
            _arc(extent, point.start, point.mid, point.end, mapping, pad)
        else:
            extent.add(point_to_iu(mapping(point)), pad)


def _arc(
    extent: Extent,
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
    mapping: PointMap,
    pad: int,
) -> None:
    # Find axis crossings after rotation, in the measurement frame.
    for point in arc_extreme_points_iu(
        point_to_iu(mapping(start)), point_to_iu(mapping(mid)), point_to_iu(mapping(end))
    ):
        extent.add(point, pad)


def _shown(entry: RenderItem, raw: str, context: PlotTextContext) -> str:
    scope = dict(context.variables)
    scope["LAYER"] = entry.layer
    if entry.footprint is not None:
        scope.update({field.name: field.value for field in entry.footprint.properties})
        scope.update({field.name.upper(): field.value for field in entry.footprint.properties})
    shown = resolve(raw, scope)
    if has_reference(shown):
        raise UnsupportedPlotContent(f"Unresolved plot text: {shown!r}")
    return shown


def _path_bounds(extent: Extent, shape: SvgShape, pad: int) -> None:
    if shape.tag != "path":
        raise UnsupportedPlotContent(f"Unsupported text geometry: {shape.tag}")
    data = dict(shape.attrs)["d"]
    commands = set(re.findall(r"[A-DF-Za-df-z]", data))
    if commands <= {"M", "L", "Z"}:
        numbers = [
            float(value)
            for value in re.findall(r"[-+]?(?:[0-9]*\.)?[0-9]+(?:[eE][-+]?[0-9]+)?", data)
        ]
        if len(numbers) % 2:
            raise UnsupportedPlotContent("Incomplete text geometry coordinate.")
        for x, y in zip(numbers[::2], numbers[1::2], strict=True):
            extent.add(point_to_iu((x, y)), pad)
        return
    if not commands <= set("MmLlHhVvCcSsQqTtZz"):
        raise UnsupportedPlotContent("Text geometry contains an unsupported curve command.")
    BoundsPen = importlib.import_module("fontTools.pens.boundsPen").BoundsPen
    parse_path = importlib.import_module("fontTools.svgLib.path").parse_path

    pen = BoundsPen(None)
    parse_path(data, pen)
    if pen.bounds is not None:
        x0, y0, x1, y1 = pen.bounds
        extent.add(point_to_iu((x0, y0)), pad)
        extent.add(point_to_iu((x1, y1)), pad)


def _text(
    extent: Extent, entry: RenderItem, policy: PlotBoundsPolicy, context: PlotTextContext
) -> None:
    item = entry.item
    if not isinstance(item, PcbText | Property):
        raise TypeError("Text bounds require PCB text or a footprint field.")
    attrs = item.attributes or TextAttributes()
    if not item.visible or not attrs.visible:
        return
    kind = item.name.lower() if isinstance(item, Property) else item.text_type.lower()
    if entry.footprint is not None:
        if policy.hide_footprint_values and kind == "value":
            return
        if policy.hide_footprint_references and kind == "reference":
            return
    raw = item.value if isinstance(item, Property) else item.content
    shown = _shown(entry, raw, context)
    if context.use_saved_text_cache:
        cached = saved_text_shapes(item, shown)
        if cached is not None:
            for shape in cached:
                _path_bounds(extent, shape, 0)
            return
    if attrs.font_face:
        request = PlotFontRequest(attrs.font_face, attrs.bold, attrs.italic)
        font = context.fonts.get(request)
        if font is None and not request.bold and not request.italic:
            font = context.fonts.get(attrs.font_face)
        if font is None:
            raise UnsupportedPlotContent(
                f"Named plot font requires an explicit loaded face: {attrs.font_face}"
            )
        prepared = item.model_copy(
            update={"value" if isinstance(item, Property) else "content": shown}
        )
        for shape in pcb_text_shapes(prepared, entry.to_board, 6, font):
            _path_bounds(extent, shape, 0)
        return
    prepared = item.model_copy(
        update={"value" if isinstance(item, Property) else "content": shown}
    )
    for shape in pcb_text_shapes(prepared, entry.to_board, 6, None):
        width = shape.stroke_width_mm or 0.0
        pad = to_iu(width / 2) if policy.stroke_mode == "visible_ink" and not shape.filled else 0
        _path_bounds(extent, shape, pad)


def _textbox(
    extent: Extent, entry: RenderItem, policy: PlotBoundsPolicy, context: PlotTextContext
) -> None:
    item = entry.item
    if not isinstance(item, PcbTextBox):
        raise TypeError("Text box bounds require a PCB text box.")
    attrs = item.attributes or TextAttributes()
    if attrs.font_face:
        raise UnsupportedPlotContent("Named text box fonts require exact glyph bounds support.")
    prepared = item.model_copy(update={"content": _shown(entry, item.content, context)})
    shapes = pcb_text_box_shapes(prepared, entry.to_board, 6, None)
    if shapes is None:
        raise UnsupportedPlotContent("Unsupported text box geometry.")
    for shape in shapes:
        width = shape.stroke_width_mm if shape.stroke_width_mm is not None else item.stroke.width
        pad = to_iu(width / 2) if policy.stroke_mode == "visible_ink" and not shape.filled else 0
        _path_bounds(extent, shape, pad)


def _measure(
    extent: Extent,
    entry: RenderItem,
    board: Board,
    policy: PlotBoundsPolicy,
    context: PlotTextContext,
) -> None:
    item, mapping = entry.item, entry.to_board
    if isinstance(item, BOARD_FRAME_TYPES):
        mapping = identity_map
    stroke = getattr(item, "stroke", None)
    width = getattr(stroke, "width", getattr(item, "width", 0.0))
    pad = to_iu(width / 2) if policy.stroke_mode == "visible_ink" else 0
    if isinstance(item, PcbPoint):
        return
    if isinstance(item, PcbText | Property):
        _text(extent, entry, policy, context)
    elif isinstance(item, PcbTextBox):
        _textbox(extent, entry, policy, context)
    elif isinstance(item, PcbLine | TrackSegment):
        _chain(extent, [item.start, item.end], mapping, pad)
    elif isinstance(item, PcbArc | TrackArc):
        _arc(extent, item.start, item.mid, item.end, mapping, pad)
    elif isinstance(item, PcbRect):
        _chain(extent, rectangle_chain(item), mapping, pad)
    elif isinstance(item, PcbCircle):
        radius = math.dist(item.center, item.end)
        extent.add(point_to_iu(mapping(item.center)), to_iu(radius) + pad)
    elif isinstance(item, PcbPoly):
        _chain(extent, item.pts, mapping, pad)
    elif isinstance(item, Via):
        radius = via_diameter_mm(item, entry.layer, DEFAULT_VIA_DIAMETER) / 2
        extent.add(point_to_iu(item.at), to_iu(radius))
    elif isinstance(item, Zone):
        if is_rule_area(item):
            return
        for polygon in item.filled_polygons:
            if polygon.layer in (entry.layer, ""):
                _chain(extent, polygon.pts, mapping)
    elif isinstance(item, Pad):
        margin = pad_aperture_margin(
            item, entry.layer, footprint=entry.footprint, setup=board.setup, version=board.version
        )
        outline = pad_outline(item, entry.layer, mapping, margin=margin)
        for pad_polygon in (*outline.polygons, *outline.compound_polygons):
            _chain(extent, pad_polygon, identity_map)
        for center, radius in outline.discs:
            extent.add(point_to_iu(center), to_iu(radius))
        for start, end, width in outline.strokes:
            _chain(extent, [start, end], identity_map, to_iu(width / 2))
        for start, mid, end, width in outline.arcs:
            _arc(extent, start, mid, end, identity_map, to_iu(width / 2))
    elif isinstance(item, Dimension):
        segments = dimension_segments_mm(item)
        if segments is None:
            raise UnsupportedPlotContent("Dimension text requires resolved plot geometry.")
        for start, end in segments:
            _chain(
                extent,
                [start, end],
                mapping,
                to_iu(item.style.thickness / 2) if policy.stroke_mode == "visible_ink" else 0,
            )
        if item.text is not None:
            surrogate = PcbText.model_validate(item.text.model_dump())
            _text(
                extent,
                replace(entry, item=surrogate, footprint=None, to_board=identity_map),
                policy,
                context,
            )
    else:
        raise UnsupportedPlotContent(f"Unsupported selected plot content: {type(item).__name__}")


def _drill_marks(extent: Extent, board: Board, layers: set[str]) -> None:
    def add(position: tuple[float, float], width: float, height: float, angle: float) -> None:
        radians = math.radians(angle)
        radius = min(width, height) / 2
        half_line = abs(width - height) / 2
        if height > width:
            radians += math.pi / 2
        dx = abs(math.cos(radians)) * half_line + radius
        dy = abs(math.sin(radians)) * half_line + radius
        x, y = position
        extent.add(point_to_iu((x - dx, y - dy)))
        extent.add(point_to_iu((x + dx, y + dy)))

    def clamped(drill: float, size: float) -> float:
        upper = to_iu(size) - 1
        if upper < 1:
            raise UnsupportedPlotContent("Drill mark pad dimensions must be positive.")
        return max(1, min(to_iu(drill), upper)) / 1e6

    for track in board.tracks:
        if not isinstance(track, Via) or not layers.intersection(via_layers(track, board)):
            continue
        if track.drill <= 0:
            continue
        width = clamped(track.drill, via_diameter_mm(track, "F.Cu", DEFAULT_VIA_DIAMETER))
        add(track.at, width, width, 0)
    for footprint in board.footprints:
        mapping = footprint_point_map(footprint)
        for pad in footprint.pads:
            drill = pad.drill
            if drill is None or drill.width <= 0:
                continue
            size = pad_layer_geometry(pad, "F.Cu").size
            width = clamped(drill.width, size[0])
            height = clamped(drill.height or drill.width, size[1]) if drill.oval else width
            add(mapping(pad.position), width, height, pad.angle)


def _selected_items(board: Board, layers: Collection[str]) -> Iterator[RenderItem]:
    wanted = set(layers)
    grouped = render_items_by_layer(board, list(dict.fromkeys(layers)), KICAD_CONTENT)
    for entries in grouped.values():
        for entry in entries:
            yield entry
    extra: list[object] = [*board.trailing_drawings, *board.targets]
    extra.extend(zone for footprint in board.footprints for zone in footprint.zones)
    for item in extra:
        for layer in set(item_layers(item)) & wanted:
            entry = RenderItem(
                item=item,
                layer=layer,
                layers=(layer,),
                footprint=None,
                uuid=getattr(item, "id", None),
                to_board=identity_map,
            )
            yield entry


def plot_content_bbox(
    board: Board,
    layers: Collection[str],
    *,
    policy: PlotBoundsPolicy,
    text_context: PlotTextContext | None = None,
) -> BoundingBox | None:
    """Return selected content bounds in board millimeters, with downward-positive Y.

    Unsupported selected content raises instead of producing an incomplete box.
    This candidate is not yet qualified as a replacement for KiCad DXF bounds.
    The function does not mutate the board or access files or subprocesses.
    """
    if policy.stroke_mode not in ("dxf_centerline", "visible_ink"):
        raise ValueError("Unknown plot stroke mode.")
    text_context = text_context or PlotTextContext()
    wanted = set(layers)
    extent = Extent()
    for entry in _selected_items(board, layers):
        _measure(extent, entry, board, policy, text_context)
    if policy.actual_drill_marks and wanted:
        _drill_marks(extent, board, wanted)
    return extent.to_bbox()


__all__ = [
    "PlotBoundsPolicy",
    "PlotFontRequest",
    "PlotTextContext",
    "UnsupportedPlotContent",
    "plot_content_bbox",
    "plot_font_requirements",
]
