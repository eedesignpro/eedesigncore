"""Literal-preserving JSON parser for KiCad project files.

This module is the JSON counterpart of :mod:`eedesigncore.sexpr.parser`. It
turns text into a generic tree of :data:`~eedesigncore.pro.tree.JsonValue`
nodes, and it wraps every number in :class:`~eedesigncore.pro.tree.JsonInt` or
:class:`~eedesigncore.pro.tree.JsonFloat` so that the source text of the number
survives to the writer (audit section 9.3).

KiCad parses project files with ``nlohmann::json::parse`` under the strict error
handler (``common/settings/json_settings.cpp:286``), so this parser is strict
too. It rejects ``NaN``, ``Infinity``, and ``-Infinity``, none of which are legal
JSON.
"""

from __future__ import annotations

import json
from typing import cast

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonObject, JsonValue

_BOM = "﻿"


def _reject_constant(name: str) -> float:
    """Reject the three non-finite literals that plain JSON forbids."""
    raise KicadParseError(
        f"Invalid JSON: the literal {name!r} is not valid JSON. "
        "KiCad writes a non-finite number as null."
    )


def parse_json(text: str) -> JsonValue:
    """Parse JSON *text* into a tree that remembers every number literal.

    Args:
        text: The file content, already decoded from UTF-8.

    Returns:
        The parsed tree. Numbers arrive as :class:`JsonInt` or
        :class:`JsonFloat`, both of which behave as ordinary Python numbers.

    Raises:
        KicadParseError: The text is not valid JSON, carries a byte-order mark,
            or nests too deeply for the interpreter to parse.
    """
    if text.startswith(_BOM):
        raise KicadParseError(
            "Invalid JSON: the file starts with a byte-order mark. "
            "KiCad writes UTF-8 without a byte-order mark."
        )
    try:
        value = json.loads(
            text,
            parse_int=JsonInt.from_literal,
            parse_float=JsonFloat.from_literal,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise KicadParseError(f"Invalid JSON: {exc.msg}", line=exc.lineno) from exc
    except RecursionError as exc:
        raise KicadParseError("Invalid JSON: the document nests too deeply") from exc
    return cast(JsonValue, value)


def parse_json_object(text: str) -> JsonObject:
    """Parse JSON *text* that must hold an object at its root.

    Raises:
        KicadParseError: The text is not valid JSON, or its root is not an
            object. Every KiCad project file has an object at its root.
    """
    value = parse_json(text)
    if not isinstance(value, dict):
        raise KicadParseError("Not a KiCad project file: the JSON root is not an object")
    return value
