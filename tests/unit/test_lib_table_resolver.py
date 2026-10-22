"""Unit tests for library-table discovery, merge, and lookup (R3, T4).

Authority: ``context/audit/lib-table-discovery-2026-07-28.md`` (the A2 report).
Every test builds its tables in a temporary directory and passes an explicit
environment mapping, so no test reads the machine settings directory.

Confinement note. The resolver confines the global tier to the KiCad install
root and the settings directory (ADR-047 amendment 2). A test whose libraries or
nested tables sit elsewhere under ``tmp_path`` therefore passes
``allowed_roots=[tmp_path]``, which is the widening the API offers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.exceptions import LibTableResolutionError
from eedesigncore.tables import resolver as resolver_module
from eedesigncore.tables.models import LibTable, LibTableKind, LibTableRow
from eedesigncore.tables.resolver import (
    PLACEHOLDER_NICKNAME,
    LibTableResolver,
    ResolvedLibrary,
    ResolvedLibTable,
    is_remote_uri,
    is_unc_path,
    path_within_roots,
    qualify_item_name,
    resolve_lib_table,
    symbol_library_files,
)

FILE_NAMES: dict[LibTableKind, str] = {"fp": "fp-lib-table", "sym": "sym-lib-table"}


def _row(
    name: str,
    uri: str,
    *,
    row_type: str = "KiCad",
    disabled: bool = False,
    hidden: bool = False,
) -> LibTableRow:
    return LibTableRow(name=name, type=row_type, uri=uri, disabled=disabled, hidden=hidden)


def _write_table(
    directory: Path,
    rows: list[LibTableRow],
    *,
    kind: LibTableKind = "fp",
    file_name: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (file_name or FILE_NAMES[kind])
    LibTable(kind=kind, version=7, rows=rows).to_file(path, line_ending="lf")
    return path


def _resolver(
    *,
    settings_dir: Path | None,
    project_dir: Path | None = None,
    kicad_root: Path | None = None,
    strict: bool = False,
    allowed_roots: list[Path] | None = None,
    env: dict[str, str] | None = None,
    trust_path_variables: bool = True,
    trust_table_chain: bool = True,
) -> LibTableResolver:
    return LibTableResolver.discover(
        settings_dir=settings_dir,
        project_dir=project_dir,
        kicad_root=kicad_root,
        env={} if env is None else env,
        strict=strict,
        allowed_roots=allowed_roots,
        trust_path_variables=trust_path_variables,
        trust_table_chain=trust_table_chain,
    )


def _pretty(directory: Path, name: str, *footprints: str) -> Path:
    library = directory / f"{name}.pretty"
    library.mkdir(parents=True, exist_ok=True)
    for footprint in footprints:
        (library / f"{footprint}.kicad_mod").write_text("(footprint)", encoding="utf-8")
    return library


def _symbol_lib(directory: Path, name: str, *symbols: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.kicad_sym"
    path.write_text(_symbol_lib_text(*symbols), encoding="utf-8")
    return path


def _symbol_lib_text(*symbols: str) -> str:
    body = "".join(f'\t(symbol "{symbol}"\n\t)\n' for symbol in symbols)
    return f"(kicad_symbol_lib\n{body})\n"


def _symbol_folder_lib(directory: Path, name: str, *symbols: str) -> Path:
    """Build the folder shape: one ``<symbol>.kicad_sym`` per definition."""
    folder = directory / name
    folder.mkdir(parents=True, exist_ok=True)
    for symbol in symbols:
        (folder / f"{symbol}.kicad_sym").write_text(_symbol_lib_text(symbol), encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
# The two tiers and the merge (library_manager.cpp:647-648, :687)
# ---------------------------------------------------------------------------


class TestTiers:
    def test_global_only(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Global", "C:/g.pretty")])

        table = _resolver(settings_dir=settings).resolve("fp")

        assert table.nicknames == ["Global"]
        assert table.libraries[0].scope == "global"

    def test_project_only(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        project = tmp_path / "proj"
        _write_table(project, [_row("Proj", "C:/p.pretty")])

        table = _resolver(settings_dir=settings, project_dir=project).resolve("fp")

        assert table.nicknames == ["Proj"]
        assert table.libraries[0].scope == "project"

    def test_both_tiers_union_without_a_collision(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        project = tmp_path / "proj"
        _write_table(settings, [_row("Global", "C:/g.pretty")])
        _write_table(project, [_row("Proj", "C:/p.pretty")])

        table = _resolver(settings_dir=settings, project_dir=project).resolve("fp")

        assert table.nicknames == ["Global", "Proj"]

    def test_the_project_tier_wins_a_collision(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        project = tmp_path / "proj"
        _write_table(settings, [_row("Shared", "C:/global.pretty")])
        _write_table(project, [_row("Shared", "C:/project.pretty")])

        table = _resolver(settings_dir=settings, project_dir=project).resolve("fp")

        winner = table.get("Shared")
        assert winner is not None
        assert winner.uri == "C:/project.pretty"
        assert winner.scope == "project"

    def test_the_winning_row_keeps_the_losing_display_position(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        project = tmp_path / "proj"
        _write_table(settings, [_row("Shared", "C:/g.pretty"), _row("Other", "C:/o.pretty")])
        _write_table(project, [_row("Shared", "C:/p.pretty")])

        table = _resolver(settings_dir=settings, project_dir=project).resolve("fp")

        assert table.nicknames == ["Shared", "Other"]

    def test_neither_tier_present_yields_an_empty_table(self, tmp_path: Path) -> None:
        table = _resolver(
            settings_dir=tmp_path / "settings", project_dir=tmp_path / "proj"
        ).resolve("fp")

        assert len(table) == 0
        assert table.nicknames == []

    def test_the_last_duplicate_inside_one_table_wins(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Dup", "C:/first.pretty"), _row("Dup", "C:/second.pretty")])

        table = _resolver(settings_dir=settings).resolve("fp")

        winner = table.get("Dup")
        assert winner is not None
        assert winner.uri == "C:/second.pretty"
        assert len(table) == 1

    def test_the_two_kinds_read_different_files(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Feet", "C:/f.pretty")], kind="fp")
        _write_table(settings, [_row("Sym", "C:/s.kicad_sym")], kind="sym")

        resolver = _resolver(settings_dir=settings)

        assert resolver.resolve_footprints().nicknames == ["Feet"]
        assert resolver.resolve_symbols().nicknames == ["Sym"]

    def test_a_wrong_kind_root_token_is_skipped(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings, [_row("Sym", "C:/s.kicad_sym")], kind="sym", file_name="fp-lib-table"
        )

        assert len(_resolver(settings_dir=settings).resolve("fp")) == 0

    def test_a_wrong_kind_root_token_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings, [_row("Sym", "C:/s.kicad_sym")], kind="sym", file_name="fp-lib-table"
        )

        with pytest.raises(LibTableResolutionError, match="expected fp"):
            _resolver(settings_dir=settings, strict=True).resolve("fp")


# ---------------------------------------------------------------------------
# Nested tables (library_manager.cpp:118-166, :671-681)
# ---------------------------------------------------------------------------


class TestNestedTables:
    def test_a_nested_table_splices_its_leaf_rows_into_the_namespace(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        child_dir = tmp_path / "child"
        child = _write_table(child_dir, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", str(child), row_type="Table")])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert table.nicknames == ["Leaf"]
        assert "Chain" not in table

    def test_a_relative_nested_uri_resolves_against_the_parent_directory(
        self, tmp_path: Path
    ) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", "child-table", row_type="Table")])

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Leaf"]

    def test_a_two_level_chain_flattens(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Deep", "C:/deep.pretty")], file_name="level-two")
        _write_table(
            settings, [_row("Down", "level-two", row_type="Table")], file_name="level-one"
        )
        _write_table(settings, [_row("Chain", "level-one", row_type="Table")])

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Deep"]

    def test_a_cycle_is_broken_and_the_leaf_rows_survive(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Leaf", "C:/leaf.pretty"), _row("Back", "fp-lib-table", row_type="Table")],
            file_name="child-table",
        )
        _write_table(settings, [_row("Chain", "child-table", row_type="Table")])

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Leaf"]

    def test_a_self_referencing_table_is_broken(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Self", "fp-lib-table", row_type="Table"), _row("Leaf", "C:/leaf.pretty")],
        )

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Leaf"]

    def test_a_cycle_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Self", "fp-lib-table", row_type="Table")])

        with pytest.raises(LibTableResolutionError, match="cycle"):
            _resolver(settings_dir=settings, strict=True).resolve("fp")

    def test_a_disabled_nested_row_contributes_nothing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", "child-table", row_type="Table", disabled=True)])

        assert len(_resolver(settings_dir=settings).resolve("fp")) == 0

    def test_a_hidden_nested_row_hides_every_descendant(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", "child-table", row_type="Table", hidden=True)])

        leaf = _resolver(settings_dir=settings).resolve("fp").get("Leaf")

        assert leaf is not None
        assert leaf.hidden is True

    def test_a_missing_child_table_is_skipped(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Chain", "absent-table", row_type="Table"), _row("Leaf", "C:/leaf.pretty")],
        )

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Leaf"]

    def test_the_depth_cap_raises(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Deep", "C:/deep.pretty")], file_name="level-two")
        _write_table(
            settings, [_row("Down", "level-two", row_type="Table")], file_name="level-one"
        )
        _write_table(settings, [_row("Chain", "level-one", row_type="Table")])

        resolver = _resolver(settings_dir=settings)
        resolver.max_depth = 1

        with pytest.raises(LibTableResolutionError, match="depth cap"):
            resolver.resolve("fp")

    def test_the_table_file_budget_raises(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", "child-table", row_type="Table")])

        resolver = _resolver(settings_dir=settings)
        resolver.max_tables = 1

        with pytest.raises(LibTableResolutionError, match="budget"):
            resolver.resolve("fp")


# ---------------------------------------------------------------------------
# Disabled and hidden leaf rows (library_manager.cpp:677-678 vs :684-687)
# ---------------------------------------------------------------------------


class TestDisabledRows:
    def test_a_disabled_project_row_still_masks_a_global_row(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        project = tmp_path / "proj"
        _write_table(settings, [_row("Shared", "C:/g.pretty")])
        _write_table(project, [_row("Shared", "C:/p.pretty", disabled=True)])

        winner = _resolver(settings_dir=settings, project_dir=project).resolve("fp").get("Shared")

        assert winner is not None
        assert winner.disabled is True
        assert winner.uri == "C:/p.pretty"


# ---------------------------------------------------------------------------
# URI expansion inside discovery
# ---------------------------------------------------------------------------


class TestUriExpansion:
    def test_the_compiled_in_symbol_directory_default_expands(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        root = tmp_path / "KiCad" / "10.0"
        _write_table(
            settings,
            [_row("Device", "${KICAD10_SYMBOL_DIR}/Device.kicad_sym")],
            kind="sym",
        )

        table = _resolver(settings_dir=settings, kicad_root=root).resolve("sym")

        device = table.get("Device")
        assert device is not None
        symbols = (root / "share" / "kicad" / "symbols").as_posix()
        assert device.uri == f"{symbols}/Device.kicad_sym"
        assert device.raw_uri == "${KICAD10_SYMBOL_DIR}/Device.kicad_sym"

    def test_a_kicad_common_json_user_variable_expands(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "kicad_common.json").write_text(
            '{"environment": {"vars": {"MY_LIBRARY_ROOT": "C:/lib/provisional"}}}',
            encoding="utf-8",
        )
        _write_table(settings, [_row("Caps", "${MY_LIBRARY_ROOT}/Capacitors.pretty")])

        caps = _resolver(settings_dir=settings).resolve("fp").get("Caps")

        assert caps is not None
        assert caps.uri == "C:/lib/provisional/Capacitors.pretty"

    def test_the_project_variable_expands_to_the_project_directory(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        project = tmp_path / "proj"
        _write_table(project, [_row("Coils", "${KIPRJMOD}/pcb_coils/")])

        coils = _resolver(settings_dir=settings, project_dir=project).resolve("fp").get("Coils")

        assert coils is not None
        assert coils.uri == f"{project.as_posix()}/pcb_coils/"

    def test_a_user_variable_expands_a_nested_table_uri(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        store = tmp_path / "store"
        (settings / "kicad_common.json").write_text(
            f'{{"environment": {{"vars": {{"STORE": "{store.as_posix()}"}}}}}}',
            encoding="utf-8",
        )
        _write_table(store, [_row("Leaf", "C:/leaf.pretty")], file_name="store-table")
        _write_table(settings, [_row("Chain", "${STORE}/store-table", row_type="Table")])

        resolver = _resolver(settings_dir=settings, allowed_roots=[store])

        assert resolver.resolve("fp").nicknames == ["Leaf"]

    def test_an_unresolved_variable_leaves_the_uri_verbatim(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Ghost", "${NO_SUCH_VAR}/x.pretty")])

        ghost = _resolver(settings_dir=settings).resolve("fp").get("Ghost")

        assert ghost is not None
        assert ghost.uri == "${NO_SUCH_VAR}/x.pretty"


# ---------------------------------------------------------------------------
# Failure policy and the security guards
# ---------------------------------------------------------------------------


class TestFailurePolicy:
    def test_a_table_that_is_not_utf_8_is_skipped(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "fp-lib-table").write_bytes(b"\xff\xfe(fp_lib_table)")

        assert len(_resolver(settings_dir=settings).resolve("fp")) == 0

    def test_a_table_that_is_not_utf_8_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "fp-lib-table").write_bytes(b"\xff\xfe(fp_lib_table)")

        with pytest.raises(LibTableResolutionError, match="Cannot read"):
            _resolver(settings_dir=settings, strict=True).resolve("fp")

    def test_a_symbol_library_file_that_is_absent_matches_nothing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Gone", "C:/absent/Device.kicad_sym")], kind="sym")

        assert _resolver(settings_dir=settings).resolve("sym").nickname_for("R") is None

    def test_a_malformed_table_is_skipped(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "fp-lib-table").write_text("(fp_lib_table (bogus 1))", encoding="utf-8")

        assert len(_resolver(settings_dir=settings).resolve("fp")) == 0

    def test_a_malformed_table_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        (settings / "fp-lib-table").write_text("(fp_lib_table (bogus 1))", encoding="utf-8")

        with pytest.raises(LibTableResolutionError, match="Malformed"):
            _resolver(settings_dir=settings, strict=True).resolve("fp")

    def test_a_missing_tier_raises_under_strict(self, tmp_path: Path) -> None:
        with pytest.raises(LibTableResolutionError, match="No library table"):
            _resolver(settings_dir=tmp_path / "absent", strict=True).resolve("fp")

    def test_a_global_nested_table_outside_the_roots_is_followed(self, tmp_path: Path) -> None:
        """ADR-047 amendment 4: a row of the user's own global table is trusted."""
        settings = tmp_path / "settings"
        outside = tmp_path / "outside"
        _write_table(outside, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", str(outside / "child-table"), row_type="Table")])

        table = _resolver(settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0").resolve(
            "fp"
        )

        assert table.nicknames == ["Leaf"]
        assert table.skipped == []

    def test_the_chain_trust_can_be_turned_off_and_the_loss_is_recorded(
        self, tmp_path: Path
    ) -> None:
        settings = tmp_path / "settings"
        outside = tmp_path / "outside"
        _write_table(outside, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", str(outside / "child-table"), row_type="Table")])

        table = _resolver(
            settings_dir=settings,
            kicad_root=tmp_path / "KiCad" / "10.0",
            trust_table_chain=False,
        ).resolve("fp")

        assert len(table) == 0
        assert len(table.skipped) == 1
        assert "outside the allowed roots" in table.skipped[0].reason

    def test_a_table_outside_the_confinement_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        outside = tmp_path / "outside"
        _write_table(outside, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", str(outside / "child-table"), row_type="Table")])

        with pytest.raises(LibTableResolutionError):
            _resolver(
                settings_dir=settings,
                kicad_root=tmp_path / "KiCad" / "10.0",
                strict=True,
                trust_table_chain=False,
            ).resolve("fp")

    def test_an_allowed_root_permits_the_nested_table(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        outside = tmp_path / "outside"
        child = _write_table(outside, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", str(child), row_type="Table")])

        resolver = _resolver(settings_dir=settings, allowed_roots=[outside])

        assert resolver.resolve("fp").nicknames == ["Leaf"]


# ---------------------------------------------------------------------------
# Confinement (ADR-047 amendment 2, security review MEDIUM-1 and MEDIUM-2)
# ---------------------------------------------------------------------------


class TestDefaultConfinement:
    def test_the_global_tier_defaults_to_the_install_root_and_the_settings_dir(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "KiCad" / "10.0"
        resolver = _resolver(settings_dir=tmp_path / "settings", kicad_root=root)

        assert resolver.default_roots("global") == [root, tmp_path / "settings"]

    def test_the_project_tier_defaults_to_the_project_directory(self, tmp_path: Path) -> None:
        # kicad_root names a directory that does not exist, so the built-in
        # path variables contribute no root and the tier default stands alone.
        resolver = _resolver(
            settings_dir=tmp_path / "settings",
            project_dir=tmp_path / "proj",
            kicad_root=tmp_path / "KiCad" / "10.0",
        )

        assert resolver.default_roots("project") == [tmp_path / "proj"]

    def test_allowed_roots_widen_and_never_narrow(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        root = tmp_path / "KiCad" / "10.0"
        resolver = _resolver(
            settings_dir=settings, kicad_root=root, allowed_roots=[tmp_path / "extra"]
        )

        assert resolver.roots("global") == [root, settings, tmp_path / "extra"]

    def test_a_table_inside_the_settings_directory_needs_no_widening(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Leaf", "C:/leaf.pretty")], file_name="child-table")
        _write_table(settings, [_row("Chain", "child-table", row_type="Table")])

        assert _resolver(settings_dir=settings).resolve("fp").nicknames == ["Leaf"]


class TestFolderFormSymbolLibrary:
    """A symbol library may be a directory of one-part files (ADR-099)."""

    def test_a_folder_library_is_searched(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        folder = _symbol_folder_lib(tmp_path / "syms", "company_Resistors", "R__1787", "THERM")
        _write_table(settings, [_row("company_Resistors", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("R__1787") == "company_Resistors"
        assert table.nickname_for("THERM") == "company_Resistors"
        assert table.nickname_for("NOT_THERE") is None

    def test_a_definition_whose_file_name_differs_is_still_found(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        folder = tmp_path / "syms" / "Odd"
        folder.mkdir(parents=True)
        (folder / "file-name-unrelated.kicad_sym").write_text(
            _symbol_lib_text("REAL_NAME"), encoding="utf-8"
        )
        _write_table(settings, [_row("Odd", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("REAL_NAME") == "Odd"

    def test_a_file_that_is_not_a_kicad_sym_is_ignored(self, tmp_path: Path) -> None:
        folder = _symbol_folder_lib(tmp_path / "syms", "Mixed", "GOOD")
        (folder / "notes.txt").write_text('(symbol "SNEAKY"', encoding="utf-8")
        (folder / "old.kicad_sym.bak").write_text('(symbol "STALE"', encoding="utf-8")
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Mixed", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("GOOD") == "Mixed"
        assert table.nickname_for("SNEAKY") is None
        assert table.nickname_for("STALE") is None

    def test_a_subdirectory_is_not_walked(self, tmp_path: Path) -> None:
        folder = _symbol_folder_lib(tmp_path / "syms", "Flat", "TOP")
        _symbol_folder_lib(folder, "nested", "DEEP")
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Flat", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("TOP") == "Flat"
        assert table.nickname_for("DEEP") is None

    def test_the_file_list_is_sorted_and_both_shapes_resolve(self, tmp_path: Path) -> None:
        folder = _symbol_folder_lib(tmp_path / "syms", "Sorted", "B", "A", "C")
        single = _symbol_lib(tmp_path / "syms", "Single", "X")

        assert [path.name for path in symbol_library_files(folder)] == [
            "A.kicad_sym",
            "B.kicad_sym",
            "C.kicad_sym",
        ]
        assert symbol_library_files(single) == [single]
        assert symbol_library_files(tmp_path / "absent") == []

    def test_an_empty_folder_holds_nothing(self, tmp_path: Path) -> None:
        folder = tmp_path / "syms" / "Empty"
        folder.mkdir(parents=True)
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Empty", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("ANYTHING") is None

    def test_a_folder_outside_the_roots_is_never_read(self, tmp_path: Path) -> None:
        folder = _symbol_folder_lib(tmp_path / "outside", "Shut", "SECRET")
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Shut", str(folder))], kind="sym")

        table = _resolver(settings_dir=settings, kicad_root=tmp_path / "KiCad" / "10.0").resolve(
            "sym"
        )

        assert table.nickname_for("SECRET") is None


class TestPathVariableRoots:
    """ADR-047 amendment 3: a KiCad path variable is a read root by default."""

    def test_a_variable_naming_an_existing_directory_becomes_a_global_root(
        self, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        resolver = _resolver(
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={"KICAD_SHARED_LIB_DIR": str(shared)},
        )

        assert shared in resolver.default_roots("global")
        # The PROJECT tier is NOT widened. A project is often a repository the
        # caller did not write, and the iteration-20 security review found that
        # widening it let the project name its own read root. ADR-047
        # amendment 5.
        assert shared not in resolver.default_roots("project")

    def test_the_flag_turns_the_widening_off(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        resolver = _resolver(
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={"KICAD_SHARED_LIB_DIR": str(shared)},
            trust_path_variables=False,
        )

        assert resolver.default_roots("global") == [
            tmp_path / "KiCad" / "10.0",
            tmp_path / "settings",
        ]

    def test_a_library_under_a_variable_directory_is_searchable(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        _pretty(shared, "Outside", "PART")
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Outside", "${KICAD_SHARED_LIB_DIR}/Outside.pretty")],
        )

        table = _resolver(
            settings_dir=settings,
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={"KICAD_SHARED_LIB_DIR": str(shared)},
        ).resolve("fp")

        assert table.nickname_for("PART") == "Outside"

    def test_a_variable_that_does_not_name_a_directory_contributes_nothing(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "gone"
        a_file = tmp_path / "partsdb.kicad_dbl"
        a_file.write_text("{}", encoding="utf-8")
        resolver = _resolver(
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={
                "KICAD_MISSING_DIR": str(missing),
                "KICAD_A_FILE": str(a_file),
                "KICAD_UNEXPANDED": "${SOMETHING}/lib",
                "KICAD_REMOTE": "https://example.invalid/lib",
            },
        )

        assert resolver.default_roots("global") == [
            tmp_path / "KiCad" / "10.0",
            tmp_path / "settings",
        ]

    def test_a_unc_variable_is_still_refused(self, tmp_path: Path) -> None:
        resolver = _resolver(
            settings_dir=tmp_path / "settings",
            kicad_root=tmp_path / "KiCad" / "10.0",
            env={"KICAD_UNC_DIR": r"\server\share\libs"},
        )

        assert resolver.default_roots("global") == [
            tmp_path / "KiCad" / "10.0",
            tmp_path / "settings",
        ]


class TestUncRejection:
    """MEDIUM-1: a UNC URI must not reach the network, not even through resolution."""

    def test_a_unc_nested_table_uri_is_skipped(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Remote", "//attacker.example/share/fp-lib-table", row_type="Table")],
        )

        assert len(_resolver(settings_dir=settings).resolve("fp")) == 0

    def test_a_unc_nested_table_uri_raises_under_strict(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Remote", "\\\\attacker.example\\share\\fp-lib-table", row_type="Table")],
        )

        with pytest.raises(LibTableResolutionError, match="outside the allowed roots"):
            _resolver(settings_dir=settings, strict=True).resolve("fp")

    def test_a_unc_uri_never_reaches_path_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = tmp_path / "settings"
        _write_table(
            settings,
            [_row("Remote", "//attacker.example/share/fp-lib-table", row_type="Table")],
        )
        seen: list[str] = []
        real = resolver_module._resolve_path

        def spy(path: Path) -> Path:
            seen.append(str(path))
            return real(path)

        monkeypatch.setattr(resolver_module, "_resolve_path", spy)

        _resolver(settings_dir=settings).resolve("fp")

        assert seen
        assert not any(is_unc_path(candidate) for candidate in seen)

    def test_a_unc_root_covers_a_unc_path(self) -> None:
        assert path_within_roots(Path("//host/share/libs/fp-lib-table"), [Path("//host/share")])

    def test_a_unc_path_ignores_case_like_smb(self) -> None:
        assert path_within_roots(Path("//HOST/Share/x"), [Path("//host/share")])

    def test_another_host_is_not_covered(self) -> None:
        assert not path_within_roots(Path("//other/share/x"), [Path("//host/share")])

    def test_a_local_root_never_covers_a_unc_path(self, tmp_path: Path) -> None:
        assert not path_within_roots(Path("//host/share/x"), [tmp_path])

    def test_a_url_scheme_is_never_covered(self, tmp_path: Path) -> None:
        assert not path_within_roots(Path("http://attacker.example/t"), [tmp_path])
        assert is_remote_uri("http://attacker.example/t")
        assert not is_remote_uri("C:/Program Files/KiCad/10.0")


class TestLibraryScanConfinement:
    """MEDIUM-2: the confinement covers the files an item search opens."""

    def test_a_footprint_library_outside_the_roots_matches_nothing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path / "outside", "Caps", "PART")
        _write_table(settings, [_row("Capacitor_SMD", str(library))])

        table = _resolver(settings_dir=settings).resolve("fp")

        assert table.nickname_for("PART") is None
        assert table.get("Capacitor_SMD") is not None

    def test_a_symbol_library_outside_the_roots_is_never_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = tmp_path / "settings"
        library = _symbol_lib(tmp_path / "outside", "Device", "R")
        _write_table(settings, [_row("Device", str(library))], kind="sym")
        read: list[str] = []
        real = resolver_module.read_text_capped

        def spy(path: Path, max_bytes: int) -> str:
            read.append(str(path))
            return real(path, max_bytes)

        monkeypatch.setattr(resolver_module, "read_text_capped", spy)

        table = _resolver(settings_dir=settings).resolve("sym")

        assert table.nickname_for("R") is None
        assert str(library) not in read

    def test_a_widened_root_restores_the_match(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path / "outside", "Caps", "PART")
        _write_table(settings, [_row("Capacitor_SMD", str(library))])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path / "outside"]).resolve(
            "fp"
        )

        assert table.nickname_for("PART") == "Capacitor_SMD"

    def test_the_project_tier_confines_to_the_project_directory(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        settings.mkdir()
        project = tmp_path / "proj"
        inside = _pretty(project, "Coils", "COIL")
        outside = _pretty(tmp_path / "elsewhere", "Other", "PART")
        _write_table(project, [_row("Coils", str(inside)), _row("Other", str(outside))])

        table = _resolver(settings_dir=settings, project_dir=project).resolve("fp")

        assert table.nickname_for("COIL") == "Coils"
        assert table.nickname_for("PART") is None

    def test_a_hand_built_table_stays_unconfined(self) -> None:
        """A table the caller builds carries no roots, so nothing is filtered."""
        library = ResolvedLibrary(
            nickname="Caps",
            type="KiCad",
            uri="C:/anywhere/Caps.pretty",
            raw_uri="C:/anywhere/Caps.pretty",
            scope="global",
            source_table=Path("C:/anywhere/fp-lib-table"),
        )
        table = ResolvedLibTable(kind="fp", libraries=[library])

        assert table.scan_allowed(library)

    def test_a_resolved_table_carries_the_roots_it_used(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        root = tmp_path / "KiCad" / "10.0"
        _write_table(settings, [_row("A", "C:/a.pretty")])

        table = _resolver(
            settings_dir=settings, kicad_root=root, project_dir=tmp_path / "proj"
        ).resolve("fp")

        assert table.global_roots == [root, settings]
        assert table.project_roots == [tmp_path / "proj"]


# ---------------------------------------------------------------------------
# Item lookup (library policy, ADR-047)
# ---------------------------------------------------------------------------


class TestItemLookup:
    def test_a_footprint_library_holding_the_item_wins(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path, "Caps", "C_0402_1005Metric")
        _write_table(settings, [_row("Capacitor_SMD", str(library))])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert table.nickname_for("C_0402_1005Metric") == "Capacitor_SMD"

    def test_an_absent_footprint_matches_nothing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path, "Caps", "C_0402_1005Metric")
        _write_table(settings, [_row("Capacitor_SMD", str(library))])

        assert (
            _resolver(settings_dir=settings, allowed_roots=[tmp_path])
            .resolve("fp")
            .nickname_for("NoSuchPart")
            is None
        )

    def test_a_symbol_library_defining_the_item_wins(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _symbol_lib(tmp_path / "syms", "Device", "R", "C")
        _write_table(settings, [_row("Device", str(library))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("R") == "Device"
        assert table.nickname_for("L") is None

    def test_a_unit_name_does_not_count_as_a_definition(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _symbol_lib(tmp_path / "syms", "Device", "R", "R_0_1")
        _write_table(settings, [_row("Device", str(library))], kind="sym")

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("sym")

        assert table.nickname_for("R_1_1") is None

    def test_the_project_tier_is_searched_first(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        project = tmp_path / "proj"
        _write_table(settings, [_row("GlobalCaps", str(_pretty(tmp_path, "g", "PART")))])
        _write_table(project, [_row("ProjectCaps", str(_pretty(tmp_path, "p", "PART")))])

        table = _resolver(
            settings_dir=settings, project_dir=project, allowed_roots=[tmp_path]
        ).resolve("fp")

        assert table.nickname_for("PART") == "ProjectCaps"

    def test_a_disabled_row_is_not_searched(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path, "Caps", "PART")
        _write_table(settings, [_row("Off", str(library), disabled=True)])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert table.nickname_for("PART") is None
        assert table.nickname_for("PART", include_disabled=True) == "Off"

    def test_a_non_kicad_row_type_is_not_searched(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path, "Caps", "PART")
        _write_table(settings, [_row("Db", str(library), row_type="Database")])

        assert (
            _resolver(settings_dir=settings, allowed_roots=[tmp_path])
            .resolve("fp")
            .nickname_for("PART")
            is None
        )

    def test_a_qualified_name_is_never_searched(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Caps", str(_pretty(tmp_path, "c", "PART")))])

        assert (
            _resolver(settings_dir=settings, allowed_roots=[tmp_path])
            .resolve("fp")
            .find_library("Other:PART")
            is None
        )

    def test_a_path_separator_in_the_item_name_matches_nothing(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Caps", str(_pretty(tmp_path, "c", "PART")))])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert table.nickname_for("../PART") is None

    def test_a_repeated_lookup_uses_the_cache(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        library = _pretty(tmp_path, "Caps", "PART")
        _write_table(settings, [_row("Caps", str(library))])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert table.nickname_for("PART") == "Caps"
        (library / "PART.kicad_mod").unlink()
        assert table.nickname_for("PART") == "Caps"


# ---------------------------------------------------------------------------
# Container behaviour and the public entry points
# ---------------------------------------------------------------------------


class TestContainer:
    def test_len_iter_and_contains(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("A", "C:/a.pretty"), _row("B", "C:/b.pretty")])

        table = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

        assert len(table) == 2
        assert "A" in table
        assert "Missing" not in table
        assert [library.nickname for library in table] == ["A", "B"]

    def test_the_path_property_returns_the_expanded_uri(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("A", "C:/a.pretty")])

        library = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp").get("A")

        assert library is not None
        assert library.path == Path("C:/a.pretty")

    def test_the_source_table_records_the_file_the_row_came_from(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        path = _write_table(settings, [_row("A", "C:/a.pretty")])

        library = _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp").get("A")

        assert library is not None
        assert library.source_table == path.resolve()


class TestConvenienceFunction:
    def test_resolve_lib_table_matches_the_resolver(self, tmp_path: Path) -> None:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("A", "C:/a.pretty")])

        table = resolve_lib_table("fp", settings_dir=settings, env={})

        assert isinstance(table, ResolvedLibTable)
        assert table.nicknames == ["A"]


class TestQualifyItemName:
    def _table(self, tmp_path: Path) -> ResolvedLibTable:
        settings = tmp_path / "settings"
        _write_table(settings, [_row("Capacitor_SMD", str(_pretty(tmp_path, "c", "PART")))])
        return _resolver(settings_dir=settings, allowed_roots=[tmp_path]).resolve("fp")

    def test_a_pre_qualified_name_passes_through(self, tmp_path: Path) -> None:
        table = self._table(tmp_path)
        assert qualify_item_name("Other:PART", lib_table=table) == "Other:PART"

    def test_an_explicit_nickname_outranks_the_table(self, tmp_path: Path) -> None:
        table = self._table(tmp_path)
        assert qualify_item_name("PART", lib_nickname="Mine", lib_table=table) == "Mine:PART"

    def test_the_table_match_supplies_the_nickname(self, tmp_path: Path) -> None:
        table = self._table(tmp_path)
        assert qualify_item_name("PART", lib_table=table) == "Capacitor_SMD:PART"

    def test_no_match_falls_back_to_the_placeholder(self, tmp_path: Path) -> None:
        table = self._table(tmp_path)
        assert qualify_item_name("Ghost", lib_table=table) == f"{PLACEHOLDER_NICKNAME}:Ghost"

    def test_no_table_falls_back_to_the_placeholder(self) -> None:
        assert qualify_item_name("Ghost") == f"{PLACEHOLDER_NICKNAME}:Ghost"
