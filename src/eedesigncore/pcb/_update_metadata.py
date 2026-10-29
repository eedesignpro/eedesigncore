"""Transfer schematic library metadata to a placed footprint."""

from __future__ import annotations

from eedesigncore.common.pcb_family import ComponentClasses, FootprintGateUnit, FootprintUnitInfo
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import join_fp_filters
from eedesigncore.mod.models import Footprint
from eedesigncore.project._update_common import field_value
from eedesigncore.sch._library_update_fields import flatten_definition
from eedesigncore.sch.models import PlacedSymbol, Schematic


def update_metadata(footprint: Footprint, schematic: Schematic, placed: PlacedSymbol) -> None:
    definitions = {definition.name: definition for definition in schematic.lib_symbols}
    definition = flatten_definition(
        definitions[placed.lib_name or placed.lib_id], definitions, placed.lib_id
    )
    footprint.fp_filters = join_fp_filters(definition.fp_filters) or None
    footprint.duplicate_pad_numbers_are_jumpers = definition.duplicate_pin_numbers_are_jumpers
    footprint.jumper_pad_groups = (
        [list(group) for group in definition.jumper_pin_groups]
        if definition.jumper_pin_groups is not None
        else None
    )
    footprint.unit_info = unit_information(schematic, placed)
    class_name = field_value(placed.fields, "Component Class")
    footprint.component_classes = ComponentClasses(classes=[class_name]) if class_name else None


def unit_information(schematic: Schematic, placed: PlacedSymbol) -> FootprintUnitInfo:
    definitions = {definition.name: definition for definition in schematic.lib_symbols}
    definition = flatten_definition(
        definitions[placed.lib_name or placed.lib_id], definitions, placed.lib_id
    )
    units: list[FootprintGateUnit] = []
    for number in range(
        1, max(1, max((unit.unit_index for unit in definition.units), default=0)) + 1
    ):
        selected = [unit for unit in definition.units if unit.unit_index in (0, number)]
        pins = [pin for unit in selected for pin in unit.items if isinstance(pin, Pin)]
        pins.sort(key=lambda pin: (pin.position[0], -pin.position[1]))
        name = next(
            (
                unit.unit_display_name
                for unit in selected
                if unit.unit_index == number and unit.unit_display_name
            ),
            None,
        )
        if name is None:
            index = number
            name = ""
            while index:
                index, remainder = divmod(index - 1, 26)
                name = chr(65 + remainder) + name
        units.append(
            FootprintGateUnit(name=name, pins=list(dict.fromkeys(pin.number for pin in pins)))
        )
    return FootprintUnitInfo(units=units)
