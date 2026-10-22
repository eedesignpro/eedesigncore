"""Zone fill record operations and the layer-selection rule (iteration 14, P6).

A zone's fill is stored, not derived, so three operations are possible without a
zone filler: read the records out, put other records in, and turn the records
into plain copper graphics.

The flattening rules come from the KiCad 10.0.3 plot path and each one is cited
on ``flatten_fills``. These tests hold the code to those five rules, one test per
rule, so a change to any of them fails with the rule named.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    Board,
    FillType,
    PcbLine,
    PcbPoly,
    PcbText,
    PtsArc,
    Via,
    Zone,
    ZoneFill,
    ZoneFilledPolygon,
    extract_fills,
    flatten_fills,
    generate_uuids,
    item_layers,
    items_on_layers,
    replace_fills,
)


def _zone(*, net_name: str | None = "GND", filled: bool = True) -> Zone:
    return Zone(
        net_name=net_name,
        layer="F.Cu",
        id="z0000000-0000-0000-0000-000000000001",
        fill=ZoneFill(filled=filled),
        filled_polygons=[
            ZoneFilledPolygon(layer="F.Cu", pts=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]),
            ZoneFilledPolygon(
                layer="F.Cu", island=True, pts=[(20.0, 20.0), (21.0, 20.0), (21.0, 21.0)]
            ),
        ],
    )


class TestExtractFills:
    def test_it_returns_every_record(self) -> None:
        records = extract_fills(_zone())

        assert len(records) == 2
        assert records[0].pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
        assert records[1].island is True

    def test_the_records_are_detached_from_the_zone(self) -> None:
        zone = _zone()

        records = extract_fills(zone)
        records[0].pts = [(99.0, 99.0)]

        assert zone.filled_polygons[0].pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]

    def test_a_zone_with_no_fill_gives_an_empty_list(self) -> None:
        assert extract_fills(Zone()) == []


class TestReplaceFills:
    def test_the_records_arrive(self) -> None:
        source = _zone()
        target = Zone(net_name="VCC", layer="B.Cu")

        replace_fills(target, extract_fills(source))

        assert len(target.filled_polygons) == 2
        assert target.filled_polygons[0].pts == source.filled_polygons[0].pts

    def test_the_two_zones_never_alias(self) -> None:
        source = _zone()
        target = Zone()

        replace_fills(target, extract_fills(source))
        target.filled_polygons[0].pts = [(99.0, 99.0)]

        assert source.filled_polygons[0].pts != [(99.0, 99.0)]

    def test_whatever_was_there_is_gone(self) -> None:
        target = _zone()

        replace_fills(target, [ZoneFilledPolygon(layer="B.Cu", pts=[(1.0, 1.0)])])

        assert len(target.filled_polygons) == 1
        assert target.filled_polygons[0].layer == "B.Cu"

    def test_a_zone_that_receives_records_is_flagged_filled(self) -> None:
        # No corpus zone carries a record while flagged unfilled: 11714 zones on
        # 166 boards, zero exceptions.
        target = Zone(fill=ZoneFill(filled=False))

        replace_fills(target, extract_fills(_zone()))

        assert target.fill.filled is True

    def test_an_empty_list_clears_the_records_and_leaves_the_flag(self) -> None:
        # A filled zone whose fill came to nothing is a real state; 296 corpus
        # zones are in it.
        target = _zone()

        replace_fills(target, [])

        assert target.filled_polygons == []
        assert target.fill.filled is True

    def test_a_transplant_between_boards_round_trips(self) -> None:
        donor = Board(zones=[_zone()])
        host = Board(zones=[Zone(net_name="GND", layer="F.Cu", id="z2")])

        replace_fills(host.zones[0], extract_fills(donor.zones[0]))
        text = host.to_string()

        assert Board.from_string(text).to_string() == text


class TestFlattenFills:
    """One test per rule, each named for the rule it holds."""

    def test_rule_one_gives_one_graphic_per_stored_outline(self) -> None:
        graphics = flatten_fills(_zone())

        assert len(graphics) == 2
        assert all(isinstance(item, PcbPoly) for item in graphics)

    def test_rule_two_sets_the_stroke_width_to_zero(self) -> None:
        for graphic in flatten_fills(_zone()):
            assert graphic.stroke.width == 0.0

    def test_rule_three_sets_a_solid_fill(self) -> None:
        for graphic in flatten_fills(_zone()):
            # ``FillType.outline`` is what the loader gives ``(fill yes)``, which
            # is what the writer emits for FILLED_SHAPE.
            assert graphic.fill.type is FillType.outline

    def test_rule_four_strips_the_net_from_an_island_only(self) -> None:
        solid, island = flatten_fills(_zone())

        assert solid.net_name == "GND"
        assert island.net_name is None

    def test_rule_five_sets_no_solder_mask_property(self) -> None:
        for graphic in flatten_fills(_zone()):
            assert graphic.solder_mask_margin is None

    def test_the_layer_comes_from_the_record_not_the_zone(self) -> None:
        # A zone spanning layers stores one record per layer, and each record
        # names its own.
        zone = _zone()
        zone.filled_polygons[1].layer = "In1.Cu"

        assert [graphic.layer for graphic in flatten_fills(zone)] == ["F.Cu", "In1.Cu"]

    def test_a_zone_with_no_net_gives_graphics_with_no_net(self) -> None:
        graphics = flatten_fills(_zone(net_name=None))

        assert [graphic.net_name for graphic in graphics] == [None, None]

    def test_the_geometry_is_copied_point_for_point(self) -> None:
        zone = _zone()

        graphic = flatten_fills(zone)[0]

        assert graphic.pts == zone.filled_polygons[0].pts

    def test_an_arc_inside_a_fill_outline_survives(self) -> None:
        zone = Zone(
            net_name="GND",
            filled_polygons=[
                ZoneFilledPolygon(
                    layer="F.Cu",
                    pts=[
                        (0.0, 0.0),
                        PtsArc(start=(1.0, 0.0), mid=(2.0, 1.0), end=(3.0, 0.0)),
                    ],
                )
            ],
        )

        graphic = flatten_fills(zone)[0]

        assert isinstance(graphic.pts[1], PtsArc)
        assert graphic.pts[1] is not zone.filled_polygons[0].pts[1]

    def test_the_caller_mints_the_uuids(self) -> None:
        graphics = flatten_fills(_zone())

        assert [graphic.id for graphic in graphics] == [None, None]

    def test_a_flattened_zone_round_trips_on_a_board(self) -> None:
        zone = _zone()
        board = Board(drawings=list(flatten_fills(zone)))
        generate_uuids(board)

        text = board.to_string()

        assert "(gr_poly" in text
        assert Board.from_string(text).to_string() == text

    def test_the_zone_is_not_modified(self) -> None:
        zone = _zone()
        before = zone.model_dump()

        flatten_fills(zone)

        assert zone.model_dump() == before


class TestLayerSelection:
    """The rule the consumer had to hand-roll, now public."""

    def test_a_single_layer_item_names_its_layer(self) -> None:
        assert item_layers(PcbLine(layer="F.SilkS")) == ["F.SilkS"]

    def test_a_multi_layer_shape_names_the_layers_token(self) -> None:
        # ``(layers ...)`` supersedes ``(layer ...)`` when the shape spans more
        # than one layer (pcb_io_kicad_sexpr.cpp:1094-1097).
        line = PcbLine(layer="F.SilkS", layers=["F.Cu", "B.Cu"])

        assert item_layers(line) == ["F.Cu", "B.Cu"]

    def test_a_via_names_its_start_and_end_layers(self) -> None:
        assert item_layers(Via(layers=("F.Cu", "B.Cu"), size=0.6, drill=0.3)) == [
            "F.Cu",
            "B.Cu",
        ]

    def test_an_item_with_no_layer_names_none(self) -> None:
        from eedesigncore import PcbGroup

        assert item_layers(PcbGroup(name="g")) == []

    def test_the_filter_keeps_an_item_that_names_any_of_the_layers(self) -> None:
        front = PcbLine(layer="F.Cu")
        back = PcbLine(layer="B.Cu")
        both = PcbLine(layer="F.SilkS", layers=["B.Cu", "In1.Cu"])

        assert items_on_layers([front, back, both], {"B.Cu"}) == [back, both]

    def test_no_filter_keeps_everything(self) -> None:
        items = [PcbLine(layer="F.Cu"), PcbText(layer="B.SilkS")]

        assert items_on_layers(items, None) == items

    def test_an_item_with_no_layer_never_passes_a_filter(self) -> None:
        from eedesigncore import PcbGroup

        assert items_on_layers([PcbGroup(name="g")], {"F.Cu"}) == []

    def test_it_agrees_with_the_bbox_layer_argument(self) -> None:
        from eedesigncore import board_bbox, items_bbox

        board = Board(
            drawings=[
                PcbLine(start=(0.0, 0.0), end=(10.0, 10.0), layer="Edge.Cuts"),
                PcbLine(start=(-50.0, -50.0), end=(50.0, 50.0), layer="F.SilkS"),
            ]
        )

        selected = items_on_layers(board.drawings, {"Edge.Cuts"})

        assert board_bbox(board, layers={"Edge.Cuts"}) == items_bbox(selected)


@pytest.mark.parametrize("island", [True, False])
def test_a_flattened_island_matches_the_net_rule(island: bool) -> None:
    zone = Zone(
        net_name="GND",
        filled_polygons=[ZoneFilledPolygon(layer="F.Cu", island=island, pts=[(0.0, 0.0)])],
    )

    graphic = flatten_fills(zone)[0]

    assert graphic.net_name == (None if island else "GND")
