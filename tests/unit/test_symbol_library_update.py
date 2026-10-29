"""CONSTRUCTED source-behavior cases. Native GUI golden pairs remain pending."""

from __future__ import annotations

from uuid import UUID, uuid5

import pytest

from eedesigncore import (
    InstancePath,
    InstanceProject,
    InstanceVariant,
    Line,
    Pin,
    PinAlternate,
    PinElectricalType,
    PinShape,
    PlacedSymbol,
    PlacedSymbolPin,
    Property,
    Schematic,
    Symbol,
    SymbolFieldUpdateOptions,
    SymbolFieldUpdatePolicy,
    SymbolLibraryUpdateOptions,
    SymbolLibraryUpdateTarget,
    SymbolUnit,
    TextAttributes,
    apply_symbol_library_updates,
    change_symbol,
    plan_symbol_library_updates,
    select_symbol_library_targets,
    update_symbol_from_library,
)
from eedesigncore.common.sch_family.enums import PowerType


def identity(name: str) -> str:
    return str(uuid5(UUID("9461d999-6d64-4ec2-9e09-54df6998e3c5"), name))


def pin(number: str, x: float = 0, y: float = 0, *, name: str = "") -> Pin:
    return Pin(
        number=number,
        name=name,
        position=(x, y),
        electrical_type=PinElectricalType.passive,
        shape=PinShape.line,
    )


def definition(
    name: str = "Test:Old", *, numbers: tuple[str, ...] = ("1", "2"), units: int = 1
) -> Symbol:
    return Symbol(
        name=name,
        fields=[
            Property(name="Reference", value="U", position=(0, 2.54)),
            Property(name="Value", value=name.split(":")[-1]),
            Property(name="Footprint", value="Package:Library"),
            Property(name="Datasheet", value="https://example.com/part"),
            Property(name="Description", value="Library description"),
            Property(name="Supplier", value="Library supplier"),
        ],
        units=[
            SymbolUnit.for_symbol(
                name,
                unit_index=unit,
                items=[
                    pin(number, float(index), float(unit)) for index, number in enumerate(numbers)
                ],
            )
            for unit in range(1, units + 1)
        ],
    )


def schematic(*, units: int = 1, placed_unit: int = 1, copies: int = 1) -> Schematic:
    source = definition(units=units)
    symbols = []
    for index in range(copies):
        fields = [field.model_copy(deep=True) for field in source.fields]
        fields[0].value = f"U{index + 1}"
        fields[1].value = "Custom value"
        fields[2].value = "Package:Manual"
        fields[-1].value = "Manual supplier"
        symbols.append(
            PlacedSymbol(
                uuid=identity(f"symbol{index}"),
                lib_id=source.name,
                position=(50.8, 38.1),
                unit=placed_unit,
                fields=fields,
                pins=[
                    PlacedSymbolPin(
                        number=item.number,
                        uuid=identity(f"pin{index}-{unit.unit_index}-{item.number}"),
                    )
                    for unit in source.units
                    for item in unit.items
                ],
                instances=[
                    InstanceProject(
                        name="demo",
                        paths=[
                            InstancePath(
                                path="/root/child", reference=f"U{index + 1}", unit=placed_unit
                            )
                        ],
                    )
                ],
            )
        )
    return Schematic(
        uuid=identity("sheet"),
        symbols=symbols,
        lib_symbols=[source],
        lines=[Line(start=(0, 0), end=(50.8, 38.1), uuid=identity("wire"))],
    )


def fields(placed: PlacedSymbol) -> dict[str, Property]:
    return {field.name: field for field in placed.fields}


def update(sch: Schematic, incoming: Symbol, options: SymbolLibraryUpdateOptions | None = None):
    return update_symbol_from_library(
        sch,
        symbol_uuid=identity("symbol0"),
        library_symbols={"Test:Old": incoming},
        options=options,
    )


def test_update_keep_manual_footprint_and_user_fields():
    sch = schematic()
    before = sch.model_copy(deep=True)
    incoming = definition()
    incoming.units[0].items[0].position = (3.81, 0)
    result = update(sch, incoming, SymbolLibraryUpdateOptions.preserve_custom_fields())
    placed = result.schematic.symbols[0]
    assert placed.fields == before.symbols[0].fields
    assert placed.pins == before.symbols[0].pins
    assert result.schematic.lib_symbols[0] == incoming
    assert result.schematic.lines == before.lines
    assert sch == before
    assert "pin_mismatch" in {issue.code for issue in result.issues}
    assert result.changes[0].pins[0].kind == "moved"


def test_change_atomic_part_reset_selected_fields():
    sch = schematic()
    sch.symbols[0].dnp = True
    sch.symbols[0].angle, sch.symbols[0].mirror = 90, "x"
    sch.symbols[0].fields.append(Property(name="User only", value="remove"))
    incoming = definition("Test:New")
    incoming.in_bom = incoming.on_board = incoming.in_pos_files = False
    incoming.exclude_from_sim = True
    result = change_symbol(
        sch,
        symbol_uuid=identity("symbol0"),
        new_lib_id=incoming.name,
        library_symbols={incoming.name: incoming},
        options=SymbolLibraryUpdateOptions.atomic_part(),
    )
    placed = result.schematic.symbols[0]
    assert (placed.position, placed.angle, placed.mirror, placed.unit) == (
        (50.8, 38.1),
        90,
        "x",
        1,
    )
    assert fields(placed)["Reference"].value == "U1"
    assert fields(placed)["Footprint"].value == "Package:Library"
    assert fields(placed)["Value"].value == "New"
    assert "User only" not in fields(placed)
    assert placed.dnp and placed.exclude_from_sim
    assert not placed.in_bom and not placed.on_board and not placed.in_pos_files
    assert placed.instances == sch.symbols[0].instances
    assert [entry.name for entry in result.schematic.lib_symbols] == ["Test:New"]


@pytest.mark.parametrize("reset_text", [False, True])
@pytest.mark.parametrize("reset_empty", [False, True])
def test_update_empty_fields_independent_of_text_reset(reset_text, reset_empty):
    sch, incoming = schematic(), definition()
    fields_incoming = {field.name: field for field in incoming.fields}
    fields_incoming["Footprint"].value = ""
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(reset_text=reset_text, reset_empty=reset_empty)
    )
    placed = update(sch, incoming, options).schematic.symbols[0]
    assert fields(placed)["Footprint"].value == ("" if reset_empty else "Package:Manual")
    assert fields(placed)["Supplier"].value == (
        "Library supplier" if reset_text else "Manual supplier"
    )


@pytest.mark.parametrize(
    ("angle", "mirror", "expected"),
    [
        (0, None, (52.8, 35.1)),
        (90, None, (47.8, 36.1)),
        (180, None, (48.8, 41.1)),
        (270, None, (53.8, 40.1)),
        (0, "x", (52.8, 41.1)),
        (0, "y", (48.8, 35.1)),
    ],
)
def test_change_rotated_mirrored_field_styles_and_positions(angle, mirror, expected):
    sch, incoming = schematic(), definition()
    sch.symbols[0].angle, sch.symbols[0].mirror = angle, mirror
    field = next(field for field in incoming.fields if field.name == "Supplier")
    field.position, field.angle = (2, 3), 90
    field.attributes = TextAttributes(bold=True, font_size=(2, 2))
    field.visible = False
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(reset_positions=True, reset_effects=True)
    )
    placed = update(sch, incoming, options).schematic.symbols[0]
    result = fields(placed)["Supplier"]
    assert result.position == pytest.approx(expected)
    assert result.angle == 90
    assert result.attributes.bold
    assert result.visible == fields(sch.symbols[0])["Supplier"].visible


@pytest.mark.parametrize(
    ("name", "new_name", "reset", "expected"),
    [
        ("ALT", "ALT", False, "ALT"),
        ("OLD", "NEW", False, "NEW"),
        ("OLD", None, False, None),
        ("ALT", "ALT", True, None),
        ("BASE", "BASE", False, None),
    ],
)
def test_update_alternate_pin_functions_preserve_remap_clear(name, new_name, reset, expected):
    sch, incoming = schematic(), definition()
    old_pin = sch.lib_symbols[0].units[0].items[0]
    old_pin.alternates = [
        PinAlternate(name=name, electrical_type=PinElectricalType.input, shape=PinShape.inverted)
    ]
    new_pin = incoming.units[0].items[0]
    new_pin.name = "BASE"
    if new_name:
        new_pin.alternates = [
            PinAlternate(
                name=new_name, electrical_type=PinElectricalType.input, shape=PinShape.inverted
            )
        ]
    sch.symbols[0].pins[0].alternate = name
    options = SymbolLibraryUpdateOptions(reset_alternate_pins=reset)
    result = update(sch, incoming, options)
    assert result.schematic.symbols[0].pins[0].alternate == expected
    assert result.schematic.symbols[0].pins[0].uuid == sch.symbols[0].pins[0].uuid


def test_change_moved_added_removed_and_renumbered_pins():
    sch, incoming = schematic(), definition(numbers=("2", "3", "4"))
    incoming.units[0].items[0].position = (10, 10)
    result = update(sch, incoming)
    pins = result.schematic.symbols[0].pins
    assert [(pin.number, pin.uuid) for pin in pins[:2]] == [
        ("3", sch.symbols[0].pins[0].uuid),
        ("2", sch.symbols[0].pins[1].uuid),
    ]
    assert pins[2].number == "4"
    assert pins[2].uuid not in {pin.uuid for pin in sch.symbols[0].pins}
    assert {change.kind for change in result.changes[0].pins} == {"moved", "added", "removed"}
    assert result.schematic.lines == sch.lines


def test_change_multiunit_and_repeated_sheet_instances():
    sch = schematic(units=2, placed_unit=2, copies=2)
    sch.symbols[1].unit = 1
    sch.symbols[1].instances[0].paths.extend(
        [
            InstancePath(
                path="/root/other",
                reference="U9",
                unit=2,
                variants=[InstanceVariant(name="Option", dnp=True)],
            )
        ]
    )
    incoming = definition()
    targets = select_symbol_library_targets({"child": sch})
    plan = plan_symbol_library_updates({"child": sch}, {incoming.name: incoming}, targets)
    result = apply_symbol_library_updates({"child": sch}, {incoming.name: incoming}, plan)
    assert len(result.changes) == 1
    assert result.documents["child"].symbols[0] == sch.symbols[0]
    assert result.documents["child"].symbols[1].instances == sch.symbols[1].instances
    assert {"unavailable_unit", "unit_count_changed", "instance_unit_mismatch"} <= {
        issue.code for issue in result.issues
    }


def test_update_selected_definition_cache_split_and_cleanup():
    sch, incoming = schematic(copies=2), definition()
    incoming.keywords = "new keyword"
    first = update(sch, incoming)
    assert first.schematic.symbols[0].lib_id == "Test:Old"
    assert first.schematic.symbols[0].lib_name == "Old_1"
    assert first.schematic.symbols[1] == sch.symbols[1]
    assert {entry.name for entry in first.schematic.lib_symbols} == {"Test:Old", "Old_1"}
    targets = select_symbol_library_targets({"main": sch})
    plan = plan_symbol_library_updates({"main": sch}, {incoming.name: incoming}, targets)
    result = apply_symbol_library_updates({"main": sch}, {incoming.name: incoming}, plan)
    assert [entry.name for entry in result.documents["main"].lib_symbols] == ["Test:Old"]
    assert all(symbol.lib_name is None for symbol in result.documents["main"].symbols)
    again = update(first.schematic, incoming)
    assert again.schematic.symbols[0].lib_name == "Old_1"
    assert len(again.schematic.lib_symbols) == 2


@pytest.mark.parametrize("custom_power", [False, True])
def test_update_power_value_attributes_and_private_fields(custom_power):
    sch, incoming = schematic(), definition()
    incoming.power_type = PowerType.global_
    sch.symbols[0].fields.append(Property(name="Secret", value="old", private=True))
    incoming.fields.extend(
        [
            Property(name="Secret", value="new", private=True),
            Property(name="Private new", value="not added", private=True),
        ]
    )
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(excluded_fields=["Reference"]),
        reset_custom_power_value=custom_power,
    )
    placed = update(sch, incoming, options).schematic.symbols[0]
    assert fields(placed)["Value"].value == ("Old" if custom_power else "Custom value")
    assert fields(placed)["Secret"].value == "new"
    assert "Private new" not in fields(placed)


def test_change_reference_prefix_and_autoplace_state():
    sch, incoming = schematic(), definition()
    sch.symbols[0].instances[0].paths.append(
        InstancePath(path="/root/other", reference="U007", unit=1)
    )
    incoming.fields[0].value = "IC"
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(selected_fields=["Reference"], excluded_fields=[]),
        reset_reference_prefix=True,
    )
    target = SymbolLibraryUpdateTarget(
        document_id="child", symbol_uuid=identity("symbol0"), instance_paths=["/root/other"]
    )
    plan = plan_symbol_library_updates(
        {"child": sch}, {incoming.name: incoming}, [target], options=options
    )
    result = apply_symbol_library_updates({"child": sch}, {incoming.name: incoming}, plan)
    placed = result.documents["child"].symbols[0]
    assert [path.reference for path in placed.instances[0].paths] == ["U1", "IC7"]
    assert fields(placed)["Reference"].value == "U1"
    assert list(result.changes[0].changed_instance_references.values()) == ["IC7"]


@pytest.mark.parametrize("placement", ["library", "isolated"])
def test_autoplace_policy_is_reported(placement):
    sch = schematic()
    sch.symbols[0].fields_autoplaced = True
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(reset_positions=True), field_placement=placement
    )
    result = update(sch, definition(), options)
    expected = (
        "isolated_field_placement" if placement == "isolated" else "native_autoplace_skipped"
    )
    assert expected in {issue.code for issue in result.issues}


def test_per_field_controls_and_new_fields():
    sch, incoming = schematic(), definition()
    incoming.fields.append(Property(name="Added", value="Library", position=(2, 3), visible=False))
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(
            overrides={"Footprint": SymbolFieldUpdatePolicy(reset_text=False)}, reset_text=True
        )
    )
    placed = update(sch, incoming, options).schematic.symbols[0]
    assert fields(placed)["Footprint"].value == "Package:Manual"
    assert fields(placed)["Supplier"].value == "Library supplier"
    assert fields(placed)["Added"].position == (52.8, 35.1)
    assert not fields(placed)["Added"].visible


@pytest.mark.parametrize("preserve", [False, True])
def test_pin_visibility_true_preserve_extension(preserve):
    sch, incoming = schematic(), definition()
    incoming.show_pin_names = incoming.show_pin_numbers = False
    result = update(
        sch,
        incoming,
        SymbolLibraryUpdateOptions(
            reset_pin_name_visibility=not preserve, reset_pin_number_visibility=not preserve
        ),
    )
    assert result.schematic.lib_symbols[0].show_pin_names == preserve
    assert result.schematic.lib_symbols[0].show_pin_numbers == preserve


def test_inheritance_is_flattened_without_mutating_inputs():
    sch, parent = schematic(), definition("Test:Parent")
    parent.keywords, parent.fp_filters = "parent", ["Parent*"]
    child = Symbol(
        name="Test:Old",
        extends="Test:Parent",
        keywords="child",
        fields=[Property(name="Value", value="Child"), Property(name="Supplier", value="")],
    )
    before = child.model_copy(deep=True)
    result = update_symbol_from_library(
        sch,
        symbol_uuid=identity("symbol0"),
        library_symbols={"Test:Old": child, "Test:Parent": parent},
    )
    cached = result.schematic.lib_symbols[0]
    assert cached.extends is None and cached.keywords == "child"
    assert cached.fp_filters == ["Parent*"]
    assert cached.units == parent.renamed("Test:Old").units
    assert child == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "invalid_id",
        "missing_parent",
        "cycle",
        "unknown_path",
        "duplicate_target",
        "unknown_symbol",
    ],
)
def test_invalid_targets_do_not_mutate_inputs(fault):
    sch, incoming = schematic(), definition()
    before = sch.model_copy(deep=True)
    targets = [SymbolLibraryUpdateTarget(document_id="main", symbol_uuid=identity("symbol0"))]
    libraries = {incoming.name: incoming}
    if fault == "missing":
        libraries = {}
    elif fault == "invalid_id":
        targets[0].new_lib_id = "bad"
    elif fault == "missing_parent":
        incoming.extends = "Missing"
    elif fault == "cycle":
        incoming.extends = incoming.name
    elif fault == "unknown_path":
        targets[0].instance_paths = ["/absent"]
    elif fault == "duplicate_target":
        targets.append(targets[0])
    elif fault == "unknown_symbol":
        targets[0].symbol_uuid = identity("absent")
    if fault == "duplicate_target":
        with pytest.raises(ValueError, match="only once"):
            plan_symbol_library_updates({"main": sch}, libraries, targets)
    else:
        plan = plan_symbol_library_updates({"main": sch}, libraries, targets)
        assert not plan.changes
        assert any(issue.severity == "error" for issue in plan.issues)
    assert sch == before


@pytest.mark.parametrize("edit", ["schematic", "library", "preview"])
def test_stale_or_edited_preview_is_rejected(edit):
    sch, incoming = schematic(), definition()
    documents, libraries = {"main": sch}, {incoming.name: incoming}
    plan = plan_symbol_library_updates(
        documents, libraries, select_symbol_library_targets(documents)
    )
    if edit == "schematic":
        sch.symbols[0].position = (1, 1)
    elif edit == "library":
        incoming.keywords = "changed"
    else:
        plan.changes.clear()
    with pytest.raises(ValueError, match="changed"):
        apply_symbol_library_updates(documents, libraries, plan)


def test_plan_round_trip_and_deterministic_apply():
    sch, incoming = schematic(), definition(numbers=("1", "2", "3"))
    documents, libraries = {"main": sch}, {incoming.name: incoming}
    plan = plan_symbol_library_updates(
        documents, libraries, select_symbol_library_targets(documents)
    )
    restored = type(plan).model_validate_json(plan.model_dump_json())
    first = apply_symbol_library_updates(documents, libraries, restored)
    second = apply_symbol_library_updates(documents, libraries, restored)
    assert first == second
    text = first.schematic.to_string()
    assert Schematic.from_string(text).to_string() == text


def test_reference_selection_deduplicates_shared_sheet_paths():
    sch = schematic(copies=2)
    sch.symbols[0].instances[0].paths.extend(
        [
            InstancePath(path="/a", reference="R9", unit=1),
            InstancePath(path="/b", reference="R10", unit=1),
        ]
    )
    targets = select_symbol_library_targets({"main": sch}, reference_pattern="r*")
    assert len(targets) == 1
    assert targets[0].instance_paths == ["/a", "/b"]
    assert not select_symbol_library_targets({"main": sch}, value_pattern="missing")
    assert not select_symbol_library_targets({"main": sch}, lib_id="Different:Part")


def test_dialog_defaults_differ_by_operation():
    update_options = SymbolLibraryUpdateOptions.gui_defaults("update")
    change_options = SymbolLibraryUpdateOptions.gui_defaults("change")
    assert not update_options.fields.reset_positions and change_options.fields.reset_positions
    assert not update_options.reset_alternate_pins and change_options.reset_alternate_pins
    assert not update_options.attributes.in_bom and change_options.attributes.in_bom
    assert update_options.fields.excluded_fields == ["Reference", "Value"]


@pytest.mark.parametrize("old_hide", ["property", "effects"])
@pytest.mark.parametrize("new_hide", [None, "property", "effects"])
@pytest.mark.parametrize("reset_style", [False, True])
def test_visibility_reset_handles_both_hide_forms(old_hide, new_hide, reset_style):
    sch, incoming = schematic(), definition()
    old = fields(sch.symbols[0])["Supplier"]
    old.attributes = TextAttributes(visible=old_hide != "effects")
    old.visible = old_hide != "property"
    new = next(field for field in incoming.fields if field.name == "Supplier")
    new.attributes = TextAttributes(visible=new_hide != "effects")
    new.visible = new_hide != "property"
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(reset_visibility=True, reset_effects=reset_style)
    )
    changed = fields(update(sch, incoming, options).schematic.symbols[0])["Supplier"]
    assert (changed.visible and changed.attributes.visible) == (new_hide is None)


def test_inherited_exclusions_come_from_immediate_parent():
    sch, root = schematic(), definition("Test:Root")
    root.in_bom = root.on_board = root.in_pos_files = True
    parent = Symbol(
        name="Test:Parent",
        extends=root.name,
        in_bom=False,
        on_board=False,
        in_pos_files=False,
        exclude_from_sim=True,
    )
    child = Symbol(name="Test:Old", extends=parent.name)
    result = update_symbol_from_library(
        sch,
        symbol_uuid=identity("symbol0"),
        library_symbols={root.name: root, parent.name: parent, child.name: child},
        options=SymbolLibraryUpdateOptions.atomic_part(),
    )
    placed = result.schematic.symbols[0]
    assert (
        placed.exclude_from_sim
        and not placed.in_bom
        and not placed.on_board
        and not placed.in_pos_files
    )


def test_ambiguous_pin_reuse_is_reported_with_one_old_candidate():
    sch = schematic()
    incoming = definition(numbers=("1", "3", "4"))
    result = update(sch, incoming)
    assert "pin_identity_tie" in {issue.code for issue in result.issues}
    assert result.changes[0].before_pins == sch.symbols[0].pins
    assert result.changes[0].after_pins == result.schematic.symbols[0].pins


def test_alternate_cleanup_uses_matched_instance_units_and_body_style():
    sch, incoming = schematic(units=2), definition(units=2)
    incoming.units[1].items[0].number = "9"
    sch.lib_symbols[0].units[1].items[0].number = "9"
    sch.symbols[0].pins[2].number = "9"
    sch.symbols[0].pins[2].alternate = "STALE"
    incoming.units.append(
        SymbolUnit.for_symbol(incoming.name, body_style_index=2, items=[pin("100")])
    )
    result = update(sch, incoming, SymbolLibraryUpdateOptions(reset_alternate_pins=True))
    assert (
        next(pin for pin in result.schematic.symbols[0].pins if pin.number == "9").alternate
        == "STALE"
    )
    assert "100" not in {pin.number for pin in result.schematic.symbols[0].pins}


def test_input_pin_order_is_part_of_preview_guard():
    sch, incoming = schematic(), definition(numbers=("3", "4"))
    documents, libraries = {"main": sch}, {incoming.name: incoming}
    plan = plan_symbol_library_updates(
        documents, libraries, select_symbol_library_targets(documents)
    )
    sch.symbols[0].pins.reverse()
    with pytest.raises(ValueError, match="changed"):
        apply_symbol_library_updates(documents, libraries, plan)


def test_cache_reuses_matching_suffix_for_another_selected_symbol():
    sch, incoming = schematic(copies=3), definition()
    incoming.keywords = "new"
    first = update(sch, incoming)
    second = update_symbol_from_library(
        first.schematic, symbol_uuid=identity("symbol1"), library_symbols={incoming.name: incoming}
    )
    assert second.schematic.symbols[1].lib_name == "Old_1"
    assert len(second.schematic.lib_symbols) == 2
    assert second.schematic.symbols[2] == sch.symbols[2]


def test_batch_results_require_explicit_document_selection():
    documents = {"a": schematic(), "b": schematic()}
    incoming = definition()
    plan = plan_symbol_library_updates(
        documents, {incoming.name: incoming}, select_symbol_library_targets(documents)
    )
    result = apply_symbol_library_updates(documents, {incoming.name: incoming}, plan)
    with pytest.raises(ValueError, match="Select a document"):
        _ = result.schematic
    assert len(result.changes) == 2


@pytest.mark.parametrize("common_only", [False, True])
def test_empty_or_common_only_definition_has_one_unit(common_only):
    sch, incoming = schematic(), definition()
    incoming.units = (
        [SymbolUnit.for_symbol(incoming.name, unit_index=0, items=[pin("1")])]
        if common_only
        else []
    )
    result = update(sch, incoming)
    assert len(result.changes) == 1
    assert result.changes[0].after_unit_count == 1


@pytest.mark.parametrize(
    "bad_id",
    [
        "Bare",
        ":Name",
        "Lib:",
        "Lib:Bad:Name",
        "Lib:Bad<Name",
        "Lib:Bad>name",
        'Lib:Bad"Name',
        "Lib:Bad\\Name",
        "Bad\\Lib:Name",
        "Lib:Bad\tName",
    ],
)
def test_native_identifier_rejections(bad_id):
    sch = schematic()
    result = change_symbol(
        sch,
        symbol_uuid=identity("symbol0"),
        new_lib_id=bad_id,
        library_symbols={bad_id: definition(bad_id)},
    )
    assert not result.changes
    assert result.issues[0].code == "invalid_library_id"


def test_reference_reset_display_uses_selected_instance_reference():
    sch, incoming = schematic(), definition()
    sch.symbols[0].instances[0].paths.append(
        InstancePath(path="/root/other", reference="U007", unit=1)
    )
    incoming.fields[0].value = "IC"
    options = SymbolLibraryUpdateOptions(
        fields=SymbolFieldUpdateOptions(selected_fields=["Reference"], excluded_fields=[]),
        reset_reference_prefix=True,
    )
    target = SymbolLibraryUpdateTarget(
        document_id="child",
        symbol_uuid=identity("symbol0"),
        instance_paths=["/root/other"],
        display_instance_path="/root/other",
    )
    plan = plan_symbol_library_updates(
        {"child": sch}, {incoming.name: incoming}, [target], options=options
    )
    result = apply_symbol_library_updates({"child": sch}, {incoming.name: incoming}, plan)
    placed = result.documents["child"].symbols[0]
    assert placed.instances[0].paths[1].reference == "IC7"
    assert fields(placed)["Reference"].value == "IC7"
