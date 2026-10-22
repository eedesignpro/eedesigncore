"""Jumper group members survive a round trip.

A jumper group is a bare list of pin or pad numbers, ``("1" "3")``. It carries
no keyword, so element zero is data.

``children()`` drops element zero, because every other list in the grammar puts
a keyword there. Reading a jumper group with it lost the first member of every
group, silently. KiCad wrote ``("1" "3")`` and this library wrote ``("3")``.

Measured on KiCad's own fixtures: ``qa/data/eeschema/issue23058``,
``qa/data/eeschema/netlists/jumpers`` and ``qa/data/pcbnew/api_kitchen_sink``.
"""

from __future__ import annotations

from eedesigncore.common.loaders import children, elements
from eedesigncore.mod.models import Footprint
from eedesigncore.sexpr.parser import parse
from eedesigncore.sym.models import SymbolLib

# ---------------------------------------------------------------------------
# The helpers themselves
# ---------------------------------------------------------------------------


def test_elements_keeps_the_first_item() -> None:
    """A string-headed list has data at element zero, not a keyword."""
    node = parse('("1" "3")')
    assert [e.value for e in elements(node)] == ["1", "3"]


def test_children_still_skips_the_first_item() -> None:
    """Guard the 489 callers that DO have a keyword at element zero."""
    node = parse("(width 0.15)")
    assert [c.value for c in children(node)] == [0.15]


def test_the_two_helpers_differ_on_a_keywordless_list() -> None:
    """State the trap this pair exists to avoid."""
    node = parse('("1" "3")')
    assert len(elements(node)) == 2
    assert len(children(node)) == 1


# ---------------------------------------------------------------------------
# Symbol: jumper_pin_groups
# ---------------------------------------------------------------------------

_SYM = """(kicad_symbol_lib
\t(version 20251024)
\t(generator "kicad_symbol_editor")
\t(generator_version "10.0")
\t(symbol "SW1"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(in_pos_files yes)
\t\t(duplicate_pin_numbers_are_jumpers yes)
\t\t(jumper_pin_groups ("1" "3") ("2" "4" "5"))
\t\t(embedded_fonts no)
\t)
)
"""


def test_symbol_keeps_every_pin_in_a_jumper_group() -> None:
    lib = SymbolLib.from_string(_SYM)
    assert lib.symbols[0].jumper_pin_groups == [["1", "3"], ["2", "4", "5"]]


def test_symbol_jumper_groups_survive_a_write_and_reload() -> None:
    """Write, read back, and compare the data.

    This asserts no member is lost. It does not assert a layout. KiCad writes
    this construct inline in a schematic and multi-line in a board, so the
    corpus oracle owns byte identity.
    """
    first = SymbolLib.from_string(_SYM)
    second = SymbolLib.from_string(first.to_string())
    assert second.symbols[0].jumper_pin_groups == [["1", "3"], ["2", "4", "5"]]


def test_symbol_single_member_group_is_untouched() -> None:
    """A one-member group must not gain or lose anything."""
    text = _SYM.replace('("1" "3") ("2" "4" "5")', '("7")')
    lib = SymbolLib.from_string(text)
    assert lib.symbols[0].jumper_pin_groups == [["7"]]
    assert SymbolLib.from_string(lib.to_string()).symbols[0].jumper_pin_groups == [["7"]]


# ---------------------------------------------------------------------------
# Footprint: jumper_pad_groups
# ---------------------------------------------------------------------------

_MOD = """(footprint "SW_Jumper"
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(layer "F.Cu")
\t(attr through_hole)
\t(duplicate_pad_numbers_are_jumpers yes)
\t(jumper_pad_groups ("1" "2") ("3" "4" "5"))
\t(embedded_fonts no)
)
"""


def test_footprint_keeps_every_pad_in_a_jumper_group() -> None:
    fp = Footprint.from_string(_MOD)
    assert fp.jumper_pad_groups == [["1", "2"], ["3", "4", "5"]]


def test_footprint_jumper_groups_survive_a_write_and_reload() -> None:
    first = Footprint.from_string(_MOD)
    second = Footprint.from_string(first.to_string())
    assert second.jumper_pad_groups == [["1", "2"], ["3", "4", "5"]]


def test_footprint_writes_every_member() -> None:
    """Guard the exact symptom: the first member disappearing from the text."""
    text = Footprint.from_string(_MOD).to_string()
    assert '"1"' in text
    assert '"3"' in text
