"""Unit tests for the nlohmann-compatible JSON writer.

The float cases below come from ``to_chars`` and ``format_buffer`` in
``thirdparty/nlohmann_json/nlohmann/json.hpp:17887-18022``, as recorded in the
audit ``context/audit/json-save-path-2026-07-28.md`` section 4.3. The 1e15 cases
are the documented divergence from CPython ``repr``: nlohmann switches to
exponent notation at ``digits10 = 15``, CPython stays fixed until 1e16.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eedesigncore.pro.parser import parse_json
from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonValue
from eedesigncore.pro.writer import dump_json, format_exponent, format_float
from tests.private_inputs import private_path

# The in-house template project files, named in tests/private_inputs.py.
TEMPLATE_FILES = [
    private_path("template_project", ".kicad_pro"),
    private_path("template_project_local", ".kicad_prl"),
]
TEMPLATE_IDS = ["kicad_pro", "kicad_prl"]


class TestFormatFloat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (50.0, "50.0"),
            (100.0, "100.0"),
            (-2.5, "-2.5"),
            (0.1, "0.1"),
            (0.6, "0.6"),
            (0.8700000047683716, "0.8700000047683716"),
            (0.46000000834465027, "0.46000000834465027"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (999999999999999.0, "999999999999999.0"),
            (1e15, "1e+15"),
            (1234567890123456.0, "1.234567890123456e+15"),
            (1e16, "1e+16"),
            (1.5e300, "1.5e+300"),
            (-1e-300, "-1e-300"),
        ],
    )
    def test_matches_the_nlohmann_layout(self, value: float, expected: str) -> None:
        assert format_float(value) == expected

    def test_every_finite_value_carries_a_point_or_an_exponent(self) -> None:
        for value in (0.0, 1.0, 50.0, 1e15, 1e-5):
            text = format_float(value)
            assert "." in text or "e" in text

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_value_becomes_null(self, value: float) -> None:
        assert format_float(value) == "null"

    def test_the_fixed_window_ends_at_1e15(self) -> None:
        # Below the threshold the two agree.
        assert format_float(999999999999999.0) == repr(999999999999999.0)
        # At and above it they diverge, and the writer follows nlohmann.
        assert repr(1e15) == "1000000000000000.0"
        assert format_float(1e15) == "1e+15"
        assert repr(1234567890123456.0) == "1234567890123456.0"
        assert format_float(1234567890123456.0) == "1.234567890123456e+15"


class TestFormatExponent:
    @pytest.mark.parametrize(
        ("exponent", "expected"),
        [(0, "+00"), (5, "+05"), (-5, "-05"), (15, "+15"), (-15, "-15"), (300, "+300")],
    )
    def test_signs_and_pads_to_two_digits(self, exponent: int, expected: str) -> None:
        assert format_exponent(exponent) == expected


class TestDumpJsonLayout:
    def test_ends_with_exactly_one_line_feed(self) -> None:
        assert dump_json({"a": 1}).endswith("1\n}\n")
        assert not dump_json({"a": 1}).endswith("\n\n")

    def test_sorts_keys_by_code_point(self) -> None:
        text = dump_json({"PCB_AUTHOR": 1, "PCBA_REVISION": 2, "__CICD": 3, "TEMPLATE": 4})
        keys = [line.split('"')[1] for line in text.splitlines() if ": " in line]
        assert keys == ["PCBA_REVISION", "PCB_AUTHOR", "TEMPLATE", "__CICD"]

    def test_indents_two_spaces_per_level(self) -> None:
        text = dump_json({"board": {"x": [1]}})
        assert text == '{\n  "board": {\n    "x": [\n      1\n    ]\n  }\n}\n'

    def test_keeps_empty_containers_inline(self) -> None:
        assert dump_json({"o": {}, "l": []}) == '{\n  "l": [],\n  "o": {}\n}\n'

    def test_an_empty_root_object_is_two_bytes_plus_the_line_feed(self) -> None:
        assert dump_json({}) == "{}\n"

    def test_renders_booleans_and_null_bare(self) -> None:
        assert dump_json({"t": True, "f": False, "n": None}) == (
            '{\n  "f": false,\n  "n": null,\n  "t": true\n}\n'
        )

    def test_puts_one_space_after_the_key_colon(self) -> None:
        assert '"a": 1' in dump_json({"a": 1})

    def test_separates_members_with_a_comma_and_a_line_feed(self) -> None:
        assert dump_json({"a": 1, "b": 2}) == '{\n  "a": 1,\n  "b": 2\n}\n'

    def test_serialises_a_bare_scalar_root(self) -> None:
        assert dump_json("text") == '"text"\n'
        assert dump_json(3) == "3\n"


class TestDumpJsonStrings:
    def test_keeps_non_ascii_raw(self) -> None:
        assert dump_json({"v": "170°C ±1.5"}) == '{\n  "v": "170°C ±1.5"\n}\n'

    def test_escapes_the_seven_named_characters(self) -> None:
        source = '\b\t\n\f\r"\\'
        assert dump_json(source) == '"\\b\\t\\n\\f\\r\\"\\\\"\n'

    def test_escapes_other_control_characters_as_lowercase_hex(self) -> None:
        assert dump_json("\x01\x1f") == '"\\u0001\\u001f"\n'

    def test_does_not_escape_the_forward_slash_or_delete(self) -> None:
        assert dump_json("a/b\x7f") == '"a/b\x7f"\n'

    def test_escapes_keys_the_same_way(self) -> None:
        assert dump_json({'a"b': 1}) == '{\n  "a\\"b": 1\n}\n'


class TestDumpJsonNumbers:
    def test_reuses_a_stored_integer_literal(self) -> None:
        assert dump_json({"v": JsonInt.from_literal("+7")}) == '{\n  "v": +7\n}\n'

    def test_reuses_a_stored_float_literal(self) -> None:
        assert dump_json({"v": JsonFloat.from_literal("1.500")}) == '{\n  "v": 1.500\n}\n'

    def test_canonical_numbers_ignore_the_stored_literal(self) -> None:
        tree: JsonValue = {"i": JsonInt.from_literal("+7"), "f": JsonFloat.from_literal("1.500")}
        assert dump_json(tree, canonical_numbers=True) == '{\n  "f": 1.5,\n  "i": 7\n}\n'

    def test_renders_a_plain_int_as_decimal(self) -> None:
        assert dump_json({"v": 12345}) == '{\n  "v": 12345\n}\n'

    def test_renders_a_plain_float_through_the_nlohmann_rules(self) -> None:
        assert dump_json({"v": 50.0}) == '{\n  "v": 50.0\n}\n'

    def test_a_non_finite_float_becomes_null(self) -> None:
        assert dump_json({"v": float("inf")}) == '{\n  "v": null\n}\n'

    def test_keeps_the_integer_and_float_distinction(self) -> None:
        assert dump_json({"a": 4, "b": 4.0}) == '{\n  "a": 4,\n  "b": 4.0\n}\n'


class TestReferenceAgreement:
    """The stdlib encoder with the audit section 9.1 settings is the reference."""

    @staticmethod
    def _reference(tree: JsonValue) -> str:
        return (
            json.dumps(tree, sort_keys=True, indent=2, separators=(",", ": "), ensure_ascii=False)
            + "\n"
        )

    @pytest.mark.parametrize("path", TEMPLATE_FILES, ids=TEMPLATE_IDS)
    def test_agrees_with_the_stdlib_encoder_on_a_corpus_fixture(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        tree = parse_json(text)
        assert dump_json(tree) == self._reference(tree)

    @pytest.mark.parametrize("path", TEMPLATE_FILES, ids=TEMPLATE_IDS)
    def test_reproduces_the_fixture_text_exactly(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        assert dump_json(parse_json(text)) == text
