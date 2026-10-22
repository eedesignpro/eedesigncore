"""Enumerations for KiCad footprint (.kicad_mod) file format.

The enum definitions moved to ``common/pcb_family/enums.py`` (shared by
``.kicad_mod`` and ``.kicad_pcb``, the ADR-015 pattern). Re-exported here for
backward compatibility.
"""

from eedesigncore.common.pcb_family.enums import (
    ChamferPosition,
    CustomAnchor,
    CustomShapeClearance,
    PadProperty,
    PadShape,
    PadType,
)

__all__ = [
    "ChamferPosition",
    "CustomAnchor",
    "CustomShapeClearance",
    "PadProperty",
    "PadShape",
    "PadType",
]
