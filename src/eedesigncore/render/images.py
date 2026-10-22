"""Embed saved PNG and JPEG images without file or network access."""

from __future__ import annotations

import base64
import binascii
import math
import struct

from eedesigncore.render.geometry import Point, PointMap, SvgShape, format_mm


def image_shape(
    data: str, position: Point, scale: float | None, to_board: PointMap, precision: int
) -> SvgShape:
    try:
        payload = base64.b64decode("".join(data.split()), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("The image contains invalid base64 data.") from error
    width, height, ppi, mime = image_dimensions(payload)
    factor = 1.0 if scale is None else scale
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("The image scale must be finite and positive.")
    width_mm, height_mm = width * 25.4 / ppi * factor, height * 25.4 / ppi * factor
    center = to_board(position)
    origin = to_board((position[0] - width_mm / 2, position[1] - height_mm / 2))
    unit_x, unit_y = (
        to_board((position[0] + 1, position[1])),
        to_board((position[0], position[1] + 1)),
    )
    matrix = (
        unit_x[0] - center[0],
        unit_x[1] - center[1],
        unit_y[0] - center[0],
        unit_y[1] - center[1],
        origin[0],
        origin[1],
    )
    return SvgShape(
        "image",
        (
            ("x", "0"),
            ("y", "0"),
            ("width", format_mm(width_mm, precision)),
            ("height", format_mm(height_mm, precision)),
            ("preserveAspectRatio", "none"),
            (
                "transform",
                "matrix(" + " ".join(format_mm(value, precision) for value in matrix) + ")",
            ),
            ("href", f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")),
        ),
    )


def image_dimensions(payload: bytes) -> tuple[int, int, float, str]:
    ppi = 300.0
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        has_pixels = False
        offset, width, height = 8, 0, 0
        ended = False
        while offset + 12 <= len(payload):
            length = int.from_bytes(payload[offset : offset + 4], "big")
            kind = payload[offset + 4 : offset + 8]
            end = offset + 12 + length
            if end > len(payload):
                raise ValueError("The PNG image has a truncated chunk.")
            chunk = payload[offset + 8 : offset + 8 + length]
            expected_crc = int.from_bytes(payload[end - 4 : end], "big")
            if binascii.crc32(kind + chunk) & 0xFFFFFFFF != expected_crc:
                raise ValueError("The PNG image contains an invalid chunk checksum.")
            if kind == b"IDAT" and length:
                has_pixels = True
            if kind == b"IHDR" and length == 13:
                width, height = struct.unpack(">II", chunk[:8])
            elif kind == b"pHYs" and length == 9 and chunk[8] == 1:
                density = int.from_bytes(chunk[:4], "big")
                if density > 39:
                    ppi = float(round(density * 0.0254))
            elif kind == b"IEND":
                ended = True
                break
            offset = end
        if width <= 0 or height <= 0 or not ended or not has_pixels:
            raise ValueError("The PNG image lacks dimensions or its end marker.")
        return width, height, ppi, "image/png"
    if (
        payload.startswith(b"\xff\xd8")
        and payload.endswith(b"\xff\xd9")
        and b"\xff\xda" in payload
    ):
        offset = 2
        while offset + 4 <= len(payload):
            if payload[offset] != 255:
                break
            while offset < len(payload) and payload[offset] == 255:
                offset += 1
            if offset >= len(payload):
                break
            marker = payload[offset]
            offset += 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(payload[offset : offset + 2], "big")
            if length < 2 or offset + length > len(payload):
                break
            chunk = payload[offset + 2 : offset + length]
            if marker == 0xE0 and chunk.startswith(b"JFIF\0") and len(chunk) >= 12:
                density = int.from_bytes(chunk[8:10], "big")
                if density > 1 and chunk[7] in (1, 2):
                    ppi = float(round(density * (2.54 if chunk[7] == 2 else 1)))
            if (
                marker
                in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
                and len(chunk) >= 5
            ):
                height, width = struct.unpack(">HH", chunk[1:5])
                if width and height:
                    return width, height, ppi, "image/jpeg"
            offset += length
    raise ValueError("The saved image must contain a valid PNG or JPEG image.")
