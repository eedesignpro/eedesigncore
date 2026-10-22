"""Via copper: the diameter on one layer, and whether the via shows there.

A via carries a padstack, exactly as a pad does, so its diameter can differ per
layer. It also carries the unused-layer rule, so a via can name a layer and still
put no copper on it.

KiCad's plot reads both:

- ``diameter = std::max( diameter, via->GetWidth( layer ) )`` over the layers
  being plotted (``plot_board_layers.cpp:747-748``).
- ``if( onCopperLayer && !via->FlashLayer( aLayerMask ) ) continue;``
  (``plot_board_layers.cpp:740-741``).

This renderer draws ONE layer per document, so KiCad's maximum over the plotted
set reduces to the width of that single layer. :class:`ViaDiameter` therefore
offers the per-layer width as the default, and a whole-padstack maximum as the
controlled step past KiCad for a caller who wants one circle per via.
"""

from __future__ import annotations

from enum import Enum

from eedesigncore.common.layers import is_copper_layer_name
from eedesigncore.pcb.models import Via, ViaPadstackLayer
from eedesigncore.render.pads import PadFlash


class ViaDiameter(Enum):
    """How to pick the diameter of a via that carries a padstack.

    A via with no padstack draws the same circle either way, so this changes
    nothing for the great majority of boards.
    """

    #: The width the padstack gives for the layer being drawn.
    #:
    #: This is what KiCad plots. A single-layer export takes the maximum over one
    #: layer, which is that layer's own width.
    per_layer = "per_layer"

    #: The largest width anywhere in the padstack, on every layer.
    #:
    #: KiCad never draws this on a single-layer plot. It is useful for a caller
    #: who wants one circle per via, such as a clearance or keep-out preview,
    #: where the widest ring is the one that matters.
    largest = "largest"


#: What a render uses when the caller says nothing. KiCad's own answer.
DEFAULT_VIA_DIAMETER = ViaDiameter.per_layer


def _padstack_entry(via: Via, layer: str) -> ViaPadstackLayer | None:
    """Return the padstack override for *layer*, or ``None`` for the via's own size.

    The resolution matches a pad's. ``front_inner_back`` mode writes
    ``(layer "Inner")`` and ``(layer "B.Cu")`` only, and ``custom`` mode names
    every copper layer except ``F.Cu``, so a front layer always falls back to the
    via's own ``(size ...)``.
    """
    padstack = via.padstack
    if padstack is None:
        return None

    by_name = {entry.layer_name: entry for entry in padstack.layers}

    if padstack.mode == "front_inner_back":
        if layer.startswith("F."):
            return None
        if layer.startswith("B."):
            return by_name.get("B.Cu")
        return by_name.get("Inner")

    if layer in by_name:
        return by_name[layer]
    if layer.startswith("B.") and "B.Cu" in by_name:
        return by_name["B.Cu"]
    return None


def via_diameter_mm(via: Via, layer: str, mode: ViaDiameter = DEFAULT_VIA_DIAMETER) -> float:
    """Return the copper diameter of *via* on *layer*, in millimeters."""
    if mode is ViaDiameter.largest:
        widths = [via.size]
        if via.padstack is not None:
            widths.extend(entry.size for entry in via.padstack.layers if entry.size > 0.0)
        return max(widths)

    entry = _padstack_entry(via, layer)
    if entry is None or entry.size <= 0.0:
        return via.size
    return entry.size


def via_flash(via: Via, layer: str) -> PadFlash:
    """``PCB_VIA::FlashLayer``, ``pcb_track.cpp:1953-2005``.

    A VIA and a PAD do not share this rule, and the difference is easy to miss
    because the two functions have the same name and the same enum.

    ``PAD::FlashLayer`` keeps ``REMOVE_EXCEPT_START_AND_END`` on an EXTERNAL
    copper layer, tested with ``IsExternalCopperLayer`` (``pad.cpp:490-494``).
    ``PCB_VIA::FlashLayer`` keeps it on the via's OWN drill start and end
    instead (``pcb_track.cpp:1976-1981``). For a through via the two agree,
    because its span ends on the outer layers. For a BLIND or BURIED via they do
    not: a via spanning In1 to In4 keeps copper on In1 and In4, and the outer
    layers do not enter the question.

    That is why this function takes no external-copper set. The via rule never
    asks about one.

    ``certain`` is ``False`` when the answer needs the board connectivity graph,
    which this library derives for schematics only. The via still DRAWS in that
    case, matching what KiCad returns when it cannot consult connectivity. See
    :class:`eedesigncore.render.pads.PadFlash`.
    """
    if not is_copper_layer_name(layer):
        return PadFlash(True)

    mode = via.unconnected_layer_mode
    if mode == "keep_all":
        return PadFlash(True)

    # `via.layers` is the stored pair, which IS the drill start and end.
    if mode == "start_end_only":
        return PadFlash(layer in via.layers)

    if mode == "remove_except_start_end" and layer in via.layers:
        return PadFlash(True)

    return PadFlash(True, certain=False)


__all__ = [
    "DEFAULT_VIA_DIAMETER",
    "ViaDiameter",
    "via_diameter_mm",
    "via_flash",
]
