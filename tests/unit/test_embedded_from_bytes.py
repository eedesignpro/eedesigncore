"""Tests for EmbeddedFile.from_bytes (GAP-014, T20).

The strongest test extracts a real corpus embedded file, decodes+decompresses
its stored data to recover the original bytes, and verifies our checksum
computation reproduces the checksum KiCad itself wrote.
"""

from __future__ import annotations

import base64
from pathlib import Path

import zstandard

from eedesigncore.common.models import EmbeddedFile, _mmh3_128_kicad
from eedesigncore.common.writers import dump_embedded_files
from eedesigncore.mod.models import Footprint

FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "mod"
    / "CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod"
)


def _corpus_embedded_file() -> EmbeddedFile:
    fp = Footprint.from_file(FIXTURE)
    assert fp.embedded_files, "fixture must carry an embedded file"
    return fp.embedded_files[0]


class TestChecksumAgainstRealCorpus:
    def test_computed_checksum_matches_stored(self) -> None:
        """Recompute the hash over the decompressed corpus bytes -> stored value."""
        ef = _corpus_embedded_file()
        raw = zstandard.ZstdDecompressor().decompress(base64.b64decode(ef.data))
        assert _mmh3_128_kicad(raw) == ef.checksum

    def test_from_bytes_reproduces_stored_checksum(self) -> None:
        """from_bytes over the same raw bytes yields the corpus checksum exactly."""
        ef = _corpus_embedded_file()
        raw = zstandard.ZstdDecompressor().decompress(base64.b64decode(ef.data))
        rebuilt = EmbeddedFile.from_bytes(ef.name, raw, kind="model")
        assert rebuilt.checksum == ef.checksum


class TestFromBytesRoundTrip:
    def test_data_decodes_back_to_input(self) -> None:
        payload = b"hello embedded world" * 100
        ef = EmbeddedFile.from_bytes("t.bin", payload, kind="other")
        raw = zstandard.ZstdDecompressor().decompress(base64.b64decode(ef.data))
        assert raw == payload

    def test_checksum_is_32_upper_hex(self) -> None:
        ef = EmbeddedFile.from_bytes("t.bin", b"abc", kind="other")
        assert len(ef.checksum) == 32
        assert ef.checksum == ef.checksum.upper()
        int(ef.checksum, 16)  # parses as hex

    def test_from_bytes_to_string_is_kicad_shaped(self) -> None:
        ef = EmbeddedFile.from_bytes("model.step", b"payload-bytes", kind="model")
        flat = dump_embedded_files([ef])
        assert flat.startswith("(embedded_files")
        assert '(name "model.step")' in flat
        assert "(type model)" in flat
        assert "(data\n|" in flat  # pipe-chunked data block
        assert f'(checksum "{ef.checksum}")' in flat


class TestParsedRoundTripUnchanged:
    def test_parsed_checksum_preserved_verbatim(self) -> None:
        """Round-trip must NOT recompute a parsed file's checksum.

        Compared line by line rather than byte for byte, because this fixture
        stores its drawings in a pre-canonical layer order that our writer sorts
        (ADR-058). The checksum line itself must survive untouched.
        """
        original = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
        fp = Footprint.from_file(FIXTURE)
        from eedesigncore.mod.writer import write_mod

        written = write_mod(fp)
        checksums = [line for line in original.splitlines() if "(checksum " in line]

        assert checksums
        for line in checksums:
            assert line in written.splitlines()
        assert sorted(written.splitlines()) == sorted(original.splitlines())


class TestMmh3TailPaths:
    def test_empty_and_various_lengths(self) -> None:
        # Exercise multiple length%16 tail branches; just assert well-formed hex.
        for length in (0, 1, 4, 8, 9, 12, 15, 16, 17, 33):
            digest = _mmh3_128_kicad(b"\x01" * length)
            assert len(digest) == 32
            int(digest, 16)
