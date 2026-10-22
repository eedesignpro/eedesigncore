"""KiCad footprint library (.kicad_mod) parser and writer."""

from eedesigncore.common.pcb_family.shapes import (
    PcbArc,
    PcbCircle,
    PcbCurve,
    PcbDrawItem,
    PcbGroup,
    PcbLine,
    PcbPoly,
    PcbRect,
    PcbText,
    PcbTextBox,
)
from eedesigncore.mod.enums import PadProperty, PadShape, PadType
from eedesigncore.mod.loader import load_mod
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintAttrs,
    FootprintPlacement,
    Model3D,
    Pad,
    PadPrimitive,
    PrimArc,
    PrimBbox,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimPoly,
    PrimRect,
    PrimVector,
    TeardropParams,
)
from eedesigncore.mod.writer import dump_mod, write_mod

__all__ = [
    # Enums
    "PadType",
    "PadShape",
    "PadProperty",
    # Models (canonical Pcb* names)
    "Footprint",
    "FootprintAttrs",
    "FootprintPlacement",
    "PcbArc",
    "PcbCircle",
    "PcbCurve",
    "PcbDrawItem",
    "PcbGroup",
    "PcbLine",
    "PcbPoly",
    "PcbRect",
    "PcbText",
    "PcbTextBox",
    "Model3D",
    "Pad",
    "DrillInfo",
    "TeardropParams",
    # Pad primitives
    "PrimLine",
    "PrimRect",
    "PrimArc",
    "PrimCircle",
    "PrimCurve",
    "PrimPoly",
    "PrimVector",
    "PrimBbox",
    "PadPrimitive",
    # Functions
    "load_mod",
    "dump_mod",
    "write_mod",
]
