"""Independent inspection query bounds, selection, and portable parity checks."""

from copy import deepcopy

import pytest

from eedesigncore.inspection import DesignInspector
from eedesigncore.netbom import NetbomDocument, NetbomRecord


def document() -> dict:
    rows = [
        NetbomRecord(
            id="component:r1",
            kind="component",
            data={
                "reference": "R1",
                "value": "10k",
                "side": "top",
                "placement": {"position": [0, 0], "angle": 0},
            },
            links={"pins": ["pin:r1:1"]},
        ),
        NetbomRecord(
            id="component:r2",
            kind="component",
            data={
                "reference": "R2",
                "value": "20k",
                "side": "top",
                "placement": {"position": [3, 4], "angle": 0},
            },
            links={"pins": ["pin:r2:1"]},
        ),
        NetbomRecord(
            id="pin:r1:1",
            kind="pin",
            data={"number": "1"},
            links={"component": ["component:r1"], "nets": ["net:in"]},
        ),
        NetbomRecord(
            id="pin:r2:1",
            kind="pin",
            data={"number": "1"},
            links={"component": ["component:r2"], "nets": ["net:in"]},
        ),
        NetbomRecord(
            id="net:in", kind="net", data={"name": "IN"}, links={"pins": ["pin:r1:1", "pin:r2:1"]}
        ),
        NetbomRecord(
            id="issue:one",
            kind="issue",
            data={"code": "example", "message": "text <script> is data"},
            links={"subjects": ["component:r1"]},
        ),
    ]
    records = {row.id: row for row in rows}
    indexes = {
        "kind": {},
        "reference": {"R1": ["component:r1"], "R2": ["component:r2"]},
        "net_name": {"IN": ["net:in"]},
    }
    for row in rows:
        indexes["kind"].setdefault(row.kind, []).append(row.id)
    return NetbomDocument(
        design_id="design:test", revision_id="revision:test", records=records, indexes=indexes
    ).model_dump(mode="json")


def test_search_pages_keep_all_records_and_input_unchanged() -> None:
    source = document()
    original = deepcopy(source)
    inspector = DesignInspector(source)
    first = inspector.execute("search", {"kind": "component", "limit": 1})
    assert first["schema_version"] == "design-inspection-1"
    assert first["operation"] == "search"
    assert isinstance(first["revision_id"], str) and first["revision_id"]
    assert first["data"]["total"] == 2
    second = inspector.execute(
        "search", {"kind": "component", "limit": 1, "cursor": first["data"]["next_cursor"]}
    )
    identifiers = {row["id"] for row in first["data"]["items"] + second["data"]["items"]}
    assert identifiers == {"component:r1", "component:r2"}
    assert second["data"]["next_cursor"] is None
    assert source == original


def test_search_combines_kind_text_and_exact_field() -> None:
    inspector = DesignInspector(document())
    result = inspector.execute("search", {"kind": "component", "field": "value", "equals": "20k"})
    assert [row["id"] for row in result["data"]["items"]] == ["component:r2"]
    result = inspector.execute("search", {"kind": "component", "text": "no-such-part"})
    assert result["data"]["items"] == []


@pytest.mark.parametrize("limit", [0, -1, 201, 1.5, True, "2"])
def test_search_limit_rejects_invalid_values(limit: object) -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("search", {"limit": limit})


def test_unknown_operation_and_malformed_cursor_fail() -> None:
    inspector = DesignInspector(document())
    with pytest.raises(ValueError):
        inspector.execute("invented_operation")
    with pytest.raises(ValueError):
        inspector.execute("search", {"cursor": "not-a-cursor"})


def test_duplicate_references_refuse_silent_first_match() -> None:
    source = document()
    source["records"]["component:r2"]["data"]["reference"] = "R1"
    source["indexes"]["reference"] = {"R1": ["component:r1", "component:r2"]}
    with pytest.raises(ValueError) as error:
        DesignInspector(source).execute("component", {"reference": "R1"})
    assert "component:r1" in str(error.value)
    assert "component:r2" in str(error.value)


def test_portable_geometry_requires_native_data() -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("geometry", {})


def test_issue_filter_preserves_source_text_as_data() -> None:
    result = DesignInspector(document()).execute("issues", {"code": "example"})
    assert result["data"]["items"][0]["message"] == "text <script> is data"


def test_cursor_is_bound_to_revision_and_query() -> None:
    inspector = DesignInspector(document())
    cursor = inspector.execute("search", {"kind": "component", "limit": 1})["data"]["next_cursor"]
    with pytest.raises(ValueError):
        inspector.execute("search", {"kind": "pin", "limit": 1, "cursor": cursor})
    revised = document()
    revised["revision_id"] = "revision:other"
    with pytest.raises(ValueError):
        DesignInspector(revised).execute(
            "search", {"kind": "component", "limit": 1, "cursor": cursor}
        )


@pytest.mark.parametrize(
    "operation,inputs",
    [
        ("component", {"reference": "R1"}),
        ("net", {"name": "IN"}),
        ("pin", {"reference": "R1", "number": "1"}),
    ],
)
def test_selected_inspections_return_bounded_pin_pages(operation: str, inputs: dict) -> None:
    response = DesignInspector(document()).execute(operation, inputs)
    assert response["operation"] == operation
    assert response["data"]["total"] >= 1
    assert response["data"]["items"]
    assert response["data"]["next_cursor"] is None


def test_records_are_complete_and_preserve_untrusted_text() -> None:
    result = DesignInspector(document()).execute("records", {"ids": ["issue:one"]})
    row = result["data"]["items"][0]
    assert row["id"] == "issue:one"
    assert row["data"]["message"] == "text <script> is data"
    assert row["links"] == {"subjects": ["component:r1"]}


@pytest.mark.parametrize("radius", [-1, float("nan"), float("inf"), "5", True])
def test_neighbors_reject_invalid_radius(radius: object) -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("neighbors", {"reference": "R1", "radius_mm": radius})


def test_trace_requires_explicit_relationship_kinds() -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("trace", {"from_net": "IN", "to_net": "IN"})


def test_unknown_record_ids_do_not_silently_disappear() -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("records", {"ids": ["component:r1", "missing"]})


def test_portable_source_matches_installed_query_without_site_packages(tmp_path) -> None:
    import json
    import subprocess
    import sys

    from eedesigncore.inspection import portable_inspection_source

    source = portable_inspection_source()
    payload = document()
    expected = DesignInspector(payload).execute("search", {"kind": "component", "limit": 1})
    runner = tmp_path / "standalone_inspection_probe.py"
    runner.write_text(
        "import json\n"
        + "namespace = {'__name__': 'portable_probe'}\n"
        + f"exec({source!r}, namespace)\n"
        + f"inspector = namespace['DesignInspector']({payload!r})\n"
        + "print(json.dumps(inspector.execute('search', "
        "{'kind':'component','limit':1}), sort_keys=True))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(runner)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("inputs", [[], "", False, 0, ["kind"], [{"kind": "component"}]])
def test_execute_requires_an_object(inputs: object) -> None:
    with pytest.raises(ValueError):
        DesignInspector(document()).execute("search", inputs)


def test_common_nets_and_neighbor_distance_are_specific() -> None:
    inspector = DesignInspector(document())
    common = inspector.execute("common_nets", {"references": ["R1", "R2"]})
    assert [row["id"] for row in common["data"]["items"]] == ["net:in"]
    neighbors = inspector.execute("neighbors", {"reference": "R1", "radius_mm": 5})
    assert [row["id"] for row in neighbors["data"]["items"]] == ["component:r2"]
    assert neighbors["data"]["items"][0]["distance_mm"] == 5
    assert (
        inspector.execute("neighbors", {"reference": "R1", "radius_mm": 4.9})["data"]["items"]
        == []
    )
    assert (
        inspector.execute("neighbors", {"reference": "R1", "radius_mm": 5, "side": "bottom"})[
            "data"
        ]["items"]
        == []
    )


def test_exact_field_search_does_not_conflate_boolean_and_number() -> None:
    source = document()
    source["records"]["component:r1"]["data"]["enabled"] = True
    source["records"]["component:r2"]["data"]["enabled"] = 1
    result = DesignInspector(source).execute("search", {"field": "enabled", "equals": True})
    assert [row["id"] for row in result["data"]["items"]] == ["component:r1"]


def test_open_rejects_duplicate_json_keys(tmp_path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"netbom-1","records":{},"records":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        DesignInspector.open(path)


def test_responses_and_source_are_isolated_from_mutation() -> None:
    source = document()
    inspector = DesignInspector(source)
    first = inspector.execute("records", {"ids": ["component:r1"]})
    first["data"]["items"][0]["data"]["reference"] = "CHANGED"
    source["records"]["component:r1"]["data"]["reference"] = "ALSO_CHANGED"
    result = inspector.execute("component", {"reference": "R1"})
    assert result["data"]["component"]["reference"] == "R1"


def test_native_geometry_filters_and_snapshot_without_mutating_board() -> None:
    from eedesigncore import Board
    from eedesigncore.inspection import inspect_design
    from eedesigncore.pcb.models import TrackSegment

    board = Board(
        tracks=[
            TrackSegment(start=(0, 0), end=(1, 0), layer="F.Cu", net="IN", width=0.2),
            TrackSegment(start=(3, 0), end=(4, 0), layer="B.Cu", net="IN", width=0.2),
            TrackSegment(start=(8, 0), end=(9, 0), layer="F.Cu", net="OUT", width=0.2),
        ]
    )
    original = board.model_dump()
    inspector = inspect_design(board)
    result = inspector.execute("geometry", {"net": "IN", "layer": "F.Cu", "bbox": [-1, -1, 2, 1]})
    assert result["data"]["total"] == 1
    row = result["data"]["items"][0]
    assert row["kind"] == "segment"
    assert row["nets"] == ["IN"]
    assert row["geometry"]["end"] == [1.0, 0.0]
    assert board.model_dump() == original
    board.tracks[0].end = (99, 99)
    again = inspector.execute("geometry", {"net": "IN", "layer": "F.Cu"})
    assert again["data"]["items"][0]["geometry"]["end"] == [1.0, 0.0]


def test_native_geometry_bbox_validation() -> None:
    from eedesigncore import Board
    from eedesigncore.inspection import inspect_design

    inspector = inspect_design(Board())
    for bbox in ([0, 0, -1, -1], [0, 0, 1], [0, 0, float("inf"), 1]):
        with pytest.raises(ValueError):
            inspector.execute("geometry", {"bbox": bbox})


def test_net_page_materializes_only_requested_endpoints(monkeypatch) -> None:
    source = document()
    for index in range(1000):
        identifier = f"pin:extra:{index}"
        source["records"][identifier] = {
            "id": identifier,
            "kind": "pin",
            "data": {"number": str(index)},
            "links": {"component": ["component:r1"], "nets": ["net:in"]},
            "evidence": [],
        }
        source["records"]["net:in"]["links"]["pins"].append(identifier)
    inspector = DesignInspector(source)
    original = inspector._pin
    calls = []

    def counted(key):
        calls.append(key)
        return original(key)

    monkeypatch.setattr(inspector, "_pin", counted)
    result = inspector.execute("net", {"name": "IN", "limit": 5})
    assert result["data"]["total"] == 1002
    assert len(result["data"]["items"]) == 5
    assert len(calls) == 5


def native_board():
    from eedesigncore import Board, Footprint, Pad, Property

    return Board(
        footprints=[
            Footprint(
                uuid="00000000-0000-0000-0000-000000000001",
                properties=[Property(name="Reference", value="R1")],
                pads=[Pad(number="1", layers=["F.Cu"], net_name="IN")],
            )
        ]
    )


def test_native_records_root_list_scalar_selection_and_isolation() -> None:
    from eedesigncore.inspection import inspect_design

    board = native_board()
    original = board.model_dump()
    inspector = inspect_design(board)
    assert inspector.execute("native_sources")["data"]["items"] == [
        {"id": "board", "type": "Board"}
    ]
    assert (
        "footprints"
        in inspector.execute("native_record", {"source": "board"})["data"]["fields_available"]
    )
    assert (
        inspector.execute("native_record", {"source": "board", "path": "footprints"})["data"][
            "length"
        ]
        == 1
    )
    assert (
        inspector.execute(
            "native_record", {"source": "board", "path": "footprints/0/pads/0/number"}
        )["data"]["value"]
        == "1"
    )
    query = {"source": "board", "path": "footprints/0/pads/0", "fields": ["number", "net_name"]}
    assert inspector.execute("native_record", query)["data"]["values"] == {
        "number": "1",
        "net_name": "IN",
    }
    assert board.model_dump() == original
    board.footprints[0].pads[0].net_name = "CHANGED"
    reply = inspector.execute("native_record", query)
    reply["data"]["values"]["net_name"] = "RESPONSE_CHANGED"
    assert inspector.execute("native_record", query)["data"]["values"]["net_name"] == "IN"


@pytest.mark.parametrize(
    "path",
    [
        "__class__",
        "model_dump",
        "footprints/-1",
        "footprints/0/__dict__",
        "../secret",
        "C:/Windows/win.ini",
        "footprints/999999999",
        "footprints//0",
    ],
)
def test_native_record_refuses_nonstored_paths(path) -> None:
    from eedesigncore.inspection import inspect_design

    with pytest.raises(ValueError):
        inspect_design(native_board()).execute("native_record", {"source": "board", "path": path})


def test_native_file_open_retains_source_bytes(tmp_path) -> None:
    from eedesigncore.inspection import open_inspection

    path = tmp_path / "board.kicad_pcb"
    native_board().to_file(path)
    before = path.read_bytes()
    inspector = open_inspection(path)
    values = inspector.execute(
        "native_record",
        {"source": "board", "path": "footprints/0/pads/0", "fields": ["number", "net_name"]},
    )["data"]["values"]
    assert values == {"number": "1", "net_name": "IN"}
    assert path.read_bytes() == before


def test_complete_jsonl_import_preserves_all_records(tmp_path) -> None:
    from eedesigncore.inspection import open_inspection

    source = NetbomDocument.model_validate(document())
    path = tmp_path / "design.jsonl"
    path.write_text("\n".join(source.iter_jsonl()) + "\n", encoding="utf-8")
    inspector = open_inspection(path)
    rows = inspector.execute("records", {"ids": sorted(source.records)})["data"]["items"]
    assert {row["id"]: row for row in rows} == {
        key: value.model_dump(mode="json") for key, value in source.records.items()
    }
