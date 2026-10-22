import pytest

from eedesigncore import Board, flip_board
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.pcb_family.shapes import PcbArc, PcbText
from eedesigncore.pcb.models import TrackSegment


def test_flip_root_arc_and_side_specific_text():
    board = Board(
        drawings=[
            PcbArc(start=(1, 0), mid=(0, 1), end=(-1, 0), layer="Edge.Cuts"),
            PcbText(position=(2, 3), angle=30, attributes=TextAttributes()),
            PcbText(position=(2, 3), angle=30, layer="User.40", attributes=TextAttributes()),
        ]
    )
    before = board.model_copy(deep=True)
    flip_board(board, center=(0, 0))
    assert board.drawings[0].start == (1, 0)
    assert board.drawings[0].mid == (0, 1)
    assert board.drawings[0].end == (-1, 0)
    assert board.drawings[1].layer == "B.SilkS"
    assert board.drawings[1].attributes.mirror
    assert not board.drawings[2].attributes.mirror
    assert board.drawings[1].angle == 330
    flip_board(board, center=(0, 0))
    assert board == before


def test_unsupported_content_leaves_board_unchanged():
    board = Board(tracks=[TrackSegment()], drawings=[PcbText()])
    before = board.model_copy(deep=True)
    with pytest.raises(ValueError, match="tracks"):
        flip_board(board, center=(0, 0))
    assert board == before


def test_invalid_center_leaves_board_unchanged():
    board = Board(drawings=[PcbText()])
    before = board.model_copy(deep=True)
    with pytest.raises(ValueError, match="finite"):
        flip_board(board, center=(float("nan"), 0))
    assert board == before
