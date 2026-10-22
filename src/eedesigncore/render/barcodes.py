"""KiCad barcode shapes from the in-memory Zint encoder supplied by ZXing."""

from __future__ import annotations

import math
from dataclasses import replace

import zxingcpp

from eedesigncore.common.font import default_font
from eedesigncore.common.models import TextAttributes
from eedesigncore.common.pcb_family.board_items import Barcode
from eedesigncore.common.transform import point_to_iu
from eedesigncore.render.filled_geometry import (
    Polygon,
    difference,
    polygon_shape,
    rectangle_polygon,
    stroke_polygons,
)
from eedesigncore.render.geometry import PointMap, SvgShape, text_shapes
from eedesigncore.render.text_boxes import text_pen
from eedesigncore.render.traversal import identity_map


def barcode_shapes(
    item: Barcode, to_board: PointMap, precision: int, font: object | None
) -> list[SvgShape]:
    if not item.text:
        return []
    if any(not math.isfinite(value) or value <= 0 for value in item.size):
        raise ValueError("Barcode dimensions must be finite and positive.")
    formats = {
        "code39": zxingcpp.BarcodeFormat.Code39,
        "code128": zxingcpp.BarcodeFormat.Code128,
        "datamatrix": zxingcpp.BarcodeFormat.DataMatrix,
        "qr": zxingcpp.BarcodeFormat.QRCode,
        "microqr": zxingcpp.BarcodeFormat.MicroQRCode,
    }
    options = (
        {"ec_level": item.ecc_level.value if item.ecc_level else "L"}
        if item.barcode_type.value in ("qr", "microqr")
        else {}
    )
    encoded = zxingcpp.create_barcode(item.text, formats[item.barcode_type.value], **options)
    pixels = memoryview(encoded.to_image(add_quiet_zones=False, add_hrt=False))
    if pixels.shape is None or len(pixels.shape) != 2:
        raise ValueError("The barcode encoder returned an invalid image.")
    height, width = pixels.shape
    scale_x, scale_y = item.size[0] / width, item.size[1] / height
    origin = (-item.size[0] / 2, -item.size[1] / 2)
    active: dict[tuple[int, int], int] = {}
    polygons: list[Polygon] = []
    for row in range(height + 1):
        runs: set[tuple[int, int]] = set()
        if row < height:
            column = 0
            while column < width:
                if pixels[row, column] != 0:
                    column += 1
                    continue
                start = column
                while column < width and pixels[row, column] == 0:
                    column += 1
                runs.add((start, column))
        for run, start_row in list(active.items()):
            if run not in runs:
                polygons.append(
                    rectangle_polygon(
                        (origin[0] + run[0] * scale_x, origin[1] + start_row * scale_y),
                        (origin[0] + run[1] * scale_x, origin[1] + row * scale_y),
                    )
                )
                del active[run]
        for run in sorted(runs):
            active.setdefault(run, row)
    if item.show_text and item.text_height > 0:
        attrs = TextAttributes(font_size=(item.text_height, item.text_height))
        pen = text_pen(attrs)
        attrs = attrs.model_copy(update={"font_thickness": pen})
        glyphs = [
            replace(shape, stroke_width_mm=pen)
            for shape in text_shapes(
                item.text, (0, 0), 0, attrs, identity_map, 6, font or default_font()
            )
        ]
        text_polygons = stroke_polygons(glyphs)
        if text_polygons:
            left = min(p[0] for poly in text_polygons for p in poly)
            right = max(p[0] for poly in text_polygons for p in poly)
            top = min(p[1] for poly in text_polygons for p in poly)
            offset_x = -(left + right) // 2
            offset_y = point_to_iu((item.size[1] / 2 + 1, 0))[0] - top
            polygons.extend(
                [[(x + offset_x, y + offset_y) for x, y in poly] for poly in text_polygons]
            )
    if item.knockout and polygons:
        left = min(p[0] for poly in polygons for p in poly)
        right = max(p[0] for poly in polygons for p in poly)
        top = min(p[1] for poly in polygons for p in poly)
        bottom = max(p[1] for poly in polygons for p in poly)
        minimum = math.ceil(min(item.size) * 0.1 / 0.1) * 0.1
        margins = item.margins or (0, 0)
        margin_x, margin_y = point_to_iu((max(minimum, margins[0]), max(minimum, margins[1])))
        background = [
            (left - margin_x, top - margin_y),
            (right + margin_x, top - margin_y),
            (right + margin_x, bottom + margin_y),
            (left - margin_x, bottom + margin_y),
        ]
        polygons = difference([background], polygons)
    radians = math.radians(item.angle)

    def position(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        if item.layer.startswith("B."):
            x = -x
        return to_board(
            (
                item.position[0] + x * math.cos(radians) + y * math.sin(radians),
                item.position[1] - x * math.sin(radians) + y * math.cos(radians),
            )
        )

    return [polygon_shape(polygons, position, precision)] if polygons else []
