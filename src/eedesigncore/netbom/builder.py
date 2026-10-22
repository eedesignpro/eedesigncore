"""Board-first netbom extraction without design changes or external lookups."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue

from eedesigncore.common.kimath import rotate_point_iu
from eedesigncore.common.models import Property
from eedesigncore.common.paths import is_remote_uri
from eedesigncore.common.pcb_family.board_items import Zone
from eedesigncore.common.sch_family.ordering import resolve_parent_symbol
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.source_reads import _active, _capture_reads, _record_absent
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.comparison import content_signature
from eedesigncore.connectivity.geometry import pin_sheet_position, symbol_transform
from eedesigncore.connectivity.units import iu_to_mm
from eedesigncore.connectivity.units import point_to_iu as sheet_point_to_iu
from eedesigncore.exceptions import KicadParseError, SheetHierarchyError
from eedesigncore.mod.models import Footprint
from eedesigncore.netbom.models import (
    NetbomCoverage,
    NetbomDocument,
    NetbomEvidence,
    NetbomRecord,
)
from eedesigncore.netbom.source_evidence import _manifest
from eedesigncore.netbom.zone_areas import zone_area_estimates
from eedesigncore.pcb.models import Board, TrackSegment, Via
from eedesigncore.pro.models import ProjectFile
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode, load_hierarchy
from eedesigncore.project.netclasses import read_netclass_assignments
from eedesigncore.sch.models import PlacedSymbol, Schematic


def _json(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return cast(JsonValue, json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False)))


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _id(kind: str, value: object) -> str:
    return kind + ":" + _digest(value)


def _fields(fields: list[Property]) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    empty: list[str] = []
    duplicate: list[JsonValue] = []
    seen: set[str] = set()
    for item in fields:
        if item.name in seen:
            duplicate.append({"name": item.name, "value": item.value})
        elif item.value == "":
            empty.append(item.name)
        else:
            values[item.name] = item.value
        seen.add(item.name)
    return {
        "fields": values,
        "empty_fields": _json(sorted(empty)),
        "duplicate_fields": duplicate,
        "absent_standard_fields": _json(
            sorted({"Reference", "Value", "Footprint", "Datasheet", "Description"} - seen)
        ),
    }


def _field(fields: list[Property], name: str) -> str:
    return next((item.value for item in fields if item.name == name), "")


def _canonical_path(value: str | None) -> str | None:
    if not value or not value.startswith("/"):
        return None
    parts = value[1:].split("/")
    try:
        if any(str(UUID(part)) != part.lower() for part in parts):
            return None
    except ValueError:
        return None
    return "/" + "/".join(part.lower() for part in parts)


def _pad_position(footprint: Footprint, point: tuple[float, float]) -> tuple[float, float]:
    if footprint.at is None:
        return point
    origin = point_to_iu(footprint.at.position)
    turned = rotate_point_iu(point_to_iu(point), footprint.at.angle)
    return to_mm(turned[0] + origin[0]), to_mm(turned[1] + origin[1])


class _Builder:
    def __init__(self, name: str, max_records: int) -> None:
        if not 1 <= max_records <= 1000000:
            raise ValueError("Invalid netbom record limit.")
        self.document = NetbomDocument(design_id=_id("design", name), design_name=name)
        self.max_records = max_records
        self.board_source = ""
        self.schematic_supplied = False
        self.components_by_reference: dict[str, list[str]] = defaultdict(list)
        self.components_by_path: dict[str, list[str]] = defaultdict(list)
        self.pins: dict[tuple[str, str], str] = {}
        self.nets: dict[str, str] = {}
        self.no_connects: dict[tuple[str, tuple[int, int]], list[str]] = defaultdict(list)

    def add(
        self,
        kind: str,
        key: object,
        data: dict[str, JsonValue],
        evidence: NetbomEvidence | None = None,
    ) -> NetbomRecord:
        identifier = _id(kind, [self.document.design_id, key])
        if identifier in self.document.records:
            raise ValueError("Netbom record identity collision.")
        if len(self.document.records) >= self.max_records:
            raise ValueError("Netbom exceeds the record limit.")
        record = NetbomRecord(
            id=identifier, kind=kind, data=data, evidence=[] if evidence is None else [evidence]
        )
        self.document.records[identifier] = record
        return record

    def definition(self, kind: str, data: dict[str, JsonValue], evidence: NetbomEvidence) -> str:
        identifier = _id(kind, [self.document.design_id, data])
        if identifier not in self.document.records:
            return self.add(kind, data, data, evidence).id
        record = self.document.records[identifier]
        if evidence not in record.evidence:
            record.evidence.append(evidence)
        return identifier

    def link(self, left: str, relation: str, right: str, reverse: str) -> None:
        for source, name, target in ((left, relation, right), (right, reverse, left)):
            values = self.document.records[source].links.setdefault(name, [])
            if target not in values:
                values.append(target)

    def issue(self, code: str, message: str, records: list[str]) -> None:
        issue = self.add("issue", [code, records, message], {"code": code, "message": message})
        for target in records:
            self.link(issue.id, "subjects", target, "issues")

    def source(self, kind: str, locator: str, value: Board | Schematic | ProjectFile) -> str:
        return self.add(
            "source",
            [kind, locator],
            {"format": kind, "locator": locator, "content_signature": content_signature(value)},
        ).id

    def net(self, name: str) -> str:
        if name not in self.nets:
            record = self.add(
                "net",
                name,
                {
                    "name": name,
                    "name_origin": "unknown",
                    "netclass_status": "unresolved",
                    "connectivity_basis": "saved_board_assignment",
                },
                NetbomEvidence(
                    source_id=self.board_source, locator="net_names", representation="parsed_value"
                ),
            )
            self.nets[name] = record.id
        return self.nets[name]

    def endpoint(self, component: str, number: str, fallback: str = "") -> str:
        key = component, number if number else "unnumbered:" + fallback
        if key not in self.pins:
            record = self.add("pin", key, {"number": number, "connection_status": "unresolved"})
            self.link(component, "pins", record.id, "component")
            self.pins[key] = record.id
        return self.pins[key]

    def board(self, board: Board) -> None:
        self.board_source = self.source("kicad_pcb", "board", board)
        counts = Counter(fp.uuid for fp in board.footprints if fp.uuid)
        board_record = self.add(
            "board",
            "board",
            {
                "layers": _json([item.model_dump(mode="json") for item in board.layers]),
                "stackup": _json(board.setup.stackup)
                if board.setup and board.setup.stackup
                else None,
                "general": _json(board.general),
                "title_block": _json(board.title_block),
                "outline": _json(
                    [
                        item.model_dump(mode="json")
                        for item in [*board.drawings, *board.trailing_drawings]
                        if getattr(item, "layer", None) == "Edge.Cuts"
                    ]
                ),
                "variants": _json([item.model_dump(mode="json") for item in board.variants]),
            },
            NetbomEvidence(source_id=self.board_source, locator="board"),
        )
        for index, item in enumerate([*board.drawings, *board.trailing_drawings]):
            text = getattr(item, "text", None)
            if isinstance(text, str):
                note = self.add(
                    "board_note",
                    index,
                    {"text": text, "layer": getattr(item, "layer", None)},
                    NetbomEvidence(source_id=self.board_source, locator=f"drawings/{index}"),
                )
                self.link(board_record.id, "notes", note.id, "board")
        for index, footprint in enumerate(board.footprints):
            evidence = NetbomEvidence(source_id=self.board_source, locator=f"footprints/{index}")
            stable = (
                footprint.uuid
                if footprint.uuid and counts[footprint.uuid] == 1
                else f"index:{index}"
            )
            reference = _field(footprint.properties, "Reference")
            component = self.add(
                "component",
                ["board", stable],
                {
                    "reference": reference,
                    "value": _field(footprint.properties, "Value"),
                    "footprint": footprint.fp_id,
                    "schematic_path": footprint.path,
                    "identity_basis": "uuid" if stable == footprint.uuid else "occurrence_index",
                    "placement": _json(footprint.at),
                    "layer": footprint.layer,
                    "side": "bottom"
                    if footprint.layer == "B.Cu"
                    else "top"
                    if footprint.layer == "F.Cu"
                    else "unknown",
                    "assembly": _json(footprint.attributes),
                    **_fields(footprint.properties),
                },
                evidence,
            )
            self.link(board_record.id, "components", component.id, "board")
            self.components_by_reference[reference.casefold()].append(component.id)
            canonical = _canonical_path(footprint.path)
            if canonical:
                self.components_by_path[canonical].append(component.id)
            elif footprint.path:
                self.issue(
                    "invalid_schematic_path",
                    "Footprint association is not a UUID path.",
                    [component.id],
                )
            pad_geometry: list[JsonValue] = []
            for pad in footprint.pads:
                data = pad.model_dump(mode="json")
                for name in ("id", "net_name", "net_code", "pinfunction", "pintype"):
                    data.pop(name, None)
                pad_geometry.append(_json(data))
            definition = self.definition("footprint_definition", {"pads": pad_geometry}, evidence)
            self.link(component.id, "definitions", definition, "instances")
            pad_counts = Counter(pad.id for pad in footprint.pads if pad.id)
            for pad_index, pad in enumerate(footprint.pads):
                pad_key = pad.id if pad.id and pad_counts[pad.id] == 1 else f"index:{pad_index}"
                record = self.add(
                    "pad",
                    [component.id, pad_key],
                    {
                        "number": pad.number,
                        "definition_index": pad_index,
                        "board_position": _json(_pad_position(footprint, pad.position)),
                        "local_position": _json(pad.position),
                        "layers": _json(pad.layers),
                        "saved_pin_function": pad.pinfunction,
                        "saved_pin_type": pad.pintype,
                        "assignment_status": "assigned" if pad.net_name else "unassigned",
                    },
                    NetbomEvidence(
                        source_id=self.board_source,
                        locator=f"footprints/{index}/pads/{pad_index}",
                        derivation="calculated",
                        rule="footprint-pad-transform-v1",
                    ),
                )
                self.link(component.id, "pads", record.id, "component")
                endpoint = self.endpoint(component.id, pad.number, record.id)
                self.link(endpoint, "pads", record.id, "pin")
                if pad.net_name:
                    net = self.net(pad.net_name)
                    self.link(endpoint, "nets", net, "pins")
                    self.link(record.id, "nets", net, "pads")
            if not footprint.uuid or counts[footprint.uuid] > 1:
                self.issue(
                    "unstable_component_identity",
                    "Missing or repeated footprint UUID. Identity uses source position.",
                    [component.id],
                )
            for zone_index, zone in enumerate(footprint.zones):
                self.zone(
                    zone,
                    [component.id, zone.id, zone_index],
                    f"footprints/{index}/zones/{zone_index}",
                    board_record.id,
                    board,
                    component.id,
                )
        for name in board.net_names:
            if name:
                self.net(name)
        summaries: dict[str, dict[str, JsonValue]] = {}
        for track in board.tracks:
            name = track.net or ""
            if not name:
                continue
            net = self.net(name)
            summary = summaries.setdefault(
                net, {"straight_length_mm": 0.0, "segments": 0, "arcs": 0, "vias": 0}
            )
            if isinstance(track, TrackSegment):
                length = (
                    (track.end[0] - track.start[0]) ** 2 + (track.end[1] - track.start[1]) ** 2
                ) ** 0.5
                summary["straight_length_mm"] = (
                    float(cast(float, summary["straight_length_mm"])) + length
                )
                summary["segments"] = int(cast(int, summary["segments"])) + 1
            else:
                key = "vias" if isinstance(track, Via) else "arcs"
                summary[key] = int(cast(int, summary[key])) + 1
        for net, summary in summaries.items():
            summary["length_scope"] = (
                "Sum of straight segments only. Not a pin-to-pin route length."
            )
            self.document.records[net].data["routing"] = summary
        for index, zone in enumerate(board.zones):
            self.zone(zone, [zone.id, index], f"zones/{index}", board_record.id, board)
        for endpoint in list(self.pins.values()):
            record = self.document.records[endpoint]
            nets = record.links.get("nets", [])
            record.data["connection_status"] = (
                "assigned" if len(nets) == 1 else "unassigned" if not nets else "conflicting"
            )
            if len(nets) > 1:
                self.issue(
                    "pad_number_net_conflict",
                    "Pads with the same number have different saved net assignments.",
                    [endpoint],
                )

    def zone(
        self,
        zone: Zone,
        identity: list[object],
        locator: str,
        board_id: str,
        board: Board,
        component_id: str | None = None,
    ) -> None:
        rule_area = zone.keepout is not None or zone.placement is not None
        record = self.add(
            "zone",
            identity,
            {
                "name": zone.name,
                "layers": _json(zone.layers or ([zone.layer] if zone.layer else [])),
                "priority": zone.priority,
                "filled_polygon_count": len(zone.filled_polygons),
                "fill_freshness": "unknown",
                "rule_area": rule_area,
                "area_estimates": None
                if rule_area
                else zone_area_estimates(zone, [layer.canonical_name for layer in board.layers]),
            },
            NetbomEvidence(
                source_id=self.board_source,
                locator=locator,
                derivation="calculated",
                rule="saved-linear-zone-polygons-v1",
            ),
        )
        if zone.net_name:
            self.link(record.id, "nets", self.net(zone.net_name), "zones")
        self.link(board_id, "zones", record.id, "board")
        if component_id is not None:
            self.link(component_id, "zones", record.id, "component")

    def schematic(self, hierarchy: SheetHierarchy) -> None:
        self.schematic_supplied = True
        source_ids: dict[str, str] = {}
        entries: list[tuple[SheetNode, str, str, PlacedSymbol, int, int]] = []
        root_paths: dict[str, str] = {}
        stack: list[tuple[SheetNode, str | None, int, frozenset[int], str]] = [
            (root, None, 0, frozenset(), root.kiid_path) for root in reversed(hierarchy.roots)
        ]
        sheet_paths: set[str] = set()
        while stack:
            node, parent, depth, ancestors, root_path = stack.pop()
            if depth > 32 or id(node) in ancestors:
                raise ValueError("Netbom hierarchy contains a cycle or excessive depth.")
            if len(sheet_paths) >= 4096:
                raise ValueError("Netbom hierarchy exceeds the sheet occurrence limit.")
            path = node.kiid_path
            if path in sheet_paths:
                raise ValueError("Netbom hierarchy has repeated occurrence paths.")
            sheet_paths.add(path)
            root_paths[path] = root_path
            locator = (
                str(node.path.relative_to(hierarchy.project_root))
                if node.path.is_relative_to(hierarchy.project_root)
                else node.path.name
            )
            if locator not in source_ids:
                source_ids[locator] = self.source("kicad_sch", locator, node.schematic)
            source = source_ids[locator]
            sheet = self.add(
                "sheet",
                path,
                {
                    "name": node.name,
                    "page": node.page,
                    "uuid_path": path,
                    "source_file": locator,
                    "title_block": _json(node.schematic.title_block),
                },
                NetbomEvidence(source_id=source, locator="sheet"),
            )
            if parent:
                self.link(parent, "children", sheet.id, "parent")
            for child in reversed(node.children):
                stack.append((child, sheet.id, depth + 1, ancestors | {id(node)}, root_path))
            for index, placed in enumerate(node.schematic.symbols):
                if len(entries) + len(self.document.records) >= self.max_records:
                    raise ValueError("Netbom exceeds the symbol occurrence limit.")
                entries.append((node, source, sheet.id, placed, index, len(entries)))
            for kind, items in (
                ("note", node.schematic.texts),
                ("text_box", node.schematic.text_boxes),
                ("label", node.schematic.labels),
            ):
                for index, item in enumerate(items):
                    record = self.add(
                        kind,
                        [sheet.id, getattr(item, "uuid", None), index],
                        {
                            "content": str(getattr(item, "content", "")),
                            "position": _json(getattr(item, "position", None)),
                            "label_kind": str(getattr(item, "kind", "")),
                            "fields": _json(
                                [p.model_dump(mode="json") for p in getattr(item, "fields", [])]
                            ),
                        },
                        NetbomEvidence(
                            source_id=source, locator=f"{kind}/{index}", representation="explicit"
                        ),
                    )
                    self.link(sheet.id, "annotations", record.id, "sheet")
            for index, marker in enumerate(node.schematic.no_connects):
                record = self.add(
                    "no_connect",
                    [sheet.id, index],
                    {"position": _json(marker.position)},
                    NetbomEvidence(
                        source_id=source, locator=f"no_connects/{index}", representation="explicit"
                    ),
                )
                self.link(sheet.id, "no_connects", record.id, "sheet")
                self.no_connects[(node.kiid_path, sheet_point_to_iu(marker.position))].append(
                    record.id
                )
        references: dict[str, list[int]] = defaultdict(list)
        resolved: list[tuple[str, int, list[JsonValue], bool]] = []
        for node, _source, _sheet, placed, _index, position in entries:
            instance_paths = [
                p
                for group in placed.instances
                for p in group.paths
                if p.path.rstrip("/").casefold() == node.kiid_path.rstrip("/").casefold()
            ]
            unique = {(p.reference, p.unit) for p in instance_paths}
            ambiguous = len(unique) > 1
            reference, unit = (
                next(iter(unique))
                if len(unique) == 1
                else (_field(placed.fields, "Reference"), placed.unit)
            )
            variants = [_json(p.model_dump(mode="json")) for p in instance_paths]
            references[reference.casefold()].append(position)
            resolved.append((reference, unit, variants, ambiguous))
        symbol_counts = Counter(
            (node.kiid_path, placed.uuid) for node, _, _, placed, _, _ in entries
        )
        path_aliases: list[set[str]] = []
        path_owners: dict[str, set[int]] = defaultdict(set)
        for node, _, _, placed, _, position in entries:
            canonical_path = _canonical_path(
                node.kiid_path.rstrip("/") + "/" + str(placed.uuid or "")
            )
            aliases = {canonical_path} if canonical_path else set()
            canonical_root = _canonical_path(root_paths[node.kiid_path].rstrip("/"))
            if (
                canonical_path
                and canonical_root
                and canonical_path.startswith(canonical_root + "/")
            ):
                aliases.add(canonical_path[len(canonical_root) :])
            path_aliases.append(aliases)
            for alias in aliases:
                path_owners[alias].add(position)
        exact_matches: list[list[str]] = []
        path_conflicts: list[bool] = []
        for node, _, _, placed, _, position in entries:
            aliases = path_aliases[position]
            exact_matches.append(
                sorted(
                    {
                        candidate
                        for alias in aliases
                        for candidate in self.components_by_path[alias]
                    }
                )
                if symbol_counts[(node.kiid_path, placed.uuid)] == 1
                else []
            )
            path_conflicts.append(
                any(
                    len(path_owners[alias]) > 1 and self.components_by_path[alias]
                    for alias in aliases
                )
            )
        for node, source, sheet_id, placed, index, position in entries:
            reference, unit, variants, ambiguous = resolved[position]
            ambiguous = ambiguous or path_conflicts[position]
            stable = (
                placed.uuid
                if placed.uuid and symbol_counts[(node.kiid_path, placed.uuid)] == 1
                else f"index:{index}"
            )
            evidence = NetbomEvidence(source_id=source, locator=f"symbols/{index}")
            occurrence = self.add(
                "symbol_instance",
                [node.kiid_path, stable],
                {
                    "reference": reference,
                    "lib_id": placed.lib_id,
                    "unit": unit,
                    "body_style": placed.body_style,
                    "position": _json(placed.position),
                    "angle": placed.angle,
                    "mirror": placed.mirror,
                    "assembly": {
                        "in_bom": placed.in_bom,
                        "on_board": placed.on_board,
                        "dnp": placed.dnp,
                        "in_pos_files": placed.in_pos_files,
                    },
                    "instance_variants": variants,
                    "identity_basis": "uuid" if stable == placed.uuid else "occurrence_index",
                    **_fields(placed.fields),
                },
                evidence,
            )
            self.link(sheet_id, "symbols", occurrence.id, "sheet")
            if stable != placed.uuid:
                self.issue(
                    "symbol_identity_fallback",
                    "Symbol UUID is missing or duplicated.",
                    [occurrence.id],
                )
            candidates = exact_matches[position]
            basis = "exact_path"
            unresolved_candidates: list[str] = []
            if not candidates and reference and not reference.endswith("?") and not ambiguous:
                group = references[reference.casefold()]
                units = [resolved[i][1] for i in group]
                libraries = {entries[i][3].lib_id for i in group}
                compatible = len(units) == len(set(units)) and len(libraries) == 1
                reference_candidates = self.components_by_reference.get(reference.casefold(), [])
                anchors = {
                    candidate
                    for i in group
                    for candidate in exact_matches[i]
                    if candidate in reference_candidates
                }
                if (
                    compatible
                    and len(anchors) == 1
                    and all(len(exact_matches[i]) <= 1 and not path_conflicts[i] for i in group)
                ):
                    candidates = sorted(anchors)
                    basis = "anchored_multiunit_reference"
                elif compatible:
                    candidates = [
                        c
                        for c in reference_candidates
                        if not self.document.records[c].data.get("schematic_path")
                    ]
                    basis = "unique_reference_and_units"
                unresolved_candidates = reference_candidates
            if len(candidates) == 1 and basis == "exact_path":
                board_reference = self.document.records[candidates[0]].data.get("reference")
                if (
                    isinstance(board_reference, str)
                    and board_reference.casefold() != reference.casefold()
                ):
                    self.issue(
                        "reference_mismatch",
                        "Board and schematic references differ.",
                        [occurrence.id, candidates[0]],
                    )
            if len(candidates) == 1 and not ambiguous:
                component = candidates[0]
                occurrence.data["board_match"] = basis
                occurrence.data["association_derivation"] = (
                    "copied" if basis == "exact_path" else "inferred"
                )
                if basis != "exact_path":
                    occurrence.evidence.append(
                        NetbomEvidence(
                            source_id=source,
                            locator=f"symbols/{index}",
                            derivation="inferred",
                            rule=basis + "-v1",
                            inputs=[component],
                        )
                    )
                self.link(component, "symbols", occurrence.id, "component")
            else:
                component = self.add(
                    "component",
                    ["schematic", occurrence.id],
                    {
                        "reference": reference,
                        "value": _field(placed.fields, "Value"),
                        "board_match": "conflicting" if candidates or ambiguous else "unresolved",
                        **_fields(placed.fields),
                    },
                    evidence,
                ).id
                self.link(component, "symbols", occurrence.id, "component")
                for candidate in sorted(set(candidates + unresolved_candidates)):
                    self.link(
                        occurrence.id, "candidate_components", candidate, "candidate_symbols"
                    )
                if self.board_source and not reference.startswith("#") and placed.on_board:
                    self.issue(
                        "unmatched_schematic_instance",
                        "No unambiguous board component association was found.",
                        [occurrence.id, *sorted(set(candidates + unresolved_candidates))],
                    )
            self.symbol_pins(node, placed, unit, source, index, occurrence.id, component)
        for error in dict.fromkeys(hierarchy.errors):
            self.issue("hierarchy_incomplete", error, [])

    def symbol_pins(
        self,
        node: SheetNode,
        placed: PlacedSymbol,
        unit: int,
        source: str,
        index: int,
        occurrence: str,
        component: str,
    ) -> None:
        matches = [
            s for s in node.schematic.lib_symbols if s.name == (placed.lib_name or placed.lib_id)
        ]
        if len(matches) != 1:
            self.issue(
                "symbol_definition_unresolved",
                "Embedded symbol definition is missing or ambiguous.",
                [occurrence],
            )
            return
        symbol = matches[0]
        seen: set[str] = set()
        by_name = {s.name: s for s in node.schematic.lib_symbols}
        while symbol.extends:
            if symbol.name in seen:
                self.issue(
                    "symbol_inheritance_unresolved",
                    "Symbol inheritance contains a cycle.",
                    [occurrence],
                )
                return
            seen.add(symbol.name)
            parent = resolve_parent_symbol(symbol, by_name)
            if parent is None:
                self.issue(
                    "symbol_inheritance_unresolved",
                    "Parent symbol is absent from the supplied design.",
                    [occurrence],
                )
                return
            symbol = parent
        definitions: list[JsonValue] = []
        selected: list[tuple[Pin, int]] = []
        for symbol_unit in symbol.units:
            for item in symbol_unit.items:
                if isinstance(item, Pin):
                    definitions.append(
                        {
                            "unit": symbol_unit.unit_index,
                            "body_style": symbol_unit.body_style_index,
                            "pin": _json(item),
                        }
                    )
                    if symbol_unit.unit_index in (0, unit) and symbol_unit.body_style_index in (
                        0,
                        placed.body_style,
                    ):
                        selected.append((item, len(definitions) - 1))
        evidence = NetbomEvidence(source_id=source, locator=f"lib_symbols/{matches[0].name}")
        definition = self.definition(
            "symbol_definition",
            {
                "pins": definitions,
                "power_type": _json(symbol.power_type),
                **_fields(matches[0].fields),
            },
            evidence,
        )
        self.link(occurrence, "definitions", definition, "instances")
        for pin, definition_index in selected:
            choices = {p.alternate for p in placed.pins if p.number == pin.number and p.alternate}
            alternate_choices = choices - {pin.name}
            available = [a for a in pin.alternates if a.name in alternate_choices]
            invalid = len(choices) > 1 or bool(alternate_choices) and len(available) != 1
            alternate = available[0] if len(available) == 1 and not invalid else None
            record = self.add(
                "symbol_pin",
                [occurrence, definition_index],
                {
                    "number": pin.number,
                    "base_name": pin.name,
                    "effective_name": None
                    if invalid
                    else alternate.name
                    if alternate
                    else pin.name,
                    "effective_electrical_type": None
                    if invalid
                    else (alternate.electrical_type if alternate else pin.electrical_type).value,
                    "effective_shape": None
                    if invalid
                    else (alternate.shape if alternate else pin.shape).value,
                    "alternate_selection": _json(sorted(choices)),
                    "placed_pin_uuids": _json(
                        [p.uuid for p in placed.pins if p.number == pin.number]
                    ),
                    "selection_status": "unresolved"
                    if invalid
                    else "selected"
                    if alternate
                    else "base",
                    "definition_index": definition_index,
                },
                NetbomEvidence(
                    source_id=source,
                    locator=f"symbols/{index}/pins/{pin.number}",
                    derivation="calculated",
                    rule="selected-pin-function-v1",
                    inputs=[definition, occurrence],
                ),
            )
            self.link(occurrence, "pins", record.id, "symbol")
            endpoint = self.endpoint(component, pin.number, record.id)
            self.link(endpoint, "symbol_pins", record.id, "pin")
            if pin.number.startswith("[") and pin.number.endswith("]"):
                record.data["number_status"] = "stacked_notation_unresolved"
                self.issue(
                    "stacked_pin_number_unresolved",
                    "Stacked pin numbers require expansion before pad matching.",
                    [record.id],
                )
            else:
                record.data["number_status"] = "literal"
            if placed.angle % 90 == 0 and placed.mirror in (None, "x", "y"):
                location = pin_sheet_position(
                    pin.position, symbol_transform(placed.angle, placed.mirror), placed.position
                )
                record.data["sheet_position"] = _json([iu_to_mm(v) for v in location])
                markers = self.no_connects.get((node.kiid_path, location), [])
                record.data["no_connect_status"] = "explicit_marker" if markers else "not_declared"
                for marker in markers:
                    self.link(record.id, "no_connects", marker, "symbol_pins")
                    self.link(endpoint, "no_connects", marker, "pins")
                if markers and self.document.records[endpoint].links.get("nets"):
                    self.issue(
                        "no_connect_assignment_conflict",
                        "A declared no-connect pin has a board net assignment.",
                        [record.id, endpoint],
                    )
            else:
                record.data["no_connect_status"] = "unresolved_position"
                self.issue(
                    "pin_position_unresolved",
                    "Symbol placement transform is unsupported.",
                    [record.id],
                )
            if invalid:
                self.issue(
                    "alternate_unresolved",
                    "Selected alternate pin function is missing or ambiguous.",
                    [record.id],
                )

    def project(self, project: ProjectFile, board: Board | None) -> None:
        source = self.source("kicad_pro", "project", project)
        for name, value in project.root.entries.items():
            self.add(
                "project_settings",
                name,
                {"section": name, "value": _json(value)},
                NetbomEvidence(source_id=source, locator=name, representation="explicit"),
            )

        if board is not None:
            try:
                assignments = read_netclass_assignments(board, project)
            except KicadParseError as error:
                self.issue("netclass_assignments_unresolved", str(error), [source])
            else:
                for name, classes in assignments.items():
                    if name in self.nets:
                        net = self.document.records[self.nets[name]]
                        net.data["netclasses"] = _json(classes)
                        net.data["netclass_status"] = "saved_and_pattern_assignments"
                        net.evidence.append(
                            NetbomEvidence(
                                source_id=source,
                                locator="net_settings",
                                derivation="calculated",
                                rule="read-netclass-assignments-v1",
                            )
                        )

    def finish(self) -> NetbomDocument:
        for endpoint_id in self.pins.values():
            endpoint = self.document.records[endpoint_id]
            symbol_pins = [
                self.document.records[key] for key in endpoint.links.get("symbol_pins", [])
            ]
            endpoint.data["symbol_association"] = (
                "matched"
                if symbol_pins and endpoint.links.get("pads")
                else "schematic_only"
                if symbol_pins
                else "unresolved"
                if self.schematic_supplied
                else "not_supplied"
            )
            endpoint.data["pin_functions"] = _json(
                sorted(
                    {
                        str(pin.data["effective_name"])
                        for pin in symbol_pins
                        if pin.data.get("effective_name") is not None
                    }
                )
            )
            if not endpoint.links.get("nets") and endpoint.links.get("no_connects"):
                endpoint.data["connection_status"] = "declared_no_connect"
        if self.schematic_supplied:
            for component in list(self.document.records.values()):
                if component.kind != "component" or not component.links.get("board"):
                    continue
                unmatched = [
                    key
                    for key in component.links.get("pins", [])
                    if self.document.records[key].data.get("number")
                    and self.document.records[key].data.get("symbol_association") == "unresolved"
                ]
                if unmatched:
                    self.issue(
                        "board_pin_symbol_unresolved",
                        "Board pin numbers have no resolved schematic pin association.",
                        [component.id, *unmatched],
                    )
                    self.document.coverage["schematic"].status = "partial"
        for record in list(self.document.records.values()):
            if record.kind != "component":
                continue
            endpoints = record.links.get("pins", [])
            symbols = [self.document.records[key] for key in record.links.get("symbols", [])]
            lib_ids = {str(symbol.data.get("lib_id", "")) for symbol in symbols}
            kinds = {
                "Device:R": "resistive_connection",
                "Device:C": "capacitive_connection",
                "Device:L": "inductive_connection",
            }
            if len(lib_ids) == 1 and next(iter(lib_ids)) in kinds and len(endpoints) == 2:
                kind = kinds[next(iter(lib_ids))]
                nets = sorted(
                    {
                        net
                        for pin in endpoints
                        for net in self.document.records[pin].links.get("nets", [])
                    }
                )
                if len(nets) == 2 and all(
                    len(self.document.records[p].links.get("nets", [])) == 1 for p in endpoints
                ):
                    relationship = self.add(
                        "relationship",
                        [record.id, kind],
                        {
                            "relationship_kind": kind,
                            "scope": "Inferred passive connection. Current flow is unknown.",
                        },
                        NetbomEvidence(
                            source_id=symbols[0].evidence[0].source_id,
                            locator="derived",
                            derivation="inferred",
                            rule="generic-passive-two-terminal-v1",
                            inputs=[record.id, *endpoints],
                        ),
                    )
                    self.link(relationship.id, "component", record.id, "relationships")
                    for net in nets:
                        self.link(relationship.id, "nets", net, "relationships")
        groups: dict[str, list[str]] = defaultdict(list)
        for record in list(self.document.records.values()):
            if record.kind == "component" and record.links.get("board"):
                grouping = {
                    name: record.data.get(name)
                    for name in (
                        "fields",
                        "empty_fields",
                        "duplicate_fields",
                        "footprint",
                        "assembly",
                    )
                }
                fields = cast(dict[str, JsonValue], grouping.get("fields") or {}).copy()
                fields.pop("Reference", None)
                grouping["fields"] = fields
                schematic_fields: list[JsonValue] = []
                for key in record.links.get("symbols", []):
                    symbol_record = self.document.records[key]
                    source_fields = cast(
                        dict[str, JsonValue], symbol_record.data.get("fields") or {}
                    ).copy()
                    source_fields.pop("Reference", None)
                    schematic_fields.append(
                        {
                            "unit": symbol_record.data.get("unit"),
                            "fields": source_fields,
                            "empty_fields": symbol_record.data.get("empty_fields"),
                            "duplicate_fields": symbol_record.data.get("duplicate_fields"),
                            "assembly": symbol_record.data.get("assembly"),
                        }
                    )
                grouping["schematic_fields"] = sorted(schematic_fields, key=_digest)
                groups[_digest(grouping)].append(record.id)
        for key, members in groups.items():
            group = self.add(
                "bom_group",
                key,
                {
                    "quantity": len(members),
                    "basis": "Matching board and symbol fields, footprint, and assembly flags.",
                    "purchasing_equivalence": "unknown",
                },
            )
            for member in members:
                self.link(group.id, "components", member, "bom_groups")
        indexes: dict[str, dict[str, list[str]]] = {
            name: {} for name in ("kind", "reference", "net_name", "pin_name", "sheet_name")
        }
        for record in self.document.records.values():
            indexes["kind"].setdefault(record.kind, []).append(record.id)
            for index, field in (
                ("reference", "reference"),
                ("net_name", "name"),
                ("pin_name", "effective_name"),
                ("sheet_name", "name"),
            ):
                if (
                    index == "net_name"
                    and record.kind != "net"
                    or index == "sheet_name"
                    and record.kind != "sheet"
                ):
                    continue
                value = record.data.get(field)
                if isinstance(value, str) and value:
                    indexes[index].setdefault(value, []).append(record.id)
            for key in record.links:
                record.links[key].sort()
            record.fingerprint = _digest(record.model_dump(mode="json", exclude={"fingerprint"}))
        self.document.indexes = {
            name: {key: sorted(values) for key, values in sorted(index.items())}
            for name, index in indexes.items()
        }
        self.document.records = dict(sorted(self.document.records.items()))
        self.document.summary = {kind: len(values) for kind, values in indexes["kind"].items()}
        self.document.revision_id = _id(
            "revision",
            {
                "records": [
                    (key, record.fingerprint) for key, record in self.document.records.items()
                ],
                "coverage": {
                    key: value.model_dump() for key, value in self.document.coverage.items()
                },
                "conventions": self.document.conventions,
            },
        )
        self.document.validate_links()
        return self.document


def build_netbom(
    board: Board | None = None,
    *,
    schematic: Schematic | SheetHierarchy | None = None,
    project: ProjectFile | None = None,
    design_name: str = "design",
    max_records: int = 250000,
) -> NetbomDocument:
    """Build a standalone board-first description from supplied objects without file reads."""
    if board is None and schematic is None and project is None:
        raise ValueError("Supply a board, schematic, hierarchy, or project.")
    builder = _Builder(design_name, max_records)
    if board is not None:
        builder.board(board)
    if isinstance(schematic, Schematic):
        schematic = SheetHierarchy(
            project_root=Path("."),
            roots=[
                SheetNode(
                    path=Path("design.kicad_sch"),
                    schematic=schematic,
                    name=design_name,
                    kiid_path="/" + (schematic.uuid or "root"),
                )
            ],
        )
    if schematic is not None:
        builder.schematic(schematic)
        if project is None:
            project = schematic.project
    if project is not None:
        builder.project(project, board)
    builder.document.coverage = {
        "board_assignments": NetbomCoverage(
            status="complete" if board else "not_supplied",
            details=["Saved pad-to-net assignments. Copper continuity is not evaluated."],
        ),
        "schematic": NetbomCoverage(
            status="partial"
            if schematic
            and (
                schematic.errors
                or any(r.kind == "issue" for r in builder.document.records.values())
            )
            else "complete"
            if schematic
            else "not_supplied",
            details=[
                "Embedded definitions, instance pin selections, fields, hierarchy, and notes.",
                "Geometric schematic connectivity is not evaluated.",
            ],
        ),
        "project_settings": NetbomCoverage(
            status="complete" if project else "not_supplied",
            details=[
                "Stored settings and available net class memberships are retained.",
                "Class priority and final rule constraints are not evaluated.",
            ],
        ),
        "routing": NetbomCoverage(
            status="partial" if board else "not_supplied",
            details=[
                "Placement, pads, stackup, outline, zones, straight lengths, and via counts.",
                "No copper continuity, route completeness, return-path, or zone freshness claim.",
            ],
        ),
        "intent": NetbomCoverage(
            status="partial",
            details=[
                "Existing notes and fields are retained.",
                "Generic passive relationships are inferred.",
                "Operating states, device internals, and undocumented purpose remain unknown.",
            ],
        ),
        "authorship": NetbomCoverage(
            status="not_evaluated",
            details=["Saved files do not establish authorship or distinguish accepted defaults."],
        ),
    }
    capture = _active.get()
    manifest = _manifest(
        capture, capture.source if capture else None, schematic, builder.document.records
    )
    builder.add(
        "source_manifest",
        "extraction",
        cast(dict[str, JsonValue], manifest.model_dump(mode="json")),
    )
    return builder.finish()


def _load_inputs(
    path: str | Path,
) -> tuple[Path, Board | None, SheetHierarchy | None, ProjectFile | None]:
    """Read one bounded native design snapshot for export or inspection."""
    if is_remote_uri(str(path)):
        raise ValueError("Netbom requires a local file path.")
    source = Path(path).resolve()
    if is_remote_uri(str(source)):
        raise ValueError("Netbom requires a local file path.")
    if source.suffix not in (".kicad_pcb", ".kicad_sch", ".kicad_pro"):
        raise ValueError("Netbom requires a board, schematic, or project path.")
    if not source.is_file():
        raise FileNotFoundError(source)
    capture = _active.get()
    if capture is not None:
        capture.source = source
    board_path = source.with_suffix(".kicad_pcb")
    schematic_path = source.with_suffix(".kicad_sch")
    project_path = source.with_suffix(".kicad_pro")
    for candidate in (board_path, schematic_path, project_path):
        if candidate.exists() and (
            candidate.resolve().parent != source.parent
            or candidate.stat().st_size > 64 * 1024 * 1024
        ):
            raise ValueError(
                "Netbom companion file escapes its directory or exceeds the size limit."
            )
    for candidate in (board_path, schematic_path, project_path):
        if not candidate.is_file():
            _record_absent(candidate)
    board = Board.from_file(board_path) if board_path.is_file() else None
    project = ProjectFile.from_file(project_path) if project_path.is_file() else None
    hierarchy = None
    if schematic_path.is_file() or source.suffix == ".kicad_pro":
        try:
            hierarchy = load_hierarchy(
                project_path if project_path.is_file() else schematic_path,
                strict=False,
                max_occurrences=4096,
                max_total_bytes=256 * 1024 * 1024,
            )
        except SheetHierarchyError as error:
            if not str(error).startswith("Top-level sheet file not found:"):
                raise
            hierarchy = SheetHierarchy(project_root=source.parent, errors=[str(error)])
    return source, board, hierarchy, project


def load_netbom(path: str | Path, *, max_records: int = 250000) -> NetbomDocument:
    """Load same-stem design files and confined child sheets, then derive a netbom."""
    with _capture_reads():
        source, board, hierarchy, project = _load_inputs(path)
        return build_netbom(
            board,
            schematic=hierarchy,
            project=project,
            design_name=source.stem,
            max_records=max_records,
        )
