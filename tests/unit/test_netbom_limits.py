"""Input bounds and standalone netbom export behavior."""

import json
from pathlib import Path

import pytest

from eedesigncore import Board, Property, Schematic, build_netbom, load_hierarchy, load_netbom
from eedesigncore.exceptions import SheetHierarchyError
from eedesigncore.netbom import NetbomDocument, NetbomRecord
from eedesigncore.netbom.__main__ import main
from eedesigncore.pro import JsonTree, ProjectFile
from eedesigncore.sch.models import Sheet


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_is_rejected(number: str) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        NetbomDocument.from_json(
            '{"design_id":"x","records":{"x":{"id":"x","kind":"source","data":{"n":'
            + number
            + "}}}}"
        )


def test_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        NetbomDocument.from_json('{"design_id":"first","design_id":"second"}')


def test_deep_json_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        NetbomDocument.from_json("[" * 70 + "0" + "]" * 70)


def test_foreign_relationship_kind_is_rejected() -> None:
    document = NetbomDocument(
        design_id="x",
        records={"r": NetbomRecord(id="r", kind="relationship", data={"relationship_kind": []})},
    )
    with pytest.raises(ValueError, match="kind"):
        NetbomDocument.from_json(document.to_json())


@pytest.mark.parametrize(
    "path",
    ["//example.invalid/share/design.kicad_pcb", "https://example.invalid/design.kicad_pcb"],
)
def test_remote_path_refused_before_resolution(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolve(*args, **kwargs):
        pytest.fail("Remote input reached path resolution")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(ValueError, match="local"):
        load_netbom(path)


def test_repeated_sheet_loading_is_bounded(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.kicad_sch"
    Schematic().to_file(leaf)
    root = tmp_path / "root.kicad_sch"
    Schematic(
        sheets=[Sheet(fields=[Property(name="Sheetfile", value=leaf.name)]) for _ in range(5)]
    ).to_file(root)
    with pytest.raises(SheetHierarchyError, match="occurrence"):
        load_hierarchy(root, max_occurrences=3)
    with pytest.raises(SheetHierarchyError, match="total byte"):
        load_hierarchy(root, max_total_bytes=1)
    with pytest.raises(ValueError, match="positive"):
        load_hierarchy(root, max_occurrences=0)


def test_project_without_schematic_retains_settings(tmp_path: Path) -> None:
    path = tmp_path / "design.kicad_pro"
    ProjectFile(root=JsonTree({"text_variables": {"NOTE": "test"}})).to_file(path)
    result = load_netbom(path)
    assert result.find("kind", "project_settings")
    assert result.coverage["schematic"].status == "partial"
    assert result.find("kind", "issue")


def test_source_text_remains_data_in_serialized_json() -> None:
    result = build_netbom(
        project=ProjectFile(root=JsonTree({"text_variables": {"NOTE": "</script>&"}}))
    )
    text = result.to_json()
    assert "</script>" not in text
    assert json.loads(text)["records"]
    assert "</script>&" in str(NetbomDocument.from_json(text).model_dump())


def test_duplicate_links_stop_discovery() -> None:
    document = NetbomDocument(
        design_id="x",
        records={"c": NetbomRecord(id="c", kind="component", links={"pins": ["c"] * 10000})},
    )
    with pytest.raises(ValueError, match="scan limit"):
        document.component_view("c", max_records=1)
    with pytest.raises(ValueError, match="scan limit"):
        document.neighborhood("c", max_records=1)


def test_command_line_json_and_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "design.kicad_pcb"
    Board().to_file(path)
    monkeypatch.setattr("sys.argv", ["netbom", str(path)])
    main()
    result = NetbomDocument.from_json(capsys.readouterr().out)
    assert result.coverage["board_assignments"].status == "complete"
    monkeypatch.setattr("sys.argv", ["netbom", str(path), "--format", "jsonl"])
    main()
    lines = capsys.readouterr().out.splitlines()
    assert "manifest" in json.loads(lines[0])
    assert all("kind" in json.loads(line) for line in lines[1:])
