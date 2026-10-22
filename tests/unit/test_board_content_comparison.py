"""Board comparison follows source-audited ordering boundaries."""

from pathlib import Path

import pytest

from eedesigncore import Board, Footprint, Pad, Property, compare_content, content_signature
from eedesigncore.common.pcb_family.board_items import (
    Table,
    TableCell,
    Zone,
    ZoneFilledPolygon,
    ZoneOutlinePolygon,
)
from eedesigncore.common.pcb_family.shapes import PcbGroup
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.models import (
    BoardLayer,
    BoardStackup,
    BoardStackupLayer,
    BoardStackupSubLayer,
    GenString,
    PcbGenerator,
    Setup,
    TrackSegment,
    Via,
)


def identifier(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012x}"


def sample() -> Board:
    return Board(
        nets=["GND", "VCC"],
        tracks=[
            TrackSegment(uuid=identifier(1), net="GND", end=(1, 2)),
            TrackSegment(uuid=identifier(2), net="VCC", end=(3, 4)),
        ],
        footprints=[
            Footprint(
                pads=[
                    Pad(id=identifier(3), layers=["F.Cu"]),
                    Pad(id=identifier(4), number="2", layers=["F.Cu"]),
                ]
            )
        ],
    )


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/pcb").glob("*.kicad_pcb")))
def test_board_fixture_permutations(path: Path) -> None:
    before = Board.from_file(path)
    original = before.model_dump()
    after = before.model_copy(deep=True)
    for name in (
        "nets",
        "footprints",
        "drawings",
        "trailing_drawings",
        "points",
        "tracks",
        "zones",
        "groups",
        "generators",
        "targets",
    ):
        getattr(after, name).reverse()
    for footprint in after.footprints:
        for name in ("pads", "drawings", "points", "zones", "groups"):
            getattr(footprint, name).reverse()
    result = compare_content(before, after)
    assert result.equal
    assert result.before_signature == result.after_signature
    assert before.model_dump() == original


def test_track_net_edit_has_identifier_path() -> None:
    before = sample()
    after = before.model_copy(deep=True)
    after.tracks.reverse()
    after.nets.reverse()
    assert compare_content(before, after).equal
    after.tracks[1].net = "VCC"
    result = compare_content(before, after)
    assert len(result.changes) == 1
    assert result.changes[0].path == ("tracks", f'TrackSegment[uuid="{identifier(1)}"]', "net")
    assert result.before_signature != result.after_signature


@pytest.mark.parametrize("collision", ["pad", "property", "table_cell", "case", "legacy"])
def test_ambiguous_identifiers_retain_root_and_footprint_order(collision: str) -> None:
    before = sample()
    if collision == "pad":
        before.footprints[0].pads[0].id = identifier(1)
    elif collision == "property":
        before.footprints[0].properties.append(Property(name="Value", value="R", id=identifier(1)))
    elif collision == "table_cell":
        before.drawings.append(Table(cells=[TableCell(id=identifier(1))]))
    elif collision == "case":
        before.tracks[0].uuid = identifier(0xABC)
        before.footprints[0].pads[0].id = identifier(0xABC).upper()
    else:
        before.tracks[0].uuid = "legacy"
    after = before.model_copy(deep=True)
    after.tracks.reverse()
    assert not compare_content(before, after).equal
    after = before.model_copy(deep=True)
    after.footprints[0].pads.reverse()
    assert not compare_content(before, after).equal


def test_net_duplicate_counts_and_legacy_pad_code_participate() -> None:
    before = sample()
    after = before.model_copy(deep=True)
    after.nets.append("GND")
    assert not compare_content(before, after).equal
    after = before.model_copy(deep=True)
    after.footprints[0].pads[0].net_code = 12
    assert not compare_content(before, after).equal


def test_stackup_layers_and_sublayers_remain_ordered() -> None:
    before = Board(layers=[BoardLayer(canonical_name="F.Cu"), BoardLayer(canonical_name="B.Cu")])
    before.setup = Setup()
    before.setup.stackup = BoardStackup(
        layers=[
            BoardStackupLayer(
                name="core",
                sublayers=[
                    BoardStackupSubLayer(thickness=0.2),
                    BoardStackupSubLayer(thickness=0.3),
                ],
            ),
            BoardStackupLayer(name="B.Cu"),
        ]
    )
    for target in ("layers", "stackup", "sublayers"):
        after = before.model_copy(deep=True)
        if target == "layers":
            after.layers.reverse()
        elif target == "stackup":
            after.setup.stackup.layers.reverse()
        else:
            after.setup.stackup.layers[0].sublayers.reverse()
        assert not compare_content(before, after).equal


def test_via_layer_pair_and_track_endpoints_remain_ordered() -> None:
    before = Board(tracks=[Via(layers=("F.Cu", "B.Cu")), TrackSegment(start=(1, 2), end=(3, 4))])
    after = before.model_copy(deep=True)
    after.tracks[0].layers = ("B.Cu", "F.Cu")
    after.tracks[1].start, after.tracks[1].end = after.tracks[1].end, after.tracks[1].start
    assert {change.path[-1] for change in compare_content(before, after).changes} == {
        "removed[0]",
        "removed[1]",
        "added[0]",
        "added[1]",
    }


def test_empty_generator_and_properties_participate() -> None:
    before = Board(
        generators=[PcbGenerator(uuid=identifier(1), properties={"name": GenString(value="a")})]
    )
    after = before.model_copy(deep=True)
    after.generators[0].properties["name"].value = "b"
    assert compare_content(before, after).changes[0].path[-2:] == ("name", "value")
    assert not compare_content(Board(), before).equal


def test_drawing_buckets_remain_distinct() -> None:
    before = Board(drawings=[Table()])
    after = Board(trailing_drawings=[Table()])
    assert not compare_content(before, after).equal


def test_board_identifier_type_is_checked_after_bounded_snapshot() -> None:
    before = sample()
    before.tracks[0].__dict__["uuid"] = 42
    with pytest.raises(TypeError, match="identifiers must be strings"):
        content_signature(before)


@pytest.mark.parametrize("kind", ["groups", "generators", "footprint", "compact", "cycle"])
def test_group_ownership_ambiguity_retains_order(kind: str) -> None:
    before = sample()
    groups = [
        PcbGroup(id=identifier(10), members=[identifier(1)]),
        PcbGroup(id=identifier(11), members=[identifier(1)]),
    ]
    if kind == "compact":
        groups[1].members = [identifier(1).replace("-", "")]
    if kind == "cycle":
        groups[0].members = [identifier(11)]
        groups[1].members = [identifier(10)]
    if kind == "generators":
        before.generators = [PcbGenerator(uuid=g.id, members=g.members) for g in groups]
    elif kind == "footprint":
        for group in groups:
            group.members = [identifier(3)]
        before.footprints[0].groups = groups
    else:
        before.groups = groups
    after = before.model_copy(deep=True)
    after.groups.reverse()
    after.generators.reverse()
    after.footprints[0].groups.reverse()
    assert not compare_content(before, after).equal


def test_valid_nested_groups_and_repeated_single_owner_members_ignore_order() -> None:
    before = sample()
    before.groups = [
        PcbGroup(id=identifier(10), members=[identifier(11), identifier(11)]),
        PcbGroup(id=identifier(11), members=[identifier(1)]),
    ]
    after = before.model_copy(deep=True)
    after.groups.reverse()
    assert compare_content(before, after).equal


def test_footprint_move_reports_placement_without_moving_local_pads() -> None:
    before = sample()
    before.footprints[0].uuid = identifier(5)
    before.footprints[0].at = FootprintPlacement(position=(10, 20))
    after = before.model_copy(deep=True)
    after.footprints[0].at.position = (30, 40)
    result = compare_content(before, after)
    assert len(result.changes) == 1
    assert result.changes[0].path[-2:] == ("at", "position")


@pytest.mark.parametrize("field", ["polygons", "filled_polygons"])
def test_zone_vertices_and_saved_fill_remain_content(field: str) -> None:
    before = Board(
        zones=[
            Zone(
                id=identifier(1),
                polygons=[ZoneOutlinePolygon(pts=[(0, 0), (1, 0), (1, 1)])],
                filled_polygons=[ZoneFilledPolygon(layer="F.Cu", pts=[(0, 0), (1, 0), (1, 1)])],
            )
        ]
    )
    after = before.model_copy(deep=True)
    getattr(after.zones[0], field)[0].pts.reverse()
    result = compare_content(before, after)
    assert not result.equal
    assert all(field in change.path for change in result.changes)
