from importlib.resources import files

from eedesigncore import Schematic, create_sheet_schematic, create_subsheet


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
    first = create_subsheet("Power", "power.kicad_sch", at=(25.4, 25.4))
    second = create_subsheet("Power", "power.kicad_sch", at=(25.4, 25.4))
    assert first.sheet.size == (50.8, 30.48)
    assert first.sheet.fields[1].value == "power.kicad_sch"
    assert first.schematic.uuid != second.schematic.uuid
    assert first.sheet.uuid != second.sheet.uuid
    assert {c.uuid for c in first.schematic.tables[0].cells}.isdisjoint(
        c.uuid for c in second.schematic.tables[0].cells
    )
    first.schematic.tables[0].cells[0].content = "Changed"
    assert second.schematic.tables[0].cells[0].content == "BRANCH"
