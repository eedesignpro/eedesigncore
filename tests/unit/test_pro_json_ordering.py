"""Mutation-safety tests for the JSON project pipeline (R6).

The verdict is ADR-065, built on
``context/audit/save-canonicalization-sweep-2026-08-13.md`` section 5. Object
keys are inherently mutation-safe, because both KiCad and this writer emit them
sorted. Arrays are not, and exactly three of them are backed by a sorted KiCad
container.

There is no JSON resave oracle, so every test asserts the source-verified order
directly.
"""

from __future__ import annotations

import json

from eedesigncore.pro.models import ProjectFile, ProjectLocalSettings
from eedesigncore.pro.tree import JsonTree, JsonValue
from eedesigncore.pro.writer import dump_json


def _reloaded(value: JsonValue) -> JsonValue:
    return json.loads(dump_json(value))


# ---------------------------------------------------------------------------
# Object keys: inherently mutation-safe (sweep 5.1)
# ---------------------------------------------------------------------------


def test_a_key_inserted_at_the_front_emits_in_sorted_position() -> None:
    """``nlohmann`` stores an object in a ``std::map`` (``json_settings.cpp:515-516``)."""
    tree: dict[str, JsonValue] = {"zulu": 1, "mike": 2}
    tree = {"alpha": 0, **tree}

    assert list(tree) == ["alpha", "zulu", "mike"]
    assert dump_json(tree) == '{\n  "alpha": 0,\n  "mike": 2,\n  "zulu": 1\n}\n'


def test_nested_keys_sort_at_every_depth() -> None:
    tree: dict[str, JsonValue] = {"b": {"z": 1, "a": {"y": 2, "x": 3}}, "a": 0}

    assert dump_json(tree).index('"a": 0') < dump_json(tree).index('"b"')
    assert dump_json(tree).index('"x"') < dump_json(tree).index('"y"')


def test_a_project_file_emits_scrambled_keys_sorted() -> None:
    project = ProjectFile(root=JsonTree({"zulu": 1, "alpha": 2}))
    project.root.set("mike", 3)

    assert project.to_string().splitlines()[1:4] == [
        '  "alpha": 2,',
        '  "mike": 3,',
        '  "zulu": 1',
    ]


# ---------------------------------------------------------------------------
# The three sorted arrays (sweep 5.2)
# ---------------------------------------------------------------------------


def test_hidden_netclasses_emit_sorted() -> None:
    """``PARAM_SET<wxString>``, a ``std::set`` (``project_local_settings.cpp:193``)."""
    tree: dict[str, JsonValue] = {"board": {"hidden_netclasses": ["zulu", "alpha", "mike"]}}

    assert _reloaded(tree) == {"board": {"hidden_netclasses": ["alpha", "mike", "zulu"]}}


def test_hidden_nets_keep_stored_order() -> None:
    """The neighbouring ``PARAM_LIST`` is a ``std::vector`` (``:191``), so NOT sorted."""
    tree: dict[str, JsonValue] = {"board": {"hidden_nets": ["zulu", "alpha"]}}

    assert _reloaded(tree) == {"board": {"hidden_nets": ["zulu", "alpha"]}}


def test_a_scrambled_prl_hidden_netclasses_array_emits_sorted() -> None:
    settings = ProjectLocalSettings(
        root=JsonTree({"board": {"hidden_netclasses": ["b", "a"]}}),
    )
    board = settings.section("board")
    assert board is not None
    board.set("hidden_netclasses", ["zulu", "alpha", "mike"])

    assert json.loads(settings.to_string())["board"]["hidden_netclasses"] == [
        "alpha",
        "mike",
        "zulu",
    ]


def test_netclasses_emit_default_first_then_by_name() -> None:
    """The default netclass leads (``net_settings.cpp:194-198``, ``netclass.h:92-100``)."""
    tree: dict[str, JsonValue] = {
        "net_settings": {
            "classes": [
                {"name": "Zulu"},
                {"name": "Alpha"},
                {"name": "Default"},
                {"name": "Mike"},
            ]
        }
    }

    emitted = _reloaded(tree)
    assert isinstance(emitted, dict)
    assert emitted == {
        "net_settings": {
            "classes": [
                {"name": "Default"},
                {"name": "Alpha"},
                {"name": "Mike"},
                {"name": "Zulu"},
            ]
        }
    }


def test_a_netclass_entry_kicad_would_drop_sorts_last_and_survives() -> None:
    """KiCad skips an entry with no name on load (``net_settings.cpp:211-212``).

    This writer never drops data, so such an entry emits last instead.
    """
    tree: dict[str, JsonValue] = {
        "net_settings": {"classes": [{"clearance": 1}, "junk", {"name": "Alpha"}]}
    }

    assert _reloaded(tree) == {
        "net_settings": {"classes": [{"name": "Alpha"}, {"clearance": 1}, "junk"]}
    }


def test_netclass_assignments_sort_each_inner_array() -> None:
    """Each inner value is a ``std::set<wxString>`` (``net_settings.h:234``)."""
    tree: dict[str, JsonValue] = {
        "net_settings": {"netclass_assignments": {"/SDA": ["zulu", "alpha"], "/SCL": ["b", "a"]}}
    }

    assert _reloaded(tree) == {
        "net_settings": {"netclass_assignments": {"/SCL": ["a", "b"], "/SDA": ["alpha", "zulu"]}}
    }


def test_netclass_patterns_keep_stored_order() -> None:
    """``netclass_patterns`` is a ``std::vector`` (``net_settings.cpp:290-305``)."""
    tree: dict[str, JsonValue] = {
        "net_settings": {"netclass_patterns": [{"pattern": "z"}, {"pattern": "a"}]}
    }

    assert _reloaded(tree) == {
        "net_settings": {"netclass_patterns": [{"pattern": "z"}, {"pattern": "a"}]}
    }


def test_only_the_named_paths_sort() -> None:
    """A same-named array at another path keeps its stored order."""
    tree: dict[str, JsonValue] = {
        "pcbnew": {"classes": [{"name": "Zulu"}, {"name": "Alpha"}]},
        "board": {"nested": {"hidden_netclasses": ["zulu", "alpha"]}},
    }

    assert _reloaded(tree) == tree


def test_an_array_nested_in_a_sorted_array_is_not_sorted() -> None:
    """The ``[]`` path element keeps an inner array from matching a pattern."""
    tree: dict[str, JsonValue] = {"board": {"hidden_netclasses": [["zulu", "alpha"]]}}

    assert _reloaded(tree) == {"board": {"hidden_netclasses": [["zulu", "alpha"]]}}
