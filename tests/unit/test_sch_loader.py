"""Unit tests for the schematic loader (T1/T2).

Covers the ADR-017 hard-error contract on not-yet-modeled tokens, Lib:Name
qualification on read (embed-context Part 2a), placed-symbol parsing (Part 2b),
and the interim (sheet_instances) preserve strategy.
"""

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.sch.models import Schematic

_HEADER = (
    "(kicad_sch\n"
    "\t(version 20260306)\n"
    '\t(generator "eeschema")\n'
    '\t(generator_version "10.0")\n'
    '\t(uuid "u")\n'
    '\t(paper "A4")\n'
    "\t(lib_symbols)\n"
)


def test_hard_error_on_unknown_root_token() -> None:
    # ADR-017 hard-error contract: an unmodeled root token is never silently dropped.
    text = _HEADER + '\t(totally_unknown_token "x")\n)\n'
    with pytest.raises(KicadParseError, match="Unknown token in kicad_sch"):
        Schematic.from_string(text)


def test_lib_name_qualification_preserved_on_read() -> None:
    text = (
        "(kicad_sch\n"
        "\t(version 20260306)\n"
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        "\t(lib_symbols\n"
        '\t\t(symbol "Device:R"\n'
        "\t\t\t(exclude_from_sim no)\n"
        "\t\t\t(in_bom yes)\n"
        "\t\t\t(on_board yes)\n"
        "\t\t\t(in_pos_files yes)\n"
        "\t\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        '\t\t\t(symbol "R_0_1"\n'
        "\t\t\t)\n"
        "\t\t\t(embedded_fonts no)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )
    sch = Schematic.from_string(text)
    assert sch.lib_symbols[0].name == "Device:R"
    assert sch.lib_symbols[0].units[0].name == "R_0_1"


def test_placed_symbol_parse_fields() -> None:
    text = (
        "(kicad_sch\n"
        "\t(version 20260306)\n"
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        "\t(lib_symbols)\n"
        "\t(symbol\n"
        '\t\t(lib_id "Device:R")\n'
        "\t\t(at 10 20 90)\n"
        "\t\t(mirror y)\n"
        "\t\t(unit 2)\n"
        "\t\t(body_style 1)\n"
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(in_pos_files yes)\n"
        "\t\t(dnp no)\n"
        '\t\t(uuid "sym-uuid")\n'
        '\t\t(pin "1" (uuid "p1"))\n'
        "\t\t(instances\n"
        '\t\t\t(project "proj"\n'
        '\t\t\t\t(path "/root" (reference "R1") (unit 2))\n'
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )
    sch = Schematic.from_string(text)
    sym = sch.symbols[0]
    assert sym.lib_id == "Device:R"
    assert sym.position == (10.0, 20.0)
    assert sym.angle == 90.0
    assert sym.mirror == "y"
    assert sym.unit == 2
    assert sym.uuid == "sym-uuid"
    assert sym.pins[0].number == "1"
    assert sym.pins[0].uuid == "p1"
    assert sym.instances[0].name == "proj"
    assert sym.instances[0].paths[0].reference == "R1"
    assert sym.instances[0].paths[0].unit == 2


def test_root_sheet_instances_modeled_and_reemitted() -> None:
    text = (
        _HEADER
        + "\t(sheet_instances\n"
        + '\t\t(path "/"\n'
        + '\t\t\t(page "1")\n'
        + "\t\t)\n"
        + "\t)\n"
        + "\t(embedded_fonts no)\n"
        + ")\n"
    )
    sch = Schematic.from_string(text)
    assert sch.sheet_instances is not None
    assert len(sch.sheet_instances.paths) == 1
    assert sch.sheet_instances.paths[0].path == "/"
    assert sch.sheet_instances.paths[0].page == "1"
    assert sch.to_string() == text
