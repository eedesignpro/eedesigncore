"""Unit tests for library-table environment discovery and URI expansion (R3, T4).

Authority: ``context/audit/lib-table-discovery-2026-07-28.md`` sections 1, 7,
and 8. Every test passes an explicit environment mapping and a temporary
directory, so no test reads the machine settings directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eedesigncore.exceptions import LibTableResolutionError
from eedesigncore.tables.env import (
    LibTableEnvironment,
    default_kicad_install_root,
    default_user_settings_dir,
    is_kicad_env_var,
    kicad_builtin_vars,
    kicad_env_vars,
    load_kicad_common_vars,
    read_text_capped,
    stock_data_dir,
)


class TestKicadEnvVarFilter:
    """MEDIUM-1: only KiCad-owned process variables reach URI expansion."""

    @pytest.mark.parametrize(
        "name",
        [
            "KIPRJMOD",
            "KICAD10_SYMBOL_DIR",
            "KICAD_USER_TEMPLATE_DIR",
            "KICAD_PTEMPLATES",
            "KICAD_CONFIG_HOME",
            "KISYS3DMOD",
            "KISYSMOD",
        ],
    )
    def test_a_kicad_owned_name_passes(self, name: str) -> None:
        assert is_kicad_env_var(name)

    @pytest.mark.parametrize(
        "name",
        ["CI_JOB_TOKEN", "GITLAB_DEPLOY_PASSWORD", "PATH", "MY_LIBRARY_ROOT", "KI", ""],
    )
    def test_any_other_name_is_refused(self, name: str) -> None:
        assert not is_kicad_env_var(name)

    def test_the_filter_keeps_only_the_owned_names(self) -> None:
        source = {"KICAD10_SYMBOL_DIR": "C:/sym", "CI_JOB_TOKEN": "opaque", "KIPRJMOD": "C:/proj"}

        assert kicad_env_vars(source) == {
            "KICAD10_SYMBOL_DIR": "C:/sym",
            "KIPRJMOD": "C:/proj",
        }


# ---------------------------------------------------------------------------
# User settings directory (paths.cpp:645-668)
# ---------------------------------------------------------------------------


class TestUserSettingsDir:
    def test_appdata_branch_appends_kicad_and_version(self) -> None:
        result = default_user_settings_dir(env={"APPDATA": "C:/Users/x/AppData/Roaming"})
        assert result == Path("C:/Users/x/AppData/Roaming/kicad/10.0")

    def test_config_home_replaces_the_base_without_a_kicad_component(self) -> None:
        result = default_user_settings_dir(
            env={"KICAD_CONFIG_HOME": "D:/cfg", "APPDATA": "C:/Users/x/AppData/Roaming"}
        )
        assert result == Path("D:/cfg/10.0")

    def test_an_empty_config_home_does_not_count_as_set(self) -> None:
        result = default_user_settings_dir(
            env={"KICAD_CONFIG_HOME": "", "APPDATA": "C:/Users/x/AppData/Roaming"}
        )
        assert result == Path("C:/Users/x/AppData/Roaming/kicad/10.0")

    def test_the_version_subdirectory_is_configurable(self) -> None:
        result = default_user_settings_dir(version="9.0", env={"APPDATA": "C:/roam"})
        assert result == Path("C:/roam/kicad/9.0")

    def test_the_fallback_branch_uses_xdg_config_home(self) -> None:
        result = default_user_settings_dir(env={"XDG_CONFIG_HOME": "/home/x/.config"})
        assert result == Path("/home/x/.config/kicad/10.0")


class TestInstallRoot:
    def test_program_files_candidate_wins_when_it_exists(self, tmp_path: Path) -> None:
        root = tmp_path / "KiCad" / "10.0"
        root.mkdir(parents=True)
        result = default_kicad_install_root(env={"ProgramFiles": str(tmp_path)})
        assert result == root

    def test_no_candidate_returns_none(self, tmp_path: Path) -> None:
        result = default_kicad_install_root(version="99.9", env={"ProgramFiles": str(tmp_path)})
        assert result is None


class TestBuiltinVars:
    def test_defaults_derive_from_the_install_root(self) -> None:
        variables = kicad_builtin_vars(Path("C:/Program Files/KiCad/10.0"), env={})
        assert variables["KICAD10_SYMBOL_DIR"] == (
            "C:/Program Files/KiCad/10.0/share/kicad/symbols"
        )
        assert variables["KICAD10_FOOTPRINT_DIR"] == (
            "C:/Program Files/KiCad/10.0/share/kicad/footprints"
        )

    def test_the_design_block_directory_is_named_blocks(self) -> None:
        variables = kicad_builtin_vars(Path("C:/kicad"), env={})
        assert variables["KICAD10_DESIGN_BLOCK_DIR"].endswith("/share/kicad/blocks")

    def test_no_root_yields_no_defaults(self) -> None:
        assert kicad_builtin_vars(None, env={}) == {}

    def test_stock_data_home_overrides_the_root(self) -> None:
        result = stock_data_dir(Path("C:/kicad"), env={"KICAD_STOCK_DATA_HOME": "D:/stock"})
        assert result == Path("D:/stock")


# ---------------------------------------------------------------------------
# kicad_common.json (common_settings.cpp:151-158, pgm_base.cpp:511-535)
# ---------------------------------------------------------------------------


def _write_common(path: Path, variables: dict[str, object]) -> Path:
    target = path / "kicad_common.json"
    target.write_text(json.dumps({"environment": {"vars": variables}}), encoding="utf-8")
    return target


class TestKicadCommonVars:
    def test_user_variables_load(self, tmp_path: Path) -> None:
        target = _write_common(tmp_path, {"MY_LIBRARY_ROOT": "C:/lib/provisional"})
        assert load_kicad_common_vars(target) == {"MY_LIBRARY_ROOT": "C:/lib/provisional"}

    def test_non_string_values_are_dropped(self, tmp_path: Path) -> None:
        target = _write_common(tmp_path, {"GOOD": "yes", "BAD": 7})
        assert load_kicad_common_vars(target) == {"GOOD": "yes"}

    def test_a_missing_file_yields_no_variables(self, tmp_path: Path) -> None:
        assert load_kicad_common_vars(tmp_path / "kicad_common.json") == {}

    def test_a_missing_file_raises_under_strict(self, tmp_path: Path) -> None:
        with pytest.raises(LibTableResolutionError, match="not found"):
            load_kicad_common_vars(tmp_path / "kicad_common.json", strict=True)

    def test_malformed_json_yields_no_variables(self, tmp_path: Path) -> None:
        target = tmp_path / "kicad_common.json"
        target.write_text("{not json", encoding="utf-8")
        assert load_kicad_common_vars(target) == {}

    def test_malformed_json_raises_under_strict(self, tmp_path: Path) -> None:
        target = tmp_path / "kicad_common.json"
        target.write_text("{not json", encoding="utf-8")
        with pytest.raises(LibTableResolutionError, match="Malformed"):
            load_kicad_common_vars(target, strict=True)

    def test_a_file_without_an_environment_object_is_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "kicad_common.json"
        target.write_text(json.dumps({"other": 1}), encoding="utf-8")
        assert load_kicad_common_vars(target) == {}


class TestReadCap:
    def test_a_file_over_the_cap_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "big.txt"
        target.write_text("x" * 64, encoding="utf-8")
        with pytest.raises(LibTableResolutionError, match="read cap"):
            read_text_capped(target, 8)

    def test_a_file_under_the_cap_reads(self, tmp_path: Path) -> None:
        target = tmp_path / "small.txt"
        target.write_text("hello", encoding="utf-8")
        assert read_text_capped(target, 64) == "hello"


# ---------------------------------------------------------------------------
# Expansion (common.cpp:360-585)
# ---------------------------------------------------------------------------


@pytest.fixture
def env() -> LibTableEnvironment:
    return LibTableEnvironment(variables={"LIB": "C:/lib", "KICAD10_SYMBOL_DIR": "C:/kicad/sym"})


class TestExpansionForms:
    @pytest.mark.parametrize(
        "text",
        ["${LIB}/x.pretty", "$(LIB)/x.pretty", "%LIB%/x.pretty", "$LIB/x.pretty"],
    )
    def test_every_accepted_form_expands(self, env: LibTableEnvironment, text: str) -> None:
        assert env.expand(text) == "C:/lib/x.pretty"

    def test_an_unresolved_reference_stays_verbatim(self, env: LibTableEnvironment) -> None:
        assert env.expand("${MISSING}/x") == "${MISSING}/x"

    def test_a_backslash_suppresses_the_special_meaning(self, env: LibTableEnvironment) -> None:
        assert env.expand(r"\$LIB/x") == "$LIB/x"

    def test_an_escaped_bracket_form_survives_only_the_first_pass(
        self, env: LibTableEnvironment
    ) -> None:
        # KiCad quirk: pass one drops the backslash, and the bracket characters
        # left behind trigger a second pass that expands the reference
        # (common.cpp:561-569 with :577-582).
        assert env.expand(r"\${LIB}/x") == "C:/lib/x"

    def test_text_without_a_reference_is_unchanged(self, env: LibTableEnvironment) -> None:
        assert env.expand("C:/plain/path.kicad_sym") == "C:/plain/path.kicad_sym"

    def test_an_indirect_reference_expands_on_the_second_pass(self) -> None:
        env = LibTableEnvironment(variables={"OUTER": "${INNER}", "INNER": "D:/deep"})
        assert env.expand("${OUTER}/lib") == "D:/deep/lib"

    def test_a_self_referencing_variable_terminates(self) -> None:
        env = LibTableEnvironment(variables={"A": "${B}", "B": "${A}"})
        assert isinstance(env.expand("${A}"), str)


class TestLookupOrder:
    def test_a_project_text_variable_outranks_an_environment_variable(self) -> None:
        env = LibTableEnvironment(
            variables={"NAME": "from-env"},
            project_text_variables={"NAME": "from-project"},
        )
        assert env.expand("${NAME}") == "from-project"

    def test_an_unknown_name_looks_up_to_none(self, env: LibTableEnvironment) -> None:
        assert env.lookup("NOPE") is None

    def test_a_prior_version_symbol_dir_falls_back_to_the_current_one(
        self, env: LibTableEnvironment
    ) -> None:
        assert env.expand("${KICAD9_SYMBOL_DIR}/Device.kicad_sym") == (
            "C:/kicad/sym/Device.kicad_sym"
        )

    def test_the_legacy_3d_model_name_falls_back_to_the_versioned_one(self) -> None:
        env = LibTableEnvironment(variables={"KICAD10_3DMODEL_DIR": "C:/kicad/3d"})
        assert env.expand("${KISYS3DMOD}/x.step") == "C:/kicad/3d/x.step"

    def test_an_unversioned_name_has_no_fallback(self) -> None:
        env = LibTableEnvironment(variables={"KICAD10_SYMBOL_DIR": "C:/kicad/sym"})
        assert env.lookup("SYMBOL_DIR") is None


class TestDiscoverPrecedence:
    def test_the_process_environment_outranks_kicad_common_json(self, tmp_path: Path) -> None:
        """A KiCad-owned process variable wins, as ``pgm_base.cpp:530-531`` does."""
        _write_common(tmp_path, {"KICAD_SHARED_DIR": "from-file"})
        env = LibTableEnvironment.discover(
            settings_dir=tmp_path,
            env={"KICAD_SHARED_DIR": "from-process"},
        )
        assert env.lookup("KICAD_SHARED_DIR") == "from-process"

    def test_a_process_variable_outside_the_kicad_set_never_expands(self, tmp_path: Path) -> None:
        """MEDIUM-1: a CI secret in the environment must not reach a URI."""
        env = LibTableEnvironment.discover(
            settings_dir=tmp_path,
            env={"CI_JOB_TOKEN": "opaque", "KICAD10_SYMBOL_DIR": "C:/kicad/sym"},
        )

        assert env.lookup("CI_JOB_TOKEN") is None
        assert env.expand("//host/s/${CI_JOB_TOKEN}") == "//host/s/${CI_JOB_TOKEN}"
        assert env.lookup("KICAD10_SYMBOL_DIR") == "C:/kicad/sym"

    def test_a_kicad_common_json_name_outside_the_prefix_still_expands(
        self, tmp_path: Path
    ) -> None:
        """The user settings file is the supported way to add a name of your own."""
        _write_common(tmp_path, {"MY_LIBRARY_ROOT": "C:/lib/provisional"})
        env = LibTableEnvironment.discover(settings_dir=tmp_path, env={})

        assert env.lookup("MY_LIBRARY_ROOT") == "C:/lib/provisional"

    def test_kicad_common_json_outranks_a_compiled_in_default(self, tmp_path: Path) -> None:
        _write_common(tmp_path, {"KICAD10_SYMBOL_DIR": "D:/my/symbols"})
        env = LibTableEnvironment.discover(
            settings_dir=tmp_path,
            kicad_root=Path("C:/Program Files/KiCad/10.0"),
            env={},
        )
        assert env.lookup("KICAD10_SYMBOL_DIR") == "D:/my/symbols"

    def test_kiprjmod_is_the_project_directory_without_a_trailing_separator(
        self, tmp_path: Path
    ) -> None:
        env = LibTableEnvironment.discover(project_dir=tmp_path / "proj", env={})
        assert env.lookup("KIPRJMOD") == (tmp_path / "proj").as_posix()
        assert env.expand("${KIPRJMOD}/coils/") == f"{(tmp_path / 'proj').as_posix()}/coils/"

    def test_a_compiled_in_default_survives_unopposed(self, tmp_path: Path) -> None:
        env = LibTableEnvironment.discover(
            settings_dir=tmp_path,
            kicad_root=Path("C:/Program Files/KiCad/10.0"),
            env={},
        )
        assert env.lookup("KICAD10_FOOTPRINT_DIR") == (
            "C:/Program Files/KiCad/10.0/share/kicad/footprints"
        )
