"""T1 — pcb package scaffold + Board root model smoke tests."""

from __future__ import annotations

import pytest

from eedesigncore import Board, dump_board, load_pcb, write_board
from eedesigncore.exceptions import KicadParseError
from eedesigncore.pcb.models import BOARD_FILE_VERSION
from eedesigncore.sexpr.parser import parse

HEADER = (
    "(kicad_pcb\n"
    "\t(version 20260206)\n"
    '\t(generator "pcbnew")\n'
    '\t(generator_version "10.0")\n'
    "\t(embedded_fonts no)\n"
    ")\n"
)


class TestBoardModel:
    def test_defaults(self) -> None:
        board = Board()
        assert board.version == BOARD_FILE_VERSION
        assert board.generator == "pcbnew"
        assert board.generator_version is None
        # Child collections default empty / None.
        assert board.footprints == []
        assert board.tracks == []
        assert board.nets == []
        assert board.general is None
        assert board.setup is None

    def test_export_surface(self) -> None:
        import eedesigncore as kfi

        assert kfi.Board is Board


class TestHeaderRoundTrip:
    def test_write_board_matches_header(self) -> None:
        board = Board(version=20260206, generator="pcbnew", generator_version="10.0")
        assert write_board(board) == HEADER

    def test_load_pcb_header(self) -> None:
        board = load_pcb(parse(HEADER))
        assert board.version == 20260206
        assert board.generator == "pcbnew"
        assert board.generator_version == "10.0"

    def test_round_trip_byte_identical(self) -> None:
        board = Board.from_string(HEADER)
        assert board.to_string() == HEADER

    def test_generator_version_omitted(self) -> None:
        board = Board(version=20260206, generator="pcbnew")
        flat = dump_board(board)
        assert "generator_version" not in flat

    def test_unknown_token_raises(self) -> None:
        text = '(kicad_pcb (version 20260206) (generator "pcbnew") (bogus 1))'
        with pytest.raises(KicadParseError, match="Unknown token in kicad_pcb"):
            load_pcb(parse(text))
