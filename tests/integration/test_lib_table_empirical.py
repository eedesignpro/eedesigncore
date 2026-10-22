"""Empirical nickname resolution against the real KiCad install (R3, R4, M2).

These tests read the operator machine: the KiCad 10 user settings directory and
one real in-house project. Each one skips when its input is absent, so a CI
runner without KiCad still passes the suite.

Machine inputs, both recorded in the A2 report
``context/audit/lib-table-discovery-2026-07-28.md`` section 8:

- ``%APPDATA%/kicad/10.0/`` holds a global ``fp-lib-table`` that MIXES both row
  types: three ``type "Table"`` pointer rows, which a resolver must follow, and
  one ``type "KiCad"`` direct row, the ``EPC`` library. The operator confirmed on
  2026-08-13 that the direct row is intentional, so the resolver has to handle
  both kinds in one table (ADR-070).
- A private corpus project, named in ``tests/private_inputs.py``, holds a
  project ``fp-lib-table`` with the nickname ``PCB Coils`` and a ``${KIPRJMOD}``
  URI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.sch_family.symbol import Symbol
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.operations import inject_footprint
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.operations import inject_symbol
from eedesigncore.tables.env import default_user_settings_dir
from eedesigncore.tables.models import LibTable
from eedesigncore.tables.resolver import ResolvedLibTable, resolve_lib_table
from tests.private_inputs import private_path

SETTINGS_DIR = default_user_settings_dir()
PROJECT_DIR = private_path("lib_table_corpus_project")
COIL_FOOTPRINT = PROJECT_DIR / "pcb_coils" / "COIL.kicad_mod"

requires_global_tables = pytest.mark.skipif(
    not (SETTINGS_DIR / "fp-lib-table").is_file()
    or not (SETTINGS_DIR / "sym-lib-table").is_file(),
    reason="no KiCad 10 global library tables on this machine",
)

requires_project = pytest.mark.skipif(
    not COIL_FOOTPRINT.is_file(),
    reason="the private coil corpus project is absent",
)


@pytest.fixture(scope="module")
def footprint_table() -> ResolvedLibTable:
    return resolve_lib_table("fp", project_dir=PROJECT_DIR)


@pytest.fixture(scope="module")
def symbol_table() -> ResolvedLibTable:
    return resolve_lib_table("sym", project_dir=PROJECT_DIR)


@requires_global_tables
class TestNestedGlobalTable:
    def test_the_global_footprint_table_holds_pointer_rows_and_direct_rows(self) -> None:
        """The real table mixes both row kinds, and that mix is intentional.

        Operator ruling 2026-08-13: the ``type "KiCad"`` row pointing at
        ``C:/KiCad/EPC-KiCad-Library/KiFootprints`` belongs there. The test that
        asserted pointer rows ONLY described an earlier machine state, not the
        behaviour the resolver has to support.
        """
        table = LibTable.from_file(SETTINGS_DIR / "fp-lib-table")

        assert table.rows
        assert {row.type for row in table.rows} <= {"Table", "KiCad"}
        assert any(row.type == "Table" for row in table.rows), "a pointer row must exist"

    def test_the_resolver_returns_libraries_from_both_row_kinds(
        self, footprint_table: ResolvedLibTable
    ) -> None:
        """A direct row resolves in place, a pointer row resolves through recursion."""
        table = LibTable.from_file(SETTINGS_DIR / "fp-lib-table")
        direct = [row.name for row in table.rows if row.type == "KiCad"]
        resolved = {library.nickname for library in footprint_table}

        for name in direct:
            assert name in resolved, f"the direct row {name!r} did not reach the resolver"
        # Recursion contributes strictly more than the direct rows alone.
        assert len(resolved) > len(direct)

    def test_recursion_turns_those_pointers_into_real_libraries(
        self, footprint_table: ResolvedLibTable
    ) -> None:
        assert len(footprint_table) > 2
        assert any(library.type == "KiCad" for library in footprint_table)

    def test_the_stock_device_symbol_library_resolves(
        self, symbol_table: ResolvedLibTable
    ) -> None:
        device = symbol_table.get("Device")
        assert device is not None, "the stock Device library is missing from the chain"
        assert device.raw_uri.startswith("${KICAD10_SYMBOL_DIR}")
        assert device.path.is_file(), "the compiled-in KICAD10_SYMBOL_DIR default did not expand"

    def test_a_bare_symbol_name_resolves_to_a_library_that_defines_it(
        self, symbol_table: ResolvedLibTable
    ) -> None:
        library = symbol_table.find_library("R")
        assert library is not None
        assert library.path.is_file()
        assert '(symbol "R"' in library.path.read_text(encoding="utf-8")


@requires_global_tables
@requires_project
class TestProjectTier:
    def test_the_project_row_wins_and_expands_kiprjmod(
        self, footprint_table: ResolvedLibTable
    ) -> None:
        coils = footprint_table.get("PCB Coils")
        assert coils is not None
        assert coils.scope == "project"
        assert coils.raw_uri.startswith("${KIPRJMOD}")
        assert Path(coils.uri) == PROJECT_DIR / "pcb_coils"


@requires_global_tables
@requires_project
class TestMilestoneM2:
    """M2: a bare inject resolves to the real nickname on a real project."""

    def test_inject_footprint_resolves_the_project_nickname(
        self, footprint_table: ResolvedLibTable
    ) -> None:
        footprint = Footprint.from_file(COIL_FOOTPRINT)
        footprint.fp_id = "COIL"

        placed = inject_footprint(Board(), footprint, at=(0.0, 0.0), lib_table=footprint_table)

        assert placed.fp_id == "PCB Coils:COIL"

    def test_inject_symbol_resolves_a_stock_nickname(self, symbol_table: ResolvedLibTable) -> None:
        placed = inject_symbol(
            Schematic(), Symbol(name="R"), at=(0.0, 0.0), lib_table=symbol_table
        )

        nickname, _, item = placed.lib_id.partition(":")
        assert item == "R"
        assert nickname != "eedesigncore"
        library = symbol_table.get(nickname)
        assert library is not None
        assert library.path.is_file()
