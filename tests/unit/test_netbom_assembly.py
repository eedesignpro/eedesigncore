"""Independent connector assembly and portable system inspection checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eedesigncore.assembly.models import (
    AssemblyEvidence,
    ConnectorEndpoint,
    ConnectorMate,
    ConnectorMateDefinition,
    ConnectorPinPair,
    NetbomAssembly,
)
from eedesigncore.inspection.native import open_inspection, portable_inspection_source
from eedesigncore.netbom.models import NetbomDocument, NetbomRecord


def design():
    records = {
        "j": NetbomRecord(
            id="j", kind="component", data={"reference": "J1"}, links={"pins": ["p1", "p2"]}
        ),
        "p1": NetbomRecord(
            id="p1", kind="pin", data={"number": "1"}, links={"component": ["j"], "nets": ["n1"]}
        ),
        "p2": NetbomRecord(
            id="p2", kind="pin", data={"number": "2"}, links={"component": ["j"], "nets": ["n2"]}
        ),
        "n1": NetbomRecord(id="n1", kind="net", data={"name": "SIGNAL"}, links={"pins": ["p1"]}),
        "n2": NetbomRecord(id="n2", kind="net", data={"name": "RETURN"}, links={"pins": ["p2"]}),
    }
    return NetbomDocument(design_id="test-board", records=records)


def definition(pairs=(("1", "2"), ("2", "1"))):
    return ConnectorMateDefinition(
        pin_pairs=[ConnectorPinPair(left=a, right=b) for a, b in pairs],
        evidence=AssemblyEvidence(source="connector drawing", locator="contact table"),
    )


def assembly(mated=True):
    result = NetbomAssembly(assembly_id="instrument")
    result.add_board("sensor", design())
    result.add_board("controller", design())
    result.add_mate_definition("crossed", definition())
    if mated:
        result.mate_connectors(
            "cable-A",
            ConnectorMate(
                definition="crossed",
                left=result.connector("sensor", "J1"),
                right=result.connector("controller", "J1"),
                evidence=AssemblyEvidence(source="assembly drawing", locator="cable-A"),
            ),
        )
    return result


def data(inspector, query, **inputs):
    return inspector.execute(query, inputs)["data"]


def test_crossed_map_preserves_instances_and_deduplicates_designs():
    item = assembly()
    assert len(item.designs) == 1
    inspector = item.inspect()
    assert data(inspector, "overview")["system_net_count"] == 2
    members = data(inspector, "system_net", instance="sensor", name="SIGNAL")["items"]
    assert {(m["instance"], m["name"]) for m in members} == {
        ("sensor", "SIGNAL"),
        ("controller", "RETURN"),
    }
    pairs = data(inspector, "mate", id="cable-A")["items"]
    assert {(p["left"]["number"], p["right"]["number"]) for p in pairs} == {("1", "2"), ("2", "1")}


def test_same_names_do_not_connect_without_mates():
    inspector = assembly(False).inspect()
    assert data(inspector, "overview")["system_net_count"] == 4
    assert data(inspector, "system_net", instance="sensor", name="SIGNAL")["total"] == 1


def test_snapshots_and_replacement_recompute_without_source_mutation():
    item = assembly()
    old = item.inspect()
    before = item.to_json()
    item.replace_mate_definition("crossed", definition((("1", "1"), ("2", "2"))))
    assert item.to_json() != before
    assert {
        m["name"] for m in data(old, "system_net", instance="sensor", name="SIGNAL")["items"]
    } == {"SIGNAL", "RETURN"}
    assert {
        m["name"]
        for m in data(item.inspect(), "system_net", instance="sensor", name="SIGNAL")["items"]
    } == {"SIGNAL"}
    item.remove_mate("cable-A")
    assert data(item.inspect(), "overview")["system_net_count"] == 4
    item.remove_board("sensor")
    assert len(item.designs) == 1
    item.remove_board("controller")
    assert not item.designs


def test_unmapped_contacts_are_reported_without_invented_connections():
    item = assembly()
    item.replace_mate_definition("crossed", definition((("1", "2"),)))
    inspector = item.inspect()
    assert data(inspector, "issues", code="unmapped_connector_pin")["total"] == 2
    assert data(inspector, "overview")["system_net_count"] == 3


@pytest.mark.parametrize(
    "failure",
    [
        "missing_pin",
        "ambiguous_pin",
        "no_net",
        "no_connect",
        "symbol_no_connect",
        "missing_definition",
        "missing_connector",
        "self_mate",
        "multiple_mates",
    ],
)
def test_invalid_mates_never_partially_connect(failure):
    item = assembly()
    board = design()
    if failure == "missing_pin":
        item.replace_mate_definition("crossed", definition((("1", "2"), ("3", "1"))))
    elif failure == "ambiguous_pin":
        board.records["p2"].data["number"] = "1"
    elif failure == "no_net":
        board.records["p2"].links["nets"] = []
    elif failure in ("no_connect", "symbol_no_connect"):
        board.records["nc"] = NetbomRecord(id="nc", kind="no_connect")
        if failure == "no_connect":
            board.records["p2"].links["no_connects"] = ["nc"]
        else:
            board.records["sp"] = NetbomRecord(
                id="sp", kind="symbol_pin", links={"no_connects": ["nc"]}
            )
            board.records["p2"].links["symbol_pins"] = ["sp"]
    elif failure == "missing_definition":
        item.mates["cable-A"].definition = "absent"
    elif failure == "missing_connector":
        item.mates["cable-A"].right.component_id = "absent"
    elif failure == "self_mate":
        item.mates["cable-A"].right = item.mates["cable-A"].left.model_copy()
    elif failure == "multiple_mates":
        item.mates["cable-B"] = item.mates["cable-A"].model_copy(deep=True)
    item.replace_board("sensor", board)
    inspector = item.inspect()
    assert data(inspector, "overview")["resolved_mate_count"] == 0
    assert data(inspector, "overview")["system_net_count"] == 4
    assert data(inspector, "mate", id="cable-A")["items"] == []
    assert data(inspector, "issues")["total"] > 0


def test_expected_connector_and_duplicate_reference_diagnostics():
    item = assembly(False)
    item.expected_connectors = [
        item.connector("sensor", "J1"),
        ConnectorEndpoint(instance="controller", component_id="absent"),
    ]
    assert {r["code"] for r in data(item.inspect(), "issues")["items"]} == {
        "unmated_connector",
        "missing_expected_connector",
    }
    board = design()
    board.records["j2"] = NetbomRecord(id="j2", kind="component", data={"reference": "J1"})
    item.replace_board("sensor", board)
    with pytest.raises(ValueError, match="ambiguous"):
        item.connector("sensor", "J1")


def test_reused_contact_numbers_reject_definition_atomically():
    item = assembly()
    before = item.to_json()
    with pytest.raises(ValueError, match="unique"):
        item.replace_mate_definition("crossed", definition((("1", "1"), ("1", "2"))))
    assert item.to_json() == before
    with pytest.raises(ValueError):
        item.remove_board("sensor")


def test_roundtrip_and_query_paging(tmp_path):
    item = assembly()
    destination = tmp_path / "system.json"
    item.save(destination)
    assert NetbomAssembly.load(destination).to_json() == item.to_json()
    inspector = open_inspection(destination)
    page = data(inspector, "instances", limit=1)
    assert page["total"] == 2
    next_page = data(inspector, "instances", limit=1, cursor=page["next_cursor"])
    assert page["items"] != next_page["items"]
    with pytest.raises(ValueError):
        data(inspector, "search", limit=1, cursor=page["next_cursor"])
    item.remove_mate("cable-A")
    with pytest.raises(ValueError):
        data(item.inspect(), "instances", limit=1, cursor=page["next_cursor"])
    assert data(inspector, "search", kind="component", text="j1")["total"] == 2
    assert (
        data(inspector, "system_net", instance="sensor", name="SIGNAL", view="endpoints")["total"]
        == 2
    )
    assert data(inspector, "connector", instance="sensor", reference="J1")["mates"] == ["cable-A"]


def test_trace_requires_selected_relationship_and_retains_mate_evidence():
    item = assembly()
    board = design()
    board.records["r"] = NetbomRecord(
        id="r",
        kind="relationship",
        data={"relationship_kind": "resistor"},
        links={"nets": ["n1", "n2"]},
    )
    item.replace_board("sensor", board)
    inspector = item.inspect()
    query = dict(
        from_instance="sensor", from_net="SIGNAL", to_instance="controller", to_net="SIGNAL"
    )
    assert not data(inspector, "trace", **query)["found"]
    traced = data(inspector, "trace", relationship_kinds=["resistor"], **query)
    assert traced["found"]
    edge = next(row for row in traced["path"] if row.get("kind") == "mate_connection")
    assert edge["mate_id"] == "cable-A"
    assert edge["mate_evidence"]["source"] == "assembly drawing"
    assert edge["definition_evidence"]["source"] == "connector drawing"


def test_portable_helper_without_installed_packages(tmp_path: Path):
    item = assembly()
    source = tmp_path / "system.json"
    item.save(source)
    helper = tmp_path / "inspect_system.py"
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(helper),
            str(source),
            "system_net",
            "--inputs",
            json.dumps({"instance": "sensor", "name": "SIGNAL"}),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == item.inspect().execute(
        "system_net", {"instance": "sensor", "name": "SIGNAL"}
    )


def test_definition_orientation_uses_left_and_right_pin_numbers():
    item = assembly(False)
    board = design()
    board.records["p1"].data["number"] = "A"
    board.records["p2"].data["number"] = "B"
    item.replace_board("controller", board)
    item.replace_mate_definition("crossed", definition((("A", "2"), ("B", "1"))))
    item.mate_connectors(
        "reversed",
        ConnectorMate(
            definition="crossed",
            left=item.connector("controller", "J1"),
            right=item.connector("sensor", "J1"),
            evidence=AssemblyEvidence(source="assembly", locator="reversed"),
        ),
    )
    inspector = item.inspect()
    assert data(inspector, "overview")["resolved_mate_count"] == 1
    members = data(inspector, "system_net", instance="controller", name="SIGNAL")["items"]
    assert {(m["instance"], m["name"]) for m in members} == {
        ("controller", "SIGNAL"),
        ("sensor", "RETURN"),
    }


def test_content_tampering_rejected_and_returned_results_are_independent():
    item = assembly()
    raw = json.loads(item.to_json())
    next(iter(raw["designs"].values()))["records"]["n1"]["data"]["name"] = "CHANGED"
    with pytest.raises(ValueError, match="content"):
        NetbomAssembly.from_json(json.dumps(raw))
    inspector = item.inspect()
    result = data(inspector, "mate", id="cable-A")
    result["items"][0]["left"]["number"] = "CHANGED"
    assert data(inspector, "mate", id="cable-A")["items"][0]["left"]["number"] == "1"
    before = inspector.execute("overview")
    item.name = "renamed"
    assert inspector.execute("overview") == before


def test_board_query_remains_board_scoped_and_batch_recovers(tmp_path):
    item = assembly()
    inspector = item.inspect()
    result = data(
        inspector, "board_query", instance="sensor", operation="net", inputs={"name": "SIGNAL"}
    )
    assert result["instance"] == "sensor"
    assert result["result"]["data"]["total"] == 1
    source, helper = tmp_path / "assembly.json", tmp_path / "inspect.py"
    item.save(source)
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(helper), str(source), "--batch"],
        input='{"operation":"absent"}\n{"operation":"overview"}\n',
        capture_output=True,
        text=True,
        check=True,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert replies[0]["ok"] is False
    assert replies[1]["ok"] is True
    assert replies[1]["result"] == inspector.execute("overview")


@pytest.mark.parametrize("marker", ["explicit_marker", "declared_no_connect"])
def test_no_connect_status_without_link_blocks_entire_mate(marker):
    item = assembly()
    board = design()
    if marker == "explicit_marker":
        board.records["sp"] = NetbomRecord(
            id="sp", kind="symbol_pin", data={"no_connect_status": marker}
        )
        board.records["p2"].links["symbol_pins"] = ["sp"]
    else:
        board.records["p2"].data["connection_status"] = marker
    item.replace_board("sensor", board)
    inspector = item.inspect()
    assert data(inspector, "overview")["resolved_mate_count"] == 0
    assert data(inspector, "overview")["system_net_count"] == 4


def test_duplicate_expected_connector_is_rejected():
    item = assembly(False)
    endpoint = item.connector("sensor", "J1")
    item.expected_connectors = [endpoint, endpoint.model_copy()]
    with pytest.raises(ValueError, match="duplicates"):
        item.inspect()


def placed_design(distance=2.0):
    board = design()
    for number, position in [("1", [0.0, 0.0]), ("2", [distance, 0.0])]:
        key = "pad" + number
        board.records[key] = NetbomRecord(
            id=key, kind="pad", data={"number": number, "board_position": position}
        )
        board.records["p" + number].links["pads"] = [key]
    board.records["j"].links["pads"] = ["pad1", "pad2"]
    board.records["j"].data["footprint"] = "Connector:two_contacts"
    return board


def test_comparison_hypotheses_page_raw_names_without_creating_mates(tmp_path):
    item = assembly(False)
    left = placed_design()
    left.records["n1"].data["name"] = "/Sensor/SIGNAL"
    item.replace_board("sensor", left)
    item.replace_board("controller", placed_design())
    inspector = item.inspect()
    before = inspector.execute("overview")
    query = dict(
        left_instance="sensor", left_id="j", right_instance="controller", right_id="j", limit=1
    )
    first = data(inspector, "compare_connectors", **query)
    assert first["total"] == 2
    assert first["items"][0]["left_net_names"] == ["/Sensor/SIGNAL"]
    assert first["items"][0]["matching_leaf_names"] == ["signal"]
    assert first["comparison"]["derivation"] == "inferred"
    second = data(inspector, "compare_connectors", cursor=first["next_cursor"], **query)
    assert second["items"][0]["left"] == "2"
    reverse = data(inspector, "compare_connectors", mapping="reverse_numeric", **query)
    assert reverse["items"][0]["right"] == "2"
    assert reverse["comparison"]["named_signal_matches"] == 0
    assert inspector.execute("overview") == before
    source, helper = tmp_path / "assembly.json", tmp_path / "inspect.py"
    item.save(source)
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(helper),
            str(source),
            "compare_connectors",
            "--inputs",
            json.dumps(query),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == inspector.execute("compare_connectors", query)


def test_candidate_ranking_ignores_schematic_only_components_and_does_not_join():
    item = assembly(False)
    item.replace_board("sensor", placed_design())
    item.replace_board("controller", placed_design())
    other = placed_design()
    other.records["n1"].data["name"] = "UNRELATED"
    item.add_board("other", other)
    item.add_board("schematic_only", design())
    inspector = item.inspect()
    result = data(inspector, "mate_candidates", instance="sensor", id="j")
    assert result["total"] == 2
    assert result["items"][0]["right"]["instance"] == "controller"
    assert data(inspector, "overview")["mate_count"] == 0
    assert data(inspector, "overview")["system_net_count"] == 8


def test_inferred_mate_requires_method_and_remains_labeled():
    item = assembly()
    item.mates["cable-A"].evidence = AssemblyEvidence(
        source="pattern comparison", locator="J1", derivation="inferred"
    )
    with pytest.raises(ValueError, match="method"):
        item.inspect()
    item.mates["cable-A"].evidence.method = "Matching contact geometry and signal groups."
    inspector = item.inspect()
    assert data(inspector, "issues", code="inferred_mate")["total"] == 1
    assert data(inspector, "overview")["resolved_mate_count"] == 1
    assert data(inspector, "mate", id="cable-A")["mate"]["evidence"]["derivation"] == "inferred"


@pytest.mark.parametrize(
    "distance,expected,residual",
    [(2.0, "fits", 0.0), (4.0, "conflict", 1.0), (None, "unavailable", None)],
)
def test_placement_check_reports_fit_conflict_and_absent_coordinates(distance, expected, residual):
    item = assembly()
    item.replace_board("sensor", placed_design())
    item.replace_board("controller", design() if distance is None else placed_design(distance))
    inspector = item.inspect()
    before = inspector.execute("overview")
    result = data(
        inspector,
        "placement_check",
        left_instance="sensor",
        right_instance="controller",
        tolerance_mm=0.1,
    )
    assert result["status"] == expected
    assert result["mates"] == ["cable-A"]
    if residual is not None:
        assert result["fit"]["maximum_residual_mm"] == pytest.approx(residual)
        assert result["fit"]["paired_pad_count"] == 2
    else:
        assert result["fit"] is None
        assert result["missing_pad_positions"] == 2
    assert inspector.execute("overview") == before


def test_placement_check_rejects_same_instance_and_negative_tolerance():
    inspector = assembly().inspect()
    with pytest.raises(ValueError):
        data(inspector, "placement_check", left_instance="sensor", right_instance="sensor")
    with pytest.raises(ValueError):
        data(
            inspector,
            "placement_check",
            left_instance="sensor",
            right_instance="controller",
            tolerance_mm=-0.1,
        )
