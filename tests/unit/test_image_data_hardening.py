"""Unit tests for LOW-3 hardening (iteration-11 T6, plan R7).

Three writers emit base64 image data as QUOTED chunks and escape nothing:
``sch/writer.py:_dump_image`` for ``Image.data``, ``mod/writer.py``
``_dump_stream_data`` for ``ReferenceImage.data``, and ``pcb/writer.py``, which
imports the mod dumper. A quote or a backslash inside the payload would break
the token. The base64 character-set validator makes that unreachable on all
three paths.

KiCad 10.0.3 has NO ``gr_bitmap`` token. A grep over ``pcbnew/`` and ``common/``
in the clone returns nothing, and ``format( const PCB_REFERENCE_IMAGE* )``
(pcb_io_kicad_sexpr.cpp:1125-1144) writes ``(image …)``. The PCB path is
therefore ``ReferenceImage``, which these tests cover.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eedesigncore.common import models as common_models
from eedesigncore.common.models import MAX_EMBEDDED_DATA_SIZE, EmbeddedFile
from eedesigncore.common.pcb_family import ReferenceImage, board_items
from eedesigncore.mod.models import Footprint
from eedesigncore.mod.writer import write_mod
from eedesigncore.sch import models as sch_models
from eedesigncore.sch.models import Image

# The real cap is 1 GiB (ADR-042). Each cap-breach test lowers the constant the
# validator reads, because allocating a string over the real cap would cost a
# gigabyte of memory per test.
SMALL_CAP = 16

_HOSTILE = [
    pytest.param('AAAA") (evil "', id="quote_and_parens"),
    pytest.param("AAAA\\", id="backslash"),
    pytest.param('AA"AA', id="bare_quote"),
    pytest.param("AA|AA", id="pipe"),
    pytest.param("AA(AA", id="open_paren"),
    pytest.param("AA)AA", id="close_paren"),
]


class TestTheCapNumber:
    def test_every_stored_text_cap_is_one_gibibyte(self) -> None:
        """One number across the three base64 fields (ADR-042, operator ruling)."""
        assert MAX_EMBEDDED_DATA_SIZE == 1024 * 1024 * 1024
        assert sch_models.MAX_EMBEDDED_DATA_SIZE == MAX_EMBEDDED_DATA_SIZE
        assert board_items.MAX_IMAGE_DATA_SIZE == MAX_EMBEDDED_DATA_SIZE


class TestSchImageDataValidator:
    def test_valid_base64_is_accepted(self) -> None:
        assert Image(data="QUJDRA==").data == "QUJDRA=="

    def test_whitespace_is_accepted(self) -> None:
        """The loader joins on-disk chunks, so newlines must survive."""
        assert Image(data="QUJD\nRA==").data == "QUJD\nRA=="

    def test_empty_data_is_accepted(self) -> None:
        assert Image().data == ""

    @pytest.mark.parametrize("payload", _HOSTILE)
    def test_hostile_characters_are_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError, match="invalid base64 characters"):
            Image(data=payload)

    def test_oversize_data_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sch_models, "MAX_EMBEDDED_DATA_SIZE", SMALL_CAP)
        with pytest.raises(ValidationError, match="exceeds the 1 GiB limit"):
            Image(data="A" * (SMALL_CAP + 1))

    def test_the_cap_matches_the_embedded_file_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One policy, one number (R7)."""
        monkeypatch.setattr(common_models, "MAX_EMBEDDED_DATA_SIZE", SMALL_CAP)
        with pytest.raises(ValidationError, match="exceeds the 1 GiB limit"):
            EmbeddedFile(
                name="f",
                file_type="other",
                data="A" * (SMALL_CAP + 1),
                checksum="0" * 32,
            )

    def test_data_at_the_cap_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sch_models, "MAX_EMBEDDED_DATA_SIZE", SMALL_CAP)
        assert Image(data="A" * SMALL_CAP).data == "A" * SMALL_CAP


class TestPcbReferenceImageDataValidator:
    def test_valid_base64_is_accepted(self) -> None:
        assert ReferenceImage(data="QUJDRA==").data == "QUJDRA=="

    @pytest.mark.parametrize("payload", _HOSTILE)
    def test_hostile_characters_are_rejected(self, payload: str) -> None:
        with pytest.raises(ValidationError, match="invalid base64 characters"):
            ReferenceImage(data=payload)

    def test_oversize_data_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(board_items, "MAX_IMAGE_DATA_SIZE", SMALL_CAP)
        with pytest.raises(ValidationError, match="exceeds the 1 GiB limit"):
            ReferenceImage(data="A" * (SMALL_CAP + 1))


class TestQuotedChunkEmitStaysWellFormed:
    def test_the_mod_writer_emits_balanced_quotes(self) -> None:
        fp = Footprint.new("R")
        fp.drawings = [ReferenceImage(data="QUJDRA==")]
        text = write_mod(fp)
        assert text.count('"QUJDRA=="') == 1
        assert text.count('"') % 2 == 0

    def test_an_empty_payload_emits_a_bare_data_token(self) -> None:
        fp = Footprint.new("R")
        fp.drawings = [ReferenceImage(data="")]
        assert "(data)" in write_mod(fp)
