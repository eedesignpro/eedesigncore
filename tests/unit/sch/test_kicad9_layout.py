"""KiCad 9 schematic layout: hidden fields, five tokens, and the pin order.

KiCad 9 (file version 20250114) writes a field's ``(hide yes)`` inside the
``(effects …)`` block (9.0.9 ``eeschema/sch_field.cpp:517-586``), KiCad 10 at
property level. The loader folds both spellings into ``Property.visible``, and
the writer puts the token back where the file version says.

The fixture is cut from a schematic that KiCad 9.0 saved: a diode array and a
power symbol with hidden standard fields, the bare ``(power)`` token, a placed
instance of each, and a global label with a hidden intersheet field. The ten
pins of the diode array sit in position order, not number order.
"""

from pathlib import Path

import pytest

from eedesigncore.common.sch_family.ordering import sort_draw_items
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.versions import (
    is_legacy_schematic,
    is_legacy_symbol_lib,
)
from eedesigncore.sch.models import PlacedSymbol, Schematic
from eedesigncore.sym.enums import PinElectricalType, PinShape
from tests.private_inputs import private_path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sch" / "kicad9"
FIXTURE = FIXTURE / "hidden_fields.kicad_sch"
HIDE_TOKENS = 26
KICAD9_PROJECT_SCHEMATIC = private_path("kicad9_hidden_fields_schematic", ".kicad_sch")


def _diode(sch: Schematic) -> PlacedSymbol:
    return next(sym for sym in sch.symbols if sym.lib_id == "0_JLC_Diodes:SP3420")


def test_fixture_round_trips_byte_for_byte() -> None:
    # A Windows checkout may hold the fixture with CRLF; to_string returns LF.
    source = FIXTURE.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    assert source.count("(hide yes)") == HIDE_TOKENS

    written = Schematic.from_file(FIXTURE).to_string()

    assert written == source


def test_hidden_lib_symbol_property_loads_as_hidden() -> None:
    sch = Schematic.from_file(FIXTURE)
    diode = next(sym for sym in sch.lib_symbols if sym.name == "0_JLC_Diodes:SP3420")
    by_name = {field.name: field for field in diode.fields}

    assert by_name["Reference"].visible
    assert not by_name["Footprint"].visible
    assert not by_name["Datasheet"].visible
    assert not by_name["Description"].visible


def test_hidden_placed_symbol_property_loads_as_hidden() -> None:
    by_name = {field.name: field for field in _diode(Schematic.from_file(FIXTURE)).fields}

    assert by_name["Reference"].visible
    assert not by_name["Value"].visible
    assert not by_name["Footprint"].visible


def test_upgrade_to_kicad_10_keeps_every_hidden_field() -> None:
    """A version bump moves the token to property level, and loses none."""
    sch = Schematic.from_file(FIXTURE)
    sch.version = 20260306
    sch.generator_version = "10.0"

    written = sch.to_string()

    assert written.count("(hide yes)") == HIDE_TOKENS
    assert "(in_pos_files yes)" in written
    assert "(power global)" in written
    assert "(body_style 1)" in written
    # KiCad 10 writes a field's hide token next to show_name, never in effects.
    effects_hide = "\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)\n"
    assert effects_hide not in written


def test_kicad_9_placed_fields_write_show_name_only_when_set() -> None:
    sch = Schematic.from_file(FIXTURE)
    reference = _diode(sch).fields[0]
    reference.show_name = True
    reference.do_not_autoplace = True

    written = sch.to_string()

    assert "(show_name yes)" in written
    assert "(do_not_autoplace yes)" in written
    assert "(show_name no)" not in written
    assert "(do_not_autoplace no)" not in written


def test_kicad_9_hidden_placed_field_without_effects_still_carries_the_token() -> None:
    sch = Schematic.from_file(FIXTURE)
    reference = _diode(sch).fields[0]
    reference.visible = False
    reference.attributes = None

    written = sch.to_string()
    start = written.index('\t\t(property "Reference" "D1"')
    block = written[start : written.index("\n\t\t)\n", start)]

    assert "(effects" in block
    assert "(hide yes)" in block


def test_kicad_9_placed_symbol_writes_convert_for_demorgan_only() -> None:
    sch = Schematic.from_file(FIXTURE)
    _diode(sch).body_style = 2

    written = sch.to_string()

    assert written.count("(convert 2)") == 1
    assert "(body_style" not in written


def _pin(number: str, x: float) -> Pin:
    return Pin(
        electrical_type=PinElectricalType.passive,
        shape=PinShape.line,
        position=(x, 0.0),
        angle=270.0,
        length=2.54,
        name="~",
        number=number,
    )


def test_kicad_9_sorts_library_pins_on_position_before_number() -> None:
    pins = [_pin("1", 5.08), _pin("10", -5.08), _pin("2", 0.0)]

    legacy = [pin.number for pin in sort_draw_items(pins, legacy=True)]
    current = [pin.number for pin in sort_draw_items(pins)]

    assert legacy == ["10", "2", "1"]
    assert current == ["1", "2", "10"]


def test_version_switches() -> None:
    assert is_legacy_schematic(20250114)
    assert not is_legacy_schematic(20250222)
    assert is_legacy_symbol_lib(20241209)
    assert not is_legacy_symbol_lib(20251024)


@pytest.mark.skipif(
    not KICAD9_PROJECT_SCHEMATIC.is_file(), reason="private KiCad 9 schematic not present"
)
def test_private_kicad_9_project_schematic_round_trips() -> None:
    source = KICAD9_PROJECT_SCHEMATIC.read_bytes().replace(b"\r\n", b"\n")

    written = Schematic.from_file(KICAD9_PROJECT_SCHEMATIC).to_string().encode("utf-8")

    assert written == source
