"""Copied-project evidence, identity, inspection, and source-preservation checks."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    DesignInspector,
    NetbomDocument,
    NetbomSourceFile,
    NetbomSourceManifest,
    build_netbom,
    load_netbom,
    open_inspection,
    portable_inspection_source,
)
from eedesigncore.common.source_reads import _active, _capture_reads, _record_read
from tests.unit.test_project_hierarchy import _sheet, _uuid, _write_pro, _write_sch


@pytest.fixture
def copied_project(tmp_path: Path) -> Path:
    original = tmp_path / "original"
    original.mkdir()
    fixture = Path(__file__).parents[1] / "fixtures/pcb/pad_shapes_probe.kicad_pcb"
    shutil.copy2(fixture, original / "demo.kicad_pcb")
    _write_sch(original / "child.kicad_sch", uuid=_uuid(20))
    _write_sch(
        original / "demo.kicad_sch",
        uuid=_uuid(1),
        sheets=[
            _sheet(uuid=_uuid(2), file_name="child.kicad_sch"),
            _sheet(uuid=_uuid(3), file_name="child.kicad_sch"),
        ],
    )
    _write_pro(original / "demo.kicad_pro")
    return shutil.copytree(original, tmp_path / "copy")


def inspector(document: NetbomDocument) -> DesignInspector:
    return DesignInspector(document.model_dump(mode="json"))


def test_exact_read_files_repeated_sheets_and_preservation(copied_project: Path) -> None:
    before = {p.name: p.read_bytes() for p in copied_project.iterdir()}
    document = load_netbom(copied_project / "demo.kicad_pro")
    manifest = document.source_manifest()
    assert isinstance(manifest, NetbomSourceManifest)
    assert manifest.mode == "files"
    assert manifest.schematic_dependencies == "complete"
    assert {f.path for f in manifest.files} == set(before)
    for entry in manifest.files:
        assert entry.status == "read"
        assert entry.sha256 == hashlib.sha256(before[entry.path]).hexdigest()
        assert entry.size_bytes == len(before[entry.path])
        assert entry.source_ids
        assert all(document.records[key].kind == "source" for key in entry.source_ids)
    entries = {f.path: f for f in manifest.files}
    assert entries["demo.kicad_pro"].read_count == 2
    assert entries["child.kicad_sch"].read_count == 1
    assert entries["child.kicad_sch"].role == "schematic_dependency"
    assert entries["demo.kicad_sch"].role == "root_schematic"
    assert len(document.find("kind", "sheet")) == 3
    assert before == {p.name: p.read_bytes() for p in copied_project.iterdir()}
    assert NetbomDocument.from_json(document.to_json()).source_manifest() == manifest


def test_relocation_and_native_inspection(copied_project: Path, tmp_path: Path) -> None:
    document = load_netbom(copied_project / "demo.kicad_pro")
    relocated = shutil.copytree(copied_project, tmp_path / "relocated")
    second = load_netbom(relocated / "demo.kicad_pro")
    assert document.to_json() == second.to_json()
    native = open_inspection(copied_project / "demo.kicad_pro")
    assert native.execute("source_files") == inspector(document).execute("source_files")


def test_byte_hash_changes_without_changing_engineering_identities(copied_project: Path) -> None:
    source = copied_project / "demo.kicad_pcb"
    first = load_netbom(source)
    source.write_bytes(
        source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") + b"\r\n"
    )
    second = load_netbom(source)
    assert first.source_manifest() != second.source_manifest()
    assert {k: r for k, r in first.records.items() if r.kind != "source_manifest"} == {
        k: r for k, r in second.records.items() if r.kind != "source_manifest"
    }
    assert first.revision_id != second.revision_id


def test_missing_dependency_is_absent_and_unrelated_file_is_not_inspected(
    copied_project: Path,
) -> None:
    (copied_project / "child.kicad_sch").unlink()
    (copied_project / "demo.kicad_dru").write_text("(version 1)\n", encoding="utf-8")
    document = load_netbom(copied_project / "demo.kicad_pro")
    manifest = document.source_manifest()
    assert manifest.schematic_dependencies == "partial"
    missing = next(f for f in manifest.files if f.path == "child.kicad_sch")
    assert missing.status == "absent"
    assert missing.sha256 is None
    assert missing.source_ids == []
    result = inspector(document).execute("source_files", {"path": "demo.kicad_dru"})["data"]
    assert result["status"] == "not_inspected"
    assert result["total"] == 0
    assert "custom_rules" in manifest.not_inspected


def test_alternate_root_and_missing_root_roles(copied_project: Path) -> None:
    _write_pro(
        copied_project / "demo.kicad_pro",
        [("child.kicad_sch", "Child", _uuid(20)), ("missing.kicad_sch", "Missing", _uuid(30))],
    )
    (copied_project / "demo.kicad_sch").unlink()
    document = load_netbom(copied_project / "demo.kicad_pro")
    manifest = document.source_manifest()
    entries = {f.path: f for f in manifest.files}
    assert entries["child.kicad_sch"].role == "root_schematic"
    assert entries["missing.kicad_sch"].role == "root_schematic"
    assert entries["missing.kicad_sch"].status == "absent"
    assert entries["demo.kicad_sch"].status == "absent"
    assert manifest.schematic_dependencies == "partial"


def test_memory_and_legacy_do_not_claim_absence(copied_project: Path) -> None:
    document = build_netbom(Board.from_file(copied_project / "demo.kicad_pcb"))
    manifest = document.source_manifest()
    assert manifest.mode == "in_memory"
    assert manifest.files == []
    assert inspector(document).execute("source_manifest")["data"]["status"] == "not_inspected"
    assert inspector(document).execute("source_files")["data"]["status"] == "not_inspected"
    raw = document.model_dump(mode="json")
    raw["records"] = {k: r for k, r in raw["records"].items() if r["kind"] != "source_manifest"}
    for index in raw["indexes"].values():
        for key, ids in list(index.items()):
            index[key] = [i for i in ids if i in raw["records"]]
    legacy = NetbomDocument.from_json(json.dumps(raw))
    assert legacy.source_manifest() is None
    assert inspector(legacy).execute("source_manifest")["data"]["status"] == "not_recorded"


def test_paging_filters_help_and_cursor_binding(copied_project: Path) -> None:
    tool = inspector(load_netbom(copied_project / "demo.kicad_pro"))
    usage = tool.execute("usage", {"operation": "source_files"})["data"]
    assert usage["operation"] == "source_files"
    assert "path" in usage["definition"]["inputs"]
    summary = tool.execute("source_manifest")["data"]
    assert summary["read_file_count"] == 4
    assert "files" not in summary
    first = tool.execute("source_files", {"limit": 1})["data"]
    second = tool.execute("source_files", {"limit": 1, "cursor": first["next_cursor"]})["data"]
    assert first["items"] != second["items"]
    assert len(first["items"]) == 1
    assert first["total"] == 4
    with pytest.raises(ValueError, match="another snapshot or query"):
        tool.execute("source_files", {"role": "board", "limit": 1, "cursor": first["next_cursor"]})
    assert tool.execute("source_files", {"role": "board"})["data"]["total"] == 1
    assert tool.execute("source_files", {"status": "absent"})["data"]["status"] == "recorded"
    for inputs in ({"role": []}, {"status": {}}, {"path": False}, {"limit": 201}):
        with pytest.raises(ValueError):
            tool.execute("source_files", inputs)


@pytest.mark.parametrize("path", [".", "../secret", "/absolute", "C:/host", "sub\\file", "a/../b"])
def test_manifest_rejects_nonrelative_paths(path: str) -> None:
    with pytest.raises(ValueError):
        NetbomSourceFile(path=path, role="board", status="absent")


def test_invalid_read_claims_and_links(copied_project: Path) -> None:
    with pytest.raises(ValueError):
        NetbomSourceFile(path="x.kicad_pcb", role="board", status="read")
    with pytest.raises(ValueError):
        NetbomSourceFile(path="x.kicad_pcb", role="board", status="absent", sha256="0" * 64)
    entry = NetbomSourceFile(path="x.kicad_pcb", role="board", status="absent")
    with pytest.raises(ValueError, match="duplicate"):
        NetbomSourceManifest(mode="files", files=[entry, entry])
    with pytest.raises(ValueError, match="In-memory"):
        NetbomSourceManifest(files=[entry])
    document = load_netbom(copied_project / "demo.kicad_pro")
    record = document.find("kind", "source_manifest")[0]
    record.data["files"][0]["source_ids"] = ["missing"]
    with pytest.raises(ValueError, match="missing source"):
        NetbomDocument.from_json(document.to_json())


def test_changed_read_fails_and_capture_resets(tmp_path: Path) -> None:
    path = tmp_path / "source"
    with pytest.raises(ValueError, match="changed"), _capture_reads():
        _record_read(path, b"first")
        _record_read(path, b"second")
    assert _active.get() is None
    with _capture_reads() as outer:
        _record_read(path, b"outer")
        with _capture_reads() as inner:
            _record_read(path, b"inner")
        assert outer.reads[path.resolve()][0] != inner.reads[path.resolve()][0]
    assert _active.get() is None


def test_concurrent_extractions_do_not_share_evidence(
    copied_project: Path, tmp_path: Path
) -> None:
    other = shutil.copytree(copied_project, tmp_path / "other")
    (other / "child.kicad_sch").unlink()
    with ThreadPoolExecutor(max_workers=2) as executor:
        documents = list(
            executor.map(
                load_netbom, [copied_project / "demo.kicad_pro", other / "demo.kicad_pro"]
            )
        )
    assert documents[0].source_manifest().schematic_dependencies == "complete"
    assert documents[1].source_manifest().schematic_dependencies == "partial"


def test_portable_helper_and_jsonl(copied_project: Path, tmp_path: Path) -> None:
    document = load_netbom(copied_project / "demo.kicad_pro")
    path = tmp_path / "design.netbom.jsonl"
    path.write_text("\n".join(document.iter_jsonl()), encoding="utf-8")
    expected = open_inspection(path).execute("source_files", {"limit": 1})
    assert (
        expected["data"]["items"]
        == inspector(document).execute("source_files", {"limit": 1})["data"]["items"]
    )
    runner = tmp_path / "portable.py"
    runner.write_text(portable_inspection_source(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(runner),
            str(path),
            "source_files",
            "--inputs",
            '{"limit":1}',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(result.stdout) == expected


def test_hash_uses_consumed_bytes_without_a_second_read(copied_project: Path, monkeypatch) -> None:
    source = copied_project / "demo.kicad_pcb"
    original = source.read_bytes()
    read_bytes = Path.read_bytes
    calls = []

    def read_then_replace(path):
        raw = read_bytes(path)
        calls.append(path.resolve())
        if path.resolve() == source.resolve():
            path.write_bytes(raw + b"\n")
        return raw

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)
    manifest = load_netbom(source).source_manifest()
    entry = next(f for f in manifest.files if f.role == "board")
    assert entry.sha256 == hashlib.sha256(original).hexdigest()
    assert calls.count(source.resolve()) == 1
    assert sum(f.read_count for f in manifest.files) == len(calls)


def test_changed_project_on_repeated_read_rejects_extraction(
    copied_project: Path, monkeypatch
) -> None:
    source = copied_project / "demo.kicad_pro"
    read_bytes = Path.read_bytes
    calls = 0

    def changed_bytes(path):
        nonlocal calls
        raw = read_bytes(path)
        if path.resolve() == source.resolve():
            calls += 1
            if calls == 2:
                return raw + b"\n"
        return raw

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    with pytest.raises(ValueError, match="changed during extraction"):
        load_netbom(source)
    assert _active.get() is None


def test_root_fallback_preserves_missing_named_root_evidence(copied_project: Path) -> None:
    _write_pro(copied_project / "demo.kicad_pro", [("missing.kicad_sch", "MISSING", _uuid(30))])
    manifest = load_netbom(copied_project / "demo.kicad_pro").source_manifest()
    entries = {f.path: f for f in manifest.files}
    assert entries["demo.kicad_sch"].status == "read"
    assert entries["missing.kicad_sch"].status == "absent"
    assert entries["missing.kicad_sch"].role == "root_schematic"
    assert manifest.schematic_dependencies == "partial"


def test_unknown_manifest_version_is_not_presented_as_supported(copied_project: Path) -> None:
    document = load_netbom(copied_project / "demo.kicad_pro")
    record = document.find("kind", "source_manifest")[0]
    record.data["schema_version"] = "source-evidence-future"
    with pytest.raises(ValueError):
        NetbomDocument.from_json(document.to_json())
    with pytest.raises(ValueError, match="Unsupported"):
        inspector(document).execute("source_manifest")


def test_absent_companions_and_unread_existing_files(copied_project: Path) -> None:
    (copied_project / "demo.kicad_pro").unlink()
    (copied_project / "demo.kicad_sch").unlink()
    manifest = load_netbom(copied_project / "demo.kicad_pcb").source_manifest()
    entries = {f.path: f for f in manifest.files}
    assert entries["demo.kicad_pro"].status == "absent"
    assert entries["demo.kicad_sch"].status == "absent"
    assert "child.kicad_sch" not in entries
    assert manifest.schematic_dependencies == "not_inspected"
