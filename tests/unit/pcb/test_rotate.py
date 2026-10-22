"""Rotation of board content (iteration 14, work order P1-rotate).

Rotation is a point move PLUS an angle change, and the angle rule is per item
type. Getting the points right and the angles wrong produces a board that LOOKS
right in a text diff and plots wrongly, which is why the integration suite backs
these tests with a plot oracle and a control that must fail
(``tests/integration/test_mutation_fidelity.py``).

The rules and their KiCad 10.0.3 citations are in
``context/audit/footprint-transform-frame-2026-09-02.md``. In one line: a pad
angle and a footprint text angle are board-absolute and take the turn; a text-box
angle is footprint-local and does not; a footprint-local position never moves,
because the placement already carries the turn for it.

KiCad has TWO turn operations and this module covers both.
``rotate_footprint`` is ``FOOTPRINT::Rotate``: move, turn, then ``KeepUpright``.
``set_footprint_orientation`` is ``SetOrientation``: turn in place, no
``KeepUpright``. ``inject_footprint`` and ``extract_footprint`` use the second,
which is what makes them inverse.
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
    JustifyH,
    JustifyV,
    Pad,
    PadShape,
    PadType,
    PcbArc,
    PcbLine,
    PcbPoint,
    PcbRect,
    PcbTarget,
    PcbText,
    PcbTextBox,
    Property,
    ReferenceImage,
    RenderCache,
    RenderCachePolygon,
    Stroke,
    TextAttributes,
    Zone,
    ZoneOutlinePolygon,
    extract_footprint,
    inject_footprint,
    rotate_board,
    rotate_footprint,
    rotate_items,
)
from eedesigncore.common.transform import Rotation
from eedesigncore.pcb.models import TrackSegment, Via
from eedesigncore.pcb.transform import set_footprint_orientation

CENTER = (100.0, 100.0)


class TestRotationKernel:
    def test_a_quarter_turn_maps_x_to_y_as_kicad_does(self) -> None:
        # ``RotatePoint`` sends (x, y) to (y, -x) at 90 degrees
        # (libs/kimath/src/trigo.cpp:229-264).
        assert Rotation(90.0)((10.0, 0.0)) == (0.0, -10.0)
        assert Rotation(90.0)((0.0, 10.0)) == (10.0, 0.0)

    def test_a_turn_about_a_centre_leaves_the_centre_alone(self) -> None:
        assert Rotation(37.0, CENTER)(CENTER) == CENTER

    def test_a_full_circle_is_the_identity(self) -> None:
        assert Rotation(360.0).is_identity
        assert Rotation(0.0)((1.234567, -7.654321)) == (1.234567, -7.654321)

    def test_a_quarter_turn_is_exact(self) -> None:
        # No trigonometry runs on a cardinal angle, so no coordinate drifts.
        point = (123.456789, -98.765432)
        turned = Rotation(90.0, CENTER)(point)
        back = Rotation(-90.0, CENTER)(turned)

        assert back == point

    def test_only_a_multiple_of_ninety_counts_as_cardinal(self) -> None:
        assert Rotation(270.0).is_cardinal
        assert Rotation(-90.0).is_cardinal
        assert not Rotation(45.0).is_cardinal

    def test_an_absolute_angle_normalises_into_a_full_circle(self) -> None:
        assert Rotation(90.0).turn(350.0) == 80.0
        assert Rotation(-90.0).turn(0.0) == 270.0


class TestBoardItemAngles:
    def test_a_board_text_takes_the_turn(self) -> None:
        board = Board(drawings=[PcbText(content="A", position=(110.0, 100.0), angle=0.0)])

        rotate_board(board, 90.0, center=CENTER)

        text = board.drawings[0]
        assert isinstance(text, PcbText)
        assert text.position == (100.0, 90.0)
        assert text.angle == 90.0

    def test_a_board_text_box_takes_the_turn(self) -> None:
        board = Board(drawings=[PcbTextBox(content="A", start=(0.0, 0.0), end=(10.0, 5.0))])

        rotate_board(board, 90.0)

        box = board.drawings[0]
        assert isinstance(box, PcbTextBox)
        assert box.angle == 90.0

    def test_a_barcode_takes_the_turn_without_normalising(self) -> None:
        # ``PCB_BARCODE::Rotate`` adds and does not normalise (pcb_barcode.cpp:295).
        board = Board(drawings=[Barcode(position=(0.0, 0.0), angle=300.0, size=(2.0, 2.0))])

        rotate_board(board, 90.0)

        barcode = board.drawings[0]
        assert isinstance(barcode, Barcode)
        assert barcode.angle == 390.0

    def test_a_dimension_turns_its_points_and_its_text(self) -> None:
        board = Board(
            drawings=[
                Dimension(
                    pts=[(110.0, 100.0), (120.0, 100.0)],
                    text=DimensionText(content="10", position=(115.0, 100.0), angle=0.0),
                )
            ]
        )

        rotate_board(board, 90.0, center=CENTER)

        dimension = board.drawings[0]
        assert isinstance(dimension, Dimension)
        assert dimension.pts == [(100.0, 90.0), (100.0, 80.0)]
        assert dimension.text is not None
        assert dimension.text.angle == 90.0

    def test_geometry_with_no_angle_only_moves(self) -> None:
        board = Board(
            drawings=[PcbLine(start=(110.0, 100.0), end=(120.0, 100.0))],
            points=[PcbPoint(position=(110.0, 100.0))],
            zones=[Zone(polygons=[ZoneOutlinePolygon(pts=[(110.0, 100.0)])])],
            tracks=[
                TrackSegment(start=(110.0, 100.0), end=(120.0, 100.0), width=0.2, layer="F.Cu"),
                Via(at=(110.0, 100.0), size=0.6, drill=0.3),
            ],
            targets=[PcbTarget(position=(110.0, 100.0), size=1.0, layer="F.Cu")],
        )

        rotate_board(board, 90.0, center=CENTER)

        line = board.drawings[0]
        assert isinstance(line, PcbLine)
        assert (line.start, line.end) == ((100.0, 90.0), (100.0, 80.0))
        assert board.points[0].position == (100.0, 90.0)
        assert board.zones[0].polygons[0].pts == [(100.0, 90.0)]
        segment, via = board.tracks
        assert isinstance(segment, TrackSegment)
        assert isinstance(via, Via)
        assert segment.start == (100.0, 90.0)
        assert via.at == (100.0, 90.0)
        assert board.targets[0].position == (100.0, 90.0)

    def test_an_arc_turns_all_three_points(self) -> None:
        board = Board(
            drawings=[PcbArc(start=(110.0, 100.0), mid=(110.0, 110.0), end=(100.0, 110.0))]
        )

        rotate_board(board, 180.0, center=CENTER)

        arc = board.drawings[0]
        assert isinstance(arc, PcbArc)
        assert (arc.start, arc.mid, arc.end) == ((90.0, 100.0), (90.0, 90.0), (100.0, 90.0))


class TestRenderCacheIsDropped:
    def _board(self) -> Board:
        return Board(
            drawings=[
                PcbText(
                    content="A",
                    position=(110.0, 100.0),
                    render_cache=RenderCache(
                        resolved_text="A",
                        angle=0.0,
                        polygons=[RenderCachePolygon(pts=[(110.0, 100.0)])],
                    ),
                )
            ]
        )

    def test_a_turn_drops_the_cache(self) -> None:
        # KiCad clears the same cache on an angle change
        # (``EDA_TEXT::SetTextAngle``, common/eda_text.cpp:283-288) and rebuilds
        # it from the outline font before it writes. This library has no font.
        board = self._board()

        rotate_board(board, 90.0, center=CENTER)

        text = board.drawings[0]
        assert isinstance(text, PcbText)
        assert text.render_cache is None

    def test_a_board_with_no_cache_still_round_trips(self) -> None:
        board = self._board()

        rotate_board(board, 90.0, center=CENTER)
        emitted = board.to_string()

        assert "render_cache" not in emitted
        assert Board.from_string(emitted).to_string() == emitted


class TestTheRectangleLimit:
    def test_a_quarter_turn_keeps_a_rectangle_a_rectangle(self) -> None:
        board = Board(drawings=[PcbRect(start=(110.0, 100.0), end=(120.0, 110.0))])

        rotate_board(board, 90.0, center=CENTER)

        rectangle = board.drawings[0]
        assert isinstance(rectangle, PcbRect)
        assert (rectangle.start, rectangle.end) == ((100.0, 90.0), (110.0, 80.0))

    def test_any_other_angle_is_refused(self) -> None:
        # KiCad tessellates a non-cardinally rotated rectangle into a polygon
        # (eda_shape.cpp:957-972) and this library does not reproduce that.
        board = Board(drawings=[PcbRect(start=(0.0, 0.0), end=(10.0, 10.0))])

        with pytest.raises(ValueError, match="multiple of 90"):
            rotate_board(board, 45.0)

    def test_the_board_is_untouched_when_the_turn_is_refused(self) -> None:
        board = Board(
            drawings=[
                PcbLine(start=(0.0, 0.0), end=(10.0, 0.0)),
                PcbRect(start=(0.0, 0.0), end=(10.0, 10.0)),
            ]
        )
        before = board.to_string()

        with pytest.raises(ValueError):
            rotate_board(board, 45.0)

        assert board.to_string() == before

    def test_a_free_shape_takes_any_angle(self) -> None:
        board = Board(drawings=[PcbLine(start=(110.0, 100.0), end=(120.0, 100.0))])

        rotate_board(board, 45.0, center=CENTER)

        line = board.drawings[0]
        assert isinstance(line, PcbLine)
        assert line.start != (110.0, 100.0)


def _placed_part(angle: float = 0.0) -> Footprint:
    return Footprint(
        fp_id="lib:part",
        at=FootprintPlacement(position=CENTER, angle=angle),
        pads=[
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.rect,
                position=(-3.0, 0.0),
                angle=30.0,
                size=(2.0, 1.0),
                layers=["F.Cu"],
            )
        ],
        drawings=[
            PcbLine(start=(-4.0, -2.0), end=(4.0, -2.0), stroke=Stroke(width=0.12)),
            PcbText(content="U1", position=(0.0, -3.0), angle=0.0, unlocked=True),
            PcbTextBox(content="note", start=(0.0, 0.0), end=(2.0, 1.0), angle=15.0),
            ReferenceImage(position=(50.0, 50.0)),
        ],
        properties=[
            Property(
                name="Reference",
                value="U1",
                position=(0.0, -2.0),
                angle=0.0,
                unlocked=True,
                attributes=TextAttributes(),
            )
        ],
    )


class TestFootprintRotation:
    def test_the_placement_moves_and_turns(self) -> None:
        footprint = _placed_part()

        rotate_footprint(footprint, 90.0, center=(90.0, 100.0))

        assert footprint.at is not None
        assert footprint.at.position == (90.0, 90.0)
        assert footprint.at.angle == 90.0

    def test_the_placement_angle_normalises_to_a_half_circle(self) -> None:
        # ``SetOrientation`` calls ``Normalize180`` (footprint.cpp:3105).
        footprint = _placed_part(angle=180.0)

        rotate_footprint(footprint, 90.0)

        assert footprint.at is not None
        assert footprint.at.angle == -90.0

    def test_a_pad_takes_the_turn_and_keeps_its_position(self) -> None:
        footprint = _placed_part()

        rotate_footprint(footprint, 90.0)

        pad = footprint.pads[0]
        assert pad.position == (-3.0, 0.0)
        assert pad.angle == 120.0

    def test_a_footprint_text_takes_the_turn_and_keeps_its_position(self) -> None:
        footprint = _placed_part()

        rotate_footprint(footprint, 90.0)

        text = footprint.drawings[1]
        assert isinstance(text, PcbText)
        assert text.position == (0.0, -3.0)
        assert text.angle == 90.0

    def test_a_text_box_angle_is_footprint_local_and_stays(self) -> None:
        footprint = _placed_part()

        rotate_footprint(footprint, 90.0)

        box = footprint.drawings[2]
        assert isinstance(box, PcbTextBox)
        assert box.angle == 15.0
        assert (box.start, box.end) == ((0.0, 0.0), (2.0, 1.0))

    def test_a_board_frame_child_turns_as_a_board_item(self) -> None:
        footprint = _placed_part()

        rotate_footprint(footprint, 90.0, center=CENTER)

        image = footprint.drawings[3]
        assert isinstance(image, ReferenceImage)
        assert image.position == (50.0, 150.0)

    def test_a_library_footprint_has_no_orientation_to_turn(self) -> None:
        with pytest.raises(ValueError, match="board-context footprint"):
            rotate_footprint(Footprint(fp_id="part"), 90.0)


class TestKeepUpright:
    """``FOOTPRINT::Rotate`` applies ``KeepUpright`` to every field and text."""

    def _field(self, *, angle: float, unlocked: bool) -> Property:
        return Property(
            name="Reference",
            value="U1",
            position=(0.0, -2.0),
            angle=angle,
            unlocked=unlocked,
            attributes=TextAttributes(justify_h=JustifyH.left, justify_v=JustifyV.top),
        )

    def _footprint(self, field: Property) -> Footprint:
        return Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=CENTER, angle=0.0),
            properties=[field],
        )

    def test_a_field_past_a_half_circle_turns_a_further_half_circle(self) -> None:
        # 0 + 180 is 180, which is not upright, so KiCad turns it back to 0 and
        # swaps both justifications (pcb_text.cpp:374-393).
        footprint = self._footprint(self._field(angle=0.0, unlocked=False))

        rotate_footprint(footprint, 180.0)

        field = footprint.properties[0]
        assert field.angle == 0.0
        assert field.attributes is not None
        assert field.attributes.justify_h is JustifyH.right
        assert field.attributes.justify_v is JustifyV.bottom

    def test_a_field_short_of_a_half_circle_is_left_alone(self) -> None:
        footprint = self._footprint(self._field(angle=0.0, unlocked=False))

        rotate_footprint(footprint, 90.0)

        field = footprint.properties[0]
        assert field.angle == 90.0
        assert field.attributes is not None
        assert field.attributes.justify_h is JustifyH.left

    def test_an_unlocked_field_is_never_turned_upright(self) -> None:
        # ``unlocked yes`` is the stored inverse of keep-upright
        # (pcb_io_kicad_sexpr_parser.cpp:3890-3892).
        footprint = self._footprint(self._field(angle=0.0, unlocked=True))

        rotate_footprint(footprint, 180.0)

        field = footprint.properties[0]
        assert field.angle == 180.0
        assert field.attributes is not None
        assert field.attributes.justify_h is JustifyH.left


class TestSetOrientation:
    """The second turn operation: no move, no ``KeepUpright``."""

    def test_it_turns_the_children_without_moving_the_placement(self) -> None:
        footprint = _placed_part()

        set_footprint_orientation(footprint, 90.0)

        assert footprint.at is not None
        assert footprint.at.position == CENTER
        assert footprint.at.angle == 90.0
        assert footprint.pads[0].angle == 120.0

    def test_it_does_not_turn_a_field_upright(self) -> None:
        footprint = Footprint(
            fp_id="lib:part",
            at=FootprintPlacement(position=CENTER, angle=0.0),
            properties=[
                Property(
                    name="Reference",
                    value="U1",
                    angle=0.0,
                    unlocked=False,
                    attributes=TextAttributes(justify_h=JustifyH.left),
                )
            ],
        )

        set_footprint_orientation(footprint, 180.0)

        field = footprint.properties[0]
        assert field.angle == 180.0
        assert field.attributes is not None
        assert field.attributes.justify_h is JustifyH.left

    def test_it_measures_the_change_from_the_current_orientation(self) -> None:
        footprint = _placed_part(angle=90.0)

        set_footprint_orientation(footprint, 90.0)

        assert footprint.pads[0].angle == 30.0  # unchanged: the delta is zero


class TestExtractAndInjectAreInverse:
    """A placement orientation must survive a trip out to a library and back."""

    def _board_with(self, angle: float) -> Board:
        return Board(footprints=[_placed_part(angle=angle)])

    @pytest.mark.parametrize("angle", [0.0, 90.0, 180.0, -90.0])
    def test_the_round_trip_restores_every_child_angle(self, angle: float) -> None:
        board = self._board_with(angle)
        original = board.footprints[0].model_copy(deep=True)

        library = extract_footprint(board, reference="U1")
        target = Board()
        placed = inject_footprint(target, library, at=CENTER, rotation=angle)

        assert placed.at is not None
        assert placed.at.angle == angle
        assert [pad.angle for pad in placed.pads] == [pad.angle for pad in original.pads]
        assert [item.angle for item in placed.drawings if isinstance(item, PcbText)] == [
            item.angle for item in original.drawings if isinstance(item, PcbText)
        ]
        assert [field.angle for field in placed.properties] == [
            field.angle for field in original.properties
        ]

    def test_extraction_de_rotates_the_children(self) -> None:
        """A part placed at 90 comes out of extraction as the library part again.

        KiCad's own FootprintSave calls SetOrientation( ANGLE_0 ) before it
        writes (pcb_io_kicad_sexpr.cpp:3446-3447), so the library file carries
        library-relative angles and not the board placement.
        """
        board = Board()
        library = _placed_part()
        library.at = None
        inject_footprint(board, library, at=CENTER, rotation=90.0)
        assert board.footprints[0].pads[0].angle == 120.0

        extracted = extract_footprint(board, reference="U1")

        assert extracted.at is None
        assert extracted.pads[0].angle == 30.0

    def test_injection_turns_the_children(self) -> None:
        library = extract_footprint(self._board_with(0.0), reference="U1")
        target = Board()

        placed = inject_footprint(target, library, at=CENTER, rotation=90.0)

        assert placed.pads[0].angle == 120.0


class TestItemsCollection:
    def test_it_turns_every_board_frame_item_kind(self) -> None:
        line = PcbLine(start=(110.0, 100.0), end=(120.0, 100.0))
        footprint = _placed_part()
        target = PcbTarget(position=(110.0, 100.0), size=1.0, layer="F.Cu")

        rotate_items([line, footprint, target], 90.0, center=CENTER)

        assert line.start == (100.0, 90.0)
        assert footprint.at is not None
        assert footprint.at.angle == 90.0
        assert target.position == (100.0, 90.0)

    def test_an_unknown_item_is_an_error(self) -> None:
        with pytest.raises(TypeError, match="does not cover"):
            rotate_items([object()], 90.0)


class TestEmittedBytes:
    def _board(self) -> Board:
        return Board(
            footprints=[_placed_part()],
            drawings=[
                PcbLine(start=(110.0, 100.0), end=(120.0, 100.0)),
                PcbArc(start=(110.0, 100.0), mid=(110.0, 110.0), end=(100.0, 110.0)),
            ],
            tracks=[
                TrackSegment(start=(110.0, 100.0), end=(120.0, 100.0), width=0.2, layer="F.Cu")
            ],
        )

    def test_a_zero_turn_changes_no_byte(self) -> None:
        board = self._board()
        before = board.to_string()

        rotate_board(board, 0.0, center=CENTER)

        assert board.to_string() == before

    def test_a_turn_and_its_inverse_restore_the_bytes(self) -> None:
        board = self._board()
        before = board.to_string()

        rotate_board(board, 90.0, center=CENTER)
        assert board.to_string() != before
        rotate_board(board, -90.0, center=CENTER)

        assert board.to_string() == before

    def test_the_same_turn_twice_produces_the_same_bytes(self) -> None:
        first, second = self._board(), self._board()

        rotate_board(first, 90.0, center=CENTER)
        rotate_board(second, 90.0, center=CENTER)

        assert first.to_string() == second.to_string()

    def test_a_turned_board_still_round_trips(self) -> None:
        board = self._board()

        rotate_board(board, 90.0, center=CENTER)
        text = board.to_string()

        assert Board.from_string(text).to_string() == text


# ---------------------------------------------------------------------------
# Board-frame children of a footprint (defect report 2026-09-05, item 1)
# ---------------------------------------------------------------------------


def _placed_with_zone_and_point() -> Footprint:
    """A placed footprint carrying a zone and a point, both in board coordinates."""
    footprint = Footprint(
        fp_id="PanelTab:Tab",
        at=FootprintPlacement(position=(10.0, 0.0), angle=0.0),
    )
    footprint.zones = [Zone(layers=["F.Cu"], polygons=[ZoneOutlinePolygon(pts=[(11.0, 0.0)])])]
    footprint.points = [PcbPoint(position=(12.0, 0.0), size=1.0, layer="F.Cu")]
    return footprint


def test_rotate_board_turns_a_footprint_hosted_zone() -> None:
    """A quarter turn about the origin sends (x, y) to (y, -x)."""
    footprint = _placed_with_zone_and_point()
    rotate_board(Board(footprints=[footprint]), 90.0)

    assert footprint.at is not None
    assert footprint.at.position == (0.0, -10.0)
    assert footprint.zones[0].polygons[0].pts[0] == (0.0, -11.0)


def test_rotate_board_turns_a_footprint_hosted_point() -> None:
    footprint = _placed_with_zone_and_point()
    rotate_board(Board(footprints=[footprint]), 90.0)
    assert footprint.points[0].position == (0.0, -12.0)


def test_rotate_footprint_turns_its_board_frame_children() -> None:
    footprint = _placed_with_zone_and_point()
    rotate_footprint(footprint, 90.0)
    assert footprint.zones[0].polygons[0].pts[0] == (0.0, -11.0)
    assert footprint.points[0].position == (0.0, -12.0)


def test_set_orientation_turns_board_frame_children_about_the_placement() -> None:
    """``SetOrientation`` turns children about the UNMOVED placement.

    Source: ``pcbnew/footprint.cpp:3108``. The placement stays at (10, 0), so the
    zone corner at (11, 0) swings to (10, -1) rather than about the board origin.
    """
    footprint = _placed_with_zone_and_point()
    set_footprint_orientation(footprint, 90.0)

    assert footprint.at is not None
    assert footprint.at.position == (10.0, 0.0)
    assert footprint.zones[0].polygons[0].pts[0] == (10.0, -1.0)
    assert footprint.points[0].position == (10.0, -2.0)


def test_turning_a_footprint_zone_is_reversible() -> None:
    footprint = _placed_with_zone_and_point()
    board = Board(footprints=[footprint])
    before = board.model_dump_json()

    rotate_board(board, 90.0)
    rotate_board(board, -90.0)

    assert board.model_dump_json() == before
