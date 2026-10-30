"""Electrical projections and comparisons built from native schematic preparation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.source_reads import _capture_reads
from eedesigncore.common.text_variables import has_reference
from eedesigncore.project.hierarchy import SheetHierarchy, load_hierarchy
from eedesigncore.project.netclass_patterns import NetclassPattern
from eedesigncore.project.netclasses import _saved_patterns
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.models import (
    SchematicCoverage,
    ViewItem,
)
from eedesigncore.schematic_view.native import _prepare_schematic_view
from eedesigncore.text_resolution import (
    TextResolutionContext,
    context_for_sheet,
    resolve_document_text,
)


class SchematicClassEvidence(KicadModel):
    name: str
    source: Literal["label", "bus_label", "project_pattern", "default"]
    item_id: str | None = None
    pattern: str | None = None


class SchematicElectricalNet(KicadModel):
    id: str
    name: str | None = None
    pin_ids: list[str] = Field(default_factory=list)
    label_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    class_evidence: list[SchematicClassEvidence] = Field(default_factory=list)


class SchematicElectricalLabel(KicadModel):
    id: str
    source_path: str | None
    sheet_path: str
    source_uuid: str
    kind: str
    text: str
    source_text: str
    source_field_values: list[tuple[str, str]]
    shape: str | None
    fields: dict[str, str]
    field_values: list[tuple[str, str]]
    class_fields: list[str]
    net_ids: list[str]
    bus_ids: list[str]


class SchematicElectricalPart(KicadModel):
    id: str
    reference: str | None = None
    lib_id: str
    lib_name: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    unit: int
    body_style: int
    dnp: bool
    in_bom: bool
    on_board: bool
    exclude_from_sim: bool
    variants_json: str


class SchematicElectricalSnapshot(KicadModel):
    schema_version: Literal["schematic-electrical-1"] = "schematic-electrical-1"
    document_id: str
    instance: str
    coverage: SchematicCoverage
    class_coverage: Literal["complete", "partial"]
    class_issues: list[str] = Field(default_factory=list)
    nets: dict[str, SchematicElectricalNet]
    items: dict[str, ViewItem]
    parts: dict[str, SchematicElectricalPart]
    labels: dict[str, SchematicElectricalLabel]
    sheet_files: dict[str, str]
    no_connect_pins: list[str]
    unbound_no_connect_ids: list[str]
    source_hashes: dict[str, str]
    drawing_signature: str


class SchematicElectricalChange(KicadModel):
    category: Literal[
        "connectivity", "net_names", "parts", "pins", "no_connect", "classes", "labels", "drawing"
    ]
    identity: str
    before_json: str | None = None
    after_json: str | None = None


class SchematicElectricalComparison(KicadModel):
    changes: list[SchematicElectricalChange]
    coverage_complete: bool
    equivalent: bool | None
    before_coverage: SchematicCoverage
    after_coverage: SchematicCoverage
    class_coverage_complete: bool


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def inspect_schematic_electrical(
    source: str | Path | Schematic | SheetHierarchy,
    *,
    document_id: str,
    instance: str = "design",
    strict: bool = True,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    drawing: bool = True,
) -> SchematicElectricalSnapshot:
    """Resolve current schematic facts without reading saved board assignments.

    Source hashes exist only for files read during this call. In-memory inputs
    have no claimed byte baseline. Class names are memberships, not effective
    clearance settings. Rule-area class attachment remains explicitly partial.

    With *drawing* false no sheet is rendered (see ``prepare_schematic_view``).
    Nets, parts, pins and labels are the same; items carry no drawn bounds and
    the drawing coverage reads ``unavailable``. The board and schematic update
    planners pass false.
    """
    hashes: dict[str, str] = {}
    if isinstance(source, str | Path):
        with _capture_reads() as capture:
            source = load_hierarchy(
                source, strict=strict, max_occurrences=4096, max_total_bytes=256 * 1024 * 1024
            )
        hashes = {str(path): evidence[0] for path, evidence in capture.reads.items()}
    resolved_sheets: dict[str, Schematic] = {}
    view = _prepare_schematic_view(
        source,
        document_id=document_id,
        instance=instance,
        strict=strict,
        font=font,
        text_context=text_context,
        drawing=drawing,
        resolved_sheets=resolved_sheets,
    )
    sheets = (
        [(node.kiid_path, node.schematic) for node in source.walk()]
        if isinstance(source, SheetHierarchy)
        else [("/" + (source.uuid or "root"), source)]
    )
    items = {
        key: item.model_copy(deep=True)
        for page in view.pages.values()
        for key, item in page.items.items()
    }
    lookup: dict[tuple[str | None, str | None, str], ViewItem] = {
        (item.source.sheet_path, item.source.source_uuid, item.kind): item
        for item in items.values()
        if item.source and item.source.source_uuid
    }
    nets = {
        key: SchematicElectricalNet(
            id=key,
            name=net.name,
            item_ids=net.item_ids[:],
            pin_ids=sorted(item_id for item_id in net.item_ids if items[item_id].kind == "pin"),
            label_ids=sorted(
                item_id
                for item_id in net.item_ids
                if items[item_id].kind in {"label", "sheet_pin"}
            ),
        )
        for key, net in view.nets.items()
    }
    sheet_files = (
        {node.kiid_path: str(node.path) for node in source.walk()}
        if isinstance(source, SheetHierarchy)
        else {}
    )
    labels: dict[str, SchematicElectricalLabel] = {}
    parts: dict[str, SchematicElectricalPart] = {}
    class_issues: list[str] = []
    geometries: dict[str, str] = {}
    for path, sheet in sheets:
        context = (
            context_for_sheet(source, path, context=text_context)
            if isinstance(source, SheetHierarchy)
            else text_context or TextResolutionContext(instance_path=path)
        )
        resolved = resolved_sheets.get(path) or resolve_document_text(sheet, context).document
        if sheet.rule_areas:
            class_issues.append(f"Rule-area class attachment is not calculated: {path}")
        for symbol in resolved.symbols:
            item = lookup.get((path, symbol.uuid, "symbol"))
            if item is None:
                class_issues.append(f"Symbol without stable identity: {path}")
                continue
            parts[item.id] = SchematicElectricalPart(
                id=item.id,
                reference=item.reference,
                lib_id=symbol.lib_id,
                lib_name=symbol.lib_name,
                fields={field.name: field.value for field in symbol.fields},
                unit=symbol.unit,
                body_style=symbol.body_style,
                dnp=symbol.dnp,
                in_bom=symbol.in_bom,
                on_board=symbol.on_board,
                exclude_from_sim=symbol.exclude_from_sim,
                variants_json=_json([entry.model_dump(mode="json") for entry in symbol.instances]),
            )
        for label_index, label in enumerate(resolved.labels):
            item = lookup.get((path, label.uuid, "label"))
            class_fields = [
                field.value
                for field in label.fields
                if field.name in {"Netclass", "Net Class"} and field.value
            ]
            if item is None:
                if class_fields:
                    class_issues.append(f"Class label without stable identity: {path}")
                continue
            labels[item.id] = SchematicElectricalLabel(
                id=item.id,
                source_path=sheet_files.get(path),
                sheet_path=path,
                source_uuid=label.uuid or "",
                kind=label.kind.value,
                text=label.content,
                source_text=sheet.labels[label_index].content,
                source_field_values=[
                    (field.name, field.value) for field in sheet.labels[label_index].fields
                ],
                shape=label.shape,
                fields={field.name: field.value for field in label.fields},
                field_values=[(field.name, field.value) for field in label.fields],
                class_fields=class_fields,
                net_ids=item.net_ids[:],
                bus_ids=item.bus_ids[:],
            )
            targets = set(item.net_ids)
            bus_targets = {net for bus in item.bus_ids for net in view.buses[bus].net_ids}
            if class_fields and not targets and not bus_targets:
                class_issues.append(f"Class label has no resolved target: {item.id}")
            for name in class_fields:
                if has_reference(name):
                    class_issues.append(f"Unresolved class text: {item.id}")
                    continue
                for key in sorted(targets | bus_targets):
                    nets[key].class_evidence.append(
                        SchematicClassEvidence(
                            name=name,
                            source="bus_label" if key in bus_targets else "label",
                            item_id=item.id,
                        )
                    )
        # Keep graphical differences separate from electrical facts.
        geometry = sheet.model_dump(mode="json")
        for symbol_data in geometry["symbols"]:
            for field in symbol_data["fields"]:
                field.pop("value", None)
            for key in (
                "lib_id",
                "lib_name",
                "dnp",
                "in_bom",
                "on_board",
                "exclude_from_sim",
                "instances",
                "pins",
            ):
                symbol_data.pop(key, None)
        for label_data in geometry["labels"]:
            label_data.pop("content", None)
            for field in label_data["fields"]:
                field.pop("value", None)
        geometry.pop("lib_symbols", None)
        geometries[path] = _json(geometry)
    project = source.project if isinstance(source, SheetHierarchy) else None
    patterns: list[tuple[NetclassPattern, str, str]] = []
    if project is None:
        class_issues.append("Project patterns are unavailable for this input.")
    else:
        settings = project.section("net_settings")
        if settings is None or "netclass_patterns" not in settings:
            class_issues.append("Project net class patterns are absent.")
        else:
            patterns = [
                (NetclassPattern(pattern), name, pattern)
                for pattern, name in _saved_patterns(project)
            ]
    for net in nets.values():
        for matcher, name, pattern in patterns:
            if net.name and matcher.matches(net.name):
                net.class_evidence.append(
                    SchematicClassEvidence(name=name, source="project_pattern", pattern=pattern)
                )
        if not net.class_evidence:
            net.class_evidence.append(SchematicClassEvidence(name="Default", source="default"))
        net.class_names = sorted({evidence.name for evidence in net.class_evidence})
    no_connect_pins: set[str] = set()
    unbound: list[str] = []
    pin_contacts: dict[tuple[str | None, int, int], list[str]] = {}
    for pin in items.values():
        if pin.kind == "pin" and pin.source and pin.position is not None:
            contact = (
                pin.source.sheet_path,
                round(pin.position[0] * 1e6),
                round(pin.position[1] * 1e6),
            )
            pin_contacts.setdefault(contact, []).append(pin.id)
    for mark in items.values():
        if mark.kind != "no_connect":
            continue
        attached: list[str] = []
        if mark.source and mark.position is not None:
            attached = pin_contacts.get(
                (
                    mark.source.sheet_path,
                    round(mark.position[0] * 1e6),
                    round(mark.position[1] * 1e6),
                ),
                [],
            )
        no_connect_pins.update(attached)
        if not attached:
            unbound.append(mark.id)
    coverage = view.coverage.model_copy(deep=True)
    if any(issue.scope in {"identity", "hierarchy"} for issue in coverage.issues):
        coverage.electrical = (
            "partial" if coverage.electrical == "complete" else coverage.electrical
        )
    for path, digest in hashes.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError("A schematic source changed during inspection.")
    return SchematicElectricalSnapshot(
        document_id=document_id,
        instance=instance,
        coverage=coverage,
        class_coverage="complete"
        if not class_issues and coverage.electrical == "complete"
        else "partial",
        class_issues=sorted(set(class_issues)),
        nets=nets,
        items=items,
        parts=parts,
        labels=labels,
        sheet_files=sheet_files,
        no_connect_pins=sorted(no_connect_pins),
        unbound_no_connect_ids=sorted(unbound),
        source_hashes=hashes,
        drawing_signature=hashlib.sha256(_json(geometries).encode()).hexdigest(),
    )


def compare_schematic_electrical(
    before: SchematicElectricalSnapshot, after: SchematicElectricalSnapshot
) -> SchematicElectricalComparison:
    """Compare pin groups independently of wire segmentation or net identifiers."""
    if (before.document_id, before.instance) != (after.document_id, after.instance):
        raise ValueError(
            "Electrical comparisons require the same document and instance identifiers."
        )
    changes: list[SchematicElectricalChange] = []

    def change(
        category: Literal[
            "connectivity",
            "net_names",
            "parts",
            "pins",
            "no_connect",
            "classes",
            "labels",
            "drawing",
        ],
        identity: str,
        old: object,
        new: object,
    ) -> None:
        if old != new:
            changes.append(
                SchematicElectricalChange(
                    category=category,
                    identity=identity,
                    before_json=_json(old),
                    after_json=_json(new),
                )
            )

    def groups(snapshot: SchematicElectricalSnapshot) -> dict[str, list[SchematicElectricalNet]]:
        result: dict[str, list[SchematicElectricalNet]] = {}
        for net in snapshot.nets.values():
            key = _json(net.pin_ids) if net.pin_ids else _json(["pinless", net.name])
            result.setdefault(key, []).append(net)
        return result

    left, right = groups(before), groups(after)
    change(
        "connectivity",
        "pin_groups",
        sorted(key for key in left if not key.startswith('["pinless"')),
        sorted(key for key in right if not key.startswith('["pinless"')),
    )
    for key in sorted(left.keys() | right.keys()):
        change(
            "net_names",
            key,
            sorted(_json(net.name) for net in left.get(key, [])),
            sorted(_json(net.name) for net in right.get(key, [])),
        )
        change(
            "classes",
            key,
            sorted({name for net in left.get(key, []) for name in net.class_names}),
            sorted({name for net in right.get(key, []) for name in net.class_names}),
        )
    for key in sorted(before.parts.keys() | after.parts.keys()):
        change(
            "parts",
            key,
            before.parts[key].model_dump(mode="json") if key in before.parts else None,
            after.parts[key].model_dump(mode="json") if key in after.parts else None,
        )
    for kind in ("pin", "label", "sheet_pin"):

        def facts(
            snapshot: SchematicElectricalSnapshot, item_kind: str = kind
        ) -> dict[str, tuple[str | None, ...]]:
            return {
                key: (item.pin_number, item.pin_name, item.electrical_type, item.label)
                for key, item in snapshot.items.items()
                if item.kind == item_kind
            }

        old_items, new_items = facts(before), facts(after)
        for key in sorted(old_items.keys() | new_items.keys()):
            change(
                "pins" if kind == "pin" else "labels", key, old_items.get(key), new_items.get(key)
            )
    for key in sorted(before.labels.keys() | after.labels.keys()):
        old_label, new_label = before.labels.get(key), after.labels.get(key)
        change(
            "labels",
            key + ":shape",
            old_label.shape if old_label else None,
            new_label.shape if new_label else None,
        )
    change("no_connect", "attached_pins", before.no_connect_pins, after.no_connect_pins)
    change(
        "no_connect", "unbound_marks", before.unbound_no_connect_ids, after.unbound_no_connect_ids
    )
    change("drawing", "drawing_signature", before.drawing_signature, after.drawing_signature)
    complete = before.coverage.electrical == after.coverage.electrical == "complete"
    class_complete = before.class_coverage == after.class_coverage == "complete"
    differences = any(change.category != "drawing" for change in changes)
    return SchematicElectricalComparison(
        changes=changes,
        coverage_complete=complete,
        class_coverage_complete=class_complete,
        equivalent=False if differences else True if complete and class_complete else None,
        before_coverage=before.coverage,
        after_coverage=after.coverage,
    )
