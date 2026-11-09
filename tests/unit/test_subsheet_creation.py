from importlib.resources import files

import pytest

from eedesigncore import (
    Schematic,
    create_sheet_schematic,
    create_subsheet,
    load_hierarchy,
    next_page_number,
)
from eedesigncore.sch.models import RootInstancePath, SheetInstances

PLACE = {"project_name": "demo", "parent_path": "/root", "page": "2"}


def test_child_content_matches_template_except_title_and_identifiers():
    original = Schematic.from_string(
        files("eedesigncore.sch").joinpath("templates/child_sheet.kicad_sch").read_text()
    )
    created = create_sheet_schematic()
    assert created.paper == original.paper
    assert created.tables[0].column_widths == [8.89, 29.21]
    assert created.tables[0].row_heights == [2.54, 2.54, 2.54]
    for new, old in zip(created.texts, original.texts, strict=True):
        assert new.uuid != old.uuid
        expected = old.model_copy(deep=True)
        expected.uuid = new.uuid
        if "upper" in expected.content:
            expected.content = '@{upper("${SHEETNAME}")}'
        assert new == expected
    expected_table = original.tables[0].model_copy(deep=True)
    expected_table.uuid = created.tables[0].uuid
    for new, old in zip(created.tables[0].cells, expected_table.cells, strict=True):
        assert new.uuid != old.uuid
        old.uuid = new.uuid
    assert created.tables[0] == expected_table
    restored = Schematic.from_string(created.to_string())
    assert restored.to_string() == created.to_string()
    assert {t.uuid: t for t in restored.texts} == {t.uuid: t for t in created.texts}


def test_paired_creation_and_independent_copies():
    first = create_subsheet("Power", "power.kicad_sch", at=(25.4, 25.4), **PLACE)
    second = create_subsheet("Power", "power.kicad_sch", at=(25.4, 25.4), **PLACE)
    assert first.sheet.size == (50.8, 30.48)
    assert first.sheet.fields[1].value == "power.kicad_sch"
    assert first.schematic.uuid != second.schematic.uuid
    assert first.sheet.uuid != second.sheet.uuid
    assert {c.uuid for c in first.schematic.tables[0].cells}.isdisjoint(
        c.uuid for c in second.schematic.tables[0].cells
    )
    first.schematic.tables[0].cells[0].content = "Changed"
    assert second.schematic.tables[0].cells[0].content == "BRANCH"


def _root(tmp_path):
    root = create_sheet_schematic()
    root.sheet_instances = SheetInstances(paths=[RootInstancePath(path="/", page="1")])
    return root


def test_no_place_raises():
    with pytest.raises(ValueError, match="page number"):
        create_subsheet("Power", "power.kicad_sch", at=(0.0, 0.0))


def test_explicit_place_writes_one_instance():
    made = create_subsheet("Power", "power.kicad_sch", at=(0.0, 0.0), **PLACE)
    [project] = made.sheet.instances
    assert project.name == "demo"
    assert [(p.path, p.page) for p in project.paths] == [("/root", "2")]


def test_hierarchy_gives_next_page_and_round_trips(tmp_path):
    root = _root(tmp_path)
    made = create_subsheet(
        "A", "a.kicad_sch", at=(0.0, 0.0),
        project_name="demo", parent_path=f"/{root.uuid}", page="3",
    )
    root.sheets.append(made.sheet)
    made.schematic.to_file(tmp_path / "a.kicad_sch")
    root.to_file(tmp_path / "demo.kicad_sch")

    hierarchy = load_hierarchy(tmp_path / "demo.kicad_sch")
    assert next_page_number(hierarchy) == "4"
    second = create_subsheet("B", "b.kicad_sch", at=(60.0, 0.0), hierarchy=hierarchy)
    [project] = second.sheet.instances
    assert project.name == "demo"
    assert [(p.path, p.page) for p in project.paths] == [(f"/{root.uuid}", "4")]

    root.sheets.append(second.sheet)
    second.schematic.to_file(tmp_path / "b.kicad_sch")
    root.to_file(tmp_path / "demo.kicad_sch")
    pages = {n.name: n.page for n in load_hierarchy(tmp_path / "demo.kicad_sch").walk()}
    assert pages == {"demo": "1", "A": "3", "B": "4"}


def test_parent_file_used_twice_gets_one_entry_per_instance(tmp_path):
    root = _root(tmp_path)
    for index, name in enumerate(("A1", "A2")):
        made = create_subsheet(
            name, "a.kicad_sch", at=(60.0 * index, 0.0),
            project_name="demo", parent_path=f"/{root.uuid}", page=str(index + 2),
        )
        root.sheets.append(made.sheet)
    made.schematic.to_file(tmp_path / "a.kicad_sch")
    root.to_file(tmp_path / "demo.kicad_sch")

    hierarchy = load_hierarchy(tmp_path / "demo.kicad_sch")
    parent = next(n for n in hierarchy.walk() if n.name == "A1")
    child = create_subsheet("C", "c.kicad_sch", at=(0.0, 0.0), hierarchy=hierarchy, parent=parent)
    [project] = child.sheet.instances
    expected = [(n.kiid_path, page) for n, page in zip(
        [n for n in hierarchy.walk() if n.name in ("A1", "A2")], ("4", "5"), strict=True
    )]
    assert [(p.path, p.page) for p in project.paths] == expected


def test_parent_outside_hierarchy_raises(tmp_path):
    root = _root(tmp_path)
    root.to_file(tmp_path / "demo.kicad_sch")
    one = load_hierarchy(tmp_path / "demo.kicad_sch")
    other = load_hierarchy(tmp_path / "demo.kicad_sch")
    with pytest.raises(ValueError, match="not a node"):
        create_subsheet("C", "c.kicad_sch", at=(0.0, 0.0), hierarchy=one, parent=other.root)
