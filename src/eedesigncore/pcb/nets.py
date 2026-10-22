"""Net renaming across every item that references a net (work order P5).

KiCad 10 references a net by NAME. There is no net table, so renaming a net means
rewriting every reference, and a reference this module misses leaves the board
electrically split between the old name and the new one.

Four kinds of item hold a net name: a track, arc or via; a zone; a footprint pad;
and a copper graphic. Two of them also hold a LEGACY numeric net code beside the
name, ``Zone.net`` and ``Pad.net_code``. A stale code beside a renamed name is
exactly the mixed reference this operation exists to prevent, so a rename clears
it.

This module lives under ``pcb/`` because it reaches ``pcb.Board``,
``mod.Footprint`` and the shared ``common/pcb_family`` items at once. ``common``
may not import a format sub-package (ADR-015, ADR-035).
"""

from __future__ import annotations

from eedesigncore.common.pcb_family.board_items import Zone
from eedesigncore.mod.models import Footprint, Pad
from eedesigncore.pcb.models import Board, TrackArc, TrackItem, TrackSegment, Via


def _rename_track(track: TrackItem, mapping: dict[str, str]) -> None:
    """A track, arc or via holds its net name in ``net``, which is a string."""
    if track.net is not None and track.net in mapping:
        track.net = mapping[track.net]


def _rename_zone(zone: Zone, mapping: dict[str, str]) -> None:
    """A zone holds a name and, on an older file, a numeric code beside it.

    ``Zone.net`` is the legacy code and it emits a two-token form when it is not
    ``None``. The code cannot survive a rename, so it goes.
    """
    if zone.net_name is not None and zone.net_name in mapping:
        zone.net_name = mapping[zone.net_name]
        zone.net = None


def _rename_pad(pad: Pad, mapping: dict[str, str]) -> None:
    """A pad holds a name and, on an older file, a numeric code beside it."""
    if pad.net_name is not None and pad.net_name in mapping:
        pad.net_name = mapping[pad.net_name]
        pad.net_code = None


def _rename_graphic(item: object, mapping: dict[str, str]) -> None:
    """A copper graphic can carry a net name; every other graphic carries none."""
    net_name = getattr(item, "net_name", None)
    if isinstance(net_name, str) and net_name in mapping:
        item.net_name = mapping[net_name]  # type: ignore[attr-defined]


def rename_footprint_nets(footprint: Footprint, mapping: dict[str, str]) -> None:
    """Apply *mapping* to every net reference inside *footprint*, in place.

    A name absent from the mapping is left alone, so a partial mapping renames
    only what it names.
    """
    for pad in footprint.pads:
        _rename_pad(pad, mapping)
    for zone in footprint.zones:
        _rename_zone(zone, mapping)
    for item in footprint.drawings:
        _rename_graphic(item, mapping)


def rename_nets(board: Board, mapping: dict[str, str]) -> None:
    """Apply *mapping* to every net reference on *board*, in place.

    *mapping* is a general old-name to new-name map. Suffixing, prefixing and
    every other naming scheme is the caller's policy; this operation only
    applies the map it is given. A name absent from the mapping is left alone.

    Covered: tracks, arcs and vias; board zones and footprint zones; footprint
    pads; and copper graphics at board level and inside a footprint. The legacy
    numeric code beside a renamed name is cleared, so no item is left holding
    one name and one stale code.

    ``board.net_names`` reads back the result.
    """
    for track in board.tracks:
        if isinstance(track, TrackSegment | TrackArc | Via):
            _rename_track(track, mapping)
    for zone in board.zones:
        _rename_zone(zone, mapping)
    for item in (*board.drawings, *board.trailing_drawings):
        _rename_graphic(item, mapping)
    for footprint in board.footprints:
        rename_footprint_nets(footprint, mapping)


__all__ = ["rename_footprint_nets", "rename_nets"]
