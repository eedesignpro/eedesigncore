"""Plan and apply library definition updates without changing input schematics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from hashlib import sha256
from uuid import UUID, uuid4

from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.sch._library_update_fields import flatten_definition, update_fields
from eedesigncore.sch._library_update_pins import pin_changes, unit_count, update_pins
from eedesigncore.sch.autoplace import autoplace_fields
from eedesigncore.sch.library_update_models import (
    SymbolLibraryChange,
    SymbolLibraryFieldChange,
    SymbolLibraryUpdateIssue,
    SymbolLibraryUpdateOptions,
    SymbolLibraryUpdatePlan,
    SymbolLibraryUpdateResult,
    SymbolLibraryUpdateTarget,
)
from eedesigncore.sch.models import PlacedSymbol, Schematic
from eedesigncore.sch.operations import _register_lib_def


def _signature(value: Schematic | Symbol) -> str:
    return sha256(value.model_dump_json().encode("utf-8")).hexdigest()


def select_symbol_library_targets(
    documents: Mapping[str, Schematic],
    *,
    symbol_uuids: Sequence[str] | None = None,
    reference_pattern: str | None = None,
    value_pattern: str | None = None,
    lib_id: str | None = None,
    new_lib_id: str | None = None,
) -> list[SymbolLibraryUpdateTarget]:
    """Select physical symbols once, retaining matching sheet paths for reference resets.

    With no filters, select all symbols. Multiple filters intersect. Patterns use
    case-insensitive shell wildcards. Selection never expands to sibling units.
    """
    targets: list[SymbolLibraryUpdateTarget] = []
    for document_id, schematic in documents.items():
        for symbol in schematic.symbols:
            if not symbol.uuid or (symbol_uuids is not None and symbol.uuid not in symbol_uuids):
                continue
            if lib_id is not None and symbol.lib_id != lib_id:
                continue
            fields = {field.name: field.value for field in symbol.fields}
            if value_pattern is not None and not fnmatchcase(
                fields.get("Value", "").casefold(), value_pattern.casefold()
            ):
                continue
            paths: list[str] | None = None
            if reference_pattern is not None:
                paths = sorted(
                    {
                        path.path
                        for project in symbol.instances
                        for path in project.paths
                        if fnmatchcase(path.reference.casefold(), reference_pattern.casefold())
                    }
                )
                if not paths:
                    if symbol.instances or not fnmatchcase(
                        fields.get("Reference", "").casefold(), reference_pattern.casefold()
                    ):
                        continue
                    paths = None
            targets.append(
                SymbolLibraryUpdateTarget(
                    document_id=document_id,
                    symbol_uuid=symbol.uuid,
                    new_lib_id=new_lib_id,
                    instance_paths=paths,
                )
            )
    return targets


def _valid_lib_id(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2 or not all(parts):
        return False
    nickname, item = parts
    return not any(ord(char) < 32 or char == chr(92) for char in nickname) and not any(
        char in item for char in (":", "\t", "\n", "\r", chr(92), "<", ">", chr(34))
    )


def _run(
    documents: Mapping[str, Schematic],
    library_symbols: Mapping[str, Symbol],
    targets: Sequence[SymbolLibraryUpdateTarget],
    options: SymbolLibraryUpdateOptions,
    seed: str,
) -> SymbolLibraryUpdateResult:
    UUID(seed)
    copies = {key: document.model_copy(deep=True) for key, document in documents.items()}
    issues: list[SymbolLibraryUpdateIssue] = []
    changes: list[SymbolLibraryChange] = []
    prepared: list[tuple[SymbolLibraryUpdateTarget, PlacedSymbol, Symbol, Symbol]] = []
    seen: set[tuple[str, str]] = set()

    def issue(
        target: SymbolLibraryUpdateTarget, code: str, message: str, *, error: bool = False
    ) -> None:
        issues.append(
            SymbolLibraryUpdateIssue(
                code=code,
                severity="error" if error else "warning",
                message=message,
                document_id=target.document_id,
                symbol_uuid=target.symbol_uuid,
            )
        )

    for target in targets:
        identity = (target.document_id, target.symbol_uuid)
        if identity in seen:
            raise ValueError("Select each physical symbol only once.")
        seen.add(identity)
        document = copies.get(target.document_id)
        matches = (
            []
            if document is None
            else [symbol for symbol in document.symbols if symbol.uuid == target.symbol_uuid]
        )
        if len(matches) != 1 or document is None:
            issue(
                target,
                "target_not_unique",
                "The target must identify one symbol in one document.",
                error=True,
            )
            continue
        placed = matches[0]
        new_id = target.new_lib_id if target.new_lib_id is not None else placed.lib_id
        if not _valid_lib_id(new_id):
            issue(
                target,
                "invalid_library_id",
                "Supply a qualified library identifier with a nickname and item name.",
                error=True,
            )
            continue
        source = library_symbols.get(new_id)
        old_matches = [
            definition
            for definition in document.lib_symbols
            if definition.name == (placed.lib_name or placed.lib_id)
        ]
        if source is None or len(old_matches) != 1:
            issue(
                target,
                "missing_definition",
                "Supply the library definition and one matching embedded definition.",
                error=True,
            )
            continue
        known_paths = {path.path for project in placed.instances for path in project.paths}
        if target.instance_paths is not None and (
            not target.instance_paths or not set(target.instance_paths) <= known_paths
        ):
            issue(
                target,
                "invalid_instance_paths",
                "Selected instance paths must exist on the placed symbol.",
                error=True,
            )
            continue
        if (
            target.display_instance_path is not None
            and target.display_instance_path not in known_paths
        ):
            issue(
                target,
                "invalid_display_path",
                "The displayed sheet path must exist on the placed symbol.",
                error=True,
            )
            continue
        try:
            incoming = flatten_definition(source, library_symbols, new_id)
            old = flatten_definition(
                old_matches[0],
                {entry.name: entry for entry in document.lib_symbols},
                old_matches[0].name,
            )
        except ValueError as exc:
            issue(target, "invalid_inheritance", str(exc), error=True)
            continue
        if placed.unit < 1 or unit_count(incoming) < placed.unit:
            issue(
                target,
                "unavailable_unit",
                "The replacement does not contain the placed unit. The symbol remains unchanged.",
                error=True,
            )
            continue
        if not options.reset_pin_name_visibility:
            incoming.show_pin_names = old.show_pin_names
        if not options.reset_pin_number_visibility:
            incoming.show_pin_numbers = old.show_pin_numbers
        prepared.append((target, placed, old, incoming))

    removed: dict[str, list[str]] = {}
    for document_id, document in copies.items():
        selected = {
            target.symbol_uuid for target, _, _, _ in prepared if target.document_id == document_id
        }
        affected = {
            symbol.lib_name or symbol.lib_id
            for symbol in document.symbols
            if symbol.uuid in selected
        }
        remaining = {
            symbol.lib_name or symbol.lib_id
            for symbol in document.symbols
            if symbol.uuid not in selected
        }
        removed[document_id] = sorted(affected - remaining)
        document.lib_symbols = [
            entry for entry in document.lib_symbols if entry.name not in affected - remaining
        ]

    for target, placed, old, incoming in prepared:
        before = placed.model_copy(deep=True)
        all_paths = {path.path for project in placed.instances for path in project.paths}
        if target.instance_paths is not None and all_paths - set(target.instance_paths):
            issue(
                target,
                "shared_sheet_instances",
                "The shared sheet changes for all instances. Reference reset uses selected paths.",
            )
        references = update_fields(
            placed, incoming, options, target.instance_paths, target.display_instance_path
        )
        for attribute, reset in options.attributes.model_dump().items():
            if reset:
                setattr(placed, attribute, getattr(incoming, attribute))
        differences = pin_changes(old, incoming, placed)
        if differences:
            issue(
                target,
                "pin_mismatch",
                "Pins moved, appeared, or disappeared. Wires and markers remain unchanged.",
            )
        if unit_count(old) != unit_count(incoming):
            issue(
                target,
                "unit_count_changed",
                "The unit count changed. Other placed units are not selected automatically.",
            )
        if any(
            path.unit > unit_count(incoming) or path.unit < 1
            for project in placed.instances
            for path in project.paths
        ):
            issue(
                target,
                "instance_unit_mismatch",
                "A saved sheet instance selects a unit absent from the replacement.",
            )
        ambiguous = update_pins(
            placed,
            old,
            incoming,
            reset_alternates=options.reset_alternate_pins,
            instance_paths=target.instance_paths,
            seed=seed,
            identity=target.document_id + ":" + target.symbol_uuid,
        )
        if ambiguous:
            issue(
                target,
                "pin_identity_tie",
                "File order resolves pin ties. KiCad memory order can give different UUIDs.",
            )
        if any(pin.uuid not in {old_pin.uuid for old_pin in before.pins} for pin in placed.pins):
            issue(
                target,
                "new_pin_identity",
                "New pins receive new UUIDs. A separate KiCad operation generates different ones.",
            )
        resets_positions = options.fields.reset_positions or any(
            policy.reset_positions for policy in options.fields.overrides.values()
        )
        if resets_positions and before.fields_autoplaced:
            if options.field_placement == "isolated":
                autoplace_fields(placed, definition=incoming)
                issue(
                    target,
                    "isolated_field_placement",
                    "Fields use isolated placement. KiCad can also use surrounding items.",
                )
            else:
                issue(
                    target,
                    "native_autoplace_skipped",
                    "Fields use library positions. KiCad can also use surrounding items.",
                )
        document = copies[target.document_id]
        cache_before = {entry.name for entry in document.lib_symbols}
        placed.lib_id = incoming.name
        key = _register_lib_def(document, incoming)
        placed.lib_name = key if key != placed.lib_id else None
        if placed.lib_name:
            issue(
                target,
                "cache_split",
                "Unselected symbols retain their definition. The replacement gets another entry.",
            )
        old_fields, new_fields = (
            {field.name: field for field in before.fields},
            {field.name: field for field in placed.fields},
        )
        changes.append(
            SymbolLibraryChange(
                document_id=target.document_id,
                symbol_uuid=target.symbol_uuid,
                before_lib_id=before.lib_id,
                after_lib_id=placed.lib_id,
                before_cache_key=before.lib_name or before.lib_id,
                after_cache_key=key,
                before_unit_count=unit_count(old),
                after_unit_count=unit_count(incoming),
                fields=[
                    SymbolLibraryFieldChange(
                        name=name, before=old_fields.get(name), after=new_fields.get(name)
                    )
                    for name in sorted(old_fields.keys() | new_fields.keys())
                    if old_fields.get(name) != new_fields.get(name)
                ],
                pins=differences,
                before_pins=before.pins,
                after_pins=placed.pins,
                affected_instance_paths=sorted(
                    {path.path for project in placed.instances for path in project.paths}
                ),
                changed_definition_properties=[
                    name
                    for name in type(incoming).model_fields
                    if name not in ("name", "units", "fields")
                    and getattr(old, name) != getattr(incoming, name)
                ],
                changed_instance_references=references,
                changed_attributes=[
                    name
                    for name in type(options.attributes).model_fields
                    if getattr(before, name) != getattr(placed, name)
                ],
                added_cache_keys=sorted(
                    {entry.name for entry in document.lib_symbols} - cache_before
                ),
                removed_cache_keys=removed[target.document_id],
            )
        )
        removed[target.document_id] = []
    return SymbolLibraryUpdateResult(documents=copies, changes=changes, issues=issues)


def plan_symbol_library_updates(
    documents: Mapping[str, Schematic],
    library_symbols: Mapping[str, Symbol],
    targets: Sequence[SymbolLibraryUpdateTarget],
    *,
    options: SymbolLibraryUpdateOptions | None = None,
) -> SymbolLibraryUpdatePlan:
    """Preview a batch. Missing definitions or unavailable units block only those targets."""
    settings = (options or SymbolLibraryUpdateOptions.gui_defaults()).model_copy(deep=True)
    selected = [target.model_copy(deep=True) for target in targets]
    seed = str(uuid4())
    result = _run(documents, library_symbols, selected, settings, seed)
    return SymbolLibraryUpdatePlan(
        source_signatures={key: _signature(value) for key, value in documents.items()},
        library_signatures={key: _signature(value) for key, value in library_symbols.items()},
        targets=selected,
        options=settings,
        uuid_seed=seed,
        changes=result.changes,
        issues=result.issues,
    )


def apply_symbol_library_updates(
    documents: Mapping[str, Schematic],
    library_symbols: Mapping[str, Symbol],
    plan: SymbolLibraryUpdatePlan,
) -> SymbolLibraryUpdateResult:
    """Apply a preview to detached copies. Reject changed inputs or edited preview results."""
    if plan.source_signatures != {
        key: _signature(value) for key, value in documents.items()
    } or plan.library_signatures != {
        key: _signature(value) for key, value in library_symbols.items()
    }:
        raise ValueError("The schematic or library changed after the update preview.")
    result = _run(documents, library_symbols, plan.targets, plan.options, plan.uuid_seed)
    if result.changes != plan.changes or result.issues != plan.issues:
        raise ValueError("The update preview changed. Create a new preview before applying it.")
    return result


def update_symbol_from_library(
    schematic: Schematic,
    *,
    symbol_uuid: str,
    library_symbols: Mapping[str, Symbol],
    options: SymbolLibraryUpdateOptions | None = None,
) -> SymbolLibraryUpdateResult:
    """Apply a placed symbol's own library definition again, returning a detached schematic."""
    return _run(
        {"schematic": schematic},
        library_symbols,
        [SymbolLibraryUpdateTarget(document_id="schematic", symbol_uuid=symbol_uuid)],
        options or SymbolLibraryUpdateOptions.gui_defaults("update"),
        str(uuid4()),
    )


def change_symbol(
    schematic: Schematic,
    *,
    symbol_uuid: str,
    new_lib_id: str,
    library_symbols: Mapping[str, Symbol],
    options: SymbolLibraryUpdateOptions | None = None,
) -> SymbolLibraryUpdateResult:
    """Change the definition while preserving placement and reference."""
    return _run(
        {"schematic": schematic},
        library_symbols,
        [
            SymbolLibraryUpdateTarget(
                document_id="schematic", symbol_uuid=symbol_uuid, new_lib_id=new_lib_id
            )
        ],
        options or SymbolLibraryUpdateOptions.gui_defaults("change"),
        str(uuid4()),
    )
