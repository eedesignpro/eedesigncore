"""Explicit KiCad paths retain precedence and filesystem restrictions."""

import json
import os

import pytest

from eedesigncore import (
    LibTable,
    LibTableEnvironment,
    LibTableResolutionError,
    LibTableResolver,
    LibTableRow,
    resolve_lib_table,
)


def test_explicit_values_override_saved_and_filtered_process_values(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "kicad_common.json").write_text(
        json.dumps({"environment": {"vars": {"PARTS_PATH": "old", "KICAD_CUSTOM": "saved"}}})
    )
    explicit = {"PARTS_PATH": "current", "KICAD_CUSTOM": "explicit", "KIPRJMOD": "wrong"}
    process = {"KICAD_CUSTOM": "process", "UNRELATED_SECRET": "private"}
    before = dict(os.environ)
    resolver = LibTableResolver.discover(
        settings_dir=settings,
        project_dir=tmp_path,
        kicad_root=tmp_path / "install",
        env=process,
        path_variables=explicit,
    )
    assert resolver.environment.lookup("PARTS_PATH") == "current"
    assert resolver.environment.lookup("KICAD_CUSTOM") == "explicit"
    assert resolver.environment.lookup("KIPRJMOD") == tmp_path.as_posix()
    assert resolver.environment.lookup("UNRELATED_SECRET") is None
    assert explicit["KIPRJMOD"] == "wrong"
    assert process == {"KICAD_CUSTOM": "process", "UNRELATED_SECRET": "private"}
    assert dict(os.environ) == before
    resolver.environment.variables["PARTS_PATH"] = "changed"
    assert explicit["PARTS_PATH"] == "current"


def test_project_text_precedence_is_unchanged():
    environment = LibTableEnvironment.discover(
        env={}, path_variables={"CUSTOM": "path"}, project_text_variables={"CUSTOM": "text"}
    )
    assert environment.expand("${CUSTOM}") == "text"


def test_nested_global_table_uses_explicit_path_and_respects_trust(tmp_path):
    settings = tmp_path / "settings"
    assets = tmp_path / "assets"
    settings.mkdir()
    assets.mkdir()
    library = assets / "Parts.pretty"
    library.mkdir()
    LibTable(
        kind="fp",
        version=7,
        rows=[LibTableRow(name="Nested", type="Table", uri="${PARTS_PATH}/fp-lib-table")],
    ).to_file(settings / "fp-lib-table")
    LibTable(
        kind="fp",
        version=7,
        rows=[LibTableRow(name="Parts", type="KiCad", uri="${PARTS_PATH}/Parts.pretty")],
    ).to_file(assets / "fp-lib-table")
    kwargs = dict(
        settings_dir=settings,
        kicad_root=tmp_path / "install",
        env={},
        path_variables={"PARTS_PATH": str(assets)},
        strict=True,
        trust_table_chain=False,
    )
    result = resolve_lib_table("fp", **kwargs)
    assert result.get("Parts") is not None
    assert result.get("Parts").path == library
    with pytest.raises(LibTableResolutionError):
        resolve_lib_table("fp", **kwargs, trust_path_variables=False)


@pytest.mark.parametrize("value", [[], [("PARTS_PATH", "value")], {1: "value"}, {"X": 1}])
def test_invalid_mapping_types_are_rejected(value):
    with pytest.raises(TypeError):
        LibTableEnvironment.discover(env={}, path_variables=value)


@pytest.mark.parametrize("value", [{"": "value"}, {"bad name": "value"}, {"X": "a\x00b"}])
def test_invalid_names_and_nul_values_are_rejected(value):
    with pytest.raises(ValueError):
        LibTableEnvironment.discover(env={}, path_variables=value)


def test_explicit_remote_paths_do_not_become_roots():
    environment = LibTableEnvironment.discover(
        env={},
        path_variables={
            "REMOTE": "https://example.invalid/",
            "SHARE": "//server/share",
            "RELATIVE": "../other",
        },
    )
    assert environment.directory_roots() == []


def test_omitting_explicit_paths_keeps_process_filter():
    environment = LibTableEnvironment.discover(env={"PARTS_PATH": "not imported"})
    assert environment.expand("${PARTS_PATH}") == "${PARTS_PATH}"


def test_explicit_global_root_does_not_widen_project_table(tmp_path):
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    LibTable(
        kind="fp",
        version=7,
        rows=[LibTableRow(name="Escape", type="Table", uri="${PARTS_PATH}/fp-lib-table")],
    ).to_file(project / "fp-lib-table")
    LibTable(kind="fp", version=7, rows=[]).to_file(external / "fp-lib-table")
    with pytest.raises(LibTableResolutionError):
        resolve_lib_table(
            "fp",
            project_dir=project,
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "install",
            env={},
            strict=True,
            path_variables={"PARTS_PATH": str(external)},
        )
