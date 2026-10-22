"""Preserved project JSON comparison boundaries."""

from pathlib import Path

import pytest

from eedesigncore import ProjectFile, compare_content, content_signature
from eedesigncore.pro import JsonTree, ProjectLocalSettings, parse_json_object


def project(text: str) -> ProjectFile:
    return ProjectFile(root=JsonTree(parse_json_object(text)))


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/pro").glob("*.kicad_pro")))
def test_fixture_key_order_is_ignored_without_mutation(path: Path) -> None:
    before = ProjectFile.from_file(path)
    original = before.to_string()

    def reverse(value):
        if isinstance(value, dict):
            return {key: reverse(item) for key, item in reversed(value.items())}
        if isinstance(value, list):
            return [reverse(item) for item in value]
        return value

    after = ProjectFile(root=JsonTree(reverse(before.root.entries)))
    result = compare_content(before, after)
    assert result.equal
    assert result.before_signature == result.after_signature
    assert before.to_string() == original


def test_numeric_spelling_and_formatting_are_ignored() -> None:
    before = project('{"a":1.0,"b":"\\u0041"}')
    after = project(' { "b": "A", "a": 1e0 }\n')
    assert compare_content(before, after).equal


@pytest.mark.parametrize(
    "before,after",
    [
        ('{"a":true}', '{"a":1}'),
        ("{}", '{"a":null}'),
        ('{"a":[]}', '{"a":{}}'),
        ('{"a":[1,2]}', '{"a":[2,1]}'),
        ('{"a":[1,1]}', '{"a":[1]}'),
        ('{"unknown":{"value":1}}', '{"unknown":{"value":2}}'),
        ('{"meta":{"filename":"a"}}', '{"meta":{"filename":"b"}}'),
    ],
)
def test_stored_distinctions_remain(before: str, after: str) -> None:
    result = compare_content(project(before), project(after))
    assert not result.equal
    assert result.before_signature != result.after_signature


def test_changed_clearance_has_complete_path() -> None:
    result = compare_content(
        project('{"board":{"design_settings":{"clearance":0.2}}}'),
        project('{"board":{"design_settings":{"clearance":0.3}}}'),
    )
    assert len(result.changes) == 1
    assert result.changes[0].path == ("root", "board", "design_settings", "clearance")
    assert result.changes[0].before == "0.2"
    assert result.changes[0].after == "0.3"


@pytest.mark.parametrize("value", [(1, 2), {1: "bad"}, object(), float("inf")])
def test_invalid_json_values_are_rejected(value: object) -> None:
    value_project = ProjectFile(root=JsonTree({"bad": value}))
    with pytest.raises((TypeError, ValueError)):
        content_signature(value_project)


def test_cycles_and_local_settings_are_rejected() -> None:
    value = ProjectFile()
    value.root.entries["cycle"] = value.root.entries
    with pytest.raises(ValueError, match="cycle"):
        content_signature(value)
    with pytest.raises(TypeError):
        content_signature(ProjectLocalSettings())
