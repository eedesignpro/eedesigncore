"""Identity and validation shared by both update directions."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256

from pydantic import BaseModel

from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.connectivity.geometry import instance_reference
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import DesignUpdateIssue
from eedesigncore.sch.models import PlacedSymbol, Schematic
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def signature(value: BaseModel) -> str:
    if isinstance(value, SheetHierarchy):
        encoded = value.model_dump_json(exclude={"project"})
        if value.project is not None:
            encoded += value.project.to_string()
        return sha256(encoded.encode()).hexdigest()
    return sha256(value.model_dump_json().encode()).hexdigest()


def validate_inputs(
    board: Board, source: Schematic | SheetHierarchy, issues: list[DesignUpdateIssue]
) -> None:
    if any(fp.uuid is None for fp in board.footprints):
        issue(issues, "missing_footprint_uuid", "Every board footprint needs a UUID.")
    if any(count > 1 for count in Counter(fp.uuid for fp in board.footprints).values()):
        issue(issues, "duplicate_footprint_uuid", "Board footprint UUIDs must be unique.")
    if board.variants or any(fp.variants for fp in board.footprints):
        issue(
            issues,
            "assembly_variants",
            "Assembly variant updates need explicit variant selection.",
        )
    for path, document, sheet in sources(source):
        if any(
            instance.unit != symbol.unit
            for symbol in sheet.symbols
            for project in symbol.instances
            for instance in project.paths
            if instance.path.rstrip("/") == path
        ):
            issue(
                issues,
                "instance_unit_geometry",
                "The saved occurrence unit differs from the placed symbol unit.",
                document,
            )
        if any(
            isinstance(item, Pin) and item.number.startswith("[") and item.number.endswith("]")
            for definition in sheet.lib_symbols
            for unit in definition.units
            for item in unit.items
        ):
            issue(
                issues,
                "stacked_pin_numbers",
                "Design updates require expanded stacked pin numbers.",
                document,
            )
        if sheet.rule_areas:
            issue(
                issues,
                "rule_area_metadata",
                "Design updates do not support rule-area population "
                "or component-class inheritance.",
                document,
            )
        if any(symbol.uuid is None for symbol in sheet.symbols) or any(
            count > 1 for count in Counter(symbol.uuid for symbol in sheet.symbols).values()
        ):
            issue(
                issues,
                "symbol_identity",
                "Placed symbols need unique UUIDs within each sheet.",
                document,
            )
        if any(
            instance.variants
            for symbol in sheet.symbols
            for project in symbol.instances
            for instance in project.paths
        ):
            issue(
                issues,
                "assembly_variants",
                "Schematic assembly variants need explicit variant selection.",
                document,
            )


def sources(source: Schematic | SheetHierarchy) -> list[tuple[str, str, Schematic]]:
    if isinstance(source, Schematic):
        return [("/" + (source.uuid or "root"), "schematic", source)]
    return [(node.kiid_path, str(node.path), node.schematic) for node in source.walk()]


def field_value(fields: list[Property], name: str) -> str:
    return next((field.value for field in fields if field.name == name), "")


def set_field(
    fields: list[Property],
    name: str,
    value: str,
    *,
    position: tuple[float, float] = (0, 0),
    layer: str | None = None,
) -> None:
    existing = [field for field in fields if field.name == name]
    if len(existing) > 1:
        raise ValueError(f"Duplicate field: {name}")
    if existing:
        existing[0].value = value
    else:
        fields.append(
            Property(
                name=name,
                value=value,
                position=position,
                layer=layer,
                visible=False,
                attributes=TextAttributes(visible=False),
            )
        )


def issue(
    issues: list[DesignUpdateIssue],
    code: str,
    message: str,
    identity: str = "",
    *,
    warning: bool = False,
) -> None:
    issues.append(
        DesignUpdateIssue(
            code=code,
            message=message,
            identity=identity,
            severity="warning" if warning else "error",
        )
    )


def part_groups(
    inspection: SchematicElectricalSnapshot,
    issues: list[DesignUpdateIssue],
    source: Schematic | SheetHierarchy,
) -> dict[str, list[str]]:
    raw = placed_symbols(source)
    groups: dict[str, list[str]] = defaultdict(list)
    for part in inspection.parts.values():
        path = symbol_path(inspection, part.id)
        part.reference = instance_reference(raw[path][1], path.rsplit("/", 1)[0])
        if not part.on_board:
            continue
        if not part.reference or "?" in part.reference:
            issue(
                issues, "unannotated", "Annotate the schematic before updating the board.", part.id
            )
            continue
        groups[part.reference].append(part.id)
    for reference, ids in groups.items():
        parts = [inspection.parts[key] for key in ids]
        if (
            len({part.unit for part in parts}) != len(parts)
            or len({part.lib_id for part in parts}) != 1
        ):
            issue(
                issues,
                "ambiguous_reference",
                "The reference does not identify distinct units of one symbol.",
                reference,
            )
        if (
            len(
                {
                    (
                        part.fields.get("Footprint", ""),
                        part.fields.get("Value", ""),
                        part.dnp,
                        part.in_bom,
                    )
                    for part in parts
                }
            )
            != 1
        ):
            issue(
                issues,
                "unit_conflict",
                "Symbol units disagree on footprint, value, or population attributes.",
                reference,
            )
    return dict(groups)


def symbol_path(inspection: SchematicElectricalSnapshot, part_id: str) -> str:
    item = inspection.items[part_id]
    if item.source is None or not item.source.sheet_path or not item.source.source_uuid:
        raise ValueError(f"Symbol occurrence has no complete path: {part_id}")
    return item.source.sheet_path.rstrip("/") + "/" + item.source.source_uuid


def match_footprints(
    footprints: list[Footprint],
    groups: dict[str, list[str]],
    inspection: SchematicElectricalSnapshot,
    by_reference: bool,
    issues: list[DesignUpdateIssue],
) -> dict[str, Footprint]:
    matched: dict[str, Footprint] = {}
    used: set[int] = set()
    for reference, ids in groups.items():
        paths = {symbol_path(inspection, key) for key in ids}
        candidates = [
            fp
            for fp in footprints
            if not fp.attributes.board_only
            and (
                field_value(fp.properties, "Reference").casefold() == reference.casefold()
                if by_reference
                else fp.path in paths
            )
        ]
        if len(candidates) > 1:
            issue(
                issues, "ambiguous_match", "More than one footprint matches the symbol.", reference
            )
        elif candidates:
            fp = candidates[0]
            if id(fp) in used:
                issue(
                    issues,
                    "duplicate_match",
                    "A footprint matches more than one physical symbol.",
                    reference,
                )
            used.add(id(fp))
            matched[reference] = fp
    return matched


def placed_symbols(source: Schematic | SheetHierarchy) -> dict[str, tuple[str, PlacedSymbol]]:
    return {
        path.rstrip("/") + "/" + (symbol.uuid or ""): (document, symbol)
        for path, document, schematic in sources(source)
        for symbol in schematic.symbols
    }
