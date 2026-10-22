"""Board-into-board injection and its three companions (iteration 14, P3-P7).

Four operations meet here, because the consumer uses them as one step:

* ``new_board_from`` scaffolds an empty board from a source board's SETTINGS;
* ``inject_board`` copies a source board's CONTENT into it at a placement;
* ``derive_uuids`` gives every copy its own uuids, deterministically;
* ``rename_nets`` keeps two copies of one net electrically apart.

The property that matters most is determinism: the same source, the same key and
the same placement must give the same bytes on every run and on every machine. A
build that cannot be reproduced cannot be audited.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    DERIVED_UUID_NAMESPACE,
    Board,
    BoardGeneral,
    BoardLayer,
    Footprint,
    FootprintPlacement,
    InjectedContent,
    Pad,
    PadShape,
    PadType,
    Paper,
    PcbGroup,
    PcbLine,
    PcbText,
    Property,
    Setup,
    Stroke,
    TitleBlock,
    Zone,
    ZoneFilledPolygon,
    ZoneOutlinePolygon,
    derive_uuids,
    inject_board,
    new_board_from,
    rename_nets,
)
from eedesigncore.pcb.models import GenPoint, PcbGenerator, PcbTarget, TrackSegment, Via


def _source_board() -> Board:
    """A small board carrying one of every stream the injection must move."""
    return Board(
        general=BoardGeneral(thickness=1.6),
        paper=Paper(paper_type="A4"),
        title_block=TitleBlock(title="Widget"),
        properties=[Property(name="MPN", value="ACME-1")],
        layers=[BoardLayer(ordinal=0, canonical_name="F.Cu", layer_type="signal")],
        setup=Setup(),
        footprints=[
            Footprint(
                fp_id="lib:part",
                uuid="f0000000-0000-0000-0000-000000000001",
                at=FootprintPlacement(position=(10.0, 10.0), angle=0.0),
                pads=[
                    Pad(
                        number="1",
                        pad_type=PadType.smd,
                        shape=PadShape.rect,
                        position=(-1.0, 0.0),
                        angle=0.0,
                        size=(1.0, 1.0),
                        layers=["F.Cu"],
                        net_name="GND",
                        id="f0000000-0000-0000-0000-000000000002",
                    )
                ],
                drawings=[
                    PcbText(
                        content="U1",
                        position=(0.0, -2.0),
                        id="f0000000-0000-0000-0000-000000000003",
                    )
                ],
            )
        ],
        drawings=[
            PcbLine(
                start=(0.0, 0.0),
                end=(20.0, 0.0),
                stroke=Stroke(width=0.1),
                layer="Edge.Cuts",
                id="d0000000-0000-0000-0000-000000000001",
            ),
            PcbLine(
                start=(0.0, 5.0),
                end=(20.0, 5.0),
                stroke=Stroke(width=0.2),
                layer="F.Cu",
                net_name="GND",
                id="d0000000-0000-0000-0000-000000000002",
            ),
        ],
        tracks=[
            TrackSegment(
                start=(1.0, 1.0),
                end=(9.0, 1.0),
                width=0.2,
                layer="F.Cu",
                net="VCC",
                uuid="t0000000-0000-0000-0000-000000000001",
            ),
            Via(
                at=(9.0, 1.0),
                size=0.6,
                drill=0.3,
                net="VCC",
                uuid="t0000000-0000-0000-0000-000000000002",
            ),
        ],
        zones=[
            Zone(
                net=3,
                net_name="GND",
                layer="F.Cu",
                id="z0000000-0000-0000-0000-000000000001",
                polygons=[ZoneOutlinePolygon(pts=[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0)])],
                filled_polygons=[
                    ZoneFilledPolygon(layer="F.Cu", pts=[(1.0, 1.0), (19.0, 1.0), (19.0, 19.0)])
                ],
            )
        ],
        groups=[
            PcbGroup(
                name="block",
                id="g0000000-0000-0000-0000-000000000001",
                members=[
                    "d0000000-0000-0000-0000-000000000001",
                    "t0000000-0000-0000-0000-000000000001",
                ],
            )
        ],
        generators=[
            PcbGenerator(
                uuid="e0000000-0000-0000-0000-000000000001",
                layer="F.Cu",
                properties={"origin": GenPoint(value=(4.0, 4.0))},
                members=["t0000000-0000-0000-0000-000000000001"],
            )
        ],
        targets=[
            PcbTarget(
                position=(2.0, 2.0),
                size=1.0,
                layer="F.Cu",
                uuid="a0000000-0000-0000-0000-000000000001",
            )
        ],
    )


class TestNewBoardFrom:
    def test_settings_travel(self) -> None:
        scaffold = new_board_from(_source_board())

        assert scaffold.general is not None
        assert scaffold.general.thickness == 1.6
        assert scaffold.paper is not None
        assert scaffold.title_block is not None
        assert [layer.canonical_name for layer in scaffold.layers] == ["F.Cu"]
        assert scaffold.setup is not None
        assert [prop.name for prop in scaffold.properties] == ["MPN"]

    def test_content_does_not_travel(self) -> None:
        scaffold = new_board_from(_source_board())

        assert scaffold.footprints == []
        assert scaffold.drawings == []
        assert scaffold.tracks == []
        assert scaffold.zones == []
        assert scaffold.groups == []
        assert scaffold.generators == []
        assert scaffold.targets == []
        assert scaffold.embedded_files == []

    def test_the_copy_is_deep(self) -> None:
        source = _source_board()
        scaffold = new_board_from(source)

        scaffold.layers[0].canonical_name = "B.Cu"

        assert source.layers[0].canonical_name == "F.Cu"

    def test_the_source_is_untouched(self) -> None:
        source = _source_board()
        before = source.to_string()

        new_board_from(source)

        assert source.to_string() == before


class TestDeriveUuids:
    def test_the_same_key_gives_the_same_uuids(self) -> None:
        first, second = _source_board(), _source_board()

        derive_uuids(first, key="0|0")
        derive_uuids(second, key="0|0")

        assert first.to_string() == second.to_string()

    def test_a_different_key_gives_different_uuids(self) -> None:
        first, second = _source_board(), _source_board()

        derive_uuids(first, key="0|0")
        derive_uuids(second, key="0|1")

        assert first.footprints[0].uuid != second.footprints[0].uuid

    def test_the_derived_value_is_the_documented_formula(self) -> None:
        from uuid import uuid5

        board = _source_board()
        source_uuid = board.footprints[0].uuid
        assert source_uuid is not None

        derive_uuids(board, key="0|0")

        assert board.footprints[0].uuid == str(uuid5(DERIVED_UUID_NAMESPACE, f"{source_uuid}|0|0"))

    def test_it_is_a_valid_uuid_kicad_reads(self) -> None:
        from uuid import UUID

        board = _source_board()

        derive_uuids(board, key="0|0")

        # KIID takes any well-formed value and never inspects the version nibble
        # (common/kiid.cpp:109-140), so a version-5 uuid is accepted.
        assert UUID(str(board.footprints[0].uuid)).version == 5

    def test_a_group_still_names_its_members(self) -> None:
        board = _source_board()

        derive_uuids(board, key="0|0")

        assert board.groups[0].members == [
            board.drawings[0].id,
            board.tracks[0].uuid,
        ]

    def test_a_generator_still_names_the_items_it_owns(self) -> None:
        # The member remap covers a generator too, not only a group.
        board = _source_board()

        derive_uuids(board, key="0|0")

        assert board.generators[0].members == [board.tracks[0].uuid]

    def test_an_item_with_no_uuid_gets_none(self) -> None:
        board = Board(drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0))])

        derive_uuids(board, key="0|0")

        assert board.drawings[0].id is None


class TestRenameNets:
    def test_every_reference_kind_is_renamed(self) -> None:
        board = _source_board()

        rename_nets(board, {"GND": "GND_A", "VCC": "VCC_A"})

        assert board.zones[0].net_name == "GND_A"
        assert board.tracks[0].net == "VCC_A"
        assert board.tracks[1].net == "VCC_A"
        assert board.drawings[1].net_name == "GND_A"
        assert board.footprints[0].pads[0].net_name == "GND_A"
        assert board.net_names == {"GND_A", "VCC_A"}

    def test_the_legacy_zone_code_is_cleared(self) -> None:
        # A stale numeric code beside a renamed name is the mixed reference this
        # operation exists to prevent.
        board = _source_board()
        assert board.zones[0].net == 3

        rename_nets(board, {"GND": "GND_A"})

        assert board.zones[0].net is None

    def test_a_name_outside_the_mapping_is_left_alone(self) -> None:
        board = _source_board()

        rename_nets(board, {"GND": "GND_A"})

        assert board.tracks[0].net == "VCC"

    def test_an_empty_mapping_changes_no_byte(self) -> None:
        board = _source_board()
        before = board.to_string()

        rename_nets(board, {})

        assert board.to_string() == before


class TestInjectBoard:
    def test_every_stream_arrives(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        added = inject_board(target, source, instance_key="0|0")

        assert isinstance(added, InjectedContent)
        assert len(target.footprints) == 1
        assert len(target.drawings) == 2
        assert len(target.tracks) == 2
        assert len(target.zones) == 1
        assert len(target.groups) == 1
        assert len(target.generators) == 1
        assert len(target.targets) == 1

    def test_the_returned_items_are_the_ones_on_the_target(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        added = inject_board(target, source, instance_key="0|0")

        assert added.footprints[0] is target.footprints[0]
        assert added.tracks[0] is target.tracks[0]
        # 1 footprint, 2 drawings, 2 tracks, 1 zone, 1 group, 1 generator, 1 target.
        assert len(added.uuids) == 9

    def test_the_source_is_untouched(self) -> None:
        source = _source_board()
        before = source.to_string()
        target = new_board_from(source)

        inject_board(target, source, offset=(50.0, 50.0), rotation=90.0, instance_key="0|0")

        assert source.to_string() == before

    def test_the_placement_moves_and_turns_the_copy(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, offset=(100.0, 0.0), instance_key="0|0")

        assert target.footprints[0].at is not None
        assert target.footprints[0].at.position == (110.0, 10.0)
        line = target.drawings[0]
        assert isinstance(line, PcbLine)
        assert line.start == (100.0, 0.0)

    def test_a_turn_reaches_the_child_angles(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, rotation=90.0, instance_key="0|0")

        placed = target.footprints[0]
        assert placed.at is not None
        assert placed.at.angle == 90.0
        assert placed.pads[0].angle == 90.0

    def test_two_instances_do_not_share_a_uuid(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        first = inject_board(target, source, offset=(0.0, 0.0), instance_key="0|0")
        second = inject_board(target, source, offset=(50.0, 0.0), instance_key="0|1")

        assert set(first.uuids).isdisjoint(second.uuids)

    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        source = _source_board()
        first, second = new_board_from(source), new_board_from(source)

        for target in (first, second):
            inject_board(target, source, offset=(50.0, 25.0), rotation=90.0, instance_key="A")

        assert first.to_string() == second.to_string()

    def test_a_fresh_uuid_run_is_not_reproducible_and_says_so(self) -> None:
        source = _source_board()
        first, second = new_board_from(source), new_board_from(source)

        for target in (first, second):
            inject_board(target, source)

        assert first.to_string() != second.to_string()

    def test_the_source_uuids_can_ride_along(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, regenerate_uuids=False)

        assert target.footprints[0].uuid == "f0000000-0000-0000-0000-000000000001"

    def test_a_derived_run_cannot_also_keep_the_source_uuids(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        with pytest.raises(ValueError, match="instance_key"):
            inject_board(target, source, instance_key="A", regenerate_uuids=False)

    def test_a_net_map_keeps_two_instances_apart(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, instance_key="0|0", net_map={"GND": "GND_0", "VCC": "VCC_0"})
        inject_board(
            target,
            source,
            offset=(50.0, 0.0),
            instance_key="0|1",
            net_map={"GND": "GND_1", "VCC": "VCC_1"},
        )

        assert target.net_names == {"GND_0", "VCC_0", "GND_1", "VCC_1"}

    def test_without_a_net_map_two_instances_share_their_nets(self) -> None:
        # Not a defect. A net is a NAME in KiCad 10, so two copies of one name
        # are one net. The docstring says so and the caller decides.
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, instance_key="0|0")
        inject_board(target, source, offset=(50.0, 0.0), instance_key="0|1")

        assert target.net_names == {"GND", "VCC"}

    def test_a_group_survives_the_copy(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        added = inject_board(target, source, instance_key="0|0")

        group = added.groups[0]
        assert group.members == [added.drawings[0].id, added.tracks[0].uuid]
        assert None not in group.members

    def test_a_fill_record_rides_along_as_geometry(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, offset=(100.0, 0.0), instance_key="0|0")

        assert target.zones[0].filled_polygons[0].pts == [
            (101.0, 1.0),
            (119.0, 1.0),
            (119.0, 19.0),
        ]

    def test_an_embedded_file_arrives_once(self) -> None:
        from eedesigncore import EmbeddedFile

        source = _source_board()
        source.embedded_files.append(
            EmbeddedFile(name="font.ttf", file_type="font", data="AAAA", checksum="x")
        )
        target = new_board_from(source)

        inject_board(target, source, instance_key="0|0")
        inject_board(target, source, offset=(50.0, 0.0), instance_key="0|1")

        assert [f.name for f in target.embedded_files] == ["font.ttf"]

    def test_the_result_round_trips(self) -> None:
        source = _source_board()
        target = new_board_from(source)

        inject_board(target, source, offset=(50.0, 25.0), rotation=90.0, instance_key="A")
        text = target.to_string()

        assert Board.from_string(text).to_string() == text

    def test_a_refused_turn_leaves_the_target_alone(self) -> None:
        from eedesigncore import PcbRect

        source = _source_board()
        source.drawings.append(PcbRect(start=(0.0, 0.0), end=(1.0, 1.0)))
        target = new_board_from(source)
        before = target.to_string()

        with pytest.raises(ValueError, match="multiple of 90"):
            inject_board(target, source, rotation=45.0, instance_key="A")

        assert target.to_string() == before
