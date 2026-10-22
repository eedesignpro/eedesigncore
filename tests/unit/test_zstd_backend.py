"""Tests for the tiered zstd backend selector (P1, R13).

The selector prefers the standard library module ``compression.zstd`` and falls
back to the ``zstandard`` package. Both tiers exist on this machine, so both run
for real here. The selection-order tests still fake the module table, because a
test must not depend on which interpreter runs it.
"""

from __future__ import annotations

import importlib
import io
import tracemalloc
from collections.abc import Callable
from types import ModuleType, SimpleNamespace

import pytest
import zstandard

from eedesigncore.common import _zstd
from eedesigncore.common.models import MAX_DECOMPRESSED_SIZE
from eedesigncore.exceptions import (
    ZstdBackendUnavailableError,
    ZstdDecompressionError,
)

PAYLOAD = b"embedded payload bytes " * 500
# The zstd magic number, then too few bytes for a frame header.
TRUNCATED_HEADER = b"\x28\xb5\x2f\xfd\xff\xff\xff"

MIB = 1024 * 1024


def _bomb_frame(total: int, block: int = MIB) -> bytes:
    """Return a tiny frame of zero bytes that decodes to *total* bytes.

    The frame is written a block at a time and declares no content size, so
    building it costs one block of memory, not *total*. This is the shape of a
    decompression bomb: a kilobyte of input, tens of megabytes of output.
    """
    sink = io.BytesIO()
    writer = zstandard.ZstdCompressor(level=3, write_content_size=False).stream_writer(
        sink, closefd=False
    )
    for _ in range(total // block):
        writer.write(bytes(block))
    writer.close()
    return sink.getvalue()


def _corrupt_body_frame() -> bytes:
    """Return a frame whose header still reads but whose compressed body does not."""
    intact = zstandard.ZstdCompressor(level=15).compress(bytes(range(256)) * 20)
    return intact[:6] + bytes(byte ^ 0xA5 for byte in intact[6:])


CORRUPT_BODY = _corrupt_body_frame()


def _stdlib_module() -> ModuleType | None:
    try:
        return importlib.import_module(_zstd.STDLIB_BACKEND)
    except ImportError:
        return None


stdlib_present = pytest.mark.skipif(
    _stdlib_module() is None,
    reason="compression.zstd needs Python 3.14 or later",
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _zstd.reset_backend_cache()


def _fake_import(available: set[str]) -> Callable[[str], ModuleType | None]:
    """Return an import hook that only resolves the module names in *available*."""

    def _hook(name: str) -> ModuleType | None:
        if name not in available:
            return None
        return importlib.import_module(name)

    return _hook


def _fake_stdlib_module() -> ModuleType:
    """Build a ``compression.zstd``-shaped module over the zstandard primitives.

    Python 3.11 to 3.13 have no ``compression.zstd``. This fake gives those
    interpreters the same call shape, so the tier-1 code path still runs there.
    The fake copies the API shape only. It makes no safety claim, because it
    decompresses the whole frame before it applies *max_length*.
    """

    class FakeZstdDecompressor:
        def __init__(self) -> None:
            self._object = zstandard.ZstdDecompressor().decompressobj()
            self._buffer = b""
            self.eof = False

        def decompress(self, data: bytes, max_length: int = -1) -> bytes:
            if data:
                self._buffer += self._object.decompress(data)
            if 0 <= max_length < len(self._buffer):
                out, self._buffer = self._buffer[:max_length], self._buffer[max_length:]
                return out
            out, self._buffer = self._buffer, b""
            self.eof = True
            return out

    def get_frame_info(data: bytes) -> SimpleNamespace:
        size = zstandard.frame_content_size(data)
        return SimpleNamespace(decompressed_size=None if size < 0 else size, dictionary_id=0)

    module = ModuleType("fake_compression_zstd")
    module.compress = lambda data, level=None: zstandard.ZstdCompressor(  # type: ignore[attr-defined]
        level=15 if level is None else level
    ).compress(data)
    module.ZstdDecompressor = FakeZstdDecompressor  # type: ignore[attr-defined]
    module.get_frame_info = get_frame_info  # type: ignore[attr-defined]
    module.ZstdError = zstandard.ZstdError  # type: ignore[attr-defined]
    return module


class TestBackendSelection:
    def test_stdlib_tier_wins_when_both_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tier 1 beats tier 2 whenever both modules import."""
        if _stdlib_module() is None:
            pytest.skip("compression.zstd needs Python 3.14 or later")
        monkeypatch.setattr(
            _zstd,
            "_import_module",
            _fake_import({_zstd.STDLIB_BACKEND, _zstd.PACKAGE_BACKEND}),
        )
        assert _zstd.select_backend().name == _zstd.STDLIB_BACKEND

    def test_falls_back_to_package_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The selector uses zstandard when compression.zstd does not import."""
        monkeypatch.setattr(_zstd, "_import_module", _fake_import({_zstd.PACKAGE_BACKEND}))
        assert _zstd.select_backend().name == _zstd.PACKAGE_BACKEND

    def test_both_absent_raises_named_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The error names both backends and the fix."""
        monkeypatch.setattr(_zstd, "_import_module", _fake_import(set()))
        with pytest.raises(ZstdBackendUnavailableError) as excinfo:
            _zstd.select_backend()
        message = str(excinfo.value)
        assert _zstd.STDLIB_BACKEND in message
        assert _zstd.PACKAGE_BACKEND in message
        assert "pip install" in message

    def test_missing_module_resolves_to_none(self) -> None:
        """The import helper answers None instead of raising ImportError."""
        assert _zstd._import_module("eedesigncore_no_such_backend_module") is None

    def test_get_backend_caches(self) -> None:
        assert _zstd.get_backend() is _zstd.get_backend()

    def test_reset_clears_the_cache(self) -> None:
        first = _zstd.get_backend()
        _zstd.reset_backend_cache()
        assert _zstd.get_backend() is not first

    def test_backend_name_is_one_of_the_two_tiers(self) -> None:
        assert _zstd.backend_name() in {_zstd.STDLIB_BACKEND, _zstd.PACKAGE_BACKEND}

    def test_module_level_calls_round_trip(self) -> None:
        frame = _zstd.compress(PAYLOAD, 15)
        assert _zstd.decompress(frame, 1 << 20) == PAYLOAD


class TestPackageBackend:
    def _backend(self) -> _zstd.PackageBackend:
        return _zstd.PackageBackend(importlib.import_module(_zstd.PACKAGE_BACKEND))

    def test_round_trip(self) -> None:
        backend = self._backend()
        assert backend.decompress(backend.compress(PAYLOAD, 15), 1 << 20) == PAYLOAD

    def test_declared_size_over_cap_raises(self) -> None:
        backend = self._backend()
        frame = backend.compress(PAYLOAD, 15)
        with pytest.raises(ZstdDecompressionError, match="declares"):
            backend.decompress(frame, 10)

    def test_hidden_size_over_cap_raises(self) -> None:
        """A frame with no declared content size still hits the hard output limit."""
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        assert zstandard.frame_content_size(frame) == -1
        with pytest.raises(ZstdDecompressionError, match="decompresses to more than"):
            self._backend().decompress(frame, 10)

    def test_corrupt_frame_raises(self) -> None:
        with pytest.raises(ZstdDecompressionError, match="not readable"):
            self._backend().decompress(b"this is not a zstd frame", 1 << 20)

    def test_unreadable_frame_header_raises(self) -> None:
        """The zstd magic number with a broken header fails on the header read."""
        with pytest.raises(ZstdDecompressionError, match="header is not readable"):
            self._backend().decompress(TRUNCATED_HEADER, 1 << 20)

    def test_corrupt_body_raises(self) -> None:
        """A readable header with a broken body fails on the decompress call."""
        with pytest.raises(ZstdDecompressionError, match="frame is not readable"):
            self._backend().decompress(CORRUPT_BODY, 1 << 20)

    def test_exact_cap_is_allowed(self) -> None:
        backend = self._backend()
        frame = backend.compress(PAYLOAD, 15)
        assert backend.decompress(frame, len(PAYLOAD)) == PAYLOAD

    def test_truncated_frame_raises(self) -> None:
        """LOW-4: tier 2 must refuse a short frame, not return the partial payload."""
        backend = self._backend()
        frame = backend.compress(PAYLOAD, 15)
        with pytest.raises(ZstdDecompressionError, match="incomplete"):
            backend.decompress(frame[:-4], 1 << 20)

    def test_truncated_frame_with_no_declared_size_raises(self) -> None:
        """The header check cannot help here, so the frame check has to."""
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        assert zstandard.frame_content_size(frame) == -1
        with pytest.raises(ZstdDecompressionError, match="incomplete"):
            self._backend().decompress(frame[: len(frame) // 2], 1 << 20)

    def test_a_truncated_frame_returns_no_partial_payload(self) -> None:
        """The streaming reader hands back a partial payload. The backend must not."""
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        with pytest.raises(ZstdDecompressionError):
            self._backend().decompress(frame[:-4], 1 << 20)

    def test_a_truncated_bomb_frame_raises_incomplete(self) -> None:
        """Chunked feeding keeps the completeness check on a hidden-size frame."""
        with pytest.raises(ZstdDecompressionError, match="incomplete"):
            frame = _bomb_frame(4 * MIB)
            self._backend().decompress(frame[: len(frame) // 2], 8 * MIB)

    def test_a_bomb_frame_raises_oversize_before_the_payload_lands(self) -> None:
        """MEDIUM-3: the count catches the bomb inside one input slice of output.

        The frame is a kilobyte of input that decodes to 32 MiB. The cap is
        64 KiB, so the peak allocation must stay near the cap plus the overshoot
        one slice can carry, far below the 32 MiB the frame would produce.
        """
        frame = _bomb_frame(32 * MIB)
        assert len(frame) < 4096

        tracemalloc.start()
        try:
            with pytest.raises(ZstdDecompressionError, match="decompresses to more than"):
                self._backend().decompress(frame, 64 * 1024)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

        assert peak < 12 * MIB

    def test_the_default_cap_costs_nothing_up_front(self) -> None:
        """MEDIUM-3: a 1 GiB cap on a 4 MiB payload allocates 4 MiB, not 1 GiB."""
        frame = _bomb_frame(4 * MIB)

        tracemalloc.start()
        try:
            out = self._backend().decompress(frame, MAX_DECOMPRESSED_SIZE)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

        assert len(out) == 4 * MIB
        assert peak < 16 * MIB


@stdlib_present
class TestStdlibBackend:
    def _backend(self) -> _zstd.StdlibBackend:
        module = _stdlib_module()
        assert module is not None
        return _zstd.StdlibBackend(module)

    def test_round_trip(self) -> None:
        backend = self._backend()
        assert backend.decompress(backend.compress(PAYLOAD, 15), 1 << 20) == PAYLOAD

    def test_declared_size_over_cap_raises(self) -> None:
        backend = self._backend()
        frame = backend.compress(PAYLOAD, 15)
        with pytest.raises(ZstdDecompressionError, match="declares"):
            backend.decompress(frame, 10)

    def test_hidden_size_over_cap_raises(self) -> None:
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        with pytest.raises(ZstdDecompressionError, match="decompresses to more than"):
            self._backend().decompress(frame, 10)

    def test_corrupt_frame_raises(self) -> None:
        with pytest.raises(ZstdDecompressionError, match="not readable"):
            self._backend().decompress(b"this is not a zstd frame", 1 << 20)

    def test_unreadable_frame_header_raises(self) -> None:
        """The zstd magic number with a broken header fails on the header read."""
        with pytest.raises(ZstdDecompressionError, match="header is not readable"):
            self._backend().decompress(TRUNCATED_HEADER, 1 << 20)

    def test_corrupt_body_raises(self) -> None:
        """A readable header with a broken body fails on the decompress call."""
        with pytest.raises(ZstdDecompressionError, match="frame is not readable"):
            self._backend().decompress(CORRUPT_BODY, 1 << 20)

    def test_truncated_frame_raises(self) -> None:
        backend = self._backend()
        frame = backend.compress(PAYLOAD, 15)
        with pytest.raises(ZstdDecompressionError, match="incomplete"):
            backend.decompress(frame[:-4], 1 << 20)

    def test_truncated_frame_with_no_declared_size_raises(self) -> None:
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        with pytest.raises(ZstdDecompressionError, match="incomplete"):
            self._backend().decompress(frame[: len(frame) // 2], 1 << 20)


class TestStdlibBackendAgainstFakeModule:
    """Run the tier-1 code path on any interpreter, over a fake stdlib module.

    Python 3.11 to 3.13 have no ``compression.zstd``. These tests keep the tier-1
    branch covered on those interpreters. The fake module wraps the real
    zstandard primitives, so the frames stay real zstd frames.
    """

    def _backend(self) -> _zstd.StdlibBackend:
        return _zstd.StdlibBackend(_fake_stdlib_module())

    def test_round_trip(self) -> None:
        backend = self._backend()
        assert backend.decompress(backend.compress(PAYLOAD, 15), 1 << 20) == PAYLOAD

    def test_declared_size_over_cap_raises(self) -> None:
        backend = self._backend()
        with pytest.raises(ZstdDecompressionError, match="declares"):
            backend.decompress(backend.compress(PAYLOAD, 15), 10)

    def test_hidden_size_over_cap_raises(self) -> None:
        frame = zstandard.ZstdCompressor(level=15, write_content_size=False).compress(PAYLOAD)
        with pytest.raises(ZstdDecompressionError, match="decompresses to more than"):
            self._backend().decompress(frame, 10)

    def test_corrupt_frame_raises(self) -> None:
        with pytest.raises(ZstdDecompressionError, match="not readable"):
            self._backend().decompress(b"this is not a zstd frame", 1 << 20)

    def test_unreadable_frame_header_raises(self) -> None:
        """The zstd magic number with a broken header fails on the header read."""
        with pytest.raises(ZstdDecompressionError, match="header is not readable"):
            self._backend().decompress(TRUNCATED_HEADER, 1 << 20)

    def test_corrupt_body_raises(self) -> None:
        """A readable header with a broken body fails on the decompress call."""
        with pytest.raises(ZstdDecompressionError, match="frame is not readable"):
            self._backend().decompress(CORRUPT_BODY, 1 << 20)

    def test_fake_tier_frame_reads_on_the_package_tier(self) -> None:
        """The fake tier-1 module writes a frame the real tier-2 backend reads."""
        frame = self._backend().compress(PAYLOAD, 15)
        package = _zstd.PackageBackend(importlib.import_module(_zstd.PACKAGE_BACKEND))
        assert package.decompress(frame, 1 << 20) == PAYLOAD


@stdlib_present
class TestBackendParity:
    """Cross-backend parity.

    Both interpreters tiers are present on this machine, so these tests compress
    with one real backend and decompress with the other real backend. On a host
    without ``compression.zstd`` the class skips, and the parity claim drops to
    the frame-format check that the package-tier tests already make.
    """

    def _pair(self) -> tuple[_zstd.StdlibBackend, _zstd.PackageBackend]:
        module = _stdlib_module()
        assert module is not None
        return (
            _zstd.StdlibBackend(module),
            _zstd.PackageBackend(importlib.import_module(_zstd.PACKAGE_BACKEND)),
        )

    def test_stdlib_frame_decompresses_on_package_tier(self) -> None:
        stdlib, package = self._pair()
        assert package.decompress(stdlib.compress(PAYLOAD, 15), 1 << 20) == PAYLOAD

    def test_package_frame_decompresses_on_stdlib_tier(self) -> None:
        stdlib, package = self._pair()
        assert stdlib.decompress(package.compress(PAYLOAD, 15), 1 << 20) == PAYLOAD

    def test_both_tiers_reject_a_truncated_frame_the_same_way(self) -> None:
        """LOW-4: one failure mode and one message across both tiers."""
        stdlib, package = self._pair()
        frame = stdlib.compress(PAYLOAD, 15)[:-4]

        with pytest.raises(ZstdDecompressionError) as stdlib_error:
            stdlib.decompress(frame, 1 << 20)
        with pytest.raises(ZstdDecompressionError) as package_error:
            package.decompress(frame, 1 << 20)

        assert str(stdlib_error.value) == str(package_error.value)

    def test_both_tiers_reject_an_oversize_payload_the_same_way(self) -> None:
        """MEDIUM-3: the chunked tier-2 decode keeps the oversize message parity."""
        stdlib, package = self._pair()
        frame = _bomb_frame(4 * MIB)

        with pytest.raises(ZstdDecompressionError) as stdlib_error:
            stdlib.decompress(frame, 64 * 1024)
        with pytest.raises(ZstdDecompressionError) as package_error:
            package.decompress(frame, 64 * 1024)

        assert str(stdlib_error.value) == str(package_error.value)

    def test_both_tiers_declare_the_same_content_size(self) -> None:
        stdlib, package = self._pair()
        assert package._declared_size(stdlib.compress(PAYLOAD, 15)) == len(PAYLOAD)
        assert stdlib._declared_size(package.compress(PAYLOAD, 15)) == len(PAYLOAD)
