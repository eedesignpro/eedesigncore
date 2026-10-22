"""Additional board drawings above the frozen geometry layer."""

from __future__ import annotations

import math

from eedesigncore.common.pcb_family.board_items import Barcode, ReferenceImage, Table
from eedesigncore.common.pcb_family.shapes import PcbCircle, PcbCurve, PcbPoly, PcbRect
from eedesigncore.pcb.models import PcbTarget
from eedesigncore.render.barcodes import barcode_shapes
from eedesigncore.render.geometry import PointMap, SvgShape, format_mm
from eedesigncore.render.images import image_shape
from eedesigncore.render.tables import pcb_table_shapes


def extra_shapes(
    item: object,
    to_board: PointMap,
    precision: int,
    font: object | None,
    *,
    dash_ratio: float = 12.0,
    gap_ratio: float = 3.0,
) -> list[SvgShape] | None:
    def point(x: float, y: float) -> str:
        x, y = to_board((x, y))
        return f"{format_mm(x, precision)},{format_mm(y, precision)}"

    from eedesigncore.common.models import Fill, FillType
    from eedesigncore.render.geometry import shapes_for
    from eedesigncore.render.hatching import HATCHES, hatch_shapes

    if isinstance(item, PcbCircle) and item.fill.type == FillType.outline:
        center, edge = to_board(item.center), to_board(item.end)
        radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
        # KiCad PlotShape includes the outline width in a solid circle's diameter.
        radius += max(0.0, item.stroke.width) / 2
        return [
            SvgShape(
                "circle",
                (
                    ("cx", format_mm(center[0], precision)),
                    ("cy", format_mm(center[1], precision)),
                    ("r", format_mm(radius, precision)),
                ),
                filled=True,
            )
        ]
    if isinstance(item, PcbRect | PcbCircle | PcbPoly) and item.fill.type in HATCHES:
        outline = item.model_copy(update={"fill": Fill(type=FillType.none)})
        return (
            shapes_for(outline, to_board, precision=precision, font=font) or []
        ) + hatch_shapes(item, to_board, precision, item.stroke.width)
    from eedesigncore.common.models import Property
    from eedesigncore.common.pcb_family.shapes import PcbText
    from eedesigncore.render.board_text import pcb_text_shapes

    if isinstance(item, PcbText | Property):
        return pcb_text_shapes(item, to_board, precision, font)
    if isinstance(item, Barcode):
        return barcode_shapes(item, to_board, precision, font)
    if isinstance(item, PcbCurve):
        if not all(isinstance(p, tuple) for p in item.pts):
            raise ValueError("Bezier curves require four plain control points.")
        pts = [p for p in item.pts if isinstance(p, tuple)]
        data = f"M{point(*pts[0])} C{point(*pts[1])} {point(*pts[2])} {point(*pts[3])}"
        return [SvgShape("path", (("d", data),))]
    if isinstance(item, ReferenceImage):
        return [image_shape(item.data, item.position, item.scale, to_board, precision)]
    if isinstance(item, Table):
        return pcb_table_shapes(
            item, to_board, precision, font, dash_ratio=dash_ratio, gap_ratio=gap_ratio
        )
    if isinstance(item, PcbTarget):
        x, y = to_board(item.position)
        radius = item.size / (2 if item.is_x else 3)
        shapes = [
            SvgShape(
                "circle",
                (
                    ("cx", format_mm(x, precision)),
                    ("cy", format_mm(y, precision)),
                    ("r", format_mm(radius, precision)),
                ),
                stroke_width_mm=item.width,
            )
        ]
        x, y = item.position
        half = item.size / 2
        lines = (
            [
                ((x - half, y - half), (x + half, y + half)),
                ((x - half, y + half), (x + half, y - half)),
            ]
            if item.is_x
            else [((x - half, y), (x + half, y)), ((x, y - half), (x, y + half))]
        )
        for start, end in lines:
            shapes.append(
                SvgShape(
                    "path",
                    (("d", f"M{point(*start)} L{point(*end)}"),),
                    stroke_width_mm=item.width,
                )
            )
        return shapes
    return None
