"""Footprints older than KiCad 10 read the way KiCad 10 reads them.

Each source file in ``tests/fixtures/mod/legacy/source`` was written by hand in
the syntax of one KiCad release. The file of the same name in ``kicad10`` is the
output of ``kicad-cli fp upgrade --force`` from KiCad 10.0.6 for it. KiCad mints
a random uuid for every item that has none, so the comparison drops uuid lines.

The rules come from ``pcb_io_kicad_sexpr_parser.cpp`` at KiCad 10.0.7; the
module :mod:`eedesigncore.mod.legacy` cites each one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eedesigncore import Footprint
from eedesigncore.mod.legacy import convert_overbar, format_layers

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "mod" / "legacy"
_NAMES = sorted(path.name for path in (_FIXTURES / "source").glob("*.kicad_mod"))
_UUID = re.compile(r'\n\t*\(uuid "[^"]*"\)')


def _kicad(name: str) -> str:
    text = (_FIXTURES / "kicad10" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    return _UUID.sub("", text)


def _ours(name: str) -> str:
    footprint = Footprint.from_file(_FIXTURES / "source" / name)
    footprint.version = 20260206
    footprint.generator = "pcbnew"
    footprint.generator_version = "10.0"
    return _UUID.sub("", footprint.to_string())


def test_every_source_has_a_kicad_reference() -> None:
    assert sorted(p.name for p in (_FIXTURES / "kicad10").glob("*.kicad_mod")) == _NAMES
    assert len(_NAMES) == 4


@pytest.mark.parametrize("name", _NAMES)
def test_output_matches_kicad_10(name: str) -> None:
    assert _ours(name) == _kicad(name)


def test_a_kicad_7_footprint_loads() -> None:
    """``(at x y unlocked)``, bare ``hide`` and ``tstamp`` failed to load in 0.39.0."""
    footprint = Footprint.from_file(_FIXTURES / "source" / "kicad7_texts.kicad_mod")
    fields = {prop.name: prop for prop in footprint.properties}
    assert fields["Reference"].unlocked
    assert not fields["Reference"].visible
    assert fields["Reference"].id == "178de9d9-fcca-45d6-b17c-9b4cb9c37fea"
    # A hidden user text becomes a user field named after the field count.
    assert fields["Field5"].value == "${REFERENCE}"
    # Before PCB fields, ki_description held the symbol description.
    assert fields["Description"].value == "A legacy symbol description"
    assert "Footprint" not in fields and "ki_keywords" not in fields


def test_a_kicad_5_module_loads() -> None:
    footprint = Footprint.from_file(_FIXTURES / "source" / "kicad5_module.kicad_mod")
    assert footprint.fp_id == "kicad5_module"
    # No attribute meant a through-hole part, and "net tie" keywords made a group.
    assert footprint.attributes.through_hole
    assert footprint.net_tie_pad_groups == ["2, 1, 3, , "]
    # A margin of 0 meant "inherit" before 9.0.
    assert footprint.solder_mask_margin is None
    texts = [item.content for item in footprint.drawings if getattr(item, "content", None)]
    assert "${REFERENCE}" in texts and "~{RESET}" in texts


def test_a_number_spelled_value_keeps_its_leading_zero() -> None:
    footprint = Footprint.from_string(
        "(module C_0805 (layer F.Cu)\n"
        "  (fp_text reference C1 (at 0 0) (layer F.SilkS) (effects (font (size 1 1))))\n"
        "  (fp_text value 0805 (at 0 1) (layer F.Fab) (effects (font (size 1 1))))\n"
        "  (pad 01 smd rect (at 0 0) (size 1 1) (layers F.Cu))\n)"
    )
    values = {prop.name: prop.value for prop in footprint.properties}
    assert values["Value"] == "0805"
    assert footprint.pads[0].number == "01"


def test_a_library_footprint_drops_lock_and_position() -> None:
    """Locking has no meaning outside a board (parser 1068-1074)."""
    footprint = Footprint.from_file(_FIXTURES / "source" / "kicad5_virtual.kicad_mod")
    assert not footprint.locked
    assert footprint.at is None
    assert footprint.attributes.exclude_from_bom
    assert footprint.attributes.exclude_from_pos_files


def test_a_current_footprint_is_kept_as_written() -> None:
    """A KiCad 10 file without the empty mandatory fields gains none on load."""
    text = (
        '(footprint "X"\n\t(version 20260206)\n\t(generator "test")\n\t(layer "F.Cu")\n'
        "\t(duplicate_pad_numbers_are_jumpers no)\n\t(embedded_fonts no)\n)\n"
    )
    assert Footprint.from_string(text).to_string() == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("~RESET~", "~{RESET}"),
        ("~CS~/~WR~", "~{CS}/~{WR}"),
        ("A~~B", "A~B"),
        ("~EN", "~{EN}"),
        ("~EN DIS", "~{EN} DIS"),
        ("~", "~"),
        ("~{EN}", "~{EN}"),
        ("(~EN)", "(~{EN})"),
    ],
)
def test_overbar_notation(text: str, expected: str) -> None:
    assert convert_overbar(text) == expected


@pytest.mark.parametrize(
    ("layers", "expected"),
    [
        (["F.Cu", "F.Paste", "F.Mask"], ["F.Cu", "F.Mask", "F.Paste"]),
        (["*.Mask", "F.SilkS", "*.Cu"], ["*.Cu", "*.Mask", "F.SilkS"]),
        (["F&B.Cu", "*.Mask"], ["*.Cu", "*.Mask"]),
        (["F.Paste"], ["F.Paste"]),
    ],
)
def test_pad_layers_take_kicad_order(layers: list[str], expected: list[str]) -> None:
    assert format_layers(layers) == expected
