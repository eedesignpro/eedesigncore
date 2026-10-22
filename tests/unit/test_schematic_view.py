"""Assembly drawing geometry never supplies or changes electrical connectivity."""

import json
import xml.etree.ElementTree as ET

import pytest

from eedesigncore import (
    AssemblyEvidence,
    AssemblyStack,
    AssemblyWorkspace,
    Board,
    ConnectorMate,
    ConnectorMateDefinition,
    ConnectorPinPair,
    InstancePath,
    InstanceProject,
    NetbomAssembly,
    NetbomDocument,
    NetbomRecord,
    Paper,
    PlacedSymbol,
    PlacedSymbolPin,
    Property,
    Schematic,
    SchematicView,
    SchRect,
    SheetHierarchy,
    SheetNode,
    Symbol,
    SymbolUnit,
    ViewBounds,
    prepare_assembly_view,
    prepare_schematic_view,
    view_identity,
)


def design():
    return NetbomDocument(
        design_id="board",
        records={
            "j": NetbomRecord(
                id="j", kind="component", data={"reference": "J1"}, links={"pins": ["p"]}
            ),
            "p": NetbomRecord(
                id="p", kind="pin", data={"number": "1"}, links={"component": ["j"], "nets": ["n"]}
            ),
            "n": NetbomRecord(id="n", kind="net", data={"name": "GND"}, links={"pins": ["p"]}),
        },
    )


def assembly():
    result = NetbomAssembly(assembly_id="system", name="<system & test>")
    for instance in ["base", "module", "isolated"]:
        result.add_board(instance, design())
    evidence = AssemblyEvidence(source="fixture", locator="explicit contact mapping")
    result.add_mate_definition(
        "one",
        ConnectorMateDefinition(
            pin_pairs=[ConnectorPinPair(left="1", right="1")], evidence=evidence
        ),
    )
    result.mate_connectors(
        "link",
        ConnectorMate(
            definition="one",
            left=result.connector("base", "J1"),
            right=result.connector("module", "J1"),
            evidence=evidence,
        ),
    )
    return result


def root(view):
    return view.pages[view.roots[0]]


def test_every_board_visible_and_bundle_contacts_match_system_truth():
    source = assembly()
    before = source.to_json()
    view = prepare_assembly_view(source)
    page = root(view)
    boards = [item for item in page.items.values() if item.kind == "board"]
    assert {item.source.instance for item in boards} == {"base", "module", "isolated"}
    bundle = next(item for item in page.items.values() if item.kind == "bundle")
    assert len(bundle.contacts) == 1
    assert bundle.contacts[0].left_pin.instance == "base"
    summary = source.inspect().execute("system_net", {"instance": "base", "name": "GND"})
    assert bundle.contacts[0].system_net == summary["data"]["system_net"]["id"]
    assert source.inspect().execute("overview")["data"]["system_net_count"] == 2
    assert source.to_json() == before
    assert (
        ET.fromstring(page.svg).find("{http://www.w3.org/2000/svg}text").text == "<system & test>"
    )
    assert view.to_json() == prepare_assembly_view(source).to_json()
    assert SchematicView.from_json(view.to_json()) == view


def test_reordering_input_preserves_generated_bytes():
    source = assembly()
    first = prepare_assembly_view(source).to_json()
    source.instances = dict(reversed(list(source.instances.items())))
    assert prepare_assembly_view(source).to_json() == first


def test_unresolved_mate_is_drawn_without_partial_connections():
    source = assembly()
    source.mate_definitions["one"].pin_pairs.append(ConnectorPinPair(left="2", right="2"))
    bundle = next(
        item
        for item in root(prepare_assembly_view(source)).items.values()
        if item.kind == "bundle"
    )
    assert bundle.status == "unresolved"
    assert bundle.contacts == []
    assert len(bundle.declared_pairs) == 2
    assert len(bundle.route) == 4


def test_missing_board_does_not_create_a_fake_block():
    source = assembly()
    source.mates["link"].right.instance = "missing"
    page = root(prepare_assembly_view(source))
    assert len([item for item in page.items.values() if item.kind == "board"]) == 3
    bundle = next(item for item in page.items.values() if item.kind == "bundle")
    assert bundle.status == "unresolved" and bundle.route == []
    assert any("missing board" in note for note in page.notes)


def test_declared_stack_orders_visible_blocks_and_preserves_evidence():
    source = assembly()
    stack = AssemblyStack(
        name="main",
        instances=["module", "base"],
        evidence=AssemblyEvidence(source="mechanical drawing", locator="stack"),
    )
    page = root(prepare_assembly_view(source, stacks=[stack]))
    boards = [item for item in page.items.values() if item.kind == "board"]
    assert [item.source.instance for item in boards] == ["module", "base", "isolated"]
    assert boards[0].evidence[0].source == "mechanical drawing"
    for previous, following in zip(boards, boards[1:], strict=False):
        assert previous.bounds.y + previous.bounds.height < following.bounds.y
    stack.instances.append("base")
    with pytest.raises(ValueError, match="repeated"):
        prepare_assembly_view(source, stacks=[stack])


def sheet():
    return Schematic(
        uuid="sheet",
        paper=Paper(),
        lib_symbols=[
            Symbol(
                name="Part",
                units=[SymbolUnit.for_symbol("Part", items=[SchRect(start=(-2, 2), end=(2, -2))])],
            )
        ],
        symbols=[
            PlacedSymbol(
                uuid="symbol",
                lib_id="Part",
                position=(20, 20),
                fields=[Property(name="Reference", value="U1")],
                pins=[PlacedSymbolPin(number="1", alternate="GPIO")],
            )
        ],
    )


def test_native_and_assembly_pages_share_contract_and_qualify_repeated_boards():
    source = sheet()
    before = source.model_dump()
    views = {
        instance: prepare_schematic_view(source, document_id="system", instance=instance)
        for instance in ["base", "module"]
    }
    view = prepare_assembly_view(assembly(), native_views=views)
    assert len(view.pages) == 3
    symbol_ids = []
    for page in view.pages.values():
        if page.kind == "native_sheet":
            symbol = next(item for item in page.items.values() if item.kind == "symbol")
            symbol_ids.append(symbol.id)
            assert symbol.pins[0].alternate == "GPIO"
            assert symbol.id in page.svg
            assert page.parent == view.roots[0]
    assert len(set(symbol_ids)) == 2
    assert source.model_dump() == before
    assert SchematicView.from_json(view.to_json()) == view


def test_native_hierarchy_retains_shared_sheet_occurrences(tmp_path):
    native = sheet()
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "root.kicad_sch",
                schematic=native,
                name="root",
                kiid_path="/r",
                children=[
                    SheetNode(
                        path=tmp_path / "child.kicad_sch",
                        schematic=native,
                        name="child",
                        kiid_path="/r/a",
                    ),
                    SheetNode(
                        path=tmp_path / "child.kicad_sch",
                        schematic=native,
                        name="child",
                        kiid_path="/r/b",
                    ),
                ],
            )
        ],
    )
    view = prepare_schematic_view(hierarchy, document_id="native")
    assert len(view.pages) == 3
    assert len(root(view).items) == 3
    assert (
        len(
            {
                next(item.id for item in page.items.values() if item.kind == "symbol")
                for page in view.pages.values()
            }
        )
        == 3
    )


def test_workspace_native_queries_and_snapshot_isolation():
    board = Board()
    workspace = AssemblyWorkspace(NetbomAssembly(assembly_id="native"))
    workspace.add_design("a", board)
    snapshot = workspace.inspect()
    board.generator = "changed"
    result = snapshot.execute(
        "board_query",
        {
            "instance": "a",
            "operation": "native_record",
            "inputs": {"source": "board", "path": "generator"},
        },
    )
    assert "changed" not in json.dumps(result)
    assert snapshot.execute("board_query", {"instance": "a", "operation": "native_sources"})
    workspace.assembly.remove_board("a")
    with pytest.raises(ValueError, match="changed"):
        workspace.inspect()
    assert snapshot.execute("overview")["data"]["instance_count"] == 1


def test_native_attachment_requires_matching_content():
    workspace = AssemblyWorkspace(assembly())
    with pytest.raises(ValueError, match="does not match"):
        workspace.add_design("base", Board())


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<foreignObject/>",
        '<image href="https://example.org/a.png"/>',
        '<rect onclick="alert(1)"/>',
        '<rect fill="url(https://example.org/a)"/>',
    ],
)
def test_saved_views_refuse_active_svg(payload):
    data = prepare_assembly_view(assembly()).model_dump(mode="json")
    data["pages"][data["roots"][0]]["svg"] = (
        '<svg xmlns="http://www.w3.org/2000/svg">' + payload + "</svg>"
    )
    with pytest.raises(ValueError):
        SchematicView.from_json(json.dumps(data))


def test_saved_views_reject_dangling_navigation_and_cycles():
    view = prepare_assembly_view(assembly())
    item = next(iter(root(view).items.values()))
    item.target_pages = ["missing"]
    with pytest.raises(ValueError, match="navigation"):
        view.to_json()
    item.target_pages = []
    root(view).parent = view.roots[0]
    with pytest.raises(ValueError, match="cycle"):
        view.to_json()


def test_identity_qualification_and_invalid_bounds():
    assert view_identity("a/b", "c") != view_identity("a", "b/c")
    with pytest.raises(ValueError):
        ViewBounds(x=0, y=0, width=float("inf"), height=1)
    source = Schematic()
    with pytest.raises(ValueError, match="explicit bounds"):
        prepare_schematic_view(source, document_id="empty")
    assert prepare_schematic_view(
        source,
        document_id="empty",
        sheet_bounds={"/root": ViewBounds(x=0, y=0, width=100, height=100)},
    )


def test_long_labels_fit_board_bounds_and_routes_stay_outside_blocks():
    source = assembly()
    source.name = "Long system title " * 40
    source.designs[next(iter(source.designs))].records["j"].data["reference"] = "x" * 200
    # Rebuild the content key after a deliberate input change.
    modified = next(iter(source.designs.values())).model_copy(deep=True)
    for instance in list(source.instances):
        source.replace_board(instance, modified)
    page = root(prepare_assembly_view(source))
    boards = [item for item in page.items.values() if item.kind == "board"]
    for bundle in (item for item in page.items.values() if item.kind == "bundle"):
        assert bundle.route[1][0] > max(board.bounds.x + board.bounds.width for board in boards)
    for item in page.items.values():
        if item.bounds is not None:
            assert item.bounds.y + item.bounds.height <= page.bounds.height
            assert item.bounds.x + item.bounds.width <= page.bounds.width


def test_native_workspace_view_and_portable_snapshot(tmp_path):
    workspace = AssemblyWorkspace(NetbomAssembly(assembly_id="native"))
    source = sheet()
    workspace.add_design("a", source)
    source.symbols[0].pins[0].alternate = "changed"
    view = workspace.schematic_view()
    symbol = next(
        item
        for page in view.pages.values()
        for item in page.items.values()
        if item.kind == "symbol"
    )
    assert symbol.pins[0].alternate == "GPIO"
    destination = tmp_path / "native.schematic-view.json"
    view.save(destination)
    assert SchematicView.load(destination) == view


def test_expected_unmated_connector_and_inferred_evidence():
    source = assembly()
    source.expected_connectors.append(source.connector("isolated", "J1"))
    source.mate_definitions["one"].evidence.derivation = "inferred"
    source.mate_definitions["one"].evidence.method = "fixture hypothesis"
    page = root(prepare_assembly_view(source))
    assert any(item.status == "unmated" for item in page.items.values())
    bundle = next(item for item in page.items.values() if item.kind == "bundle")
    assert bundle.evidence_derivation == "inferred"
    assert bundle.contacts[0].left_net_name == "GND"


def test_portable_view_rejects_unknown_svg_identity():
    data = prepare_assembly_view(assembly()).model_dump(mode="json")
    data["pages"][data["roots"][0]]["svg"] = (
        '<svg xmlns="http://www.w3.org/2000/svg"><g data-item-id="missing"/></svg>'
    )
    with pytest.raises(ValueError, match="missing item"):
        SchematicView.from_json(json.dumps(data))


def test_unknown_net_name_stays_unknown_without_losing_connectivity():
    source = assembly()
    modified = next(iter(source.designs.values())).model_copy(deep=True)
    modified.records["n"].data.pop("name")
    for instance in list(source.instances):
        source.replace_board(instance, modified)
    bundle = next(
        item
        for item in root(prepare_assembly_view(source)).items.values()
        if item.kind == "bundle"
    )
    assert bundle.contacts[0].left_net_name is None
    assert bundle.status == "resolved"


def test_repeated_sheet_references_are_occurrence_specific(tmp_path):
    native = sheet()
    native.symbols[0].instances = [
        InstanceProject(
            name="project",
            paths=[
                InstancePath(path="/a", reference="U10", unit=1),
                InstancePath(path="/b", reference="U20", unit=1),
            ],
        )
    ]
    hierarchy = SheetHierarchy(
        project_root=tmp_path,
        roots=[
            SheetNode(
                path=tmp_path / "shared.kicad_sch", schematic=native, name=name, kiid_path=path
            )
            for name, path in [("a", "/a"), ("b", "/b"), ("c", "/c")]
        ],
    )
    view = prepare_schematic_view(hierarchy, document_id="repeated")
    assert [next(iter(page.items.values())).label for page in view.pages.values()] == [
        "U10",
        "U20",
        "U1",
    ]
    assert native.symbols[0].fields[0].value == "U1"


def test_open_native_board_reads_once_then_uses_memory(tmp_path):
    path = tmp_path / "board.kicad_pcb"
    Board().to_file(path)
    workspace = AssemblyWorkspace(NetbomAssembly(assembly_id="project"))
    workspace.open_design("board", path)
    before = path.read_bytes()
    assert workspace.inspect().execute(
        "board_query", {"instance": "board", "operation": "native_sources"}
    )
    assert workspace.schematic_view().pages
    assert path.read_bytes() == before


def test_saved_svg_rejects_stylesheet_processing_instruction():
    data = prepare_assembly_view(assembly()).model_dump(mode="json")
    page = data["pages"][data["roots"][0]]
    page["svg"] = '<?xml-stylesheet href="https://example.org/style.css"?>' + page["svg"]
    with pytest.raises(ValueError, match="processing instructions"):
        SchematicView.from_json(json.dumps(data))


def test_native_hierarchy_depth_is_bounded(tmp_path):
    node = SheetNode(
        path=tmp_path / "sheet.kicad_sch",
        schematic=Schematic(paper=Paper()),
        name="root",
        kiid_path="/root",
    )
    hierarchy = SheetHierarchy(project_root=tmp_path, roots=[node])
    for index in range(66):
        child = SheetNode(
            path=node.path,
            schematic=node.schematic,
            name=str(index),
            kiid_path=node.kiid_path + "/child",
        )
        node.children.append(child)
        node = child
    with pytest.raises(ValueError, match="exceeds limits"):
        prepare_schematic_view(hierarchy, document_id="deep")
