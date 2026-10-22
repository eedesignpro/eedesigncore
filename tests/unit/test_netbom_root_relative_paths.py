"""Root-relative schematic associations without reference-only or suffix matching."""

from pathlib import Path

import pytest

from eedesigncore import Schematic, build_netbom
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode
from eedesigncore.sch.models import InstancePath, InstanceProject
from tests.unit.test_netbom import board, records, schematic, uid


def repeated_hierarchy(tmp_path: Path, *, multiple_roots: bool = False) -> SheetHierarchy:
    shared = schematic()
    paths = (
        [f"/{uid(100)}/{uid(10)}", f"/{uid(101)}/{uid(10)}"]
        if multiple_roots
        else [f"/{uid(100)}/{uid(10)}", f"/{uid(100)}/{uid(11)}"]
    )
    shared.symbols[0].instances = [
        InstanceProject(
            name="test",
            paths=[
                InstancePath(path=path, reference=f"R{i}", unit=1)
                for i, path in enumerate(paths, 1)
            ],
        )
    ]
    children = [
        SheetNode(
            path=tmp_path / "shared.kicad_sch", schematic=shared, name=f"child{i}", kiid_path=path
        )
        for i, path in enumerate(paths)
    ]
    roots = [
        SheetNode(
            path=tmp_path / f"root{i}.kicad_sch",
            schematic=Schematic(uuid=uid(100 + i)),
            name=f"root{i}",
            kiid_path=f"/{uid(100 + i)}",
            children=[children[i]] if multiple_roots else children,
        )
        for i in range(2 if multiple_roots else 1)
    ]
    return SheetHierarchy(project_root=tmp_path, roots=roots)


@pytest.mark.parametrize("uppercase", [False, True])
def test_root_relative_match_uses_uuid_and_preserves_source(uppercase: bool) -> None:
    pcb, sch = board(), schematic()
    pcb.footprints[0].path = f"/{uid(3)}"
    if uppercase:
        sch.uuid = "ABCDEF01-2345-6789-ABCD-0123456789AB"
        sch.symbols[0].uuid = "ABCDEF02-2345-6789-ABCD-0123456789AB"
        pcb.footprints[0].path = "/abcdef02-2345-6789-abcd-0123456789ab"
    sch.symbols[0].fields[0].value = "R99"
    before = pcb.model_dump(), sch.model_dump()
    document = build_netbom(pcb, schematic=sch)
    assert len(records(document, "component")) == 1
    occurrence = records(document, "symbol_instance")[0]
    assert occurrence.data["board_match"] == "exact_path"
    assert occurrence.data["association_derivation"] == "copied"
    assert "reference_mismatch" in {r.data["code"] for r in records(document, "issue")}
    assert all(r.data["symbol_association"] == "matched" for r in records(document, "pin"))
    assert before == (pcb.model_dump(), sch.model_dump())


def test_root_relative_anchor_joins_other_unit() -> None:
    pcb, sch = board(), schematic()
    pcb.footprints[0].path = f"/{uid(3)}"
    second = sch.symbols[0].model_copy(deep=True)
    second.uuid, second.unit = uid(45), 2
    sch.symbols.append(second)
    result = build_netbom(pcb, schematic=sch)
    assert len(records(result, "component")) == 1
    assert {r.data["board_match"] for r in records(result, "symbol_instance")} == {
        "exact_path",
        "anchored_multiunit_reference",
    }


def test_repeated_child_paths_remain_distinct(tmp_path: Path) -> None:
    hierarchy = repeated_hierarchy(tmp_path)
    pcb = board()
    pcb.footprints[0].path = f"/{uid(10)}/{uid(3)}"
    second = pcb.footprints[0].model_copy(deep=True)
    second.uuid = uid(40)
    second.path = f"/{uid(11)}/{uid(3)}"
    second.properties[0].value = "R2"
    pcb.footprints.append(second)
    result = build_netbom(pcb, schematic=hierarchy)
    assert len(records(result, "component")) == 2
    occurrences = records(result, "symbol_instance")
    assert all(r.data["board_match"] == "exact_path" for r in occurrences)
    assert len({r.links["component"][0] for r in occurrences}) == 2
    for occurrence in occurrences:
        assert (
            result.records[occurrence.links["component"][0]].data["reference"]
            == occurrence.data["reference"]
        )


def test_never_drops_child_occurrence_from_path(tmp_path: Path) -> None:
    hierarchy = repeated_hierarchy(tmp_path)
    pcb = board()
    pcb.footprints[0].path = f"/{uid(3)}"
    result = build_netbom(pcb, schematic=hierarchy)
    component = next(r for r in records(result, "component") if r.links.get("board"))
    assert not component.links.get("symbols")


def test_two_roots_with_same_relative_path_remain_conflicting(tmp_path: Path) -> None:
    hierarchy = repeated_hierarchy(tmp_path, multiple_roots=True)
    pcb = board()
    pcb.footprints[0].path = f"/{uid(10)}/{uid(3)}"
    result = build_netbom(pcb, schematic=hierarchy)
    board_component = next(r for r in records(result, "component") if r.links.get("board"))
    assert not board_component.links.get("symbols")
    occurrences = records(result, "symbol_instance")
    assert len(occurrences) == 2
    assert all(r.links["candidate_components"] == [board_component.id] for r in occurrences)
    assert all(
        result.records[r.links["component"][0]].data["board_match"] == "conflicting"
        for r in occurrences
    )


def test_full_path_still_disambiguates_multiple_roots(tmp_path: Path) -> None:
    hierarchy = repeated_hierarchy(tmp_path, multiple_roots=True)
    pcb = board()
    pcb.footprints[0].path = f"/{uid(100)}/{uid(10)}/{uid(3)}"
    result = build_netbom(pcb, schematic=hierarchy)
    board_component = next(r for r in records(result, "component") if r.links.get("board"))
    assert len(board_component.links["symbols"]) == 1
    assert result.records[board_component.links["symbols"][0]].data["reference"] == "R1"


def test_full_and_relative_board_candidates_are_not_arbitrarily_selected() -> None:
    pcb, sch = board(), schematic()
    second = pcb.footprints[0].model_copy(deep=True)
    second.uuid = uid(40)
    second.path = f"/{uid(3)}"
    second.properties[0].value = "R2"
    pcb.footprints.append(second)
    result = build_netbom(pcb, schematic=sch)
    occurrence = records(result, "symbol_instance")[0]
    assert len(occurrence.links["candidate_components"]) == 2
    assert not any(
        r.links.get("symbols") for r in records(result, "component") if r.links.get("board")
    )


def test_ambiguous_root_relative_anchor_does_not_attach_additional_units(tmp_path: Path) -> None:
    hierarchy = repeated_hierarchy(tmp_path, multiple_roots=True)
    shared = hierarchy.roots[0].children[0].schematic
    second = shared.symbols[0].model_copy(deep=True)
    second.uuid, second.unit = uid(45), 2
    for group in second.instances:
        for entry in group.paths:
            entry.unit = 2
    shared.symbols.append(second)
    pcb = board()
    pcb.footprints[0].path = f"/{uid(10)}/{uid(3)}"
    result = build_netbom(pcb, schematic=hierarchy)
    board_component = next(r for r in records(result, "component") if r.links.get("board"))
    assert not board_component.links.get("symbols")
    assert len(records(result, "symbol_instance")) == 4
