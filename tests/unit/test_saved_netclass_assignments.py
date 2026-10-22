"""Read saved assignments without changing the project document."""

from __future__ import annotations

import pytest

from eedesigncore import ProjectFile
from eedesigncore.exceptions import KicadParseError


@pytest.mark.parametrize("text", ["{}", '{"net_settings": {}}'])
def test_missing_assignments_remain_distinct_from_empty(text: str) -> None:
    assert ProjectFile.from_string(text).saved_netclass_assignments is None


@pytest.mark.parametrize("value", ["null", "{}"])
def test_kicad_empty_assignment_forms(value: str) -> None:
    project = ProjectFile.from_string('{"net_settings":{"netclass_assignments":' + value + "}}")
    assert project.saved_netclass_assignments == {}


def test_saved_membership_preserves_names_without_applying_patterns() -> None:
    project = ProjectFile.from_string(
        '{"net_settings":{"netclass_assignments":'
        '{"/Channel A/SENSE":["Power","Analog","Power","Unconfigured"],"GND":[]},'
        '"netclass_patterns":[{"pattern":"*","netclass":"All Nets"}]}}'
    )
    before = project.to_string()
    assignments = project.saved_netclass_assignments
    assert assignments == {
        "/Channel A/SENSE": ("Analog", "Power", "Unconfigured"),
        "GND": (),
    }
    assignments.clear()
    assert project.to_string() == before
    assert project.saved_netclass_assignments


@pytest.mark.parametrize(
    "value",
    ["[]", "true", '{"GND":null}', '{"GND":"Power"}', '{"GND":[1]}'],
)
def test_malformed_saved_assignments_raise(value: str) -> None:
    project = ProjectFile.from_string('{"net_settings":{"netclass_assignments":' + value + "}}")
    with pytest.raises(KicadParseError):
        _ = project.saved_netclass_assignments


def test_read_reflects_project_edits_without_a_stale_copy() -> None:
    project = ProjectFile.from_string('{"net_settings":{"netclass_assignments":{"GND":[]}}}')
    assert project.saved_netclass_assignments == {"GND": ()}
    settings = project.section("net_settings")
    assert settings is not None
    settings.set("netclass_assignments", {"GND": ["Ground"]})
    assert project.saved_netclass_assignments == {"GND": ("Ground",)}
