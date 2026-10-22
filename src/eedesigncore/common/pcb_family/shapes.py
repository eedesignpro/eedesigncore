"""Shared-child PCB-family graphic payloads (Tier C: board + footprint).

Tier C -- shared-child: reachable from BOTH ``format(BOARD)`` and
``format(FOOTPRINT)`` (commonality Part 1 table). One class per geometry; the
``gr_``/``fp_`` token prefix and the board-vs-footprint coordinate frame are a
writer concern (a prefix argument on the shared dumper), NOT a type distinction --
a single ``PcbLine`` serves both ``.kicad_mod`` ``fp_line`` and ``.kicad_pcb``
``gr_line`` (arch-plan section 2a). Names mirror the KiCad C++ classes
(``PCB_SHAPE``/``PCB_TEXT``/``PCB_TEXTBOX``/``PCB_GROUP``).
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import (
    Fill,
    PtsElement,
    RenderCache,
    Stroke,
    TextAttributes,
)
from eedesigncore.common.pcb_family.board_items import (
    Barcode,
    Dimension,
    ReferenceImage,
    Table,
)

# ---------------------------------------------------------------------------
# PcbPoint (§2.5)
# ---------------------------------------------------------------------------


class PcbPoint(KicadModel):
    """``(point ...)`` PCB point marker (PCB_POINT, §2.5)."""

    position: tuple[float, float] = (0.0, 0.0)
    size: float = 0.0
    layer: str = "F.Cu"
    id: str | None = None


# ---------------------------------------------------------------------------
# Drawing items
# ---------------------------------------------------------------------------


class PcbText(KicadModel):
    """An ``fp_text``/``gr_text`` item (reference, value, or user text)."""

    text_type: str = "user"  # "reference", "value", or "user"
    content: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    # angle is ALWAYS written (even when 0) per source line 2293
    angle: float = 0.0
    layer: str = "F.SilkS"
    knockout: bool = False
    unlocked: bool = False  # written only when True (inverted IsKeepUpright)
    locked: bool = False
    visible: bool = True  # False => (hide yes)
    id: str | None = None
    attributes: TextAttributes | None = None
    render_cache: RenderCache | None = None  # outline-font polygon cache; None when absent


class PcbLine(KicadModel):
    """An ``fp_line``/``gr_line`` drawing item."""

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    stroke: Stroke = Stroke()
    locked: bool = False
    layer: str = "F.SilkS"
    # Multi-layer shapes emit (layers ...) instead of (layer ...) when the
    # LSET spans >1 layer (pcb_io_kicad_sexpr.cpp:1094-1097). When set, the
    # writer emits (layers ...) and ignores ``layer``.
    layers: list[str] | None = None
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None


class PcbRect(KicadModel):
    """An ``fp_rect``/``gr_rect`` drawing item.

    ``fill`` is ALWAYS written (even when false/none) per source line 1086.
    """

    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    corner_radius: float | None = None
    stroke: Stroke = Stroke()
    fill: Fill = Fill()  # Always written
    locked: bool = False
    layer: str = "F.SilkS"
    layers: list[str] | None = None  # multi-layer: (layers ...) supersedes (layer ...)
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None


class PcbCircle(KicadModel):
    """An ``fp_circle``/``gr_circle`` drawing item.

    ``fill`` is ALWAYS written (even when false/none) per source line 1086.
    """

    center: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)  # point on perimeter
    stroke: Stroke = Stroke()
    fill: Fill = Fill()  # Always written
    locked: bool = False
    layer: str = "F.SilkS"
    layers: list[str] | None = None  # multi-layer: (layers ...) supersedes (layer ...)
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None


class PcbArc(KicadModel):
    """An ``fp_arc``/``gr_arc`` drawing item."""

    start: tuple[float, float] = (0.0, 0.0)
    mid: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    stroke: Stroke = Stroke()
    locked: bool = False
    layer: str = "F.SilkS"
    layers: list[str] | None = None  # multi-layer: (layers ...) supersedes (layer ...)
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None


class PcbPoly(KicadModel):
    """An ``fp_poly``/``gr_poly`` drawing item.

    ``fill`` is ALWAYS written (even when false/none) per source line 1086.
    """

    pts: list[PtsElement] = []
    stroke: Stroke = Stroke()
    fill: Fill = Fill()  # Always written
    locked: bool = False
    layer: str = "F.SilkS"
    layers: list[str] | None = None  # multi-layer: (layers ...) supersedes (layer ...)
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None


class PcbCurve(KicadModel):
    """An ``fp_curve``/``gr_curve`` (Bezier) drawing item -- exactly 4 control points.

    ``pts`` holds start, control 1, control 2 and end, in that order. KiCad writes
    exactly four ``(xy …)`` points for a Bezier shape
    (pcb_io_kicad_sexpr.cpp:1045-1051), and construction rejects any other count
    (GAP-015). All 126 corpus curves carry four points.
    """

    pts: list[PtsElement] = []  # exactly 4
    stroke: Stroke = Stroke()
    locked: bool = False
    layer: str = "F.SilkS"
    layers: list[str] | None = None  # multi-layer: (layers ...) supersedes (layer ...)
    # PCB_SHAPE emits solder_mask_margin/net for ALL shape kinds
    # (pcb_io_kicad_sexpr.cpp:1099-1108); modeled here for parity with the
    # other Pcb* shapes so the hard-error loop is consistent (T2 §3b).
    solder_mask_margin: float | None = None
    net_name: str | None = None
    id: str | None = None

    @model_validator(mode="after")
    def _check_four_control_points(self) -> Self:
        if len(self.pts) != 4:
            raise ValueError(
                f"PcbCurve needs exactly 4 control points, got {len(self.pts)}. "
                "Supply start, control 1, control 2 and end."
            )
        return self


class PcbTextBox(KicadModel):
    """An ``fp_text_box``/``gr_text_box`` drawing item."""

    content: str = ""
    locked: bool = False
    # Rectangle shape: start/end; Poly shape: pts
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    pts: list[PtsElement] = []
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    angle: float | None = None  # written only when non-zero
    layer: str = "F.SilkS"
    id: str | None = None
    attributes: TextAttributes | None = None
    border_enabled: bool = False
    stroke: Stroke = Stroke()
    knockout: bool = False
    render_cache: RenderCache | None = None  # outline-font polygon cache; None when absent


# Union type for all drawing items.
# KiCad routes fp_*/gr_* shapes AND dimension/table/image/barcode through ONE
# sorted GraphicalItems drawings loop (format(FOOTPRINT*) :1425-1439); modeling
# them in a single ordered collection preserves interleaved file order (GAP-001).
PcbDrawItem = (
    PcbText
    | PcbLine
    | PcbRect
    | PcbCircle
    | PcbArc
    | PcbPoly
    | PcbCurve
    | PcbTextBox
    | Dimension
    | Table
    | ReferenceImage
    | Barcode
)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


class PcbGroup(KicadModel):
    """A ``group`` token (PCB_GROUP)."""

    name: str = ""
    id: str | None = None
    locked: bool = False
    lib_id: str | None = None
    members: list[str] = []


__all__ = [
    "PcbPoint",
    "PcbText",
    "PcbLine",
    "PcbRect",
    "PcbCircle",
    "PcbArc",
    "PcbPoly",
    "PcbCurve",
    "PcbTextBox",
    "PcbDrawItem",
    "PcbGroup",
]
