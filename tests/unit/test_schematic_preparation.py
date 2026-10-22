"""Public schematic preparation needs no private imports in a viewer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from eedesigncore import (
    Junction,
    Label,
    Line,
    Paper,
    Pin,
    PinAlternate,
    PlacedSymbol,
    PlacedSymbolPin,
    PowerType,
    Property,
    Schematic,
    SchematicView,
    SchRect,
    SchTable,
    SchTableCell,
    SchText,
    Sheet,
    SheetPin,
    Symbol,
    SymbolUnit,
    TitleBlock,
    load_hierarchy,
    prepare_schematic_view,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "schematic_preparation"


def pins_by_net(view):
    items = {item.id: item for page in view.pages.values() for item in page.items.values()}
    return {
        net.name: sorted(
            [items[key].reference, items[key].pin_number]
            for key in net.item_ids
            if items[key].kind == "pin"
        )
        for net in view.nets.values()
        if any(items[key].kind == "pin" for key in net.item_ids)
    }


def fixture_view():
    return prepare_schematic_view(FIXTURE / "root.kicad_pro", document_id="test")


def test_captured_kicad_netlist_and_source_preservation():
    paths = sorted(FIXTURE.glob("*.kicad_*"))
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    view = fixture_view()
    assert pins_by_net(view) == json.loads(
        (FIXTURE / "expected_nets.json").read_text(encoding="utf-8")
    )
    assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert view.coverage.electrical == "complete"
    assert SchematicView.from_json(view.to_json()) == view
    assert prepare_schematic_view(FIXTURE / "root.kicad_sch", document_id="test").nets == view.nets


def test_every_electrical_item_has_drawing_and_resolved_membership():
    view = fixture_view()
    for page in view.pages.values():
        drawing_ids = {
            node.attrib["data-item-id"]
            for node in ET.fromstring(page.svg).iter()
            if "data-item-id" in node.attrib
        }
        assert all(
            item.id in drawing_ids and item.bounds is not None for item in page.items.values()
        )
        for item in page.items.values():
            if item.kind not in {"sheet", "symbol"}:
                assert item.net_ids or item.bus_ids
            for net in item.net_ids:
                assert item.id in view.nets[net].item_ids
    assert {page.page_number for page in view.pages.values()} == {"1", "2", "3"}
    local = [net for net in view.nets.values() if (net.name or "").endswith("/DATA0")]
    assert len(local) == 2 and not set(local[0].item_ids) & set(local[1].item_ids)
    ground = next(net for net in view.nets.values() if net.name == "GND")
    assert (
        sum(
            item.kind == "pin" and item.id in ground.item_ids
            for page in view.pages.values()
            for item in page.items.values()
        )
        == 2
    )
    assert len(view.buses) == 2
    assert all(len(bus.net_ids) == 2 and not bus.unresolved_members for bus in view.buses.values())


def test_stable_identities_after_source_order_and_reference_changes():
    hierarchy = load_hierarchy(FIXTURE / "root.kicad_sch")
    first = prepare_schematic_view(hierarchy, document_id="test")
    visited = set()
    for node in hierarchy.walk():
        if id(node.schematic) in visited:
            continue
        visited.add(id(node.schematic))
        sheet = node.schematic
        sheet.symbols.reverse()
        sheet.lines.reverse()
        sheet.labels.reverse()
        sheet.lib_symbols.reverse()
        for symbol in sheet.symbols:
            for project in symbol.instances:
                for selection in project.paths:
                    selection.reference = selection.reference.replace("U", "IC")
    second = prepare_schematic_view(hierarchy, document_id="test")
    assert set(first.pages) == set(second.pages)
    assert set(first.nets) == set(second.nets)
    assert set(first.buses) == set(second.buses)
    assert {key for page in first.pages.values() for key in page.items} == {
        key for page in second.pages.values() for key in page.items
    }


def simple_sheet():
    return Schematic(
        uuid="root",
        paper=Paper(paper_type="A4"),
        lib_symbols=[
            Symbol(
                name="T:S",
                units=[
                    SymbolUnit(
                        name="S_1_1",
                        items=[
                            SchRect(start=(-2, 2), end=(2, -2)),
                            Pin(
                                number="1",
                                name="DEFAULT",
                                position=(-5, 0),
                                electrical_type="passive",
                                shape="line",
                                alternates=[
                                    PinAlternate(
                                        name="SELECTED", electrical_type="input", shape="clock"
                                    )
                                ],
                            ),
                            Pin(
                                number="2",
                                name="HIDDEN",
                                position=(5, 0),
                                electrical_type="passive",
                                shape="line",
                                visible=False,
                            ),
                        ],
                    )
                ],
            )
        ],
        symbols=[
            PlacedSymbol(
                uuid="symbol",
                lib_id="T:S",
                position=(50, 50),
                pins=[PlacedSymbolPin(number="1", uuid="pin1", alternate="SELECTED")],
                fields=[
                    Property(name="Reference", value="U1", position=(70, 50)),
                    Property(name="Value", value="Wide field", position=(70, 55)),
                ],
            )
        ],
    )


@pytest.mark.parametrize("angle,mirror", [(0, None), (90, None), (180, "x"), (270, "y")])
def test_bounds_include_body_pin_text_and_fields_without_changing_source(angle, mirror):
    source = simple_sheet()
    source.symbols[0].angle = angle
    source.symbols[0].mirror = mirror
    source.symbols[0].dnp = True
    before = source.model_dump()
    view = prepare_schematic_view(source, document_id="bounds")
    page = next(iter(view.pages.values()))
    symbol = next(item for item in page.items.values() if item.kind == "symbol")
    assert symbol.body_bounds is not None and symbol.bounds is not None
    assert symbol.body_bounds.x < 48 and symbol.body_bounds.x + symbol.body_bounds.width > 52
    assert symbol.body_bounds.y < 48 and symbol.body_bounds.y + symbol.body_bounds.height > 52
    assert symbol.bounds.x <= symbol.body_bounds.x
    assert symbol.bounds.y <= symbol.body_bounds.y
    assert symbol.bounds.x + symbol.bounds.width >= symbol.body_bounds.x + symbol.body_bounds.width
    assert (
        symbol.bounds.y + symbol.bounds.height >= symbol.body_bounds.y + symbol.body_bounds.height
    )
    assert symbol.bounds.x + symbol.bounds.width > 70
    pins = [item for item in page.items.values() if item.kind == "pin"]
    chosen = next(pin for pin in pins if pin.pin_number == "1")
    hidden = next(pin for pin in pins if pin.pin_number == "2")
    assert chosen.pin_name == "SELECTED" and chosen.electrical_type == "input" and chosen.drawn
    assert not hidden.drawn and hidden.bounds is None and hidden.net_ids
    assert source.model_dump() == before


def test_missing_definition_reports_both_drawing_and_electrical_coverage():
    source = simple_sheet()
    source.lib_symbols = []
    view = prepare_schematic_view(source, document_id="missing")
    page = next(iter(view.pages.values()))
    assert page.coverage.drawing == "unavailable"
    assert page.coverage.electrical == "partial"
    assert {"missing_symbol_definition", "drawing_unavailable"} <= {
        issue.code for issue in view.coverage.issues
    }
    assert not view.nets


def test_missing_sheet_reports_incomplete_hierarchy(tmp_path):
    for path in FIXTURE.glob("root.kicad_*"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(ValueError):
        prepare_schematic_view(tmp_path / "root.kicad_pro", document_id="missing")
    view = prepare_schematic_view(tmp_path / "root.kicad_pro", document_id="missing", strict=False)
    assert len(view.pages) == 1 and view.coverage.electrical == "partial"
    assert any(issue.code == "incomplete_hierarchy" for issue in view.coverage.issues)


def test_wire_only_and_unnamed_bus_are_separate():
    source = Schematic(
        uuid="root",
        paper=Paper(paper_type="A4"),
        lines=[
            Line(uuid="wire", start=(10, 10), end=(20, 10)),
            Line(uuid="bus", line_type="bus", start=(10, 20), end=(20, 20)),
        ],
    )
    view = prepare_schematic_view(source, document_id="undriven")
    assert len(view.nets) == 1 and next(iter(view.nets.values())).name is None
    assert len(view.buses) == 1 and not next(iter(view.buses.values())).net_ids
    assert any(issue.code == "unnamed_bus" for issue in view.coverage.issues)


def test_duplicate_and_unannotated_references_do_not_create_false_connections():
    source = simple_sheet()
    duplicate = source.symbols[0].model_copy(deep=True)
    duplicate.uuid = "duplicate"
    duplicate.position = (100, 100)
    source.symbols.append(duplicate)
    view = prepare_schematic_view(source, document_id="ambiguous")
    assert not view.nets and view.coverage.electrical == "unavailable"
    assert any(issue.code == "ambiguous_annotation" for issue in view.coverage.issues)
    source.symbols = source.symbols[:1]
    source.symbols[0].fields[0].value = "U?"
    assert (
        prepare_schematic_view(source, document_id="ambiguous").coverage.electrical
        == "unavailable"
    )


def test_crossing_wires_join_only_with_a_junction():
    source = Schematic(
        uuid="root",
        paper=Paper(paper_type="A4"),
        lines=[
            Line(uuid="a", start=(10, 20), end=(30, 20)),
            Line(uuid="b", start=(20, 10), end=(20, 30)),
        ],
    )
    assert len(prepare_schematic_view(source, document_id="crossing").nets) == 2
    source.junctions = [Junction(uuid="junction", position=(20, 20))]
    view = prepare_schematic_view(source, document_id="crossing")
    assert len(view.nets) == 1 and len(next(iter(view.nets.values())).item_ids) == 3


@pytest.mark.parametrize(
    "path", ["https://example.invalid/design.kicad_sch", "\\\\server\\share\\design.kicad_pro"]
)
def test_refuse_remote_source_before_loading(path):
    with pytest.raises(ValueError, match="local source"):
        prepare_schematic_view(path, document_id="remote")


@pytest.mark.parametrize("variant", ["hierarchical_bus", "alternate"])
def test_captured_bus_and_alternate_netlists(variant):
    folder = FIXTURE / variant
    view = prepare_schematic_view(folder / "root.kicad_pro", document_id="test")
    assert pins_by_net(view) == json.loads(
        (folder / "expected_nets.json").read_text(encoding="utf-8")
    )
    if variant == "hierarchical_bus":
        assert len(view.buses) == 1
        assert len(next(iter(view.buses.values())).net_ids) == 2
    else:
        for page in view.pages.values():
            for pin in page.items.values():
                if pin.kind == "pin" and pin.pin_number == "4":
                    assert pin.pin_name == "SELECTED" and pin.electrical_type == "no_connect"
                    assert view.nets[pin.net_ids[0]].name.endswith("-OUT-Pad4)")


def test_duplicate_pin_numbers_have_distinct_geometric_identities():
    source = simple_sheet()
    pin = source.lib_symbols[0].units[0].items[1].model_copy(deep=True)
    pin.position = (-5, 2)
    source.lib_symbols[0].units[0].items.append(pin)
    view = prepare_schematic_view(source, document_id="duplicate-pins")
    pins = [
        item
        for page in view.pages.values()
        for item in page.items.values()
        if item.pin_number == "1"
    ]
    assert len(pins) == 2 and len({pin.id for pin in pins}) == 2
    assert len({pin.position for pin in pins}) == 2


def test_bounds_enclose_arc_extremes_and_cubic_control_hull():
    from eedesigncore.render.geometry import SvgShape
    from eedesigncore.render.schematic_metadata import shape_bounds

    quarter = shape_bounds(SvgShape("path", (("d", "M1,0 A1,1 0 0 1 0,1"),)), 0.2)
    assert quarter.min_x == pytest.approx(-0.1001)
    assert quarter.max_x == pytest.approx(1.1001)
    assert quarter.min_y == pytest.approx(-0.1001)
    assert quarter.max_y == pytest.approx(1.1001)
    large = shape_bounds(SvgShape("path", (("d", "M1,0 A1,1 0 1 0 0,1"),)), 0.2)
    assert large.min_x == pytest.approx(-1.1001)
    assert large.min_y == pytest.approx(-1.1001)
    curve = shape_bounds(SvgShape("path", (("d", "M0,0 C10,20 20,-10 30,0"),)), 0.2)
    assert curve.min_y < -10 and curve.max_y > 20


def test_project_variables_and_bus_aliases_use_loaded_project():
    hierarchy = load_hierarchy(FIXTURE / "root.kicad_pro")
    hierarchy.project.root.set("text_variables", {"LINK_NAME": "LINK"})
    hierarchy.project.root.set("schematic", {"bus_aliases": {"DATA_BUS": ["DATA0", "DATA1"]}})
    visited = set()
    for node in hierarchy.walk():
        if id(node.schematic) in visited:
            continue
        visited.add(id(node.schematic))
        for label in node.schematic.labels:
            if label.content == "LINK":
                label.content = "${LINK_NAME}"
            if label.content == "DATA[0..1]":
                label.content = "{DATA_BUS}"
    before = hierarchy.project.to_string()
    view = prepare_schematic_view(hierarchy, document_id="variables")
    assert pins_by_net(view) == json.loads(
        (FIXTURE / "expected_nets.json").read_text(encoding="utf-8")
    )
    assert len(view.buses) == 2
    assert all(len(bus.net_ids) == 2 for bus in view.buses.values())
    assert hierarchy.project.to_string() == before


@pytest.mark.parametrize("text", ["DATA[0..999999999]", "{RECURSIVE}"])
def test_bus_expansion_is_bounded(text):
    hierarchy = load_hierarchy(FIXTURE / "root.kicad_pro")
    hierarchy.root.schematic.labels.append(
        Label(uuid="hostile-label", kind="label", content=text, position=(20, 20))
    )
    hierarchy.project.root.set("schematic", {"bus_aliases": {"RECURSIVE": ["{RECURSIVE}"]}})
    with pytest.raises(ValueError, match="limit"):
        prepare_schematic_view(hierarchy, document_id="bounded")


def test_unresolved_label_variables_do_not_join_literal_placeholders():
    source = simple_sheet()
    source.labels = [
        Label(
            uuid="unknown",
            kind="global_label",
            content="${UNKNOWN}",
            position=(45, 50),
            shape="input",
        )
    ]
    view = prepare_schematic_view(source, document_id="unknown")
    assert not view.nets and view.coverage.electrical == "unavailable"
    assert any(issue.code == "unresolved_text_variable" for issue in view.coverage.issues)


def test_portable_membership_validation_rejects_one_sided_links():
    data = fixture_view().model_dump(mode="json")
    net = next(iter(data["nets"].values()))
    item_id = net["item_ids"][0]
    for page in data["pages"].values():
        if item_id in page["items"]:
            page["items"][item_id]["net_ids"] = []
    with pytest.raises(ValueError, match="both directions"):
        SchematicView.model_validate(data)


def test_loaded_native_assembly_keeps_electrical_records():
    from eedesigncore import AssemblyWorkspace, NetbomAssembly

    hierarchy = load_hierarchy(FIXTURE / "root.kicad_pro")
    workspace = AssemblyWorkspace(NetbomAssembly(assembly_id="system"))
    workspace.add_design("board", hierarchy)
    view = workspace.schematic_view()
    assert len(view.pages) == 4 and view.nets
    restored = SchematicView.from_json(view.to_json())
    assert restored.nets == view.nets


@pytest.mark.parametrize(
    "path",
    [
        "M10,20 H30 V40 Q40,50 50,40 L10,20 Z",
        "m10,20 h20 v20 q10,10 20,0 l-40,-20 z",
    ],
)
def test_outline_path_hitbox_commands(path):
    from eedesigncore.render.geometry import SvgShape
    from eedesigncore.render.schematic_metadata import shape_bounds

    box = shape_bounds(SvgShape("path", (("d", path),)), 0)
    assert box is not None
    assert box.min_x == pytest.approx(9.9999)
    assert box.min_y == pytest.approx(19.9999)
    assert box.max_x == pytest.approx(50.0001)
    assert box.max_y == pytest.approx(50.0001)


def test_closed_path_resets_relative_start_and_rejects_unknown_commands():
    from eedesigncore.render.geometry import SvgShape
    from eedesigncore.render.schematic_metadata import shape_bounds

    box = shape_bounds(SvgShape("path", (("d", "M10,20 h10 v10 z m2,3 h1"),)), 0)
    assert box is not None
    assert box.max_x == pytest.approx(20.0001)
    assert box.max_y == pytest.approx(30.0001)
    with pytest.raises(ValueError, match="Unsupported"):
        shape_bounds(SvgShape("path", (("d", "M0,0 X10,20"),)), 0)


@pytest.mark.parametrize("drawing", ["text", "table", "title", "git", "value"])
def test_display_diagnostics_preserve_nets_buses_and_membership(drawing):
    source = load_hierarchy(FIXTURE / "root.kicad_pro")
    baseline = prepare_schematic_view(source, document_id="display")
    sheet = source.root.schematic
    if drawing == "text":
        sheet.texts.append(SchText(content="${MISSING_DRAWING_NOTE}"))
    elif drawing == "table":
        sheet.tables.append(
            SchTable(column_count=1, cells=[SchTableCell(content="${MISSING_CELL}")])
        )
    elif drawing == "title":
        sheet.title_block = TitleBlock(title="${MISSING_TITLE}")
    elif drawing == "git":
        sheet.texts.append(SchText(content="@{vcsbranch()} ${VCSSHORTHASH}"))
    else:
        symbol = next(
            node.schematic.symbols[0] for node in source.walk() if node.schematic.symbols
        )
        symbol.fields.append(Property(name="Description", value="${MISSING_DESCRIPTION}"))
        next(field for field in symbol.fields if field.name == "Value").value = "${MISSING_VALUE}"
    before = source.model_dump()
    actual = prepare_schematic_view(source, document_id="display")
    assert actual.nets == baseline.nets
    assert actual.buses == baseline.buses
    assert actual.coverage.electrical == baseline.coverage.electrical == "complete"
    for key, page in actual.pages.items():
        assert {key: (item.net_ids, item.bus_ids) for key, item in page.items.items()} == {
            key: (item.net_ids, item.bus_ids) for key, item in baseline.pages[key].items.items()
        }
    issues = [
        issue for issue in actual.coverage.issues if issue.code == "unresolved_text_variable"
    ]
    assert issues and all(issue.scope == "drawing" for issue in issues)
    assert source.model_dump() == before


@pytest.mark.parametrize(
    "name", ["label", "expression", "sheet_pin", "sheet_name", "reference", "power"]
)
def test_unresolved_electrical_names_still_disable_membership(name):
    source = simple_sheet()
    unknown = "${MISSING_NET_NAME}"
    if name in {"label", "expression"}:
        source.labels = [
            Label(
                kind="global_label",
                content=unknown if name == "label" else "@{unknown_function()}",
            )
        ]
    elif name in {"sheet_pin", "sheet_name"}:
        source.sheets = [
            Sheet(
                uuid="child",
                pins=[SheetPin(name=unknown)] if name == "sheet_pin" else [],
                fields=[Property(name="Sheetname", value=unknown)] if name == "sheet_name" else [],
            )
        ]
    elif name == "reference":
        source.symbols[0].fields[0].value = unknown
    else:
        source.lib_symbols[0].power_type = PowerType.global_
        source.symbols[0].fields[1].value = unknown
    view = prepare_schematic_view(source, document_id="uncertain")
    assert not view.nets and not view.buses
    assert view.coverage.electrical == "unavailable"
    assert any(
        issue.scope == "electrical" and issue.code == "unresolved_text_variable"
        for issue in view.coverage.issues
    )
    assert all(
        not item.net_ids and not item.bus_ids
        for page in view.pages.values()
        for item in page.items.values()
    )
