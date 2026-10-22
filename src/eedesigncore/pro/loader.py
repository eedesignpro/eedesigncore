"""Loader for ``.kicad_pro`` and ``.kicad_prl`` JSON trees.

The loader is deliberately permissive about content and strict about shape. It
requires a JSON object at the root and nothing else, because the corpus proves
that project files vary in completeness: a project that KiCad 10 has not
re-saved carries no ``component_class_settings`` block at all (audit section
8.8). The writer must never synthesise a missing subtree, so the loader must
never demand one.
"""

from __future__ import annotations

from typing import TypeVar

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro.models import ProjectFile, ProjectJsonFile, ProjectLocalSettings
from eedesigncore.pro.parser import parse_json_object
from eedesigncore.pro.tree import JsonTree, JsonValue

_ProjectT = TypeVar("_ProjectT", bound=ProjectJsonFile)


def load_project_json(model: type[_ProjectT], text: str) -> _ProjectT:
    """Parse project-file *text* into *model*.

    Raises:
        KicadParseError: The text is not valid JSON, or its root is not a JSON
            object.
    """
    return model(root=JsonTree(parse_json_object(text)))


def load_pro(tree: JsonValue) -> ProjectFile:
    """Build a :class:`ProjectFile` from an already parsed JSON tree.

    Raises:
        KicadParseError: The tree root is not a JSON object.
    """
    return ProjectFile(root=JsonTree(_require_object(tree, "kicad_pro")))


def load_prl(tree: JsonValue) -> ProjectLocalSettings:
    """Build a :class:`ProjectLocalSettings` from an already parsed JSON tree.

    Raises:
        KicadParseError: The tree root is not a JSON object.
    """
    return ProjectLocalSettings(root=JsonTree(_require_object(tree, "kicad_prl")))


def _require_object(tree: JsonValue, extension: str) -> dict[str, JsonValue]:
    if not isinstance(tree, dict):
        raise KicadParseError(f"Not a .{extension} file: the JSON root is not an object")
    return tree
