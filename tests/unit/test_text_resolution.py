"""KiCad shown text retains its source and reports missing context."""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

from eedesigncore import (
    Board,
    Footprint,
    Paper,
    PlacedSymbol,
    Property,
    Schematic,
    SchText,
    Symbol,
    SymbolUnit,
    SymText,
    TextResolutionContext,
    ViewBox,
    prepare_schematic_view,
    render_schematic_svg,
    resolve_document_text,
    resolve_text,
)
from eedesigncore.common.page import TitleBlock
from eedesigncore.common.pcb_family.board_items import Table, TableCell
from eedesigncore.common.pcb_family.shapes import PcbText
from eedesigncore.sch.models import SchTable, SchTableCell


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+3*4", "14"),
        ("-2^2", "-4"),
        ("2^3^2", "512"),
        ("2^-2", "0.25"),
        ("4k7 + 1k", "48000"),
        ("1mm + 2cm", "21"),
        ("1in / 1mil", "1000"),
        ("1e-3 * 1M", "1000"),
        ("-5 % 3", "-2"),
        ("3>=2", "1"),
        ('"x" + 2', "x2"),
        ('"1" == "01"', "0"),
        ('"1" == 1', "1"),
        ('if(1,"yes","no")', "yes"),
        ('concat("a",2,"c")', "a2c"),
        ('upper("abc")', "ABC"),
        ('lower("ABC")', "abc"),
        ('beforefirst("ab:cd:ef", ":")', "ab"),
        ('beforelast("ab:cd:ef", ":")', "ab:cd"),
        ('afterfirst("ab:cd:ef", ":")', "cd:ef"),
        ('afterlast("ab:cd:ef", ":")', "ef"),
        ("round(-2.5)", "-3"),
        ("round(12.345,2)", "12.35"),
        ("sum(1,2,3)", "6"),
        ("avg(2,4)", "3"),
        ("min(3,2,4)", "2"),
        ("max(3,2,4)", "4"),
        ("abs(-5)", "5"),
        ("sqrt(9)", "3"),
        ("pow(3,2)", "9"),
        ("floor(1.8)", "1"),
        ("ceil(1.2)", "2"),
        ("shunt(100,100)", "50"),
        ("db(100)", "20"),
        ("dbv(100)", "40"),
        ("fromdb(20)", "100"),
        ("fromdbv(40)", "100"),
        ("format(2,3)", "2.000"),
        ("fixed(2)", "2.00"),
        ('currency(2,"USD")', "USD2.00"),
        ('enearest(990,"E24")', "910"),
        ('eup(100,"E24")', "110"),
        ('edown(100,"E24")', "91"),
        ('enearest(100,"E192")', "100"),
        ("dateformat(0)", "1970-01-01"),
        ('datestring("1970-01-02")', "1"),
        ("weekdayname(0)", "Thursday"),
        ('dateformat(-1,"US")', "12/31/1969"),
    ],
)
def test_expression_source_semantics(expression, expected):
    raw = "@{" + expression + "}"
    result = resolve_text(raw)
    assert result.raw == raw
    assert result.resolved == expected
    assert result.complete


def test_nested_variables_and_protected_literals():
    context = TextResolutionContext(variables={"NAME": "A", "A": "@{2+3}", "FIELD5": "done"})
    result = resolve_text(r"${${NAME}} ${FIELD@{2+3}} \${A} \@{1+2}", context)
    assert result.resolved == "5 done ${A} @{1+2}"
    assert result.complete


@pytest.mark.parametrize(
    "raw",
    [
        "@{1/0}",
        "@{if(1,2,1/0)}",
        "@{unknown(1)}",
        '@{__import__("os")}',
        "@{2**3}",
        "@{1+}",
        "${missing}",
        "@{today()}",
        "@{vcsbranch()}",
    ],
)
def test_failure_preserves_text_and_other_expressions(raw):
    result = resolve_text(raw + " @{1+1}")
    assert result.resolved == raw + " 2"
    assert not result.complete


def test_cycles_and_limits_are_reported():
    result = resolve_text("${A}", TextResolutionContext(variables={"A": "${B}", "B": "${A}"}))
    assert not result.complete
    assert result.diagnostics[0].code == "resolution_limit"
    result = resolve_text("${A}", TextResolutionContext(variables={"A": "x" * 100}, max_length=20))
    assert result.resolved == "${A}"
    assert not result.complete


def test_runtime_inputs_are_explicit():
    context = TextResolutionContext(
        now=datetime(1970, 1, 2, 3, 4, 5, tzinfo=UTC),
        random=lambda: 0.25,
        function_provider=lambda name, args: "main" if name == "vcsbranch" else None,
    )
    result = resolve_text(
        "${CURRENT_DATE} ${CURRENT_TIME_HH_MM_SS} @{today()} @{random()} @{vcsbranch()}", context
    )
    assert result.resolved == "1970-01-02 03h04m05s 1 0.25 main"
    assert result.complete


def test_owner_fields_and_cross_references_do_not_change_source():
    source = Schematic(
        title_block=TitleBlock(title="${PROJECT}"),
        symbols=[
            PlacedSymbol(
                lib_id="Device:R",
                uuid="symbol",
                fields=[
                    Property(name="Reference", value="R1"),
                    Property(name="Value", value="${LOCAL}"),
                    Property(name="LOCAL", value="4k7"),
                    Property(name="Comment", value="${value}"),
                ],
            )
        ],
        texts=[SchText(content="${R1:Value} ${symbol:Comment} ${TITLE}")],
    )
    before = source.model_dump()
    result = resolve_document_text(
        source, TextResolutionContext(variables={"LOCAL": "wrong", "PROJECT": "Probe"})
    )
    assert result.document.texts[0].content == "4k7 4k7 Probe"
    assert result.texts["$.symbols[0].fields[1].value"].raw == "${LOCAL}"
    assert source.model_dump() == before


def test_tables_use_different_schematic_and_board_indices():
    sch = Schematic(
        tables=[
            SchTable(
                column_count=2,
                cells=[
                    SchTableCell(content="${ROW}/${COL}/${ADDR}"),
                    SchTableCell(content='${CELL("A0")}'),
                ],
            )
        ]
    )
    result = resolve_document_text(sch)
    assert [cell.content for cell in result.document.tables[0].cells] == ["0/0/A0", "0/0/A0"]
    board = Board(
        drawings=[Table(column_count=1, cells=[TableCell(content="${ROW}/${COL}/${ADDR}")])]
    )
    assert resolve_document_text(board).document.drawings[0].cells[0].content == "1/1/A1"


def test_board_footprint_scope_and_layer():
    fp = Footprint(
        fp_id="Test:Part",
        properties=[Property(name="Reference", value="U1"), Property(name="Value", value="part")],
        drawings=[PcbText(content="${REFERENCE} ${LAYER}", layer="B.SilkS")],
    )
    board = Board(footprints=[fp], drawings=[PcbText(content="${u1:VALUE}", layer="F.SilkS")])
    result = resolve_document_text(board)
    assert result.document.footprints[0].drawings[0].content == "U1 B.SilkS"
    assert result.document.drawings[0].content == "part"


def test_svg_resolves_symbol_graphics_per_placement():
    class Font:
        def __init__(self):
            self.texts = []

        def text_strokes(self, text, size, position, thickness, **kwargs):
            self.texts.append(text)
            return [[position, (position[0] + 1000, position[1])]]

    definition = Symbol(
        name="Probe", units=[SymbolUnit.for_symbol("Probe", items=[SymText(content="${VALUE}")])]
    )
    sheet = Schematic(
        lib_symbols=[definition],
        symbols=[
            PlacedSymbol(lib_id="Probe", fields=[Property(name="Value", value=value)])
            for value in ("first", "second")
        ],
    )
    before = sheet.model_dump()
    font = Font()
    svg = render_schematic_svg(
        sheet, view_box=ViewBox(0, 0, 100, 100), font=font, text_context=TextResolutionContext()
    )
    ET.fromstring(svg)
    assert "first" in font.texts and "second" in font.texts
    assert "${VALUE}" not in font.texts
    assert sheet.model_dump() == before


def test_viewer_preserves_raw_and_shown_text():
    sheet = Schematic(paper=Paper(), texts=[SchText(content="@{2+3}")])
    view = prepare_schematic_view(sheet, document_id="probe")
    page = next(iter(view.pages.values()))
    assert page.resolved_texts["$.texts[0].content"].raw == "@{2+3}"
    assert page.resolved_texts["$.texts[0].content"].resolved == "5"
    assert sheet.texts[0].content == "@{2+3}"


def test_pin_and_label_net_names_use_connectivity():
    from eedesigncore import Label, Pin

    definition = Symbol(
        name="Probe",
        units=[
            SymbolUnit.for_symbol(
                "Probe",
                items=[
                    Pin(
                        number="1",
                        name="IN",
                        position=(0, 0),
                        electrical_type="input",
                        shape="line",
                    )
                ],
            )
        ],
    )
    sheet = Schematic(
        uuid="root",
        lib_symbols=[definition],
        symbols=[
            PlacedSymbol(
                uuid="symbol",
                lib_id="Probe",
                position=(20, 20),
                fields=[
                    Property(name="Reference", value="U1"),
                    Property(name="Value", value="${NET_NAME(1)} ${PIN_NAME(1)} ${NET_CLASS(1)}"),
                ],
            )
        ],
        labels=[
            Label(
                uuid="label",
                kind="label",
                content="SUPPLY",
                position=(20, 20),
                fields=[Property(name="Comment", value="${NET_NAME}/${SHORT_NET_NAME}")],
            )
        ],
    )
    resolved = resolve_document_text(sheet)
    assert resolved.document.symbols[0].fields[1].value == "/SUPPLY IN Default"
    assert resolved.document.labels[0].fields[0].value == "/SUPPLY/SUPPLY"
    assert resolved.complete


def test_repeated_sheet_occurrences_and_title_precedence(tmp_path):
    from eedesigncore import (
        InstancePath,
        InstanceProject,
        Sheet,
        SheetHierarchy,
        SheetNode,
        context_for_sheet,
    )

    child = Schematic(
        uuid="child",
        title_block=TitleBlock(title="child title"),
        symbols=[
            PlacedSymbol(
                lib_id="Probe",
                instances=[
                    InstanceProject(
                        name="project",
                        paths=[
                            InstancePath(path="/root/a", reference="U1", unit=1),
                            InstancePath(path="/root/b", reference="U2", unit=1),
                        ],
                    )
                ],
                fields=[
                    Property(name="Reference", value="U1"),
                    Property(name="Value", value="${NAME} ${#} ${TITLE}"),
                ],
            )
        ],
    )
    a = SheetNode(
        path=tmp_path / "child.kicad_sch",
        schematic=child,
        name="a",
        kiid_path="/root/a",
        page="2",
        sheet=Sheet(fields=[Property(name="NAME", value="first")]),
    )
    b = SheetNode(
        path=tmp_path / "child.kicad_sch",
        schematic=child,
        name="b",
        kiid_path="/root/b",
        page="3",
        sheet=Sheet(fields=[Property(name="NAME", value="second")]),
    )
    root = SheetNode(
        path=tmp_path / "root.kicad_sch",
        schematic=Schematic(uuid="root"),
        name="root",
        kiid_path="/root",
        children=[a, b],
    )
    hierarchy = SheetHierarchy(
        project_root=tmp_path, project_path=tmp_path / "project.kicad_pro", roots=[root]
    )
    shown_a = resolve_document_text(child, context_for_sheet(hierarchy, a.kiid_path))
    shown_b = resolve_document_text(child, context_for_sheet(hierarchy, b.kiid_path))
    assert [field.value for field in shown_a.document.symbols[0].fields] == [
        "U1",
        "first 2 child title",
    ]
    assert [field.value for field in shown_b.document.symbols[0].fields] == [
        "U2",
        "second 3 child title",
    ]
    assert shown_b.texts["$.symbols[0].fields[0].value"].raw == "U1"
    assert child.symbols[0].fields[0].value == "U1"


def test_effective_netclass_name_includes_default_contribution():
    from eedesigncore.pro.models import ProjectFile
    from eedesigncore.pro.tree import JsonTree
    from eedesigncore.text_resolution.netclasses import names_for_nets

    project = ProjectFile(
        root=JsonTree(
            {
                "net_settings": {
                    "netclass_assignments": {"VCC": ["Power", "Other"]},
                    "netclass_patterns": [{"pattern": "VC*", "netclass": "Match"}],
                    "classes": [
                        {"name": "Power", "priority": 0},
                        {"name": "Other", "priority": 2},
                        {"name": "Match", "priority": 1},
                    ],
                }
            }
        )
    )
    assert names_for_nets(["VCC", "GND"], project) == {
        "VCC": "Power,Match,Other,Default",
        "GND": "Default",
    }


def test_table_circular_reference_is_visible():
    sheet = Schematic(
        tables=[SchTable(column_count=1, cells=[SchTableCell(content='${CELL("A0")}')])]
    )
    result = resolve_document_text(sheet)
    assert result.document.tables[0].cells[0].content == "<Circular reference>"


def test_runtime_git_uses_explicit_repository_and_confines_file_paths(tmp_path):
    import os
    import shutil
    import subprocess

    from eedesigncore import GitTextResolver

    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    root = tmp_path / "repo"
    root.mkdir()
    env = dict(
        os.environ,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Probe",
        GIT_AUTHOR_EMAIL="probe@example.invalid",
        GIT_COMMITTER_NAME="Probe",
        GIT_COMMITTER_EMAIL="probe@example.invalid",
    )

    def command(*args):
        return subprocess.run(
            [git, *args], cwd=root, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    command("init", "-b", "probe")
    (root / "part.txt").write_text("first", encoding="utf-8")
    command("add", "part.txt")
    command("commit", "-m", "Initial test file")
    original = command("rev-parse", "HEAD")
    command("tag", "-a", "first", "-m", "First test tag")
    (root / "other.txt").write_text("second", encoding="utf-8")
    command("add", "other.txt")
    command("commit", "-m", "Second test file")
    resolver = GitTextResolver(root)
    assert resolver("vcsbranch", ()) == "probe"
    assert resolver("vcsfileidentifier", ("part.txt", 4.0)) == original[:4]
    assert resolver("vcsnearestlabel", ()) == "first"
    assert resolver("vcslabeldistance", ()) == "1"
    assert resolver("vcsdirty", ()) == "0"
    assert resolver.variable("", "VCSSHORTHASH") == command("rev-parse", "HEAD")[:8]
    with pytest.raises(ValueError, match="inside"):
        resolver("vcsfileidentifier", ("../../outside.txt",))


def test_saved_kicad_expression_results():
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/text_resolution/expressions.json").read_text(
            encoding="utf-8"
        )
    )
    context = TextResolutionContext(variables=fixture["variables"])
    for case in fixture["cases"]:
        assert resolve_text(case["raw"], context).resolved == case["expected"], case["raw"]


def test_directive_does_not_shift_label_connection_identity():
    from eedesigncore import Label

    sheet = Schematic(
        uuid="root",
        labels=[
            Label(kind="netclass_flag", position=(10, 10)),
            Label(
                kind="global_label",
                content="VCC",
                position=(20, 20),
                fields=[Property(name="Shown", value="${NET_NAME}")],
            ),
            Label(
                kind="label",
                content="GND",
                position=(30, 30),
                fields=[Property(name="Shown", value="${NET_NAME}")],
            ),
        ],
    )
    result = resolve_document_text(sheet)
    assert result.document.labels[1].fields[0].value == "VCC"
    assert result.document.labels[2].fields[0].value == "/GND"


def test_sheet_pin_connection_type_and_explicit_net_context():
    from eedesigncore import Sheet, SheetPin

    sheet = Schematic(
        uuid="root",
        sheets=[
            Sheet(
                uuid="child",
                pins=[SheetPin(uuid="pin", name="${CONNECTION_TYPE} ${NET_NAME}", shape="output")],
            )
        ],
    )
    context = TextResolutionContext(
        resolve_connections=False, connections={("/root", "pin", ""): "/DATA"}
    )
    result = resolve_document_text(sheet, context)
    assert result.document.sheets[0].pins[0].name == "Output /DATA"
    assert result.complete
    assert sheet.sheets[0].pins[0].name == "${CONNECTION_TYPE} ${NET_NAME}"


def test_dimension_format_preserves_raw_expression():
    from eedesigncore.common.pcb_family.board_items import Dimension, DimensionFormat

    board = Board(
        drawings=[Dimension(dim_format=DimensionFormat(prefix="${PREFIX}", suffix="@{1+2}"))]
    )
    result = resolve_document_text(board, TextResolutionContext(variables={"PREFIX": "Length "}))
    assert result.document.drawings[0].dim_format.prefix == "Length "
    assert result.document.drawings[0].dim_format.suffix == "3"
    assert result.texts["$.drawings[0].dim_format.suffix"].raw == "@{1+2}"


def test_escaped_cross_reference_stays_literal():
    sheet = Schematic(
        symbols=[
            PlacedSymbol(
                lib_id="Probe",
                fields=[
                    Property(name="Reference", value="U1"),
                    Property(name="Value", value=r"\${A}"),
                ],
            )
        ],
        texts=[SchText(content="${U1:VALUE}")],
    )
    result = resolve_document_text(sheet, TextResolutionContext(variables={"A": "wrong"}))
    assert result.document.texts[0].content == "${A}"
    assert result.complete


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('@{if(${__CICD}==1,"removed"," ")', " "),
        ("prefix @{1+2", "prefix 3"),
        ("${MISSING", "${MISSING}"),
    ],
)
def test_native_end_of_text_closes_variables_and_expressions(raw, expected):
    result = resolve_text(raw, TextResolutionContext(variables={"__CICD": "0"}))
    assert result.raw == raw
    assert result.resolved == expected
    assert result.complete == ("MISSING" not in raw)
