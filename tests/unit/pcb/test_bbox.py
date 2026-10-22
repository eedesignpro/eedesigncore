"""Bounding boxes of board content (iteration 14, work order P2).

The box is a STATED rule, not a guess at what KiCad would compute. KiCad's own
box needs glyph metrics from a font this library does not load, so a text with
no cached outlines contributes its anchor point and nothing more. The rule, and
the reason behind each inclusion and exclusion, is in the docstring of
``common/pcb_family/bbox.py``. These tests hold the rule to it.

The two properties a caller depends on: an arc is measured by its true extent,
not by its stored points, and a translated board yields a box translated by the
same amount.
"""

from __future__ import annotations

import pytest

from eedesigncore import (
    Board,
    BoundingBox,
    Footprint,
    FootprintPlacement,
    Pad,
    PadShape,
    PadType,
    PcbArc,
    PcbCircle,
    PcbLine,
    PcbPoint,
    PcbText,
    PcbTextBox,
    PrimRect,
    RenderCache,
    RenderCachePolygon,
    Stroke,
    Zone,
    ZoneOutlinePolygon,
    board_bbox,
    footprint_bbox,
    items_bbox,
    translate_board,
)
from eedesigncore.pcb.models import TrackSegment, Via


def _box(*, min_x: float, min_y: float, max_x: float, max_y: float) -> BoundingBox:
    return BoundingBox(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


class TestBoundingBox:
    def test_it_reports_width_height_centre_and_size(self) -> None:
        box = _box(min_x=1.0, min_y=2.0, max_x=5.0, max_y=10.0)

        assert box.width == 4.0
        assert box.height == 8.0
        assert box.center == (3.0, 6.0)
        assert box.size == (4.0, 8.0)

    def test_two_boxes_merge_into_the_box_that_holds_both(self) -> None:
        first = _box(min_x=0.0, min_y=0.0, max_x=1.0, max_y=1.0)
        second = _box(min_x=-1.0, min_y=0.5, max_x=0.5, max_y=3.0)

        assert first.merged(second) == _box(min_x=-1.0, min_y=0.0, max_x=1.0, max_y=3.0)


class TestEmptySelection:
    def test_an_empty_board_has_no_box(self) -> None:
        assert board_bbox(Board()) is None

    def test_a_layer_filter_that_matches_nothing_has_no_box(self) -> None:
        board = Board(drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS")])

        assert board_bbox(board, layers={"Edge.Cuts"}) is None


class TestStrokeWidth:
    def _line(self) -> PcbLine:
        return PcbLine(start=(0.0, 0.0), end=(10.0, 0.0), stroke=Stroke(width=0.2))

    def test_half_the_stroke_width_grows_the_box(self) -> None:
        box = items_bbox([self._line()])

        assert box == _box(min_x=-0.1, min_y=-0.1, max_x=10.1, max_y=0.1)

    def test_the_caller_can_ask_for_the_bare_geometry(self) -> None:
        box = items_bbox([self._line()], include_width=False)

        assert box == _box(min_x=0.0, min_y=0.0, max_x=10.0, max_y=0.0)

    def test_a_zero_width_adds_nothing(self) -> None:
        # A zero stroke width means "the board default", which an item alone
        # cannot resolve, so it contributes no growth.
        line = PcbLine(start=(0.0, 0.0), end=(10.0, 0.0), stroke=Stroke(width=0.0))

        assert items_bbox([line]) == _box(min_x=0.0, min_y=0.0, max_x=10.0, max_y=0.0)


class TestArcExtent:
    def test_an_arc_reaches_past_its_stored_points(self) -> None:
        # A half circle from (10,0) through (0,10) to (-10,0), centred on the
        # origin. The stored points give max_y 10, and so does the true extent;
        # the crossing that matters is the top of the arc, which IS the mid here.
        arc = PcbArc(start=(10.0, 0.0), mid=(0.0, 10.0), end=(-10.0, 0.0))

        box = items_bbox([arc], include_width=False)

        assert box == _box(min_x=-10.0, min_y=0.0, max_x=10.0, max_y=10.0)

    def test_a_quarter_arc_that_crosses_an_axis_reaches_the_radius(self) -> None:
        # From 45 degrees round to 135 degrees. No stored point sits at 90
        # degrees, so only the axis crossing gives the true top of the box.
        half = 10.0 * (0.5**0.5)
        arc = PcbArc(start=(half, half), mid=(0.0, 10.0), end=(-half, half))

        box = items_bbox([arc], include_width=False)

        assert box is not None
        assert box.max_y == pytest.approx(10.0, abs=1e-6)
        assert box.min_y == pytest.approx(half, abs=1e-6)

    def test_a_circle_is_measured_from_its_radius(self) -> None:
        circle = PcbCircle(center=(5.0, 5.0), end=(8.0, 5.0))

        box = items_bbox([circle], include_width=False)

        assert box == _box(min_x=2.0, min_y=2.0, max_x=8.0, max_y=8.0)


class TestTextRule:
    def test_a_text_with_no_cache_contributes_its_anchor_only(self) -> None:
        text = PcbText(content="A very long string", position=(5.0, 5.0))

        assert items_bbox([text]) == _box(min_x=5.0, min_y=5.0, max_x=5.0, max_y=5.0)

    def test_a_text_with_a_cache_contributes_the_cached_outlines(self) -> None:
        text = PcbText(
            content="A",
            position=(5.0, 5.0),
            render_cache=RenderCache(
                resolved_text="A",
                angle=0.0,
                polygons=[RenderCachePolygon(pts=[(4.0, 4.0), (6.0, 7.0)])],
            ),
        )

        assert items_bbox([text]) == _box(min_x=4.0, min_y=4.0, max_x=6.0, max_y=7.0)

    def test_a_text_box_uses_its_rectangle(self) -> None:
        box_item = PcbTextBox(content="A", start=(0.0, 0.0), end=(10.0, 5.0))

        assert items_bbox([box_item]) == _box(min_x=0.0, min_y=0.0, max_x=10.0, max_y=5.0)


class TestTracks:
    def test_a_segment_grows_by_half_its_width(self) -> None:
        track = TrackSegment(start=(0.0, 0.0), end=(10.0, 0.0), width=0.5, layer="F.Cu")

        assert items_bbox([track]) == _box(min_x=-0.25, min_y=-0.25, max_x=10.25, max_y=0.25)

    def test_a_via_is_measured_from_its_diameter(self) -> None:
        via = Via(at=(5.0, 5.0), size=0.8, drill=0.4)

        assert items_bbox([via]) == _box(min_x=4.6, min_y=4.6, max_x=5.4, max_y=5.4)


class TestLayerFilter:
    def _board(self) -> Board:
        return Board(
            drawings=[
                PcbLine(start=(0.0, 0.0), end=(50.0, 40.0), layer="Edge.Cuts"),
                PcbLine(start=(-100.0, -100.0), end=(100.0, 100.0), layer="F.SilkS"),
            ]
        )

    def test_the_outline_is_reachable_through_the_filter(self) -> None:
        box = board_bbox(self._board(), layers={"Edge.Cuts"}, include_width=False)

        assert box == _box(min_x=0.0, min_y=0.0, max_x=50.0, max_y=40.0)

    def test_no_filter_measures_everything(self) -> None:
        box = board_bbox(self._board(), include_width=False)

        assert box == _box(min_x=-100.0, min_y=-100.0, max_x=100.0, max_y=100.0)


class TestFootprintFrame:
    def _footprint(self, angle: float) -> Footprint:
        return Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(100.0, 50.0), angle=angle),
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(-2.0, 0.0),
                    size=(2.0, 1.0),
                    layers=["F.Cu"],
                ),
            ],
        )

    def test_a_child_is_measured_in_board_coordinates(self) -> None:
        box = footprint_bbox(self._footprint(0.0))

        assert box == _box(min_x=97.0, min_y=49.5, max_x=99.0, max_y=50.5)

    def test_the_placement_angle_turns_the_children(self) -> None:
        # KiCad ``RotatePoint`` maps a local (x, y) to (y, -x) at 90 degrees,
        # so the local pad centre (-2, 0) lands 2 mm below the placement.
        box = footprint_bbox(self._footprint(90.0))

        assert box is not None
        assert box.center == pytest.approx((100.0, 52.0), abs=1e-9)
        assert box.size == pytest.approx((1.0, 2.0), abs=1e-9)

    def test_a_custom_pad_primitive_is_inside_the_box(self) -> None:
        footprint = self._footprint(0.0)
        footprint.pads[0].shape = PadShape.custom
        footprint.pads[0].primitives = [PrimRect(start=(-2.0, -5.0), end=(2.0, 5.0), width=0.0)]

        box = footprint_bbox(footprint)

        # The primitive is stored relative to the pad anchor at (-2, 0), so it
        # spans -4..0 by -5..5 in the footprint frame and reaches past the
        # 2 x 1 pad envelope.
        assert box == _box(min_x=96.0, min_y=45.0, max_x=100.0, max_y=55.0)


class TestBoardBox:
    def _board(self) -> Board:
        return Board(
            footprints=[
                Footprint(
                    fp_id="lib:part",
                    at=FootprintPlacement(position=(10.0, 10.0)),
                    drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0))],
                )
            ],
            drawings=[PcbLine(start=(0.0, 0.0), end=(50.0, 40.0), layer="Edge.Cuts")],
            zones=[Zone(polygons=[ZoneOutlinePolygon(pts=[(-5.0, -5.0), (60.0, 45.0)])])],
        )

    def test_the_board_box_holds_every_stream(self) -> None:
        box = board_bbox(self._board(), include_width=False)

        assert box == _box(min_x=-5.0, min_y=-5.0, max_x=60.0, max_y=45.0)

    def test_a_translated_board_yields_a_translated_box(self) -> None:
        board = self._board()
        before = board_bbox(board, include_width=False)

        translate_board(board, (12.7, -3.81))
        after = board_bbox(board, include_width=False)

        assert before is not None
        assert after is not None
        assert (after.min_x - before.min_x, after.min_y - before.min_y) == pytest.approx(
            (12.7, -3.81), abs=1e-9
        )
        assert after.size == pytest.approx(before.size, abs=1e-9)

    def test_an_unknown_item_type_is_an_error(self) -> None:
        with pytest.raises(TypeError, match="does not cover"):
            items_bbox([object()])


class TestRemainingItemTypes:
    """The extent branches the board fixture does not reach."""

    def test_a_curve_is_bounded_by_its_control_points(self) -> None:
        from eedesigncore import PcbCurve

        item = PcbCurve(pts=[(0.0, 0.0), (1.0, 5.0), (2.0, 5.0), (3.0, 0.0)])

        # The control polygon holds the curve, so the box is correct but loose.
        assert items_bbox([item]) == _box(min_x=0.0, min_y=0.0, max_x=3.0, max_y=5.0)

    def test_an_arc_inside_a_pts_chain_contributes_its_true_extent(self) -> None:
        from eedesigncore import PcbPoly, PtsArc

        item = PcbPoly(
            pts=[(10.0, 0.0), PtsArc(start=(10.0, 0.0), mid=(0.0, 10.0), end=(-10.0, 0.0))]
        )

        box = items_bbox([item])

        assert box == _box(min_x=-10.0, min_y=0.0, max_x=10.0, max_y=10.0)

    def test_a_table_is_bounded_by_its_cells(self) -> None:
        from eedesigncore import Table, TableCell

        item = Table(
            column_count=1,
            cells=[
                TableCell(content="A", start=(0.0, 0.0), end=(2.0, 1.0)),
                TableCell(content="B", start=(0.0, 1.0), end=(2.0, 2.0)),
            ],
        )

        assert items_bbox([item]) == _box(min_x=0.0, min_y=0.0, max_x=2.0, max_y=2.0)

    def test_a_barcode_is_bounded_by_its_turned_size_rectangle(self) -> None:
        from eedesigncore import Barcode

        item = Barcode(position=(10.0, 10.0), angle=0.0, size=(4.0, 2.0))

        assert items_bbox([item]) == _box(min_x=8.0, min_y=9.0, max_x=12.0, max_y=11.0)

    def test_a_reference_image_contributes_its_anchor_only(self) -> None:
        from eedesigncore import ReferenceImage

        item = ReferenceImage(position=(3.0, 4.0))

        assert items_bbox([item]) == _box(min_x=3.0, min_y=4.0, max_x=3.0, max_y=4.0)

    def test_a_dimension_is_bounded_by_its_points_and_its_text(self) -> None:
        from eedesigncore import Dimension, DimensionText

        item = Dimension(
            pts=[(0.0, 0.0), (10.0, 0.0)],
            text=DimensionText(content="10", position=(5.0, -3.0)),
        )

        assert items_bbox([item]) == _box(min_x=0.0, min_y=-3.0, max_x=10.0, max_y=0.0)

    def test_a_group_contributes_nothing(self) -> None:
        from eedesigncore import PcbGroup

        assert items_bbox([PcbGroup(name="g", members=["u1"])]) is None

    def test_a_track_arc_is_measured_by_its_true_extent(self) -> None:
        from eedesigncore.pcb.models import TrackArc

        arc = TrackArc(
            start=(10.0, 0.0), mid=(0.0, 10.0), end=(-10.0, 0.0), width=0.0, layer="F.Cu"
        )

        assert items_bbox([arc]) == _box(min_x=-10.0, min_y=0.0, max_x=10.0, max_y=10.0)

    def test_a_target_is_measured_from_its_size(self) -> None:
        from eedesigncore import PcbTarget

        assert items_bbox([PcbTarget(position=(5.0, 5.0), size=2.0, layer="F.Cu")]) == _box(
            min_x=4.0, min_y=4.0, max_x=6.0, max_y=6.0
        )

    def test_a_generator_restates_geometry_and_is_skipped(self) -> None:
        from eedesigncore import GenPoint, PcbGenerator

        generator = PcbGenerator(properties={"origin": GenPoint(value=(500.0, 500.0))})

        assert items_bbox([generator]) is None

    def test_a_library_footprint_is_measured_in_its_own_frame(self) -> None:
        footprint = Footprint(fp_id="part", drawings=[PcbLine(start=(0.0, 0.0), end=(2.0, 1.0))])

        assert footprint_bbox(footprint, include_width=False) == _box(
            min_x=0.0, min_y=0.0, max_x=2.0, max_y=1.0
        )

    def test_a_board_frame_child_of_a_footprint_is_not_mapped_twice(self) -> None:
        from eedesigncore import ReferenceImage

        footprint = Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(100.0, 100.0), angle=90.0),
            drawings=[ReferenceImage(position=(20.0, 30.0))],
        )

        # The image already holds board coordinates, so the placement does not
        # apply to it a second time.
        assert footprint_bbox(footprint) == _box(min_x=20.0, min_y=30.0, max_x=20.0, max_y=30.0)


class TestFootprintBoardCoordinateBounds:
    @pytest.mark.parametrize("angle", [None, 0.0, 90.0, 180.0])
    @pytest.mark.parametrize("kind", ["point", "zone"])
    def test_points_and_zones_keep_their_stored_coordinates(
        self, angle: float | None, kind: str
    ) -> None:
        # KiCad writes points and zones without a parent-footprint coordinate conversion.
        footprint = Footprint(
            fp_id="PanelTab:test",
            at=None if angle is None else FootprintPlacement(position=(200.0, 123.0), angle=angle),
        )
        if kind == "point":
            footprint.points = [PcbPoint(position=(199.0, 123.0), layer="Edge.Cuts")]
            expected = _box(min_x=199.0, min_y=123.0, max_x=199.0, max_y=123.0)
        else:
            footprint.zones = [
                Zone(
                    layers=["Edge.Cuts"],
                    polygons=[ZoneOutlinePolygon(pts=[(199.0, 122.0), (201.0, 124.0)])],
                )
            ]
            expected = _box(min_x=199.0, min_y=122.0, max_x=201.0, max_y=124.0)

        assert footprint_bbox(footprint, layers={"Edge.Cuts"}) == expected
        assert board_bbox(Board(footprints=[footprint]), layers={"Edge.Cuts"}) == expected
        assert items_bbox([footprint], layers={"Edge.Cuts"}) == expected
        assert footprint_bbox(footprint, layers={"F.SilkS"}) is None

    def test_local_graphics_still_follow_placement_beside_a_board_coordinate_point(self) -> None:
        footprint = Footprint(
            fp_id="PanelTab:test",
            at=FootprintPlacement(position=(200.0, 123.0), angle=180.0),
            points=[PcbPoint(position=(199.0, 123.0), layer="Edge.Cuts")],
            drawings=[PcbLine(start=(0.0, 0.0), end=(2.0, 1.0), layer="Edge.Cuts")],
        )
        expected = _box(min_x=198.0, min_y=122.0, max_x=200.0, max_y=123.0)

        assert footprint_bbox(footprint, layers={"Edge.Cuts"}, include_width=False) == expected
        assert (
            board_bbox(Board(footprints=[footprint]), layers={"Edge.Cuts"}, include_width=False)
            == expected
        )
        assert items_bbox([footprint], layers={"Edge.Cuts"}, include_width=False) == expected


class TestCustomPadPrimitives:
    """A custom pad can put copper outside its anchor envelope, so the
    primitives are measured too."""

    def _pad_with(self, primitive: object) -> Footprint:
        return Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(0.0, 0.0)),
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.custom,
                    position=(0.0, 0.0),
                    size=(1.0, 1.0),
                    layers=["F.Cu"],
                    primitives=[primitive],  # type: ignore[list-item]
                )
            ],
        )

    def test_an_arc_primitive_uses_its_true_extent(self) -> None:
        from eedesigncore import PrimArc

        box = footprint_bbox(
            self._pad_with(
                PrimArc(start=(10.0, 0.0), mid=(0.0, 10.0), end=(-10.0, 0.0), width=0.0)
            ),
            include_width=False,
        )

        assert box == _box(min_x=-10.0, min_y=-0.5, max_x=10.0, max_y=10.0)

    def test_a_circle_primitive_uses_its_radius(self) -> None:
        from eedesigncore import PrimCircle

        box = footprint_bbox(
            self._pad_with(PrimCircle(center=(0.0, 0.0), end=(4.0, 0.0), width=0.0)),
            include_width=False,
        )

        assert box == _box(min_x=-4.0, min_y=-4.0, max_x=4.0, max_y=4.0)

    def test_a_polygon_primitive_uses_its_points(self) -> None:
        from eedesigncore import PrimPoly

        box = footprint_bbox(
            self._pad_with(PrimPoly(pts=[(0.0, 0.0), (3.0, 0.0), (3.0, 2.0)], width=0.0)),
            include_width=False,
        )

        assert box == _box(min_x=-0.5, min_y=-0.5, max_x=3.0, max_y=2.0)


class TestLayerFilterOnPadsAndTracks:
    def test_a_pad_off_the_filtered_layers_is_skipped(self) -> None:
        footprint = Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=(0.0, 0.0)),
            pads=[
                Pad(
                    number="1",
                    pad_type=PadType.smd,
                    shape=PadShape.rect,
                    position=(0.0, 0.0),
                    size=(1.0, 1.0),
                    layers=["B.Cu"],
                )
            ],
        )

        assert footprint_bbox(footprint, layers={"F.Cu"}) is None

    def test_a_track_off_the_filtered_layers_is_skipped(self) -> None:
        track = TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), width=0.2, layer="B.Cu")

        assert items_bbox([track], layers={"F.Cu"}) is None
