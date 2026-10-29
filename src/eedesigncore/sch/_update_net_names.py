"""Change net naming sources within physical schematic connection groups."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID, uuid5

from eedesigncore.common.models import JustifyH, JustifyV, TextAttributes
from eedesigncore.common.sch_family.ordering import resolve_symbol_definition
from eedesigncore.connectivity.build import build_subgraphs
from eedesigncore.connectivity.geometry import (
    PinRef,
    _library_pins,
    apply,
    collect,
    symbol_transform,
)
from eedesigncore.connectivity.subgraph import ItemKind, Priority
from eedesigncore.mod.models import Footprint
from eedesigncore.project._update_common import issue, sources
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import DesignUpdateChange, DesignUpdateIssue
from eedesigncore.sch.models import Label, LabelKind, Schematic, SheetPin
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def _label_style(
    source: Schematic | SheetHierarchy, sheet: Schematic, pin: PinRef
) -> tuple[float, TextAttributes]:
    placed = sheet.symbols[pin.symbol_index]
    definitions = {definition.name: definition for definition in sheet.lib_symbols}
    definition, _ = resolve_symbol_definition(
        definitions[placed.lib_name or placed.lib_id], definitions
    )
    library_pin = _library_pins(definition, placed.unit or 1, placed.body_style or 1)[
        pin.pin_index
    ]
    vector = {0: (-1.0, 0.0), 90: (0.0, 1.0), 180: (1.0, 0.0), 270: (0.0, -1.0)}[
        int(library_pin.angle) % 360
    ]
    direction = apply(symbol_transform(placed.angle, placed.mirror), vector)
    angle = {(1.0, 0.0): 0.0, (0.0, -1.0): 90.0, (-1.0, 0.0): 180.0, (0.0, 1.0): 270.0}[direction]
    size = 1.27
    if isinstance(source, SheetHierarchy) and source.project is not None:
        schematic_settings = source.project.root.get_object("schematic")
        drawing = (
            schematic_settings.get_object("drawing") if schematic_settings is not None else None
        )
        configured = drawing.get("default_text_size") if drawing is not None else None
        if (
            isinstance(configured, int | float)
            and not isinstance(configured, bool)
            and configured > 0
        ):
            size = float(configured) * 0.0254
    return angle, TextAttributes(
        font_size=(size, size),
        justify_h=JustifyH.left if angle in (0, 90) else JustifyH.right,
        justify_v=JustifyV.bottom,
    )


def update_net_names(
    source: Schematic | SheetHierarchy,
    inspection: SchematicElectricalSnapshot,
    matched: dict[str, Footprint],
    documents: dict[str, Schematic],
    seed: str,
    changes: list[DesignUpdateChange],
    issues: list[DesignUpdateIssue],
    *,
    skipped: set[str],
    skipped_pins: set[tuple[str, str]],
) -> None:
    edits: dict[tuple[str, str], set[str]] = defaultdict(set)
    objects: dict[tuple[str, str], Label | SheetPin] = {}
    ranks = {
        LabelKind.local: Priority.LOCAL_LABEL,
        LabelKind.global_: Priority.GLOBAL,
        LabelKind.hierarchical: Priority.HIER_LABEL,
    }
    kinds = {
        LabelKind.local: ItemKind.LABEL,
        LabelKind.global_: ItemKind.GLOBAL_LABEL,
        LabelKind.hierarchical: ItemKind.HIER_LABEL,
    }
    for path, document_id, sheet in sources(source):
        geometry = collect(sheet, path)
        keys: dict[tuple[ItemKind, int], str] = {}
        priorities: dict[str, Priority] = {}
        label_index = 0
        for index, label in enumerate(sheet.labels):
            if label.kind == LabelKind.netclass_flag:
                continue
            key = "label:" + str(index)
            keys[kinds[label.kind], label_index] = key
            priorities[key] = ranks[label.kind]
            objects[document_id, key] = documents[document_id].labels[index]
            label_index += 1
        pin_index = 0
        for sheet_index, child in enumerate(sheet.sheets):
            for local_index, _sheet_pin in enumerate(child.pins):
                key = f"sheet_pin:{sheet_index}:{local_index}"
                keys[ItemKind.SHEET_PIN, pin_index] = key
                priorities[key] = Priority.SHEET_PIN
                objects[document_id, key] = (
                    documents[document_id].sheets[sheet_index].pins[local_index]
                )
                pin_index += 1
        for group_index, group in enumerate(build_subgraphs(geometry, source_items=keys)):
            desired: set[str] = set()
            current: set[str] = set()
            for pin in group.pins:
                if (
                    path.rstrip("/") + "/" + pin.symbol_uuid in skipped
                    or (path.rstrip("/") + "/" + pin.symbol_uuid, pin.pin_number) in skipped_pins
                ):
                    continue
                fp = matched.get(pin.reference)
                if fp is None:
                    continue
                desired.update(
                    pad.net_name.rsplit("/", 1)[-1]
                    for pad in fp.pads
                    if pad.number == pin.pin_number and pad.net_name
                )
                for item in inspection.items.values():
                    parent = inspection.items.get(item.parent_item or "")
                    if (
                        item.kind == "pin"
                        and item.pin_number == pin.pin_number
                        and parent
                        and parent.source
                        and parent.source.source_uuid == pin.symbol_uuid
                        and parent.source.sheet_path == path
                    ):
                        current.update(
                            (inspection.nets[key].name or "").rsplit("/", 1)[-1]
                            for key in item.net_ids
                        )
            if not desired:
                continue
            drivers = [key for key in group.source_items if key in priorities]
            highest = max(
                [
                    *(priorities[key] for key in drivers),
                    *(driver.priority for driver in group.drivers),
                ],
                default=Priority.PIN,
            )
            candidates = [key for key in drivers if priorities[key] == highest]
            if len(desired) != 1:
                issue(
                    issues,
                    "pin_swap_required",
                    "Board nets split a physical schematic connection. "
                    "Pin or unit geometry changes are required.",
                    path,
                )
                continue
            target = next(iter(desired))
            if candidates:
                if len(candidates) > 1 and desired != current:
                    issue(
                        issues,
                        "ambiguous_naming_source",
                        "Multiple naming sources have equal priority on this physical connection.",
                        path,
                    )
                    continue
                for key in candidates:
                    naming_source = objects[document_id, key]
                    unchanged = (
                        naming_source.content
                        if isinstance(naming_source, Label)
                        else naming_source.name
                    )
                    edits[document_id, key].add(unchanged if desired == current else target)
            elif desired != current:
                if any(
                    driver.priority in {Priority.GLOBAL_POWER_PIN, Priority.LOCAL_POWER_PIN}
                    for driver in group.drivers
                ):
                    issue(
                        issues,
                        "power_net_name",
                        "A power pin controls this net name. Its value needs an explicit update.",
                        path,
                    )
                    continue
                if len(current) != 1:
                    issue(
                        issues,
                        "unresolved_physical_net",
                        "The physical connection does not have one resolved schematic net.",
                        path,
                    )
                    continue
                if sum(key == document_id for _, key, _ in sources(source)) > 1:
                    issue(
                        issues,
                        "shared_sheet_label",
                        "Adding a label would affect multiple sheet occurrences.",
                        document_id,
                    )
                    continue
                naming_pin = next(
                    (
                        pin
                        for pin in group.pins
                        if not pin.from_power_symbol and not pin.is_no_connect
                    ),
                    None,
                )
                if naming_pin is None:
                    issue(
                        issues,
                        "missing_label_contact",
                        "No ordinary pin is available for the new local label.",
                        path,
                    )
                    continue
                angle, attributes = _label_style(source, sheet, naming_pin)
                label = Label(
                    kind=LabelKind.local,
                    content=target,
                    position=(naming_pin.point[0] / 1e6, naming_pin.point[1] / 1e6),
                    angle=angle,
                    attributes=attributes,
                    uuid=str(uuid5(UUID(seed), f"{path}:{group_index}")),
                )
                documents[document_id].labels.append(label)
                changes.append(
                    DesignUpdateChange(
                        kind="add_net_label",
                        reference=naming_pin.reference,
                        identity=label.uuid or "",
                        after=target,
                    )
                )
    for object_key, targets in edits.items():
        naming_item = objects[object_key]
        before = naming_item.content if isinstance(naming_item, Label) else naming_item.name
        if len(targets) != 1:
            issue(
                issues,
                "shared_label_conflict",
                "Repeated sheet occurrences require different text for one naming source.",
                object_key[0],
            )
            continue
        target = next(iter(targets))
        if target == before:
            continue
        if "${" in before:
            issue(
                issues,
                "label_text_variable",
                "The naming source contains a text variable and needs an explicit update.",
                object_key[0],
            )
            continue
        if isinstance(naming_item, Label):
            naming_item.content = target
        else:
            naming_item.name = target
        changes.append(
            DesignUpdateChange(
                kind="rename_net_label",
                reference="",
                identity=naming_item.uuid or "",
                before=before,
                after=target,
            )
        )
