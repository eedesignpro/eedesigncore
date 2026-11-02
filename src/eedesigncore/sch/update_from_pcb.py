"""Preview and apply schematic changes from associated PCB footprints."""

from __future__ import annotations

from uuid import uuid4

from eedesigncore.pcb.models import Board
from eedesigncore.project._update_common import (
    field_value,
    footprint_path,
    issue,
    match_footprints,
    part_groups,
    placed_symbols,
    set_field,
    signature,
    sources,
    symbol_path,
    validate_inputs,
)
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import (
    DesignUpdateChange,
    DesignUpdateIssue,
    SchematicUpdateOptions,
    SchematicUpdatePlan,
    SchematicUpdateResult,
)
from eedesigncore.sch._update_net_names import update_net_names
from eedesigncore.sch._update_pin_swaps import swap_pins
from eedesigncore.sch._update_unit_swaps import swap_units
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.inspection import (
    inspect_schematic_electrical,
)


def _run(
    board: Board, source: Schematic | SheetHierarchy, options: SchematicUpdateOptions, seed: str
) -> SchematicUpdateResult:
    updated_board = board.model_copy(deep=True)
    issues: list[DesignUpdateIssue] = []
    changes: list[DesignUpdateChange] = []
    validate_inputs(board, source, issues)
    if issues:
        return SchematicUpdateResult(
            board=updated_board,
            documents={
                document: sheet.model_copy(deep=True) for _, document, sheet in sources(source)
            },
            changes=changes,
            issues=issues,
        )
    inspection = inspect_schematic_electrical(
        source, document_id="schematic-update", drawing=False
    )
    if options.update_net_names and inspection.coverage.electrical != "complete":
        issue(
            issues,
            "electrical_coverage",
            "Net updates require complete schematic electrical coverage.",
        )
    groups = part_groups(inspection, issues, source)
    matched = match_footprints(
        board.footprints, groups, inspection, options.match_by_reference, issues, source
    )
    documents = {
        document: schematic.model_copy(deep=True) for _, document, schematic in sources(source)
    }
    originals = placed_symbols(source)
    skipped = (
        swap_units(source, inspection, groups, matched, documents, changes, issues)
        if options.update_net_names and options.prefer_unit_swaps
        else set()
    )
    swapped_pins = (
        swap_pins(source, inspection, groups, matched, documents, skipped, changes, issues)
        if options.update_net_names
        and options.prefer_pin_swaps
        and options.allow_unconstrained_pin_swaps
        else set()
    )
    demands: dict[tuple[str, str, str], str | None] = {}
    used_references: dict[str, str] = {
        reference.casefold(): reference for reference in groups if reference not in matched
    }
    for reference, ids in groups.items():
        fp = matched.get(reference)
        if fp is None:
            issue(
                issues,
                "unmatched_symbol",
                "No PCB footprint matches the symbol. The symbol is retained.",
                reference,
                warning=True,
            )
            continue
        new_reference = (
            field_value(fp.properties, "Reference")
            if options.update_references and not options.match_by_reference
            else reference
        )
        if not new_reference or "?" in new_reference:
            issue(
                issues,
                "invalid_reference",
                "The footprint reference is empty or unannotated.",
                fp.uuid or "",
            )
        if (
            new_reference.casefold() in used_references
            and used_references[new_reference.casefold()] != reference
        ):
            issue(
                issues,
                "duplicate_reference",
                "Different physical symbols would receive the same reference.",
                new_reference,
            )
        used_references[new_reference.casefold()] = reference
        for part_id in ids:
            path = symbol_path(inspection, part_id)
            document_id, original = originals[path]
            symbol = next(
                symbol for symbol in documents[document_id].symbols if symbol.uuid == original.uuid
            )
            before = symbol.model_dump_json()
            sheet_path = path.rsplit("/", 1)[0]
            updates: dict[str, str | None] = {}
            if options.update_footprints and path not in skipped:
                updates["Footprint"] = fp.fp_id
            if options.update_values and path not in skipped:
                updates["Value"] = field_value(fp.properties, "Value")
            if options.update_other_fields:
                updates.update(
                    {
                        field.name: field.value
                        for field in fp.properties
                        if field.name not in {"Reference", "Value", "Footprint"}
                    }
                )
                mandatory = {"Reference", "Value", "Footprint", "Datasheet", "Description"}
                for field in original.fields:
                    if field.name not in mandatory and field.name not in updates:
                        updates[field.name] = None
            for name, value in updates.items():
                if "${" in field_value(original.fields, name):
                    continue
                key = (document_id, original.uuid or "", name)
                if key in demands and demands[key] != value:
                    issue(
                        issues,
                        "shared_field_conflict",
                        "Repeated sheet occurrences require different values "
                        "for one symbol field.",
                        name,
                    )
                demands[key] = value
                if value is None:
                    symbol.fields = [field for field in symbol.fields if field.name != name]
                else:
                    set_field(symbol.fields, name, value, position=symbol.position)
            if options.update_attributes and path not in skipped:
                for name, flag in (
                    ("dnp", fp.attributes.dnp),
                    ("in_bom", not fp.attributes.exclude_from_bom),
                ):
                    key = (document_id, original.uuid or "", "@" + name)
                    if key in demands and demands[key] != str(flag):
                        issue(
                            issues,
                            "shared_attribute_conflict",
                            "Repeated sheet occurrences require different population attributes.",
                            original.uuid or "",
                        )
                    demands[key] = str(flag)
                    setattr(symbol, name, flag)
            if options.match_by_reference:
                target_fp = next(item for item in updated_board.footprints if item.uuid == fp.uuid)
                target_fp.path = footprint_path(source, inspection, ids, target_fp.path)[0]
            if (
                options.update_references
                and path not in skipped
                and not options.match_by_reference
                and "${" not in field_value(original.fields, "Reference")
            ):
                for project in symbol.instances:
                    for instance in project.paths:
                        if instance.path.rstrip("/") == sheet_path:
                            instance.reference = new_reference
                set_field(symbol.fields, "Reference", new_reference, position=symbol.position)
            if before != symbol.model_dump_json():
                changes.append(
                    DesignUpdateChange(
                        kind="update_symbol",
                        reference=reference,
                        identity=path,
                        before=before,
                        after=symbol.model_dump_json(),
                    )
                )
    if options.update_net_names:
        update_net_names(
            source,
            inspection,
            matched,
            documents,
            seed,
            changes,
            issues,
            skipped=skipped,
            skipped_pins=swapped_pins,
        )
    return SchematicUpdateResult(
        board=updated_board, documents=documents, changes=changes, issues=issues
    )


def plan_update_schematic_from_pcb(
    board: Board,
    source: Schematic | SheetHierarchy,
    *,
    options: SchematicUpdateOptions | None = None,
) -> SchematicUpdatePlan:
    """Preview changes to existing symbols. No schematic symbols are added or deleted."""
    settings = (options or SchematicUpdateOptions()).model_copy(deep=True)
    seed = str(uuid4())
    result = _run(board, source, settings, seed)
    return SchematicUpdatePlan(
        source_signature=signature(source),
        board_signature=signature(board),
        options=settings,
        uuid_seed=seed,
        changes=result.changes,
        issues=result.issues,
        result_board_signature=signature(result.board),
        result_signatures={key: signature(value) for key, value in result.documents.items()},
    )


def apply_update_schematic_from_pcb(
    board: Board, source: Schematic | SheetHierarchy, plan: SchematicUpdatePlan
) -> SchematicUpdateResult:
    """Recheck the preview and return detached schematics without saving files."""
    if signature(board) != plan.board_signature or signature(source) != plan.source_signature:
        raise ValueError("The board or schematic changed. Create a new preview.")
    result = _run(board, source, plan.options, plan.uuid_seed)
    if (
        signature(result.board) != plan.result_board_signature
        or result.changes != plan.changes
        or result.issues != plan.issues
        or {key: signature(value) for key, value in result.documents.items()}
        != plan.result_signatures
    ):
        raise ValueError("The update preview changed. Create a new preview.")
    if plan.blockers:
        raise ValueError(
            "The schematic update has blockers: "
            + "; ".join(item.message for item in plan.blockers)
        )
    return result


def update_schematic_from_pcb(
    board: Board,
    source: Schematic | SheetHierarchy,
    *,
    options: SchematicUpdateOptions | None = None,
) -> SchematicUpdateResult:
    """Plan and apply a schematic update in memory. Reject unresolved changes."""
    plan = plan_update_schematic_from_pcb(board, source, options=options)
    return apply_update_schematic_from_pcb(board, source, plan)
