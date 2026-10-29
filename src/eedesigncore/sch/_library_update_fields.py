"""Source-derived field and definition transforms for symbol updates."""

from __future__ import annotations

from collections.abc import Mapping

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.models import Property, TextAttributes
from eedesigncore.common.sch_family.ordering import (
    resolve_parent_symbol,
    resolve_symbol_definition,
)
from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.connectivity.geometry import pin_sheet_position, symbol_transform
from eedesigncore.connectivity.units import iu_to_mm
from eedesigncore.sch.library_update_models import SymbolLibraryUpdateOptions
from eedesigncore.sch.models import PlacedSymbol
from eedesigncore.sch.operations import _refdes_number, _refdes_prefix

MANDATORY = {"Reference", "Value", "Footprint", "Datasheet", "Description"}


def flatten_definition(symbol: Symbol, definitions: Mapping[str, Symbol], name: str) -> Symbol:
    root, fields = resolve_symbol_definition(symbol, definitions)
    result = root.model_copy(deep=True).renamed(name)
    result.extends = None
    result.fields = [field.model_copy(deep=True) for field in fields]
    chain = [symbol]
    while chain[-1].extends:
        parent = resolve_parent_symbol(chain[-1], definitions)
        if parent is None:
            raise ValueError("The inherited symbol definition is missing.")
        chain.append(parent)
    for child in reversed(chain[:-1]):
        if child.keywords:
            result.keywords = child.keywords
        if child.fp_filters:
            result.fp_filters = list(child.fp_filters)
    if symbol.extends:
        parent = chain[1]
        for attribute in ("exclude_from_sim", "in_bom", "on_board", "in_pos_files"):
            setattr(result, attribute, getattr(parent, attribute))
        embedded = {file.name: file for file in result.embedded_files}
        embedded.update({file.name: file.model_copy(deep=True) for file in symbol.embedded_files})
        result.embedded_files = list(embedded.values())
    return result


def placed_field(field: Property, placed: PlacedSymbol) -> Property:
    result = field.model_copy(deep=True)
    point = pin_sheet_position(
        field.position, symbol_transform(placed.angle, placed.mirror), placed.position
    )
    result.position = (iu_to_mm(point[0]), iu_to_mm(point[1]))
    return result


def update_fields(
    placed: PlacedSymbol,
    definition: Symbol,
    options: SymbolLibraryUpdateOptions,
    instance_paths: list[str] | None,
    display_instance_path: str | None,
) -> dict[str, str]:
    settings = options.fields
    library = {field.name: field for field in definition.fields}
    selected = (
        set(settings.selected_fields)
        if settings.selected_fields is not None
        else {field.name for field in [*placed.fields, *definition.fields] if not field.private}
    )
    selected.difference_update(settings.excluded_fields)
    references: dict[str, str] = {}
    kept: list[Property] = []
    for field in placed.fields:
        policy = settings.overrides.get(field.name, settings)
        if field.name not in selected and not (field.private and policy.update_private):
            kept.append(field)
            continue
        incoming = library.get(field.name)
        if incoming is None:
            if field.name in MANDATORY or not policy.remove_extra:
                kept.append(field)
            continue
        field.private = incoming.private
        reset_text = policy.reset_text if incoming.value else policy.reset_empty
        if reset_text:
            if field.name == "Reference":
                if options.reset_reference_prefix:
                    prefix = _refdes_prefix(incoming.value)

                    def new_reference(old: str, prefix: str = prefix) -> str:
                        number = _refdes_number(old)
                        return prefix + (str(number) if number >= 0 else "?")

                    for project in placed.instances:
                        for path in project.paths:
                            if instance_paths is None or path.path in instance_paths:
                                path.reference = new_reference(path.reference)
                                references[project.name + ":" + path.path] = path.reference
                                if path.path == display_instance_path:
                                    field.value = path.reference
                    if instance_paths is None and display_instance_path is None:
                        field.value = new_reference(field.value)
            elif (
                field.name != "Value"
                or definition.power_type is None
                or options.reset_custom_power_value
            ):
                field.value = (
                    unescape_string(incoming.value) if field.name == "Value" else incoming.value
                )
        if policy.reset_visibility:
            field.visible = incoming.visible
            if field.attributes is None:
                field.attributes = TextAttributes()
            field.attributes.visible = incoming.attributes.visible if incoming.attributes else True
        if policy.reset_effects:
            visible = field.attributes.visible if field.attributes else True
            field.attributes = (
                incoming.attributes.model_copy(deep=True)
                if incoming.attributes
                else TextAttributes()
            )
            field.attributes.visible = visible
            field.angle = incoming.angle
            field.show_name = incoming.show_name
            field.do_not_autoplace = incoming.do_not_autoplace
        if policy.reset_positions:
            field.position = placed_field(incoming, placed).position
        kept.append(field)
    existing_names = {field.name for field in kept}
    for field in definition.fields:
        policy = settings.overrides.get(field.name, settings)
        if (
            field.name not in MANDATORY
            and field.name in selected
            and field.name not in existing_names
            and policy.add_missing
        ):
            added = placed_field(field, placed)
            added.show_name = False
            added.do_not_autoplace = False
            kept.append(added)
    placed.fields = kept
    return references
