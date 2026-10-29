"""Pin identity and displacement handling for library replacement."""

from __future__ import annotations

from collections import Counter
from uuid import UUID, uuid5

from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.connectivity.geometry import pin_sheet_position, symbol_transform
from eedesigncore.connectivity.units import iu_to_mm
from eedesigncore.sch.library_update_models import SymbolLibraryPinChange
from eedesigncore.sch.models import PlacedSymbol, PlacedSymbolPin


def library_pins(definition: Symbol, body_style: int) -> list[tuple[int, Pin]]:
    return [
        (unit.unit_index, item)
        for unit in definition.units
        if not body_style or unit.body_style_index in (0, body_style)
        for item in unit.items
        if isinstance(item, Pin)
    ]


def unit_count(definition: Symbol) -> int:
    return max(1, max((unit.unit_index for unit in definition.units), default=1))


def pin_changes(old: Symbol, new: Symbol, placed: PlacedSymbol) -> list[SymbolLibraryPinChange]:
    transform = symbol_transform(placed.angle, placed.mirror)

    def positions(definition: Symbol) -> dict[tuple[str, int, int], tuple[float, float]]:
        result: dict[tuple[str, int, int], tuple[float, float]] = {}
        counts: Counter[tuple[str, int]] = Counter()
        for unit, pin in library_pins(definition, placed.body_style):
            key = (pin.number, unit)
            point = pin_sheet_position(pin.position, transform, placed.position)
            result[(*key, counts[key])] = (iu_to_mm(point[0]), iu_to_mm(point[1]))
            counts[key] += 1
        return result

    before, after = positions(old), positions(new)
    changes: list[SymbolLibraryPinChange] = []
    for key in sorted(before.keys() | after.keys()):
        start, end = before.get(key), after.get(key)
        if start != end:
            changes.append(
                SymbolLibraryPinChange(
                    number=key[0],
                    unit=key[1],
                    before_mm=start,
                    after_mm=end,
                    kind="added" if start is None else "removed" if end is None else "moved",
                )
            )
    return changes


def update_pins(
    placed: PlacedSymbol,
    old: Symbol,
    new: Symbol,
    *,
    reset_alternates: bool,
    instance_paths: list[str] | None,
    seed: str,
    identity: str,
) -> bool:
    """Use file order for native pointer-order ties, and return whether ties occurred."""
    old_definitions = library_pins(old, placed.body_style)
    incoming = library_pins(new, placed.body_style)
    old_by_number = {pin.number: pin for _, pin in old_definitions}
    alternates = {pin.number: pin.alternate for pin in placed.pins if pin.alternate}
    available = list(range(len(placed.pins)))
    assignments: dict[int, tuple[int, Pin]] = {}
    unmatched: list[tuple[int, Pin]] = []
    ambiguous = any(count > 1 for count in Counter(pin.number for _, pin in incoming).values())
    for unit, pin in incoming:
        candidates = [index for index in available if placed.pins[index].number == pin.number]
        if candidates:
            index = candidates[0]
            ambiguous |= len(candidates) > 1
            assignments[index] = (unit, pin)
            available.remove(index)
        else:
            unmatched.append((unit, pin))
    ambiguous |= bool(unmatched and available and (len(available) > 1 or len(unmatched) > 1))
    for offset, item in enumerate(unmatched):
        if available:
            index = available.pop(0)
        else:
            index = len(placed.pins)
            placed.pins.append(
                PlacedSymbolPin(
                    number=item[1].number,
                    uuid=str(uuid5(UUID(seed), f"{identity}:{offset}:{item[1].number}")),
                )
            )
        assignments[index] = item
    units = {
        path.unit
        for project in placed.instances
        for path in project.paths
        if instance_paths is None or path.path in instance_paths
    }
    if not placed.instances:
        units.add(placed.unit)
    retained: list[PlacedSymbolPin] = []
    for index, record in enumerate(placed.pins):
        if index not in assignments:
            continue
        unit, pin = assignments[index]
        record.number = pin.number
        alternate = alternates.get(pin.number)
        if alternate:
            record.alternate = alternate
            if alternate not in {choice.name for choice in pin.alternates}:
                previous = old_by_number.get(pin.number)
                old_choice = (
                    next(
                        (choice for choice in previous.alternates if choice.name == alternate),
                        None,
                    )
                    if previous
                    else None
                )
                if old_choice:
                    match = next(
                        (
                            choice
                            for choice in sorted(pin.alternates, key=lambda value: value.name)
                            if choice.electrical_type == old_choice.electrical_type
                            and choice.shape == old_choice.shape
                        ),
                        None,
                    )
                    if match:
                        record.alternate = match.name
        if (unit == 0 or unit in units) and (
            reset_alternates
            or record.alternate == pin.name
            or record.alternate not in {choice.name for choice in pin.alternates}
        ):
            record.alternate = None
        retained.append(record)
    placed.pins = retained
    return ambiguous
