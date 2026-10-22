"""Single-sheet comparison and ordering boundaries."""

import json
from pathlib import Path

import pytest

from eedesigncore import (
    Footprint,
    Group,
    Junction,
    Label,
    LabelKind,
    Line,
    PlacedSymbol,
    Property,
    RuleArea,
    Schematic,
    SchPolyline,
    SchTable,
    SchTableCell,
    Sheet,
    Symbol,
    SymbolLib,
    TitleBlock,
    compare_content,
    content_signature,
)

ITEM_FIELDS = (
    "graphic_shapes",
    "texts",
    "text_boxes",
    "junctions",
    "no_connects",
    "bus_entries",
    "lines",
    "labels",
    "images",
    "tables",
    "rule_areas",
    "groups",
    "symbols",
    "sheets",
)


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/sch").glob("*.kicad_sch")))
def test_schematic_fixture_permutations(path: Path) -> None:
    before = Schematic.from_file(path)
    original = before.model_dump()
    after = before.model_copy(deep=True)
    for name in ITEM_FIELDS:
        getattr(after, name).reverse()
    after.lib_symbols.reverse()
    result = compare_content(before, after)
    assert result.equal
    assert result.before_signature == result.after_signature
    assert before.model_dump() == original


def test_symbol_position_and_value_changes_have_object_paths() -> None:
    before = Schematic(
        symbols=[
            PlacedSymbol(
                lib_id="Device:R",
                uuid="00000000-0000-0000-0000-000000000001",
                fields=[
                    Property(name="Reference", value="R1"),
                    Property(name="Value", value="10k"),
                ],
            )
        ]
    )
    after = before.model_copy(deep=True)
    after.symbols[0].position = (25.4, 50.8)
    after.symbols[0].fields[1].value = "22k"
    result = compare_content(before, after)
    assert not result.equal
    assert {change.path[-1] for change in result.changes} == {"position", "value"}
    assert all(
        change.path[1] == 'PlacedSymbol[uuid="00000000-0000-0000-0000-000000000001"]'
        for change in result.changes
    )
    assert result.before_signature != result.after_signature


def test_wire_endpoint_and_label_name_remain_significant() -> None:
    before = Schematic(
        lines=[Line(uuid="00000000-0000-0000-0000-000000000002", end=(1, 2))],
        labels=[Label(kind=LabelKind.local, uuid="label", content="GND")],
    )
    after = before.model_copy(deep=True)
    after.lines[0].end = (3, 4)
    after.labels[0].content = "VCC"
    result = compare_content(before, after)
    assert {change.path[-1] for change in result.changes} == {"end", "content"}


def test_duplicate_items_retain_counts() -> None:
    before = Schematic(junctions=[Junction(), Junction()])
    after = Schematic(junctions=[Junction()])
    result = compare_content(before, after)
    assert [change.kind for change in result.changes] == ["removed"]


def test_duplicate_identifiers_keep_root_order_for_group_references() -> None:
    before = Schematic(
        lines=[Line(uuid="duplicate", end=(1, 2)), Line(uuid="duplicate", end=(3, 4))],
        groups=[Group(uuid="group", members=["duplicate"])],
    )
    after = before.model_copy(deep=True)
    after.lines.reverse()
    assert not compare_content(before, after).equal


def test_identifiers_across_categories_and_rule_area_shapes_are_checked() -> None:
    before = Schematic(
        lines=[Line(uuid="same"), Line(uuid="other")],
        rule_areas=[RuleArea(shape=SchPolyline(id="same"))],
    )
    after = before.model_copy(deep=True)
    after.lines.reverse()
    assert not compare_content(before, after).equal


@pytest.mark.parametrize(
    "names,equal",
    [
        (("LibraryA:R", "LibraryB:R"), True),
        (("Library:R", "Library:R"), False),
        (("Library:A/B", "Library:A{slash}B"), False),
    ],
)
def test_embedded_definitions_use_full_library_names(names: tuple[str, str], equal: bool) -> None:
    before = Schematic(lib_symbols=[Symbol(name=names[0]), Symbol(name=names[1], in_bom=False)])
    after = before.model_copy(deep=True)
    after.lib_symbols.reverse()
    assert compare_content(before, after).equal is equal


def test_title_block_comment_numbers_are_typed_and_order_independent() -> None:
    before = Schematic(title_block=TitleBlock(comments={1: "one", 2: "two"}))
    after = Schematic(title_block=TitleBlock(comments={2: "two", 1: "one"}))
    assert compare_content(before, after).equal
    after.title_block.comments[2] = "changed"
    result = compare_content(before, after)
    assert len(result.changes) == 1
    assert result.changes[0].path == ("title_block", "comments", "2")
    assert json.loads(result.changes[0].after) == "changed"


def test_ordered_table_cells_and_rule_area_vertices_remain_significant() -> None:
    before = Schematic(
        tables=[
            SchTable(
                uuid="table",
                cells=[
                    SchTableCell(content="first"),
                    SchTableCell(content="second"),
                ],
            )
        ],
        rule_areas=[RuleArea(shape=SchPolyline(id="area", pts=[(0, 0), (1, 0), (1, 1)]))],
    )
    after = before.model_copy(deep=True)
    after.tables[0].cells.reverse()
    after.rule_areas[0].shape.pts.reverse()
    result = compare_content(before, after)
    assert not result.equal
    assert {change.path[0] for change in result.changes} == {"tables", "rule_areas"}


def test_subsheet_path_is_content_but_not_followed(tmp_path: Path) -> None:
    before = Schematic(
        sheets=[
            Sheet(
                uuid="sheet",
                fields=[
                    Property(name="Sheetfile", value=str(tmp_path / "missing.kicad_sch")),
                ],
            )
        ]
    )
    after = before.model_copy(deep=True)
    after.sheets[0].fields[0].value = "different-missing.kicad_sch"
    assert not compare_content(before, after).equal
    assert not list(tmp_path.iterdir())


def test_existing_footprint_and_symbol_signature_contract_is_unchanged() -> None:
    footprint = Footprint.from_file("tests/fixtures/mod/R_0402_1005Metric.kicad_mod")
    symbols = SymbolLib.from_file("tests/fixtures/sym/474811-E.kicad_sym")
    assert content_signature(footprint) == (
        "kicad-content-v1:sha256:a84e7681cf68314d46bf1473705835b961498041d5a75600eb783a5d31eaadea"
    )
    assert content_signature(symbols) == (
        "kicad-content-v1:sha256:b70469a9e10a1200c649e06386a7f18c75e397571b832cf9bf73b318a00dbfe2"
    )


def test_uuid_case_does_not_hide_duplicate_reference_targets() -> None:
    identity = "abcdefab-1234-1234-1234-123456789abc"
    before = Schematic(lines=[Line(uuid=identity), Line(uuid=identity.upper(), end=(1, 2))])
    after = before.model_copy(deep=True)
    after.lines.reverse()
    assert not compare_content(before, after).equal


def test_added_and_removed_schematic_items_are_reported() -> None:
    before = Schematic(lines=[Line(uuid="00000000-0000-0000-0000-000000000002")])
    after = Schematic(junctions=[Junction(uuid="00000000-0000-0000-0000-000000000003")])
    result = compare_content(before, after)
    assert {(change.path[0], change.kind) for change in result.changes} == {
        ("lines", "removed"),
        ("junctions", "added"),
    }


def test_dictionary_key_types_cannot_collide() -> None:
    before = Schematic(title_block=TitleBlock(comments={1: "text"}))
    after = before.model_copy(deep=True)
    after.title_block.comments = {"1": "text"}
    assert not compare_content(before, after).equal
    after.title_block.comments = {1: "integer", "1": "string"}
    with pytest.raises(TypeError, match="keys"):
        content_signature(after)


@pytest.mark.parametrize(
    "names",
    [
        ("R", ":R"),
        ("R", "R:"),
        ("R", "A:B:R"),
        ("R", "A<B"),
        ("R", "A>B"),
        ("R", 'A"B'),
        ("R", "A\\B"),
        ("R", "A\tB"),
        ("R", "A\nB"),
        ("R", "A\rB"),
    ],
)
def test_uncertain_embedded_names_keep_order(names: tuple[str, str]) -> None:
    before = Schematic(lib_symbols=[Symbol(name=names[0]), Symbol(name=names[1], in_bom=False)])
    after = before.model_copy(deep=True)
    after.lib_symbols.reverse()
    assert not compare_content(before, after).equal


@pytest.mark.parametrize(
    "ids",
    [
        ("1", "00000001"),
        (
            "abcdefab123412341234123456789abc",
            "abcdefab-1234-1234-1234-123456789abc",
        ),
    ],
)
def test_noncanonical_identifiers_keep_order(ids: tuple[str, str]) -> None:
    before = Schematic(lines=[Line(uuid=ids[0]), Line(uuid=ids[1], end=(1, 2))])
    after = before.model_copy(deep=True)
    after.lines.reverse()
    assert not compare_content(before, after).equal


def test_identifier_checks_follow_size_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    import eedesigncore.comparison as comparison

    class Identifier(str):
        def casefold(self) -> str:
            pytest.fail("Identifier normalization ran before the input size check.")

    original = comparison._Budget
    monkeypatch.setattr(comparison, "_Budget", lambda: original(encoding=2000))
    schematic = Schematic(symbols=[PlacedSymbol(lib_id="R")])
    schematic.symbols[0].uuid = Identifier("x" * 10_000)
    with pytest.raises(ValueError, match="size limit"):
        content_signature(schematic)


def test_malformed_identifier_is_an_explicit_error() -> None:
    schematic = Schematic(symbols=[PlacedSymbol(lib_id="R")])
    schematic.symbols[0].uuid = 123
    with pytest.raises(TypeError, match="identifiers"):
        content_signature(schematic)
