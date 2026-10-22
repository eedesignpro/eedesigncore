"""Resolve a template into page graphics without changing the template."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from eedesigncore.common.font import default_font
from eedesigncore.common.font.stroke import pen_size_for_bold
from eedesigncore.common.kimath import rotate_point_iu
from eedesigncore.common.models import Fill, FillType, JustifyH, JustifyV, Stroke, TextAttributes
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.sch_family.shapes import SchPolyline, SchRect
from eedesigncore.render.document import ViewBox
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.models import Image, Schematic, SchText
from eedesigncore.text_resolution import (
    ResolvedText,
    TextDiagnostic,
    TextResolutionContext,
    resolve_text,
)
from eedesigncore.wks.models import DrawingSheet, DrawingSheetCoordinate
from eedesigncore.wks.text import legacy_text

_SIZES = {
    "A5": (210.0, 148.0),
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "A": (279.4, 215.9),
    "B": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9),
    "USLegal": (355.6, 215.9),
    "USLedger": (431.8, 279.4),
    "GERBER": (812.8, 812.8),
}


@dataclass(frozen=True)
class DrawingSheetGraphics:
    """Page graphics and source text records, suitable for an in-memory viewer."""

    schematic: Schematic
    view_box: ViewBox
    texts: dict[str, ResolvedText]
    item_paths: dict[str, str]

    @property
    def complete(self) -> bool:
        return all(text.complete for text in self.texts.values())


def _display_escapes(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        if text[index : index + 2] in ("\\\\", "\\n"):
            result.append("\\" if text[index + 1] == "\\" else "\n")
            index += 2
        else:
            result.append(text[index])
            index += 1
    return "".join(result)


def drawing_sheet_graphics(
    sheet: DrawingSheet,
    *,
    paper: Paper | None = None,
    title_block: TitleBlock | None = None,
    text_context: TextResolutionContext | None = None,
    first_page: bool = True,
    layer: str = "",
    kicad_version: str | None = None,
    default_line_width: float = 0.1524,
    font: SymbolFont | None = None,
    max_items: int = 100000,
) -> DrawingSheetGraphics:
    sheet = DrawingSheet.model_validate(sheet.model_dump())
    page = paper or Paper()
    if page.paper_type == "User":
        if page.width is None or page.height is None:
            raise ValueError("Custom paper needs width and height.")
        width, height = page.width, page.height
    else:
        if page.paper_type not in _SIZES:
            raise ValueError("Unknown drawing-sheet paper size.")
        width, height = (math.floor(v / 0.0254 + 0.5) * 0.0254 for v in _SIZES[page.paper_type])
        if page.portrait:
            width, height = height, width
    if not all(math.isfinite(v) and v > 0 for v in (width, height, default_line_width)):
        raise ValueError("Page dimensions and default line width must be finite and positive.")
    if (
        max_items < 1
        or sum(item.repeat * max(1, len(item.polygons)) for item in sheet.items) > max_items
    ):
        raise ValueError("Drawing-sheet repetition exceeds the item limit.")
    setup = sheet.setup
    left, top, right, bottom = (
        setup.left_margin,
        setup.top_margin,
        width - setup.right_margin,
        height - setup.bottom_margin,
    )
    if left >= right or top >= bottom:
        raise ValueError("Drawing-sheet margins leave no page area.")

    def position(
        point: DrawingSheetCoordinate, index: int, increment: tuple[float, float]
    ) -> tuple[float, float]:
        x, y = point.x + increment[0] * index, point.y + increment[1] * index
        return (
            left + x if point.anchor.startswith("l") else right - x,
            top + y if point.anchor[1] == "t" else bottom - y,
        )

    def inside(points: list[tuple[float, float]]) -> bool:
        return all(left <= x <= right and top <= y <= bottom for x, y in points)

    title = title_block or TitleBlock()
    variables = {
        "TITLE": title.title,
        "ISSUE_DATE": title.date,
        "REVISION": title.rev,
        "COMPANY": title.company,
        "PAPER": page.paper_type,
        "LAYER": layer,
        **{f"COMMENT{i}": title.comments.get(i, "") for i in range(1, 10)},
    }
    if kicad_version is not None:
        variables["KICAD_VERSION"] = kicad_version
    ctx = text_context or TextResolutionContext(
        page="1", page_count=1, filename="", sheet_name="", sheet_path=""
    )
    if ctx.filename == "":
        variables.update(FILENAME="", FILEPATH="")
    ctx = replace(
        ctx,
        variables={**(ctx.project.text_variables if ctx.project else {}), **ctx.variables},
        scopes=(variables, *ctx.scopes),
    )
    graphics = Schematic(paper=page.model_copy(deep=True))
    records: dict[str, ResolvedText] = {}
    paths: dict[str, str] = {}
    for number, item in enumerate(sheet.items):
        if (item.page_option == "page1only" and not first_page) or (
            item.page_option == "notonpage1" and first_page
        ):
            continue
        raw = item.text
        base_text = legacy_text(raw, sheet.version)
        result = resolve_text(base_text, ctx)
        shown = _display_escapes(result.resolved)
        multiline = "\n" in shown
        size = tuple(a or b for a, b in zip(item.text_size, setup.text_size, strict=True))
        if any(v <= 0 for v in size):
            raise ValueError("Drawing-sheet text sizes must be positive.")
        if item.kind == "tbtext" and (item.max_length or item.max_height):
            metrics = getattr(font or default_font(), "text_box", None)
            if not callable(metrics):
                raise ValueError("Constrained drawing-sheet text requires font text_box support.")
            box = metrics(
                shown,
                (round(size[0] * 1000), round(size[1] * 1000)),
                0,
                bold=item.bold,
                italic=item.italic,
            )
            measured = (math.floor(box.width / 1000 + 0.5), math.floor(box.height / 1000 + 0.5))
            size = tuple(
                s * limit / d if limit > 0 and d > limit else s
                for s, limit, d in zip(
                    size, (item.max_length, item.max_height), measured, strict=True
                )
            )
        for index in range(item.repeat):
            start, end = (
                position(item.position, index, item.increment),
                position(item.end, index, item.increment),
            )
            polygons = []
            angle = math.radians(item.rotation)
            for contour in item.polygons:
                polygons.append(
                    [
                        (
                            start[0] + x * math.cos(angle) + y * math.sin(angle),
                            start[1] - x * math.sin(angle) + y * math.cos(angle),
                        )
                        for x, y in contour
                    ]
                )
            check = [start, end]
            if item.kind == "polygon":
                bounds = [
                    rotate_point_iu((int(x), int(y)), item.rotation)
                    for contour in item.polygons
                    for x, y in contour
                ]
                check = [(start[0] + x, start[1] + y) for x, y in bounds] or [start]
            if index and not inside(check):
                continue
            path = f"$.items[{number}].repeat[{index}]"
            stroke = Stroke(width=max(item.line_width or setup.line_width, default_line_width))
            if item.kind in ("line", "rect"):
                paths[f"$.graphic_shapes[{len(graphics.graphic_shapes)}]"] = path
                graphics.graphic_shapes.append(
                    SchRect(start=start, end=end, stroke=stroke)
                    if item.kind == "rect"
                    else SchPolyline(pts=[start, end], stroke=stroke)
                )
            elif item.kind == "polygon":
                for contour in polygons:
                    if len(contour) < 3:
                        raise ValueError("A drawing-sheet polygon needs at least three corners.")
                    paths[f"$.graphic_shapes[{len(graphics.graphic_shapes)}]"] = path
                    graphics.graphic_shapes.append(
                        SchPolyline(
                            pts=[*contour, contour[0]],
                            stroke=Stroke(width=max(item.line_width, default_line_width)),
                            fill=Fill(type=FillType.outline),
                        )
                    )
            elif item.kind == "bitmap":
                if item.image_data:
                    paths[f"$.images[{len(graphics.images)}]"] = path
                    graphics.images.append(
                        Image(position=start, scale=item.image_scale, data=item.image_data)
                    )
            else:
                attrs = TextAttributes(
                    font_size=(size[0], size[1]),
                    font_thickness=max(
                        pen_size_for_bold(round(min(size) * 1e6)) / 1e6
                        if item.bold
                        else item.line_width or setup.text_line_width,
                        default_line_width,
                    ),
                    bold=item.bold,
                    italic=item.italic,
                    font_face=item.font_face,
                    justify_h=JustifyH(item.justify_h),
                    justify_v=JustifyV(item.justify_v),
                )
                if item.color:
                    attrs.color = item.color.model_copy()
                paths[f"$.texts[{len(graphics.texts)}]"] = path
                graphics.texts.append(
                    SchText(content=shown, position=start, angle=item.rotation, attributes=attrs)
                )
                diagnostics = list(result.diagnostics) if index == 0 else []
                if index and ("${" in shown or "@{" in shown):
                    diagnostics.append(
                        TextDiagnostic(
                            code="missing_context",
                            token=shown,
                            message="KiCad repeats the base label without resolving it again.",
                        )
                    )
                records[path + ".text"] = ResolvedText(
                    raw=raw, resolved=shown, diagnostics=diagnostics
                )
                if item.repeat > 1 and not multiline and raw:
                    last = base_text[-1]
                    shown = base_text[:-1] + (
                        str(int(last) + (index + 1) * item.increment_label)
                        if last in "0123456789"
                        else chr(ord(last) + (index + 1) * item.increment_label)
                    )
    return DrawingSheetGraphics(graphics, ViewBox(0, 0, width, height), records, paths)
