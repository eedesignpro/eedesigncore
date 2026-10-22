"""Circuit context, physical pad locations, and bounded command-line output."""

import json
import subprocess
import sys
from copy import deepcopy

import pytest

from eedesigncore import NetbomAssembly, NetbomDocument, portable_inspection_source
from eedesigncore.assembly import (
    AssemblyEvidence,
    ConnectorEndpoint,
    ConnectorMate,
    ConnectorMateDefinition,
    ConnectorPinPair,
)
from eedesigncore.inspection import DesignInspector


def circuit_document():
    records = {}

    def add(key, kind, data=None, links=None):
        records[key] = {"id": key, "kind": kind, "data": data or {}, "links": links or {}}

    add("board", "board")
    for net in ("INPUT", "OUTPUT", "EARTH"):
        add(net, "net", {"name": net}, {"pins": []})
    for reference, assignments in (
        ("R1", ("INPUT", "OUTPUT")),
        ("J17", ("OUTPUT", "INPUT", "EARTH")),
    ):
        pins, pads = [], []
        for index, net in enumerate(assignments, 1):
            pin, pad = f"{reference}:{index}", f"{reference}:pad:{index}"
            pins.append(pin)
            pads.append(pad)
            add(
                pin,
                "pin",
                {"number": str(index)},
                {"component": [reference], "nets": [net], "pads": [pad]},
            )
            add(
                pad,
                "pad",
                {
                    "number": str(index),
                    "board_position": [10 + index, 20],
                    "local_position": [index, 0],
                    "layers": ["F.Cu"],
                    "saved_pin_function": "K" if index == 1 else "A",
                },
                {"component": [reference], "nets": [net], "pin": [pin]},
            )
            records[net]["links"]["pins"].append(pin)
        add(
            reference,
            "component",
            {"reference": reference, "value": "stored part"},
            {"board": ["board"], "pins": pins, "pads": pads},
        )
    add("unmatched-symbol", "component", {"reference": "J17"})
    return NetbomDocument.model_validate({"design_id": "test", "records": records})


def inspector():
    return DesignInspector(circuit_document().model_dump(mode="json"))


def query(view, operation, **inputs):
    return view.execute(operation, inputs)["data"]


def test_circuit_includes_other_nets_without_claiming_internal_connections():
    result = query(inspector(), "circuit", name="INPUT", scope="board")
    assert result["total"] == 2
    connector = next(row for row in result["items"] if row["component"]["reference"] == "J17")
    assert {net["name"] for pin in connector["pins"] for net in pin["nets"]} == {
        "INPUT",
        "OUTPUT",
        "EARTH",
    }
    assert connector["conduction"] == "not_evaluated"
    assert connector["relationships"] == []
    assert connector["pins"][0]["on_selected_net"]
    assert connector["pins"][0]["pads"][0]["saved_pin_function"] == "A"


def test_scope_resolves_board_reference_without_merging_unmatched_symbol():
    view = inspector()
    with pytest.raises(ValueError, match="Candidate IDs"):
        query(view, "component", reference="J17")
    assert query(view, "component", reference="J17", scope="board")["total"] == 3
    assert query(view, "component", reference="J17", scope="schematic_only")["total"] == 0
    with pytest.raises(ValueError, match="outside"):
        query(view, "component", id="unmatched-symbol", scope="board")
    with pytest.raises(ValueError, match="Scope"):
        query(view, "circuit", name="INPUT", scope="unknown")


def test_circuit_pages_and_nested_limits_are_explicit():
    view = inspector()
    first = query(view, "circuit", name="INPUT", limit=1, pin_limit=1)
    assert first["items"][0]["pins_truncated"]
    assert first["items"][0]["pins"][0]["on_selected_net"]
    second = query(
        view, "circuit", name="INPUT", limit=1, pin_limit=1, cursor=first["next_cursor"]
    )
    assert first["items"][0]["component"]["id"] != second["items"][0]["component"]["id"]
    assert second["next_cursor"] is None
    with pytest.raises(ValueError, match="another snapshot or query"):
        query(view, "circuit", name="OUTPUT", limit=1, pin_limit=1, cursor=first["next_cursor"])


@pytest.mark.parametrize("limit", [0, 65, True, "3"])
def test_circuit_rejects_bad_pin_limits(limit):
    with pytest.raises(ValueError, match="pin limit"):
        query(inspector(), "circuit", name="INPUT", pin_limit=limit)


def test_pads_preserve_duplicate_numbers_and_missing_positions():
    document = circuit_document().model_dump(mode="json")
    records = document["records"]
    duplicate = deepcopy(records["R1:pad:1"])
    duplicate["id"] = "duplicate-pad"
    duplicate["data"].pop("board_position")
    records["duplicate-pad"] = duplicate
    records["R1"]["links"]["pads"].append("duplicate-pad")
    records["R1:1"]["links"]["pads"].append("duplicate-pad")
    view = DesignInspector(document)
    first = query(view, "pads", reference="R1", number="1", limit=1)
    pad = first["items"][0]
    assert pad["board_position"] == [11, 20]
    assert pad["local_position"] == [1, 0]
    assert pad["coordinate_units"] == "mm"
    assert pad["coordinate_frame"] == "board"
    second = query(view, "pads", reference="R1", number="1", limit=1, cursor=first["next_cursor"])
    assert second["items"][0]["position_status"] == "unavailable"
    assert first["total"] == 2
    assert query(view, "pin", reference="R1", number="1")["items"][0]["pad_count"] == 2


def test_pad_preview_is_bounded_and_complete_pad_query_is_paged():
    document = circuit_document().model_dump(mode="json")
    records = document["records"]
    for index in range(12):
        pad = deepcopy(records["R1:pad:1"])
        pad["id"] = f"extra:{index}"
        records[pad["id"]] = pad
        records["R1:1"]["links"]["pads"].append(pad["id"])
        records["R1"]["links"]["pads"].append(pad["id"])
    view = DesignInspector(document)
    pin = query(view, "pin", reference="R1", number="1")["items"][0]
    assert len(pin["pads"]) == 8
    assert pin["pad_count"] == 13
    assert pin["pads_truncated"]
    assert query(view, "pads", reference="R1", number="1")["total"] == 13


def assembled():
    result = NetbomAssembly(assembly_id="test-system")
    result.add_board("base", circuit_document())
    result.add_board("module", circuit_document())
    evidence = AssemblyEvidence(
        source="test", locator="contact table", derivation="inferred", method="test hypothesis"
    )
    result.add_mate_definition(
        "contacts",
        ConnectorMateDefinition(
            pin_pairs=[ConnectorPinPair(left="1", right="1")], evidence=evidence
        ),
    )
    result.mate_connectors(
        "mate",
        ConnectorMate(
            definition="contacts",
            left=ConnectorEndpoint(instance="base", component_id="J17"),
            right=ConnectorEndpoint(instance="module", component_id="J17"),
            evidence=evidence,
        ),
    )
    return result


def test_system_circuit_crosses_only_mapped_nets_and_preserves_local_coordinates():
    view = assembled().inspect()
    result = query(view, "circuit", instance="base", name="OUTPUT", scope="board")
    assert {row["instance"] for row in result["items"]} == {"base", "module"}
    assert result["system_net"]["depends_on_inferred_mates"]
    connector = next(
        row
        for row in result["items"]
        if row["instance"] == "module" and row["component"]["id"] == "J17"
    )
    by_number = {pin["number"]: pin for pin in connector["pins"]}
    assert by_number["1"]["system_net_ids"] != by_number["2"]["system_net_ids"]
    assert by_number["1"]["mate_ids"]
    assert not by_number["2"]["mate_ids"]
    assert by_number["1"]["pads"][0]["board_position"] == [11, 20]
    isolated = query(view, "circuit", instance="base", name="INPUT", scope="board")
    assert {row["instance"] for row in isolated["items"]} == {"base"}


def test_system_circuit_requires_unambiguous_net_selector():
    view = assembled().inspect()
    for inputs in ({"instance": "base"}, {"instance": "base", "name": "INPUT", "net_id": "INPUT"}):
        with pytest.raises(ValueError, match="Select one"):
            view.execute("circuit", inputs)


def test_portable_circuit_and_output_budget(tmp_path):
    helper = tmp_path / "inspect_design.py"
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    source = tmp_path / "design.json"
    source.write_text(circuit_document().to_json(), encoding="utf-8")
    command = [sys.executable, "-I", "-S", str(helper), str(source)]
    inputs = {"name": "INPUT", "scope": "board"}
    normal = subprocess.run(
        command + ["circuit", "--inputs", json.dumps(inputs), "--pretty"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(normal.stdout) == inspector().execute("circuit", inputs)
    bounded = subprocess.run(
        command + ["circuit", "--inputs", json.dumps(inputs), "--max-output-bytes", "1024"],
        capture_output=True,
        text=True,
    )
    assert bounded.returncode == 2
    assert not bounded.stdout
    assert len(bounded.stderr.encode()) <= 1024
    assert "output budget" in bounded.stderr
    batch = subprocess.run(
        command + ["--batch", "--max-output-bytes", "1024"],
        input=json.dumps({"operation": "circuit", "inputs": inputs})
        + "\n"
        + json.dumps({"operation": "pads", "inputs": {"reference": "R1", "number": "99"}})
        + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    replies = [json.loads(line) for line in batch.stdout.splitlines()]
    assert not replies[0]["ok"]
    assert replies[1]["ok"]
    assert all(len(line.encode()) < 1024 for line in batch.stdout.splitlines())


def test_portable_assembly_circuit_matches_installed(tmp_path):
    helper = tmp_path / "inspect_design.py"
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    source = tmp_path / "assembly.json"
    item = assembled()
    source.write_text(item.to_json(), encoding="utf-8")
    inputs = {"instance": "base", "name": "OUTPUT", "scope": "board", "limit": 1}
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(helper),
            str(source),
            "circuit",
            "--inputs",
            json.dumps(inputs),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == item.inspect().execute("circuit", inputs)


@pytest.mark.parametrize("layer", ["F.Cu", "B.Cu"])
def test_native_pad_query_preserves_rotation_and_bottom_board_frame(layer):
    from eedesigncore import Board, Footprint, Pad, Property, inspect_design
    from eedesigncore.mod.models import FootprintPlacement

    board = Board(
        footprints=[
            Footprint(
                layer=layer,
                at=FootprintPlacement(position=(10, 20), angle=90),
                properties=[Property(name="Reference", value="Q1")],
                pads=[
                    Pad(
                        number="1",
                        position=(1, 2),
                        layers=[layer],
                        pinfunction="D",
                        net_name="POWER",
                    )
                ],
            )
        ]
    )
    view = inspect_design(board)
    pad = query(view, "pads", reference="Q1")["items"][0]
    assert pad["board_position"] == [12, 19]
    assert pad["local_position"] == [1, 2]
    assert pad["layers"] == [layer]
    assert pad["saved_pin_function"] == "D"
    assert query(view, "pin", reference="Q1", number="1")["items"][0]["pads"][0] == pad


def test_exported_help_survives_json_and_jsonl():
    document = circuit_document()
    conventions = NetbomDocument.from_json(document.to_json()).conventions
    assert (
        "eedesigncore.inspection design.netbom.json usage" in conventions["inspection_installed"]
    )
    assert "coverage" in conventions["inspection_without_tools"]
    assert conventions["inspection_package_source"] == (
        "Not recorded. Install from the package index that distributes this package."
    )
    assert json.loads(next(document.iter_jsonl()))["manifest"]["conventions"] == conventions
    assert all("C:" not in value for value in conventions.values())


def test_portable_command_help_needs_no_input_or_site_packages(tmp_path):
    helper = tmp_path / "inspect_design.py"
    helper.write_text(portable_inspection_source(), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(helper), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for word in ("circuit", "pads", "--batch", "--max-output-bytes", "usage"):
        assert word in result.stdout
    help_data = query(inspector(), "usage")
    assert help_data["operations"]["pads"]["available"]
    assert help_data["scope_values"] == ["all", "board", "schematic_only"]
    assert help_data["examples"]
