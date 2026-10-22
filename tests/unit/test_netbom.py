"""Independent board-first netbom association and discovery checks."""

from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    Footprint,
    Pad,
    Pin,
    PinElectricalType,
    PinShape,
    Property,
    Schematic,
    Symbol,
    SymbolUnit,
)
from eedesigncore.common.sch_family.pins import PinAlternate
from eedesigncore.netbom import NetbomDocument, build_netbom, load_netbom
from eedesigncore.pro import JsonTree, ProjectFile
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode
from eedesigncore.sch.models import InstancePath, InstanceProject, PlacedSymbol, PlacedSymbolPin


def uid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012x}"


def board() -> Board:
    return Board(
        footprints=[
            Footprint(
                uuid=uid(1),
                path=f"/{uid(2)}/{uid(3)}",
                properties=[
                    Property(name="Reference", value="R1"),
                    Property(name="Value", value="10k"),
                ],
                pads=[
                    Pad(id=uid(4), number="1", layers=["F.Cu"], net_name="IN"),
                    Pad(id=uid(5), number="2", layers=["F.Cu"], net_name="OUT"),
                ],
            )
        ]
    )


def schematic() -> Schematic:
    pins = [
        Pin(
            number=str(i),
            name=f"P{i}",
            electrical_type=PinElectricalType.passive,
            shape=PinShape.line,
        )
        for i in (1, 2)
    ]
    return Schematic(
        uuid=uid(2),
        lib_symbols=[
            Symbol(name="Device:R", units=[SymbolUnit.for_symbol("Device:R", items=pins)])
        ],
        symbols=[
            PlacedSymbol(
                uuid=uid(3), lib_id="Device:R", fields=[Property(name="Reference", value="R1")]
            )
        ],
    )


def records(document: NetbomDocument, kind: str):
    return document.find("kind", kind)


def test_board_first_exact_join_retains_nets_and_does_not_mutate() -> None:
    pcb, sch = board(), schematic()
    sch.symbols[0].fields[0].value = "R99"
    old = pcb.model_dump(), sch.model_dump()
    result = build_netbom(pcb, schematic=sch)
    assert len(records(result, "component")) == 1
    assert records(result, "symbol_instance")[0].data["board_match"] == "exact_path"
    assert {record.data["name"] for record in records(result, "net")} == {"IN", "OUT"}
    assert len(records(result, "symbol_pin")) == 2
    assert old == (pcb.model_dump(), sch.model_dump())
    assert result.to_json() == build_netbom(pcb, schematic=sch).to_json()
    assert NetbomDocument.from_json(result.to_json()).to_json() == result.to_json()


def test_reference_fallback_and_duplicate_reference_refusal() -> None:
    pcb, sch = board(), schematic()
    pcb.footprints[0].path = None
    result = build_netbom(pcb, schematic=sch)
    assert (
        records(result, "symbol_instance")[0].data["board_match"] == "unique_reference_and_units"
    )
    duplicate = pcb.footprints[0].model_copy(deep=True)
    duplicate.uuid = uid(12)
    pcb.footprints.append(duplicate)
    result = build_netbom(pcb, schematic=sch)
    assert len(records(result, "component")) == 3
    assert "component" in records(result, "symbol_instance")[0].links
    assert any(r.data["code"] == "unmatched_schematic_instance" for r in records(result, "issue"))


@pytest.mark.parametrize("net,conflict", [("IN", False), ("OTHER", True)])
def test_repeated_pad_numbers_preserve_physical_pads_and_conflicts(
    net: str, conflict: bool
) -> None:
    pcb = board()
    pcb.footprints[0].pads.append(Pad(id=uid(20), number="1", layers=["F.Cu"], net_name=net))
    result = build_netbom(pcb)
    assert len(records(result, "pad")) == 3
    assert len(records(result, "pin")) == 2
    pin = next(r for r in records(result, "pin") if r.data["number"] == "1")
    assert len(pin.links["pads"]) == 2
    assert pin.data["connection_status"] == ("conflicting" if conflict else "assigned")
    assert len(pin.links["nets"]) == (2 if conflict else 1)


@pytest.mark.parametrize("selection,status", [("ALT", "selected"), ("MISSING", "unresolved")])
def test_alternate_pin_function(selection: str, status: str) -> None:
    sch = schematic()
    sch.lib_symbols[0].units[0].items[0].alternates = [
        PinAlternate(name="ALT", electrical_type=PinElectricalType.output, shape=PinShape.inverted)
    ]
    sch.symbols[0].pins = [PlacedSymbolPin(number="1", alternate=selection)]
    result = build_netbom(board(), schematic=sch)
    pin = next(r for r in records(result, "symbol_pin") if r.data["number"] == "1")
    assert pin.data["selection_status"] == status
    assert pin.data["effective_name"] == ("ALT" if status == "selected" else None)
    assert pin.data["effective_electrical_type"] == ("output" if status == "selected" else None)


def test_repeated_sheet_screen_uses_occurrence_reference(tmp_path: Path) -> None:
    sch = schematic()
    paths = [f"/{uid(30)}/{uid(31)}", f"/{uid(30)}/{uid(32)}"]
    sch.symbols[0].instances = [
        InstanceProject(
            name="test",
            paths=[
                InstancePath(path=path, reference=f"R{i}", unit=1)
                for i, path in enumerate(paths, 1)
            ],
        )
    ]
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "shared.kicad_sch", schematic=sch, name=f"sheet{i}", kiid_path=path
            )
            for i, path in enumerate(paths)
        ],
    )
    pcb = board()
    pcb.footprints[0].path = paths[0] + "/" + uid(3)
    other = pcb.footprints[0].model_copy(deep=True)
    other.uuid = uid(40)
    other.path = paths[1] + "/" + uid(3)
    other.properties[0].value = "R2"
    pcb.footprints.append(other)
    result = build_netbom(pcb, schematic=hierarchy)
    assert len(records(result, "component")) == 2
    assert {r.data["reference"] for r in records(result, "symbol_instance")} == {"R1", "R2"}
    assert len({r.links["component"][0] for r in records(result, "symbol_instance")}) == 2


def test_multiunit_reference_fallback_merges_only_distinct_units() -> None:
    pcb, sch = board(), schematic()
    pcb.footprints[0].path = None
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid, second.unit = uid(45), 2
    sch.symbols.append(second)
    assert len(records(build_netbom(pcb, schematic=sch), "component")) == 1
    second.unit = 1
    assert len(records(build_netbom(pcb, schematic=sch), "component")) == 3


def test_discovery_and_relationship_trace_limits() -> None:
    result = build_netbom(board(), schematic=schematic())
    start = result.find("net_name", "IN")[0].id
    end = result.find("net_name", "OUT")[0].id
    assert result.trace(start, end, relationship_kinds=set()) == []
    route = result.trace(start, end, relationship_kinds={"resistive_connection"})
    assert route[0] == start and route[-1] == end and len(route) == 3
    assert result.records[route[1]].kind == "relationship"
    assert result.neighborhood(start, depth=0) == [result.records[start]]
    with pytest.raises(ValueError, match="limit"):
        result.neighborhood(start, max_records=1)
    with pytest.raises(ValueError, match="limit"):
        result.trace(start, end, relationship_kinds={"resistive_connection"}, max_visited=1)
    with pytest.raises(KeyError):
        result.neighborhood("missing")
    assert result.find("unknown", "unknown") == []


def test_unknown_project_settings_and_empty_fields_remain_distinct() -> None:
    pcb = board()
    pcb.footprints[0].properties += [
        Property(name="Description", value=""),
        Property(name="Custom", value="data"),
    ]
    project = ProjectFile(root=JsonTree({"unknown": {"array": [None, "", False, 2]}}))
    result = build_netbom(pcb, project=project)
    component = records(result, "component")[0]
    assert "Description" in component.data["empty_fields"]
    assert "Datasheet" in component.data["absent_standard_fields"]
    assert component.data["fields"]["Custom"] == "data"
    assert records(result, "project_settings")[0].data["value"] == {"array": [None, "", False, 2]}


def test_empty_inputs_and_record_budget() -> None:
    with pytest.raises(ValueError, match="Supply"):
        build_netbom()
    empty = build_netbom(Board())
    assert not records(empty, "component")
    assert empty.coverage["schematic"].status == "not_supplied"
    for limit in (0, 1, 1000001):
        with pytest.raises(ValueError, match="limit"):
            build_netbom(board(), max_records=limit)


def test_load_board_only_and_reject_unsupported_path(tmp_path: Path) -> None:
    path = tmp_path / "test.kicad_pcb"
    board().to_file(path)
    result = load_netbom(path)
    assert len(records(result, "component")) == 1
    assert result.coverage["schematic"].status == "not_supplied"
    with pytest.raises(ValueError):
        load_netbom(tmp_path / "wrong.txt")


def test_json_rejects_dangling_links_and_unknown_schema() -> None:
    result = build_netbom(board())
    record = records(result, "component")[0]
    record.links["bad"] = ["missing"]
    with pytest.raises(ValueError, match="dangling"):
        NetbomDocument.from_json(result.to_json())
    with pytest.raises(ValueError):
        NetbomDocument.from_json('{"schema_version":"future","design_id":"x"}')


def test_duplicate_symbol_identifiers_use_distinct_occurrence_records() -> None:
    sch = schematic()
    duplicate = sch.symbols[0].model_copy(deep=True)
    duplicate.fields[0].value = "R2"
    sch.symbols.append(duplicate)
    result = build_netbom(schematic=sch)
    assert len(records(result, "symbol_instance")) == 2
    assert len({record.id for record in records(result, "symbol_instance")}) == 2
    assert any(record.kind == "issue" for record in result.records.values())


def test_unnumbered_physical_pads_do_not_merge() -> None:
    pcb = board()
    for pad in pcb.footprints[0].pads:
        pad.number = ""
    result = build_netbom(pcb)
    assert len(records(result, "pin")) == 2
    assert all(len(pin.links["pads"]) == 1 for pin in records(result, "pin"))


def test_stale_board_path_refuses_reference_fallback() -> None:
    pcb = board()
    pcb.footprints[0].path = f"/{uid(90)}/{uid(91)}"
    result = build_netbom(pcb, schematic=schematic())
    assert len(records(result, "component")) == 2
    board_component = next(
        record for record in records(result, "component") if "board" in record.links
    )
    assert not board_component.links.get("symbols")


def test_exact_anchor_allows_other_unit_to_join_same_component() -> None:
    sch = schematic()
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid, second.unit = uid(45), 2
    sch.symbols.append(second)
    result = build_netbom(board(), schematic=sch)
    assert len(records(result, "component")) == 1
    assert {record.data["board_match"] for record in records(result, "symbol_instance")} == {
        "exact_path",
        "anchored_multiunit_reference",
    }


def test_alternate_equal_to_base_name_uses_base_function() -> None:
    sch = schematic()
    sch.symbols[0].pins = [PlacedSymbolPin(number="1", alternate="P1")]
    result = build_netbom(board(), schematic=sch)
    pin = next(record for record in records(result, "symbol_pin") if record.data["number"] == "1")
    assert pin.data["selection_status"] == "base"
    assert pin.data["effective_name"] == "P1"
    assert pin.data["effective_electrical_type"] == "passive"


def test_no_connect_marker_reports_saved_board_assignment_conflict() -> None:
    from eedesigncore.sch.models import NoConnect

    sch = schematic()
    sch.lib_symbols[0].units[0].items[0].position = (1, 2)
    sch.lib_symbols[0].units[0].items[1].position = (3, 4)
    sch.no_connects = [NoConnect(uuid=uid(70), position=(1, -2))]
    result = build_netbom(board(), schematic=sch)
    pin = next(record for record in records(result, "symbol_pin") if record.data["number"] == "1")
    assert pin.data["no_connect_status"] == "explicit_marker"
    assert pin.links["no_connects"]
    assert any(
        record.data["code"] == "no_connect_assignment_conflict"
        for record in records(result, "issue")
    )
    assert result.find("net_name", "IN")


@pytest.mark.parametrize("layer", ["F.Cu", "B.Cu"])
def test_pad_board_position_uses_saved_coordinates_without_second_mirror(layer: str) -> None:
    from eedesigncore.mod.models import FootprintPlacement

    pcb = board()
    pcb.footprints[0].layer = layer
    pcb.footprints[0].at = FootprintPlacement(position=(10, 20), angle=90)
    pcb.footprints[0].pads[0].position = (1, 2)
    result = build_netbom(pcb)
    pad = next(record for record in records(result, "pad") if record.data["number"] == "1")
    assert pad.data["board_position"] == [12, 19]
    assert pad.data["local_position"] == [1, 2]


def test_repeated_footprints_share_geometry_without_merging_instances() -> None:
    pcb = board()
    for index in range(10):
        copy = pcb.footprints[0].model_copy(deep=True)
        copy.uuid = uid(100 + index)
        copy.path = None
        copy.properties[0].value = f"R{index + 2}"
        pcb.footprints.append(copy)
    result = build_netbom(pcb)
    assert len(records(result, "component")) == 11
    assert len(records(result, "footprint_definition")) == 1
    assert len(records(result, "pad")) == 22
    definition = records(result, "footprint_definition")[0]
    assert len(definition.links["instances"]) == 11
    assert len({record.id for record in records(result, "component")}) == 11


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/pcb").glob("*.kicad_pcb")))
def test_saved_board_fixture_extracts_without_mutation(path: Path) -> None:
    pcb = Board.from_file(path)
    original = pcb.model_dump()
    result = build_netbom(pcb, design_name=path.stem)
    assert len(records(result, "component")) == len(pcb.footprints)
    assert len(records(result, "pad")) == sum(len(fp.pads) for fp in pcb.footprints)
    result.validate_links()
    assert NetbomDocument.from_json(result.to_json()).revision_id == result.revision_id
    assert pcb.model_dump() == original


def test_component_view_does_not_expand_other_components_on_common_net() -> None:
    pcb = board()
    for index in range(40):
        other = pcb.footprints[0].model_copy(deep=True)
        other.uuid = uid(200 + index)
        other.path = None
        other.properties[0].value = f"R{index + 2}"
        other.pads[1].net_name = f"OUT{index}"
        pcb.footprints.append(other)
    result = build_netbom(pcb)
    component = next(
        record for record in records(result, "component") if record.data["reference"] == "R1"
    )
    view = result.component_view(component.id, max_records=20)
    assert [record.id for record in view if record.kind == "component"] == [component.id]
    assert {record.data["name"] for record in view if record.kind == "net"} == {"IN", "OUT"}
    net = result.find("net_name", "IN")[0]
    net_view = result.net_view(net.id)
    assert {record.id for record in net_view if record.kind == "net"} == {net.id}
    assert len([record for record in net_view if record.kind == "component"]) == 41
    assert {record.data["number"] for record in net_view if record.kind == "pad"} == {"1"}
    with pytest.raises(ValueError, match="limit"):
        result.net_view(net.id, max_records=10)


def test_net_view_includes_symbol_pin_issues() -> None:
    sch = schematic()
    sch.symbols[0].pins = [PlacedSymbolPin(number="1", alternate="MISSING")]
    result = build_netbom(board(), schematic=sch)
    net = result.find("net_name", "IN")[0]
    view = result.net_view(net.id)
    assert any(
        record.kind == "issue" and record.data["code"] == "alternate_unresolved" for record in view
    )


def test_saved_and_pattern_netclasses_are_retained() -> None:
    project = ProjectFile(
        root=JsonTree(
            {
                "net_settings": {
                    "netclass_assignments": {"IN": ["Power"]},
                    "netclass_patterns": [{"pattern": "I*", "netclass": "Matched"}],
                }
            }
        )
    )
    result = build_netbom(board(), project=project)
    assert result.find("net_name", "IN")[0].data["netclasses"] == ["Matched", "Power"]
    assert result.find("net_name", "OUT")[0].data["netclasses"] == ["Default"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_bom_groups_do_not_merge_different_schematic_part_metadata(duplicate: bool) -> None:
    pcb, sch = board(), schematic()
    second = pcb.footprints[0].model_copy(deep=True)
    second.uuid, second.path = uid(50), f"/{uid(2)}/{uid(51)}"
    second.properties[0].value = "R2"
    pcb.footprints.append(second)
    placed = sch.symbols[0].model_copy(deep=True)
    placed.uuid = uid(51)
    placed.fields[0].value = "R2"
    sch.symbols.append(placed)
    for index, symbol in enumerate(sch.symbols):
        if duplicate:
            symbol.fields.append(Property(name="MPN", value="common-first-value"))
        symbol.fields.append(Property(name="MPN", value=f"part-{index}"))
    result = build_netbom(pcb, schematic=sch)
    assert len(records(result, "bom_group")) == 2
    assert all(record.data["quantity"] == 1 for record in records(result, "bom_group"))


def test_repeated_sheet_occurrences_select_their_own_unit(tmp_path: Path) -> None:
    sch = schematic()
    second_unit = SymbolUnit.for_symbol(
        "Device:R",
        unit_index=2,
        items=[
            Pin(
                number="3",
                name="UNIT2",
                electrical_type=PinElectricalType.output,
                shape=PinShape.line,
            )
        ],
    )
    sch.lib_symbols[0].units.append(second_unit)
    paths = [f"/{uid(60)}/{uid(61)}", f"/{uid(60)}/{uid(62)}"]
    sch.symbols[0].instances = [
        InstanceProject(
            name="test",
            paths=[
                InstancePath(path=path, reference=f"R{index}", unit=index)
                for index, path in enumerate(paths, 1)
            ],
        )
    ]
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "shared.kicad_sch",
                schematic=sch,
                name=f"sheet{index}",
                kiid_path=path,
            )
            for index, path in enumerate(paths)
        ],
    )
    result = build_netbom(schematic=hierarchy)
    occurrences = {record.data["unit"]: record for record in records(result, "symbol_instance")}
    assert {result.records[key].data["number"] for key in occurrences[1].links["pins"]} == {
        "1",
        "2",
    }
    assert {result.records[key].data["number"] for key in occurrences[2].links["pins"]} == {"3"}


def test_unmatched_board_pin_is_reported_as_incomplete_schematic_association() -> None:
    pcb = board()
    pcb.footprints[0].pads.append(Pad(id=uid(85), number="9", layers=["F.Cu"], net_name="IN"))
    result = build_netbom(pcb, schematic=schematic())
    pin = next(record for record in records(result, "pin") if record.data["number"] == "9")
    assert pin.data["symbol_association"] == "unresolved"
    assert pin.links.get("issues")
    assert result.coverage["schematic"].status == "partial"
