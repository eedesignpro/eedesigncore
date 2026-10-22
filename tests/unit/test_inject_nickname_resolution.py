"""Nickname resolution inside both injects (R4, T4, ADR-047).

Precedence under test: a pre-qualified name passes through, an explicit
``lib_nickname`` wins next, a resolved-table match comes third, and the
``eedesigncore`` placeholder covers the no-match case.

Each fixture keeps its library beside the settings directory, so the table
passes ``allowed_roots=[tmp_path]`` to widen the default confinement (ADR-047
amendment 2).
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
from eedesigncore.tables.models import LibTable, LibTableKind, LibTableRow
from eedesigncore.tables.resolver import (
    PLACEHOLDER_NICKNAME,
    LibTableResolver,
    ResolvedLibTable,
)

FILE_NAMES: dict[LibTableKind, str] = {"fp": "fp-lib-table", "sym": "sym-lib-table"}


def _table_for(tmp_path: Path, kind: LibTableKind, nickname: str, uri: Path) -> ResolvedLibTable:
    settings = tmp_path / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    rows = [LibTableRow(name=nickname, type="KiCad", uri=str(uri))]
    LibTable(kind=kind, version=7, rows=rows).to_file(
        settings / FILE_NAMES[kind], line_ending="lf"
    )
    resolver = LibTableResolver.discover(settings_dir=settings, env={}, allowed_roots=[tmp_path])
    return resolver.resolve(kind)


@pytest.fixture
def fp_table(tmp_path: Path) -> ResolvedLibTable:
    library = tmp_path / "Capacitor_SMD.pretty"
    library.mkdir()
    (library / "C_0402_1005Metric.kicad_mod").write_text("(footprint)", encoding="utf-8")
    return _table_for(tmp_path, "fp", "Capacitor_SMD", library)


@pytest.fixture
def sym_table(tmp_path: Path) -> ResolvedLibTable:
    library = tmp_path / "Device.kicad_sym"
    library.write_text('(kicad_symbol_lib\n\t(symbol "R"\n\t)\n)\n', encoding="utf-8")
    return _table_for(tmp_path, "sym", "Device", library)


def _footprint(name: str = "C_0402_1005Metric") -> Footprint:
    return Footprint(fp_id=name)


def _symbol(name: str = "R") -> Symbol:
    return Symbol(name=name)


# ---------------------------------------------------------------------------
# inject_footprint
# ---------------------------------------------------------------------------


class TestInjectFootprint:
    def test_a_table_match_supplies_the_kicad_nickname(self, fp_table: ResolvedLibTable) -> None:
        placed = inject_footprint(Board(), _footprint(), at=(0.0, 0.0), lib_table=fp_table)
        assert placed.fp_id == "Capacitor_SMD:C_0402_1005Metric"

    def test_no_match_falls_back_to_the_placeholder(self, fp_table: ResolvedLibTable) -> None:
        placed = inject_footprint(
            Board(), _footprint("NoSuchPart"), at=(0.0, 0.0), lib_table=fp_table
        )
        assert placed.fp_id == f"{PLACEHOLDER_NICKNAME}:NoSuchPart"

    def test_no_table_falls_back_to_the_placeholder(self) -> None:
        placed = inject_footprint(Board(), _footprint(), at=(0.0, 0.0))
        assert placed.fp_id == f"{PLACEHOLDER_NICKNAME}:C_0402_1005Metric"

    def test_an_explicit_nickname_outranks_the_table(self, fp_table: ResolvedLibTable) -> None:
        placed = inject_footprint(
            Board(), _footprint(), at=(0.0, 0.0), lib_nickname="Mine", lib_table=fp_table
        )
        assert placed.fp_id == "Mine:C_0402_1005Metric"

    def test_a_pre_qualified_fpid_passes_through(self, fp_table: ResolvedLibTable) -> None:
        placed = inject_footprint(
            Board(), _footprint("Other:C_0402_1005Metric"), at=(0.0, 0.0), lib_table=fp_table
        )
        assert placed.fp_id == "Other:C_0402_1005Metric"

    def test_a_symbol_table_is_rejected(self, sym_table: ResolvedLibTable) -> None:
        with pytest.raises(ValueError, match="'fp' lib table"):
            inject_footprint(Board(), _footprint(), at=(0.0, 0.0), lib_table=sym_table)


# ---------------------------------------------------------------------------
# inject_symbol
# ---------------------------------------------------------------------------


class TestInjectSymbol:
    def test_a_table_match_supplies_the_kicad_nickname(self, sym_table: ResolvedLibTable) -> None:
        placed = inject_symbol(Schematic(), _symbol(), at=(0.0, 0.0), lib_table=sym_table)
        assert placed.lib_id == "Device:R"

    def test_no_match_falls_back_to_the_placeholder(self, sym_table: ResolvedLibTable) -> None:
        placed = inject_symbol(
            Schematic(), _symbol("NoSuchPart"), at=(0.0, 0.0), lib_table=sym_table
        )
        assert placed.lib_id == f"{PLACEHOLDER_NICKNAME}:NoSuchPart"

    def test_no_table_falls_back_to_the_placeholder(self) -> None:
        placed = inject_symbol(Schematic(), _symbol(), at=(0.0, 0.0))
        assert placed.lib_id == f"{PLACEHOLDER_NICKNAME}:R"

    def test_an_explicit_nickname_outranks_the_table(self, sym_table: ResolvedLibTable) -> None:
        placed = inject_symbol(
            Schematic(), _symbol(), at=(0.0, 0.0), lib_nickname="Mine", lib_table=sym_table
        )
        assert placed.lib_id == "Mine:R"

    def test_a_pre_qualified_name_passes_through(self, sym_table: ResolvedLibTable) -> None:
        placed = inject_symbol(Schematic(), _symbol("Other:R"), at=(0.0, 0.0), lib_table=sym_table)
        assert placed.lib_id == "Other:R"

    def test_a_footprint_table_is_rejected(self, fp_table: ResolvedLibTable) -> None:
        with pytest.raises(ValueError, match="'sym' lib table"):
            inject_symbol(Schematic(), _symbol(), at=(0.0, 0.0), lib_table=fp_table)

    def test_the_registered_lib_symbols_key_carries_the_nickname(
        self, sym_table: ResolvedLibTable
    ) -> None:
        sch = Schematic()
        inject_symbol(sch, _symbol(), at=(0.0, 0.0), lib_table=sym_table)
        assert "Device:R" in {entry.name for entry in sch.lib_symbols}


# ---------------------------------------------------------------------------
# Shared surface (R1 discipline, ADR-045)
# ---------------------------------------------------------------------------


class TestSharedSurface:
    def test_both_injects_take_the_same_lib_table_argument(self) -> None:
        import inspect

        fp_param = inspect.signature(inject_footprint).parameters["lib_table"]
        sym_param = inspect.signature(inject_symbol).parameters["lib_table"]
        assert fp_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert sym_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert fp_param.default is None
        assert sym_param.default is None
