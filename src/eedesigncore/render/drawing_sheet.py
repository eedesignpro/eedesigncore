"""Drawing-sheet SVG from an editable in-memory template."""

from __future__ import annotations

from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.render.document import ViewBox, build_document
from eedesigncore.render.images import image_shape
from eedesigncore.render.schematic_symbols import map_graphic
from eedesigncore.render.symbols import SymbolFont, SymbolStyle, _SymbolDrawing
from eedesigncore.render.traversal import identity_map
from eedesigncore.text_resolution import TextResolutionContext
from eedesigncore.wks.layout import DrawingSheetGraphics, drawing_sheet_graphics
from eedesigncore.wks.models import DrawingSheet


def render_drawing_sheet_svg(
    sheet: DrawingSheet | DrawingSheetGraphics,
    *,
    paper: Paper | None = None,
    title_block: TitleBlock | None = None,
    text_context: TextResolutionContext | None = None,
    first_page: bool = True,
    layer: str = "",
    kicad_version: str | None = None,
    color: str = "#000000",
    font: SymbolFont | None = None,
    view_box: ViewBox | None = None,
    precision: int = 4,
    default_line_width: float = 0.1524,
) -> str:
    """Render page graphics without file access or a running KiCad process.

    Pass prepared graphics to retain raw/resolved text and avoid resolving twice.
    A supplied view box permits composition with a board or schematic layer.
    """
    prepared = (
        sheet
        if isinstance(sheet, DrawingSheetGraphics)
        else drawing_sheet_graphics(
            sheet,
            paper=paper,
            title_block=title_block,
            text_context=text_context,
            first_page=first_page,
            layer=layer,
            kicad_version=kicad_version,
            font=font,
            default_line_width=default_line_width,
        )
    )
    drawing = _SymbolDrawing(
        SymbolStyle(body=color, text=color, fields=color, line_width_mm=default_line_width),
        precision,
        font,
    )
    for image in prepared.schematic.images:
        drawing.element(
            image_shape(image.data, image.position, image.scale, identity_map, precision),
            color="none",
            background=True,
        )
    # Mapping insertion order retains template order across text and graphics.
    for path in prepared.item_paths:
        index = int(path.rsplit("[", 1)[1][:-1])
        if path.startswith("$.graphic_shapes["):
            graphic = prepared.schematic.graphic_shapes[index]
            drawing.graphic(map_graphic(graphic, lambda p: (p[0], -p[1])))
        elif path.startswith("$.texts["):
            text = prepared.schematic.texts[index]
            drawing.text(
                text.content,
                text.position,
                text.angle,
                text.attributes,
                color,
                minimum_line_width=default_line_width,
            )
    return build_document(
        view_box or prepared.view_box, drawing.background + drawing.foreground, precision
    )
