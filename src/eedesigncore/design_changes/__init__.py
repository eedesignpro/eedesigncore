"""Labelled change records between two designs, and text views of them.

:func:`compare_designs` is the entry point. See ``labels.py`` for the category
rules and ``records.py`` for the record format.
"""

from eedesigncore.design_changes.adapters import (
    changes_from_schematic_electrical,
    changes_from_update,
)
from eedesigncore.design_changes.compare import (
    DESIGN_SUFFIXES,
    Design,
    compare_designs,
    load_design,
)
from eedesigncore.design_changes.printouts import format_change_diff, format_change_report
from eedesigncore.design_changes.records import (
    CHANGE_CATEGORIES,
    CHANGE_FORMAT,
    ChangeCategory,
    ChangeKind,
    DesignChange,
    DesignComparison,
)

__all__ = [
    "CHANGE_CATEGORIES",
    "CHANGE_FORMAT",
    "DESIGN_SUFFIXES",
    "ChangeCategory",
    "ChangeKind",
    "Design",
    "DesignChange",
    "DesignComparison",
    "changes_from_schematic_electrical",
    "changes_from_update",
    "compare_designs",
    "format_change_diff",
    "format_change_report",
    "load_design",
]
