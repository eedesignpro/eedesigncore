"""Apply complete unit permutations before ordinary reverse annotation."""

from __future__ import annotations

from collections import defaultdict

from eedesigncore.mod.models import Footprint
from eedesigncore.pcb._update_metadata import unit_information
from eedesigncore.project._update_common import issue, placed_symbols, sources, symbol_path
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.project.update_models import DesignUpdateChange, DesignUpdateIssue
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.inspection import SchematicElectricalSnapshot


def swap_units(
    source: Schematic | SheetHierarchy,
    inspection: SchematicElectricalSnapshot,
    groups: dict[str, list[str]],
    matched: dict[str, Footprint],
    documents: dict[str, Schematic],
    changes: list[DesignUpdateChange],
    issues: list[DesignUpdateIssue],
) -> set[str]:
    raw = placed_symbols(source)
    sheets = {document: sheet for _, document, sheet in sources(source)}
    skipped: set[str] = set()
    demands: dict[tuple[str, str], set[int]] = defaultdict(set)
    for reference, part_ids in groups.items():
        footprint = matched.get(reference)
        if footprint is None or len(part_ids) < 2:
            continue
        ids = sorted(part_ids, key=lambda key: inspection.items[key].label)
        vectors: list[list[str]] = []
        wanted: list[list[str]] = []
        original_units: list[int] = []
        for part_id in ids:
            path = symbol_path(inspection, part_id)
            document, placed = raw[path]
            information = unit_information(sheets[document], placed)
            original_units.append(placed.unit)
            if placed.unit > len(information.units):
                vectors.append([])
                wanted.append([])
                continue
            pin_numbers = information.units[placed.unit - 1].pins
            names: dict[str, str] = {}
            for pin in inspection.items.values():
                if pin.kind == "pin" and pin.parent_item == part_id and pin.pin_number:
                    resolved = {
                        (inspection.nets[key].name or "").rsplit("/", 1)[-1] for key in pin.net_ids
                    }
                    if len(resolved) == 1:
                        names[pin.pin_number] = next(iter(resolved))
            vectors.append([names.get(number, "") for number in pin_numbers])
            wanted.append(
                [
                    next(
                        (pad.net_name or "" for pad in footprint.pads if pad.number == number), ""
                    ).rsplit("/", 1)[-1]
                    for number in pin_numbers
                ]
            )
        mapping: list[int] = []
        used: set[int] = set()
        for vector in wanted:
            candidate = next(
                (
                    index
                    for index, current in enumerate(vectors)
                    if current == vector and index not in used
                ),
                None,
            )
            if candidate is None:
                mapping = []
                break
            mapping.append(candidate)
            used.add(candidate)
        if not mapping or all(index == target for index, target in enumerate(mapping)):
            continue
        new_units = original_units[:]
        visited: set[int] = set()
        for start in range(len(ids)):
            cycle: list[int] = []
            current = start
            while current not in visited:
                visited.add(current)
                cycle.append(current)
                current = mapping[current]
            for index in range(len(cycle) - 1, 0, -1):
                left, right = cycle[index - 1], cycle[index]
                new_units[left], new_units[right] = new_units[right], new_units[left]
        for part_id, before, after in zip(ids, original_units, new_units, strict=True):
            if before == after:
                continue
            path = symbol_path(inspection, part_id)
            document, original = raw[path]
            demands[document, original.uuid or ""].add(after)
            placed = next(
                item for item in documents[document].symbols if item.uuid == original.uuid
            )
            placed.unit = after
            sheet_path = path.rsplit("/", 1)[0]
            instances = [
                instance
                for project in placed.instances
                for instance in project.paths
                if instance.path.rstrip("/") == sheet_path
            ]
            if len(instances) != 1:
                issue(
                    issues,
                    "unit_instance",
                    "A unit swap requires one matching saved sheet occurrence.",
                    path,
                )
            else:
                instances[0].unit = after
            skipped.add(path)
            changes.append(
                DesignUpdateChange(
                    kind="swap_unit",
                    reference=reference,
                    identity=path,
                    before=str(before),
                    after=str(after),
                )
            )
    for path, (document, placed) in raw.items():
        if path not in skipped:
            demands[document, placed.uuid or ""].add(placed.unit)
    for (document, uuid), units in demands.items():
        if len(units) > 1:
            issue(
                issues,
                "shared_unit_conflict",
                "Sheet occurrences require different base units for one saved symbol.",
                document + ":" + uuid,
            )
    return skipped
