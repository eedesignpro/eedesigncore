"""Compare two designs and return labelled change records.

This is a second view of :func:`~eedesigncore.compare_content`. It reads the same
normalised content, so the same lists ignore file order and the same items match
by their stored identifier. It adds a category and a readable name to each
change, an optional number tolerance, and, for a board, a summary per net.
``compare_content`` itself is not changed.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from eedesigncore.comparison import _display, _identity, _json, _Node, _root, _signature
from eedesigncore.design_changes.labels import (
    categorize,
    is_item,
    item_name,
    root_name,
    secondary_key,
)
from eedesigncore.design_changes.nets import net_changes
from eedesigncore.design_changes.records import (
    ChangeCategory,
    ChangeKind,
    DesignChange,
    DesignComparison,
)
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.pro.models import ProjectFile
from eedesigncore.sch.models import Schematic
from eedesigncore.sym.models import SymbolLib

Design = Footprint | SymbolLib | Schematic | Board | ProjectFile

#: The file types :func:`load_design` reads, by suffix.
DESIGN_SUFFIXES: dict[str, type[Design]] = {
    ".kicad_pcb": Board,
    ".kicad_sch": Schematic,
    ".kicad_mod": Footprint,
    ".kicad_sym": SymbolLib,
    ".kicad_pro": ProjectFile,
}


def load_design(path: str | Path, *, kind: str | None = None) -> Design:
    """Read a board, schematic, footprint, symbol library, or project file.

    The suffix of *path*, or of *kind* when given, selects the reader. *kind*
    exists for a file whose name has no KiCad suffix, such as a temporary copy
    that git hands to a diff command.

    Raises:
        ValueError: when the suffix is not one of :data:`DESIGN_SUFFIXES`.
    """
    suffix = Path(kind if kind is not None else path).suffix.lower()
    reader = DESIGN_SUFFIXES.get(suffix)
    if reader is None:
        raise ValueError(f"Cannot compare a {suffix or 'suffix-less'} file.")
    return reader.from_file(path)


@dataclass(frozen=True)
class _Frame:
    item_type: str
    item: str
    identity: str
    node: _Node | None


def _frame(node: _Node, parent: _Frame, *, element: bool = False) -> _Frame:
    """Open a frame for *node*, or keep *parent*.

    An *element* of an unordered list is always its own item: a footprint line
    with no stored identifier is still one line, not the footprint.
    """
    if node.kind != "record" or not (element or is_item(node)):
        return parent
    identity = _identity(node)
    return _Frame(
        node.value,
        item_name(node.value, node, parent.item),
        "" if identity is None else str(json.loads(identity[2])),
        node,
    )


def _key(node: _Node) -> tuple[str, str, str] | None:
    """The stored identifier, or for an item without one, its number or name key."""
    return _identity(node) or secondary_key(node)


def _keyed(node: _Node) -> bool:
    """True when every item is a record with its own unique key."""
    keys = [_key(item) for item in node.items]
    return bool(keys) and None not in keys and len(set(keys)) == len(keys)


def _label(node: _Node, index: int) -> str:
    key = _key(node)
    return f"{key[0]}[{key[1]}={key[2]}]" if key is not None else f"matched[{index}]"


def _number(node: _Node) -> float | None:
    if node.kind != "scalar":
        return None
    value = json.loads(node.value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class _Walker:
    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance
        self.changes: list[DesignChange] = []

    def same(self, before: _Node, after: _Node) -> bool:
        if before == after:
            return True
        if self.tolerance <= 0.0 or before.kind != after.kind or before.value != after.value:
            old, new = _number(before), _number(after)
            return (
                self.tolerance > 0.0
                and old is not None
                and new is not None
                and abs(old - new) <= self.tolerance
            )
        if before.kind in ("list", "tuple", "multiset", "enum"):
            return len(before.items) == len(after.items) and all(
                self.same(a, b) for a, b in zip(before.items, after.items, strict=True)
            )
        if before.kind in ("record", "mapping", "integer_mapping"):
            old_fields, new_fields = dict(before.fields), dict(after.fields)
            return old_fields.keys() == new_fields.keys() and all(
                self.same(old_fields[key], new_fields[key]) for key in old_fields
            )
        return False

    def emit(
        self,
        frame: _Frame,
        field: tuple[str, ...],
        path: tuple[str, ...],
        before: _Node | None,
        after: _Node | None,
        category: ChangeCategory | None = None,
    ) -> None:
        kind: ChangeKind = "added" if before is None else "removed" if after is None else "changed"
        self.changes.append(
            DesignChange(
                category=category or categorize(frame.item_type, frame.node, field),
                kind=kind,
                item_type=frame.item_type,
                item=frame.item,
                identity=frame.identity,
                field=".".join(field),
                before=None if before is None else _json(_display(before)),
                after=None if after is None else _json(_display(after)),
                path=path,
            )
        )

    def walk(
        self,
        before: _Node,
        after: _Node,
        frame: _Frame,
        field: tuple[str, ...],
        path: tuple[str, ...],
    ) -> None:
        if self.same(before, after):
            return
        if before.kind != after.kind or before.value != after.value:
            self.emit(frame, field, path, before, after)
        elif before.kind == "multiset":
            self.multiset(before, after, frame, field, path)
        elif before.kind in ("record", "mapping", "integer_mapping"):
            old, new = dict(before.fields), dict(after.fields)
            for key in sorted(old.keys() | new.keys()):
                if key in old and key in new:
                    self.walk(old[key], new[key], frame, (*field, key), (*path, key))
                else:
                    self.emit(frame, (*field, key), (*path, key), old.get(key), new.get(key))
        elif before.kind == "list" and _keyed(before) and _keyed(after):
            self.multiset(before, after, frame, field, path)
            self.order(before, after, frame, field, path)
        elif (
            before.kind == "list"
            and len(before.items) == len(after.items)
            and any(item.kind == "record" for item in (*before.items, *after.items))
        ):
            for index, (a, b) in enumerate(zip(before.items, after.items, strict=True)):
                if is_item(a) and a.value == b.value:
                    self.walk(a, b, _frame(a, frame), (), (*path, str(index)))
                else:
                    self.walk(a, b, frame, (*field, str(index)), (*path, str(index)))
        else:
            self.emit(frame, field, path, before, after)

    def order(
        self,
        before: _Node,
        after: _Node,
        frame: _Frame,
        field: tuple[str, ...],
        path: tuple[str, ...],
    ) -> None:
        """Report a new order of the items both sides share, as one cosmetic change."""
        old_ids = [_key(item) for item in before.items]
        new_ids = [_key(item) for item in after.items]
        shared = set(old_ids) & set(new_ids)
        old_order = [identity[2] for identity in old_ids if identity in shared and identity]
        new_order = [identity[2] for identity in new_ids if identity in shared and identity]
        if old_order != new_order:
            self.changes.append(
                DesignChange(
                    category="cosmetic",
                    kind="changed",
                    item_type=frame.item_type,
                    item=frame.item,
                    identity=frame.identity,
                    field=".".join((*field, "order")),
                    before=_json([json.loads(value) for value in old_order]),
                    after=_json([json.loads(value) for value in new_order]),
                    path=(*path, "order"),
                )
            )

    def multiset(
        self,
        before: _Node,
        after: _Node,
        frame: _Frame,
        field: tuple[str, ...],
        path: tuple[str, ...],
    ) -> None:
        """Pair the items that differ, then walk each pair and report the rest.

        Pairs come from a key unique on both sides, then (with a tolerance) from
        content equal within it, then from the one remaining item of a type on
        each side. Every lookup is indexed, so the work grows with the item count.
        """
        old = list((Counter(before.items) - Counter(after.items)).elements())
        new = list((Counter(after.items) - Counter(before.items)).elements())
        old_keys = Counter(_key(item) for item in before.items)
        new_keys = Counter(_key(item) for item in after.items)
        by_key: dict[tuple[str, str, str], int] = {}
        for j, other in enumerate(new):
            key = _key(other)
            if key is not None:
                by_key.setdefault(key, j)
        matched: dict[int, int] = {}
        taken: set[int] = set()
        for i, item in enumerate(old):
            key = _key(item)
            if key is None or not old_keys[key] == new_keys[key] == 1 or key not in by_key:
                continue
            matched[i] = by_key[key]
            taken.add(by_key[key])

        def unkeyed(nodes: list[_Node], used: set[int] | dict[int, int]) -> dict[str, list[int]]:
            groups: dict[str, list[int]] = {}
            for index, node in enumerate(nodes):
                if index not in used and node.kind == "record" and _key(node) is None:
                    groups.setdefault(node.value, []).append(index)
            return groups

        if self.tolerance > 0.0:
            spare = unkeyed(new, taken)
            for kind, indexes in unkeyed(old, matched).items():
                for i in indexes:
                    for j in spare.get(kind, ()):
                        if j not in taken and self.same(old[i], new[j]):
                            matched[i] = j
                            taken.add(j)
                            break
        spare = unkeyed(new, taken)
        for kind, indexes in unkeyed(old, matched).items():
            if len(indexes) == 1 and len(spare.get(kind, ())) == 1:
                matched[indexes[0]] = spare[kind][0]
                taken.add(spare[kind][0])

        def place(node: _Node) -> tuple[_Frame, tuple[str, ...]]:
            if node.kind == "record":
                return _frame(node, frame, element=True), ()
            return frame, field

        for i, item in enumerate(old):
            target, inner = place(item)
            if i in matched:
                self.walk(item, new[matched[i]], target, inner, (*path, _label(item, i)))
            else:
                self.emit(target, inner, (*path, f"removed[{i}]"), item, None)
        for j, item in enumerate(new):
            if j not in taken:
                target, inner = place(item)
                self.emit(target, inner, (*path, f"added[{j}]"), None, item)


def compare_designs(
    before: Design | str | Path,
    after: Design | str | Path,
    *,
    tolerance: float = 0.0,
    nets: bool = True,
) -> DesignComparison:
    """Compare two designs and return one labelled record per change.

    *before* and *after* are two models of the same type, or two file paths that
    :func:`load_design` reads. Two revisions of one file are two paths, or two
    models loaded from the text of each revision.

    *tolerance* applies to every number: millimetres for a length, degrees for
    an angle. The default, zero, is exact. KiCad stores whole nanometres, so a
    file round trip needs no tolerance. Items with a stored identifier always
    match by it. With a tolerance above zero, items with no identifier also
    match when every number agrees within it.

    For a board, *nets* adds one record per net whose pads or routing changed,
    with ``item_type`` ``"Net"``. A net renamed with the same pads is one record.

    An empty result means the two designs are equal under *tolerance*.

    Raises:
        TypeError: when the two sides are of different types, or not a design.
        ValueError: when *tolerance* is negative or not finite.
    """
    if not math.isfinite(tolerance) or not 0.0 <= tolerance < 1.0:
        raise ValueError(
            "The tolerance must be at least zero and below 1. A tolerance of 1 or more "
            "would hide changes to counts and indexes, such as a unit number."
        )
    old = load_design(before) if isinstance(before, str | Path) else before
    new = load_design(after) if isinstance(after, str | Path) else after
    if type(old) is not type(new):
        raise TypeError(
            f"Compare two designs of one type, not {type(old).__name__} and {type(new).__name__}."
        )
    old_root, new_root = _root(old), _root(new)
    root_type = old_root.value
    frame = _Frame(root_type, root_name(root_type, old_root), "", old_root)
    walker = _Walker(tolerance)
    walker.walk(old_root, new_root, frame, (), ())
    if nets and isinstance(old, Board) and isinstance(new, Board):
        walker.changes.extend(net_changes(old, new, tolerance))
    return DesignComparison(
        before_signature=_signature(old_root),
        after_signature=_signature(new_root),
        tolerance=tolerance,
        changes=tuple(walker.changes),
    )


__all__ = ["DESIGN_SUFFIXES", "Design", "compare_designs", "load_design"]
