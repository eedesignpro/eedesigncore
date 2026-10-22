"""Zone fill record operations (work order P6).

A zone's fill is not derived on load and it is not derived on save. It is stored,
one ``(filled_polygon …)`` record per outline, and this library reads and writes
those records verbatim. That makes three operations possible without a zone
filler: read the records out, put other records in, and turn the records into
plain copper graphics.

Nothing here fills a zone. Computing a fill needs the clearance engine, the
thermal-relief rules and the whole DRC model, none of which this library has.
These operations move a fill that KiCad already computed.

The flattening rules are the KiCad 10.0.3 plot path, re-verified 2026-09-02.
Every one of them is cited on :func:`flatten_fills`.
"""

from __future__ import annotations

from eedesigncore.common.models import Fill, FillType, Stroke
from eedesigncore.common.pcb_family.board_items import Zone, ZoneFilledPolygon
from eedesigncore.common.pcb_family.shapes import PcbPoly


def extract_fills(zone: Zone) -> list[ZoneFilledPolygon]:
    """Return a deep copy of the fill records of *zone*.

    The copies are detached: changing one never reaches the zone it came from,
    and the zone can be discarded while the records live on. That is what makes
    them safe to hand to :func:`replace_fills` on another board.
    """
    return [record.model_copy(deep=True) for record in zone.filled_polygons]


def replace_fills(zone: Zone, fills: list[ZoneFilledPolygon]) -> None:
    """Put *fills* on *zone*, in place, replacing whatever was there.

    This is the transplant the work order names. The records are deep-copied, so
    the caller keeps its own set and the two never alias.

    A zone that receives records is a FILLED zone, so ``fill.filled`` is set. The
    corpus agrees: of 11714 zones on 166 boards, not one carries a fill record
    while flagged unfilled. Passing an empty list clears the records and leaves
    the flag alone, because a filled zone whose fill came to nothing is a real
    state that 296 corpus zones are in.

    Nothing checks that the records suit the zone. A record names its own layer
    and the caller is responsible for that naming: KiCad will not repair it.
    """
    zone.filled_polygons = [record.model_copy(deep=True) for record in fills]
    if fills:
        zone.fill.filled = True


def flatten_fills(zone: Zone) -> list[PcbPoly]:
    """Turn the fill records of *zone* into plain copper graphics.

    The result is what a fabricator sees: the same copper, no longer owned by a
    zone, so no later refill can move it. The caller appends the graphics to a
    BOARD and mints their uuids; this function only builds them.

    Five rules, all read from the KiCad 10.0.3 plot path and re-verified
    2026-09-02:

    1. ONE graphic per stored outline, no merging. ``BRDITEMS_PLOTTER::PlotZone``
       loops the outlines of the fill and plots each on its own
       (``plot_brditems_plotter.cpp:888-906``).
    2. Stroke width EXACTLY zero. The plot call passes ``0`` as the line width
       (``:904``).
    3. Solid fill. The plot call passes ``FILL_T::FILLED_SHAPE`` (``:904``),
       which the board writer emits as ``(fill yes)``
       (``pcb_io_kicad_sexpr.cpp:1081-1083``). ``FillType.outline`` is the value
       the loader gives that token, so a flattened graphic compares equal to one
       read from a file.
    4. An ISLAND record carries NO net. The plot path splits the island outlines
       out and plots them through a copy of the zone whose net has been cleared
       (``plot_board_layers.cpp:860-877``), which lands them on the
       NONCONDUCTOR aperture attribute instead of CONDUCTOR
       (``plot_brditems_plotter.cpp:869-877``). A board-level graphic with no net
       takes the same attribute (``:980-985``), so dropping the net reproduces
       the plot.
    5. BOARD level only, and never a solder-mask property. A copper graphic
       inside a footprint takes the component attribute and the ETCHEDCMP
       aperture attribute instead (``:946-970``), which is not what a zone fill
       emits. A solder-mask property would widen the shape onto the mask layer
       (``:966-975``), which a zone fill never does, so ``solder_mask_margin``
       stays unset.

    The zone's own net reaches every non-island graphic, which puts them on the
    CONDUCTOR attribute the zone fill uses (``:973-978``).
    """
    graphics: list[PcbPoly] = []
    for record in zone.filled_polygons:
        graphics.append(
            PcbPoly(
                pts=[
                    point if isinstance(point, tuple) else point.model_copy(deep=True)
                    for point in record.pts
                ],
                stroke=Stroke(width=0.0),
                fill=Fill(type=FillType.outline),
                layer=record.layer,
                net_name=None if record.island else zone.net_name,
            )
        )
    return graphics


__all__ = ["extract_fills", "flatten_fills", "replace_fills"]
