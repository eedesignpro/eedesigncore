"""SCH-family shared models (the ``Symbol`` sub-grammar embedded by .kicad_sym
and, from iteration 10, .kicad_sch).

- ``enums`` -- pin electrical type / shape.
- ``shapes`` -- Sch* graphic shapes (arc/circle/rect/bezier/polyline).
- ``pins`` -- Pin, PinAlternate.
- ``text`` -- SymText (int-tenths angle), SymTextBox.
- ``symbol`` -- Symbol, SymbolUnit (the SaveSymbol sub-grammar), SymbolItem union.

``common/`` never imports a format sub-package (ADR-015); formats import from here.
"""

from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape, PowerType
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import (
    Symbol,
    SymbolItem,
    SymbolUnit,
    join_fp_filters,
    semantic_fp_filters,
    split_fp_filters,
)
from eedesigncore.common.sch_family.text import SymText, SymTextBox

__all__ = [
    # enums
    "PinElectricalType",
    "PinShape",
    "PowerType",
    # shapes
    "SchArc",
    "SchBezier",
    "SchCircle",
    "SchPolyline",
    "SchRect",
    # pins
    "Pin",
    "PinAlternate",
    # text
    "SymText",
    "SymTextBox",
    # symbol
    "Symbol",
    "SymbolUnit",
    "SymbolItem",
    # footprint filters
    "split_fp_filters",
    "join_fp_filters",
    "semantic_fp_filters",
]
