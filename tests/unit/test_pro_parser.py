"""Unit tests for the literal-preserving JSON parser."""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro.parser import parse_json, parse_json_object
from eedesigncore.pro.tree import JsonFloat, JsonInt


class TestParseJson:
    def test_parses_an_object(self) -> None:
        assert parse_json('{"a": 1}') == {"a": 1}

    def test_parses_every_scalar_kind(self) -> None:
        tree = parse_json('{"n": null, "t": true, "f": false, "s": "x"}')
        assert tree == {"n": None, "t": True, "f": False, "s": "x"}

    def test_wraps_integers_with_their_literal(self) -> None:
        tree = parse_json('{"v": 12}')
        assert isinstance(tree, dict)
        value = tree["v"]
        assert isinstance(value, JsonInt)
        assert value.literal == "12"

    def test_wraps_floats_with_their_literal(self) -> None:
        tree = parse_json('{"v": 1.500}')
        assert isinstance(tree, dict)
        value = tree["v"]
        assert isinstance(value, JsonFloat)
        assert value.literal == "1.500"
        assert value == 1.5

    def test_keeps_an_exponent_literal_untouched(self) -> None:
        tree = parse_json('{"v": 1e15}')
        assert isinstance(tree, dict)
        value = tree["v"]
        assert isinstance(value, JsonFloat)
        assert value.literal == "1e15"

    def test_booleans_are_not_wrapped_as_integers(self) -> None:
        tree = parse_json('{"v": true}')
        assert isinstance(tree, dict)
        assert tree["v"] is True

    def test_parses_a_bare_scalar_root(self) -> None:
        assert parse_json("3") == 3

    def test_rejects_a_byte_order_mark(self) -> None:
        with pytest.raises(KicadParseError, match="byte-order mark"):
            parse_json("﻿{}")

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_rejects_a_non_finite_literal(self, literal: str) -> None:
        with pytest.raises(KicadParseError, match="not valid JSON"):
            parse_json('{"v": ' + literal + "}")

    def test_rejects_malformed_text_and_reports_the_line(self) -> None:
        with pytest.raises(KicadParseError) as caught:
            parse_json('{\n  "a": 1,\n  ,\n}')
        assert caught.value.line == 3

    def test_rejects_a_truncated_document(self) -> None:
        with pytest.raises(KicadParseError, match="Invalid JSON"):
            parse_json('{"a": {')

    def test_rejects_text_that_nests_too_deeply(self) -> None:
        with pytest.raises(KicadParseError, match="nests too deeply"):
            parse_json("[" * 40000 + "]" * 40000)


class TestParseJsonObject:
    def test_returns_the_root_mapping(self) -> None:
        assert parse_json_object('{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("text", ["[]", '"text"', "3", "null"])
    def test_rejects_a_non_object_root(self, text: str) -> None:
        with pytest.raises(KicadParseError, match="root is not an object"):
            parse_json_object(text)
