"""Flip unrouted board content about one explicit board datum."""

from __future__ import annotations

from math import isfinite

from eedesigncore.common.kimath import normalize_angle
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoly,
    PcbRect,
    PcbText,
)
from eedesigncore.common.pcb_family.transform import drop_render_cache, move_points
from eedesigncore.common.transform import FLIP_AXIS, FlipDirection, Mirror, Point
from eedesigncore.pcb.layers import board_layer_opposites, flip_board_layer
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.transform import flip_footprint

_SUPPORTED_DRAWINGS = (PcbArc, PcbCircle, PcbLine, PcbPoly, PcbRect, PcbText)
_UNSUPPORTED_PAD_FIELDS = (
    "padstack",
    "primitives",
    "chamfer_positions",
    "backdrill",
    "tertiary_drill",
    "front_post_machining",
    "back_post_machining",
    "zone_layer_connections",
    "tenting_front",
    "tenting_back",
    "thermal_spoke_angle",
)


def _preflight(board: Board) -> None:
    for name in ("tracks", "zones", "points", "generators", "targets"):
        if getattr(board, name):
            raise ValueError(f"flip_board does not support {name} yet.")
    for item in (*board.drawings, *board.trailing_drawings):
        if not isinstance(item, _SUPPORTED_DRAWINGS):
            raise ValueError(f"flip_board does not support {type(item).__name__} yet.")
    for footprint in board.footprints:
        if footprint.at is None:
            raise ValueError("flip_board requires placed footprints.")
        if footprint.zones or footprint.points:
            raise ValueError("flip_board does not support footprint zones or points yet.")
        for drawing in footprint.drawings:
            if not isinstance(drawing, _SUPPORTED_DRAWINGS):
                raise ValueError(f"flip_board does not support {type(drawing).__name__} yet.")
        for pad in footprint.pads:
            if pad.shape.value == "custom":
                raise ValueError("flip_board does not support custom pads yet.")
            for name in _UNSUPPORTED_PAD_FIELDS:
                if getattr(pad, name) is not None:
                    raise ValueError(f"flip_board does not support pad {name} yet.")


def flip_board(
    board: Board,
    *,
    center: Point,
    direction: FlipDirection = "left_right",
) -> None:
    """Reflect an unrouted board and swap sides about one explicit datum.

    Supports placed footprints with basic pads and line, arc, circle, polygon,
    rectangle, and text drawings. Rejects tracks, vias, zones, dimensions,
    images, tables, generators, points, targets, and advanced pad features.
    Validation and changes run on a detached copy before replacement. No file
    is saved. UUIDs, nets, groups, setup, and model records remain unchanged.
    Nested object references must be obtained again after this operation.
    """
    if direction not in FLIP_AXIS:
        raise ValueError("direction must be 'left_right' or 'top_bottom'.")
    if len(center) != 2 or not all(isfinite(value) for value in center):
        raise ValueError("center must contain two finite board coordinates.")
    _preflight(board)
    staged = board.model_copy(deep=True)
    opposite = board_layer_opposites(staged)
    reflect = Mirror(FLIP_AXIS[direction], center)
    sided_layers = {
        row.canonical_name for row in staged.layers if row.layer_type in ("front", "back")
    }
    for footprint in staged.footprints:
        flip_footprint(footprint, direction=direction, center=center, board=staged)
    for item in (*staged.drawings, *staged.trailing_drawings):
        move_points(item, reflect)
        item.layer = flip_board_layer(staged, item.layer, opposite)
        if (
            isinstance(item, PcbArc | PcbCircle | PcbLine | PcbPoly | PcbRect)
            and item.layers is not None
        ):
            item.layers = [flip_board_layer(staged, name, opposite) for name in item.layers]
        if isinstance(item, PcbText):
            item.angle = normalize_angle(
                -item.angle if direction == "left_right" else 180.0 - item.angle
            )
            if item.layer.startswith(("F.", "B.")) or item.layer in sided_layers:
                if item.attributes is None:
                    item.attributes = TextAttributes()
                item.attributes.mirror = not item.attributes.mirror
            drop_render_cache(item)
        elif isinstance(item, PcbArc):
            item.start, item.end = item.end, item.start
    board.footprints = staged.footprints
    board.drawings = staged.drawings
    board.trailing_drawings = staged.trailing_drawings
