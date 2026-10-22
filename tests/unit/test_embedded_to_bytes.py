"""Tests for EmbeddedFile.to_bytes (P1, R16).

``to_bytes`` reverses ``from_bytes``. It base64-decodes the stored text,
decompresses the zstd frame under a hard size cap, and verifies the MurmurHash3
checksum over the decompressed bytes.
"""

from __future__ import annotations

import base64
import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
import zstandard

from eedesigncore.common import _zstd
from eedesigncore.common.models import (
    MAX_DECOMPRESSED_SIZE,
    MAX_EMBEDDED_DATA_SIZE,
    EmbeddedFile,
)
from eedesigncore.exceptions import (
    EmbeddedFileChecksumError,
    KicadParseError,
    ZstdDecompressionError,
)
from eedesigncore.mod.models import Footprint

FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "mod"
    / "CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod"
)


def _available_backends() -> list[str]:
    names = [_zstd.PACKAGE_BACKEND]
    try:
        importlib.import_module(_zstd.STDLIB_BACKEND)
    except ImportError:
        return names
    return [_zstd.STDLIB_BACKEND, *names]


def _backend(name: str) -> _zstd.ZstdBackend:
    module = importlib.import_module(name)
    if name == _zstd.STDLIB_BACKEND:
        return _zstd.StdlibBackend(module)
    return _zstd.PackageBackend(module)


@pytest.fixture(autouse=True)
def _restore_backend() -> Iterator[None]:
    yield
    _zstd.reset_backend_cache()


def _use(name: str) -> None:
    _zstd._BACKEND = _backend(name)


class TestRoundTrip:
    @pytest.mark.parametrize("compress_with", _available_backends())
    @pytest.mark.parametrize("decompress_with", _available_backends())
    def test_round_trip_across_backend_combinations(
        self, compress_with: str, decompress_with: str
    ) -> None:
        """to_bytes(from_bytes(data)) == data for every backend pair."""
        payload = b"round trip payload " * 300
        _use(compress_with)
        embedded = EmbeddedFile.from_bytes("t.bin", payload, kind="other")
        _use(decompress_with)
        assert embedded.to_bytes() == payload

    def test_empty_payload(self) -> None:
        embedded = EmbeddedFile.from_bytes("empty.bin", b"", kind="other")
        assert embedded.to_bytes() == b""

    def test_binary_payload(self) -> None:
        payload = bytes(range(256)) * 40
        embedded = EmbeddedFile.from_bytes("blob.bin", payload, kind="model")
        assert embedded.to_bytes() == payload

    def test_corpus_file_decodes_and_verifies(self) -> None:
        """A real KiCad-written embedded file decodes and passes its own checksum."""
        footprint = Footprint.from_file(FIXTURE)
        embedded = footprint.embedded_files[0]
        raw = embedded.to_bytes()
        assert raw == zstandard.ZstdDecompressor().decompress(base64.b64decode(embedded.data))


class TestChecksumVerification:
    def test_mismatch_raises(self) -> None:
        embedded = EmbeddedFile.from_bytes("t.bin", b"payload", kind="other")
        tampered = embedded.model_copy(update={"checksum": "0" * 32})
        with pytest.raises(EmbeddedFileChecksumError, match="checksum"):
            tampered.to_bytes()

    def test_error_names_the_file(self) -> None:
        embedded = EmbeddedFile.from_bytes("logo.png", b"payload", kind="other")
        tampered = embedded.model_copy(update={"checksum": "0" * 32})
        with pytest.raises(EmbeddedFileChecksumError, match="logo.png"):
            tampered.to_bytes()

    def test_lowercase_stored_checksum_passes(self) -> None:
        embedded = EmbeddedFile.from_bytes("t.bin", b"payload", kind="other")
        lowered = embedded.model_copy(update={"checksum": embedded.checksum.lower()})
        assert lowered.to_bytes() == b"payload"

    def test_tampered_content_raises(self) -> None:
        """Content that no longer hashes to the stored value fails."""
        good = EmbeddedFile.from_bytes("t.bin", b"payload", kind="other")
        other = EmbeddedFile.from_bytes("t.bin", b"different payload", kind="other")
        swapped = good.model_copy(update={"data": other.data})
        with pytest.raises(EmbeddedFileChecksumError):
            swapped.to_bytes()


class TestSizeCap:
    def test_default_cap_matches_the_stored_text_cap(self) -> None:
        """1 GiB, so a large STEP model decodes (ADR-042, operator ruling)."""
        assert MAX_DECOMPRESSED_SIZE == 1024 * 1024 * 1024
        assert MAX_DECOMPRESSED_SIZE == MAX_EMBEDDED_DATA_SIZE

    def test_a_small_payload_decodes_under_the_default_cap(self) -> None:
        """The default cap admits an ordinary file with no argument from the caller."""
        embedded = EmbeddedFile.from_bytes("t.bin", b"step model bytes " * 100, kind="model")
        assert embedded.to_bytes() == b"step model bytes " * 100

    def test_over_cap_raises_before_allocation(self) -> None:
        """A 4 MB payload of zeros compresses small. The cap stops the decode."""
        embedded = EmbeddedFile.from_bytes("bomb.bin", b"\x00" * (4 * 1024 * 1024))
        assert len(embedded.data) < 4096
        with pytest.raises(ZstdDecompressionError, match="declares"):
            embedded.to_bytes(max_size=1024)

    def test_cap_applies_to_a_frame_with_no_declared_size(self) -> None:
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(
            b"\x00" * (4 * 1024 * 1024)
        )
        embedded = EmbeddedFile(
            name="bomb.bin",
            file_type="other",
            data=base64.b64encode(frame).decode("ascii"),
            checksum="0" * 32,
        )
        with pytest.raises(ZstdDecompressionError, match="decompresses to more than"):
            embedded.to_bytes(max_size=1024)

    def test_payload_at_the_cap_is_allowed(self) -> None:
        payload = b"x" * 2048
        embedded = EmbeddedFile.from_bytes("t.bin", payload, kind="other")
        assert embedded.to_bytes(max_size=2048) == payload


class TestMalformedInput:
    def test_non_decodable_base64_raises_parse_error(self) -> None:
        embedded = EmbeddedFile(name="t.bin", file_type="other", data="A", checksum="0" * 32)
        with pytest.raises(KicadParseError, match="valid base64"):
            embedded.to_bytes()

    def test_whitespace_in_stored_text_is_ignored(self) -> None:
        payload = b"wrapped payload " * 20
        embedded = EmbeddedFile.from_bytes("t.bin", payload, kind="other")
        text = embedded.data
        chunked = "\n".join(text[i : i + 40] for i in range(0, len(text), 40))
        assert embedded.model_copy(update={"data": chunked}).to_bytes() == payload

    def test_base64_that_is_not_a_zstd_frame_raises(self) -> None:
        embedded = EmbeddedFile(
            name="t.bin",
            file_type="other",
            data=base64.b64encode(b"plain bytes, no frame").decode("ascii"),
            checksum="0" * 32,
        )
        with pytest.raises(ZstdDecompressionError):
            embedded.to_bytes()
