"""Common Pydantic v2 models shared across KiCad file formats."""

from __future__ import annotations

import base64
import binascii
import re
from enum import Enum
from typing import Self

from pydantic import field_validator

from eedesigncore.common import _zstd
from eedesigncore.common.base import KicadModel
from eedesigncore.exceptions import EmbeddedFileChecksumError, KicadParseError

# MurmurHash3 x64_128 constants (KiCad libs/kimath/include/mmh3_hash.h).
_MMH3_MASK64 = (1 << 64) - 1
_MMH3_C1 = 0x87C37B91114253D5
_MMH3_C2 = 0x4CF5AD432745937F
# EMBEDDED_FILES::Seed() == 0xABBA2345 (embedded_files.h:263-266).
EMBEDDED_FILES_SEED = 0xABBA2345
# ZSTD compression level KiCad uses (embedded_files.cpp:251).
_ZSTD_LEVEL = 15
# Cap on the stored base64 text of an embedded file. A 1 GiB STEP model is a
# legitimate embedded file, so both caps sit at 1 GiB (ADR-042, operator ruling
# 2026-07-29). The caps are denial-of-service guards, not correctness bounds.
MAX_EMBEDDED_DATA_SIZE = 1024 * 1024 * 1024
# Cap on the decoded content of an embedded file. It guards the decode path
# against a decompression bomb and matches the stored-text cap above (ADR-042).
MAX_DECOMPRESSED_SIZE = MAX_EMBEDDED_DATA_SIZE


def _mmh3_128_kicad(data: bytes, seed: int = EMBEDDED_FILES_SEED) -> str:
    """Port of KiCad's ``MMH3_HASH`` digest over *data*, as an uppercase hex string.

    This reproduces the STREAMING MurmurHash3 x64_128 in KiCad's
    ``mmh3_hash.h`` exactly, including its length-accounting quirk (the trailing
    partial block is zero-padded to a multiple of four and that padded length -
    not the true byte length - feeds the finalizer). A stock ``mmh3`` library
    would diverge for inputs whose length is not a multiple of 16. Verified to
    reproduce a real corpus embedded-file checksum
    (``CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod``). The result is formatted
    like ``HASH_128::ToString`` (``hash_128.h``): two 64-bit words, each 16
    uppercase hex digits.
    """
    h1 = seed & _MMH3_MASK64
    h2 = seed & _MMH3_MASK64
    length = 0
    pos = 0
    n = len(data)

    while n - pos >= 16:
        k1 = int.from_bytes(data[pos : pos + 8], "little")
        k2 = int.from_bytes(data[pos + 8 : pos + 16], "little")
        k1 = (k1 * _MMH3_C1) & _MMH3_MASK64
        k1 = _mmh3_rotl64(k1, 31)
        k1 = (k1 * _MMH3_C2) & _MMH3_MASK64
        h1 ^= k1
        h1 = _mmh3_rotl64(h1, 27)
        h1 = (h1 + h2) & _MMH3_MASK64
        h1 = (h1 * 5 + 0x52DCE729) & _MMH3_MASK64
        k2 = (k2 * _MMH3_C2) & _MMH3_MASK64
        k2 = _mmh3_rotl64(k2, 33)
        k2 = (k2 * _MMH3_C1) & _MMH3_MASK64
        h2 ^= k2
        h2 = _mmh3_rotl64(h2, 31)
        h2 = (h2 + h1) & _MMH3_MASK64
        h2 = (h2 * 5 + 0x38495AB5) & _MMH3_MASK64
        pos += 16
        length += 16

    remaining = n - pos
    tail = bytearray(16)
    if remaining > 0:
        tail[0:remaining] = data[pos : pos + remaining]
        padding = 4 - (remaining + 4) % 4
        length += remaining + padding

    tail_len = length & 15
    k1 = 0
    k2 = 0
    if tail_len >= 15:
        k2 ^= tail[14] << 48
    if tail_len >= 14:
        k2 ^= tail[13] << 40
    if tail_len >= 13:
        k2 ^= tail[12] << 32
    if tail_len >= 12:
        k2 ^= tail[11] << 24
    if tail_len >= 11:
        k2 ^= tail[10] << 16
    if tail_len >= 10:
        k2 ^= tail[9] << 8
    if tail_len >= 9:
        k2 ^= tail[8]
        k2 = (k2 * _MMH3_C2) & _MMH3_MASK64
        k2 = _mmh3_rotl64(k2, 33)
        k2 = (k2 * _MMH3_C1) & _MMH3_MASK64
        h2 ^= k2
    if tail_len >= 8:
        k1 ^= tail[7] << 56
    if tail_len >= 7:
        k1 ^= tail[6] << 48
    if tail_len >= 6:
        k1 ^= tail[5] << 40
    if tail_len >= 5:
        k1 ^= tail[4] << 32
    if tail_len >= 4:
        k1 ^= tail[3] << 24
    if tail_len >= 3:
        k1 ^= tail[2] << 16
    if tail_len >= 2:
        k1 ^= tail[1] << 8
    if tail_len >= 1:
        k1 ^= tail[0]
        k1 = (k1 * _MMH3_C1) & _MMH3_MASK64
        k1 = _mmh3_rotl64(k1, 31)
        k1 = (k1 * _MMH3_C2) & _MMH3_MASK64
        h1 ^= k1

    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & _MMH3_MASK64
    h2 = (h2 + h1) & _MMH3_MASK64
    h1 = _mmh3_fmix64(h1)
    h2 = _mmh3_fmix64(h2)
    h1 = (h1 + h2) & _MMH3_MASK64
    h2 = (h2 + h1) & _MMH3_MASK64
    return f"{h1:016X}{h2:016X}"


def _mmh3_rotl64(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & _MMH3_MASK64


def _mmh3_fmix64(k: int) -> int:
    k ^= k >> 33
    k = (k * 0xFF51AFD7ED558CCD) & _MMH3_MASK64
    k ^= k >> 33
    k = (k * 0xC4CEB9FE1A85EC53) & _MMH3_MASK64
    k ^= k >> 33
    return k


class StrokeType(str, Enum):
    """Values for the ``(type ...)`` sub-token inside ``(stroke ...)``.

    Token values match KiCad file format exactly.
    """

    default = "default"
    dash = "dash"
    dash_dot = "dash_dot"
    dash_dot_dot = "dash_dot_dot"
    dot = "dot"
    solid = "solid"


class FillType(str, Enum):
    """Values for the ``(type ...)`` sub-token inside ``(fill ...)``."""

    none = "none"
    outline = "outline"
    background = "background"
    color = "color"
    hatch = "hatch"
    reverse_hatch = "reverse_hatch"
    cross_hatch = "cross_hatch"


class JustifyH(Enum):
    """Horizontal text justification."""

    left = "left"
    right = "right"
    center = "center"


class JustifyV(Enum):
    """Vertical text justification."""

    top = "top"
    bottom = "bottom"
    center = "center"


class Color(KicadModel):
    """RGBA color. r/g/b in 0–255; a in 0.0–1.0."""

    r: int
    g: int
    b: int
    a: float

    @field_validator("r", "g", "b")
    @classmethod
    def validate_rgb(cls, v: int) -> int:
        if not 0 <= v <= 255:
            raise ValueError(f"Color component must be in 0-255, got {v}")
        return v

    @field_validator("a")
    @classmethod
    def validate_alpha(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Alpha must be in 0.0-1.0, got {v}")
        return v


class Stroke(KicadModel):
    """Line stroke style."""

    width: float = 0.0
    type: StrokeType = StrokeType.default
    color: Color | None = None


class Fill(KicadModel):
    """Shape fill style."""

    type: FillType = FillType.none
    color: Color | None = None


class TextAttributes(KicadModel):
    """Text rendering attributes (contents of an ``effects`` block)."""

    font_face: str | None = None
    font_size: tuple[float, float] = (1.27, 1.27)
    font_thickness: float | None = None
    bold: bool = False
    italic: bool = False
    line_spacing: float | None = None
    color: Color | None = None
    justify_h: JustifyH = JustifyH.center
    justify_v: JustifyV = JustifyV.center
    mirror: bool = False
    visible: bool = True
    href: str | None = None  # (href "url") hyperlink; emitted after justify (eda_text.cpp:1116)


class PtsArc(KicadModel):
    """An ``(arc (start …) (mid …) (end …))`` element inside a ``(pts …)`` chain.

    KiCad's ``formatPolyPts`` (pcb_io_kicad_sexpr.cpp:487-519) emits an ``(xy …)``
    per plain point and a single ``(arc …)`` per arc run of a
    ``SHAPE_LINE_CHAIN``; the parser (``parseOutlinePoints`` :389-461) accepts the
    two forms interleaved. A pts chain is therefore an ordered list of
    ``PtsElement`` (``tuple[float, float]`` xy | ``PtsArc``).
    """

    start: tuple[float, float]
    mid: tuple[float, float]
    end: tuple[float, float]


# An element of a (pts …) chain: a plain xy point or an arc.
PtsElement = tuple[float, float] | PtsArc


class RenderCachePolygon(KicadModel):
    """A single polygon in a render_cache block."""

    pts: list[PtsElement] = []


class RenderCache(KicadModel):
    """Pre-rendered outline-font polygon cache on a text item."""

    resolved_text: str = ""
    angle: float = 0.0
    polygons: list[RenderCachePolygon] = []


class Property(KicadModel):
    """A named string field (``property`` token) attached to a KiCad item."""

    private: bool = False
    name: str
    value: str
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    show_name: bool = False
    do_not_autoplace: bool = False
    visible: bool = True
    attributes: TextAttributes | None = None
    # Fields used in PCB/footprint context (not written in sym context)
    layer: str | None = None
    knockout: bool = False
    unlocked: bool = False  # (unlocked yes) = not keep_upright
    id: str | None = None  # uuid
    render_cache: RenderCache | None = None  # PCBnew outline-font cache; None in sym context


class EmbeddedFile(KicadModel):
    """An embedded binary file (font, model, etc.) inside a KiCad file."""

    name: str
    file_type: str
    data: str  # base64-encoded content as stored in the file
    checksum: str

    @field_validator("data")
    @classmethod
    def validate_base64(cls, v: str) -> str:
        if len(v) > MAX_EMBEDDED_DATA_SIZE:
            raise ValueError("EmbeddedFile data exceeds the 1 GiB limit")
        if not re.fullmatch(r"[A-Za-z0-9+/=\n\r\s]*", v):
            raise ValueError("EmbeddedFile data contains invalid base64 characters")
        return v

    @classmethod
    def from_bytes(cls, name: str, data: bytes, *, kind: str = "other") -> Self:
        """Construct a KiCad-loadable embedded file from raw (uncompressed) bytes.

        Reproduces KiCad's ``EMBEDDED_FILES::CompressAndEncode``
        (``embedded_files.cpp:244-274``): zstd-compress (level 15), base64-encode
        the compressed bytes into ``data``, and set ``checksum`` to the
        MurmurHash3 digest (seed ``0xABBA2345``) computed over the **decompressed**
        (raw) bytes. The base64 is stored unchunked; the writer re-chunks it into
        the pipe-delimited ``(data ...)`` form on save.

        A checksum computed any other way makes KiCad throw ``PARSE_ERROR`` and
        abort the entire file load (embedded-files ownership report section 5), so
        the hash MUST match byte-for-byte - which this does (verified against a
        real corpus embedded file). Note: parsed files retain their original
        checksum text verbatim on round-trip; ``from_bytes`` is for NEW
        construction only.

        *kind* is the KiCad file type: ``datasheet``/``font``/``model``/
        ``worksheet``/``other``.

        The zstd frame comes from the tiered backend in ``common/_zstd.py``
        (ADR-042). Both backend tiers write level-15 frames, and the checksum
        runs over the raw bytes, so the backend choice changes neither the
        checksum nor what KiCad reads back.
        """
        compressed = _zstd.compress(data, _ZSTD_LEVEL)
        encoded = base64.b64encode(compressed).decode("ascii")
        checksum = _mmh3_128_kicad(data, EMBEDDED_FILES_SEED)
        return cls(name=name, file_type=kind, data=encoded, checksum=checksum)

    def to_bytes(self, *, max_size: int = MAX_DECOMPRESSED_SIZE) -> bytes:
        """Return the original file content of this embedded file.

        The method reverses :meth:`from_bytes`. It base64-decodes ``data``,
        decompresses the zstd frame through the tiered backend, and verifies the
        MurmurHash3 digest (seed ``0xABBA2345``) of the result against the stored
        ``checksum``. ``to_bytes(from_bytes(raw)) == raw`` holds on both backends.

        *max_size* caps the decoded content at 1 GiB by default, which matches the
        cap on the stored base64 text. The cap stops a decompression bomb before
        it allocates memory (R16). Lower *max_size* for untrusted input, and raise
        it only for a trusted file larger than the cap.

        Raises:
            KicadParseError: if ``data`` is not valid base64.
            ZstdDecompressionError: if the frame is corrupt or over *max_size*.
            EmbeddedFileChecksumError: if the content fails the checksum test.
        """
        try:
            compressed = base64.b64decode("".join(self.data.split()), validate=True)
        except binascii.Error as exc:
            raise KicadParseError(
                f"Embedded file {self.name!r} does not hold valid base64 data: {exc}"
            ) from exc
        raw = _zstd.decompress(compressed, max_size)
        digest = _mmh3_128_kicad(raw, EMBEDDED_FILES_SEED)
        stored = self.checksum.strip()
        if digest.upper() != stored.upper():
            raise EmbeddedFileChecksumError(
                f"Embedded file {self.name!r} fails the checksum test. "
                f"The file stores {stored!r}. The content hashes to {digest!r}."
            )
        return raw
