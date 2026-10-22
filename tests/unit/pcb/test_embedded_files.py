"""Board-level embedded-files consolidation / deferral / hydration (T9).

KiCad puts all embedded bytes at the board root (a consolidated union) and writes
each footprint a checksum-only stub (``aWriteData`` polarity, ownership report
sections 2/6). On load ``BOARD::FixupEmbeddedData`` copies the bytes back into
each footprint by filename. These tests exercise both directions plus a real
corpus board carrying a footprint 3D-model embed.
"""

from __future__ import annotations

from eedesigncore.common import SaveContext
from eedesigncore.common.models import EmbeddedFile
from eedesigncore.mod.models import Footprint
from eedesigncore.mod.writer import dump_mod
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.writer import dump_board

_DATA = "KLUv/WByGKU8AIpTHA8q8A=="  # short valid base64 blob


def _board_with_embed() -> Board:
    stub = EmbeddedFile(name="m.step", file_type="model", data="", checksum="ABC123")
    root = EmbeddedFile(name="m.step", file_type="model", data=_DATA, checksum="ABC123")
    fp = Footprint(fp_id="Lib:Part", uuid="fp-uuid", embedded_files=[stub])
    return Board(footprints=[fp], embedded_files=[root])


class TestDeferralOnWrite:
    def test_footprint_stub_has_no_data(self) -> None:
        fp = _board_with_embed().footprints[0]
        out = dump_mod(fp, context=SaveContext.BOARD)
        assert '(name "m.step")' in out
        assert '(checksum "ABC123")' in out
        assert "(data" not in out  # deferred: checksum-only

    def test_board_root_block_has_data(self) -> None:
        out = dump_board(_board_with_embed())
        # embedded_fonts precedes the root (embedded_files) block.
        tail = out.split("(embedded_fonts", 1)[1]
        assert "(embedded_files" in tail
        assert _DATA in tail


class TestHydrationOnLoad:
    def test_round_trip_byte_identical(self) -> None:
        board = _board_with_embed()
        text = board.to_string()
        assert Board.from_string(text).to_string() == text

    def test_footprint_stub_hydrated_from_root(self) -> None:
        board = _board_with_embed()
        loaded = Board.from_string(board.to_string())
        stub = loaded.footprints[0].embedded_files[0]
        assert stub.name == "m.step"
        assert stub.data == _DATA  # bytes materialised from root
        assert stub.checksum == "ABC123"  # checksum preserved as read

    def test_no_hydration_without_root_match(self) -> None:
        # A footprint stub with no matching root entry stays empty (no crash).
        stub = EmbeddedFile(name="orphan.step", file_type="model", data="", checksum="Z")
        fp = Footprint(fp_id="Lib:P", uuid="u", embedded_files=[stub])
        loaded = Board.from_string(Board(footprints=[fp]).to_string())
        assert loaded.footprints[0].embedded_files[0].data == ""
