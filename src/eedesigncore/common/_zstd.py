"""Tiered zstd backend for embedded-file compression and decompression.

Tier 1 is the standard library module ``compression.zstd`` (PEP 784, Python 3.14
and later). Tier 2 is the ``zstandard`` package, which publishes prebuilt wheels
for Python 3.11 to 3.13. The selector prefers tier 1 and falls back to tier 2.
When neither backend imports, every call raises
:class:`~eedesigncore.exceptions.ZstdBackendUnavailableError`.

Both tiers write plain zstd frames at the same compression level, so a frame
from one tier decompresses on the other. The embedded-file checksum runs over
the decompressed bytes, so the backend choice cannot change it.

Every decompress call takes a hard output limit. The limit guards the decode
path against a decompression bomb (R16).

Both tiers refuse an incomplete frame with the same message, and both read the
``eof`` flag of a decompressor object to do it (LOW-4). Neither tier reserves
the output limit up front: tier 1 grows its buffer in blocks, and tier 2 feeds
the frame in small slices and counts the output between them (MEDIUM-3).
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from types import ModuleType
from typing import Final, cast

from eedesigncore.exceptions import (
    ZstdBackendUnavailableError,
    ZstdDecompressionError,
)

STDLIB_BACKEND: Final = "compression.zstd"
PACKAGE_BACKEND: Final = "zstandard"

_UNAVAILABLE_MESSAGE: Final = (
    "No zstd backend is available. "
    f"eedesigncore uses the standard library module {STDLIB_BACKEND} on Python 3.14 "
    "and later. "
    f"It uses the {PACKAGE_BACKEND} package on Python 3.11 to 3.13. "
    'Fix: run pip install "zstandard==0.25.0".'
)


class ZstdBackend(ABC):
    """One zstd implementation, wrapped behind a two-call interface."""

    name: str

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    @abstractmethod
    def compress(self, data: bytes, level: int) -> bytes:
        """Return *data* as a zstd frame at compression *level*."""

    @abstractmethod
    def decompress(self, data: bytes, max_output_size: int) -> bytes:
        """Return the payload of the zstd frame *data*.

        Raise :class:`ZstdDecompressionError` when the payload is larger than
        *max_output_size* bytes, or when the frame is corrupt or incomplete.
        """

    def _reject_declared_size(self, declared: int | None, max_output_size: int) -> None:
        """Raise before any allocation when the frame header declares an oversize payload."""
        if declared is not None and declared > max_output_size:
            raise ZstdDecompressionError(
                f"The zstd frame declares {declared} bytes of content. "
                f"The decode limit is {max_output_size} bytes."
            )

    def _reject_oversize(self, max_output_size: int) -> ZstdDecompressionError:
        return ZstdDecompressionError(
            f"The zstd frame decompresses to more than {max_output_size} bytes. "
            "Fix: raise the max_size argument if the file is trusted."
        )


class StdlibBackend(ZstdBackend):
    """Tier 1. The ``compression.zstd`` module of the standard library."""

    name = STDLIB_BACKEND

    def compress(self, data: bytes, level: int) -> bytes:
        return cast(bytes, self._module.compress(data, level=level))

    def decompress(self, data: bytes, max_output_size: int) -> bytes:
        self._reject_declared_size(self._declared_size(data), max_output_size)
        decompressor = self._module.ZstdDecompressor()
        try:
            out = cast(bytes, decompressor.decompress(data, max_length=max_output_size + 1))
        except self._module.ZstdError as exc:
            raise ZstdDecompressionError(f"The zstd frame is not readable: {exc}") from exc
        if len(out) > max_output_size:
            raise self._reject_oversize(max_output_size)
        if not decompressor.eof:
            raise ZstdDecompressionError("The zstd frame is incomplete.")
        return out

    def _declared_size(self, data: bytes) -> int | None:
        try:
            size = self._module.get_frame_info(data).decompressed_size
        except self._module.ZstdError as exc:
            raise ZstdDecompressionError(f"The zstd frame header is not readable: {exc}") from exc
        return cast("int | None", size)


_FEED_CHUNK_SIZE: Final = 256
"""Input slice size of the tier-2 decode, in bytes.

One decompressor call decodes every zstd block a slice completes, so the slice
size bounds how far the output can pass the cap before the count catches it.
The smallest zstd block is 4 bytes and decodes to at most 128 KiB, so a slice of
256 bytes bounds the overshoot at 8 MiB, whatever the cap is. Measured worst
case on zstandard 0.25.0: 1.5 MiB per 64 input bytes.
"""


class PackageBackend(ZstdBackend):
    """Tier 2. The ``zstandard`` package, installed from a prebuilt wheel."""

    name = PACKAGE_BACKEND

    def compress(self, data: bytes, level: int) -> bytes:
        compressor = self._module.ZstdCompressor(level=level)
        return cast(bytes, compressor.compress(data))

    def decompress(self, data: bytes, max_output_size: int) -> bytes:
        """Decode one frame in slices, counting the output as it arrives.

        The one-shot call of this package reserves *max_output_size* bytes
        before it decodes, so a few dozen hostile input bytes drive an
        allocation of the whole cap (MEDIUM-3). A decompressor object takes no
        output limit of its own, so this method enforces the limit itself: it
        feeds the frame in :data:`_FEED_CHUNK_SIZE` slices and adds up the
        output between two slices. The frame is refused as soon as the running
        total passes the cap, so the peak allocation follows the real payload,
        not the cap.

        The ``eof`` flag of the decompressor object supplies the completeness
        check, exactly as tier 1 does (LOW-4). It stays false when the input
        ends in the middle of a frame.
        """
        self._reject_declared_size(self._declared_size(data), max_output_size)
        decompressor = self._module.ZstdDecompressor().decompressobj()
        chunks: list[bytes] = []
        produced = 0
        for start in range(0, len(data), _FEED_CHUNK_SIZE):
            try:
                out = cast(bytes, decompressor.decompress(data[start : start + _FEED_CHUNK_SIZE]))
            except self._module.ZstdError as exc:
                raise ZstdDecompressionError(f"The zstd frame is not readable: {exc}") from exc
            produced += len(out)
            if produced > max_output_size:
                raise self._reject_oversize(max_output_size)
            chunks.append(out)
            if decompressor.eof:
                break
        if not decompressor.eof:
            raise ZstdDecompressionError("The zstd frame is incomplete.")
        return b"".join(chunks)

    def _declared_size(self, data: bytes) -> int | None:
        try:
            size = cast(int, self._module.frame_content_size(data))
        except self._module.ZstdError as exc:
            raise ZstdDecompressionError(f"The zstd frame header is not readable: {exc}") from exc
        return None if size < 0 else size


_BACKEND: ZstdBackend | None = None


def _import_module(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def select_backend() -> ZstdBackend:
    """Return the first available backend, tier 1 before tier 2.

    Raise :class:`~eedesigncore.exceptions.ZstdBackendUnavailableError` when
    neither backend imports.
    """
    module = _import_module(STDLIB_BACKEND)
    if module is not None:
        return StdlibBackend(module)
    module = _import_module(PACKAGE_BACKEND)
    if module is not None:
        return PackageBackend(module)
    raise ZstdBackendUnavailableError(_UNAVAILABLE_MESSAGE)


def get_backend() -> ZstdBackend:
    """Return the selected backend and cache it for later calls."""
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = select_backend()
    return _BACKEND


def reset_backend_cache() -> None:
    """Drop the cached backend so the next call runs the selection again."""
    global _BACKEND
    _BACKEND = None


def backend_name() -> str:
    """Return the module name of the selected backend."""
    return get_backend().name


def compress(data: bytes, level: int) -> bytes:
    """Compress *data* into a zstd frame at compression *level*."""
    return get_backend().compress(data, level)


def decompress(data: bytes, max_output_size: int) -> bytes:
    """Decompress the zstd frame *data*, with a hard *max_output_size* limit."""
    return get_backend().decompress(data, max_output_size)
