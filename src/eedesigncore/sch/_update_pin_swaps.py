"""Apply reciprocal pin swaps without changing the set of wire contacts."""

from __future__ import annotations

from collections import defaultdict

from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.connectivity.geometry import collect
from eedesigncore.mod.models import Footprint
from eedesigncore.project._update_common import issue, placed_symbols, sources, symbol_path
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import DesignUpdateChange, DesignUpdateIssue
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.operations import _register_lib_def
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def swap_pins(
    source: Schematic | SheetHierarchy,
    inspection: SchematicElectricalSnapshot,
    groups: dict[str, list[str]],
    matched: dict[str, Footprint],
    documents: dict[str, Schematic],
    skipped_units: set[str],
    changes: list[DesignUpdateChange],
    issues: list[DesignUpdateIssue],
) -> set[tuple[str, str]]:
    raw = placed_symbols(source)
    skipped: set[tuple[str, str]] = set()
    power_pins = {
        (path.rstrip("/") + "/" + pin.symbol_uuid, pin.pin_number)
        for path, _, sheet in sources(source)
        for pin in collect(sheet, path).pins
        if pin.is_power
    }
    for reference, ids in groups.items():
        footprint = matched.get(reference)
        if footprint is None:
            continue
        for part_id in ids:
            path = symbol_path(inspection, part_id)
            if path in skipped_units:
                continue
            document, original = raw[path]
            if (
                sum(name == document for _, name, _ in sources(source)) > 1
                or sum(len(project.paths) for project in original.instances) > 1
            ):
                issue(
                    issues,
                    "shared_pin_swap_fallback",
                    "Pin swaps are disabled for shared symbols. "
                    "Net labels will be checked instead.",
                    path,
                    warning=True,
                )
                continue
            mismatch: dict[str, tuple[str, str]] = {}
            for pin in inspection.items.values():
                if (
                    pin.kind != "pin"
                    or pin.parent_item != part_id
                    or not pin.pin_number
                    or (path, pin.pin_number) in power_pins
                ):
                    continue
                current = {
                    (inspection.nets[key].name or "").rsplit("/", 1)[-1] for key in pin.net_ids
                }
                wanted = {
                    (pad.net_name or "").rsplit("/", 1)[-1]
                    for pad in footprint.pads
                    if pad.number == pin.pin_number
                }
                if (
                    len(current) == len(wanted) == 1
                    and "" not in current | wanted
                    and current != wanted
                ):
                    mismatch[pin.pin_number] = (next(iter(current)), next(iter(wanted)))
            pairs: list[tuple[str, str]] = []
            used: set[str] = set()
            for number in sorted(mismatch):
                if number in used:
                    continue
                partner = next(
                    (
                        candidate
                        for candidate in sorted(mismatch)
                        if candidate not in used
                        and candidate != number
                        and mismatch[candidate] == tuple(reversed(mismatch[number]))
                    ),
                    None,
                )
                if partner:
                    pairs.append((number, partner))
                    used.update((number, partner))
            if not pairs:
                continue
            schematic = documents[document]
            symbol = next(item for item in schematic.symbols if item.uuid == original.uuid)
            previous_key = symbol.lib_name or symbol.lib_id
            definition = next(
                item for item in schematic.lib_symbols if item.name == previous_key
            ).model_copy(deep=True)
            if definition.extends:
                issue(
                    issues,
                    "derived_pin_swap",
                    "Flatten the embedded derived symbol before a pin geometry swap.",
                    path,
                )
                continue
            pins: dict[str, list[Pin]] = defaultdict(list)
            for unit in definition.units:
                if unit.unit_index in (0, symbol.unit) and unit.body_style_index in (
                    0,
                    symbol.body_style,
                ):
                    for item in unit.items:
                        if isinstance(item, Pin):
                            pins[item.number].append(item)
            for left, right in pairs:
                if len(pins[left]) != 1 or len(pins[right]) != 1:
                    issue(
                        issues,
                        "ambiguous_pin_swap",
                        "A pin geometry swap needs one library pin for each number.",
                        path,
                    )
                    continue
                a, b = pins[left][0], pins[right][0]
                if a.length != b.length:
                    issue(
                        issues,
                        "pin_swap_cleanup",
                        "Unequal pin lengths require native wire cleanup before a geometry swap.",
                        path,
                    )
                    continue
                a.position, b.position = b.position, a.position
                a.angle, b.angle = b.angle, a.angle
                skipped.update(((path, left), (path, right)))
                changes.append(
                    DesignUpdateChange(
                        kind="swap_pins",
                        reference=reference,
                        identity=path,
                        before=left,
                        after=right,
                    )
                )
            key = _register_lib_def(schematic, definition.renamed(symbol.lib_id))
            symbol.lib_name = key if key != symbol.lib_id else None
            used_keys = {item.lib_name or item.lib_id for item in schematic.symbols}
            parent_keys = {item.extends for item in schematic.lib_symbols if item.extends}
            if previous_key not in used_keys | parent_keys:
                schematic.lib_symbols = [
                    item for item in schematic.lib_symbols if item.name != previous_key
                ]
    return skipped
