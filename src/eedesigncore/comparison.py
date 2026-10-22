"""Compare represented KiCad file content."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from eedesigncore.common.sch_family.ordering import parent_map_key, unescape_slash
from eedesigncore.common.sch_family.symbol import Symbol, SymbolItem, SymbolUnit
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.pro.models import ProjectFile
from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonTree
from eedesigncore.sch.models import RuleArea, Schematic
from eedesigncore.sym.models import SymbolLib

_SCHEME = "kicad-content-v1"
_BOARD_ITEMS = frozenset(
    {
        "footprints",
        "drawings",
        "trailing_drawings",
        "points",
        "tracks",
        "zones",
        "groups",
        "generators",
        "targets",
    }
)
_SCHEMATIC_ITEMS = (
    "graphic_shapes",
    "texts",
    "text_boxes",
    "junctions",
    "no_connects",
    "bus_entries",
    "lines",
    "labels",
    "images",
    "tables",
    "rule_areas",
    "groups",
    "symbols",
    "sheets",
)
_INDEPENDENT = {
    (Footprint, "pads"),
    (Footprint, "drawings"),
    (Footprint, "points"),
    (Footprint, "zones"),
    (Footprint, "groups"),
    (SymbolUnit, "items"),
}


@dataclass(frozen=True)
class ContentChange:
    """One difference. Values are JSON text. None means an absent value."""

    path: tuple[str, ...]
    kind: Literal["added", "removed", "changed"]
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ContentComparison:
    """Exact represented-content equality and changes under one definition."""

    before_signature: str
    after_signature: str
    changes: tuple[ContentChange, ...]

    @property
    def equal(self) -> bool:
        """True when the normalized content is identical."""
        return not self.changes


@dataclass(frozen=True)
class _Node:
    kind: str
    value: str = ""
    fields: tuple[tuple[str, _Node], ...] = ()
    items: tuple[_Node, ...] = ()


@dataclass
class _Budget:
    values: int = 2_000_000
    encoding: int = 256 * 1024 * 1024

    def charge(self, size: int, *, value: bool = False) -> None:
        self.values -= int(value)
        self.encoding -= size
        if self.values < 0 or self.encoding < 0:
            raise ValueError("Content exceeds the comparison size limit.")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _payload(node: _Node) -> object:
    return [
        node.kind,
        node.value,
        [[k, _payload(v)] for k, v in node.fields],
        [_payload(v) for v in node.items],
    ]


def _encoded(node: _Node) -> str:
    return _json(_payload(node))


def _schematic_items_independent(owner: Schematic) -> bool:
    identifiers: list[str] = []
    for collection in _SCHEMATIC_ITEMS:
        for item in getattr(owner, collection):
            identifier = (
                item.shape.id
                if isinstance(item, RuleArea)
                else getattr(item, "uuid", None) or getattr(item, "id", None)
            )
            if identifier is not None and not isinstance(identifier, str):
                raise TypeError("Schematic item identifiers must be strings.")
            if identifier:
                identifiers.append(identifier.casefold())
    if any(
        re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", item) is None
        for item in identifiers
    ):
        return False
    return len(identifiers) == len(set(identifiers))


def _independent(
    owner: BaseModel, field: str, *, schematic_items_independent: bool = False
) -> bool:
    if type(owner) is Schematic:
        if field == "lib_symbols":
            definition_names = [unescape_slash(symbol.name) for symbol in owner.lib_symbols]
            if any(
                not name
                or name.startswith(":")
                or name.endswith(":")
                or name.count(":") > 1
                or any(char in name.rsplit(":", 1)[-1] for char in '\t\n\r\\<>"')
                for name in definition_names
            ):
                return False
            return len(definition_names) == len(set(definition_names))
        if field in _SCHEMATIC_ITEMS:
            return schematic_items_independent
    if (
        type(owner) is SymbolUnit
        and field == "items"
        and not all(isinstance(item, SymbolItem) for item in owner.items)
    ):
        raise TypeError("Symbol units require supported typed drawing items and pins.")
    if (type(owner), field) in _INDEPENDENT:
        return True
    if type(owner) is SymbolLib and field == "symbols":
        symbol_names = [parent_map_key(symbol.name) for symbol in owner.symbols]
        return len(symbol_names) == len(set(symbol_names))
    if type(owner) is Symbol and field == "units":
        keys = [(unit.unit_index, unit.body_style_index) for unit in owner.units]
        names: dict[int, set[str | None]] = {}
        for unit in owner.units:
            expected = SymbolUnit.encode_name(owner.name, unit.unit_index, unit.body_style_index)
            if unit.name != expected:
                return False
            names.setdefault(unit.unit_index, set()).add(unit.unit_display_name)
        return len(keys) == len(set(keys)) and all(len(v) == 1 for v in names.values())
    return False


def _snapshot(
    value: object,
    *,
    budget: _Budget,
    depth: int = 0,
    active: frozenset[int] = frozenset(),
    normalize: bool = True,
    json_only: bool = False,
) -> _Node:
    if depth > 64:
        raise ValueError("Content exceeds the comparison depth limit.")
    budget.charge(128, value=True)
    if id(value) in active:
        raise ValueError("Content contains a reference cycle.")
    nested = active | {id(value)}

    def visit(child: object) -> _Node:
        return _snapshot(
            child,
            budget=budget,
            depth=depth + 1,
            active=nested,
            normalize=normalize,
            json_only=json_only,
        )

    if json_only:
        if type(value) not in (type(None), bool, int, float, str, list, dict, JsonInt, JsonFloat):
            raise TypeError("Project content requires JSON values.")
        if isinstance(value, dict) and any(type(key) is not str for key in value):
            raise TypeError("Project content requires string object keys.")
    if isinstance(value, Enum):
        if not type(value).__module__.startswith("eedesigncore."):
            raise TypeError("Content contains an unsupported enumeration.")
        return _Node("enum", type(value).__name__, items=(visit(value.value),))
    if isinstance(value, str):
        budget.charge(6 * len(value))
    if value is None or isinstance(value, str | bool | int):
        return _Node("scalar", _json(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Content comparison requires finite numbers.")
        number: int | float = int(value) if value.is_integer() else value
        return _Node("scalar", _json(number))
    if isinstance(value, BaseModel):
        if not type(value).__module__.startswith("eedesigncore."):
            raise TypeError("Content contains an unsupported domain model.")
        fields: list[tuple[str, _Node]] = []
        for name in sorted(type(value).model_fields):
            budget.charge(6 * len(name))
            fields.append((name, visit(getattr(value, name))))
        if not normalize:
            return _Node("record", type(value).__name__, fields=tuple(fields))
        schematic_independent = (
            _schematic_items_independent(value) if type(value) is Schematic else False
        )
        normalized: list[tuple[str, _Node]] = []
        for name, node in fields:
            if node.kind == "list" and _independent(
                value, name, schematic_items_independent=schematic_independent
            ):
                node = _Node("multiset", items=tuple(sorted(node.items, key=_encoded)))
            normalized.append((name, node))
        return _Node("record", type(value).__name__, fields=tuple(normalized))
    if isinstance(value, list | tuple):
        return _Node(
            "tuple" if isinstance(value, tuple) else "list", items=tuple(map(visit, value))
        )
    if isinstance(value, dict):
        if value and all(type(key) is int for key in value):
            budget.charge(sum(len(str(key)) for key in value))
            return _Node(
                "integer_mapping",
                fields=tuple((str(key), visit(value[key])) for key in sorted(value)),
            )
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Content dictionaries require all string keys or all integer keys.")
        budget.charge(sum(6 * len(key) for key in value))
        return _Node("mapping", fields=tuple((key, visit(value[key])) for key in sorted(value)))
    raise TypeError(f"Content contains an unsupported value: {type(value).__name__}.")


def _board_items_independent(node: _Node) -> bool:
    seen: set[str] = set()
    claimed: set[str] = set()
    memberships: dict[str, set[str]] = {}
    pending = [
        (item, True) for name, child in node.fields if name in _BOARD_ITEMS for item in child.items
    ]
    safe = True
    while pending:
        current, root_item = pending.pop()
        if current.kind == "record":
            fields = dict(current.fields)
            if current.value in ("PcbGroup", "PcbGenerator"):
                members: set[str] = set()
                for member in fields["members"].items:
                    if member.kind != "scalar" or not isinstance(json.loads(member.value), str):
                        raise TypeError("Board group members must be strings.")
                    key = str(json.loads(member.value)).casefold()
                    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", key) is None:
                        safe = False
                    members.add(key)
                if claimed & members:
                    safe = False
                claimed.update(members)
                owner = fields.get("id", fields.get("uuid"))
                if owner is not None:
                    memberships[owner.value.casefold()] = {_json(member) for member in members}
            for name, child in current.fields:
                if name not in ("id", "uuid") or child.value == "null":
                    continue
                if child.kind != "scalar":
                    raise TypeError("Board item identifiers must be strings.")
                identifier = json.loads(child.value)
                if not isinstance(identifier, str):
                    raise TypeError("Board item identifiers must be strings.")
                if not identifier:
                    continue
                identifier = identifier.casefold()
                if (
                    re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identifier)
                    is None
                    or identifier in seen
                ):
                    safe = False
                seen.add(identifier)
            children = (
                {"properties", "pads", "zones", "groups", "points", "drawings"}
                if current.value == "Footprint" and root_item
                else {"cells"}
                if current.value == "Table" and root_item
                else set()
            )
            pending.extend(
                (item, False)
                for name, child in current.fields
                if name in children
                for item in child.items
            )
    incoming = dict.fromkeys(memberships, 0)
    for references in memberships.values():
        for reference in references:
            if reference in incoming:
                incoming[reference] += 1
    ready = [key for key, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        owner_key = ready.pop()
        visited += 1
        for reference in memberships[owner_key]:
            if reference in incoming:
                incoming[reference] -= 1
                if incoming[reference] == 0:
                    ready.append(reference)
    return safe and visited == len(memberships)


def _normalize_board(node: _Node, *, independent_items: bool) -> _Node:
    fields: list[tuple[str, _Node]] = []
    for name, child in node.fields:
        normalized = _normalize_board(child, independent_items=independent_items)
        independent = node.kind == "record" and (
            (
                node.value == "Board"
                and (name == "nets" or independent_items and name in _BOARD_ITEMS)
            )
            or (
                node.value == "Footprint"
                and independent_items
                and (Footprint, name) in _INDEPENDENT
            )
        )
        if normalized.kind == "list" and independent:
            normalized = _Node("multiset", items=tuple(sorted(normalized.items, key=_encoded)))
        fields.append((name, normalized))
    return _Node(
        node.kind,
        node.value,
        tuple(fields),
        tuple(
            _normalize_board(child, independent_items=independent_items) for child in node.items
        ),
    )


def _root(value: Footprint | Symbol | SymbolLib | Schematic | Board | ProjectFile) -> _Node:
    if type(value) not in (Footprint, Symbol, SymbolLib, Schematic, Board, ProjectFile):
        raise TypeError(
            "Compare a Footprint, Symbol, SymbolLib, Schematic, Board, or ProjectFile object."
        )
    if type(value) is ProjectFile:
        if type(value.root) is not JsonTree or type(value.root.entries) is not dict:
            raise TypeError("Project content requires a JSON object root.")
        return _Node(
            "record",
            "ProjectFile",
            fields=(("root", _snapshot(value.root.entries, budget=_Budget(), json_only=True)),),
        )
    if type(value) is Board:
        snapshot = _snapshot(value, budget=_Budget(), normalize=False)
        return _normalize_board(snapshot, independent_items=_board_items_independent(snapshot))
    return _snapshot(value, budget=_Budget())


def _signature(node: _Node) -> str:
    digest = hashlib.sha256((_SCHEME + "\n" + _encoded(node)).encode("utf-8")).hexdigest()
    return f"{_SCHEME}:sha256:{digest}"


def content_signature(
    value: Footprint | Symbol | SymbolLib | Schematic | Board | ProjectFile,
) -> str:
    """Return a versioned SHA-256 signature of represented content.

    All public stored fields participate. Only audited independent lists ignore
    order. This operation does not read files or resolve external references.
    """
    return _signature(_root(value))


def _display(node: _Node) -> object:
    if node.kind == "scalar":
        return json.loads(node.value)
    if node.kind == "enum":
        return _display(node.items[0])
    if node.kind in ("record", "mapping", "integer_mapping"):
        fields = {name: _display(child) for name, child in node.fields}
        return {"$type": node.value, **fields} if node.kind == "record" else fields
    return [_display(child) for child in node.items]


def _change(path: tuple[str, ...], before: _Node | None, after: _Node | None) -> ContentChange:
    return ContentChange(
        path=path,
        kind="added" if before is None else "removed" if after is None else "changed",
        before=None if before is None else _json(_display(before)),
        after=None if after is None else _json(_display(after)),
    )


def _identity(node: _Node) -> tuple[str, str, str] | None:
    fields = dict(node.fields)
    if node.value == "RuleArea" and "shape" in fields:
        identity = dict(fields["shape"].fields).get("id")
        if identity is not None and identity.value not in ("null", '""'):
            return node.value, "shape.id", identity.value
    candidates: tuple[str, ...] = ("uuid", "id")
    if node.value in ("Symbol", "SymbolUnit"):
        candidates += ("name",)
    for field in candidates:
        value = fields.get(field)
        if value is not None and value.kind == "scalar" and value.value not in ("null", '""'):
            return node.value, field, value.value
    return None


def _multiset_diff(before: _Node, after: _Node, path: tuple[str, ...]) -> list[ContentChange]:
    before_counts, after_counts = Counter(before.items), Counter(after.items)
    old = list((before_counts - after_counts).elements())
    new = list((after_counts - before_counts).elements())
    old_ids = Counter(_identity(item) for item in before.items)
    new_ids = Counter(_identity(item) for item in after.items)
    new_by_id = {_identity(item): item for item in new if _identity(item) is not None}
    matched: set[_Node] = set()
    changes: list[ContentChange] = []
    for index, item in enumerate(old):
        identity = _identity(item)
        if identity is not None and old_ids[identity] == new_ids[identity] == 1:
            other = new_by_id.get(identity)
            if other is not None:
                matched.add(other)
                label = f"{identity[0]}[{identity[1]}={identity[2]}]"
                changes.extend(_diff(item, other, (*path, label)))
                continue
        changes.append(_change((*path, f"removed[{index}]"), item, None))
    for index, item in enumerate(new):
        if item not in matched:
            changes.append(_change((*path, f"added[{index}]"), None, item))
    return changes


def _diff(before: _Node, after: _Node, path: tuple[str, ...]) -> list[ContentChange]:
    if before == after:
        return []
    if before.kind != after.kind or before.value != after.value:
        return [_change(path, before, after)]
    if before.kind == "multiset":
        return _multiset_diff(before, after, path)
    if before.kind in ("record", "mapping", "integer_mapping"):
        old, new = dict(before.fields), dict(after.fields)
        changes: list[ContentChange] = []
        for key in sorted(old.keys() | new.keys()):
            if key not in old or key not in new:
                changes.append(_change((*path, key), old.get(key), new.get(key)))
            else:
                changes.extend(_diff(old[key], new[key], (*path, key)))
        return changes
    if before.kind == "list" and len(before.items) == len(after.items):
        return [
            change
            for index, (a, b) in enumerate(zip(before.items, after.items, strict=True))
            for change in _diff(a, b, (*path, str(index)))
        ]
    return [_change(path, before, after)]


def compare_content(
    before: Footprint | Symbol | SymbolLib | Schematic | Board | ProjectFile,
    after: Footprint | Symbol | SymbolLib | Schematic | Board | ProjectFile,
) -> ContentComparison:
    """Compare exact represented content and return deterministic differences.

    Matching uses unique stored identifiers or symbol names. Ambiguous items
    remain additions and removals. Positions use their stored coordinate frame.
    """
    old, new = _root(before), _root(after)
    return ContentComparison(_signature(old), _signature(new), tuple(_diff(old, new, ())))
