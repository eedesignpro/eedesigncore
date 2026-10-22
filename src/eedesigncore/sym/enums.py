"""Symbol-library-specific enumerations.

The enum definitions moved to ``common/sch_family/enums.py`` (shared by
``.kicad_sym`` and ``.kicad_sch``, ADR-015). Re-exported here for backward
compatibility.
"""

from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape

__all__ = ["PinElectricalType", "PinShape"]
