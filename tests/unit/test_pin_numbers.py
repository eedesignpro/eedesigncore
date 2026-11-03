"""Stacked pin numbers, against ``ExpandStackedPinNotation`` (KiCad 10.0)."""

from __future__ import annotations

import pytest

from eedesigncore.common.pin_numbers import effective_pad_number, expand_pin_number, is_stacked


@pytest.mark.parametrize(
    "number, pads, valid",
    [
        ("1", ["1"], True),
        ("", [""], True),
        ("1,2", ["1,2"], True),
        ("[1]", ["1"], True),
        ("[2,4]", ["2", "4"], True),
        ("[ 1 , 3 ]", ["1", "3"], True),
        ("[1-3,7]", ["1", "2", "3", "7"], True),
        ("[A1-A3]", ["A1", "A2", "A3"], True),
        ("[01-03]", ["1", "2", "3"], True),
        ("[1,,2]", ["1", "2"], True),
        ("[EP,1]", ["EP", "1"], True),
        ("[]", ["[]"], False),
        ("[3-1]", ["[3-1]"], False),
        ("[A1-B3]", ["[A1-B3]"], False),
        ("[A-B]", ["[A-B]"], False),
        ("[1,2", ["[1,2"], False),
        ("1]", ["1]"], False),
    ],
)
def test_expansion_matches_kicad(number: str, pads: list[str], valid: bool) -> None:
    assert expand_pin_number(number) == (pads, valid)


def test_the_first_listed_pad_names_a_default_net() -> None:
    assert effective_pad_number("[4,2]") == "4"
    assert effective_pad_number("[3-1]") == "[3-1]"
    assert effective_pad_number("7") == "7"


def test_one_pad_in_brackets_is_still_stacked() -> None:
    assert is_stacked("[1]")
    assert not is_stacked("1")
    assert not is_stacked("[3-1]")
