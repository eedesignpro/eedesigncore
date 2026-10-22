"""Regression tests for unit-index recovery from the unit name suffix (UF-003).

Before this fix the loader read the indices by slicing the unit name past the
parent name. That works only when the parent name is bare. A schematic
``(lib_symbols)`` parent name is qualified ("Lib:Name"), so the slice returned an
empty string and every schematic unit reported ``unit_index=0``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.sch_family.symbol import SymbolUnit
from eedesigncore.exceptions import KicadParseError
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.operations import extract_symbol
from eedesigncore.sym.models import SymbolLib

FIXTURES = Path(__file__).parent.parent / "fixtures"
SYM_FIXTURE = FIXTURES / "sym" / "74xx.kicad_sym"


def _sch_with_units(*unit_names: str, lib_id: str = "EPC_FETs:EPC2207") -> str:
    """A schematic whose one ``(lib_symbols)`` entry has *unit_names*."""
    units = "\n".join(
        f'\t\t\t(symbol "{unit_name}"\n'
        "\t\t\t\t(circle\n"
        "\t\t\t\t\t(center 0 0)\n"
        "\t\t\t\t\t(radius 1.27)\n"
        "\t\t\t\t\t(stroke (width 0) (type default))\n"
        "\t\t\t\t\t(fill (type none))\n"
        "\t\t\t\t)\n"
        "\t\t\t)"
        for unit_name in unit_names
    )
    return (
        "(kicad_sch\n"
        "\t(version 20260306)\n"
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        '\t(uuid "1b8d4139-a1ec-4275-bd04-cd409a97a289")\n'
        '\t(paper "A4")\n'
        "\t(lib_symbols\n"
        f'\t\t(symbol "{lib_id}"\n'
        "\t\t\t(exclude_from_sim no)\n"
        "\t\t\t(in_bom yes)\n"
        "\t\t\t(on_board yes)\n"
        f"{units}\n"
        "\t\t\t(embedded_fonts no)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )


def _units_of(lib: SymbolLib, name: str) -> list[tuple[str, int, int]]:
    for symbol in lib.symbols:
        if symbol.name == name:
            return [(u.name, u.unit_index, u.body_style_index) for u in symbol.units]
    raise AssertionError(f"symbol {name!r} not in fixture")


# ---------------------------------------------------------------------------
# Library context (.kicad_sym) — locked behavior
# ---------------------------------------------------------------------------


class TestSymContext:
    def test_multi_unit_indices(self) -> None:
        lib = SymbolLib.from_file(SYM_FIXTURE)
        assert _units_of(lib, "7454") == [
            ("7454_1_0", 1, 0),
            ("7454_1_1", 1, 1),
            ("7454_2_0", 2, 0),
            ("7454_2_1", 2, 1),
        ]

    def test_body_style_two_indices(self) -> None:
        lib = SymbolLib.from_file(SYM_FIXTURE)
        # 74LS00 carries a demorgan body style, so the second index reaches 2.
        assert _units_of(lib, "74LS00")[:4] == [
            ("74LS00_1_1", 1, 1),
            ("74LS00_1_2", 1, 2),
            ("74LS00_2_1", 2, 1),
            ("74LS00_2_2", 2, 2),
        ]

    def test_common_unit_index_zero(self) -> None:
        lib = SymbolLib.from_file(SYM_FIXTURE)
        assert _units_of(lib, "74ALVC164245")[0] == ("74ALVC164245_0_1", 0, 1)

    def test_root_name_ending_in_digits(self) -> None:
        lib = SymbolLib.from_file(SYM_FIXTURE)
        # "74469" ends in digits, so only the trailing two groups are indices.
        assert _units_of(lib, "74469") == [("74469_1_0", 1, 0), ("74469_1_1", 1, 1)]


# ---------------------------------------------------------------------------
# Schematic context (.kicad_sch lib_symbols) — the UF-003 bug
# ---------------------------------------------------------------------------


class TestSchContext:
    def test_qualified_parent_yields_real_indices(self) -> None:
        sch = Schematic.from_string(_sch_with_units("EPC2207_1_1", "EPC2207_2_1"))
        units = sch.lib_symbols[0].units
        assert [(u.unit_index, u.body_style_index) for u in units] == [(1, 1), (2, 1)]

    def test_common_and_demorgan_units(self) -> None:
        sch = Schematic.from_string(
            _sch_with_units("EPC2207_0_1", "EPC2207_1_1", "EPC2207_1_2", "EPC2207_2_0")
        )
        units = sch.lib_symbols[0].units
        assert [(u.unit_index, u.body_style_index) for u in units] == [
            (0, 1),
            (1, 1),
            (1, 2),
            (2, 0),
        ]

    def test_extract_symbol_re_encode_round_trips(self) -> None:
        sch = Schematic.from_string(_sch_with_units("EPC2207_1_1", "EPC2207_2_1"))
        symbol = extract_symbol(sch, lib_id="EPC_FETs:EPC2207")
        re_encoded = [
            SymbolUnit.encode_name(symbol.name, u.unit_index, u.body_style_index)
            for u in symbol.units
        ]
        assert re_encoded == [u.name for u in symbol.units]

    def test_placed_symbols_fixture_indices(self) -> None:
        fixture = FIXTURES / "sch" / "placed_symbols.kicad_sch"
        sch = Schematic.from_file(fixture)
        units = sch.lib_symbols[0].units
        assert [(u.name, u.unit_index, u.body_style_index) for u in units] == [
            ("132-00514_117156_1_0", 1, 0)
        ]


# ---------------------------------------------------------------------------
# Unparseable suffix raises instead of defaulting
# ---------------------------------------------------------------------------


class TestUnparseableSuffix:
    @pytest.mark.parametrize("unit_name", ["EPC2207", "EPC2207_1", "EPC2207_a_1", "EPC2207_1_b"])
    def test_raises_in_sch_context(self, unit_name: str) -> None:
        with pytest.raises(KicadParseError, match="Invalid symbol unit name suffix"):
            Schematic.from_string(_sch_with_units(unit_name))

    def test_message_names_the_unit(self) -> None:
        with pytest.raises(KicadParseError, match="EPC2207_1"):
            Schematic.from_string(_sch_with_units("EPC2207_1"))

    def test_raises_in_sym_context(self) -> None:
        bad = (
            "(kicad_symbol_lib\n"
            "\t(version 20251024)\n"
            '\t(generator "kicad_symbol_editor")\n'
            '\t(generator_version "10.0")\n'
            '\t(symbol "R"\n'
            '\t\t(symbol "R_bad"\n'
            "\t\t\t(circle\n"
            "\t\t\t\t(center 0 0)\n"
            "\t\t\t\t(radius 1.27)\n"
            "\t\t\t\t(stroke (width 0) (type default))\n"
            "\t\t\t\t(fill (type none))\n"
            "\t\t\t)\n"
            "\t\t)\n"
            "\t)\n"
            ")\n"
        )
        with pytest.raises(KicadParseError, match="Invalid symbol unit name suffix"):
            SymbolLib.from_string(bad)
