"""Typed JSON value tree for KiCad project files.

KiCad stores ``.kicad_pro`` and ``.kicad_prl`` as JSON, not as S-expressions, so
this package runs its own three-layer pipeline beside the S-expression one:
text -> :func:`eedesigncore.pro.parser.parse_json` -> tree ->
:func:`eedesigncore.pro.loader.load_pro` -> model.

Two rules from the A1 audit (``context/audit/json-save-path-2026-07-28.md``)
shape this module.

1. KiCad preserves every key it does not understand (audit section 5.1), so the
   tree is the storage of record. Typed accessors read and write through it.
   Nothing rebuilds the tree from a model.
2. A number keeps the exact text it was parsed from (audit section 9.3).
   :class:`JsonInt` and :class:`JsonFloat` carry that text in ``literal``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeAlias

from eedesigncore.exceptions import KicadParseError

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
JsonObject: TypeAlias = "dict[str, JsonValue]"


class JsonInt(int):
    """An integer that remembers the exact text it was parsed from.

    KiCad renders an integer as plain decimal, so ``literal`` and ``str(value)``
    agree for every KiCad-written file. The literal still travels with the value
    so that an unusual source text, for example a leading plus sign, survives a
    round trip.
    """

    literal: str

    def __new__(cls, value: int, literal: str | None = None) -> JsonInt:
        """Build an integer that carries *literal* as its output text."""
        self = super().__new__(cls, value)
        self.literal = str(int(self)) if literal is None else literal
        return self

    @classmethod
    def from_literal(cls, text: str) -> JsonInt:
        """Build an integer from JSON source text, keeping that text."""
        return cls(int(text), text)

    def __reduce__(self) -> tuple[type[JsonInt], tuple[int, str]]:
        return (self.__class__, (int(self), self.literal))

    def __str__(self) -> str:
        return str(int(self))

    def __repr__(self) -> str:
        return f"JsonInt({int(self)!r}, {self.literal!r})"


class JsonFloat(float):
    """A float that remembers the exact text it was parsed from.

    Float text is the one place where Python and the nlohmann serializer can
    disagree (audit section 9.3). Keeping the source text removes that risk for
    every value the caller never changed.
    """

    __slots__ = ("literal",)

    literal: str

    def __new__(cls, value: float, literal: str | None = None) -> JsonFloat:
        """Build a float that carries *literal* as its output text."""
        self = super().__new__(cls, value)
        self.literal = repr(float(self)) if literal is None else literal
        return self

    @classmethod
    def from_literal(cls, text: str) -> JsonFloat:
        """Build a float from JSON source text, keeping that text."""
        return cls(float(text), text)

    def __reduce__(self) -> tuple[type[JsonFloat], tuple[float, str]]:
        return (self.__class__, (float(self), self.literal))

    def __str__(self) -> str:
        return str(float(self))

    def __repr__(self) -> str:
        return f"JsonFloat({float(self)!r}, {self.literal!r})"


class JsonTree:
    """Typed wrapper over one preserved JSON object.

    The wrapper holds the parsed mapping itself, never a copy, so a nested tree
    returned by :meth:`get_object` writes through to its parent. Keys the
    library does not model survive untouched, which is what byte identity
    requires (audit section 5.1).
    """

    def __init__(self, entries: JsonObject | None = None) -> None:
        self._entries: JsonObject = {} if entries is None else entries

    @property
    def entries(self) -> JsonObject:
        """The wrapped mapping. Mutating it mutates the tree."""
        return self._entries

    def keys(self) -> list[str]:
        """Every key of this object, in the order the file supplied them."""
        return list(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, JsonTree):
            return self._entries == other._entries
        return NotImplemented

    def __repr__(self) -> str:
        return f"JsonTree({sorted(self._entries)!r})"

    def get(self, key: str, default: JsonValue = None) -> JsonValue:
        """The value at *key*, or *default* when the key is absent."""
        return self._entries.get(key, default)

    def set(self, key: str, value: JsonValue) -> None:
        """Store *value* at *key*, replacing any current value."""
        self._entries[key] = value

    def remove(self, key: str) -> bool:
        """Delete *key*. Returns True when the key was present."""
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def get_object(self, key: str) -> JsonTree | None:
        """The nested object at *key* as a write-through view, or None."""
        value = self._entries.get(key)
        if value is None:
            return None
        return JsonTree(_expect_object(value, key))

    def get_list(self, key: str) -> list[JsonValue] | None:
        """The array at *key*, or None when the key is absent."""
        value = self._entries.get(key)
        if value is None:
            return None
        if not isinstance(value, list):
            raise KicadParseError(f"Expected a JSON array at {key!r}, got {_kind(value)}")
        return value

    def get_str(self, key: str) -> str | None:
        """The string at *key*, or None when the key is absent."""
        value = self._entries.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise KicadParseError(f"Expected a JSON string at {key!r}, got {_kind(value)}")
        return value

    def get_int(self, key: str) -> int | None:
        """The integer at *key*, or None when the key is absent."""
        value = self._entries.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise KicadParseError(f"Expected a JSON integer at {key!r}, got {_kind(value)}")
        return int(value)

    def get_bool(self, key: str) -> bool | None:
        """The boolean at *key*, or None when the key is absent."""
        value = self._entries.get(key)
        if value is None:
            return None
        if not isinstance(value, bool):
            raise KicadParseError(f"Expected a JSON boolean at {key!r}, got {_kind(value)}")
        return value

    def path(self, dotted: str) -> JsonValue:
        """The value at a dotted path such as ``schematic.top_level_sheets``.

        Returns None when any step is absent or is not an object.
        """
        current: JsonValue = self._entries
        for step in dotted.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(step)
        return current

    def has_path(self, dotted: str) -> bool:
        """True when every step of a dotted path exists."""
        current: JsonValue = self._entries
        for step in dotted.split("."):
            if not isinstance(current, dict) or step not in current:
                return False
            current = current[step]
        return True


def _kind(value: JsonValue) -> str:
    """The JSON type name of *value*, for error messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _expect_object(value: JsonValue, key: str) -> JsonObject:
    if not isinstance(value, dict):
        raise KicadParseError(f"Expected a JSON object at {key!r}, got {_kind(value)}")
    return value
