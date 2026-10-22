"""Saved net class membership, with independent expected assignments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    Footprint,
    Pad,
    PcbLine,
    ProjectFile,
    TrackSegment,
    Zone,
    read_netclass_assignments,
)
from eedesigncore.exceptions import KicadParseError
from eedesigncore.project.netclass_patterns import NetclassPattern


def _board(names: list[str]) -> Board:
    return Board(tracks=[TrackSegment(net=name, layer="F.Cu") for name in names])


def _project(
    patterns: list[tuple[str, str]], saved: dict[str, list[str]] | None = None
) -> ProjectFile:
    return ProjectFile.from_string(
        json.dumps(
            {
                "net_settings": {
                    "netclass_assignments": saved,
                    "netclass_patterns": [
                        {"pattern": pattern, "netclass": name} for pattern, name in patterns
                    ],
                }
            }
        )
    )


def test_combines_all_sources_without_settings_or_mutation() -> None:
    board = _board(["", "GND", "/Input/SENSE", "VCC", "OTHER"])
    project = _project(
        [("/Input/*", "Analog"), ("*/SENSE", "Sensitive"), ("VCC", "Power")],
        {"/Input/SENSE": ["Explicit", "Analog"], "schematic-only": ["Other"]},
    )
    before_board, before_project = board.to_string(), project.to_string()
    assert read_netclass_assignments(board, project) == {
        "": ("Default",),
        "GND": ("Default",),
        "/Input/SENSE": ("Analog", "Explicit", "Sensitive"),
        "VCC": ("Power",),
        "OTHER": ("Default",),
    }
    assert board.to_string() == before_board
    assert project.to_string() == before_project


def test_collects_nets_from_saved_board_items() -> None:
    board = Board(
        tracks=[TrackSegment(net="Track", layer="F.Cu")],
        zones=[Zone(net_name="BoardZone")],
        drawings=[PcbLine(net_name="Drawing")],
        trailing_drawings=[PcbLine(net_name="TrailingDrawing")],
        footprints=[
            Footprint(
                pads=[Pad(net_name="Pad", layers=["F.Cu"])],
                zones=[Zone(net_name="FootprintZone")],
                drawings=[PcbLine(net_name="FootprintDrawing")],
            )
        ],
    )
    assert board.nets == []
    result = read_netclass_assignments(board, _project([("*", "All")]))
    assert result == {
        name: ("All",)
        for name in (
            "Track",
            "BoardZone",
            "Drawing",
            "TrailingDrawing",
            "Pad",
            "FootprintZone",
            "FootprintDrawing",
        )
    }


@pytest.mark.parametrize(
    ("pattern", "candidate", "expected"),
    [
        ("VCC", "VCC", True),
        ("VCC", "VCC_A", False),
        ("VCC", "vcc", False),
        ("V*", "VCC", True),
        ("D?", "D7", True),
        ("D?", "D17", False),
        ("SIG.[AB]", "SIGxA", True),
        ("SIG.[AB]", "SIG.[AB]", True),
        ("[", "[", True),
        ("(VCC|GND)", "GND", True),
        ("VCC|GND", "VCC_SUFFIX", True),
        ("VCC|GND", "PREFIX_GND", False),
        ("[[:digit:]]+", "123", True),
        (r"\Q/VCC+\E", "/VCC+", True),
        (r"\u0041", "A", True),
        (r"\d+", "１２", False),
        ("*", "NET\nNAME", True),
        ("VCC", "VCC\n", True),
        (r"A\KB", "AB", False),
        ("", "", True),
    ],
)
def test_kicad_combined_pattern_semantics(pattern: str, candidate: str, expected: bool) -> None:
    assert NetclassPattern(pattern).matches(candidate) is expected


@pytest.mark.parametrize(
    ("pattern", "names", "assigned"),
    [
        ("D[03..01]", ["D0", "D1", "D2", "D3", "D03"], {"D1", "D2", "D3"}),
        ("{D[0..1] CLK}", ["D0", "D1", "CLK", "D2"], {"D0", "D1", "CLK"}),
        ("BUS{D[0..1] CLK}", ["D0", "CLK", "BUS.CLK"], {"D0", "CLK"}),
        ("D_{[1..2]}", ["D1", "D2", "D3"], {"D1", "D2"}),
        ("~{D[0..1]}", ["~{D0}", "~{D1}", "D0"], {"~{D0}", "~{D1}"}),
        ("D[1..2]+", ["D1+", "D2+", "D3+"], {"D1+", "D2+"}),
    ],
)
def test_bus_pattern_expansion(pattern: str, names: list[str], assigned: set[str]) -> None:
    result = read_netclass_assignments(_board(names), _project([(pattern, "BusClass")]))
    assert {name for name, classes in result.items() if "BusClass" in classes} == assigned


def test_project_edits_do_not_leave_stale_matches() -> None:
    project = _project([("*", "First")])
    board = _board(["GND"])
    assert read_netclass_assignments(board, project) == {"GND": ("First",)}
    settings = project.section("net_settings")
    assert settings is not None
    settings.set("netclass_patterns", [{"pattern": "*", "netclass": "Second"}])
    assert read_netclass_assignments(board, project) == {"GND": ("Second",)}


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        '{"net_settings":{"netclass_patterns":[]}}',
        '{"net_settings":{"netclass_assignments":null}}',
        '{"net_settings":{"netclass_assignments":null,"netclass_patterns":null}}',
        '{"net_settings":{"netclass_assignments":null,"netclass_patterns":[{}]}}',
        '{"net_settings":{"netclass_assignments":null,"netclass_patterns":[3]}}',
    ],
)
def test_missing_or_malformed_settings_raise(text: str) -> None:
    with pytest.raises(KicadParseError):
        read_netclass_assignments(_board(["GND"]), ProjectFile.from_string(text))


@pytest.mark.parametrize("pattern", ["D[0..999999999]", "*" * 4097, "A\0B"])
def test_oversized_or_invalid_patterns_raise(pattern: str) -> None:
    with pytest.raises(KicadParseError):
        read_netclass_assignments(_board(["GND"]), _project([(pattern, "Class")]))


def test_matching_limit_raises_instead_of_returning_default() -> None:
    with pytest.raises(KicadParseError, match="engine limit"):
        NetclassPattern("(a+)+$").matches("a" * 1000 + "!")


def test_recorded_kicad_pattern_membership() -> None:
    path = Path(__file__).parents[1] / "fixtures/pro/netclass_pattern_membership.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    board = _board(recorded["names"])
    for row in recorded["patterns"]:
        result = read_netclass_assignments(board, _project([(row["pattern"], "Probe")]))
        actual = [name for name, classes in result.items() if "Probe" in classes]
        assert sorted(actual) == sorted(row["matched_names"]), row["pattern"]
