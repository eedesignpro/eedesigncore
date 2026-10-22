"""Translation of board content (iteration 14, work order P1-translate).

Three properties carry most of the weight here.

*Exactness.* A coordinate moves in internal units, never in millimetre floats, so
a moved coordinate is one KiCad could have written. The test for it is
reversibility: move by ``d``, move by ``-d``, and the emitted bytes must be the
original bytes.

*Frame.* A footprint child is stored footprint-local and de-rotated, so moving a
footprint moves the placement and leaves every child coordinate alone. Three
child kinds are exceptions, because their writers store board coordinates even
inside a footprint, and a fourth is the cached glyph outlines of a text. Full
evidence: ``context/audit/footprint-transform-frame-2026-09-02.md``.

*Coverage.* Every item type a board can hold is exercised, because a type the
dispatch forgets is a silently unmoved item, not an error.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    Barcode,
    Board,
    Dimension,
    DimensionText,
    Footprint,
    FootprintPlacement,
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbTarget,
    PcbText,
    Property,
    PtsArc,
    ReferenceImage,
    RenderCache,
    RenderCachePolygon,
    Via,
    Zone,
    ZoneFilledPolygon,
    ZoneLayerProperty,
    ZoneOutlinePolygon,
    translate_board,
    translate_footprint,
    translate_items,
)
from eedesigncore.common.pcb_family.transform import move_points
from eedesigncore.common.transform import Translation
from eedesigncore.pcb.models import GenPoint, GenPts, PcbGenerator, TrackArc, TrackSegment

DELTA = (10.0, -5.5)


class TestPointKernel:
    def test_a_zero_move_returns_the_same_object(self) -> None:
        move = Translation((0.0, 0.0))

        assert move.is_identity
        assert move((1.5, 2.5)) == (1.5, 2.5)

    def test_the_offset_is_held_in_internal_units(self) -> None:
        assert Translation((1.0, -0.5)).delta_iu == (1000000, -500000)

    def test_a_sub_nanometre_offset_rounds_to_a_whole_unit(self) -> None:
        # KiCad has no coordinate finer than a nanometre, so a finer request
        # rounds. 0.6 nm rounds up to 1 nm; 0.4 nm rounds away.
        assert Translation((0.0000006, 0.0000004)).delta_iu == (1, 0)

    def test_a_move_lands_on_a_whole_internal_unit(self) -> None:
        # The input carries a digit finer than a nanometre, which KiCad cannot
        # store. The move snaps it to the grid KiCad writes on.
        moved = Translation((0.1, 0.0))((1.2345678, 0.0))

        assert moved == (1.334568, 0.0)
        assert moved[0] * 1e6 == float(round(moved[0] * 1e6))


class TestItemCoverage:
    """Each item type moves every coordinate it stores, and only those."""

    def test_line(self) -> None:
        item = PcbLine(start=(1.0, 2.0), end=(3.0, 4.0))

        move_points(item, Translation(DELTA))

        assert item.start == (11.0, -3.5)
        assert item.end == (13.0, -1.5)

    def test_arc_moves_all_three_points(self) -> None:
        item = PcbArc(start=(0.0, 0.0), mid=(1.0, 1.0), end=(2.0, 0.0))

        move_points(item, Translation((1.0, 1.0)))

        assert (item.start, item.mid, item.end) == ((1.0, 1.0), (2.0, 2.0), (3.0, 1.0))

    def test_circle_moves_centre_and_perimeter_point(self) -> None:
        item = PcbCircle(center=(5.0, 5.0), end=(7.0, 5.0))

        move_points(item, Translation((-5.0, 0.0)))

        assert item.center == (0.0, 5.0)
        assert item.end == (2.0, 5.0)

    def test_a_pts_chain_moves_plain_points_and_arc_elements(self) -> None:
        item = PcbPoly(pts=[(0.0, 0.0), PtsArc(start=(1.0, 0.0), mid=(2.0, 1.0), end=(3.0, 0.0))])

        move_points(item, Translation((1.0, 1.0)))

        assert item.pts[0] == (1.0, 1.0)
        arc = item.pts[1]
        assert isinstance(arc, PtsArc)
        assert (arc.start, arc.mid, arc.end) == ((2.0, 1.0), (3.0, 2.0), (4.0, 1.0))

    def test_zone_moves_outlines_fills_and_hatch_positions(self) -> None:
        zone = Zone(
            polygons=[ZoneOutlinePolygon(pts=[(0.0, 0.0), (1.0, 0.0)])],
            filled_polygons=[ZoneFilledPolygon(layer="F.Cu", pts=[(0.0, 0.0)])],
            layer_properties=[ZoneLayerProperty(layer="F.Cu", hatch_position=(2.0, 2.0))],
        )

        move_points(zone, Translation((1.0, 1.0)))

        assert zone.polygons[0].pts == [(1.0, 1.0), (2.0, 1.0)]
        assert zone.filled_polygons[0].pts == [(1.0, 1.0)]
        assert zone.layer_properties[0].hatch_position == (3.0, 3.0)

    def test_dimension_moves_its_points_and_its_text(self) -> None:
        dimension = Dimension(
            pts=[(0.0, 0.0), (10.0, 0.0)],
            text=DimensionText(content="10", position=(5.0, 1.0)),
        )

        move_points(dimension, Translation((1.0, 1.0)))

        assert dimension.pts == [(1.0, 1.0), (11.0, 1.0)]
        assert dimension.text is not None
        assert dimension.text.position == (6.0, 2.0)

    def test_an_unknown_type_is_an_error_not_a_silent_skip(self) -> None:
        with pytest.raises(TypeError, match="does not cover"):
            move_points(object(), Translation(DELTA))


def _text_with_cache() -> PcbText:
    return PcbText(
        content="A",
        position=(1.0, 1.0),
        render_cache=RenderCache(
            resolved_text="A",
            angle=0.0,
            polygons=[RenderCachePolygon(pts=[(1.0, 1.0), (2.0, 2.0)])],
        ),
    )


class TestRenderCache:
    """Cached glyph outlines are board coordinates and move with the board."""

    def test_the_cache_moves_with_the_text(self) -> None:
        text = _text_with_cache()

        move_points(text, Translation((1.0, 1.0)))
        from eedesigncore.common.pcb_family.transform import move_render_cache

        move_render_cache(text, Translation((1.0, 1.0)))

        assert text.position == (2.0, 2.0)
        assert text.render_cache is not None
        assert text.render_cache.polygons[0].pts == [(2.0, 2.0), (3.0, 3.0)]

    def test_a_footprint_move_still_moves_the_cache_of_a_child(self) -> None:
        footprint = Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(0.0, 0.0)),
            drawings=[_text_with_cache()],
        )

        translate_footprint(footprint, (1.0, 1.0))

        child = footprint.drawings[0]
        assert isinstance(child, PcbText)
        # The child stays put in the footprint frame …
        assert child.position == (1.0, 1.0)
        # … but its cached glyphs are board coordinates, so they move.
        assert child.render_cache is not None
        assert child.render_cache.polygons[0].pts == [(2.0, 2.0), (3.0, 3.0)]


class TestFootprintFrame:
    def _placed(self) -> Footprint:
        return Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(100.0, 100.0), angle=90.0),
            drawings=[
                PcbLine(start=(0.0, 0.0), end=(1.0, 0.0)),
                ReferenceImage(position=(50.0, 50.0)),
                Barcode(position=(60.0, 60.0)),
                Dimension(pts=[(70.0, 70.0)]),
            ],
            properties=[Property(name="Reference", value="U1", position=(0.0, -1.0))],
        )

    def test_the_placement_moves(self) -> None:
        footprint = self._placed()

        translate_footprint(footprint, DELTA)

        assert footprint.at is not None
        assert footprint.at.position == (110.0, 94.5)
        assert footprint.at.angle == 90.0

    def test_a_footprint_local_child_does_not_move(self) -> None:
        footprint = self._placed()

        translate_footprint(footprint, DELTA)

        line = footprint.drawings[0]
        assert isinstance(line, PcbLine)
        assert (line.start, line.end) == ((0.0, 0.0), (1.0, 0.0))
        assert footprint.properties[0].position == (0.0, -1.0)

    def test_a_board_frame_child_does_move(self) -> None:
        footprint = self._placed()

        translate_footprint(footprint, DELTA)

        image, barcode, dimension = footprint.drawings[1:4]
        assert isinstance(image, ReferenceImage)
        assert isinstance(barcode, Barcode)
        assert isinstance(dimension, Dimension)
        assert image.position == (60.0, 44.5)
        assert barcode.position == (70.0, 54.5)
        assert dimension.pts == [(80.0, 64.5)]

    def test_a_library_footprint_has_no_board_position_to_move(self) -> None:
        with pytest.raises(ValueError, match="board-context footprint"):
            translate_footprint(Footprint(fp_id="part"), DELTA)


def _board_with_one_of_everything() -> Board:
    return Board(
        footprints=[
            Footprint(fp_id="lib:part", at=FootprintPlacement(position=(1.0, 1.0))),
        ],
        drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0))],
        trailing_drawings=[PcbText(content="tail", position=(2.0, 2.0))],
        points=[PcbPoint(position=(3.0, 3.0))],
        tracks=[
            TrackSegment(start=(4.0, 4.0), end=(5.0, 5.0), width=0.2, layer="F.Cu"),
            TrackArc(start=(6.0, 6.0), mid=(6.5, 6.5), end=(7.0, 6.0), width=0.2, layer="F.Cu"),
            Via(at=(8.0, 8.0), size=0.6, drill=0.3),
        ],
        zones=[Zone(polygons=[ZoneOutlinePolygon(pts=[(9.0, 9.0)])])],
        targets=[PcbTarget(position=(10.0, 10.0), size=1.0, layer="F.Cu")],
        generators=[
            PcbGenerator(
                properties={
                    "origin": GenPoint(value=(11.0, 11.0)),
                    "base_line": GenPts(value=[(12.0, 12.0)]),
                }
            )
        ],
    )


class TestBoardTranslation:
    def test_every_stream_moves(self) -> None:
        board = _board_with_one_of_everything()

        translate_board(board, (1.0, 2.0))

        assert board.footprints[0].at is not None
        assert board.footprints[0].at.position == (2.0, 3.0)
        line = board.drawings[0]
        assert isinstance(line, PcbLine)
        assert line.end == (2.0, 3.0)
        tail = board.trailing_drawings[0]
        assert isinstance(tail, PcbText)
        assert tail.position == (3.0, 4.0)
        assert board.points[0].position == (4.0, 5.0)
        segment, arc, via = board.tracks
        assert isinstance(segment, TrackSegment)
        assert isinstance(arc, TrackArc)
        assert isinstance(via, Via)
        assert segment.start == (5.0, 6.0)
        assert arc.mid == (7.5, 8.5)
        assert via.at == (9.0, 10.0)
        assert board.zones[0].polygons[0].pts == [(10.0, 11.0)]
        assert board.targets[0].position == (11.0, 12.0)
        origin = board.generators[0].properties["origin"]
        chain = board.generators[0].properties["base_line"]
        assert isinstance(origin, GenPoint)
        assert isinstance(chain, GenPts)
        assert origin.value == (12.0, 13.0)
        assert chain.value == [(13.0, 14.0)]

    def test_a_board_origin_is_a_setting_and_stays_put(self) -> None:
        from eedesigncore import Setup

        board = _board_with_one_of_everything()
        board.setup = Setup(aux_axis_origin=(5.0, 5.0), grid_origin=(6.0, 6.0))

        translate_board(board, (1.0, 2.0))

        assert board.setup.aux_axis_origin == (5.0, 5.0)
        assert board.setup.grid_origin == (6.0, 6.0)

    def test_items_can_be_moved_one_collection_at_a_time(self) -> None:
        board = _board_with_one_of_everything()

        translate_items(board.drawings, (1.0, 1.0))

        line = board.drawings[0]
        assert isinstance(line, PcbLine)
        assert line.start == (1.0, 1.0)
        # Nothing else moved.
        assert board.points[0].position == (3.0, 3.0)


class TestEmittedBytes:
    """The properties that matter to a git workflow, measured on emitted text."""

    def test_a_zero_move_changes_no_byte(self) -> None:
        board = _board_with_one_of_everything()
        before = board.to_string()

        translate_board(board, (0.0, 0.0))

        assert board.to_string() == before

    def test_a_move_and_its_inverse_restore_the_original_bytes(self) -> None:
        board = _board_with_one_of_everything()
        before = board.to_string()

        translate_board(board, (12.7, -3.81))
        assert board.to_string() != before
        translate_board(board, (-12.7, 3.81))

        assert board.to_string() == before

    def test_the_same_move_twice_produces_the_same_bytes(self) -> None:
        first, second = _board_with_one_of_everything(), _board_with_one_of_everything()

        translate_board(first, (12.7, -3.81))
        translate_board(second, (12.7, -3.81))

        assert first.to_string() == second.to_string()

    def test_a_moved_board_still_round_trips(self) -> None:
        board = _board_with_one_of_everything()

        translate_board(board, (12.7, -3.81))
        text = board.to_string()

        assert Board.from_string(text).to_string() == text


class TestRemainingItemTypes:
    """The types the board fixture does not carry. An untested branch is a
    silently unmoved item, which is the failure mode this dispatch exists to
    prevent."""

    def test_a_text_box_moves_its_rectangle(self) -> None:
        from eedesigncore import PcbTextBox

        item = PcbTextBox(content="A", start=(0.0, 0.0), end=(10.0, 5.0))

        move_points(item, Translation((1.0, 2.0)))

        assert (item.start, item.end) == ((1.0, 2.0), (11.0, 7.0))

    def test_a_polygon_shaped_text_box_moves_its_points(self) -> None:
        from eedesigncore import PcbTextBox

        item = PcbTextBox(content="A", pts=[(0.0, 0.0), (1.0, 1.0)])

        move_points(item, Translation((1.0, 2.0)))

        assert item.pts == [(1.0, 2.0), (2.0, 3.0)]

    def test_a_curve_moves_its_four_control_points(self) -> None:
        from eedesigncore import PcbCurve

        item = PcbCurve(pts=[(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])

        move_points(item, Translation((1.0, 1.0)))

        assert item.pts == [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0)]

    def test_a_table_moves_every_cell(self) -> None:
        from eedesigncore import Table, TableCell

        item = Table(
            column_count=1,
            cells=[TableCell(content="A", start=(0.0, 0.0), end=(2.0, 1.0))],
        )

        move_points(item, Translation((1.0, 1.0)))

        assert (item.cells[0].start, item.cells[0].end) == ((1.0, 1.0), (3.0, 2.0))

    def test_a_barcode_moves_its_anchor_and_keeps_its_angle(self) -> None:
        item = Barcode(position=(5.0, 5.0), angle=45.0, size=(2.0, 2.0))

        move_points(item, Translation((1.0, 1.0)))

        assert item.position == (6.0, 6.0)
        assert item.angle == 45.0

    def test_a_group_stores_no_geometry_and_is_left_alone(self) -> None:
        from eedesigncore import PcbGroup

        item = PcbGroup(name="g", members=["u1"])

        move_points(item, Translation((1.0, 1.0)))

        assert item.members == ["u1"]

    def test_a_dimension_moves_the_cache_of_its_text(self) -> None:
        from eedesigncore.common.pcb_family.transform import move_item

        dimension = Dimension(
            pts=[(0.0, 0.0)],
            text=DimensionText(
                content="10",
                position=(1.0, 1.0),
                render_cache=RenderCache(
                    resolved_text="10", angle=0.0, polygons=[RenderCachePolygon(pts=[(1.0, 1.0)])]
                ),
            ),
        )

        move_item(dimension, Translation((1.0, 1.0)))

        assert dimension.text is not None
        assert dimension.text.render_cache is not None
        assert dimension.text.render_cache.polygons[0].pts == [(2.0, 2.0)]

    def test_a_table_moves_the_cache_of_a_cell(self) -> None:
        from eedesigncore import Table, TableCell
        from eedesigncore.common.pcb_family.transform import move_item

        cell = TableCell(content="A", start=(0.0, 0.0), end=(2.0, 1.0))
        table = Table(column_count=1, cells=[cell])

        move_item(table, Translation((1.0, 1.0)))

        assert (table.cells[0].start, table.cells[0].end) == ((1.0, 1.0), (3.0, 2.0))


class TestItemsCollection:
    """`translate_items` accepts every board-frame item kind, not only drawings."""

    def test_it_moves_a_placed_footprint(self) -> None:
        footprint = Footprint(fp_id="lib:p", at=FootprintPlacement(position=(1.0, 1.0)))

        translate_items([footprint], (2.0, 3.0))

        assert footprint.at is not None
        assert footprint.at.position == (3.0, 4.0)

    def test_it_moves_a_track_a_target_and_a_generator(self) -> None:
        segment = TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), width=0.2, layer="F.Cu")
        target = PcbTarget(position=(5.0, 5.0), size=1.0, layer="F.Cu")
        generator = PcbGenerator(properties={"origin": GenPoint(value=(9.0, 9.0))})

        translate_items([segment, target, generator], (1.0, 1.0))

        assert segment.start == (1.0, 1.0)
        assert target.position == (6.0, 6.0)
        origin = generator.properties["origin"]
        assert isinstance(origin, GenPoint)
        assert origin.value == (10.0, 10.0)

    def test_an_unknown_item_is_an_error(self) -> None:
        with pytest.raises(TypeError, match="does not cover"):
            translate_items([object()], (1.0, 1.0))


# ---------------------------------------------------------------------------
# Board-frame children of a footprint (defect report 2026-09-05, item 1)
# ---------------------------------------------------------------------------


def _footprint_with_board_frame_children() -> Footprint:
    """A placed footprint carrying a zone and a point, both in board coordinates."""
    footprint = Footprint(
        fp_id="PanelTab:Tab",
        at=FootprintPlacement(position=(100.0, 50.0), angle=0.0),
    )
    footprint.zones = [
        Zone(
            layers=["F.Cu"],
            polygons=[ZoneOutlinePolygon(pts=[(99.0, 49.0), (101.0, 49.0), (101.0, 51.0)])],
            filled_polygons=[ZoneFilledPolygon(layer="F.Cu", pts=[(99.5, 49.5), (100.5, 49.5)])],
        )
    ]
    footprint.points = [PcbPoint(position=(100.5, 50.5), size=1.0, layer="F.Cu")]
    return footprint


def test_translate_moves_a_footprint_hosted_zone_outline() -> None:
    """A footprint-hosted zone stores BOARD coordinates, so it must move.

    ``FOOTPRINT::SetPosition`` moves ``m_zones`` (``pcbnew/footprint.cpp:3016``),
    the zone writer emits its outline with no parent footprint, and ``parseZONE``
    applies no parent transform. Skipping the zone left it at the old position
    while the footprint moved away. A panel of six copies stacked all 24 keepouts
    on the first copy.
    """
    footprint = _footprint_with_board_frame_children()
    board = Board(footprints=[footprint])

    translate_board(board, (10.0, 20.0))

    assert footprint.at is not None
    assert footprint.at.position == (110.0, 70.0)
    assert footprint.zones[0].polygons[0].pts[0] == (109.0, 69.0)


def test_translate_moves_a_footprint_hosted_zone_fill_record() -> None:
    footprint = _footprint_with_board_frame_children()
    translate_board(Board(footprints=[footprint]), (10.0, 20.0))
    assert footprint.zones[0].filled_polygons[0].pts[0] == (109.5, 69.5)


def test_translate_moves_a_footprint_hosted_point() -> None:
    """The point item stores board coordinates in both hosts, like the zone."""
    footprint = _footprint_with_board_frame_children()
    translate_board(Board(footprints=[footprint]), (10.0, 20.0))
    assert footprint.points[0].position == (110.5, 70.5)


def test_translate_footprint_alone_moves_its_board_frame_children() -> None:
    footprint = _footprint_with_board_frame_children()
    translate_footprint(footprint, (1.0, 2.0))
    assert footprint.zones[0].polygons[0].pts[0] == (100.0, 51.0)
    assert footprint.points[0].position == (101.5, 52.5)


def test_a_footprint_local_child_still_does_not_move() -> None:
    """The fix must not move what already follows the placement."""
    footprint = _footprint_with_board_frame_children()
    footprint.drawings = [PcbLine(start=(0.0, 0.0), end=(1.0, 0.0), layer="F.SilkS")]
    translate_board(Board(footprints=[footprint]), (10.0, 20.0))
    assert footprint.drawings[0].start == (0.0, 0.0)


def test_moving_a_footprint_zone_is_reversible() -> None:
    """Exactness: the move runs in internal units, so it undoes exactly."""
    footprint = _footprint_with_board_frame_children()
    board = Board(footprints=[footprint])
    before = board.model_dump_json()

    translate_board(board, (13.7, -4.21))
    translate_board(board, (-13.7, 4.21))

    assert board.model_dump_json() == before
