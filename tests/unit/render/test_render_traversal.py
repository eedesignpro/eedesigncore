"""Which items exist, where they sit, and what layer they name.

No test here asserts a color or an opacity. Selection and placement are separate
concerns from presentation (ADR-098).
"""

from __future__ import annotations

from eedesigncore.common.pcb_family.shapes import PcbLine, PcbPoint, PcbRect
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import Board
from eedesigncore.render.traversal import (
    footprint_point_map,
    identity_map,
    iter_render_items,
)


def placed(angle: float = 0.0, position: tuple[float, float] = (0.0, 0.0)) -> Footprint:
    """Return a board-context footprint at *position*, turned by *angle*."""
    return Footprint(fp_id="Test:FP", at=FootprintPlacement(position=position, angle=angle))


# ---------------------------------------------------------------------------
# The footprint placement transform
# ---------------------------------------------------------------------------


def test_a_library_footprint_maps_to_itself() -> None:
    """No placement means library context, so a child keeps its own frame."""
    assert footprint_point_map(Footprint(fp_id="Test:FP")) is identity_map


def test_an_unturned_placement_only_moves() -> None:
    to_board = footprint_point_map(placed(position=(10.0, 20.0)))
    assert to_board((1.0, 2.0)) == (11.0, 22.0)


def test_a_quarter_turn_is_exact() -> None:
    """KiCad takes an exact branch at each quadrant angle, so no rounding creeps in."""
    to_board = footprint_point_map(placed(angle=90.0))
    assert to_board((1.0, 0.0)) == (0.0, -1.0)


def test_a_turn_happens_before_the_move() -> None:
    to_board = footprint_point_map(placed(angle=90.0, position=(100.0, 50.0)))
    assert to_board((1.0, 0.0)) == (100.0, 49.0)


def test_a_half_turn_is_exact() -> None:
    to_board = footprint_point_map(placed(angle=180.0))
    assert to_board((3.0, 4.0)) == (-3.0, -4.0)


# ---------------------------------------------------------------------------
# Host walking
# ---------------------------------------------------------------------------


def test_a_board_shape_carries_the_identity_map() -> None:
    board = Board(drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS")])
    (found,) = list(iter_render_items(board, "F.SilkS"))
    assert found.to_board is identity_map
    assert found.footprint is None
    assert found.in_footprint is False


def test_a_footprint_shape_carries_the_placement_map() -> None:
    footprint = placed(angle=90.0, position=(10.0, 10.0))
    footprint.drawings = [PcbLine(start=(1.0, 0.0), end=(2.0, 0.0), layer="F.SilkS")]
    board = Board(footprints=[footprint])

    (found,) = list(iter_render_items(board, "F.SilkS"))
    assert found.in_footprint is True
    assert found.to_board((1.0, 0.0)) == (10.0, 9.0)


def test_a_point_inside_a_footprint_keeps_board_coordinates() -> None:
    """The point item stores board coordinates in both hosts. Never map it."""
    footprint = placed(angle=90.0, position=(10.0, 10.0))
    footprint.points = [PcbPoint(position=(1.0, 2.0), size=1.0, layer="F.Cu")]
    board = Board(footprints=[footprint])

    (found,) = list(iter_render_items(board, "F.Cu"))
    assert found.in_footprint is True
    assert found.to_board is identity_map


def test_board_items_come_before_footprint_items() -> None:
    """Stored order is the whole rule. It is stable, which determinism needs."""
    footprint = placed()
    footprint.drawings = [PcbLine(start=(9.0, 9.0), end=(9.0, 9.0), layer="F.SilkS")]
    board = Board(
        drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS")],
        footprints=[footprint],
    )

    found = list(iter_render_items(board, "F.SilkS"))
    assert [item.in_footprint for item in found] == [False, True]


def test_trailing_drawings_are_not_lost() -> None:
    board = Board(trailing_drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS")])
    assert len(list(iter_render_items(board, "F.SilkS"))) == 1


# ---------------------------------------------------------------------------
# Layer selection
# ---------------------------------------------------------------------------


def test_a_shape_on_another_layer_does_not_appear() -> None:
    board = Board(drawings=[PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="B.SilkS")])
    assert list(iter_render_items(board, "F.SilkS")) == []


def test_a_multi_layer_shape_answers_to_every_name_it_holds() -> None:
    """A multi-layer shape writes ``(layers …)``, and then ``(layer …)`` means nothing."""
    shape = PcbRect(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS", layers=["F.Cu", "B.Cu"])
    board = Board(drawings=[shape])

    assert len(list(iter_render_items(board, "F.Cu"))) == 1
    assert len(list(iter_render_items(board, "B.Cu"))) == 1
    assert list(iter_render_items(board, "F.SilkS")) == []


def test_the_render_item_reports_every_layer_it_holds() -> None:
    shape = PcbRect(start=(0.0, 0.0), end=(1.0, 1.0), layers=["F.Cu", "B.Cu"])
    board = Board(drawings=[shape])

    (found,) = list(iter_render_items(board, "F.Cu"))
    assert found.layers == ("F.Cu", "B.Cu")
    assert found.layer == "F.Cu"


def test_the_render_item_carries_the_item_uuid() -> None:
    shape = PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS", id="abc-123")
    (found,) = list(iter_render_items(Board(drawings=[shape]), "F.SilkS"))
    assert found.uuid == "abc-123"


def test_an_item_with_no_uuid_reports_none() -> None:
    shape = PcbLine(start=(0.0, 0.0), end=(1.0, 1.0), layer="F.SilkS")
    (found,) = list(iter_render_items(Board(drawings=[shape]), "F.SilkS"))
    assert found.uuid is None
