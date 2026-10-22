"""Listing a symbol library, and walking a merged table."""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.library import (
    iter_library_records,
    iter_symbol_library,
    iter_table_records,
    library_item_names,
)
from eedesigncore.library.catalog import find_symbol, table_symbol_loader
from eedesigncore.tables.resolver import ResolvedLibrary, ResolvedLibTable

SYMBOL_TEMPLATE = (
    '(kicad_symbol_lib (version 20251024) (generator "x") (generator_version "10.0")\n'
    '\t(symbol "{name}"\n'
    '\t\t(property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
    '\t\t(property "Value" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
    '\t\t(property "Description" "{description}" (at 0 0 0)'
    " (effects (font (size 1.27 1.27)) (hide yes)))\n"
    '\t\t(property "IPN" "{ipn}" (at 0 0 0)'
    " (effects (font (size 1.27 1.27)) (hide yes)))\n"
    "\t)\n"
    ")\n"
)


def _one_part_file(directory: Path, name: str, *, description: str = "", ipn: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.kicad_sym"
    path.write_text(
        SYMBOL_TEMPLATE.format(name=name, description=description, ipn=ipn),
        encoding="utf-8",
    )
    return path


def _row(nickname: str, uri: Path, *, row_type: str = "KiCad") -> ResolvedLibrary:
    return ResolvedLibrary(
        nickname=nickname,
        type=row_type,
        uri=str(uri),
        raw_uri=str(uri),
        scope="global",
        source_table=uri.parent / "sym-lib-table",
    )


class TestIterSymbolLibrary:
    def test_a_single_file_library_yields_its_definitions(self, tmp_path: Path) -> None:
        path = tmp_path / "Device.kicad_sym"
        path.write_text(
            SYMBOL_TEMPLATE.format(name="R", description="Resistor", ipn=""),
            encoding="utf-8",
        )
        found = list(iter_symbol_library(path))
        assert [symbol.name for symbol, _file in found] == ["R"]
        assert found[0][1] == path

    def test_a_folder_library_yields_every_file_in_sorted_order(self, tmp_path: Path) -> None:
        folder = tmp_path / "company_Resistors"
        for name in ("THERMISTOR", "R__1787", "R_POT"):
            _one_part_file(folder, name)
        # Path sorting on Windows compares the case-folded name, so "r__1787"
        # precedes "r_pot": underscore is below the letters.
        assert [symbol.name for symbol, _ in iter_symbol_library(folder)] == [
            "R__1787",
            "R_POT",
            "THERMISTOR",
        ]

    def test_an_unparsable_file_is_skipped_by_default(self, tmp_path: Path) -> None:
        folder = tmp_path / "Mixed"
        _one_part_file(folder, "GOOD")
        (folder / "BROKEN.kicad_sym").write_text("(kicad_symbol_lib (nonsense", encoding="utf-8")
        assert [s.name for s, _ in iter_symbol_library(folder)] == ["GOOD"]

    def test_strict_raises_on_an_unparsable_file(self, tmp_path: Path) -> None:
        folder = tmp_path / "Mixed"
        _one_part_file(folder, "GOOD")
        (folder / "BROKEN.kicad_sym").write_text("(kicad_symbol_lib (nonsense", encoding="utf-8")
        with pytest.raises(KicadParseError):
            list(iter_symbol_library(folder, strict=True))

    def test_an_absent_path_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_symbol_library(tmp_path / "gone")) == []


class TestLibraryRecords:
    def test_a_record_carries_the_nickname_the_fields_and_the_file(self, tmp_path: Path) -> None:
        folder = tmp_path / "company_Resistors"
        path = _one_part_file(folder, "R__1787", description="resistor", ipn="PN-00457")
        records = list(iter_library_records(_row("company_Resistors", folder)))
        assert len(records) == 1
        record = records[0]
        assert record.lib_id == "company_Resistors:R__1787"
        assert record.description == "resistor"
        assert record.fields["IPN"] == "PN-00457"
        assert record.source == "library"
        assert record.source_path == str(path)

    def test_a_bare_path_takes_the_nickname_argument(self, tmp_path: Path) -> None:
        folder = tmp_path / "libs"
        _one_part_file(folder, "R")
        records = list(iter_library_records(folder, nickname="Given"))
        assert records[0].lib_id == "Given:R"

    def test_a_database_row_yields_nothing(self, tmp_path: Path) -> None:
        row = _row("partsdb", tmp_path / "partsdb.kicad_dbl", row_type="Database")
        assert list(iter_library_records(row)) == []

    def test_the_item_names_drop_duplicates(self, tmp_path: Path) -> None:
        folder = tmp_path / "Dup"
        _one_part_file(folder, "A")
        (folder / "second.kicad_sym").write_text(
            SYMBOL_TEMPLATE.format(name="A", description="later", ipn=""), encoding="utf-8"
        )
        assert library_item_names(folder) == ["A"]


class TestIterTableRecords:
    def _table(self, *libraries: ResolvedLibrary, roots: list[Path] | None = None):
        return ResolvedLibTable(
            kind="sym", libraries=list(libraries), global_roots=roots, project_roots=roots
        )

    def test_every_readable_library_contributes(self, tmp_path: Path) -> None:
        one = tmp_path / "One"
        two = tmp_path / "Two"
        _one_part_file(one, "A")
        _one_part_file(two, "B")
        table = self._table(_row("One", one), _row("Two", two))
        assert sorted(r.lib_id for r in iter_table_records(table)) == ["One:A", "Two:B"]

    def test_a_nickname_filter_limits_the_walk(self, tmp_path: Path) -> None:
        one = tmp_path / "One"
        two = tmp_path / "Two"
        _one_part_file(one, "A")
        _one_part_file(two, "B")
        table = self._table(_row("One", one), _row("Two", two))
        assert [r.lib_id for r in iter_table_records(table, nicknames=["Two"])] == ["Two:B"]

    def test_a_library_outside_the_roots_is_skipped(self, tmp_path: Path) -> None:
        inside = tmp_path / "inside" / "One"
        outside = tmp_path / "outside" / "Two"
        _one_part_file(inside, "A")
        _one_part_file(outside, "B")
        table = self._table(_row("One", inside), _row("Two", outside), roots=[tmp_path / "inside"])
        assert [r.lib_id for r in iter_table_records(table)] == ["One:A"]

    def test_a_database_row_is_skipped_and_raises_nothing(self, tmp_path: Path) -> None:
        one = tmp_path / "One"
        _one_part_file(one, "A")
        table = self._table(
            _row("partsdb", tmp_path / "partsdb.kicad_dbl", row_type="Database"), _row("One", one)
        )
        assert [r.lib_id for r in iter_table_records(table)] == ["One:A"]

    def test_an_absent_library_raises_nothing(self, tmp_path: Path) -> None:
        table = self._table(_row("Gone", tmp_path / "gone"))
        assert list(iter_table_records(table)) == []

    def test_a_footprint_table_is_refused(self, tmp_path: Path) -> None:
        table = ResolvedLibTable(kind="fp")
        with pytest.raises(ValueError, match="'sym' table"):
            list(iter_table_records(table))

    def test_the_project_tier_comes_first(self, tmp_path: Path) -> None:
        globals_dir = tmp_path / "g"
        project_dir = tmp_path / "p"
        _one_part_file(globals_dir, "A")
        _one_part_file(project_dir, "B")
        global_row = _row("G", globals_dir)
        project_row = _row("P", project_dir).model_copy(update={"scope": "project"})
        table = self._table(global_row, project_row)
        assert [r.lib_id for r in iter_table_records(table)] == ["P:B", "G:A"]


class TestFindSymbol:
    def test_a_folder_library_resolves_by_lib_id(self, tmp_path: Path) -> None:
        folder = tmp_path / "company_Resistors"
        _one_part_file(folder, "R__1787")
        table = ResolvedLibTable(kind="sym", libraries=[_row("company_Resistors", folder)])
        found = find_symbol(table, "company_Resistors:R__1787")
        assert found is not None
        assert found.name == "R__1787"

    def test_a_missing_nickname_or_name_returns_none(self, tmp_path: Path) -> None:
        table = ResolvedLibTable(kind="sym", libraries=[])
        assert find_symbol(table, "nope:thing") is None
        assert find_symbol(table, "bare-name") is None

    def test_the_last_file_wins_a_duplicate_name(self, tmp_path: Path) -> None:
        folder = tmp_path / "Dup"
        _one_part_file(folder, "A", description="first")
        (folder / "zz.kicad_sym").write_text(
            SYMBOL_TEMPLATE.format(name="A", description="later", ipn=""), encoding="utf-8"
        )
        table = ResolvedLibTable(kind="sym", libraries=[_row("Dup", folder)])
        found = find_symbol(table, "Dup:A")
        assert found is not None
        assert next(f.value for f in found.fields if f.name == "Description") == "later"

    def test_the_loader_is_bound_to_the_table(self, tmp_path: Path) -> None:
        folder = tmp_path / "L"
        _one_part_file(folder, "A")
        table = ResolvedLibTable(kind="sym", libraries=[_row("L", folder)])
        load = table_symbol_loader(table)
        assert load("L:A") is not None
        assert load("L:B") is None
