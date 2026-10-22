"""Portable and native read-only design inspection."""

from eedesigncore.inspection.native import (
    inspect_design,
    open_inspection,
    portable_inspection_source,
)
from eedesigncore.inspection.portable import DesignInspector

__all__ = ["DesignInspector", "inspect_design", "open_inspection", "portable_inspection_source"]
