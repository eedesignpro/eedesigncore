"""Unit tests for the JSON value tree of the ``pro`` package."""

from __future__ import annotations

import copy
import pickle

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro.tree import JsonFloat, JsonInt, JsonTree, JsonValue, _kind


class TestJsonInt:
    def test_behaves_as_an_integer(self) -> None:
        value = JsonInt(42, "42")
        assert value == 42
        assert value + 1 == 43
        assert str(value) == "42"

    def test_keeps_the_source_literal(self) -> None:
        assert JsonInt.from_literal("-0007").literal == "-0007"
        assert JsonInt.from_literal("-0007") == -7

    def test_defaults_the_literal_to_the_decimal_text(self) -> None:
        assert JsonInt(50).literal == "50"

    def test_survives_pickling(self) -> None:
        value = pickle.loads(pickle.dumps(JsonInt.from_literal("+3")))
        assert value == 3
        assert value.literal == "+3"

    def test_survives_a_deep_copy(self) -> None:
        value = copy.deepcopy(JsonInt.from_literal("7"))
        assert value.literal == "7"

    def test_repr_names_both_value_and_literal(self) -> None:
        assert repr(JsonInt(1, "1")) == "JsonInt(1, '1')"

    def test_str_and_format_stay_plain(self) -> None:
        value = JsonInt(42, "+42")
        assert str(value) == "42"
        assert f"{value}" == "42"


class TestJsonFloat:
    def test_behaves_as_a_float(self) -> None:
        value = JsonFloat(0.5, "0.5")
        assert value == 0.5
        assert value * 2 == 1.0

    def test_keeps_the_source_literal(self) -> None:
        value = JsonFloat.from_literal("1.500")
        assert value == 1.5
        assert value.literal == "1.500"

    def test_defaults_the_literal_to_the_python_repr(self) -> None:
        assert JsonFloat(0.6).literal == "0.6"

    def test_survives_pickling(self) -> None:
        value = pickle.loads(pickle.dumps(JsonFloat.from_literal("1e15")))
        assert value == 1e15
        assert value.literal == "1e15"

    def test_survives_a_deep_copy(self) -> None:
        assert copy.deepcopy(JsonFloat.from_literal("2.50")).literal == "2.50"

    def test_repr_names_both_value_and_literal(self) -> None:
        assert repr(JsonFloat(1.0, "1.0")) == "JsonFloat(1.0, '1.0')"

    def test_str_and_format_stay_plain(self) -> None:
        value = JsonFloat(1.5, "1.500")
        assert str(value) == "1.5"
        assert f"{value}" == "1.5"


class TestJsonTree:
    def test_wraps_the_mapping_itself_not_a_copy(self) -> None:
        entries: dict[str, object] = {"a": 1}
        tree = JsonTree(entries)  # type: ignore[arg-type]
        tree.set("b", 2)
        assert entries == {"a": 1, "b": 2}

    def test_defaults_to_an_empty_object(self) -> None:
        assert len(JsonTree()) == 0
        assert JsonTree().keys() == []

    def test_keys_follow_the_source_order(self) -> None:
        tree = JsonTree({"z": 1, "a": 2})
        assert tree.keys() == ["z", "a"]

    def test_membership_and_iteration(self) -> None:
        tree = JsonTree({"a": 1, "b": 2})
        assert "a" in tree
        assert "c" not in tree
        assert list(tree) == ["a", "b"]
        assert len(tree) == 2

    def test_equality_compares_entries(self) -> None:
        assert JsonTree({"a": 1}) == JsonTree({"a": 1})
        assert JsonTree({"a": 1}) != JsonTree({"a": 2})
        assert JsonTree({"a": 1}) != {"a": 1}

    def test_repr_lists_sorted_keys(self) -> None:
        assert repr(JsonTree({"b": 1, "a": 2})) == "JsonTree(['a', 'b'])"

    def test_get_returns_the_default_when_absent(self) -> None:
        tree = JsonTree({"a": 1})
        assert tree.get("a") == 1
        assert tree.get("missing") is None
        assert tree.get("missing", "fallback") == "fallback"

    def test_remove_reports_whether_the_key_was_present(self) -> None:
        tree = JsonTree({"a": 1})
        assert tree.remove("a") is True
        assert tree.remove("a") is False

    def test_get_object_writes_through_to_the_parent(self) -> None:
        entries: dict[str, object] = {"board": {"x": 1}}
        tree = JsonTree(entries)  # type: ignore[arg-type]
        board = tree.get_object("board")
        assert board is not None
        board.set("y", 2)
        assert entries == {"board": {"x": 1, "y": 2}}

    def test_get_object_returns_none_when_absent(self) -> None:
        assert JsonTree().get_object("board") is None

    def test_typed_getters_return_values(self) -> None:
        tree = JsonTree({"s": "text", "i": 4, "b": True, "l": [1], "o": {}})
        assert tree.get_str("s") == "text"
        assert tree.get_int("i") == 4
        assert tree.get_bool("b") is True
        assert tree.get_list("l") == [1]
        assert tree.get_object("o") == JsonTree({})

    def test_typed_getters_return_none_when_absent(self) -> None:
        tree = JsonTree()
        assert tree.get_str("s") is None
        assert tree.get_int("i") is None
        assert tree.get_bool("b") is None
        assert tree.get_list("l") is None

    @pytest.mark.parametrize(
        ("getter", "key", "expected"),
        [
            ("get_str", "i", "integer"),
            ("get_int", "s", "string"),
            ("get_int", "b", "boolean"),
            ("get_bool", "i", "integer"),
            ("get_list", "o", "object"),
            ("get_object", "l", "array"),
        ],
    )
    def test_typed_getters_reject_a_wrong_type(self, getter: str, key: str, expected: str) -> None:
        tree = JsonTree({"s": "text", "i": 4, "b": True, "l": [1], "o": {}})
        with pytest.raises(KicadParseError, match=expected):
            getattr(tree, getter)(key)

    def test_type_error_names_float_and_null(self) -> None:
        tree = JsonTree({"f": 1.5, "n": None})
        with pytest.raises(KicadParseError, match="float"):
            tree.get_str("f")
        # A null value is indistinguishable from an absent key by design.
        assert tree.get_str("n") is None

    def test_path_walks_dotted_keys(self) -> None:
        tree = JsonTree({"schematic": {"top_level_sheets": [{"name": "root"}]}})
        assert tree.path("schematic.top_level_sheets") == [{"name": "root"}]

    def test_path_returns_none_on_a_missing_step(self) -> None:
        tree = JsonTree({"schematic": {"other": 1}})
        assert tree.path("schematic.top_level_sheets") is None
        assert tree.path("missing.step") is None

    def test_path_returns_none_when_a_step_is_not_an_object(self) -> None:
        tree = JsonTree({"schematic": 3})
        assert tree.path("schematic.top_level_sheets") is None

    def test_has_path_distinguishes_a_null_value_from_an_absent_key(self) -> None:
        tree = JsonTree({"schematic": {"top_level_sheets": None}})
        assert tree.has_path("schematic.top_level_sheets") is True
        assert tree.path("schematic.top_level_sheets") is None
        assert tree.has_path("schematic.other") is False
        assert tree.has_path("board.x") is False

    def test_has_path_rejects_a_non_object_step(self) -> None:
        assert JsonTree({"a": [1]}).has_path("a.b") is False


class TestKindNames:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "null"),
            (True, "boolean"),
            (3, "integer"),
            (1.5, "float"),
            ("x", "string"),
            ([], "array"),
            ({}, "object"),
        ],
    )
    def test_names_every_json_type(self, value: JsonValue, expected: str) -> None:
        assert _kind(value) == expected
