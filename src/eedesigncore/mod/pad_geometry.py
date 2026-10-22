"""Resolve a pad onto one layer, and measure what it covers.

A pad is the only board item whose geometry changes per layer, because it
carries a padstack. Two callers need that resolution and they must not disagree:
the renderer draws the copper, and the bounding box measures it.

This module sits in ``mod`` rather than in ``common`` or ``render`` for one
reason. It needs the ``Pad`` model, which lives here, and ``mod`` imports
neither ``pcb`` nor ``render``. Both consumers can therefore reach it with no
import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eedesigncore.common.kimath import rotate_point_iu
from eedesigncore.common.pcb_family.enums import ChamferPosition, CustomAnchor, PadShape
from eedesigncore.common.pcb_family.footprint_scoped import PadstackLayer, PadstackMode
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.mod.models import Pad

#: A map from one frame to board millimeters.
PointMap = Callable[[tuple[float, float]], tuple[float, float]]

#: A representative inner copper layer.
#:
#: A ``front_inner_back`` padstack gives every inner layer the same geometry, so
#: one name stands for all of them.
INNER_LAYER = "In1.Cu"


def identity_map(point: tuple[float, float]) -> tuple[float, float]:
    """Return *point* unchanged. A library pad has no host to map from."""
    return point


@dataclass(frozen=True)
class PadLayerGeometry:
    """What a pad shows on ONE layer, after its padstack resolves.

    Every field is already the value KiCad would use for that layer, so a caller
    never re-reads the padstack. ``angle`` is board-absolute, as it is on disk,
    and stays on the pad rather than here.
    """

    shape: PadShape
    size: tuple[float, float]
    offset: tuple[float, float]
    rect_delta: tuple[float, float]
    roundrect_rratio: float
    chamfer_ratio: float
    chamfer_positions: tuple[ChamferPosition, ...]
    primitives: tuple[Any, ...] = ()
    custom_anchor: CustomAnchor = CustomAnchor.circle

    @property
    def is_chamfered(self) -> bool:
        """True when KiCad would call this shape ``CHAMFERED_RECT``.

        The token on disk is ``roundrect`` either way. KiCad decides on the
        CHAMFER LIST alone: a non-empty ``(chamfer …)`` promotes the shape
        (``pcb_io_kicad_sexpr_parser.cpp:6240-6241``). A chamfer ratio with no
        corners named stays a plain rounded rectangle.
        """
        return bool(self.chamfer_positions)


def padstack_entry(pad: Pad, layer: str) -> PadstackLayer | None:
    """Return the padstack override that governs *layer*, or ``None`` for the pad's own.

    ``PADSTACK::CopperLayer``, ``padstack.cpp:1206-1239``. Front layers always
    resolve to the pad's own top-level fields, because that is where KiCad writes
    them: ``front_inner_back`` mode emits ``(layer "Inner")`` and
    ``(layer "B.Cu")`` only, and ``custom`` mode skips ``F_Cu`` explicitly
    (``pcb_io_kicad_sexpr.cpp:2157-2180``).
    """
    padstack = pad.padstack
    if padstack is None or padstack.mode is PadstackMode.normal:
        return None

    by_name = {entry.layer_name: entry for entry in padstack.layers}

    if padstack.mode is PadstackMode.front_inner_back:
        if layer.startswith("F."):
            return None
        if layer.startswith("B."):
            return by_name.get("B.Cu")
        return by_name.get("Inner")

    # Custom mode names every copper layer it overrides.
    if layer in by_name:
        return by_name[layer]
    if layer.startswith("B.") and "B.Cu" in by_name:
        return by_name["B.Cu"]
    return None


def pad_layer_geometry(pad: Pad, layer: str) -> PadLayerGeometry:
    """Resolve *pad* onto *layer*, applying its padstack overrides.

    A field the override leaves unset falls back to the pad's own value, which is
    how KiCad's per-layer property blocks behave: the block carries only what
    differs.
    """
    entry = padstack_entry(pad, layer)

    def pick(name: str, fallback: object) -> Any:
        if entry is None:
            return fallback
        value = getattr(entry, name, None)
        return fallback if value is None else value

    drill_offset = pad.drill.offset if pad.drill is not None else None
    return PadLayerGeometry(
        shape=pick("shape", pad.shape),
        size=pick("size", pad.size),
        offset=pick("offset", drill_offset or (0.0, 0.0)),
        rect_delta=pick("rect_delta", pad.rect_delta or (0.0, 0.0)),
        roundrect_rratio=pick("roundrect_rratio", pad.roundrect_rratio or 0.0),
        chamfer_ratio=pick("chamfer_ratio", pad.chamfer_ratio or 0.0),
        chamfer_positions=tuple(pick("chamfer_positions", pad.chamfer_positions or ())),
        primitives=tuple(pick("primitives", pad.primitives or ())),
        custom_anchor=pick("custom_anchor", pad.custom_anchor or CustomAnchor.circle),
    )


def pad_unique_layers(pad: Pad) -> tuple[str, ...]:
    """Return one layer name per DISTINCT geometry this pad carries.

    ``PADSTACK::ForEachUniqueLayer``, ``padstack.cpp:1241-1258``. A normal
    padstack has one geometry. ``front_inner_back`` has three. ``custom`` has one
    per named layer, plus the pad's own for the front.

    A caller that must cover the whole pad, such as a bounding box, walks these
    rather than guessing that the top-level size is the largest.
    """
    padstack = pad.padstack
    if padstack is None or padstack.mode is PadstackMode.normal:
        return ("F.Cu",)

    if padstack.mode is PadstackMode.front_inner_back:
        return ("F.Cu", INNER_LAYER, "B.Cu")

    named = tuple(dict.fromkeys(entry.layer_name for entry in padstack.layers))
    return ("F.Cu", *named)


def pad_shape_position(
    pad: Pad, geometry: PadLayerGeometry, to_board: PointMap = identity_map
) -> tuple[float, float]:
    """Where the COPPER sits, which is not always where the pad sits.

    A pad stores its position FOOTPRINT-RELATIVE, so *to_board* is the host
    footprint's map. KiCad's parser turns the stored point by the footprint
    orientation and moves it by the footprint position
    (``BOARD_ITEM::SetFPRelativePosition``, ``board_item.cpp:363-374``), which is
    what that map does.

    The pad ANGLE is not relative. The writer emits ``GetOrientation()`` whole
    (``pcb_io_kicad_sexpr.cpp:1686-1690``), so the stored angle is already
    board-absolute and the footprint orientation must not be added to it.

    ``PAD::ShapePos``, ``pad.cpp:1558-1570``: the ``(offset …)`` inside
    ``(drill …)`` moves the COPPER, not the hole, whatever its placement in the
    file suggests. The turn runs in integer internal units, as KiCad's does.
    """
    placed = to_board(pad.position)
    if geometry.offset == (0.0, 0.0):
        return placed

    turned = rotate_point_iu(point_to_iu(geometry.offset), pad.angle)
    origin = point_to_iu(placed)
    return (to_mm(origin[0] + turned[0]), to_mm(origin[1] + turned[1]))


def pad_hole_extent_mm(pad: Pad) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return the axis-aligned extent of the pad's HOLE, or ``None`` when it has none.

    ``PAD::BuildEffectiveShapes``, ``pad.cpp:1114-1136``. KiCad builds the hole as
    a thick segment and merges its bounding box into the pad's. A drill wider
    than the copper therefore GROWS the pad's extent, which is the case a
    copper-only measurement gets wrong.

    A round drill is a segment of zero length. A slot is a segment whose half
    length is the difference between the two radii, turned by the pad angle.

    The hole sits at the pad position. The ``(offset …)`` inside ``(drill …)``
    moves the copper, not the hole.
    """
    drill = pad.drill
    if drill is None or drill.width <= 0.0:
        return None

    half_x = drill.width / 2.0
    half_y = (drill.height / 2.0) if drill.oval and drill.height else half_x

    half_width = half_x if not drill.oval else min(half_x, half_y)
    half_length = (0.0, 0.0) if not drill.oval else (half_x - half_width, half_y - half_width)

    turned = rotate_point_iu(point_to_iu(half_length), pad.angle)
    reach = (to_mm(turned[0]), to_mm(turned[1]))

    return (
        (-abs(reach[0]) - half_width, -abs(reach[1]) - half_width),
        (abs(reach[0]) + half_width, abs(reach[1]) + half_width),
    )


__all__ = [
    "INNER_LAYER",
    "PadLayerGeometry",
    "PointMap",
    "identity_map",
    "pad_hole_extent_mm",
    "pad_layer_geometry",
    "pad_shape_position",
    "pad_unique_layers",
    "padstack_entry",
]
