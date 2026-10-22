"""Tests for the ``UnescapeString`` port (R3, iteration 11.10).

Every expectation is taken from the audit
`context/audit/escape-string-module-2026-08-18.md`, sections 4.3 and 5, which
read `common/string_utils.cpp:313-394` in the KiCad 10.0.3 clone. Where the
audit gives no expectation, this module derives none: the recursive reference
below is a transliteration of the same C++ function, and it serves as the
differential oracle for the generated cases only.
"""

from __future__ import annotations

import re
import sys
import tracemalloc
from collections.abc import Iterator

import pytest

from eedesigncore import unescape_string
from eedesigncore.common import escape
from eedesigncore.common.escape import _TOKENS

# Audit section 5, the "After unescape" column. Every row of the counterexample
# table appears here, plus the two rows whose input carries no brace.
AUDIT_TABLE = [
    ("A{space}B", "A B"),
    ("A_{space}B", "A_{space}B"),
    ("A_ B", "A_ B"),
    ("A{tab}B", "A\tB"),
    ("A{brace}B", "A{B"),
    ("A{foo}B", "A{foo}B"),
    ("A{B", "A{B"),
    ("{{space}}", "{ }"),
    ("ab", "ab"),
]


# The two shapes that cost KiCad's own recursion quadratic time and quadratic
# live memory (MEDIUM-8, ADR-084). Both resolve to themselves, because no brace
# run here forms a table token, so a correct port returns the input unchanged.
_NEST_HALF = 2000
BALANCED_NEST = "{" * _NEST_HALF + "x" + "}" * _NEST_HALF
OPEN_BRACE_RUN = "{" * 32000

# The shipped 0.10.1 code peaked at 4.5 MB on BALANCED_NEST and at 521 MB on
# OPEN_BRACE_RUN. This ceiling passes only while peak memory stays linear.
_PEAK_BYTES_PER_CHARACTER = 4


def _peak_bytes(value: str) -> tuple[str, int]:
    """Resolve ``value`` and report peak live allocation across the call, in bytes."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        resolved = unescape_string(value)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return resolved, peak - before


def _brace_visits(value: str, monkeypatch: pytest.MonkeyPatch) -> tuple[str, int]:
    """Resolve ``value`` and count how many braces the scanner hands the walk.

    One visit per brace means one left-to-right pass. Any re-scan of a region
    raises the count above the brace count, which is what quadratic time is.
    """
    visits = 0
    real = escape._BRACES

    class _CountedBraces:
        def finditer(self, source: str) -> Iterator[re.Match[str]]:
            nonlocal visits
            for match in real.finditer(source):
                visits += 1
                yield match

    monkeypatch.setattr(escape, "_BRACES", _CountedBraces())
    resolved = escape.unescape_string(value)
    return resolved, visits


def _reference(source: str) -> str:
    """A line-for-line recursive transliteration of ``UnescapeString``.

    KiCad recurses on the token interior. This function does the same, so it
    raises ``RecursionError`` where KiCad would not. That contrast is the point
    of :func:`test_a_recursive_port_breaks_where_the_shipped_port_does_not`.
    """
    length = len(source)
    if length <= 2:
        return source

    out: list[str] = []
    character = ""
    index = 0
    while index < length:
        previous = character
        character = source[index]
        if character == "{":
            token: list[str] = []
            depth = 1
            terminated = False
            index += 1
            while index < length:
                character = source[index]
                if character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
                if depth <= 0:
                    terminated = True
                    break
                token.append(character)
                index += 1
            text = "".join(token)
            if not terminated:
                out.append("{" + _reference(text))
            elif previous in {"$", "~", "^", "_"}:
                out.append("{" + _reference(text) + "}")
            elif text in _TOKENS:
                out.append(_TOKENS[text])
            else:
                out.append("{" + _reference(text) + "}")
        else:
            out.append(character)
        index += 1
    return "".join(out)


class TestTheAuditTable:
    @pytest.mark.parametrize(("value", "expected"), AUDIT_TABLE, ids=[v for v, _ in AUDIT_TABLE])
    def test_a_counterexample_row_resolves_as_the_audit_states(
        self, value: str, expected: str
    ) -> None:
        assert unescape_string(value) == expected


class TestTheTokenTable:
    @pytest.mark.parametrize(("token", "produced"), sorted(_TOKENS.items()))
    def test_a_known_token_becomes_its_character(self, token: str, produced: str) -> None:
        assert unescape_string(f"A{{{token}}}B") == f"A{produced}B"

    def test_the_table_holds_fourteen_entries(self) -> None:
        assert len(_TOKENS) == 14

    def test_no_table_key_contains_a_brace(self) -> None:
        """LOW-29. The single-pass equivalence argument rests on this property.

        A group resolves only when the next brace after its opening brace is the
        closing brace. That holds because no key contains a brace, so a key can
        never span an inner group. A key with a brace would make the single
        lookahead disagree with KiCad's depth-counted scan
        (``common/string_utils.cpp:344-357``). No sample can cover the property,
        so assert it directly.
        """
        offenders = sorted(name for name in _TOKENS if "{" in name or "}" in name)
        assert offenders == []

    def test_return_produces_a_line_feed_not_a_carriage_return(self) -> None:
        assert unescape_string("A{return}B") == "A\nB"

    def test_an_unknown_token_is_preserved_whole(self) -> None:
        assert unescape_string("A{keepout}B") == "A{keepout}B"

    def test_an_unknown_token_has_its_interior_unescaped(self) -> None:
        assert unescape_string("A{foo{space}bar}B") == "A{foo bar}B"


class TestTheLengthShortcut:
    @pytest.mark.parametrize("value", ["", "a", "ab", "{}", "{a", "{{"])
    def test_two_characters_or_fewer_return_unchanged(self, value: str) -> None:
        assert unescape_string(value) == value

    def test_the_shortcut_beats_the_token_table(self) -> None:
        """``{}`` is an empty unknown token, and the shortcut returns first."""
        assert unescape_string("{}") == "{}"

    def test_three_characters_are_scanned(self) -> None:
        assert unescape_string("{a}") == "{a}"


class TestTheUnterminatedBranch:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("A{B", "A{B"),
            ("abc{def", "abc{def"),
            ("abc{space", "abc{space"),
            ("abc{{space}", "abc{ "),
            ("A{space}B{tab", "A B{tab"),
        ],
    )
    def test_a_group_with_no_closing_brace_keeps_its_brace(
        self, value: str, expected: str
    ) -> None:
        assert unescape_string(value) == expected

    def test_the_unterminated_interior_is_still_unescaped(self) -> None:
        assert unescape_string("A{x{space}y") == "A{x y"


class TestTheMarkupGuard:
    @pytest.mark.parametrize("prefix", ["$", "~", "^", "_"])
    def test_a_space_token_after_a_markup_character_is_preserved(self, prefix: str) -> None:
        assert unescape_string(f"A{prefix}{{space}}B") == f"A{prefix}{{space}}B"

    @pytest.mark.parametrize("prefix", ["$", "~", "^", "_"])
    def test_the_guard_covers_every_token_not_only_space(self, prefix: str) -> None:
        assert unescape_string(f"A{prefix}{{tab}}B") == f"A{prefix}{{tab}}B"

    @pytest.mark.parametrize("prefix", ["$", "~", "^", "_"])
    def test_the_guarded_interior_is_still_unescaped(self, prefix: str) -> None:
        assert unescape_string(f"A{prefix}{{{{tab}}}}B") == f"A{prefix}{{\t}}B"

    def test_the_guard_needs_the_markup_character_immediately_before_the_brace(self) -> None:
        assert unescape_string("A_x{space}B") == "A_x B"

    def test_a_leading_brace_has_no_previous_character(self) -> None:
        assert unescape_string("{space}B") == " B"

    def test_a_closing_brace_is_the_previous_character_of_the_next_group(self) -> None:
        """KiCad's ``prev`` after a group is ``}``, which is not a markup character."""
        assert unescape_string("A{tab}{space}B") == "A\t B"


class TestTheDepthCounter:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("{{space}}", "{ }"),
            ("A{{tab}}B", "A{\t}B"),
            ("{a{b}c}", "{a{b}c}"),
            ("{{{space}}}", "{{ }}"),
            ("A{x{y{space}z}w}B", "A{x{y z}w}B"),
        ],
    )
    def test_a_nested_group_closes_on_its_matching_brace(self, value: str, expected: str) -> None:
        assert unescape_string(value) == expected

    def test_two_sibling_groups_resolve_independently(self) -> None:
        assert unescape_string("{space}A{tab}") == " A\t"


class TestNoPythonRecursion:
    """R3: the port walks the value once, so no nesting depth reaches the stack."""

    DEEP = "A" * 110000 + "{" * 1100 + "space" + "}" * 1100

    def test_a_hundred_kilobyte_nested_value_raises_no_recursion_error(self) -> None:
        assert len(self.DEEP) > 100 * 1024
        resolved = unescape_string(self.DEEP)
        assert resolved.startswith("A" * 110000)
        assert resolved.endswith("{" * 1099 + " " + "}" * 1099)

    @pytest.mark.parametrize("value", [BALANCED_NEST, OPEN_BRACE_RUN], ids=["nest", "run"])
    def test_neither_worst_case_shape_raises_a_recursion_error(self, value: str) -> None:
        assert unescape_string(value) == value

    def test_a_recursive_port_breaks_where_the_shipped_port_does_not(self) -> None:
        assert sys.getrecursionlimit() < 1100
        with pytest.raises(RecursionError):
            _reference(self.DEEP)

    def test_a_deep_value_still_matches_the_recursive_port_inside_the_limit(self) -> None:
        shallow = "A" + "{" * 200 + "space" + "}" * 200
        assert unescape_string(shallow) == _reference(shallow)


class TestTheCostShape:
    """MEDIUM-8: the cost stays linear on both shapes that were quadratic.

    ``TestNoPythonRecursion.DEEP`` proves the absence of ``RecursionError`` and
    nothing else. It nests 1100 deep inside 112205 characters, so every level
    wraps a handful of characters and the whole value cost 0.33 s and 1.6 MB even
    under the shipped 0.10.1 code. The two shapes below are the real worst cases.

    Neither assertion below reads a clock. The memory ceiling and the one-visit
    -per-brace count each hold only while the walk stays a single pass.
    """

    @pytest.mark.parametrize(
        "value", [BALANCED_NEST, OPEN_BRACE_RUN], ids=["balanced-nest", "open-brace-run"]
    )
    def test_a_worst_case_shape_resolves_to_itself(self, value: str) -> None:
        assert unescape_string(value) == value

    @pytest.mark.parametrize(
        "value", [BALANCED_NEST, OPEN_BRACE_RUN], ids=["balanced-nest", "open-brace-run"]
    )
    def test_peak_memory_stays_linear_in_the_length(self, value: str) -> None:
        resolved, peak = _peak_bytes(value)
        assert resolved == value
        assert peak <= _PEAK_BYTES_PER_CHARACTER * len(value)

    @pytest.mark.parametrize(
        "value", [BALANCED_NEST, OPEN_BRACE_RUN], ids=["balanced-nest", "open-brace-run"]
    )
    def test_the_walk_visits_each_brace_exactly_once(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolved, visits = _brace_visits(value, monkeypatch)
        assert resolved == value
        assert visits, "the scanner is no longer reached; re-point this cost guard"
        assert visits == value.count("{") + value.count("}")

    def test_a_resolving_value_also_visits_each_brace_exactly_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        value = "{" * 400 + "{space}" + "A{tab}B" + "}" * 400
        resolved, visits = _brace_visits(value, monkeypatch)
        assert resolved == "{" * 400 + " A\tB" + "}" * 400
        assert visits == value.count("{") + value.count("}")

    @pytest.mark.parametrize("half", [50, 100, 200])
    def test_a_bounded_nest_still_matches_the_recursive_port(self, half: int) -> None:
        value = "{" * half + "x" + "}" * half
        assert unescape_string(value) == _reference(value)

    @pytest.mark.parametrize("count", [3, 50, 200])
    def test_a_bounded_open_brace_run_still_matches_the_recursive_port(self, count: int) -> None:
        value = "{" * count
        assert unescape_string(value) == _reference(value)


class TestAgainstTheRecursiveReference:
    """A differential pass over generated values, bounded by the recursion limit."""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "A B",
            "{space}",
            "A{space}B{space}C",
            "$_{space}~^",
            "{slash}{colon}{comma}",
            "A{{{{space}}}}B",
            "{{space}{tab}}",
            "}{",
            "}}}{{{",
            "{brace}{brace}",
            "A{}B",
            "_{}",
            "{{}}",
            "\t{space}\n",
            "{space" + "}" * 5,
            "{" * 5 + "space}",
        ],
    )
    def test_the_iterative_port_matches_the_transliteration(self, value: str) -> None:
        assert unescape_string(value) == _reference(value)
