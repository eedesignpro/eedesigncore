"""Connector-defined system inspection using only the Python standard library."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import cast

from eedesigncore.inspection.mates import _compare_profiles, _fit_positions, _profile
from eedesigncore.inspection.portable import (
    _COMMANDS,
    _MAX_REPLY,
    DesignInspector,
    Json,
    Object,
    _array,
    _hash,
    _json,
    _number,
    _object,
    _read,
    _strings,
    _text,
    _validate,
)

_ASSEMBLY_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "placement_check": (
        "Fit all resolved mate contact positions between two boards without moving them.",
        ("left_instance", "right_instance", "tolerance_mm"),
    ),
    "mate_candidates": (
        "Rank connector hypotheses using same-number electrical patterns, without adding mates.",
        ("instance", "id", "reference", "other_instance", "limit", "cursor"),
    ),
    "compare_connectors": (
        "Compare one connector pair under a named pin-mapping hypothesis.",
        ("left_instance", "left_id", "right_instance", "right_id", "mapping", "limit", "cursor"),
    ),
    "overview": ("Read assembly counts, coverage, and limits.", ()),
    "usage": ("Describe system and board queries.", ()),
    "instances": ("List board instances and their source snapshots.", ("limit", "cursor")),
    "search": (
        "Search records across board instances.",
        ("instance", "kind", "text", "limit", "cursor"),
    ),
    "mates": (
        "Read connector mates and their resolution status.",
        ("instance", "status", "limit", "cursor"),
    ),
    "mate": ("Read derived pin connections for one connector mate.", ("id", "limit", "cursor")),
    "connector": (
        "Read a connector's pins, mate, and system nets.",
        ("instance", "reference", "id", "scope", "limit", "cursor"),
    ),
    "circuit": (
        "Read connected components and their other pins across declared connector mates.",
        ("instance", "name", "net_id", "scope", "pin_limit", "limit", "cursor"),
    ),
    "system_nets": (
        "List resolved system nets without expanding endpoints.",
        ("instance", "text", "limit", "cursor"),
    ),
    "system_net": (
        "Read system-net member nets or endpoint pins.",
        ("id", "instance", "name", "net_id", "view", "limit", "cursor"),
    ),
    "issues": (
        "Read assembly diagnostics. Board issues remain board-scoped.",
        ("code", "limit", "cursor"),
    ),
    "trace": (
        "Trace declared mates and explicitly selected board relationships.",
        (
            "from_instance",
            "from_net",
            "to_instance",
            "to_net",
            "relationship_kinds",
            "max_visited",
        ),
    ),
    "board_query": (
        "Run an existing query in one board's coordinate and identity scope.",
        ("instance", "operation", "inputs"),
    ),
}


def _fields(value: Object, allowed: set[str], required: set[str]) -> None:
    if set(value) - allowed or required - set(value):
        raise ValueError("Unknown or missing assembly fields.")


def _identifier(value: Json) -> str:
    result = _text(value, "identifier")
    if not result or len(result) > 256:
        raise ValueError("Assembly identifiers require 1 through 256 characters.")
    return result


def _endpoint(value: Json) -> tuple[str, str]:
    row = _object(value, "connector endpoint")
    _fields(row, {"instance", "component_id"}, {"instance", "component_id"})
    return _identifier(row["instance"]), _text(row["component_id"], "component_id")


def _evidence(value: Json) -> None:
    row = _object(value, "mapping evidence")
    _fields(row, {"source", "locator", "revision", "derivation", "method"}, {"source", "locator"})
    if not _text(row["source"], "source"):
        raise ValueError("Mapping evidence requires a source description.")
    _text(row["locator"], "locator")
    if row.get("derivation", "specified") not in ("specified", "inferred"):
        raise ValueError("Mapping evidence derivation must be specified or inferred.")
    if row.get("method") is not None:
        _text(row["method"], "method")
    if row.get("derivation") == "inferred" and not row.get("method"):
        raise ValueError("Inferred mapping evidence requires a method description.")
    if row.get("revision") is not None:
        _text(row["revision"], "revision")


class AssemblyInspector:
    """A read-only system snapshot with board-local data stored once per design."""

    def __init__(self, document: Object) -> None:
        _validate(document)
        _fields(
            document,
            {
                "schema_version",
                "assembly_id",
                "name",
                "designs",
                "instances",
                "mate_definitions",
                "mates",
                "expected_connectors",
            },
            {"schema_version", "assembly_id", "designs", "instances", "mate_definitions", "mates"},
        )
        if document["schema_version"] != "netbom-assembly-1":
            raise ValueError("Unsupported assembly schema.")
        self._assembly_id = _identifier(document["assembly_id"])
        self._name = _text(document.get("name", "assembly"))
        designs = _object(document["designs"], "designs")
        instances = _object(document["instances"], "instances")
        definitions = _object(document["mate_definitions"], "mate_definitions")
        mates = _object(document["mates"], "mates")
        if (
            len(designs) > 256
            or len(instances) > 1024
            or len(definitions) > 10000
            or len(mates) > 10000
        ):
            raise ValueError("Assembly exceeds the design, instance, definition, or mate limit.")
        self._designs: dict[str, DesignInspector] = {}
        for key, value in sorted(designs.items()):
            data = _object(value, "netbom")
            if key != "design:" + _hash(data):
                raise ValueError("Embedded netbom content does not match its design identifier.")
            self._designs[key] = DesignInspector(data)
        self._instances: dict[str, str] = {}
        for key, value in sorted(instances.items()):
            _identifier(key)
            design = _text(value, "instance design")
            if design not in self._designs:
                raise ValueError("Board instance refers to a missing design.")
            self._instances[key] = design
        if sum(len(self._designs[key]._records) for key in self._instances.values()) > 1000000:
            raise ValueError("Assembly exceeds the expanded record limit.")
        design_link_counts = {
            key: sum(
                len(_array(targets))
                for record in board._records.values()
                for targets in _object(record.get("links", {})).values()
            )
            for key, board in self._designs.items()
        }
        if sum(design_link_counts[key] for key in self._instances.values()) > 4000000:
            raise ValueError("Assembly exceeds the expanded link limit.")
        for key, value in definitions.items():
            _identifier(key)
            row = _object(value, "mate definition")
            _fields(row, {"pin_pairs", "evidence"}, {"pin_pairs", "evidence"})
            _evidence(row["evidence"])
            pairs = _array(row["pin_pairs"], "pin pairs")
            if not 1 <= len(pairs) <= 4096:
                raise ValueError("A mate definition requires 1 through 4096 pin pairs.")
            left_seen: set[str] = set()
            right_seen: set[str] = set()
            for value_pair in pairs:
                pair = _object(value_pair, "pin pair")
                _fields(pair, {"left", "right"}, {"left", "right"})
                left, right = _text(pair["left"]), _text(pair["right"])
                if not left or not right or left in left_seen or right in right_seen:
                    raise ValueError("Mate pin numbers must be nonempty and unique on each side.")
                left_seen.add(left)
                right_seen.add(right)
        for key, value in mates.items():
            _identifier(key)
            row = _object(value, "mate")
            _fields(
                row,
                {"definition", "left", "right", "evidence"},
                {"definition", "left", "right", "evidence"},
            )
            _text(row["definition"], "definition")
            _endpoint(row["left"])
            _endpoint(row["right"])
            _evidence(row["evidence"])
        pair_count = sum(
            len(_array(_object(definitions[definition])["pin_pairs"]))
            for value in mates.values()
            if (definition := _text(_object(value)["definition"])) in definitions
        )
        if pair_count > 250000:
            raise ValueError("Assembly exceeds the expanded connector-pair limit.")
        expected = _array(document.get("expected_connectors", []), "expected connectors")
        if len(expected) > 20000:
            raise ValueError("Too many expected connectors.")
        expected_keys = [_endpoint(value) for value in expected]
        if len(set(expected_keys)) != len(expected_keys):
            raise ValueError("Expected connector list contains duplicates.")
        # Reuse the standard pager without copying embedded board data again.
        self._pager = DesignInspector({"schema_version": "netbom-1", "records": {}})
        self._revision = _hash(document)
        self._pager._revision = self._revision
        import copy

        self._definitions = copy.deepcopy(definitions)
        self._mates = copy.deepcopy(mates)
        self._expected = copy.deepcopy(expected)
        self._issues: list[Json] = []
        self._mate_rows: dict[str, Object] = {}
        self._derived_pairs: dict[str, list[Json]] = {}
        self._connector_mates: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._comparison_names: dict[tuple[int, str], str] = {}
        self._conditional_nets: set[str] = set()
        self._pin_cache: dict[tuple[str, str], dict[str, list[str]]] = {}
        self._pin_mates: dict[tuple[str, str], list[Json]] = defaultdict(list)
        self._groups: dict[str, list[tuple[str, str]]] = {}
        self._system_ids: dict[tuple[str, str], str] = {}
        self._net_pins: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._edges: dict[str, tuple[list[tuple[str, str]], Object, str | None]] = {}
        self._adjacency: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._resolve()

    @classmethod
    def open(cls, path: str | Path) -> AssemblyInspector:
        return cls(_object(_json(_read(path)), "assembly"))

    def _board(self, instance: str) -> DesignInspector:
        if instance not in self._instances:
            raise ValueError("Unknown board instance.")
        return self._designs[self._instances[instance]]

    def _issue(self, code: str, message: str, **subjects: Json) -> None:
        if len(self._issues) >= 100000:
            raise ValueError("Assembly exceeds the issue limit.")
        self._issues.append({"code": code, "message": message, **subjects})

    def _pins(self, endpoint: tuple[str, str]) -> dict[str, list[str]]:
        instance, component = endpoint
        board = self._board(instance)
        cache_key = self._instances[instance], component
        if cache_key in self._pin_cache:
            return self._pin_cache[cache_key]
        if component not in board._records or board._records[component]["kind"] != "component":
            raise ValueError("Connector component is missing.")
        pins: dict[str, list[str]] = defaultdict(list)
        for pin in board._links(component, "pins"):
            if board._records[pin]["kind"] != "pin" or board._links(pin, "component") != [
                component
            ]:
                raise ValueError("Connector pin ownership is inconsistent.")
            pins[_text(board._data(pin).get("number"), "pin number")].append(pin)
        self._pin_cache[cache_key] = pins
        return pins

    def _pin_net(self, instance: str, pin: str) -> tuple[str, str]:
        board = self._board(instance)
        nets = board._links(pin, "nets")
        if len(nets) != 1 or board._records[nets[0]]["kind"] != "net":
            raise ValueError("Mapped pin requires exactly one saved net assignment.")
        if any(
            board._records[value]["kind"] != "symbol_pin"
            for value in board._links(pin, "symbol_pins")
        ):
            raise ValueError("Mapped pin has an invalid symbol-pin association.")
        if (
            board._data(pin).get("connection_status") == "declared_no_connect"
            or board._links(pin, "no_connects")
            or any(
                board._links(value, "no_connects")
                or board._data(value).get("no_connect_status")
                in ("explicit_marker", "explicit_no_connect")
                for value in board._links(pin, "symbol_pins")
            )
        ):
            raise ValueError("Mapped pin has an explicit no-connect declaration.")
        return instance, nets[0]

    def _add_edge(
        self, key: str, nodes: list[tuple[str, str]], evidence: Object, kind: str | None
    ) -> None:
        unique = sorted(set(nodes))
        self._edges[key] = (unique, evidence, kind)
        for node in unique:
            self._adjacency[node].append(key)

    def _resolve(self) -> None:
        parent: dict[tuple[str, str], tuple[str, str]] = {}
        for instance in self._instances:
            board = self._board(instance)
            for net in board._kinds.get("net", []):
                parent[(instance, net)] = (instance, net)
            for pin in board._kinds.get("pin", []):
                for net in board._links(pin, "nets"):
                    if (instance, net) not in parent:
                        raise ValueError("Pin net link does not refer to a net.")
                    self._net_pins[(instance, net)].append(pin)
            for relation in board._kinds.get("relationship", []):
                nets = [(instance, net) for net in board._links(relation, "nets")]
                if any(node not in parent for node in nets):
                    raise ValueError("Relationship link does not refer to a net.")
                kind = _text(board._data(relation).get("relationship_kind"), "relationship kind")
                self._add_edge(
                    "board:" + _hash([instance, relation]),
                    nets,
                    {
                        "kind": "board_relationship",
                        "instance": instance,
                        "record_id": relation,
                        "relationship_kind": kind,
                    },
                    kind,
                )

        def find(node: tuple[str, str]) -> tuple[str, str]:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for key, value in sorted(self._mates.items()):
            mate = _object(value)
            for side in ("left", "right"):
                self._connector_mates[_endpoint(mate[side])].append(key)
        for key, value in sorted(self._mates.items()):
            mate = _object(value)
            left, right = _endpoint(mate["left"]), _endpoint(mate["right"])
            row: Object = {"id": key, **mate, "status": "unresolved", "pair_count": 0}
            self._mate_rows[key] = row
            self._derived_pairs[key] = []
            if left == right or any(len(self._connector_mates[end]) > 1 for end in (left, right)):
                self._issue(
                    "connector_multiple_mates",
                    "A connector cannot participate in multiple mates.",
                    mate_id=key,
                )
                continue
            definition_id = _text(mate["definition"])
            if definition_id not in self._definitions:
                self._issue(
                    "missing_mate_definition", "Connector mate definition is missing.", mate_id=key
                )
                continue
            definition = _object(self._definitions[definition_id])
            try:
                left_pins, right_pins = self._pins(left), self._pins(right)
                derived: list[Json] = []
                used_left: set[str] = set()
                used_right: set[str] = set()
                for value_pair in _array(definition["pin_pairs"]):
                    pair = _object(value_pair)
                    a, b = _text(pair["left"]), _text(pair["right"])
                    if len(left_pins.get(a, [])) != 1 or len(right_pins.get(b, [])) != 1:
                        raise ValueError("Mapped connector pin is missing or ambiguous.")
                    left_pin, right_pin = left_pins[a][0], right_pins[b][0]
                    left_net, right_net = (
                        self._pin_net(left[0], left_pin),
                        self._pin_net(right[0], right_pin),
                    )
                    used_left.add(a)
                    used_right.add(b)
                    derived.append(
                        {
                            "left": {
                                "instance": left[0],
                                "pin_id": left_pin,
                                "number": a,
                                "net_id": left_net[1],
                            },
                            "right": {
                                "instance": right[0],
                                "pin_id": right_pin,
                                "number": b,
                                "net_id": right_net[1],
                            },
                        }
                    )
            except ValueError as error:
                self._issue("unresolved_mate", str(error), mate_id=key)
                continue
            for endpoint, pins, used in (
                (left, left_pins, used_left),
                (right, right_pins, used_right),
            ):
                for number in sorted(set(pins) - used):
                    self._issue(
                        "unmapped_connector_pin",
                        "Mate definition does not map this connector pin.",
                        mate_id=key,
                        instance=endpoint[0],
                        component_id=endpoint[1],
                        number=number,
                    )
            if any(
                _object(item).get("derivation") == "inferred"
                for item in (mate["evidence"], definition["evidence"])
            ):
                self._issue(
                    "inferred_mate",
                    "This mate uses an inferred pairing or pin definition.",
                    mate_id=key,
                )
            row["status"] = "resolved"
            row["pair_count"] = len(derived)
            self._derived_pairs[key] = derived
            for value_pair in derived:
                pair = _object(value_pair)
                left_end, right_end = _object(pair["left"]), _object(pair["right"])
                nodes = [
                    (_text(left_end["instance"]), _text(left_end["net_id"])),
                    (_text(right_end["instance"]), _text(right_end["net_id"])),
                ]
                for mapped_end, remote in ((left_end, right_end), (right_end, left_end)):
                    self._pin_mates[
                        (_text(mapped_end["instance"]), _text(mapped_end["pin_id"]))
                    ].append({"mate_id": key, "definition_id": definition_id, "remote": remote})
                roots = [find(node) for node in nodes]
                parent[roots[1]] = roots[0]
                edge_key = "mate:" + _hash([key, left_end["number"], right_end["number"]])
                self._add_edge(
                    edge_key,
                    nodes,
                    {
                        "kind": "mate_connection",
                        "mate_id": key,
                        "definition_id": definition_id,
                        "pins": pair,
                        "mate_evidence": mate["evidence"],
                        "definition_evidence": definition["evidence"],
                    },
                    None,
                )
        for value in self._expected:
            endpoint = _endpoint(value)
            try:
                self._pins(endpoint)
            except ValueError as error:
                self._issue("missing_expected_connector", str(error), connector=value)
            else:
                if endpoint not in self._connector_mates:
                    self._issue(
                        "unmated_connector",
                        "An expected connector has no declared mate.",
                        connector=value,
                    )
        groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for node in sorted(parent):
            groups[find(node)].append(node)
        for members in groups.values():
            key = "system_net:" + _hash(
                [self._assembly_id, cast(Json, [list(node) for node in members])]
            )
            self._groups[key] = members
            for node in members:
                self._system_ids[node] = key

        for mate_id, row in self._mate_rows.items():
            if row["status"] != "resolved":
                continue
            definition = _object(self._definitions[_text(row["definition"])])
            if any(
                _object(item).get("derivation") == "inferred"
                for item in (row["evidence"], definition["evidence"])
            ):
                for value in self._derived_pairs[mate_id]:
                    mapped_endpoint = _object(_object(value)["left"])
                    self._conditional_nets.add(
                        self._system_ids[
                            (_text(mapped_endpoint["instance"]), _text(mapped_endpoint["net_id"]))
                        ]
                    )

    def _member(self, node: tuple[str, str]) -> Object:
        instance, net = node
        return {
            "instance": instance,
            "net_id": net,
            "name": self._board(instance)._data(net).get("name"),
            "system_net_id": self._system_ids[node],
        }

    def _system_summary(self, key: str) -> Object:
        members = self._groups[key]
        return {
            "id": key,
            "member_net_count": len(members),
            "depends_on_inferred_mates": key in self._conditional_nets,
            "endpoint_count": sum(len(self._net_pins[node]) for node in members),
            "instance_count": len({node[0] for node in members}),
        }

    def _page(self, operation: str, inputs: Object, items: list[Json]) -> Object:
        return self._pager._page(operation, inputs, items)

    def execute(self, operation: str, inputs: Object | None = None) -> Object:
        values = {} if inputs is None else _object(inputs, "inputs")
        _validate(values)
        if not isinstance(operation, str) or operation not in _ASSEMBLY_COMMANDS:
            raise ValueError("Unknown assembly operation. Read usage first.")
        if set(values) - set(_ASSEMBLY_COMMANDS[operation][1]):
            raise ValueError("Unknown assembly query inputs.")
        result: Object = {
            "schema_version": "assembly-inspection-1",
            "revision_id": self._revision,
            "operation": operation,
            "data": self._execute(operation, values),
        }
        _validate(result, max_bytes=_MAX_REPLY)
        import copy

        return copy.deepcopy(result)

    def _local_net(self, instance: Json, name: Json) -> tuple[str, str]:
        board_name = _text(instance, "instance")
        board = self._board(board_name)
        value = _text(name, "net")
        key = (
            value
            if value in board._records and board._records[value]["kind"] == "net"
            else board._resolve({"name": value}, "net", "name")
        )
        return board_name, key

    def _execute(self, operation: str, inputs: Object) -> Object:
        if operation == "usage":
            return {
                "operations": {
                    key: {"description": description, "inputs": list(fields)}
                    for key, (description, fields) in _ASSEMBLY_COMMANDS.items()
                },
                "workflow": [
                    "Read overview and instances for scope and coverage.",
                    "Use circuit with an instance and local net name to inspect a power stage.",
                    "Use system_net to read member nets or endpoint pins and mate for "
                    "connection evidence.",
                    "Use board_query with usage for local component, pin, pad, and net help.",
                    "Coordinates stay in each board frame. Internal component "
                    "conduction is not evaluated.",
                ],
                "examples": [
                    {
                        "operation": "circuit",
                        "inputs": {
                            "instance": "baseboard",
                            "name": "+BATT",
                            "scope": "board",
                            "limit": 4,
                        },
                    },
                    {
                        "operation": "board_query",
                        "inputs": {"instance": "baseboard", "operation": "usage"},
                    },
                ],
                "board_operations": list(_COMMANDS),
                "limits": {
                    "page_maximum": 200,
                    "circuit_page_default": 8,
                    "response_bytes": _MAX_REPLY,
                },
                "selector": (
                    "Use an instance and a board-local record identifier. "
                    "Connector queries also accept an exact reference."
                ),
            }
        if operation == "overview":
            return {
                "assembly_id": self._assembly_id,
                "name": self._name,
                "design_count": len(self._designs),
                "instance_count": len(self._instances),
                "mate_count": len(self._mates),
                "resolved_mate_count": sum(
                    row["status"] == "resolved" for row in self._mate_rows.values()
                ),
                "system_net_count": len(self._groups),
                "issues": dict(Counter(_text(_object(row)["code"]) for row in self._issues)),
                "expected_connector_count": len(self._expected),
                "completeness": (
                    "Only declared mates and expected connectors are evaluated. "
                    "Unlisted cables are unknown."
                ),
                "limitations": [
                    "Connector compatibility and cable construction are caller-supplied facts.",
                    "Resolved mates describe declared wiring, not verified physical continuity.",
                    "Board coordinates, netclasses, and rules remain local to each board.",
                    "Board issues and coverage remain available through board_query.",
                ],
            }
        if operation == "instances":
            return self._page(
                operation,
                inputs,
                [
                    {
                        "instance": key,
                        "design_id": design,
                        "design_name": self._board(key)._document.get("design_name"),
                        "source_revision": self._board(key)._document.get("revision_id"),
                        "coverage": self._board(key)._document.get("coverage", {}),
                    }
                    for key, design in self._instances.items()
                ],
            )
        if operation == "board_query":
            instance = _text(inputs.get("instance"), "instance")
            return {
                "instance": instance,
                "result": self._board(instance).execute(
                    _text(inputs.get("operation")), _object(inputs.get("inputs", {}))
                ),
            }
        if operation == "issues":
            code = inputs.get("code")
            if code is not None:
                _text(code, "code")
            return self._page(
                operation,
                inputs,
                [row for row in self._issues if code is None or _object(row)["code"] == code],
            )
        if operation == "mates":
            instance_filter = inputs.get("instance")
            if instance_filter is not None:
                self._board(_text(instance_filter))
            status = inputs.get("status")
            if status not in (None, "resolved", "unresolved"):
                raise ValueError("Mate status must be resolved or unresolved.")
            return self._page(
                operation,
                inputs,
                [
                    row
                    for row in self._mate_rows.values()
                    if (status is None or row["status"] == status)
                    and (
                        instance_filter is None
                        or any(
                            _object(row[side])["instance"] == instance_filter
                            for side in ("left", "right")
                        )
                    )
                ],
            )
        if operation == "mate":
            key = _text(inputs.get("id"), "id")
            if key not in self._mate_rows:
                raise ValueError("Unknown connector mate.")
            result = self._page(operation, inputs, self._derived_pairs[key])
            result["mate"] = self._mate_rows[key]
            definition_id = _text(self._mate_rows[key]["definition"])
            definition = self._definitions.get(definition_id)
            result["definition"] = (
                None
                if definition is None
                else {
                    "id": definition_id,
                    "evidence": _object(definition)["evidence"],
                    "pair_count": len(_array(_object(definition)["pin_pairs"])),
                }
            )
            return result
        if operation == "connector":
            instance = _text(inputs.get("instance"), "instance")
            board = self._board(instance)
            component = board._resolve(
                {
                    key: value
                    for key, value in inputs.items()
                    if key in ("id", "reference", "scope")
                },
                "component",
                "reference",
            )
            pins = self._pins((instance, component))
            ids = [pin for number in sorted(pins) for pin in pins[number]]
            result = self._pager._page(
                operation,
                inputs,
                cast(list[Json], ids),
                lambda value: self._qualified_pin(instance, _text(value)),
            )
            result["component"] = {"instance": instance, **board._compact(component)}
            result["mates"] = cast(Json, self._connector_mates.get((instance, component), []))
            return result
        if operation == "search":
            selected = (
                [_text(inputs["instance"])] if "instance" in inputs else list(self._instances)
            )
            text = _text(inputs.get("text", "")).casefold()
            kind = _text(inputs["kind"]) if "kind" in inputs else None
            matches: list[Json] = []
            for instance in selected:
                board = self._board(instance)
                for key in (
                    board._kinds.get(kind, []) if kind is not None else sorted(board._records)
                ):
                    if text in board._search[key]:
                        matches.append([instance, key])
            return self._pager._page(
                operation,
                inputs,
                matches,
                lambda value: {
                    "instance": _array(value)[0],
                    **self._board(_text(_array(value)[0]))._compact(_text(_array(value)[1])),
                },
            )
        if operation == "circuit":
            return self._circuit(inputs)
        if operation == "system_nets":
            instance_filter = inputs.get("instance")
            if instance_filter is not None:
                self._board(_text(instance_filter))
            text = _text(inputs.get("text", "")).casefold()
            keys = [
                key
                for key, members in sorted(self._groups.items())
                if (instance_filter is None or any(node[0] == instance_filter for node in members))
                and any(
                    text in str(self._board(node[0])._data(node[1]).get("name", "")).casefold()
                    for node in members
                )
            ]
            return self._pager._page(
                operation,
                inputs,
                cast(list[Json], keys),
                lambda value: self._system_summary(_text(value)),
            )
        if operation == "system_net":
            if "id" in inputs:
                if any(name in inputs for name in ("instance", "name", "net_id")):
                    raise ValueError("Select a system identifier or a board-local net.")
                key = _text(inputs["id"])
            else:
                if ("name" in inputs) == ("net_id" in inputs):
                    raise ValueError("Select one board-local net name or identifier.")
                instance = _text(inputs.get("instance"))
                board = self._board(instance)
                net = board._resolve(
                    {"id": inputs["net_id"]} if "net_id" in inputs else {"name": inputs["name"]},
                    "net",
                    "name",
                )
                key = self._system_ids[(instance, net)]
            if key not in self._groups:
                raise ValueError("Unknown system net.")
            view = inputs.get("view", "members")
            if view == "members":
                result = self._pager._page(
                    operation,
                    inputs,
                    cast(list[Json], [list(node) for node in self._groups[key]]),
                    lambda value: self._member((_text(_array(value)[0]), _text(_array(value)[1]))),
                )
            elif view == "endpoints":
                endpoints = sorted(
                    {(node[0], pin) for node in self._groups[key] for pin in self._net_pins[node]}
                )
                result = self._pager._page(
                    operation,
                    inputs,
                    cast(list[Json], [list(node) for node in endpoints]),
                    lambda value: self._qualified_pin(
                        _text(_array(value)[0]), _text(_array(value)[1])
                    ),
                )
            else:
                raise ValueError("System net view must be members or endpoints.")
            result["system_net"] = self._system_summary(key)
            return result
        if operation == "placement_check":
            return self._placement_check(inputs)
        if operation == "mate_candidates":
            return self._mate_candidates(inputs)
        if operation == "compare_connectors":
            left = (_text(inputs.get("left_instance")), _text(inputs.get("left_id")))
            right = (_text(inputs.get("right_instance")), _text(inputs.get("right_id")))
            comparison = _compare_profiles(
                _profile(self, left),
                _profile(self, right),
                _text(inputs.get("mapping", "same_number")),
            )
            result = self._page(operation, inputs, _array(comparison.pop("pairs")))
            result["comparison"] = comparison
            return result
        if operation == "trace":
            return self._trace(inputs)
        raise ValueError("Unsupported assembly operation.")

    def _circuit(self, inputs: Object) -> Object:
        if ("name" in inputs) == ("net_id" in inputs):
            raise ValueError("Select one board-local net name or identifier.")
        instance = _text(inputs.get("instance"), "instance")
        board = self._board(instance)
        net = board._resolve(
            {"id": inputs["net_id"]} if "net_id" in inputs else {"name": inputs["name"]},
            "net",
            "name",
        )
        board._circuit_component_limit(inputs)
        scope = _text(inputs.get("scope", "all"), "scope")
        system = self._system_ids[(instance, net)]
        focus: dict[str, set[str]] = defaultdict(set)
        components: set[tuple[str, str]] = set()
        scans = 0
        for member, local_net in self._groups[system]:
            target = self._board(member)
            scans += len(target._links(local_net, "pins"))
            if scans > 100000:
                raise ValueError("Circuit query exceeds the system endpoint scan limit.")
            focus[member].add(local_net)
            components.update(
                (member, component) for component in target._circuit_components(local_net, scope)
            )

        def render(value: Json) -> Object:
            member, component = _strings(value)
            target = self._board(member)
            row = target._circuit_component(component, focus[member], inputs)
            row["instance"] = member
            for pin in _array(row["pins"]):
                item = _object(pin)
                item["system_net_ids"] = [
                    self._system_ids[(member, _text(_object(net)["id"]))]
                    for net in _array(item["nets"])
                ]
                mates = self._pin_mates.get((member, _text(item["id"])), [])
                item["mate_ids"] = [_object(mate)["mate_id"] for mate in mates[:8]]
                item["mate_ids_truncated"] = len(mates) > 8
            return row

        result = self._pager._page(
            "circuit",
            inputs,
            cast(
                list[Json],
                [
                    list(item)
                    for item in sorted(
                        components,
                        key=lambda pair: (
                            pair[0],
                            str(self._board(pair[0])._data(pair[1]).get("reference", "")),
                            pair[1],
                        ),
                    )
                ],
            ),
            render,
        )
        result["system_net"] = self._system_summary(system)
        result["meaning"] = (
            "Component adjacency across declared mates. "
            "No internal connection or current flow is inferred."
        )
        return result

    def _mate_candidates(self, inputs: Object) -> Object:
        instance = _text(inputs.get("instance"), "instance")
        board = self._board(instance)
        component = board._resolve(
            {key: value for key, value in inputs.items() if key in ("id", "reference")},
            "component",
            "reference",
        )
        left = _profile(self, (instance, component))
        selected = (
            [_text(inputs["other_instance"])]
            if "other_instance" in inputs
            else [key for key in self._instances if key != instance]
        )
        rows: list[Object] = []
        work = 0
        text_work = 0
        skipped: Counter[str] = Counter()
        for other in selected:
            target = self._board(other)
            for key in target._kinds.get("component", []):
                data = target._data(key)
                reference = str(data.get("reference", ""))
                if not target._links(key, "pads") or not (
                    reference.startswith(("J", "P"))
                    or "connector" in str(data.get("footprint", "")).casefold()
                ):
                    continue
                work += len(target._links(key, "pins")) + len(_object(left["pins"]))
                if work > 250000:
                    raise ValueError(
                        "Connector candidate scan exceeds the work limit. Select another instance."
                    )
                try:
                    right_profile = _profile(self, (other, key))
                except ValueError as error:
                    skipped[str(error)] += 1
                    continue
                text_work += cast(int, left["_text_work"]) + cast(int, right_profile["_text_work"])
                if text_work > 4000000:
                    raise ValueError("Connector candidate scan exceeds the text-work limit.")
                try:
                    comparison = _compare_profiles(left, right_profile, "same_number")
                except ValueError as error:
                    skipped[str(error)] += 1
                    continue
                comparison.pop("pairs")
                rows.append(comparison)
        rows.sort(
            key=lambda row: (
                -cast(int, row["named_signal_matches"]),
                -cast(int, row["common_name_matches"]),
                cast(int, row["different_named_pairs"]),
                _text(_object(row["right"])["instance"]),
                _text(_object(row["right"])["component_id"]),
            )
        )
        result = self._page("mate_candidates", inputs, cast(list[Json], rows))
        result["skipped_candidates"] = dict(skipped)
        result["method"] = (
            "Same-number hypotheses ranked by matching leaf net names. "
            "GND, EARTH, VCC, VDD, and VSS rank after other matches. "
            "Board-resident J/P or footprint-named connectors only. No mate is created."
        )
        return result

    def _placement_check(self, inputs: Object) -> Object:
        left = _text(inputs.get("left_instance"))
        right = _text(inputs.get("right_instance"))
        self._board(left)
        self._board(right)
        if left == right:
            raise ValueError("Select distinct board instances for a placement check.")
        tolerance = _number(inputs.get("tolerance_mm", 0.1), "tolerance_mm")
        if not 0 <= tolerance <= 1000:
            raise ValueError("Placement tolerance must be from zero through 1000 millimeters.")
        positions: list[tuple[list[float], list[float]]] = []
        matched: list[Json] = []
        missing = 0
        for key, value in self._mate_rows.items():
            if value["status"] != "resolved":
                continue
            a, b = _endpoint(value["left"]), _endpoint(value["right"])
            if {a[0], b[0]} != {left, right}:
                continue
            matched.append(key)
            left_profile = _object(_profile(self, a)["pins"])
            right_profile = _object(_profile(self, b)["pins"])
            for pair_value in self._derived_pairs[key]:
                pair = _object(pair_value)
                first = _object(left_profile[_text(_object(pair["left"])["number"])])["position"]
                second = _object(right_profile[_text(_object(pair["right"])["number"])])[
                    "position"
                ]
                if first is None or second is None:
                    missing += 1
                    continue
                p, q = _array(first), _array(second)
                if len(p) != 2 or len(q) != 2:
                    missing += 1
                    continue
                x, y = [_number(v) for v in p], [_number(v) for v in q]
                positions.append((x, y) if a[0] == left else (y, x))
        fit = _fit_positions(positions)
        status = (
            "unavailable"
            if fit is None or missing
            else ("fits" if _number(fit["maximum_residual_mm"]) <= tolerance else "conflict")
        )
        return {
            "left_instance": left,
            "right_instance": right,
            "status": status,
            "fit": fit,
            "mates": matched,
            "missing_pad_positions": missing,
            "tolerance_mm": tolerance,
            "meaning": (
                "Rigid XY fit of resolved mates only. "
                "Height, collision, and hardware compatibility remain unknown."
            ),
        }

    def _qualified_pin(self, instance: str, pin: str) -> Object:
        board = self._board(instance)
        mapped = self._pin_mates.get((instance, pin), [])
        mates = {
            mate
            for component in board._links(pin, "component")
            for mate in self._connector_mates.get((instance, component), [])
        }
        status = (
            "mapped"
            if mapped
            else "unresolved_mate"
            if any(self._mate_rows[mate]["status"] != "resolved" for mate in mates)
            else "unmapped"
            if mates
            else "unmated"
        )
        return {
            "instance": instance,
            **board._pin(pin),
            "mapping_status": status,
            "mate_connections": mapped,
            "system_net_ids": cast(
                Json,
                sorted({self._system_ids[(instance, net)] for net in board._links(pin, "nets")}),
            ),
        }

    def _trace(self, inputs: Object) -> Object:
        start = self._local_net(inputs.get("from_instance"), inputs.get("from_net"))
        end = self._local_net(inputs.get("to_instance"), inputs.get("to_net"))
        kinds = set(_strings(inputs.get("relationship_kinds", [])))
        limit = inputs.get("max_visited", 10000)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ValueError("Trace limit must be an integer from 1 through 100000.")
        previous: dict[tuple[str, str], tuple[tuple[str, str], str] | None] = {start: None}
        queue = deque([start])
        expanded: set[str] = set()
        scanned = 0
        while queue:
            node = queue.popleft()
            if node == end:
                path: list[Json] = [self._member(node)]
                while previous[node] is not None:
                    step = previous[node]
                    assert step is not None
                    node, edge = step
                    path.extend([self._edges[edge][1], self._member(node)])
                return {
                    "found": True,
                    "path": list(reversed(path)),
                    "visited": len(previous),
                    "meaning": "Declared structural path. Current flow is not evaluated.",
                }
            for edge in self._adjacency[node]:
                scanned += 1
                if scanned > limit * 32:
                    raise ValueError("Assembly trace exceeds the link scan limit.")
                nodes, _, kind = self._edges[edge]
                if edge in expanded or (kind is not None and kind not in kinds):
                    continue
                expanded.add(edge)
                for target in nodes:
                    scanned += 1
                    if scanned > limit * 32:
                        raise ValueError("Assembly trace exceeds the link scan limit.")
                    if target not in previous:
                        if len(previous) >= limit:
                            raise ValueError("Assembly trace exceeds the visited-net limit.")
                        previous[target] = (node, edge)
                        queue.append(target)
        return {"found": False, "path": [], "visited": len(previous)}


def open_portable_inspection(path: str | Path) -> DesignInspector | AssemblyInspector:
    if Path(path).suffix.lower() == ".jsonl":
        return DesignInspector.open(path)
    document = _object(_json(_read(path)))
    return (
        AssemblyInspector(document)
        if document.get("schema_version") == "netbom-assembly-1"
        else DesignInspector(document)
    )
