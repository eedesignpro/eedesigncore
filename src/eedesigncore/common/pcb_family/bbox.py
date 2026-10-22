"""Per-item-type extents for the shared PCB-family models (work order P2).

What the box covers, and what it does not, is a stated rule rather than a guess.
KiCad's own box is not reachable from a file alone: the extent of a text depends
on glyph metrics from a font this library does not load.

Included:

* shape geometry, with the TRUE extent of an arc, not just its stored points;
* half the stroke width, when the caller asks for it;
* the cached glyph outlines of a text that carries a ``render_cache``, which are
  real coordinates and give that text a real extent;
* the turned ``size`` rectangle of a barcode.

Excluded, each for a stated reason:

* the glyph extent of a text with NO cache. The anchor point counts, the glyphs
  do not. No font, no metrics.
* the pixel extent of a reference image. The size lives inside the encoded image
  data, which this library stores but does not decode.
* the curve of a Bezier. The four control points bound it, so the box holds the
  curve but is not tight.

Every item is measured in internal units and mapped through a caller-supplied
transform, which is how a footprint child reaches board coordinates.
"""

from __future__ import annotations

from collections.abc import Callable, Container

from eedesigncore.common.bbox import Extent, arc_extreme_points_iu
from eedesigncore.common.layers import on_layers
from eedesigncore.common.models import Property, PtsArc, PtsElement, RenderCache
from eedesigncore.common.pcb_family.board_items import (
    Barcode,
    Dimension,
    DimensionText,
    ReferenceImage,
    Table,
    TableCell,
    Zone,
)
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
)
from eedesigncore.common.transform import Point, PointIU, point_to_iu, to_iu

# Maps a point from the frame the item stores into the frame being measured.
IuTransform = Callable[[PointIU], PointIU]


def identity_iu(point: PointIU) -> PointIU:
    """The transform for an item already in the frame being measured."""
    return point


def half_width_iu(item: object, include_width: bool) -> int:
    """Half the stroke width of *item* in internal units, or zero.

    A zero stroke width means "the board default line width" in KiCad, which
    this library cannot resolve from the item alone, so zero adds nothing.
    """
    if not include_width:
        return 0
    stroke = getattr(item, "stroke", None)
    width = getattr(stroke, "width", None)
    if width is None:
        width = getattr(item, "width", None)
    if not isinstance(width, int | float) or width <= 0:
        return 0
    return to_iu(float(width) / 2.0)


def _add(extent: Extent, point: Point, transform: IuTransform, pad: int = 0) -> None:
    extent.add(transform(point_to_iu(point)), pad)


def _add_arc(
    extent: Extent,
    start: Point,
    mid: Point,
    end: Point,
    transform: IuTransform,
    pad: int = 0,
) -> None:
    """Add a whole arc: its stored points and every axis crossing in the sweep.

    The transform is rigid, so the extremes of the mapped arc are the mapped
    extremes of the arc.
    """
    for point in arc_extreme_points_iu(point_to_iu(start), point_to_iu(mid), point_to_iu(end)):
        extent.add(transform(point), pad)


def _add_pts(extent: Extent, pts: list[PtsElement], transform: IuTransform, pad: int = 0) -> None:
    for element in pts:
        if isinstance(element, PtsArc):
            _add_arc(extent, element.start, element.mid, element.end, transform, pad)
        else:
            _add(extent, element, transform, pad)


def _add_render_cache(extent: Extent, item: object, transform: IuTransform) -> None:
    cache = getattr(item, "render_cache", None)
    if not isinstance(cache, RenderCache):
        return
    for polygon in cache.polygons:
        _add_pts(extent, polygon.pts, transform)


def _add_barcode(extent: Extent, barcode: Barcode, transform: IuTransform) -> None:
    """Add the four turned corners of a barcode's ``size`` rectangle."""
    from eedesigncore.common.kimath import rotate_point_iu

    anchor = point_to_iu(barcode.position)
    half_x, half_y = to_iu(barcode.size[0] / 2.0), to_iu(barcode.size[1] / 2.0)
    for corner in ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)):
        turned = rotate_point_iu(corner, barcode.angle)
        extent.add(transform((anchor[0] + turned[0], anchor[1] + turned[1])))


def add_item_extent(  # noqa: C901, PLR0912
    extent: Extent,
    item: object,
    transform: IuTransform = identity_iu,
    *,
    include_width: bool = True,
    layers: Container[str] | None = None,
) -> None:
    """Add the extent of one PCB-family item to *extent*.

    Raises:
        TypeError: when *item* is not a PCB-family item this dispatch covers.
    """
    if not on_layers(item, layers):
        return
    pad = half_width_iu(item, include_width)

    if isinstance(item, PcbText):
        _add(extent, item.position, transform)
        _add_render_cache(extent, item, transform)
    elif isinstance(item, PcbLine | PcbRect):
        _add(extent, item.start, transform, pad)
        _add(extent, item.end, transform, pad)
    elif isinstance(item, PcbCircle):
        span_x = item.end[0] - item.center[0]
        span_y = item.end[1] - item.center[1]
        radius = to_iu((span_x * span_x + span_y * span_y) ** 0.5)
        _add(extent, item.center, transform, radius + pad)
    elif isinstance(item, PcbArc):
        _add_arc(extent, item.start, item.mid, item.end, transform, pad)
    elif isinstance(item, PcbPoly | PcbCurve):
        _add_pts(extent, item.pts, transform, pad)
    elif isinstance(item, PcbTextBox):
        border = pad if item.border_enabled else 0
        if item.start is not None:
            _add(extent, item.start, transform, border)
        if item.end is not None:
            _add(extent, item.end, transform, border)
        _add_pts(extent, item.pts, transform, border)
        _add_render_cache(extent, item, transform)
    elif isinstance(item, TableCell):
        _add(extent, item.start, transform)
        _add(extent, item.end, transform)
    elif isinstance(item, Table):
        for cell in item.cells:
            add_item_extent(extent, cell, transform, include_width=include_width)
    elif isinstance(item, Dimension):
        _add_pts(extent, item.pts, transform)
        if item.text is not None:
            add_item_extent(extent, item.text, transform, include_width=include_width)
    elif isinstance(item, DimensionText):
        _add(extent, item.position, transform)
        _add_render_cache(extent, item, transform)
    elif isinstance(item, Barcode):
        _add_barcode(extent, item, transform)
    elif isinstance(item, ReferenceImage | PcbPoint | Property):
        _add(extent, item.position, transform)
        _add_render_cache(extent, item, transform)
    elif isinstance(item, Zone):
        for outline in item.polygons:
            _add_pts(extent, outline.pts, transform)
        for filled in item.filled_polygons:
            _add_pts(extent, filled.pts, transform)
    elif isinstance(item, PcbGroup):
        pass  # a group is a member list; it stores no geometry
    else:
        raise TypeError(f"add_item_extent does not cover {type(item).__name__}")


__all__ = [
    "IuTransform",
    "add_item_extent",
    "half_width_iu",
    "identity_iu",
]
