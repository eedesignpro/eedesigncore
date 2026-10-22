"""Bounding boxes of board content (work order P2).

The box rule, with the reason for every inclusion and every exclusion, is in the
docstring of ``common/pcb_family/bbox.py``. Read it before you trust a number:
a text with no cached glyph outlines contributes its anchor point and nothing
else, because this library loads no font.

Most footprint children store local coordinates and need the footprint placement.
Points, zones, dimensions, images, and barcodes already store board coordinates.
See ``context/audit/footprint-transform-frame-2026-09-02.md`` for the KiCad source evidence.
"""

from __future__ import annotations

from collections.abc import Container, Iterable

from eedesigncore.common.bbox import BoundingBox, Extent
from eedesigncore.common.kimath import rotate_point_iu
from eedesigncore.common.layers import on_layers
from eedesigncore.common.pcb_family.bbox import (
    IuTransform,
    add_item_extent,
    half_width_iu,
    identity_iu,
)
from eedesigncore.common.transform import PointIU, point_to_iu, to_iu
from eedesigncore.mod.models import Footprint, Pad, PadPrimitive
from eedesigncore.mod.pad_geometry import (
    pad_hole_extent_mm,
    pad_layer_geometry,
    pad_shape_position,
    pad_unique_layers,
)
from eedesigncore.pcb.models import (
    Board,
    PcbGenerator,
    PcbTarget,
    TrackArc,
    TrackItem,
    TrackSegment,
    Via,
)


def _child_transform(footprint: Footprint) -> IuTransform:
    """Return the map from *footprint*-local coordinates to board coordinates."""
    if footprint.at is None:
        return identity_iu
    origin = point_to_iu(footprint.at.position)
    angle = footprint.at.angle

    def to_board(point: PointIU) -> PointIU:
        turned = rotate_point_iu(point, angle)
        return (turned[0] + origin[0], turned[1] + origin[1])

    return to_board


def _add_pad(
    extent: Extent,
    pad: Pad,
    transform: IuTransform,
    *,
    include_width: bool,
    layers: Container[str] | None,
) -> None:
    """Add a pad: its copper on EVERY padstack layer, its hole, and its primitives.

    ``PAD::BuildEffectiveShapes``, ``pad.cpp:1093-1136``, is the rule. It merges
    the bounding box of the copper on every unique padstack layer, and then
    merges the HOLE. Three consequences, each of which a copper-only measurement
    of the top-level size gets wrong:

    1. **An inner layer can be the widest.** A padstack gives one pad a different
       size per layer, and nothing says the front is the largest. Walking only
       ``pad.size`` measures the front and misses the rest.
    2. **A drill can be wider than its copper.** KiCad merges the hole shape, so
       the hole grows the extent. A mechanical slot in a small pad is the common
       case.
    3. **A shape offset moves the copper.** The ``(offset …)`` inside
       ``(drill …)`` shifts the copper away from the pad position, so the
       envelope is not centred on ``pad.position``.

    The hole sits at the pad position, and the offset does not move it. So the
    copper and the hole are measured about different centres.
    """
    if not on_layers(pad, layers):
        return

    for layer in pad_unique_layers(pad):
        geometry = pad_layer_geometry(pad, layer)
        centre = point_to_iu(pad_shape_position(pad, geometry))
        half_x, half_y = to_iu(geometry.size[0] / 2.0), to_iu(geometry.size[1] / 2.0)
        corners = ((-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y))
        for corner in corners:
            turned = rotate_point_iu(corner, pad.angle)
            extent.add(transform((centre[0] + turned[0], centre[1] + turned[1])))

    hole = pad_hole_extent_mm(pad)
    if hole is not None:
        anchor = point_to_iu(pad.position)
        for offset in hole:
            corner = point_to_iu(offset)
            extent.add(transform((anchor[0] + corner[0], anchor[1] + corner[1])))

    for primitive in pad.primitives or ():
        _add_primitive(extent, primitive, pad, transform, include_width=include_width)


def _add_primitive(
    extent: Extent,
    primitive: PadPrimitive,
    pad: Pad,
    transform: IuTransform,
    *,
    include_width: bool,
) -> None:
    """Add one custom-pad primitive, measured in the pad frame then mapped.

    A primitive is stored relative to the pad anchor and turned with the pad, so
    the pad angle and the pad position both apply before the caller's transform.
    """
    from eedesigncore.common.bbox import arc_extreme_points_iu

    anchor = point_to_iu(pad.position)
    pad_width = half_width_iu(primitive, include_width)

    def in_pad_frame(point: PointIU) -> PointIU:
        turned = rotate_point_iu(point, pad.angle)
        return transform((anchor[0] + turned[0], anchor[1] + turned[1]))

    points: list[PointIU] = []
    start = getattr(primitive, "start", None)
    end = getattr(primitive, "end", None)
    center = getattr(primitive, "center", None)
    mid = getattr(primitive, "mid", None)
    pts = getattr(primitive, "pts", None)

    if start is not None and mid is not None and end is not None:
        points.extend(
            arc_extreme_points_iu(point_to_iu(start), point_to_iu(mid), point_to_iu(end))
        )
    elif center is not None and end is not None:
        span_x, span_y = end[0] - center[0], end[1] - center[1]
        radius = to_iu((span_x * span_x + span_y * span_y) ** 0.5)
        origin = point_to_iu(center)
        points.extend(
            [
                (origin[0] - radius, origin[1] - radius),
                (origin[0] + radius, origin[1] + radius),
            ]
        )
    else:
        if start is not None:
            points.append(point_to_iu(start))
        if end is not None:
            points.append(point_to_iu(end))
        for element in pts or ():
            if isinstance(element, tuple):
                points.append(point_to_iu(element))
            else:
                points.extend(
                    arc_extreme_points_iu(
                        point_to_iu(element.start),
                        point_to_iu(element.mid),
                        point_to_iu(element.end),
                    )
                )

    for point in points:
        extent.add(in_pad_frame(point), pad_width)


def _add_track(
    extent: Extent,
    track: TrackItem,
    *,
    include_width: bool,
    layers: Container[str] | None,
) -> None:
    """Add a track-stream item: a segment, an arc, or a via."""
    from eedesigncore.common.bbox import arc_extreme_points_iu

    if not on_layers(track, layers):
        return
    if isinstance(track, Via):
        extent.add(point_to_iu(track.at), to_iu(track.size / 2.0))
        return
    pad = to_iu(track.width / 2.0) if include_width and track.width > 0 else 0
    if isinstance(track, TrackSegment):
        extent.add(point_to_iu(track.start), pad)
        extent.add(point_to_iu(track.end), pad)
    else:
        for point in arc_extreme_points_iu(
            point_to_iu(track.start), point_to_iu(track.mid), point_to_iu(track.end)
        ):
            extent.add(point, pad)


def _add_footprint(
    extent: Extent,
    footprint: Footprint,
    *,
    include_width: bool,
    layers: Container[str] | None,
) -> None:
    """Add footprint children, converting only local coordinates to board coordinates."""
    from eedesigncore.common.pcb_family.transform import BOARD_FRAME_TYPES

    to_board = _child_transform(footprint)
    for item in footprint.drawings:
        # A dimension, image or barcode inside a footprint is already in board
        # coordinates (see the frame audit), so it takes no child mapping.
        mapping = identity_iu if isinstance(item, BOARD_FRAME_TYPES) else to_board
        add_item_extent(extent, item, mapping, include_width=include_width, layers=layers)
    for field in footprint.properties:
        add_item_extent(extent, field, to_board, include_width=include_width, layers=layers)
    for point in footprint.points:
        add_item_extent(extent, point, identity_iu, include_width=include_width, layers=layers)
    for zone in footprint.zones:
        add_item_extent(extent, zone, identity_iu, include_width=include_width, layers=layers)
    for pad in footprint.pads:
        _add_pad(extent, pad, to_board, include_width=include_width, layers=layers)


def board_bbox(
    board: Board,
    *,
    layers: Container[str] | None = None,
    include_width: bool = True,
) -> BoundingBox | None:
    """Return the extent of every item on *board*, in millimetres.

    ``layers`` restricts the measurement to items that name one of those layers,
    which is how a caller asks for the board outline: pass ``{"Edge.Cuts"}``.
    ``include_width`` adds half the stroke or track width to a stroked item.

    Returns ``None`` when nothing was measured, which happens for an empty board
    and for a layer filter that matches nothing.
    """
    extent = Extent()
    for footprint in board.footprints:
        _add_footprint(extent, footprint, include_width=include_width, layers=layers)
    for item in (*board.drawings, *board.trailing_drawings, *board.points, *board.zones):
        add_item_extent(extent, item, include_width=include_width, layers=layers)
    for track in board.tracks:
        _add_track(extent, track, include_width=include_width, layers=layers)
    for target in board.targets:
        if on_layers(target, layers):
            extent.add(point_to_iu(target.position), to_iu(target.size / 2.0))
    return extent.to_bbox()


def footprint_bbox(
    footprint: Footprint,
    *,
    layers: Container[str] | None = None,
    include_width: bool = True,
) -> BoundingBox | None:
    """Return the extent of one *footprint*, in millimetres.

    A placed footprint is measured in board coordinates. A library footprint,
    which carries no ``at`` placement, is measured in its own frame.
    """
    extent = Extent()
    _add_footprint(extent, footprint, include_width=include_width, layers=layers)
    return extent.to_bbox()


def items_bbox(
    items: Iterable[object],
    *,
    layers: Container[str] | None = None,
    include_width: bool = True,
) -> BoundingBox | None:
    """Return the extent of a collection of BOARD-frame items, in millimetres.

    Accepts anything :func:`board_bbox` measures: drawings, points, zones,
    track-stream items, targets, placed footprints, and pads.

    A PAD is measured in the frame it is handed in. A pad taken off a placed
    footprint stores a FOOTPRINT-RELATIVE position, so measuring it alone gives
    its extent in the footprint frame, not on the board. Pass the footprint to
    measure it where it sits.

    Raises:
        TypeError: when an item is not one this dispatch covers.
    """
    extent = Extent()
    for item in items:
        if isinstance(item, Footprint):
            _add_footprint(extent, item, include_width=include_width, layers=layers)
        elif isinstance(item, Pad):
            _add_pad(extent, item, identity_iu, include_width=include_width, layers=layers)
        elif isinstance(item, TrackSegment | TrackArc | Via):
            _add_track(extent, item, include_width=include_width, layers=layers)
        elif isinstance(item, PcbTarget):
            if on_layers(item, layers):
                extent.add(point_to_iu(item.position), to_iu(item.size / 2.0))
        elif isinstance(item, PcbGenerator):
            continue  # a generator restates the geometry of the items it owns
        else:
            add_item_extent(extent, item, include_width=include_width, layers=layers)
    return extent.to_bbox()


__all__ = ["board_bbox", "footprint_bbox", "items_bbox"]
