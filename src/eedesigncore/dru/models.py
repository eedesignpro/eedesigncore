"""Editable custom rule documents with retained source text."""

from __future__ import annotations

import os
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.dru._syntax import (
    MAX_FILE_BYTES,
    Node,
    atom,
    head,
    parse,
    patch,
    quote,
    replace_children,
)
from eedesigncore.exceptions import KicadParseError


class DrcConstraintType(str, Enum):
    """Constraint spellings accepted by KiCad's custom rule reader."""

    ASSERTION = "assertion"
    CLEARANCE = "clearance"
    CREEPAGE = "creepage"
    HOLE_CLEARANCE = "hole_clearance"
    EDGE_CLEARANCE = "edge_clearance"
    HOLE_SIZE = "hole_size"
    HOLE_TO_HOLE = "hole_to_hole"
    COURTYARD_CLEARANCE = "courtyard_clearance"
    SILK_CLEARANCE = "silk_clearance"
    TEXT_HEIGHT = "text_height"
    TEXT_THICKNESS = "text_thickness"
    TRACK_WIDTH = "track_width"
    TRACK_ANGLE = "track_angle"
    TRACK_SEGMENT_LENGTH = "track_segment_length"
    CONNECTION_WIDTH = "connection_width"
    ANNULAR_WIDTH = "annular_width"
    VIA_DIAMETER = "via_diameter"
    VIA_DANGLING = "via_dangling"
    ZONE_CONNECTION = "zone_connection"
    THERMAL_RELIEF_GAP = "thermal_relief_gap"
    THERMAL_SPOKE_WIDTH = "thermal_spoke_width"
    MIN_RESOLVED_SPOKES = "min_resolved_spokes"
    SOLDER_MASK_EXPANSION = "solder_mask_expansion"
    SOLDER_MASK_SLIVER = "solder_mask_sliver"
    SOLDER_PASTE_ABS_MARGIN = "solder_paste_abs_margin"
    SOLDER_PASTE_REL_MARGIN = "solder_paste_rel_margin"
    DISALLOW = "disallow"
    LENGTH = "length"
    SKEW = "skew"
    VIA_COUNT = "via_count"
    DIFF_PAIR_GAP = "diff_pair_gap"
    DIFF_PAIR_UNCOUPLED = "diff_pair_uncoupled"
    PHYSICAL_CLEARANCE = "physical_clearance"
    PHYSICAL_HOLE_CLEARANCE = "physical_hole_clearance"
    BRIDGED_MASK = "bridged_mask"
    MECHANICAL_CLEARANCE = "mechanical_clearance"
    MECHANICAL_HOLE_CLEARANCE = "mechanical_hole_clearance"
    HOLE = "hole"


class _Retained(KicadModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    _node: Node | None = PrivateAttr(default=None)
    _snapshot: str = PrivateAttr(default="")

    def _remember(self, node: Node) -> None:
        self._node = node
        self._snapshot = self.model_dump_json()

    def _unchanged(self) -> bool:
        return self._node is not None and self.model_dump_json() == self._snapshot


class DrcBound(_Retained):
    """An ordered bound or the skew option. Expressions keep their units."""

    kind: Literal["min", "opt", "max", "within_diff_pairs"]
    expression: str = ""

    @model_validator(mode="after")
    def _validate_expression(self) -> Self:
        if self.kind == "within_diff_pairs":
            if self.expression:
                raise ValueError("The differential pair option has no expression")
        else:
            nodes = parse(f"({self.kind} {self.expression})")
            if len(nodes) != 1 or len(nodes[0].children) < 2:
                raise ValueError("Expected one nonempty bound expression")
        return self

    def _render(self) -> str:
        if self._unchanged() and self._node:
            return self._node.text
        if self._node and head(self._node) == self.kind and self.kind != "within_diff_pairs":
            children = self._node.children
            return patch(
                self._node.text,
                [
                    (
                        children[1].start - self._node.start,
                        children[-1].end - self._node.start,
                        self.expression,
                    )
                ],
            )
        return f"({self.kind}{' ' + self.expression if self.expression else ''})"


class DrcConstraint(_Retained):
    """A constraint with ordered bounds or special-form arguments."""

    type: DrcConstraintType
    bounds: list[DrcBound] = Field(default_factory=list)
    arguments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_form(self) -> Self:
        special = {
            DrcConstraintType.DISALLOW,
            DrcConstraintType.ZONE_CONNECTION,
            DrcConstraintType.MIN_RESOLVED_SPOKES,
            DrcConstraintType.ASSERTION,
        }
        if self.type in special:
            if self.bounds:
                raise ValueError("This constraint uses arguments instead of bounds")
            if self.type == DrcConstraintType.DISALLOW:
                if any(value not in _DISALLOW for value in self.arguments):
                    raise ValueError("Unknown disallowed item")
            elif len(self.arguments) != 1:
                raise ValueError("Expected one constraint argument")
            elif self.type == DrcConstraintType.ZONE_CONNECTION and self.arguments[0] not in {
                "solid",
                "thermal_reliefs",
                "none",
            }:
                raise ValueError("Unknown zone connection")
            elif self.type == DrcConstraintType.MIN_RESOLVED_SPOKES and not re.fullmatch(
                r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", self.arguments[0]
            ):
                raise ValueError("Expected a spoke count")
        elif self.arguments:
            raise ValueError("This constraint uses bounds instead of arguments")
        if self.type != DrcConstraintType.SKEW and any(
            bound.kind == "within_diff_pairs" for bound in self.bounds
        ):
            raise ValueError("The differential pair option requires a skew constraint")
        return self

    def _render(self) -> str:
        if self._unchanged() and self._node:
            return self._node.text
        values = ["constraint", self.type.value]
        values.extend(
            quote(value) if self.type == DrcConstraintType.ASSERTION else value
            for value in self.arguments
        )
        values.extend(bound._render() for bound in self.bounds)
        if self._node:
            return replace_children(self._node, values)
        return "(" + " ".join(values) + ")"


class DrcClause(_Retained):
    """A condition, layer selector, or severity in its original position."""

    kind: Literal["condition", "layer", "severity"]
    value: str

    @model_validator(mode="after")
    def _validate_value(self) -> Self:
        if self.kind == "severity" and self.value not in {
            "ignore",
            "warning",
            "error",
            "exclusion",
        }:
            raise ValueError("Unknown severity")
        return self

    def _render(self) -> str:
        if self._unchanged() and self._node:
            return self._node.text
        value = (
            self.value
            if self.kind == "severity"
            or (self.kind == "layer" and self.value in {"inner", "outer"})
            else quote(self.value)
        )
        values = [self.kind, value]
        return replace_children(self._node, values) if self._node else "(" + " ".join(values) + ")"


class DrcRule(_Retained):
    """A named rule. Clause order and duplicate rule names are retained."""

    name: str
    clauses: list[DrcConstraint | DrcClause] = Field(default_factory=list)

    def _render(self) -> str:
        if self._unchanged() and self._node:
            return self._node.text
        if self._node:
            values = [self._node.children[0].text, self._node.children[1].text]
            if atom(self._node.children[1]) != self.name:
                values[1] = quote(self.name)
            values.extend(clause._render() for clause in self.clauses)
            return replace_children(self._node, values)
        return (
            "(rule "
            + quote(self.name)
            + "\n"
            + "".join("\t" + clause._render() + "\n" for clause in self.clauses)
            + ")"
        )


_DISALLOW = set(
    [
        "track",
        "via",
        "through_via",
        "blind_via",
        "buried_via",
        "micro_via",
        "pad",
        "zone",
        "text",
        "graphic",
        "hole",
        "footprint",
    ]
)
_ALIASES = {
    "mechanical_clearance": "physical_clearance",
    "mechanical_hole_clearance": "physical_hole_clearance",
    "hole": "hole_size",
}


def _symbol(node: Node) -> str:
    value = atom(node)
    if not node.text.startswith('"') and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        raise node.error("Quote a numeric name or expression")
    return value


def _constraint(node: Node) -> DrcConstraint:
    if len(node.children) < 2:
        raise node.error("Missing constraint type")
    if node.children[1].text.startswith('"'):
        raise node.error("A constraint type must not be quoted")
    try:
        kind = DrcConstraintType(atom(node.children[1]))
    except ValueError as exc:
        raise node.error("Unknown constraint type") from exc
    result = DrcConstraint.model_construct(type=kind)
    children = node.children[2:]
    if kind in {
        DrcConstraintType.DISALLOW,
        DrcConstraintType.ZONE_CONNECTION,
        DrcConstraintType.MIN_RESOLVED_SPOKES,
        DrcConstraintType.ASSERTION,
    }:
        result.arguments = [atom(child) for child in children]
        if kind == DrcConstraintType.DISALLOW:
            if any(value not in _DISALLOW for value in result.arguments):
                raise node.error("Unknown disallowed item")
        elif len(children) != 1:
            raise node.error("Expected one constraint argument")
        elif kind == DrcConstraintType.ZONE_CONNECTION and result.arguments[0] not in {
            "solid",
            "thermal_reliefs",
            "none",
        }:
            raise node.error("Unknown zone connection")
        elif kind == DrcConstraintType.MIN_RESOLVED_SPOKES and not re.fullmatch(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", result.arguments[0]
        ):
            raise node.error("Expected a spoke count")
        elif kind == DrcConstraintType.MIN_RESOLVED_SPOKES and children[0].text.startswith('"'):
            raise node.error("A spoke count must be an unquoted number")
        elif kind == DrcConstraintType.ASSERTION:
            _symbol(children[0])
    else:
        for child in children:
            key = head(child)
            if key not in {"min", "max", "opt", "within_diff_pairs"}:
                raise child.error("Unknown constraint bound")
            if key == "within_diff_pairs":
                if kind != DrcConstraintType.SKEW or len(child.children) != 1:
                    raise child.error("Invalid differential pair option")
                bound = DrcBound(kind="within_diff_pairs")
            else:
                if len(child.children) < 2:
                    raise child.error("Missing bound expression")
                bound = DrcBound(
                    kind=cast(Literal["min", "max", "opt"], key),
                    expression=child.source[child.children[1].start : child.children[-1].end],
                )
            bound._remember(child)
            result.bounds.append(bound)
    result._remember(node)
    return result


def _rule(node: Node) -> DrcRule:
    if len(node.children) < 2:
        raise node.error("Missing rule name")
    rule = DrcRule(name=_symbol(node.children[1]))
    seen: set[str] = set()
    for child in node.children[2:]:
        key = head(child)
        if key == "constraint":
            constraint = _constraint(child)
            canonical = _ALIASES.get(constraint.type.value, constraint.type.value)
            if canonical in seen:
                raise child.error("Repeated constraint type")
            seen.add(canonical)
            rule.clauses.append(constraint)
        elif key in {"condition", "layer", "severity"}:
            if len(child.children) != 2:
                raise child.error("Expected one clause value")
            value = _symbol(child.children[1])
            if key == "severity" and value not in {"ignore", "warning", "error", "exclusion"}:
                raise child.error("Unknown severity")
            clause = DrcClause(kind=key, value=value)  # type: ignore[arg-type]
            clause._remember(child)
            rule.clauses.append(clause)
        else:
            raise child.error("Unknown rule clause")
    rule._remember(node)
    return rule


class DrcFile(KicadModel):
    """Custom rules with lossless file output and isolated marked blocks."""

    version: int | None = 1
    rules: list[DrcRule] = Field(default_factory=list)
    _source: str | None = PrivateAttr(default=None)
    _roots: list[Node] = PrivateAttr(default_factory=list)
    _version: int | None = PrivateAttr(default=None)
    _path: Path | None = PrivateAttr(default=None)
    _bytes: bytes | None = PrivateAttr(default=None)

    @classmethod
    def from_string(cls, text: str) -> Self:
        """Read outer rule syntax without evaluating condition expressions."""
        roots = parse(text)
        result = cls(version=None)
        for index, node in enumerate(roots):
            key = head(node)
            if index == 0 and key != "version":
                raise node.error("Missing version statement")
            if key == "version":
                if (
                    len(node.children) != 2
                    or node.children[1].text.startswith('"')
                    or not re.fullmatch(r"[+-]?\d+", atom(node.children[1]))
                ):
                    raise node.error("Expected a version number")
                try:
                    result.version = int(atom(node.children[1]))
                except ValueError as exc:
                    raise node.error("Version number exceeds the integer limit") from exc
            elif key == "rule":
                try:
                    result.rules.append(_rule(node))
                except ValidationError as exc:
                    raise node.error("Invalid rule structure") from exc
            else:
                raise node.error("Expected a rule or version statement")
        result._source, result._roots, result._version = text, roots, result.version
        return result

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        """Read UTF-8 source and retain the bytes for stale-write detection."""
        target = Path(path).resolve()
        with target.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise KicadParseError("Custom rule file exceeds the size limit", str(target))
        try:
            result = cls.from_string(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise KicadParseError("Custom rule file is not UTF-8", str(target)) from exc
        except KicadParseError as exc:
            exc.file_path = str(target)
            raise
        result._path, result._bytes = target, data
        return result

    def _render(self) -> str:
        if self._source is None:
            header = f"(version {self.version})\n" if self.version is not None else ""
            return header + "".join("\n" + rule._render() + "\n" for rule in self.rules)
        original_rules = [node for node in self._roots if head(node) == "rule"]
        original_ids = {id(node) for node in original_rules}
        retained_ids = [id(rule._node) for rule in self.rules if id(rule._node) in original_ids]
        if len(set(retained_ids)) != len(retained_ids):
            raise ValueError("A retained rule cannot occupy multiple positions")
        retained_set = set(retained_ids)
        slots = [node for node in original_rules if id(node) in retained_set]
        if _MARKER.search(self._source) and (
            retained_ids != [id(node) for node in slots] or len(retained_ids) != len(self.rules)
        ):
            raise ValueError("Use block replacement to insert or reorder rules in a marked file")
        ending = "\r\n" if "\r\n" in self._source else "\n"
        replacements: dict[int, str] = {}
        pending = ""
        slot_index = 0
        for rule in self.rules:
            rendered = rule._render()
            if id(rule._node) not in original_ids:
                pending += rendered.replace("\r\n", "\n").replace("\n", ending) + ending
            else:
                replacements[id(slots[slot_index])] = pending + rendered
                slot_index += 1
                pending = ""
        edits: list[tuple[int, int, str]] = []
        for node in original_rules:
            edits.append(
                (
                    node.start,
                    node.end,
                    replacements.get(id(node), ""),
                )
            )
        if pending:
            position = slots[-1].end if slots else len(self._source)
            edits.append((position, position, ending + pending))
        if self.version != self._version:
            versions = [node for node in self._roots if head(node) == "version"]
            for node in versions:
                edits.append(
                    (
                        node.start,
                        node.end,
                        f"(version {self.version})" if self.version is not None else "",
                    )
                )
            if not versions and self.version is not None:
                edits.append((0, 0, f"(version {self.version})\n"))
        return patch(self._source, edits)

    def to_string(self) -> str:
        """Return valid outer syntax with line-feed endings."""
        text = self._checked_render()
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _checked_render(self) -> str:
        type(self).model_validate(self.model_dump())
        text = self._render()
        result = self.from_string(text)
        if result.model_dump() != self.model_dump():
            raise ValueError("Rule text does not match the requested document")
        return text

    def to_file(
        self,
        path: str | Path,
        *,
        line_ending: Literal["preserve", "lf", "crlf", "native"] = "preserve",
    ) -> None:
        """Atomically save. Refuse a stale write to the loaded source path."""
        target = Path(path).resolve()
        with _writer_lock(target):
            self._write(target, line_ending)

    def _write(self, target: Path, line_ending: str = "preserve") -> None:
        text = self._checked_render()
        if line_ending != "preserve":
            if line_ending not in {"lf", "crlf", "native"}:
                raise ValueError("Unknown line ending")
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            ending = (
                os.linesep
                if line_ending == "native"
                else "\r\n"
                if line_ending == "crlf"
                else "\n"
            )
            text = text.replace("\n", ending)
        data = text.encode("utf-8")
        expected = self._bytes if target == self._path else None
        _save(target, data, expected)
        self._path, self._bytes = target, data

    def block_names(self) -> tuple[str, ...]:
        """List unambiguous top-level BEGIN/END blocks in file order."""
        return tuple(_blocks(self._checked_render()))

    def replace_block(self, name: str, rules: list[DrcRule] | str) -> None:
        """Replace one block's contents and retain all text outside its markers."""
        source = self._checked_render()
        blocks = _blocks(source)
        if name not in blocks:
            raise ValueError(f"Custom rule block is missing: {name}")
        content = _block_content(rules)
        ending = "\r\n" if "\r\n" in source else "\n"
        content = content.replace("\r\n", "\n").replace("\n", ending)
        start, end = blocks[name]
        self._adopt(source[:start] + content + source[end:])

    def create_block(
        self, name: str, rules: list[DrcRule] | str, *, before: str | None = None
    ) -> None:
        """Create a block before a named block, or explicitly append it."""
        if not name or name.strip() != name or any(c in name for c in "\r\n"):
            raise ValueError("Invalid custom rule block name")
        source = self._checked_render()
        blocks = _blocks(source)
        if name in blocks:
            raise ValueError(f"Custom rule block already exists: {name}")
        position = len(source)
        if before is not None:
            if before not in blocks:
                raise ValueError(f"Custom rule block is missing: {before}")
            match = next(
                match
                for match in _MARKER.finditer(source)
                if match.group(1) == "BEGIN" and match.group(2).rstrip() == before
            )
            position = match.start()
        ending = "\r\n" if "\r\n" in source else "\n"
        text = f"# BEGIN {name}\n" + _block_content(rules) + f"# END {name}\n"
        if position and source[position - 1] != "\n":
            text = "\n" + text
        self._adopt(
            source[:position]
            + text.replace("\r\n", "\n").replace("\n", ending)
            + source[position:]
        )

    def _adopt(self, text: str) -> None:
        replacement = self.from_string(text)
        self.rules, self.version = replacement.rules, replacement.version
        self._source, self._roots, self._version = text, replacement._roots, replacement._version


_MARKER = re.compile(r"^[ \t]*# (BEGIN|END) ([^\r\n]+)[ \t]*\r?\n?", re.MULTILINE)


def _blocks(text: str) -> dict[str, tuple[int, int]]:
    roots = parse(text)
    starts = [node.start for node in roots]
    non_rules = [node.start for node in roots if head(node) != "rule"]
    result: dict[str, tuple[int, int]] = {}
    opened: tuple[str, int] | None = None
    for match in _MARKER.finditer(text):
        index = bisect_right(starts, match.start()) - 1
        if index >= 0 and match.start() < roots[index].end:
            raise ValueError("Custom rule block marker is inside a rule")
        action, name = match.group(1), match.group(2).rstrip()
        if action == "BEGIN":
            if opened is not None or name in result:
                raise ValueError("Nested or duplicate custom rule block")
            opened = name, match.end()
        else:
            if opened is None or opened[0] != name:
                raise ValueError("Unmatched custom rule block marker")
            first_non_rule = bisect_left(non_rules, opened[1])
            if first_non_rule < len(non_rules) and non_rules[first_non_rule] < match.start():
                raise ValueError("A managed block cannot contain the version statement")
            result[name] = opened[1], match.start()
            opened = None
    if opened is not None:
        raise ValueError("Unclosed custom rule block")
    return result


def _block_content(rules: list[DrcRule] | str) -> str:
    if isinstance(rules, str):
        text = rules
    else:
        DrcFile(rules=rules)._checked_render()
        text = "\n\n".join(rule._render() for rule in rules)
    for node in parse(text):
        if head(node) != "rule":
            raise node.error("A generated block can contain only rules and comments")
    DrcFile.from_string("(version 1)\n" + text)
    if _MARKER.search(text):
        raise ValueError("Block content cannot contain block markers")
    return text + ("\n" if text and not text.endswith("\n") else "")


def _save(target: Path, data: bytes, expected: bytes | None) -> None:
    from eedesigncore.guarded_save import _staged_bytes

    with _staged_bytes(target, data) as temporary:
        if expected is not None:
            try:
                with target.open("rb") as stream:
                    current = stream.read(len(expected) + 1)
            except FileNotFoundError:
                current = None
            if current != expected:
                raise ValueError(
                    "Custom rule file changed after it was read. Read it again before saving."
                )
        os.replace(temporary, target)


@contextmanager
def _writer_lock(target: Path) -> Iterator[None]:
    lock = target.with_name(target.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Another custom rule update holds the file lock") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def update_drc_block(path: str | Path, *, name: str, rules: list[DrcRule] | str) -> None:
    """Read fresh content and replace a block under a cooperative writer lock."""
    target = Path(path).resolve()
    with _writer_lock(target):
        document = DrcFile.from_file(target)
        document.replace_block(name, rules)
        document._write(target)
