"""Read-only design inspection. This file also runs with Python's standard library alone."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeAlias, cast
from urllib.parse import quote, urlsplit

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Object: TypeAlias = dict[str, Json]
_MAX_BYTES = 256 * 1024 * 1024
_MAX_REPLY = 2 * 1024 * 1024


def _object(value: Json, label: str = "value") -> Object:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _array(value: Json, label: str = "value") -> list[Json]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array.")
    return value


def _text(value: Json, label: str = "value") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    return value


def _number(value: Json, label: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} exceeds the numeric range.") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number.")
    return result


def _strings(value: Json, label: str = "value") -> list[str]:
    return [_text(item, label) for item in _array(value, label)]


def _validate(value: Json, *, max_bytes: int = _MAX_BYTES) -> None:
    stack = [(value, 0)]
    count = 0
    byte_budget = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 4000000 or depth > 64:
            raise ValueError("Inspection input exceeds the structure limit.")
        byte_budget += 16
        if isinstance(item, str):
            byte_budget += len(item) * (6 if item.isascii() else 12)
        elif isinstance(item, int):
            byte_budget += item.bit_length() // 3 + 3
        elif isinstance(item, dict):
            byte_budget += sum(
                len(key) * (6 if key.isascii() else 12) for key in item if isinstance(key, str)
            )
        if byte_budget > max_bytes:
            raise ValueError("Inspection exceeds the conservative serialized-size limit.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Object keys must be text.")
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Inspection input contains a non-finite number.")
        elif item is not None and not isinstance(item, str | bool | int | float):
            raise ValueError("Inspection input must contain JSON values only.")


def _json(text: str) -> Json:
    if len(text) > _MAX_BYTES:
        raise ValueError("Inspection input exceeds the byte limit.")

    def pairs(values: list[tuple[str, Json]]) -> Object:
        result: Object = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def constant(value: str) -> Json:
        raise ValueError("Non-finite JSON number.")

    try:
        result = cast(Json, json.loads(text, object_pairs_hook=pairs, parse_constant=constant))
    except RecursionError as error:
        raise ValueError("Inspection input exceeds the nesting limit.") from error
    _validate(result)
    return result


def _equal(left: Json, right: Json) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _dump(value: Json) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _hash(value: Json) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _read(path: str | Path) -> str:
    raw = str(path)
    if raw.replace("\\", "/").startswith("//") or "://" in raw:
        raise ValueError("Inspection requires a local file path.")
    with Path(path).open("rb") as stream:
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError("Inspection input exceeds the byte limit.")
    return content.decode("utf-8-sig")


_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "native_sources": ("List loaded native sources available for exact inspection.", ()),
    "native_record": (
        "Read selected native fields by a confined object path.",
        ("source", "path", "fields"),
    ),
    "usage": ("Describe available queries and their accepted inputs.", ("operation",)),
    "source_manifest": ("Read extraction versions, byte-hash meaning, and source coverage.", ()),
    "source_files": (
        "Page actual file reads and explicitly absent expected files.",
        ("path", "role", "status", "limit", "cursor"),
    ),
    "overview": ("Read counts, coverage, issue counts, and available evidence.", ()),
    "search": (
        "Find records by literal text or exact stored fields.",
        ("kind", "text", "field", "equals", "missing", "limit", "cursor"),
    ),
    "component": (
        "Read one component and a page of its pins.",
        ("reference", "id", "scope", "limit", "cursor"),
    ),
    "net": ("Read one net and a page of its endpoints.", ("name", "id", "limit", "cursor")),
    "pin": (
        "Read numbered pins on one unambiguous component.",
        ("reference", "id", "scope", "number", "limit", "cursor"),
    ),
    "pads": (
        "Read physical pads, coordinates, layers, and saved functions on one component.",
        ("reference", "id", "scope", "number", "limit", "cursor"),
    ),
    "zone_areas": (
        "Estimate zone outline and saved-fill area by net and copper layer.",
        ("name", "id", "layer", "limit", "cursor"),
    ),
    "circuit": (
        "Read components on a net with their other pin assignments. No inferred conduction.",
        ("name", "id", "scope", "pin_limit", "limit", "cursor"),
    ),
    "common_nets": (
        "Find nets shared by specified components.",
        ("references", "limit", "cursor"),
    ),
    "neighbors": (
        "Find component origins within a radius in millimeters.",
        ("reference", "id", "radius_mm", "side", "limit", "cursor"),
    ),
    "trace": (
        "Find a structural path through selected passive relationships.",
        ("from_net", "to_net", "relationship_kinds", "max_visited"),
    ),
    "issues": (
        "Read unresolved associations and other exported diagnostics.",
        ("code", "limit", "cursor"),
    ),
    "records": ("Read exact records without expanding their links.", ("ids", "limit", "cursor")),
    "sources": (
        "Read provenance and optional caller-supplied repository links.",
        ("ids", "repository_url", "revision", "paths", "limit", "cursor"),
    ),
    "geometry": (
        "Read native board geometry. Requires a loaded board.",
        ("net", "layer", "kind", "bbox", "limit", "cursor"),
    ),
}


class DesignInspector:
    """A reusable read-only inspection snapshot with bounded query results."""

    def __init__(self, document: Object) -> None:
        document = _object(document, "netbom")
        _validate(document)
        if document.get("schema_version") != "netbom-1":
            raise ValueError("Unsupported netbom schema.")
        self._document = copy.deepcopy(document)
        self._records = {
            key: _object(value, "record")
            for key, value in _object(self._document.get("records"), "records").items()
        }
        if len(self._records) > 250000:
            raise ValueError("Inspection exceeds the record limit.")
        self._kinds: dict[str, list[str]] = defaultdict(list)
        self._references: dict[str, list[str]] = defaultdict(list)
        self._nets: dict[str, list[str]] = defaultdict(list)
        self._search: dict[str, str] = {}
        for key, record in sorted(self._records.items()):
            if record.get("id") != key:
                raise ValueError("Record key and identifier differ.")
            kind = _text(record.get("kind"), "record kind")
            data = _object(record.get("data", {}), "record data")
            for targets in _object(record.get("links", {}), "record links").values():
                if any(target not in self._records for target in _strings(targets, "links")):
                    raise ValueError("Dangling record link.")
            for evidence in _array(record.get("evidence", []), "evidence"):
                entry = _object(evidence, "evidence")
                source = entry.get("source_id")
                if (
                    not isinstance(source, str)
                    or source not in self._records
                    or self._records[source].get("kind") != "source"
                ):
                    raise ValueError("Missing source evidence.")
                if any(
                    target not in self._records for target in _strings(entry.get("inputs", []))
                ):
                    raise ValueError("Missing derivation evidence.")
            self._kinds[kind].append(key)
            if kind == "component" and isinstance(data.get("reference"), str):
                self._references[cast(str, data["reference"])].append(key)
            if kind == "net" and isinstance(data.get("name"), str):
                self._nets[cast(str, data["name"])].append(key)
            words = [str(v) for v in data.values() if isinstance(v, str | int | float | bool)]
            fields = data.get("fields", {})
            if isinstance(fields, dict):
                words.extend(str(v) for v in fields.values())
            self._search[key] = "\n".join(words).casefold()
        self._revision = _hash(self._document)
        self._native_geometry: Callable[[Object], Object] | None = None
        self._native_operations: dict[str, Callable[[Object], Object]] = {}

    @classmethod
    def open(cls, path: str | Path) -> DesignInspector:
        """Read netbom JSON or complete JSONL."""
        source = Path(path)
        text = _read(source)
        if source.suffix.lower() == ".jsonl":
            lines = text.splitlines()
            if not lines:
                raise ValueError("Empty netbom JSONL.")
            document = _object(_object(_json(lines[0])).get("manifest"), "JSONL manifest")
            records: Object = {}
            for line in lines[1:]:
                record = _object(_json(line), "JSONL record")
                key = _text(record.get("id"), "record id")
                if key in records:
                    raise ValueError("Duplicate JSONL record.")
                records[key] = record
            document["records"] = records
            document["indexes"] = {}
        else:
            document = _object(_json(text), "netbom")
        return cls(document)

    def _data(self, key: str) -> Object:
        return _object(self._records[key].get("data", {}))

    def _links(self, key: str, relation: str) -> list[str]:
        return cast(
            list[str], _array(_object(self._records[key].get("links", {})).get(relation, []))
        )

    def _resolve(self, inputs: Object, kind: str, name: str) -> str:
        scope = _text(inputs.get("scope", "all"), "scope")
        self._in_scope(None, scope)
        identifier = inputs.get("id")
        if identifier is not None:
            key = _text(identifier, "id")
            if inputs.get(name) is not None:
                raise ValueError("Supply an identifier or a name, not both.")
            if key not in self._records or self._records[key].get("kind") != kind:
                raise ValueError("Unknown identifier or wrong record kind.")
            if kind == "component" and not self._in_scope(key, scope):
                raise ValueError("Component is outside the selected scope.")
            return key
        value = _text(inputs.get(name), name)
        candidates = (self._references if kind == "component" else self._nets).get(value, [])
        if kind == "component":
            candidates = [key for key in candidates if self._in_scope(key, scope)]
        if len(candidates) != 1:
            raise ValueError(
                f"Expected one {kind} for {value!r}. Candidate IDs: {candidates[:32]}"
            )
        return candidates[0]

    def _compact(self, key: str) -> Object:
        record, data = self._records[key], self._data(key)
        result: Object = {"id": key, "kind": record["kind"]}
        for name in (
            "reference",
            "name",
            "number",
            "value",
            "footprint",
            "side",
            "layer",
            "position",
            "placement",
            "effective_name",
            "effective_electrical_type",
            "lib_id",
            "unit",
            "body_style",
            "routing",
            "netclass_status",
            "content",
            "code",
            "message",
            "fields",
            "empty_fields",
            "absent_standard_fields",
            "connection_status",
            "symbol_association",
            "netclasses",
            "board_position",
            "local_position",
            "layers",
            "saved_pin_function",
            "saved_pin_type",
            "assignment_status",
        ):
            if name in data:
                result[name] = data[name]
        result["issue_count"] = len(self._links(key, "issues"))
        return result

    def _page(
        self,
        operation: str,
        inputs: Object,
        items: list[Json],
        render: Callable[[Json], Json] | None = None,
    ) -> Object:
        limit = inputs.get("limit", 8 if operation == "circuit" else 25)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("Page limit must be an integer from 1 through 200.")
        query = _hash({key: value for key, value in inputs.items() if key != "cursor"})
        offset = 0
        if inputs.get("cursor") is not None:
            cursor = _text(inputs["cursor"], "cursor")
            if len(cursor) > 1024:
                raise ValueError("Invalid cursor.")
            try:
                decoded = _object(_json(base64.urlsafe_b64decode(cursor.encode()).decode()))
            except (ValueError, UnicodeError) as error:
                raise ValueError("Invalid cursor.") from error
            if (
                decoded.get("revision") != self._revision
                or decoded.get("query") != query
                or decoded.get("operation") != operation
            ):
                raise ValueError("Cursor belongs to another snapshot or query.")
            value = decoded.get("offset")
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= len(items)
            ):
                raise ValueError("Invalid cursor offset.")
            offset = value
        stop = offset + limit
        next_cursor = None
        if stop < len(items):
            payload: Object = {
                "revision": self._revision,
                "query": query,
                "operation": operation,
                "offset": stop,
            }
            next_cursor = base64.urlsafe_b64encode(_dump(payload).encode()).decode()
        selected = items[offset:stop]
        return {
            "items": [render(item) for item in selected] if render else selected,
            "total": len(items),
            "next_cursor": next_cursor,
        }

    def execute(self, operation: str, inputs: Object | None = None) -> Object:
        """Execute a discovered read operation. Unknown fields and unavailable evidence fail."""
        if not isinstance(operation, str):
            raise ValueError("Inspection operation must be text.")
        values = {} if inputs is None else _object(inputs, "inputs")
        _validate(values)
        if operation not in _COMMANDS:
            raise ValueError("Unknown inspection operation. Read usage first.")
        unexpected = set(values) - set(_COMMANDS[operation][1])
        if unexpected:
            raise ValueError(f"Unknown inspection inputs: {sorted(unexpected)}")
        data = self._execute(operation, values)
        result: Object = {
            "schema_version": "design-inspection-1",
            "operation": operation,
            "revision_id": self._revision,
            "data": data,
        }
        _validate(result, max_bytes=_MAX_REPLY)
        if len(_dump(result)) > _MAX_REPLY:
            raise ValueError("Inspection response exceeds the size limit. Narrow the query.")
        return copy.deepcopy(result)

    def _pin(self, key: str) -> Object:
        result = self._compact(key)
        associated = self._links(key, "symbol_pins")
        if (
            sum(
                len(self._links(key, name))
                for name in ("component", "nets", "symbol_pins", "issues", "no_connects")
            )
            > 512
        ):
            raise ValueError("Pin detail exceeds the association limit. Read individual records.")
        result["components"] = [self._compact(v) for v in self._links(key, "component")]
        result["nets"] = [self._compact(v) for v in self._links(key, "nets")]
        pads = self._links(key, "pads")
        result["pad_count"] = len(pads)
        result["pads"] = [self._pad(value) for value in pads[:8]]
        result["pads_truncated"] = len(pads) > 8
        result["functions"] = [
            {
                "id": value,
                **{
                    name: self._data(value).get(name)
                    for name in (
                        "number",
                        "base_name",
                        "effective_name",
                        "effective_electrical_type",
                        "selection_status",
                        "alternate_selection",
                        "no_connect_status",
                        "sheet_position",
                    )
                },
            }
            for value in associated
        ]
        issues = set(self._links(key, "issues"))
        for pin in associated:
            linked = self._links(pin, "issues")
            if len(linked) > 512:
                raise ValueError("Pin detail exceeds the issue limit.")
            issues.update(linked)
        result["issues"] = cast(Json, sorted(issues))
        result["no_connects"] = cast(Json, self._links(key, "no_connects"))
        return result

    def _in_scope(self, key: str | None, scope: str) -> bool:
        if scope not in ("all", "board", "schematic_only"):
            raise ValueError("Scope must be all, board, or schematic_only.")
        if key is None or scope == "all":
            return True
        placed = bool(self._links(key, "board"))
        return placed if scope == "board" else not placed

    def _pad(self, key: str) -> Object:
        result = self._compact(key)
        result["coordinate_units"] = "mm"
        result["coordinate_frame"] = "board"
        result["local_coordinate_frame"] = "footprint"
        result["position_status"] = (
            "available" if self._data(key).get("board_position") is not None else "unavailable"
        )
        result["components"] = cast(Json, self._links(key, "component"))
        result["nets"] = [
            {"id": net, "name": self._data(net).get("name")} for net in self._links(key, "nets")
        ]
        return result

    def _pad_location(self, key: str) -> Object:
        data = self._data(key)
        return {
            "id": key,
            **{
                name: data.get(name)
                for name in (
                    "number",
                    "board_position",
                    "local_position",
                    "layers",
                    "saved_pin_function",
                    "saved_pin_type",
                )
            },
            "position_status": "available"
            if data.get("board_position") is not None
            else "unavailable",
        }

    def _circuit_components(self, net: str, scope: str) -> list[str]:
        self._in_scope(None, scope)
        components: set[str] = set()
        scanned = 0
        for pin in self._links(net, "pins"):
            scanned += 1
            for key in self._links(pin, "component"):
                scanned += 1
                if scanned > 100000:
                    raise ValueError("Circuit query exceeds the endpoint scan limit.")
                if self._in_scope(key, scope):
                    components.add(key)
        return sorted(components, key=lambda key: (str(self._data(key).get("reference", "")), key))

    def _circuit_component_limit(self, inputs: Object) -> int:
        limit = inputs.get("pin_limit", 16)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            raise ValueError("Circuit pin limit must be an integer from 1 through 64.")
        return limit

    def _circuit_component(self, key: str, focus: set[str], inputs: Object) -> Object:
        limit = self._circuit_component_limit(inputs)
        pins = sorted(
            self._links(key, "pins"),
            key=lambda pin: (
                not bool(focus.intersection(self._links(pin, "nets"))),
                str(self._data(pin).get("number", "")),
                pin,
            ),
        )
        rows: list[Json] = []
        for pin in pins[:limit]:
            nets = self._links(pin, "nets")
            pads = self._links(pin, "pads")
            functions = self._links(pin, "symbol_pins")
            rows.append(
                {
                    "id": pin,
                    "number": self._data(pin).get("number"),
                    "on_selected_net": bool(focus.intersection(nets)),
                    "nets": [{"id": net, "name": self._data(net).get("name")} for net in nets],
                    "connection_status": self._data(pin).get("connection_status"),
                    "symbol_association": self._data(pin).get("symbol_association"),
                    "no_connect_count": len(self._links(pin, "no_connects")),
                    "issue_count": len(self._links(pin, "issues")),
                    "pad_count": len(pads),
                    "pads": [self._pad_location(pad) for pad in pads[:8]],
                    "pads_truncated": len(pads) > 8,
                    "functions": [
                        {
                            "id": value,
                            **{
                                name: self._data(value).get(name)
                                for name in (
                                    "base_name",
                                    "effective_name",
                                    "effective_electrical_type",
                                    "selection_status",
                                    "alternate_selection",
                                )
                            },
                        }
                        for value in functions[:8]
                    ],
                    "functions_truncated": len(functions) > 8,
                }
            )
        relations = self._links(key, "relationships")
        data = self._data(key)
        component: Object = {
            "id": key,
            **{
                name: data.get(name)
                for name in (
                    "reference",
                    "value",
                    "footprint",
                    "side",
                    "placement",
                )
            },
        }
        fields = _object(data.get("fields", {}))
        component["fields"] = {
            name: value
            for name, value in fields.items()
            if name.casefold() in ("val", "description", "manufacturer", "mpn", "part number")
            and value not in (None, "")
        }
        return {
            "component": component,
            "scope": "board" if self._links(key, "board") else "schematic_only",
            "pins": rows,
            "pin_count": len(pins),
            "pins_truncated": len(pins) > limit,
            "relationships": [
                {"id": relation, "kind": self._data(relation).get("relationship_kind")}
                for relation in relations[:32]
            ],
            "relationships_truncated": len(relations) > 32,
            "conduction": "not_evaluated",
            "coordinate_units": "mm",
            "coordinate_frame": "board",
            "local_coordinate_frame": "footprint",
        }

    def _area_sum(self, values: list[Json]) -> float:
        numbers = [_number(value, "zone area") for value in values]
        if any(value < 0 for value in numbers):
            raise ValueError("Zone area must not be negative.")
        try:
            return math.fsum(numbers)
        except OverflowError as error:
            raise ValueError("Zone area exceeds the numeric range.") from error

    def _zone_areas(self, inputs: Object) -> Object:
        selected = (
            self._resolve(inputs, "net", "name") if "name" in inputs or "id" in inputs else None
        )
        layer_filter = _text(inputs["layer"], "layer") if "layer" in inputs else None
        groups: dict[tuple[str, str], list[tuple[str, Object, Object]]] = defaultdict(list)
        excluded = 0
        for key in self._kinds.get("zone", []):
            data = self._data(key)
            nets = self._links(key, "nets")
            if selected is not None and selected not in nets:
                continue
            if data.get("rule_area"):
                excluded += 1
                continue
            estimate = data.get("area_estimates")
            summary = _object(estimate) if estimate is not None else {}
            if summary:
                rows: list[Json] = _array(summary.get("layers", []))
            else:
                rows = [
                    {"layer": None if "*" in layer or layer == "F&B.Cu" else layer}
                    for layer in _strings(data.get("layers", []))
                ] or [{"layer": None}]
            for value in rows:
                row = _object(value)
                layer = _text(row["layer"]) if row.get("layer") is not None else ""
                if layer and not (
                    layer in ("F.Cu", "B.Cu")
                    or (layer.startswith("In") and layer.endswith(".Cu") and layer[2:-3].isdigit())
                ):
                    continue
                if layer_filter is not None and layer != layer_filter:
                    continue
                for net in nets or [""]:
                    if selected is None or net == selected:
                        groups[(net, layer)].append((key, summary, row))
        items: list[Json] = []
        for (net, layer), entries in sorted(groups.items()):
            outlines = [summary.get("outline_area_mm2") for _, summary, _ in entries]
            fills = [row.get("saved_fill_area_mm2") for _, _, row in entries]

            def total(values: list[Json]) -> float | None:
                return (
                    self._area_sum(values) if all(value is not None for value in values) else None
                )

            items.append(
                {
                    "net_id": net or None,
                    "name": self._data(net).get("name") if net else None,
                    "layer": layer or None,
                    "zone_count": len(entries),
                    "zone_ids": [key for key, _, _ in entries[:16]],
                    "zone_ids_truncated": len(entries) > 16,
                    "outline_area_mm2": total(outlines),
                    "saved_fill_area_mm2": total(fills),
                    "known_saved_fill_area_mm2": self._area_sum(
                        [row.get("known_saved_fill_area_mm2", 0) for _, _, row in entries]
                    ),
                    "zones_without_fill_estimate": sum(value is None for value in fills),
                    "zones_without_outline_estimate": sum(value is None for value in outlines),
                    "fill_freshness": "unknown",
                }
            )
        result = self._page("zone_areas", inputs, items)
        result["units"] = "mm2"
        result["method"] = "saved-linear-zone-polygons-v1"
        result["limitations"] = [
            "Arcs, degenerate polygons, and missing geometry have no complete estimate.",
            "Older netboms need re-export for area estimates. Missing estimates are not zero.",
            "Unassigned nets and unresolved layer names remain null.",
        ]
        result["excluded_rule_area_count"] = excluded
        result["meaning"] = (
            "Sum of saved polygon areas, not a copper union. "
            "Overlapping zones count more than once. "
            "Outline areas subtract stored cutouts. Saved fills include islands. "
            "No refill, clearance calculation, or extra boundary thickness is applied."
        )
        result["coverage"] = _object(self._document.get("coverage", {})).get("board")
        return result

    def _execute(self, operation: str, inputs: Object) -> Object:
        if operation == "source_manifest" or operation == "source_files":
            return self._source_evidence(operation, inputs)
        if operation == "usage":
            if "operation" in inputs:
                name = _text(inputs["operation"], "operation")
                usage = self._execute("usage", {})
                definitions = _object(usage["operations"])
                if name not in definitions:
                    raise ValueError("Unknown inspection operation. Read usage first.")
                return {
                    "operation": name,
                    "definition": definitions[name],
                    "limits": usage["limits"],
                    **(
                        {
                            "example": {
                                "operation": name,
                                "inputs": {"limit": 25} if name == "source_files" else {},
                            }
                        }
                        if name in ("source_manifest", "source_files")
                        else {}
                    ),
                    "source_file_filters": {
                        "role": ["board", "project", "root_schematic", "schematic_dependency"],
                        "status": ["read", "absent"],
                        "path": "Exact project-relative path with forward slashes.",
                    }
                    if name == "source_files"
                    else {},
                    "source_evidence_help": [
                        "Read source_manifest for extraction versions and coverage.",
                        "Read source_files with limit and cursor for exact byte hashes.",
                        "Absent means an explicitly checked file was missing. "
                        "No row means not inspected.",
                        "Legacy documents have no read manifest. "
                        "Parsed-content signatures are not byte hashes.",
                    ]
                    if name in ("source_manifest", "source_files")
                    else [],
                }
            return {
                "operations": {
                    name: {
                        "description": description,
                        "inputs": list(parameters),
                        "required": cast(
                            Json,
                            {
                                "pin": ["number"],
                                "common_nets": ["references"],
                                "neighbors": ["radius_mm"],
                                "trace": ["from_net", "to_net", "relationship_kinds"],
                                "records": ["ids"],
                                "native_record": ["source"],
                            }.get(name, []),
                        ),
                        "selector": ["name", "id"]
                        if name in ("net", "circuit", "zone_areas")
                        else ["reference", "id"]
                        if name in ("component", "pin", "pads", "neighbors")
                        else [],
                        "available": self._native_geometry is not None
                        if name == "geometry"
                        else name in self._native_operations
                        if name in ("native_sources", "native_record")
                        else True,
                    }
                    for name, (description, parameters) in _COMMANDS.items()
                },
                "limits": {
                    "page_default": 25,
                    "circuit_page_default": 8,
                    "page_maximum": 200,
                    "response_bytes": _MAX_REPLY,
                },
                "workflow": [
                    "Read overview for coverage and unresolved associations.",
                    "Use source_manifest and source_files for extraction-local byte evidence.",
                    "Use search to find record identifiers, then component, pin, net, "
                    "circuit, or pads.",
                    "Use scope=board for placed components when unmatched schematic "
                    "references repeat.",
                    "Follow next_cursor with the same query. Truncated pin and pad "
                    "previews have separate paged queries.",
                    "Keep one batch session open to load and index the design once.",
                ],
                "scope_values": ["all", "board", "schematic_only"],
                "circuit_limits": {"pin_default": 16, "pin_maximum": 64, "pads_per_pin": 8},
                "examples": [
                    {
                        "operation": "circuit",
                        "inputs": {"name": "+BATT", "scope": "board", "limit": 4},
                    },
                    {
                        "operation": "pads",
                        "inputs": {"reference": "J1", "scope": "board", "limit": 10},
                    },
                ],
                "search_scope": "Literal scalar data and stored fields. No regular expressions.",
                "effects": "Read-only. No network, browser execution, or design writes.",
            }
        if operation == "overview":
            return {
                "design_name": self._document.get("design_name"),
                "design_id": self._document.get("design_id"),
                "netbom_revision": self._document.get("revision_id"),
                "counts": {kind: len(keys) for kind, keys in sorted(self._kinds.items())},
                "coverage": self._document.get("coverage", {}),
                "issues": dict(
                    Counter(
                        str(self._data(key).get("code")) for key in self._kinds.get("issue", [])
                    )
                ),
                "native_geometry": self._native_geometry is not None,
                "limitations": [
                    "Saved assignments do not prove copper continuity or synchronization.",
                    "Component distances measure origins, not clearance.",
                    "Repository access does not establish design intent or source authorship.",
                ],
            }
        if operation == "search":
            kind = inputs.get("kind")
            keys = (
                self._kinds.get(_text(kind, "kind"), [])
                if kind is not None
                else sorted(self._records)
            )
            text = _text(inputs.get("text", ""), "text").casefold()
            field = inputs.get("field")
            if ("equals" in inputs or "missing" in inputs) and field is None:
                raise ValueError("A field filter requires a field name.")
            if "missing" in inputs and not isinstance(inputs["missing"], bool):
                raise ValueError("missing must be a boolean.")
            if field is not None:
                field = _text(field, "field")
            matches: list[Json] = []
            for key in keys:
                if text not in self._search[key]:
                    continue
                data = self._data(key)
                fields = _object(data.get("fields", {}))
                if field is not None:
                    exists = (
                        field in fields
                        or field in data
                        or field in _strings(data.get("empty_fields", []))
                    )
                    value = fields.get(field, data.get(field, ""))
                    if "missing" in inputs and bool(inputs["missing"]) == exists:
                        continue
                    if "equals" in inputs and (not exists or not _equal(value, inputs["equals"])):
                        continue
                matches.append(key)
            return self._page(operation, inputs, matches, lambda key: self._compact(_text(key)))
        if operation == "zone_areas":
            return self._zone_areas(inputs)
        if operation == "pads":
            key = self._resolve(inputs, "component", "reference")
            pads = self._links(key, "pads")
            if "number" in inputs:
                number = _text(inputs["number"], "number")
                pads = [pad for pad in pads if self._data(pad).get("number") == number]
            result = self._page(
                operation, inputs, cast(list[Json], pads), lambda value: self._pad(_text(value))
            )
            result["component"] = self._compact(key)
            return result
        if operation == "circuit":
            key = self._resolve(inputs, "net", "name")
            scope = _text(inputs.get("scope", "all"), "scope")
            keys = self._circuit_components(key, scope)
            # Validate nested limits even when the selected net has no components.
            self._circuit_component_limit(inputs)
            result = self._page(
                operation,
                inputs,
                cast(list[Json], keys),
                lambda value: self._circuit_component(_text(value), {key}, inputs),
            )
            result["net"] = {"id": key, "name": self._data(key).get("name")}
            result["meaning"] = (
                "Component adjacency only. No current flow or internal connection is inferred."
            )
            return result
        if operation in ("component", "net", "pin"):
            kind = "net" if operation == "net" else "component"
            key = self._resolve(inputs, kind, "name" if kind == "net" else "reference")
            pins = self._links(key, "pins")
            if operation == "pin":
                number = _text(inputs.get("number"), "number")
                pins = [pin for pin in pins if self._data(pin).get("number") == number]
            page = self._page(
                operation, inputs, cast(list[Json], pins), lambda key: self._pin(_text(key))
            )
            page[kind] = self._compact(key)
            if kind == "component":
                page["symbols"] = [self._compact(value) for value in self._links(key, "symbols")]
            return page
        if operation == "issues":
            code = inputs.get("code")
            if code is not None:
                code = _text(code, "code")
            return self._page(
                operation,
                inputs,
                [
                    self._compact(key)
                    for key in self._kinds.get("issue", [])
                    if code is None or self._data(key).get("code") == code
                ],
            )
        if operation == "records":
            keys = _strings(inputs.get("ids"), "ids")
            if len(keys) > 32 or any(key not in self._records for key in keys):
                raise ValueError("Supply at most 32 known record identifiers.")
            return self._page(operation, inputs, [self._records[key] for key in keys])
        if operation == "common_nets":
            references = _strings(inputs.get("references"), "references")
            if not 2 <= len(references) <= 32:
                raise ValueError("Supply from 2 through 32 component references.")
            sets = []
            for reference in references:
                key = self._resolve({"reference": reference}, "component", "reference")
                sets.append(
                    {net for pin in self._links(key, "pins") for net in self._links(pin, "nets")}
                )
            return self._page(
                operation, inputs, [self._compact(key) for key in sorted(set.intersection(*sets))]
            )
        if operation == "neighbors":
            key = self._resolve(inputs, "component", "reference")
            radius = _number(inputs.get("radius_mm"), "radius_mm")
            if radius < 0 or radius > 1000000:
                raise ValueError("Radius must be nonnegative and at most 1000000 mm.")
            side = inputs.get("side")
            if side not in (None, "top", "bottom"):
                raise ValueError("Side must be top or bottom.")
            origin = self._position(key)
            if origin is None:
                raise ValueError("The component has no board placement.")
            items: list[Json] = []
            for other in self._kinds.get("component", []):
                point = self._position(other)
                if (
                    other == key
                    or point is None
                    or side is not None
                    and self._data(other).get("side") != side
                ):
                    continue
                distance = math.hypot(point[0] - origin[0], point[1] - origin[1])
                if distance <= radius:
                    items.append({**self._compact(other), "distance_mm": distance})
            items.sort(
                key=lambda value: (
                    _number(_object(value)["distance_mm"]),
                    _text(_object(value)["id"]),
                )
            )
            page = self._page(operation, inputs, items)
            page["measurement"] = (
                "Euclidean distance between stored footprint origins. Not clearance."
            )
            return page
        if operation == "trace":
            return self._trace(inputs)
        if operation == "sources":
            return self._sources(inputs)
        if operation in self._native_operations:
            return self._native_operations[operation](inputs)
        if operation == "geometry" and self._native_geometry is not None:
            return self._native_geometry(inputs)
        raise ValueError("Native geometry is unavailable. Supply a native board file.")

    def _position(self, key: str) -> tuple[float, float] | None:
        placement = self._data(key).get("placement")
        if placement is None:
            return None
        position = _array(_object(placement).get("position"), "position")
        if len(position) != 2:
            raise ValueError("Board placement requires two coordinates.")
        return _number(position[0]), _number(position[1])

    def _trace(self, inputs: Object) -> Object:
        endpoints = []
        for name in ("from_net", "to_net"):
            value = _text(inputs.get(name), name)
            query = {"id": value} if value in self._records else {"name": value}
            endpoints.append(self._resolve(cast(Object, query), "net", "name"))
        allowed = set(_strings(inputs.get("relationship_kinds"), "relationship_kinds"))
        limit = inputs.get("max_visited", 10000)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ValueError("Invalid trace limit.")
        start, end = endpoints
        queue = deque([start])
        previous: dict[str, tuple[str, str] | None] = {start: None}
        scanned = 0
        while queue:
            current = queue.popleft()
            if current == end:
                keys = [current]
                while previous[current] is not None:
                    step = previous[current]
                    assert step is not None
                    current, relationship = step
                    keys.extend([relationship, current])
                return {
                    "found": True,
                    "path": [self._records[key] for key in reversed(keys)],
                    "visited_nets": len(previous),
                    "meaning": "Structural path only. Current flow is not evaluated.",
                }
            for relation in self._links(current, "relationships"):
                scanned += 1
                if scanned > limit * 32:
                    raise ValueError("Trace exceeds the link scan limit.")
                kind = self._data(relation).get("relationship_kind")
                if not isinstance(kind, str):
                    raise ValueError("Invalid relationship kind.")
                if kind not in allowed:
                    continue
                for target in self._links(relation, "nets"):
                    scanned += 1
                    if scanned > limit * 32:
                        raise ValueError("Trace exceeds the link scan limit.")
                    if self._records[target].get("kind") != "net":
                        raise ValueError("Relationship endpoint is not a net.")
                    if target not in previous:
                        if len(previous) >= limit:
                            raise ValueError("Trace exceeds the visited-net limit.")
                        previous[target] = current, relation
                        queue.append(target)
        return {
            "found": False,
            "path": [],
            "visited_nets": len(previous),
            "meaning": "No path under the selected relationship rules.",
        }

    def _source_evidence(self, operation: str, inputs: Object) -> Object:
        for key, allowed in (
            ("role", {"board", "project", "root_schematic", "schematic_dependency"}),
            ("status", {"read", "absent"}),
        ):
            if key in inputs and _text(inputs[key], key) not in allowed:
                raise ValueError("Unknown source file filter.")
        if "path" in inputs:
            _text(inputs["path"], "path")
        keys = self._kinds.get("source_manifest", [])
        if len(keys) > 1:
            raise ValueError("Netbom contains multiple source manifests.")
        if not keys:
            return {
                "status": "not_recorded",
                **(self._page(operation, inputs, []) if operation == "source_files" else {}),
                "limitations": [
                    "This document has no extraction byte evidence. File absence is unknown."
                ],
            }
        manifest = self._data(keys[0])
        if manifest.get("schema_version") != "source-evidence-1":
            raise ValueError("Unsupported source-evidence manifest version.")
        files = _array(manifest.get("files", []), "source files")
        if len(files) > 4099:
            raise ValueError("Source evidence exceeds the file limit.")
        if operation == "source_manifest":
            return {
                **{key: value for key, value in manifest.items() if key != "files"},
                "status": "recorded" if manifest.get("mode") == "files" else "not_inspected",
                "file_count": len(files),
                "read_file_count": sum(_object(row).get("status") == "read" for row in files),
                "absent_file_count": sum(_object(row).get("status") == "absent" for row in files),
            }
        selected = [
            row
            for row in files
            if all(
                _object(row).get(key) == inputs[key]
                for key in ("path", "role", "status")
                if key in inputs
            )
        ]
        return {
            **self._page(operation, inputs, selected),
            "status": "not_inspected"
            if manifest.get("mode") != "files"
            or "path" in inputs
            and not any(_object(row).get("path") == inputs["path"] for row in files)
            else "recorded",
            "schema_version": "source-evidence-1",
            "meaning": "Only read rows have byte hashes. "
            "An unlisted path was not inspected by this manifest.",
        }

    def _sources(self, inputs: Object) -> Object:
        keys = _strings(inputs["ids"], "ids") if "ids" in inputs else self._kinds.get("source", [])
        if "ids" in inputs and len(keys) > 32:
            raise ValueError("Supply at most 32 record identifiers.")
        selected: dict[str, Object] = {}
        for key in keys:
            if key not in self._records:
                raise ValueError("Unknown provenance record identifier.")
            record = self._records[key]
            if record.get("kind") == "source":
                selected[key] = {"source": record, "uses": []}
            for evidence in _array(record.get("evidence", [])):
                entry = _object(evidence)
                source = _text(entry.get("source_id"))
                row = selected.setdefault(source, {"source": self._records[source], "uses": []})
                _array(row["uses"]).append({"record_id": key, "evidence": entry})
        repository = inputs.get("repository_url")
        if repository is not None:
            repository = _text(repository, "repository_url").rstrip("/")
            url = urlsplit(repository)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Supply an HTTPS repository URL without credentials, query, or fragment."
                )
            revision = _text(inputs.get("revision"), "revision")
            if not revision:
                raise ValueError("Supply an explicit repository revision.")
            paths = _object(inputs.get("paths"), "paths")
            for source, row in selected.items():
                if source in paths:
                    path = _text(paths[source], "source path").replace("\\", "/")
                    if (
                        path.startswith("/")
                        or ":" in path
                        or any(part in (".", "..", "") for part in path.split("/"))
                    ):
                        raise ValueError("Repository source paths must be relative and confined.")
                    row["repository_url"] = (
                        repository
                        + "/-/blob/"
                        + quote(revision, safe="")
                        + "/"
                        + quote(path, safe="/")
                    )
                    row["link_basis"] = "Caller-supplied source mapping. Not fetched or verified."
        return self._page("sources", inputs, [selected[key] for key in sorted(selected)])


class _Inspector(Protocol):
    def execute(self, operation: str, inputs: Object | None = None) -> Object: ...


def _cli(opener: Callable[..., _Inspector] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Read-only netbom and design inspection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Installed: python -m eedesigncore.inspection design.netbom.json usage\n"
            "Standalone helper: python inspect_design.py design.netbom.json usage\n"
            "Then request overview for coverage. Use --batch to load the design once.\n"
            "Extract embedded JSON with the document publisher's documented route.\n"
            "Without these tools, read coverage, conventions, indexes, and linked records.\n"
            "Do not print an entire compact JSON file to preview it.\n\n"
            "Design operations:\n"
            + "\n".join(f"  {name}: {description}" for name, (description, _) in _COMMANDS.items())
            + "\n\nAssembly files provide system-net, mate, circuit, and board queries. "
            "Read usage on the selected file for its accepted inputs."
        ),
    )
    parser.add_argument(
        "source",
        help="Netbom or assembly JSON, or netbom JSONL. Installed mode also accepts KiCad files.",
    )
    parser.add_argument("operation", nargs="?", default="overview")
    parser.add_argument(
        "--inputs", default="{}", help="JSON object of discovered operation inputs"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Read JSON request lines from standard input after loading once",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent single-query JSON output")
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=65536,
        help="Maximum bytes per reply, from 1024 through 2097152",
    )
    args = parser.parse_args()
    if not 1024 <= args.max_output_bytes <= _MAX_REPLY:
        parser.error("Output limit must be from 1024 through 2097152 bytes.")
    if args.pretty and args.batch:
        parser.error("Pretty output cannot be used with line-based batch mode.")

    def emit(value: Object) -> None:
        output = (
            json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
            if args.pretty
            else _dump(value)
        )
        if len(output) + output.count("\n") + 2 > args.max_output_bytes:
            raise ValueError(
                "Reply exceeds the output budget. Reduce limit or pin_limit, or narrow the query."
            )
        print(output, flush=True)

    try:
        inspector = (opener or DesignInspector.open)(args.source)
        if args.batch:
            for index in range(10001):
                line = sys.stdin.readline(65537)
                if not line:
                    break
                if index >= 10000:
                    raise ValueError("Batch exceeds the request limit.")
                if len(line) > 65536:
                    raise ValueError("Request exceeds the line length limit.")
                try:
                    request = _object(_json(line), "request")
                    if set(request) - {"operation", "inputs"}:
                        raise ValueError("Unknown request fields.")
                    result = inspector.execute(
                        _text(request.get("operation")), _object(request.get("inputs", {}))
                    )
                    emit({"ok": True, "result": result})
                except ValueError as error:
                    emit({"ok": False, "error": str(error)[:128]})
        else:
            emit(inspector.execute(args.operation, _object(_json(args.inputs), "inputs")))
    except BrokenPipeError:
        return
    except (ValueError, OSError) as error:
        parser.exit(2, _dump({"ok": False, "error": str(error)[:128]}) + "\n")


if __name__ == "__main__":
    _cli()
