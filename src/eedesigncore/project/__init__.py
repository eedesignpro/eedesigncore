"""Project-level helpers that span more than one KiCad file.

Every format package (``sch``, ``pcb``, ``pro``, and the rest) handles one file
at a time, and byte identity is a per-file contract. This package sits above
them and reads across files. It never writes and never mutates a model.

Primary API::

    from eedesigncore import load_hierarchy

    tree = load_hierarchy("board.kicad_pro")
    for node in tree.walk():
        print(node.page, node.name, node.path.name)
"""

from eedesigncore.project.hierarchy import (
    MAX_HIERARCHY_DEPTH,
    MAX_HIERARCHY_FILES,
    MAX_SCHEMATIC_FILE_BYTES,
    SHEET_FILE_PROPERTY,
    SHEET_NAME_PROPERTY,
    SheetHierarchy,
    SheetNode,
    load_hierarchy,
)
from eedesigncore.project.netclasses import read_netclass_assignments

__all__ = [
    "read_netclass_assignments",
    "SheetHierarchy",
    "SheetNode",
    "load_hierarchy",
    "SHEET_FILE_PROPERTY",
    "SHEET_NAME_PROPERTY",
    "MAX_HIERARCHY_DEPTH",
    "MAX_HIERARCHY_FILES",
    "MAX_SCHEMATIC_FILE_BYTES",
]
