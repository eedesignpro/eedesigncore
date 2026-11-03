"""A per-net summary of two boards: which pads each net reaches, and its routing.

This reads the net names stored on pads, tracks, vias, and zones. It does not
rebuild copper connections from geometry, so a track that touches the wrong pad
but carries the right net name is not found here.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field

from eedesigncore.common.kimath import calc_arc_center_double
from eedesigncore.common.models import Property
from eedesigncore.common.transform import PCB_IU_PER_MM, point_to_iu
from eedesigncore.design_changes.records import DesignChange
from eedesigncore.pcb.models import Board, TrackArc, TrackSegment, Via


@dataclass
class _Net:
    # Keyed by footprint identifier and pad number; the value is "Reference.pad".
    pads: dict[tuple[str, str], str] = field(default_factory=dict)
    segments: int = 0
    arcs: int = 0
    vias: int = 0
    zones: int = 0
    length: float = 0.0

    def routing(self) -> dict[str, float | int]:
        return {
            "segments": self.segments,
            "arcs": self.arcs,
            "vias": self.vias,
            "zones": self.zones,
            "track_length_mm": round(self.length, 6),
        }

    def summary(self) -> dict[str, object]:
        return {"pads": self.pad_names(), **self.routing()}

    def pad_names(self) -> list[str]:
        return sorted(self.pads.values())


def _arc_length(
    start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]
) -> float:
    """Length of a three-point arc, about KiCad's own centre (``CalcArcCenter``)."""
    if math.dist(start, mid) == 0.0 or math.dist(mid, end) == 0.0:
        return math.dist(start, end)
    centre = calc_arc_center_double(point_to_iu(start), point_to_iu(mid), point_to_iu(end))
    ux, uy = centre[0] / PCB_IU_PER_MM, centre[1] / PCB_IU_PER_MM
    if not (math.isfinite(ux) and math.isfinite(uy)):
        return math.dist(start, end)
    radius = math.dist((ux, uy), start)

    def angle(point: tuple[float, float]) -> float:
        return math.atan2(point[1] - uy, point[0] - ux)

    to_mid = (angle(mid) - angle(start)) % math.tau
    to_end = (angle(end) - angle(start)) % math.tau
    sweep = to_end if to_mid <= to_end else math.tau - to_end
    return radius * sweep


def _reference(properties: list[Property]) -> str:
    for prop in properties:
        if prop.name == "Reference" and prop.value:
            return prop.value
    return "?"


def _nets(board: Board) -> dict[str, _Net]:
    nets: dict[str, _Net] = defaultdict(_Net)
    for footprint in board.footprints:
        reference = _reference(footprint.properties)
        owner = footprint.uuid or reference
        for pad in footprint.pads:
            if pad.net_name:
                nets[pad.net_name].pads[(owner, pad.number)] = f"{reference}.{pad.number}"
    for track in board.tracks:
        if not track.net:
            continue
        net = nets[track.net]
        if isinstance(track, Via):
            net.vias += 1
        elif isinstance(track, TrackSegment):
            net.segments += 1
            net.length += math.dist(track.start, track.end)
        elif isinstance(track, TrackArc):
            net.arcs += 1
            net.length += _arc_length(track.start, track.mid, track.end)
    for zone in board.zones:
        name = zone.net_name or zone.net
        if isinstance(name, str) and name:
            nets[name].zones += 1
    return dict(nets)


def _record(name: str, field: str, before: object, after: object) -> DesignChange:
    return DesignChange(
        category="electrical",
        kind="added" if before is None else "removed" if after is None else "changed",
        item_type="Net",
        item=f"net {name}",
        identity=name,
        field=field,
        before=None if before is None else json.dumps(before, sort_keys=True),
        after=None if after is None else json.dumps(after, sort_keys=True),
        path=("nets", name, field) if field else ("nets", name),
    )


def _routing_differs(old: _Net, new: _Net, tolerance: float) -> bool:
    a, b = old.routing(), new.routing()
    return any(
        abs(float(a[key]) - float(b[key])) > (tolerance if key == "track_length_mm" else 0.0)
        for key in a
    )


def net_changes(before: Board, after: Board, tolerance: float = 0.0) -> list[DesignChange]:
    """Return one record per net whose name, pads, or routing changed.

    Pads match by footprint identifier and pad number, so a new Reference alone
    changes no net. The record prints pads as ``Reference.pad``.

    A net that keeps exactly the same non-empty pad set under a new name is one
    ``name`` change, and the rest of its summary is compared under the new name.
    """
    old, new = _nets(before), _nets(after)
    renamed: dict[str, str] = {}
    by_pads = {frozenset(net.pads): name for name, net in new.items() if net.pads}
    for name, net in old.items():
        target = by_pads.get(frozenset(net.pads)) if net.pads else None
        if target is not None and target != name and name not in new and target not in old:
            renamed[name] = target

    changes: list[DesignChange] = []
    for name in sorted(old.keys() | new.keys() | set()):
        if name in renamed.values():
            continue
        target = renamed.get(name, name)
        if name in renamed:
            changes.append(_record(name, "name", name, target))
        if name not in old:
            changes.append(_record(name, "", None, new[name].summary()))
            continue
        if target not in new:
            changes.append(_record(name, "", old[name].summary(), None))
            continue
        if old[name].pads.keys() != new[target].pads.keys():
            changes.append(_record(target, "pads", old[name].pad_names(), new[target].pad_names()))
        if _routing_differs(old[name], new[target], tolerance):
            changes.append(_record(target, "routing", old[name].routing(), new[target].routing()))
    return changes


__all__ = ["net_changes"]
