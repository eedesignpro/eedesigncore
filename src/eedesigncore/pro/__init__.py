"""``.kicad_pro`` / ``.kicad_prl`` JSON parser, loader, models, and writer.

This package is a JSON pipeline, not the S-expression pipeline. It shares no
layout code with :mod:`eedesigncore.sexpr`, because the two formats share no
layout rules.

Primary API::

    from eedesigncore import ProjectFile

    project = ProjectFile.from_file("board.kicad_pro")
    project.to_file("board.kicad_pro")   # byte-identical

Lower-level path: :func:`parse_json` turns text into a tree, :func:`load_pro`
turns a tree into a model, and :func:`dump_json` turns a tree back into text.
JSON has exactly one layout, so there is no flat-then-prettify split here and
therefore no separate ``write_pro`` function.
"""

from eedesigncore.pro.loader import load_prl, load_pro, load_project_json
from eedesigncore.pro.models import (
    PRL_SCHEMA_VERSION,
    PRO_SCHEMA_VERSION,
    V10_MARKER_PATH,
    MetaBlock,
    ProjectFile,
    ProjectJsonFile,
    ProjectLocalSettings,
)
from eedesigncore.pro.parser import parse_json, parse_json_object
from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonObject, JsonTree, JsonValue
from eedesigncore.pro.writer import dump_json, format_float

__all__ = [
    "ProjectFile",
    "ProjectLocalSettings",
    "ProjectJsonFile",
    "MetaBlock",
    "JsonTree",
    "JsonInt",
    "JsonFloat",
    "JsonValue",
    "JsonObject",
    "parse_json",
    "parse_json_object",
    "load_pro",
    "load_prl",
    "load_project_json",
    "dump_json",
    "format_float",
    "V10_MARKER_PATH",
    "PRO_SCHEMA_VERSION",
    "PRL_SCHEMA_VERSION",
]
