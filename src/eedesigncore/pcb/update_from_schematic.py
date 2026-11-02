"""Preview and apply high-level PCB updates from schematic evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from uuid import uuid4

from eedesigncore.common.pcb_family import ComponentClasses, FootprintUnitInfo
from eedesigncore.common.uuid_ops import _iter_models
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb._update_footprints import replace_or_add, stable_ids
from eedesigncore.pcb._update_groups import transfer_groups
from eedesigncore.pcb._update_metadata import unit_information, update_metadata
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.nets import rename_nets
from eedesigncore.project._update_common import (
    board_sheet_name,
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
    PcbUpdateOptions,
    PcbUpdatePlan,
    PcbUpdateResult,
)
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.inspection import (
    SchematicElectricalSnapshot,
    inspect_schematic_electrical,
)
from eedesigncore.schematic_view.models import ViewItem


def _pin_function(name: str | None, number: str) -> str:
    """The pad ``pinfunction`` KiCad's netlist carries for one pin.

    ``netlist_exporter_xml.cpp:1322-1325``: the shown pin name, an underscore and
    the pin number, or nothing when the pin has no name. ``~`` shows as no name.
    """
    shown = "" if name in (None, "", "~") else name
    return f"{shown}_{number}" if shown else ""


def _pin_type(
    pin: ViewItem,
    inspection: SchematicElectricalSnapshot,
    net_pins: Mapping[str, list[ViewItem]],
) -> str:
    """The pad ``pintype``: the pin type, plus ``+no_connect`` under KiCad's rule.

    ``netlist_exporter_xml.cpp:1200-1206, 1327-1338``: the suffix needs a
    no-connect marker on the net, and the net must hold one pin or only stacked
    pins of one symbol. A pin of type no_connect owns the subgraph's no-connect
    slot itself, so a marker on it adds no suffix (``no_connect`` stays plain).
    """
    base = pin.electrical_type or ""
    if base == "no_connect" or pin.id not in inspection.no_connect_pins:
        return base
    members = [item for key in pin.net_ids for item in net_pins.get(key, [])]
    first = members[0] if members else pin
    stacked = all(
        item.parent_item == first.parent_item
        and item.position == first.position
        and item.pin_name == first.pin_name
        for item in members
    )
    return base + "+no_connect" if len(members) <= 1 or stacked else base


def _same_units(first: FootprintUnitInfo, second: FootprintUnitInfo) -> bool:
    return len(first.units) == len(second.units) and all(
        a.name == b.name and Counter(a.pins) == Counter(b.pins)
        for a, b in zip(first.units, second.units, strict=True)
    )


def _run(
    board: Board,
    source: Schematic | SheetHierarchy,
    libraries: Mapping[str, Footprint],
    options: PcbUpdateOptions,
    placements: Mapping[str, FootprintPlacement],
    seed: str,
) -> PcbUpdateResult:
    result = board.model_copy(deep=True)
    issues: list[DesignUpdateIssue] = []
    changes: list[DesignUpdateChange] = []
    validate_inputs(board, source, issues)
    if issues:
        return PcbUpdateResult(board=result, changes=changes, issues=issues)
    inspection = inspect_schematic_electrical(source, document_id="pcb-update", drawing=False)
    if inspection.coverage.electrical != "complete":
        issue(issues, "electrical_coverage", "Complete schematic electrical coverage is required.")
    groups = part_groups(inspection, issues, source)
    matching = match_footprints(
        result.footprints, groups, inspection, options.match_by_reference, issues, source
    )
    originals = {fp.uuid: fp for fp in board.footprints}
    if any(count > 1 for key, count in Counter(fp.uuid for fp in board.footprints).items() if key):
        issue(issues, "duplicate_uuid", "The board has duplicate footprint UUIDs.")
    symbols = placed_symbols(source)
    sheets = {document: sheet for _, document, sheet in sources(source)}
    copper = board.model_copy(deep=True)
    for item in copper.footprints:
        item.pads = []
    selected: set[str | None] = set()
    targets: dict[str, set[str]] = defaultdict(set)
    net_pins: dict[str, list[ViewItem]] = defaultdict(list)
    for view_item in inspection.items.values():
        if view_item.kind == "pin":
            for key in view_item.net_ids:
                net_pins[key].append(view_item)
    expected: dict[tuple[str, int], str] = {}
    for reference, ids in groups.items():
        part = inspection.parts[ids[0]]
        path = symbol_path(inspection, ids[0])
        old = matching.get(reference)
        desired = part.fields.get("Footprint", "")
        if not desired:
            issue(issues, "missing_footprint", "The symbol has no assigned footprint.", reference)
            continue
        if old is not None and old.variants:
            issue(
                issues,
                "variant_footprints",
                "Variant footprint replacement needs an explicit variant selection.",
                reference,
            )
        replace = old is not None and (
            options.refresh_footprints or (options.replace_footprints and old.fp_id != desired)
        )
        if old is None or replace:
            definition = libraries.get(desired)
            placement = old.at if old else placements.get(reference)
            if definition is None:
                issue(
                    issues,
                    "missing_definition",
                    "Supply the selected footprint definition.",
                    desired,
                )
                continue
            if definition.fp_id not in (desired, desired.split(":", 1)[-1]):
                issue(
                    issues,
                    "definition_identity",
                    "The supplied footprint definition has a different identifier.",
                    desired,
                )
                continue
            if placement is None:
                issue(
                    issues,
                    "missing_placement",
                    "Supply a placement for the new footprint.",
                    reference,
                )
                continue
            if old is not None and old.locked and not options.override_locks:
                issue(
                    issues,
                    "locked_replacement",
                    "Footprint replacement requires explicit permission to override its lock.",
                    reference,
                )
                continue
            definition = definition.model_copy(deep=True)
            definition.fp_id = desired
            fp = replace_or_add(result, definition, old, placement, seed, reference, issues)
            changes.append(
                DesignUpdateChange(
                    kind="replace_footprint" if old else "add_footprint",
                    reference=reference,
                    identity=fp.uuid or "",
                    before=old.fp_id if old else None,
                    after=desired,
                )
            )
            if old is not None:
                for pad in old.pads:
                    matches = [new for new in fp.pads if new.id == pad.id and pad.id]
                    changed_contact = (
                        not matches
                        or matches[0].position != pad.position
                        or matches[0].layers != pad.layers
                        or matches[0].size != pad.size
                        or matches[0].angle != pad.angle
                        or matches[0].shape != pad.shape
                        or matches[0].primitives != pad.primitives
                        or matches[0].padstack != pad.padstack
                        or matches[0].drill != pad.drill
                    )
                    if changed_contact and pad.net_name in copper.net_names:
                        issue(
                            issues,
                            "routed_contact",
                            "Replacement changes a pad on a net that has copper.",
                            reference + "." + pad.number,
                            warning=not options.preserve_routed_contacts,
                        )
        else:
            assert old is not None
            fp = old
        selected.add(fp.uuid)
        before = fp.model_dump_json()
        fp.path, link_sheet = footprint_path(source, inspection, ids, fp.path)
        source_symbol = symbols[path][1]
        previous_units = fp.unit_info.model_copy(deep=True) if fp.unit_info else None
        update_metadata(fp, sheets[symbols[path][0]], source_symbol)
        for part_id in ids:
            unit_path = symbol_path(inspection, part_id)
            document, unit_symbol = symbols[unit_path]
            unit_data = unit_information(sheets[document], unit_symbol)
            if (
                fp.unit_info
                and unit_symbol.unit <= len(unit_data.units)
                and unit_symbol.unit <= len(fp.unit_info.units)
            ):
                fp.unit_info.units[unit_symbol.unit - 1] = unit_data.units[unit_symbol.unit - 1]
        # KiCad orders a unit's pins with an unstable sort on position
        # (lib_symbol.cpp:1218-1237), so pins that share a position have no
        # recorded order. Keep the board's order when the pin sets agree.
        if fp.unit_info and previous_units and _same_units(previous_units, fp.unit_info):
            fp.unit_info = previous_units
        classes = sorted(
            {inspection.parts[key].fields.get("Component Class", "") for key in ids} - {""}
        )
        fp.component_classes = ComponentClasses(classes=classes) if classes else None
        if options.update_references:
            set_field(fp.properties, "Reference", reference, layer="F.SilkS")
        if options.update_values:
            set_field(fp.properties, "Value", part.fields.get("Value", ""), layer="F.Fab")
        if options.update_attributes:
            fp.attributes.dnp = part.dnp
            fp.attributes.exclude_from_bom = not part.in_bom
            fp.attributes.exclude_from_pos_files = not source_symbol.in_pos_files
        if options.update_fields:
            for name, value in part.fields.items():
                if name not in {"Reference", "Value", "Footprint", "Component Class"}:
                    set_field(
                        fp.properties,
                        name,
                        value,
                        layer="B.Fab" if fp.layer == "B.Cu" else "F.Fab",
                    )
        if options.remove_extra_fields:
            fp.properties = [
                field
                for field in fp.properties
                if field.name in part.fields
                or field.name in {"Reference", "Value", "Datasheet", "Description"}
            ]
        # Sheetname is the netlist (names …) value and Sheetfile the file name of
        # the sheet (board_netlist_updater.cpp:663-705).
        fp.sheetname = board_sheet_name(source, link_sheet)
        source_node = (
            next(
                (node for node in source.walk() if node.kiid_path.rstrip("/") == link_sheet),
                None,
            )
            if isinstance(source, SheetHierarchy)
            else None
        )
        if source_node is not None:
            fp.sheetfile = source_node.path.name
        if options.update_pad_nets:
            pins: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
            for pin in inspection.items.values():
                if (
                    pin.kind != "pin"
                    or pin.parent_item not in ids
                    or not pin.netlist_member
                    or pin.pin_number is None
                ):
                    continue
                names = {
                    inspection.nets[key].name or ""
                    for key in pin.net_ids
                    if key in inspection.nets
                }
                if len(names) != 1 or not next(iter(names), None):
                    issue(issues, "pin_net", "The pin does not resolve to one scalar net.", pin.id)
                    continue
                pins[pin.pin_number].append(
                    (
                        next(iter(names)) or "",
                        _pin_function(pin.pin_name, pin.pin_number),
                        _pin_type(pin, inspection, net_pins),
                    )
                )
            for number in pins.keys() - {pad.number for pad in fp.pads}:
                issue(
                    issues,
                    "missing_pad",
                    "A schematic pin has no footprint pad.",
                    reference + "." + number,
                )
            no_connect_names: set[str] = set()
            schematic_names = {net.name for net in inspection.nets.values()}
            for index, pad in sorted(enumerate(fp.pads), key=lambda entry: entry[1].id or ""):
                if not pad.number:
                    if pad.net_name:
                        targets[pad.net_name].add("")
                    pad.net_name = None
                    pad.net_code = None
                    pad.pinfunction = None
                    pad.pintype = None
                    continue
                entries = pins.get(pad.number, [])
                names = {entry[0] for entry in entries}
                if len(names) > 1:
                    issue(
                        issues,
                        "pin_net_conflict",
                        "Units assign different nets to the same pad number.",
                        reference + "." + pad.number,
                    )
                    continue
                name = next(iter(names), "")
                if not any(layer.endswith(".Cu") for layer in pad.layers):
                    name = ""
                elif entries and "no_connect" in entries[0][2]:
                    base_name = name
                    suffix = 1
                    while name in no_connect_names or (
                        name != base_name and name in schematic_names
                    ):
                        name = f"{base_name}_{suffix}"
                        suffix += 1
                    no_connect_names.add(name)
                if not entries:
                    issue(
                        issues,
                        "extra_pad",
                        "The footprint pad has no schematic pin. Its net is cleared.",
                        reference + "." + pad.number,
                        warning=True,
                    )
                old_fp = originals.get(fp.uuid)
                old_pad = (
                    next(
                        (previous for previous in old_fp.pads if previous.id == pad.id and pad.id),
                        None,
                    )
                    if old_fp
                    else None
                )
                if old_pad is None and old_fp:
                    same_number = [
                        previous for previous in old_fp.pads if previous.number == pad.number
                    ]
                    if len(same_number) == 1:
                        old_pad = same_number[0]
                if old_pad and old_pad.net_name:
                    targets[old_pad.net_name].add(name)
                expected[(fp.uuid or "", index)] = name
                pad.net_name = name or None
                pad.net_code = None
                pad.pinfunction = (entries[0][1] or None) if entries else None
                pad.pintype = entries[0][2] if entries else None
        if before != fp.model_dump_json():
            changes.append(
                DesignUpdateChange(
                    kind="update_footprint",
                    reference=reference,
                    identity=fp.uuid or "",
                    before=before,
                    after=fp.model_dump_json(),
                )
            )
    for fp in list(result.footprints):
        if fp.uuid in selected:
            continue
        if fp.attributes.board_only:
            for pad in fp.pads:
                if pad.net_name:
                    targets[pad.net_name].add(pad.net_name)
            continue
        if options.delete_extra_footprints and fp not in matching.values():
            if fp.locked and not options.override_locks:
                for pad in fp.pads:
                    if pad.net_name:
                        targets[pad.net_name].add(pad.net_name)
                issue(
                    issues,
                    "locked_deletion",
                    "The extra footprint is locked and is retained.",
                    fp.uuid or "",
                    warning=True,
                )
                continue
            referenced = {member for group in result.groups for member in group.members}
            referenced.update(
                member for generator in result.generators for member in generator.members
            )
            removed_ids = {
                str(getattr(model, name))
                for model in _iter_models(fp)
                for name in ("uuid", "id")
                if name in type(model).model_fields and getattr(model, name)
            }
            if removed_ids.intersection(referenced):
                issue(
                    issues,
                    "group_deletion",
                    "The extra footprint belongs to a board group or generator.",
                    fp.uuid or "",
                )
                continue
            if any(pad.net_name in copper.net_names for pad in fp.pads):
                issue(
                    issues,
                    "routed_deletion",
                    "Deleting the footprint removes pads on nets that have copper.",
                    fp.uuid or "",
                    warning=not options.preserve_routed_contacts,
                )
            result.footprints.remove(fp)
            changes.append(
                DesignUpdateChange(
                    kind="delete_footprint",
                    reference=field_value(fp.properties, "Reference"),
                    identity=fp.uuid or "",
                    before=fp.fp_id,
                )
            )
        else:
            for pad in fp.pads:
                if pad.net_name:
                    targets[pad.net_name].add(pad.net_name)
    mapping: dict[str, str] = {}
    for old_name, names in targets.items():
        if old_name not in copper.net_names or names == {old_name}:
            continue
        if len(names) != 1 or not next(iter(names)):
            issue(
                issues,
                "copper_split",
                "A copper net would split or lose its assignment. "
                "Physical connectivity must resolve this change.",
                old_name,
            )
        else:
            mapping[old_name] = next(iter(names))
    if mapping:
        rename_nets(result, mapping)
        for fp in result.footprints:
            for index, pad in enumerate(fp.pads):
                if (fp.uuid or "", index) in expected:
                    pad.net_name = expected[(fp.uuid or "", index)] or None
        changes.extend(
            DesignUpdateChange(
                kind="rename_copper_net", reference="", identity=old, before=old, after=new
            )
            for old, new in mapping.items()
        )
    for fp in result.footprints:
        if fp.uuid in selected:
            # New fields need stable identities for repeated previews.
            missing = [field for field in fp.properties if field.id is None]
            if missing:
                temporary = Footprint(properties=missing)
                stable_ids(temporary, seed, (fp.uuid or "") + ":fields")
    if options.transfer_symbol_groups:
        transfer_groups(result, source, inspection, groups, changes, issues)
    return PcbUpdateResult(board=result, changes=changes, issues=issues)


def plan_update_pcb_from_schematic(
    board: Board,
    source: Schematic | SheetHierarchy,
    *,
    library_footprints: Mapping[str, Footprint] | None = None,
    options: PcbUpdateOptions | None = None,
    placements: Mapping[str, FootprintPlacement] | None = None,
) -> PcbUpdatePlan:
    """Preview all board updates. Placements for new footprints are keyed by reference."""
    settings = (options or PcbUpdateOptions()).model_copy(deep=True)
    positions = {key: value.model_copy(deep=True) for key, value in (placements or {}).items()}
    libraries = library_footprints or {}
    seed = str(uuid4())
    result = _run(board, source, libraries, settings, positions, seed)
    return PcbUpdatePlan(
        source_signature=signature(source),
        board_signature=signature(board),
        library_signatures={key: signature(value) for key, value in libraries.items()},
        options=settings,
        placements=positions,
        uuid_seed=seed,
        changes=result.changes,
        issues=result.issues,
        result_signature=signature(result.board),
    )


def apply_update_pcb_from_schematic(
    board: Board,
    source: Schematic | SheetHierarchy,
    plan: PcbUpdatePlan,
    *,
    library_footprints: Mapping[str, Footprint] | None = None,
) -> PcbUpdateResult:
    """Validate a preview and return a detached board. This operation never saves files."""
    libraries = library_footprints or {}
    if (
        signature(board) != plan.board_signature
        or signature(source) != plan.source_signature
        or {key: signature(value) for key, value in libraries.items()} != plan.library_signatures
    ):
        raise ValueError(
            "The board, schematic, or footprint definitions changed. Create a new preview."
        )
    result = _run(board, source, libraries, plan.options, plan.placements, plan.uuid_seed)
    if (
        result.changes != plan.changes
        or result.issues != plan.issues
        or signature(result.board) != plan.result_signature
    ):
        raise ValueError("The update preview changed. Create a new preview.")
    if plan.blockers:
        raise ValueError(
            "The PCB update has blockers: " + "; ".join(item.message for item in plan.blockers)
        )
    return result


def update_pcb_from_schematic(
    board: Board,
    source: Schematic | SheetHierarchy,
    *,
    library_footprints: Mapping[str, Footprint] | None = None,
    options: PcbUpdateOptions | None = None,
    placements: Mapping[str, FootprintPlacement] | None = None,
) -> PcbUpdateResult:
    """Plan and apply a PCB update in memory. Reject unresolved changes."""
    plan = plan_update_pcb_from_schematic(
        board,
        source,
        library_footprints=library_footprints,
        options=options,
        placements=placements,
    )
    return apply_update_pcb_from_schematic(
        board, source, plan, library_footprints=library_footprints
    )
