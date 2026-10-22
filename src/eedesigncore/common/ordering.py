"""Shared comparator primitives, ported from KiCad.

``StrNumCmp`` lives here because both families use it, and so do the two enum
rank tables that ``EDA_SHAPE::Compare`` reads.

KiCad orders symbol pins by pin number with ``StrNumCmp``
(``common/string_utils.cpp:825-907``), which compares runs of digits by integer
value, so ``"9"`` sorts before ``"10"``. The in-source comment at
``eeschema/sch_pin.cpp:1741`` reads "sort the same as the pads in the footprint
file", so the same comparison serves the PCB-family save order.

The port keeps the C++ loop structure, including the step that compares the
character following two equal digit runs before advancing both cursors, and the
32-bit WRAP of the digit accumulator that ADR-071 records.
"""

from __future__ import annotations

from eedesigncore.common.kimath import int32
from eedesigncore.common.models import FillType, StrokeType

# ``LINE_STYLE`` order (``include/stroke_params.h:45-55``). Our ``StrokeType`` is
# declared in token order, which is NOT the enum order, so the map is explicit.
LINE_STYLE_RANK: dict[StrokeType, int] = {
    StrokeType.default: -1,
    StrokeType.solid: 0,
    StrokeType.dash: 1,
    StrokeType.dot: 2,
    StrokeType.dash_dot: 3,
    StrokeType.dash_dot_dot: 4,
}

# ``FILL_T`` starts at 1 and runs in our declaration order
# (``include/eda_shape.h:57-66``, ``sch_io_kicad_sexpr_common.cpp:37-47``).
FILL_RANK: dict[FillType, int] = {member: index + 1 for index, member in enumerate(FillType)}

# ``StrNumCmp`` declares ``aIgnoreCase = false`` (``include/string_utils.h:203-204``)
# and ``SCH_PIN::compare`` calls it with two arguments (``eeschema/sch_pin.cpp:1742``),
# so the symbol save path compares CASE-SENSITIVELY. This constant is the single
# place that decision lives (ADR-055).
STR_NUM_CMP_IGNORE_CASE = False


def _is_digit(char: str) -> bool:
    """Report whether *char* is an ASCII digit.

    ``wxIsdigit`` accepts the ASCII digits only. ``str.isdigit`` would also
    accept other Unicode digit characters, which would diverge.
    """
    return "0" <= char <= "9"


def _upper(char: str) -> str:
    """Return the upper-case form of *char*, or *char* when the case fold grows it."""
    folded = char.upper()
    return folded if len(folded) == 1 else char


def _digit_run(text: str, pos: int, length: int) -> tuple[int, int]:
    """Consume the digit run of *text* at *pos*, C++ ``int`` semantics (ADR-071).

    Returns the accumulated value and the position just past the run.

    KiCad accumulates into ``int nb1`` with ``nb1 = nb1 * 10 + (int) c1 - '0'``
    (``common/string_utils.cpp:842-857``). The loop reads the WHOLE run: it never
    stops early and it never widens. A run longer than ten digits therefore wraps
    the 32-bit accumulator, and the wrapped value is what KiCad compares. Masking
    each step to 32 bits reproduces that exactly, because two's complement
    multiplication and addition are congruent modulo 2**32, and it holds the
    accumulator at constant width so a run of ``d`` digits costs ``O(d)``.
    """
    number = 0
    while True:
        number = (number * 10 + (ord(text[pos]) - 48)) & 0xFFFFFFFF
        pos += 1
        if pos >= length or not _is_digit(text[pos]):
            break
    return int32(number), pos


def str_num_cmp(first: str, second: str, ignore_case: bool = STR_NUM_CMP_IGNORE_CASE) -> int:
    """Compare *first* against *second* in natural order.

    Returns a negative number, zero, or a positive number, exactly as KiCad
    ``StrNumCmp`` does (``common/string_utils.cpp:825-907``). Runs of digits
    compare by integer value, so ``str_num_cmp("9", "10")`` is negative. Every
    other character compares by code point, or case-insensitively when
    *ignore_case* is true.

    A digit run wider than a 32-bit ``int`` wraps rather than growing, which
    matches the KiCad binary and can order a 20-digit run below a shorter one.
    See :func:`_digit_run` and ADR-071.
    """
    len_first = len(first)
    len_second = len(second)
    pos_first = 0
    pos_second = 0

    while pos_first < len_first and pos_second < len_second:
        char_first = first[pos_first]
        char_second = second[pos_second]

        if _is_digit(char_first) and _is_digit(char_second):
            number_first, pos_first = _digit_run(first, pos_first, len_first)
            number_second, pos_second = _digit_run(second, pos_second, len_second)

            if number_first < number_second:
                return -1
            if number_first > number_second:
                return 1

            char_first = first[pos_first] if pos_first < len_first else "\0"
            char_second = second[pos_second] if pos_second < len_second else "\0"

        if ignore_case:
            if char_first != char_second:
                upper_first = _upper(char_first)
                upper_second = _upper(char_second)
                if upper_first != upper_second:
                    return -1 if upper_first < upper_second else 1
        else:
            if char_first < char_second:
                return -1
            if char_first > char_second:
                return 1

        if pos_first < len_first:
            pos_first += 1
        if pos_second < len_second:
            pos_second += 1

    if pos_first == len_first and pos_second != len_second:
        return -1
    if pos_first != len_first and pos_second == len_second:
        return 1
    return 0
