"""Stacked pin numbers: one symbol pin on several footprint pads.

KiCad 10 reads a pin number in square brackets as a list of pad numbers.
``[1,3,5-7]`` names pads 1, 3, 5, 6 and 7, and ``[A1-A3]`` names A1, A2 and A3.
KiCad expands the list wherever a pin meets a pad: the netlist nodes, the
footprint unit pins, and the pad nets of Update PCB from Schematic.

This is ``ExpandStackedPinNotation`` (``common/string_utils.cpp``, 10.0 branch).
"""

from __future__ import annotations

_DIGITS = frozenset("0123456789")


def _split_alpha_numeric(text: str) -> tuple[str, int]:
    """The prefix and the numeric suffix of *text*, or -1 with no numeric suffix.

    ``ParseAlphaNumericPin``: the suffix is the run of digits at the end.
    """
    start = len(text)
    while start and text[start - 1] in _DIGITS:
        start -= 1
    if start == len(text):
        return "", -1
    return text[:start], int(text[start:])


def expand_pin_number(number: str) -> tuple[list[str], bool]:
    """The pad numbers that one pin number names, and whether the notation is valid.

    A number without brackets names itself. Invalid notation (an unmatched
    bracket, an empty list, or a range whose ends differ in prefix or run
    backwards) also names itself, as in KiCad, and returns ``False``.
    """
    has_bracket = "[" in number or "]" in number
    bracketed = number.startswith("[") and number.endswith("]")
    if not bracketed:
        return [number], not has_bracket
    expanded: list[str] = []
    for raw in number[1:-1].split(","):
        part = raw.strip()
        if not part:
            continue
        dash = part.find("-")
        if dash < 0:
            expanded.append(part)
            continue
        start_prefix, start_value = _split_alpha_numeric(part[:dash].strip())
        end_prefix, end_value = _split_alpha_numeric(part[dash + 1 :].strip())
        if (
            start_prefix != end_prefix
            or start_value < 0
            or end_value < 0
            or start_value > end_value
        ):
            return [number], False
        expanded.extend(f"{start_prefix}{value}" for value in range(start_value, end_value + 1))
    if not expanded:
        return [number], False
    return expanded, True


def pad_numbers(number: str) -> list[str]:
    """The pad numbers that one pin number names: :func:`expand_pin_number` alone."""
    return expand_pin_number(number)[0]


def is_stacked(number: str) -> bool:
    """True when *number* is valid stacked notation, even with one pad."""
    return number.startswith("[") and number.endswith("]") and expand_pin_number(number)[1]


def effective_pad_number(number: str) -> str:
    """The pad number a stacked pin uses in a default net name: the first in its list.

    ``SCH_PIN::GetEffectivePadNumber``. Any other number returns unchanged.
    """
    return pad_numbers(number)[0]


__all__ = ["effective_pad_number", "expand_pin_number", "is_stacked", "pad_numbers"]
