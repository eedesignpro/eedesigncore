from pathlib import Path

import pytest

from eedesigncore import (
    GuardedSaveError,
    ProjectFile,
    SourceSnapshot,
    guarded_save,
    load_guarded_document,
    save_guarded_documents,
)


def document(tmp_path, name="test.kicad_pro"):
    path = tmp_path / name
    path.write_bytes(
        b'{\r\n  "meta": {"version": 3},\r\n  "text_variables": {"TEST": "old"}\r\n}\r\n'
    )
    return load_guarded_document(path)


def change(entry):
    entry.document.section("text_variables").set("TEST", "new")


def test_unchanged_model_preserves_noncanonical_bytes_and_time(tmp_path):
    entry = document(tmp_path)
    before = entry.source.path.stat().st_mtime_ns
    result = save_guarded_documents([entry])
    assert result.status == "complete"
    assert result.files[0].status == "unchanged"
    assert entry.source.path.read_bytes() == entry.source.content
    assert entry.source.path.stat().st_mtime_ns == before


def test_edit_preserves_crlf_and_reparses(tmp_path):
    entry = document(tmp_path)
    change(entry)
    result = save_guarded_documents([entry])
    raw = entry.source.path.read_bytes()
    assert result.files[0].status == "saved"
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert ProjectFile.from_file(entry.source.path).section("text_variables").get("TEST") == "new"


def test_stale_source_rejects_all_writes(tmp_path):
    first, second = document(tmp_path), document(tmp_path, "second.kicad_pro")
    change(first)
    second.source.path.write_bytes(second.source.content + b" ")
    with pytest.raises(GuardedSaveError) as caught:
        save_guarded_documents([first, second])
    assert caught.value.result.status == "rejected"
    assert first.source.path.read_bytes() == first.source.content
    assert not list(tmp_path.glob("*.lock"))


def test_dependency_change_rejects_write(tmp_path):
    entry = document(tmp_path)
    dependency = tmp_path / "contract.json"
    dependency.write_bytes(b"old")
    captured = SourceSnapshot.capture(dependency)
    change(entry)
    dependency.write_bytes(b"new")
    with pytest.raises(GuardedSaveError):
        save_guarded_documents([entry], dependencies=[captured])
    assert entry.source.path.read_bytes() == entry.source.content


def test_partial_failure_is_reported_without_rollback(tmp_path, monkeypatch):
    first, second = document(tmp_path), document(tmp_path, "second.kicad_pro")
    change(first)
    change(second)
    replace = guarded_save.os.replace

    def fail_second(source, target):
        if target == second.source.path:
            raise OSError("injected failure")
        replace(source, target)

    monkeypatch.setattr(guarded_save.os, "replace", fail_second)
    with pytest.raises(GuardedSaveError) as caught:
        save_guarded_documents([first, second])
    assert caught.value.result.status == "partial"
    assert [item.status for item in caught.value.result.files] == ["saved", "pending"]
    assert first.source.path.read_bytes() != first.source.content
    assert second.source.path.read_bytes() == second.source.content
    assert set(path.name for path in tmp_path.iterdir()) == {"test.kicad_pro", "second.kicad_pro"}


def test_mid_save_dependency_change_stops_remaining_files(tmp_path, monkeypatch):
    first, second = document(tmp_path), document(tmp_path, "second.kicad_pro")
    change(first)
    change(second)
    dependency = tmp_path / "contract.json"
    dependency.write_bytes(b"old")
    captured = SourceSnapshot.capture(dependency)
    replace = guarded_save.os.replace

    def change_dependency(source, target):
        replace(source, target)
        dependency.write_bytes(b"new")

    monkeypatch.setattr(guarded_save.os, "replace", change_dependency)
    with pytest.raises(GuardedSaveError) as caught:
        save_guarded_documents([first, second], dependencies=[captured])
    assert caught.value.result.status == "partial"
    assert second.source.path.read_bytes() == second.source.content


def test_duplicate_target_is_rejected(tmp_path):
    entry = document(tmp_path)
    with pytest.raises(ValueError, match="distinct"):
        save_guarded_documents([entry, entry])


def test_edited_schematic_reparses_and_second_stale_save_is_rejected(tmp_path):
    from eedesigncore import Schematic

    fixture = Path(__file__).parents[1] / "fixtures" / "schematic_preparation" / "root.kicad_sch"
    target = tmp_path / "root.kicad_sch"
    target.write_bytes(fixture.read_bytes())
    entry = load_guarded_document(target)
    entry.document.symbols[0].dnp = not entry.document.symbols[0].dnp
    assert save_guarded_documents([entry]).files[0].status == "saved"
    assert Schematic.from_file(target).symbols[0].dnp == entry.document.symbols[0].dnp
    with pytest.raises(GuardedSaveError):
        save_guarded_documents([entry])


def test_snapshot_content_is_immutable(tmp_path):
    from dataclasses import FrozenInstanceError

    entry = document(tmp_path)
    with pytest.raises(FrozenInstanceError):
        entry.source.content = b"replacement"
