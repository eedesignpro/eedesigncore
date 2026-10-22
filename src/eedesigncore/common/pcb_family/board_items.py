"""Shared-child PCB-family board items (Tier C: board + footprint).

Tier C -- shared-child: reachable from BOTH ``format(BOARD)`` and
``format(FOOTPRINT)`` (commonality Part 1 table). Zone, Dimension, Table,
ReferenceImage and Barcode share an identical token structure between
footprint-embedded and board-root contexts (ADR-015).
"""

from __future__ import annotations

import base64
import re
from enum import Enum
from typing import Literal, Self

from pydantic import field_validator

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import PtsElement, Stroke, TextAttributes

# Cap on the stored base64 text of a reference image, matching EmbeddedFile
# (ADR-042, operator ruling 2026-07-29).
MAX_IMAGE_DATA_SIZE = 1024 * 1024 * 1024
# The eight-byte PNG file signature (RFC 2083 section 3.1).
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Enums (§8.2)
# ---------------------------------------------------------------------------


class BarcodeKind(str, Enum):
    """Barcode types (§2.9)."""

    code39 = "code39"
    code128 = "code128"
    datamatrix = "datamatrix"
    qr = "qr"
    microqr = "microqr"


class BarcodeEcc(str, Enum):
    """QR/microQR ECC levels."""

    L = "L"
    M = "M"
    Q = "Q"
    H = "H"


class DimensionType(Enum):
    """Dimension sub-type discriminator (§2.8).

    Not a str Enum because 'center' conflicts with str.center method.
    Use .value to get the serialization token.
    """

    aligned = "aligned"
    orthogonal = "orthogonal"
    leader = "leader"
    center = "center"
    radial = "radial"


class ArrowDirection(str, Enum):
    """Arrow direction for aligned/orthogonal dimensions."""

    inward = "inward"
    outward = "outward"


# ---------------------------------------------------------------------------
# ReferenceImage (§2.6)
# ---------------------------------------------------------------------------


class ReferenceImage(KicadModel):
    """``(image ...)`` embedded reference image (PCB_REFERENCE_IMAGE, §2.6).

    ``data`` is the base64 text of the image file EXACTLY as stored, with no
    compression and no checksum. That is the whole difference from
    ``EmbeddedFile``: ``FormatStreamData`` (kicad_io_utils.cpp:55-74) base64s the
    raw stream from ``BITMAP_BASE::SaveImageData`` (bitmap_base.cpp:213-233) and
    chunks it at 76 characters. Use :meth:`from_png_bytes` to build one from raw
    PNG bytes (GAP-019).
    """

    position: tuple[float, float] = (0.0, 0.0)
    layer: str = "F.Cu"
    scale: float | None = None  # written only when != 1.0, formatted {:g}
    locked: bool = False
    data: str = ""  # base64 data
    id: str | None = None

    @field_validator("data")
    @classmethod
    def _cap_data(cls, v: str) -> str:
        # Match the EmbeddedFile 1 GiB cap and charset to bound memory and to
        # keep an unescapable character out of the quoted chunks (LOW-3).
        if len(v) > MAX_IMAGE_DATA_SIZE:
            raise ValueError("ReferenceImage data exceeds the 1 GiB limit")
        if not re.fullmatch(r"[A-Za-z0-9+/=\n\r\s]*", v):
            raise ValueError("ReferenceImage data contains invalid base64 characters")
        return v

    @classmethod
    def from_png_bytes(
        cls,
        png: bytes,
        *,
        position: tuple[float, float] = (0.0, 0.0),
        layer: str = "F.Cu",
        scale: float | None = None,
        locked: bool = False,
    ) -> Self:
        """Build a reference image from raw PNG bytes (GAP-019).

        The method mirrors the SHAPE of ``EmbeddedFile.from_bytes``, not its
        pipeline. A reference image stores plain base64 of the image file, so
        there is NO zstd step and NO MurmurHash3 checksum. KiCad writes the
        stream from ``BITMAP_BASE::SaveImageData`` straight through
        ``wxBase64Encode`` (kicad_io_utils.cpp:59). The writer re-chunks the text
        into quoted 76-character tokens on save.

        *png* must start with the eight-byte PNG signature. KiCad also reads
        JPEG (``bitmap_base.cpp:219``); a JPEG caller assigns the base64 text to
        ``data`` directly.

        Raises:
            ValueError: if *png* does not carry the PNG signature.
        """
        if not png.startswith(_PNG_SIGNATURE):
            raise ValueError(
                "from_png_bytes needs PNG data. The bytes do not start with the "
                "PNG signature. KiCad also reads JPEG, which this method does "
                "not build: set data to the base64 text instead."
            )
        return cls(
            position=position,
            layer=layer,
            scale=scale,
            locked=locked,
            data=base64.b64encode(png).decode("ascii"),
        )


# ---------------------------------------------------------------------------
# Zone sub-models (§2.7)
# ---------------------------------------------------------------------------


class ZoneKeepout(KicadModel):
    """Keepout rules sub-block inside a zone."""

    tracks: bool = True
    vias: bool = True
    pads: bool = True
    copper_pour: bool = True
    footprints: bool = True
    zone_placement: bool | None = None


class ZonePlacement(KicadModel):
    """Placement constraint sub-block inside a rule-area zone.

    KiCad emits ``(placement (enabled yes|no) (<source_type> "source"))`` where
    ``source_type`` is one of ``sheetname`` (default), ``component_class`` or
    ``group`` (``design_block`` is transitory and never serialized).  The
    source-type token is always written for a saved rule area (even with an
    empty source, e.g. ``(sheetname "")``).
    """

    enabled: bool = False
    source_type: Literal["sheetname", "component_class", "group"] = "sheetname"
    source: str = ""


class ZoneFill(KicadModel):
    """Fill settings inside a zone (§2.7)."""

    filled: bool = False
    mode: str | None = None  # "hatch" when set
    thermal_gap: float = 0.0
    thermal_bridge_width: float = 0.0
    smoothing: Literal["chamfer", "fillet"] | None = None
    radius: float | None = None
    island_removal_mode: int = 0
    island_area_min: float | None = None
    hatch_thickness: float | None = None
    hatch_gap: float | None = None
    hatch_orientation: float | None = None
    hatch_smoothing_level: int | None = None
    hatch_smoothing_value: float | None = None
    hatch_border_algorithm: Literal["hatch_thickness", "min_thickness"] | None = None
    hatch_min_hole_area: float | None = None


class ZoneLayerProperty(KicadModel):
    """Per-layer property block inside a zone.

    KiCad emits ``(property (layer "...") (hatch_position (xy X Y)))`` and only
    when the layer has a hatching offset (ZONE_LAYER_PROPERTIES::hatching_offset,
    pcb_io_kicad_sexpr.cpp:3094-3110). The legacy ``filled_areas_thickness`` token
    has zero emit sites in the 10.0.3 writer and was removed.
    """

    layer: str = ""
    hatch_position: tuple[float, float] | None = None


class ZoneFilledPolygon(KicadModel):
    """A filled_polygon block inside a zone."""

    layer: str = ""
    island: bool = False
    pts: list[PtsElement] = []


class ZoneOutlinePolygon(KicadModel):
    """An outline polygon block inside a zone."""

    pts: list[PtsElement] = []


class Zone(KicadModel):
    """A ``(zone ...)`` footprint-embedded copper zone or rule area (§2.7).

    The token structure is identical for footprint-embedded zones and board zones
    per ADR-015.
    """

    net: int | None = None  # netcode; only for Cu non-rule-area
    net_name: str | None = None
    locked: bool = False
    layer: str | None = None
    layers: list[str] | None = None  # when multi-layer
    id: str | None = None
    name: str | None = None
    hatch_style: Literal["none", "edge", "full"] = "none"
    hatch_pitch: float = 0.508
    priority: int | None = None
    teardrop_type: Literal["padvia", "track_end"] | None = None
    connect_pads_mode: Literal["thru_hole_only", "yes", "no"] | None = None
    connect_pads_clearance: float = 0.0
    min_thickness: float | None = None
    filled_areas_thickness: bool | None = None
    keepout: ZoneKeepout | None = None
    placement: ZonePlacement | None = None
    fill: ZoneFill = ZoneFill()
    layer_properties: list[ZoneLayerProperty] = []
    polygons: list[ZoneOutlinePolygon] = []
    filled_polygons: list[ZoneFilledPolygon] = []


# ---------------------------------------------------------------------------
# Barcode (§2.9)
# ---------------------------------------------------------------------------


class Barcode(KicadModel):
    """``(barcode ...)`` PCB barcode item (§2.9)."""

    locked: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    layer: str = "F.Cu"
    size: tuple[float, float] = (0.0, 0.0)
    text: str = ""
    text_height: float = 0.0
    barcode_type: BarcodeKind = BarcodeKind.qr
    ecc_level: BarcodeEcc | None = None
    show_text: bool = True  # hide = !ShowText; always written
    knockout: bool = False  # always written
    margins: tuple[float, float] | None = None
    id: str | None = None


# ---------------------------------------------------------------------------
# Table (§2.10)
# ---------------------------------------------------------------------------


class TableCell(KicadModel):
    """A ``(table_cell ...)`` inside a table, rendered as a PCB_TEXTBOX variant.

    Cells use the textbox rectangle grammar ``(start)/(end)`` (never ``(size)``),
    with ``(locked)`` immediately after the content, matching KiCad's
    ``format(PCB_TEXTBOX)`` cell path (pcb_io_kicad_sexpr.cpp:2319-2388).
    """

    content: str = ""
    start: tuple[float, float] = (0.0, 0.0)
    end: tuple[float, float] = (0.0, 0.0)
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    span: tuple[int, int] | None = None  # (cols, rows) span
    angle: float = 0.0
    locked: bool = False
    layer: str = ""
    id: str | None = None
    attributes: TextAttributes | None = None


class TableBorder(KicadModel):
    """Table border settings."""

    external: bool = True
    header: bool = False
    stroke: Stroke | None = None


class TableSeparators(KicadModel):
    """Table row/column separator settings."""

    rows: bool = True
    cols: bool = True
    stroke: Stroke | None = None


class Table(KicadModel):
    """``(table ...)`` PCB table item (PCB_TABLE, §2.10)."""

    column_count: int = 0
    id: str | None = None
    locked: bool = False
    layer: str = ""
    border: TableBorder = TableBorder()
    separators: TableSeparators = TableSeparators()
    column_widths: list[float] = []
    row_heights: list[float] = []
    cells: list[TableCell] = []


# ---------------------------------------------------------------------------
# Dimension (§2.8)
# ---------------------------------------------------------------------------


class DimensionFormat(KicadModel):
    """Format sub-block for a dimension."""

    prefix: str = ""
    suffix: str = ""
    units: int = 3
    units_format: int = 1
    precision: int = 4
    override_value: str | None = None
    suppress_zeroes: bool = False


class DimensionStyle(KicadModel):
    """Style sub-block for a dimension."""

    thickness: float = 0.0
    arrow_length: float = 0.0
    text_position_mode: int = 0
    arrow_direction: ArrowDirection | None = None
    extension_height: float | None = None
    text_frame: int | None = None
    extension_offset: float = 0.0
    keep_text_aligned: bool = False


class DimensionText(KicadModel):
    """The embedded gr_text element of a dimension."""

    content: str = ""
    locked: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    angle: float = 0.0
    layer: str = ""
    knockout: bool = False
    unlocked: bool = False
    visible: bool = True
    id: str | None = None
    attributes: TextAttributes | None = None
    render_cache: object | None = None


class Dimension(KicadModel):
    """A ``(dimension ...)`` PCB dimension item (§2.8).

    Conditional tokens per type:
    - height: aligned, orthogonal only
    - leader_length: radial only
    - orientation: orthogonal only
    - format: all except center
    - arrow_direction in style: aligned/orthogonal only
    - extension_height in style: aligned only
    - text_frame in style: leader only
    - trailing gr_text: all except center
    """

    dim_type: DimensionType = DimensionType.aligned
    locked: bool = False
    layer: str = "F.Cu"
    id: str | None = None
    pts: list[PtsElement] = []
    height: float | None = None
    leader_length: float | None = None
    orientation: int | None = None
    dim_format: DimensionFormat | None = None
    style: DimensionStyle = DimensionStyle()
    text: DimensionText | None = None


__all__ = [
    "BarcodeKind",
    "BarcodeEcc",
    "DimensionType",
    "ArrowDirection",
    "ReferenceImage",
    "ZoneKeepout",
    "ZonePlacement",
    "ZoneFill",
    "ZoneLayerProperty",
    "ZoneFilledPolygon",
    "ZoneOutlinePolygon",
    "Zone",
    "Barcode",
    "TableCell",
    "TableBorder",
    "TableSeparators",
    "Table",
    "DimensionFormat",
    "DimensionStyle",
    "DimensionText",
    "Dimension",
]
