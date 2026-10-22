"""Symbol library (.kicad_sym) parser and writer."""

from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.sym.enums import PinElectricalType, PinShape
from eedesigncore.sym.loader import load_sym
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import dump_sym, write_sym

__all__ = [
    # Enums
    "PinElectricalType",
    "PinShape",
    # Models
    "SymbolLib",
    "Symbol",
    "SymbolUnit",
    "Pin",
    "PinAlternate",
    "SchArc",
    "SchBezier",
    "SchCircle",
    "SchPolyline",
    "SchRect",
    "SymText",
    "SymTextBox",
    # Functions
    "load_sym",
    "dump_sym",
    "write_sym",
]
