"""Constructed behavior tests. Native GUI golden pairs remain pending."""

from uuid import NAMESPACE_URL, uuid5

import pytest

from eedesigncore import (
    Board,
    Footprint,
    InstancePath,
    InstanceProject,
    Label,
    LabelKind,
    Line,
    Pad,
    Paper,
    Pin,
    PinElectricalType,
    PinShape,
    PlacedSymbol,
    Property,
    Schematic,
    Symbol,
    SymbolUnit,
    TextAttributes,
)
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.models import TrackSegment
from eedesigncore.pcb.update_from_schematic import (
    apply_update_pcb_from_schematic,
    plan_update_pcb_from_schematic,
    update_pcb_from_schematic,
)
from eedesigncore.project.update_models import PcbUpdateOptions, SchematicUpdateOptions
from eedesigncore.sch.update_from_pcb import (
    apply_update_schematic_from_pcb,
    plan_update_schematic_from_pcb,
    update_schematic_from_pcb,
)


def uid(name):
    return str(uuid5(NAMESPACE_URL, "design-update-test/" + name))


def value(fields, name):
    return next(field.value for field in fields if field.name == name)


def design():
    definition = Symbol(
        name="Test:Part",
        fields=[Property(name="Reference", value="U"), Property(name="Value", value="Part")],
        units=[
            SymbolUnit.for_symbol(
                "Test:Part",
                items=[
                    Pin(
                        number="1",
                        name="A",
                        position=(0, 0),
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                    ),
                    Pin(
                        number="2",
                        name="B",
                        position=(2.54, 0),
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                    ),
                ],
            )
        ],
    )
    symbol = PlacedSymbol(
        uuid=uid("symbol"),
        lib_id=definition.name,
        position=(25.4, 25.4),
        fields=[
            Property(name="Reference", value="U1"),
            Property(name="Value", value="Schematic value"),
            Property(name="Footprint", value="Test:Package"),
            Property(name="Supplier", value="New supplier"),
        ],
        instances=[
            InstanceProject(
                name="test", paths=[InstancePath(path="/" + uid("root"), reference="U1", unit=1)]
            )
        ],
    )
    sch = Schematic(
        paper=Paper(),
        uuid=uid("root"),
        lib_symbols=[definition],
        symbols=[symbol],
        labels=[
            Label(
                kind=LabelKind.global_,
                content="NET_A",
                position=(25.4, 25.4),
                shape="passive",
                uuid=uid("labelA"),
            ),
            Label(
                kind=LabelKind.global_,
                content="NET_B",
                position=(27.94, 25.4),
                shape="passive",
                uuid=uid("labelB"),
            ),
        ],
    )
    fp = Footprint(
        uuid=uid("footprint"),
        fp_id="Test:Package",
        # KiCad form: netlist sheet path without the root sheet, plus the symbol uuid.
        path="/" + uid("symbol"),
        at=FootprintPlacement(position=(50, 60), angle=90),
        properties=[
            Property(name="Reference", value="U1", id=uid("reference")),
            Property(name="Value", value="Board value", id=uid("value")),
            Property(
                name="Supplier",
                value="Old supplier",
                position=(3, 4),
                attributes=TextAttributes(bold=True),
            ),
        ],
        pads=[
            Pad(
                number="1",
                id=uid("pad1"),
                position=(0, 0),
                size=(1, 1),
                layers=["F.Cu"],
                net_name="NET_A",
            ),
            Pad(
                number="2",
                id=uid("pad2"),
                position=(2.54, 0),
                size=(1, 1),
                layers=["F.Cu"],
                net_name="NET_B",
            ),
        ],
    )
    return Board(footprints=[fp]), sch


def replacement():
    return Footprint(
        fp_id="Test:New",
        properties=[
            Property(name="Reference", value="REF**"),
            Property(name="Value", value="New"),
        ],
        pads=[
            Pad(number="1", position=(0, 0), size=(1, 1), layers=["F.Cu"]),
            Pad(number="2", position=(2.54, 0), size=(1, 1), layers=["F.Cu"]),
        ],
    )


def test_forward_preserves_inputs_and_field_formatting_and_roundtrips():
    board, sch = design()
    before_board, before_sch = board.model_dump_json(), sch.model_dump_json()
    result = update_pcb_from_schematic(board, sch)
    assert board.model_dump_json() == before_board and sch.model_dump_json() == before_sch
    fp = result.board.footprints[0]
    assert value(fp.properties, "Value") == "Schematic value"
    supplier = next(field for field in fp.properties if field.name == "Supplier")
    assert (
        supplier.value == "New supplier"
        and supplier.position == (3, 4)
        and supplier.attributes.bold
    )
    text = result.board.to_string()
    assert Board.from_string(text).to_string() == text


def test_forward_addition_requires_placement_and_replays_deterministically():
    _, sch = design()
    definition = replacement()
    definition.fp_id = "Test:Package"
    libraries = {definition.fp_id: definition}
    empty = Board()
    plan = plan_update_pcb_from_schematic(empty, sch, library_footprints=libraries)
    assert "missing_placement" in {item.code for item in plan.blockers}
    plan = plan_update_pcb_from_schematic(
        empty,
        sch,
        library_footprints=libraries,
        placements={"U1": FootprintPlacement(position=(100, 200), angle=90)},
    )
    result = apply_update_pcb_from_schematic(empty, sch, plan, library_footprints=libraries)
    assert result == apply_update_pcb_from_schematic(
        empty, sch, plan, library_footprints=libraries
    )
    assert result.board.footprints[0].at.position == (100, 200)
    assert result.board.footprints[0].at.angle == 90
    assert not empty.footprints


@pytest.mark.parametrize("direction", ["forward", "reverse"])
@pytest.mark.parametrize("edit", ["board", "source", "plan"])
def test_preview_guards_reject_changed_inputs_and_results(direction, edit):
    board, sch = design()
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    applier = (
        apply_update_pcb_from_schematic
        if direction == "forward"
        else apply_update_schematic_from_pcb
    )
    plan = planner(board, sch)
    if edit == "board":
        board.footprints[0].locked = True
    elif edit == "source":
        sch.symbols[0].position = (1, 2)
    else:
        plan.changes.clear()
    with pytest.raises(ValueError, match="changed"):
        applier(board, sch, plan)


def test_forward_removal_fields_is_independent_of_update_fields():
    board, sch = design()
    board.footprints[0].properties.append(Property(name="Obsolete", value="old"))
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(update_fields=False, remove_extra_fields=True)
    )
    assert "Obsolete" not in {field.name for field in result.board.footprints[0].properties}
    assert value(result.board.footprints[0].properties, "Supplier") == "Old supplier"


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_reference_relink_is_case_insensitive(direction):
    board, sch = design()
    board.footprints[0].path = "/unmatched"
    board.footprints[0].properties[0].value = "u1"
    if direction == "forward":
        plan = plan_update_pcb_from_schematic(
            board, sch, options=PcbUpdateOptions(match_by_reference=True)
        )
        assert not plan.blockers
        assert any(change.kind == "update_footprint" for change in plan.changes)
    else:
        plan = plan_update_schematic_from_pcb(
            board,
            sch,
            options=SchematicUpdateOptions(match_by_reference=True, update_net_names=False),
        )
        assert not any(issue.code == "unmatched_symbol" for issue in plan.issues)
        assert any(change.kind == "update_symbol" for change in plan.changes)


def test_backside_replacement_preserves_lock_placement_and_root_identity():
    board, sch = design()
    old = board.footprints[0]
    old.layer, old.locked = "B.Cu", True
    for pad in old.pads:
        pad.layers = ["B.Cu"]
    next(field for field in sch.symbols[0].fields if field.name == "Footprint").value = "Test:New"
    libraries = {"Test:New": replacement()}
    blocked = plan_update_pcb_from_schematic(board, sch, library_footprints=libraries)
    assert "locked_replacement" in {issue.code for issue in blocked.blockers}
    result = update_pcb_from_schematic(
        board, sch, library_footprints=libraries, options=PcbUpdateOptions(override_locks=True)
    )
    new = result.board.footprints[0]
    assert new.layer == "B.Cu" and new.locked and new.uuid == old.uuid and new.at == old.at
    assert [pad.id for pad in new.pads] == [pad.id for pad in old.pads]
    assert all("B.Cu" in pad.layers for pad in new.pads)


def test_new_backside_fields_use_back_fabrication_layer():
    board, sch = design()
    board.footprints[0].layer = "B.Cu"
    sch.symbols[0].fields.append(Property(name="Added", value="text"))
    result = update_pcb_from_schematic(board, sch)
    added = next(field for field in result.board.footprints[0].properties if field.name == "Added")
    assert added.layer == "B.Fab"
    assert not added.visible


def test_routed_contact_movement_blocks_replacement():
    board, sch = design()
    board.tracks.append(TrackSegment(start=(50, 60), end=(55, 60), net="NET_A"))
    next(field for field in sch.symbols[0].fields if field.name == "Footprint").value = "Test:New"
    new = replacement()
    new.pads[0].position = (5, 5)
    plan = plan_update_pcb_from_schematic(board, sch, library_footprints={"Test:New": new})
    assert "routed_contact" in {issue.code for issue in plan.blockers}
    with pytest.raises(ValueError, match="blockers"):
        apply_update_pcb_from_schematic(board, sch, plan, library_footprints={"Test:New": new})


def test_uniform_copper_rename_keeps_track_geometry():
    board, sch = design()
    board.tracks.append(TrackSegment(start=(1, 2), end=(3, 4), net="NET_A"))
    sch.labels[0].content = "RENAMED"
    result = update_pcb_from_schematic(board, sch)
    assert result.board.tracks[0].net == "RENAMED"
    assert result.board.tracks[0].start == board.tracks[0].start
    assert result.board.footprints[0].pads[0].net_name == "RENAMED"


def test_reverse_fields_preserve_variables_style_and_roundtrip():
    board, sch = design()
    sch.symbols[0].fields.append(Property(name="Missing", value="remove"))
    sch.symbols[0].fields.append(Property(name="Variable", value="${PROJECT_TITLE}"))
    board.footprints[0].properties.append(Property(name="Variable", value="literal"))
    board.footprints[0].properties.append(Property(name="Added", value="PCB text"))
    before_board, before_sch = board.model_dump_json(), sch.model_dump_json()
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert board.model_dump_json() == before_board and sch.model_dump_json() == before_sch
    symbol = result.schematic.symbols[0]
    assert value(symbol.fields, "Value") == "Board value"
    assert value(symbol.fields, "Variable") == "${PROJECT_TITLE}"
    assert "Missing" not in {field.name for field in symbol.fields}
    added = next(field for field in symbol.fields if field.name == "Added")
    assert added.position == symbol.position and not added.visible
    text = result.schematic.to_string()
    assert Schematic.from_string(text).to_string() == text


def test_reverse_reference_collision_with_unmatched_symbol_blocks():
    board, sch = design()
    other = sch.symbols[0].model_copy(deep=True)
    other.uuid = uid("other-symbol")
    other.position = (100, 100)
    other.fields[0].value = "U2"
    other.instances[0].paths[0].reference = "U2"
    sch.symbols.append(other)
    board.footprints[0].properties[0].value = "U2"
    plan = plan_update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert "duplicate_reference" in {issue.code for issue in plan.blockers}


def test_reverse_simple_global_label_rename():
    board, sch = design()
    board.footprints[0].pads[0].net_name = "RENAMED"
    result = update_schematic_from_pcb(board, sch)
    assert result.schematic.labels[0].content == "RENAMED"
    assert sch.labels[0].content == "NET_A"


def test_forward_multiunit_aggregation_updates_one_footprint():
    board, sch = design()
    definition = sch.lib_symbols[0]
    second_unit = SymbolUnit.for_symbol(
        definition.name, unit_index=2, items=[definition.units[0].items.pop()]
    )
    definition.units.append(second_unit)
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid = uid("unit2")
    second.unit = 2
    second.instances[0].paths[0].unit = 2
    sch.symbols.append(second)
    result = update_pcb_from_schematic(board, sch)
    assert len(result.board.footprints) == 1
    assert {pad.net_name for pad in result.board.footprints[0].pads} == {"NET_A", "NET_B"}


def test_multiunit_metadata_conflict_blocks():
    board, sch = design()
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid = uid("unit2")
    second.unit = 2
    second.instances[0].paths[0].unit = 2
    second.fields[1].value = "Conflicting value"
    sch.lib_symbols[0].units.append(SymbolUnit.for_symbol("Test:Part", unit_index=2))
    sch.symbols.append(second)
    plan = plan_update_pcb_from_schematic(board, sch)
    assert "unit_conflict" in {issue.code for issue in plan.blockers}


def test_reverse_repeated_sheet_field_conflict_blocks(tmp_path):
    from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode

    board, child = design()
    root = Schematic(uuid=uid("hierarchyroot"), paper=Paper())
    root_path = "/" + root.uuid
    paths = [root_path + "/" + uid("sheet1"), root_path + "/" + uid("sheet2")]
    child.symbols[0].instances[0].paths = [
        InstancePath(path=paths[0], reference="U1", unit=1),
        InstancePath(path=paths[1], reference="U2", unit=1),
    ]
    children = [
        SheetNode(path=tmp_path / "child.kicad_sch", schematic=child, name="child", kiid_path=path)
        for path in paths
    ]
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "root.kicad_sch",
                schematic=root,
                name="root",
                kiid_path=root_path,
                children=children,
            )
        ],
    )
    board.footprints[0].path = paths[0] + "/" + uid("symbol")
    other = board.footprints[0].model_copy(deep=True)
    other.uuid = uid("second-footprint")
    other.path = paths[1] + "/" + uid("symbol")
    other.properties[0].value = "U2"
    other.properties[1].value = "Different PCB value"
    board.footprints.append(other)
    plan = plan_update_schematic_from_pcb(
        board, hierarchy, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert "shared_field_conflict" in {issue.code for issue in plan.blockers}
    with pytest.raises(ValueError, match="blockers"):
        apply_update_schematic_from_pcb(board, hierarchy, plan)


def test_unmatched_board_only_and_locked_footprints_survive_deletion():
    board, sch = design()
    for index, board_only in enumerate([True, False]):
        extra = Footprint(
            uuid=uid("extra" + str(index)), fp_id="Mechanical:Extra", locked=not board_only
        )
        extra.attributes.board_only = board_only
        board.footprints.append(extra)
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(delete_extra_footprints=True)
    )
    assert len(result.board.footprints) == 3
    assert "locked_deletion" in {issue.code for issue in result.issues}


def test_reverse_reference_variable_is_preserved():
    board, sch = design()
    sch.symbols[0].fields[0].value = "${REFERENCE_SOURCE}"
    board.footprints[0].properties[0].value = "U9"
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert value(result.schematic.symbols[0].fields, "Reference") == "${REFERENCE_SOURCE}"


def test_reverse_multiunit_value_updates_every_unit():
    board, sch = design()
    definition = sch.lib_symbols[0]
    definition.units.append(
        SymbolUnit.for_symbol(
            definition.name, unit_index=2, items=[definition.units[0].items.pop()]
        )
    )
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid = uid("unit2")
    second.unit = 2
    second.instances[0].paths[0].unit = 2
    sch.symbols.append(second)
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert [value(symbol.fields, "Value") for symbol in result.schematic.symbols] == [
        "Board value",
        "Board value",
    ]


def test_forward_group_transfer_preserves_unrelated_members():
    from eedesigncore.common.pcb_family import PcbGroup
    from eedesigncore.sch.models import Group

    board, sch = design()
    source_group = uid("source-group")
    sch.groups.append(
        Group(
            uuid=source_group,
            name="Source group",
            lib_id="Blocks:Example",
            members=[sch.symbols[0].uuid],
        )
    )
    unrelated = [uid("track-a"), uid("track-b")]
    board.tracks = [
        TrackSegment(uuid=identity, start=(0, index), end=(1, index))
        for index, identity in enumerate(unrelated)
    ]
    board.groups.append(
        PcbGroup(
            id=uid("old-group"), name="Keep group", members=[board.footprints[0].uuid, *unrelated]
        )
    )
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(transfer_symbol_groups=True)
    )
    old = next(group for group in result.board.groups if group.name == "Keep group")
    assert old.members == unrelated
    new = next(group for group in result.board.groups if group.id == source_group)
    assert new.members == [board.footprints[0].uuid]
    assert new.name == "Source group" and new.lib_id == "Blocks:Example"
    assert result.board.tracks == board.tracks


def test_missing_pin_pad_blocks_instead_of_partial_apply():
    board, sch = design()
    board.footprints[0].pads.pop()
    plan = plan_update_pcb_from_schematic(board, sch)
    assert "missing_pad" in {issue.code for issue in plan.blockers}
    with pytest.raises(ValueError, match="blockers"):
        apply_update_pcb_from_schematic(board, sch, plan)


def test_reverse_net_split_is_an_explicit_blocker():
    board, sch = design()
    sch.labels[1].content = "NET_A"
    sch.lines.append(Line(start=(25.4, 25.4), end=(27.94, 25.4), uuid=uid("wire")))
    plan = plan_update_schematic_from_pcb(board, sch)
    assert "pin_swap_required" in {issue.code for issue in plan.blockers}


def test_replacement_library_change_invalidates_preview():
    board, sch = design()
    next(field for field in sch.symbols[0].fields if field.name == "Footprint").value = "Test:New"
    libraries = {"Test:New": replacement()}
    plan = plan_update_pcb_from_schematic(board, sch, library_footprints=libraries)
    libraries["Test:New"].pads[0].size = (2, 2)
    with pytest.raises(ValueError, match="changed"):
        apply_update_pcb_from_schematic(board, sch, plan, library_footprints=libraries)


def test_reverse_disconnected_same_name_labels_update_only_requested_island():
    board, sch = design()
    sch.labels[1].content = "NET_A"
    result = update_schematic_from_pcb(board, sch)
    assert [label.content for label in result.schematic.labels] == ["NET_A", "NET_B"]
    assert [label.content for label in sch.labels] == ["NET_A", "NET_A"]


def test_reverse_relink_returns_detached_board_with_new_path():
    board, sch = design()
    expected = board.footprints[0].path
    board.footprints[0].path = "/old-association"
    result = update_schematic_from_pcb(
        board,
        sch,
        options=SchematicUpdateOptions(match_by_reference=True, update_net_names=False),
    )
    assert result.board.footprints[0].path == expected
    assert board.footprints[0].path == "/old-association"
    text = result.board.to_string()
    assert Board.from_string(text).to_string() == text


def test_reverse_unnamed_contact_adds_local_label():
    board, sch = design()
    sch.labels.pop(0)
    board.footprints[0].pads[0].net_name = "NEW_LOCAL_NAME"
    result = update_schematic_from_pcb(board, sch)
    added = next(label for label in result.schematic.labels if label.content == "NEW_LOCAL_NAME")
    assert added.kind == LabelKind.local
    assert added.position == (25.4, 25.4)
    assert len(sch.labels) == 1


def test_board_only_pad_prevents_partial_copper_net_rename():
    board, sch = design()
    extra = Footprint(
        uuid=uid("board-only"),
        pads=[Pad(number="1", id=uid("extra-pad"), layers=["F.Cu"], net_name="NET_A")],
    )
    extra.attributes.board_only = True
    board.footprints.append(extra)
    board.tracks.append(TrackSegment(start=(1, 2), end=(3, 4), net="NET_A"))
    sch.labels[0].content = "RENAMED"
    plan = plan_update_pcb_from_schematic(board, sch)
    assert "copper_split" in {issue.code for issue in plan.blockers}


def test_replacement_preserves_pad_nets_when_net_updates_disabled():
    board, sch = design()
    next(field for field in sch.symbols[0].fields if field.name == "Footprint").value = "Test:New"
    board.footprints[0].pads[0].net_name = "BOARD_ONLY_NET"
    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints={"Test:New": replacement()},
        options=PcbUpdateOptions(update_pad_nets=False),
    )
    assert [pad.net_name for pad in result.board.footprints[0].pads] == ["BOARD_ONLY_NET", "NET_B"]


def test_disabled_replacement_retains_geometry_and_updates_nets():
    board, sch = design()
    next(field for field in sch.symbols[0].fields if field.name == "Footprint").value = "Test:New"
    sch.labels[0].content = "RENAMED"
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(replace_footprints=False)
    )
    assert len(result.board.footprints) == 1
    assert result.board.footprints[0].fp_id == "Test:Package"
    assert result.board.footprints[0].pads[0].position == board.footprints[0].pads[0].position
    assert result.board.footprints[0].pads[0].net_name == "RENAMED"


@pytest.mark.parametrize("count, expected", [(2, [2, 1]), (3, [3, 1, 2])])
def test_reverse_unit_permutation_uses_native_inverse_cycle(count, expected):
    board, sch = design()
    template = sch.symbols[0].model_copy(deep=True)
    definition = sch.lib_symbols[0]
    definition.units = []
    sch.symbols = []
    sch.labels = []
    board.footprints[0].pads = []
    for index in range(count):
        number = str(index + 1)
        definition.units.append(
            SymbolUnit.for_symbol(
                definition.name,
                unit_index=index + 1,
                items=[
                    Pin(
                        number=number,
                        name=number,
                        position=(0, 0),
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                    )
                ],
            )
        )
        placed = template.model_copy(deep=True)
        if index:
            placed.uuid = uid("cycle-unit" + number)
        placed.position = (25.4 + index * 20, 25.4)
        placed.unit = index + 1
        placed.instances[0].paths[0].unit = index + 1
        sch.symbols.append(placed)
        sch.labels.append(
            Label(
                kind=LabelKind.global_,
                content="NET_" + number,
                position=placed.position,
                shape="passive",
                uuid=uid("cycle-label" + number),
            )
        )
        board.footprints[0].pads.append(
            Pad(
                number=number,
                id=uid("cycle-pad" + number),
                layers=["F.Cu"],
                net_name="NET_" + str((index + 1) % count + 1),
            )
        )
    result = update_schematic_from_pcb(board, sch)
    assert [symbol.unit for symbol in result.schematic.symbols] == expected
    assert [symbol.instances[0].paths[0].unit for symbol in result.schematic.symbols] == expected
    assert [symbol.position for symbol in result.schematic.symbols] == [
        symbol.position for symbol in sch.symbols
    ]
    assert [value(symbol.fields, "Value") for symbol in result.schematic.symbols] == [
        "Schematic value"
    ] * count
    assert result.schematic.labels == sch.labels
    assert len([change for change in result.changes if change.kind == "swap_unit"]) == count


@pytest.mark.parametrize("allow", [False, True])
def test_reverse_pin_swap_gate_preserves_contacts_pin_ids_and_shared_cache(allow):
    from eedesigncore import PlacedSymbolPin

    board, sch = design()
    sch.symbols[0].pins = [
        PlacedSymbolPin(number=number, uuid=uid("placed-pin" + number)) for number in ["1", "2"]
    ]
    unused = sch.symbols[0].model_copy(deep=True)
    unused.uuid = uid("untouched-symbol")
    unused.position = (100, 100)
    unused.fields[0].value = "U2"
    unused.instances[0].paths[0].reference = "U2"
    unused.on_board = False
    sch.symbols.append(unused)
    board.footprints[0].pads[0].net_name = "NET_B"
    board.footprints[0].pads[1].net_name = "NET_A"
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(allow_unconstrained_pin_swaps=allow)
    )
    symbol = result.schematic.symbols[0]
    assert symbol.pins == sch.symbols[0].pins
    original_positions = [pin.position for pin in sch.lib_symbols[0].units[0].items]
    changed = next(
        definition
        for definition in result.schematic.lib_symbols
        if definition.name == (symbol.lib_name or symbol.lib_id)
    )
    positions = [pin.position for pin in changed.units[0].items]
    assert set(positions) == set(original_positions)
    if allow:
        assert positions == list(reversed(original_positions))
        assert symbol.lib_name is not None
        assert result.schematic.symbols[1] == unused
        assert (
            next(
                definition
                for definition in result.schematic.lib_symbols
                if definition.name == "Test:Part"
            )
            == sch.lib_symbols[0]
        )
        assert result.schematic.labels == sch.labels
        assert any(change.kind == "swap_pins" for change in result.changes)
    else:
        assert positions == original_positions
        assert [label.content for label in result.schematic.labels] == ["NET_B", "NET_A"]
        assert not any(change.kind == "swap_pins" for change in result.changes)


def test_shared_pin_swap_uses_explicit_label_fallback():
    board, sch = design()
    sch.symbols[0].instances[0].paths.append(
        InstancePath(path="/another-project-path", reference="U2", unit=1)
    )
    board.footprints[0].pads[0].net_name = "NET_B"
    board.footprints[0].pads[1].net_name = "NET_A"
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(allow_unconstrained_pin_swaps=True)
    )
    assert "shared_pin_swap_fallback" in {issue.code for issue in result.issues}
    assert not any(change.kind == "swap_pins" for change in result.changes)
    assert [label.content for label in result.schematic.labels] == ["NET_B", "NET_A"]


def test_no_connect_duplicate_pads_receive_distinct_names():
    from eedesigncore import NoConnect

    board, sch = design()
    sch.labels.pop(0)
    sch.no_connects.append(NoConnect(position=(25.4, 25.4), uuid=uid("no-connect")))
    duplicate = board.footprints[0].pads[0].model_copy(deep=True)
    duplicate.id = uid("duplicate-no-connect-pad")
    board.footprints[0].pads.append(duplicate)
    result = update_pcb_from_schematic(board, sch)
    names = [pad.net_name for pad in result.board.footprints[0].pads if pad.number == "1"]
    assert len(set(names)) == 2
    assert all(name and name.startswith("unconnected-") for name in names)
    assert any(name.endswith("_1") for name in names)


def test_noncopper_pad_disconnects_in_forward_update():
    board, sch = design()
    board.footprints[0].pads[0].layers = ["F.Mask"]
    result = update_pcb_from_schematic(board, sch)
    assert result.board.footprints[0].pads[0].net_name is None


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_schematic_variant_requires_explicit_selection(direction):
    from eedesigncore import InstanceVariant

    board, sch = design()
    sch.symbols[0].instances[0].paths[0].variants.append(
        InstanceVariant(name="Alternate", dnp=True)
    )
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    plan = planner(board, sch)
    assert "assembly_variants" in {issue.code for issue in plan.blockers}


def test_library_metadata_updates_independently_of_field_checkbox():
    board, sch = design()
    definition = sch.lib_symbols[0]
    definition.fp_filters = ["Test:*"]
    definition.duplicate_pin_numbers_are_jumpers = True
    definition.jumper_pin_groups = [["1", "2"]]
    sch.symbols[0].fields.append(Property(name="Component Class", value="Power"))
    result = update_pcb_from_schematic(board, sch, options=PcbUpdateOptions(update_fields=False))
    footprint = result.board.footprints[0]
    assert footprint.fp_filters == "Test:*"
    assert footprint.duplicate_pad_numbers_are_jumpers
    assert footprint.jumper_pad_groups == [["1", "2"]]
    assert footprint.component_classes.classes == ["Power"]
    assert footprint.unit_info.units[0].pins == ["1", "2"]
    assert "Component Class" not in {field.name for field in footprint.properties}


def test_derived_symbol_footprint_filter_overrides_root_filter():
    board, sch = design()
    root = sch.lib_symbols[0]
    root.name = "Test:Parent"
    root.fp_filters = ["Parent:*"]
    sch.lib_symbols.append(Symbol(name="Test:Part", extends="Test:Parent", fp_filters=["Child:*"]))
    result = update_pcb_from_schematic(board, sch)
    assert result.board.footprints[0].fp_filters == "Child:*"


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_real_project_hierarchy_preview_hashes_project_settings(direction):
    from pathlib import Path

    from eedesigncore.project.hierarchy import load_hierarchy

    fixture = Path(__file__).parents[1] / "fixtures/schematic_preparation/root.kicad_pro"
    hierarchy = load_hierarchy(fixture)
    assert hierarchy.project is not None
    before = hierarchy.project.to_string()
    board = Board()
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    applier = (
        apply_update_pcb_from_schematic
        if direction == "forward"
        else apply_update_schematic_from_pcb
    )
    plan = planner(board, hierarchy)
    assert hierarchy.project.to_string() == before
    assert plan.source_signature
    hierarchy.project.root.set("text_variables", {"QA_CHANGED": "new value"})
    with pytest.raises(ValueError, match="changed"):
        applier(board, hierarchy, plan)


@pytest.mark.parametrize("owner", ["group", "generator"])
def test_delete_extra_footprint_blocks_external_group_pad_reference(owner):
    from eedesigncore.common.pcb_family import PcbGroup

    board, sch = design()
    extra = Footprint(
        uuid=uid("extra-with-pad"),
        fp_id="Mechanical:Extra",
        pads=[Pad(number="1", id=uid("extra-child-pad"), layers=["F.Cu"])],
    )
    board.footprints.append(extra)
    if owner == "group":
        board.groups.append(PcbGroup(id=uid("external-owner"), members=[extra.pads[0].id]))
    else:
        from eedesigncore.pcb.models import PcbGenerator

        board.generators.append(
            PcbGenerator(uuid=uid("external-owner"), members=[extra.pads[0].id])
        )
    plan = plan_update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(delete_extra_footprints=True)
    )
    assert "group_deletion" in {issue.code for issue in plan.blockers}
    with pytest.raises(ValueError, match="blockers"):
        apply_update_pcb_from_schematic(board, sch, plan)


def test_reverse_literal_old_delete_sentinel_is_field_content():
    board, sch = design()
    board.footprints[0].properties.append(Property(name="Literal", value="\0DELETE"))
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(update_net_names=False)
    )
    assert value(result.schematic.symbols[0].fields, "Literal") == "\0DELETE"


@pytest.mark.parametrize(
    "angle, mirror, expected",
    [(0, None, 180), (90, None, 270), (180, None, 0), (270, None, 90), (0, "y", 0), (90, "x", 90)],
)
def test_added_local_label_uses_transformed_pin_direction(angle, mirror, expected):
    from eedesigncore.common.models import JustifyH, JustifyV

    board, sch = design()
    sch.labels = []
    sch.symbols[0].angle, sch.symbols[0].mirror = angle, mirror
    board.footprints[0].pads[0].net_name = "NEW_LABEL"
    result = update_schematic_from_pcb(board, sch)
    label = next(item for item in result.schematic.labels if item.content == "NEW_LABEL")
    assert label.position == sch.symbols[0].position
    assert label.angle == expected
    assert label.attributes.justify_v == JustifyV.bottom
    assert label.attributes.justify_h == (JustifyH.left if expected in (0, 90) else JustifyH.right)


def test_added_local_label_uses_loaded_project_text_size(tmp_path):
    from eedesigncore.project.hierarchy import load_hierarchy

    board, sch = design()
    sch.labels = []
    sch.to_file(tmp_path / "test.kicad_sch")
    project = tmp_path / "test.kicad_pro"
    project.write_text('{"schematic":{"drawing":{"default_text_size":75}}}')
    hierarchy = load_hierarchy(project)
    result = update_schematic_from_pcb(board, hierarchy)
    label = next(item for item in result.schematic.labels if item.content == "NET_A")
    assert label.attributes.font_size == pytest.approx((1.905, 1.905))


@pytest.mark.parametrize("collision", ["footprint", "footprint_group"])
def test_source_group_identity_cannot_collide_with_board_descendant(collision):
    from eedesigncore.common.pcb_family import PcbGroup
    from eedesigncore.sch.models import Group

    board, sch = design()
    identity = board.footprints[0].uuid
    if collision == "footprint_group":
        identity = uid("footprint-local-group")
        board.footprints[0].groups.append(
            PcbGroup(id=identity, members=[board.footprints[0].pads[0].id])
        )
    sch.groups.append(Group(uuid=identity, name="Source", members=[sch.symbols[0].uuid]))
    plan = plan_update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(transfer_symbol_groups=True)
    )
    assert "group_identity_collision" in {issue.code for issue in plan.blockers}


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_rule_area_metadata_is_explicitly_blocked(direction):
    from eedesigncore.sch.models import RuleArea

    board, sch = design()
    sch.rule_areas.append(RuleArea())
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    plan = planner(board, sch)
    assert "rule_area_metadata" in {issue.code for issue in plan.blockers}


def test_locked_extra_footprint_retains_copper_net_demand():
    board, sch = design()
    extra = Footprint(
        uuid=uid("locked-extra"),
        locked=True,
        pads=[Pad(number="1", id=uid("locked-extra-pad"), layers=["F.Cu"], net_name="NET_A")],
    )
    board.footprints.append(extra)
    board.tracks.append(TrackSegment(start=(1, 2), end=(3, 4), net="NET_A"))
    sch.labels[0].content = "RENAMED"
    plan = plan_update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(delete_extra_footprints=True)
    )
    assert "locked_deletion" in {issue.code for issue in plan.issues}
    assert "copper_split" in {issue.code for issue in plan.blockers}


def test_cleared_unnumbered_pad_copper_assignment_requires_resolution():
    board, sch = design()
    board.footprints[0].pads.append(
        Pad(number="", id=uid("unnumbered-pad"), layers=["F.Cu"], net_name="NET_A")
    )
    board.tracks.append(TrackSegment(start=(1, 2), end=(3, 4), net="NET_A"))
    plan = plan_update_pcb_from_schematic(board, sch)
    assert "copper_split" in {issue.code for issue in plan.blockers}


def test_unchanged_resolved_net_preserves_overridden_local_label_text():
    board, sch = design()
    local = Label(
        kind=LabelKind.local,
        content="LOCAL_ALIAS",
        position=(25.4, 25.4),
        uuid=uid("overridden-local"),
    )
    sch.labels.append(local)
    result = update_schematic_from_pcb(board, sch)
    assert (
        next(label for label in result.schematic.labels if label.uuid == local.uuid).content
        == "LOCAL_ALIAS"
    )
    assert not any(change.kind == "rename_net_label" for change in result.changes)


@pytest.mark.parametrize(
    "number, blocked", [("[1]", True), ("[1,2]", True), ("[A1-A3]", True), ("1,2", False)]
)
@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_stacked_pin_syntax_requires_expansion_but_literal_comma_does_not(
    number, blocked, direction
):
    board, sch = design()
    sch.lib_symbols[0].units[0].items[0].number = number
    board.footprints[0].pads[0].number = number
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    plan = planner(board, sch)
    assert ("stacked_pin_numbers" in {issue.code for issue in plan.blockers}) == blocked


def test_common_only_definition_still_reports_one_footprint_unit():
    board, sch = design()
    sch.lib_symbols[0].units[0].unit_index = 0
    result = update_pcb_from_schematic(board, sch)
    assert len(result.board.footprints[0].unit_info.units) == 1
    assert result.board.footprints[0].unit_info.units[0].pins == ["1", "2"]


@pytest.mark.parametrize(
    "electrical_type", [PinElectricalType.power_in, PinElectricalType.power_out]
)
def test_visible_ordinary_power_electrical_types_can_swap(electrical_type):
    board, sch = design()
    for pin in sch.lib_symbols[0].units[0].items:
        pin.electrical_type = electrical_type
        pin.visible = True
    board.footprints[0].pads[0].net_name = "NET_B"
    board.footprints[0].pads[1].net_name = "NET_A"
    result = update_schematic_from_pcb(
        board, sch, options=SchematicUpdateOptions(allow_unconstrained_pin_swaps=True)
    )
    assert any(change.kind == "swap_pins" for change in result.changes)
    assert result.schematic.labels == sch.labels


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_occurrence_unit_geometry_mismatch_is_blocked(direction):
    board, sch = design()
    sch.lib_symbols[0].units.append(SymbolUnit.for_symbol("Test:Part", unit_index=2))
    sch.symbols[0].instances[0].paths[0].unit = 2
    planner = (
        plan_update_pcb_from_schematic
        if direction == "forward"
        else plan_update_schematic_from_pcb
    )
    plan = planner(board, sch)
    assert "instance_unit_geometry" in {issue.code for issue in plan.blockers}


@pytest.mark.parametrize("preserve", [False, True])
def test_routed_extra_footprint_deletion_respects_contact_guard(preserve):
    board, sch = design()
    extra = Footprint(
        uuid=uid("routed-extra"),
        pads=[Pad(number="1", id=uid("routed-extra-pad"), layers=["F.Cu"], net_name="EXTRA_NET")],
    )
    board.footprints.append(extra)
    board.tracks.append(TrackSegment(start=(1, 2), end=(3, 4), net="EXTRA_NET"))
    plan = plan_update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, preserve_routed_contacts=preserve),
    )
    deletion = next(issue for issue in plan.issues if issue.code == "routed_deletion")
    assert deletion.severity == ("error" if preserve else "warning")
    if preserve:
        with pytest.raises(ValueError, match="blockers"):
            apply_update_pcb_from_schematic(board, sch, plan)
    else:
        result = apply_update_pcb_from_schematic(board, sch, plan)
        assert len(result.board.footprints) == 1
        assert result.board.tracks == board.tracks
