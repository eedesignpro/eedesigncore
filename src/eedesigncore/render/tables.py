"""Saved table cells and borders, including merged cells."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from eedesigncore.common.models import Stroke, TextAttributes
from eedesigncore.common.pcb_family.board_items import Table, TableCell
from eedesigncore.common.pcb_family.shapes import PcbTextBox
from eedesigncore.render.geometry import Point, PointMap, SvgShape, format_mm, stroke_attrs
from eedesigncore.render.text_boxes import draw_symbol_text_box, pcb_text_box_shapes
from eedesigncore.sch.models import SchTable, SchTableCell, SchTextBox

if TYPE_CHECKING:
    from eedesigncore.render.symbols import _SymbolDrawing


def cell_corners(start: Point, end: Point, angle: float) -> list[Point]:
    left, right = sorted((start[0], end[0]))
    top, bottom = sorted((start[1], end[1]))
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    if angle % 90:
        raise ValueError("Table text angles must be multiples of 90 degrees.")
    shift = (-int(angle / 90)) % 4
    return corners[shift:] + corners[:shift]


def layout(table: SchTable | Table) -> list[tuple[Point, Point]]:
    columns = table.column_count
    if not table.cells:
        return []
    if columns <= 0 or len(table.cells) % columns:
        raise ValueError("The table needs a complete rectangular array of cells.")
    rows = len(table.cells) // columns
    if len(table.column_widths) != columns or len(table.row_heights) != rows:
        raise ValueError("Table row heights and column widths must match its cells.")
    cells: Sequence[SchTableCell | TableCell] = table.cells
    boxes = []
    for index, cell in enumerate(cells):
        row, col = divmod(index, columns)
        span = cell.span or (1, 1)
        if min(span) < 0 or col + span[0] > columns or row + span[1] > rows:
            raise ValueError("A table cell span exceeds the table bounds.")
        if isinstance(cell, SchTableCell):
            boxes.append(
                (cell.position, (cell.position[0] + cell.size[0], cell.position[1] + cell.size[1]))
            )
        else:
            boxes.append((cell.start, cell.end))
    return boxes


def borders(
    table: SchTable | Table, boxes: list[tuple[Point, Point]]
) -> Iterator[tuple[Point, Point, Stroke]]:
    if not boxes:
        return
    columns = table.column_count
    rows = len(boxes) // columns
    if isinstance(table, SchTable):
        external, header = table.border_external, table.border_header
        row_lines, col_lines = table.separators_rows, table.separators_cols
        border, separator = table.border_stroke, table.separators_stroke
    else:
        external, header = table.border.external, table.border.header
        row_lines, col_lines = table.separators.rows, table.separators.cols
        border, separator = table.border.stroke or Stroke(), table.separators.stroke or Stroke()
    corners = [cell_corners(start, end, table.cells[0].angle) for start, end in boxes]
    for col in range(columns - 1):
        for row in range(rows):
            span = table.cells[row * columns + col].span or (1, 1)
            if span[0] and col + span[0] != columns and ((row == 0 and header) or col_lines):
                points = corners[row * columns + col]
                yield points[1], points[2], border if row == 0 and header else separator
    for row in range(rows - 1):
        if not ((row == 0 and header) or row_lines):
            continue
        for col in range(columns):
            span = table.cells[row * columns + col].span or (1, 1)
            if isinstance(table, SchTable) and (not span[1] or row + span[1] == rows):
                continue
            points = corners[row * columns + col]
            yield points[2], points[3], border if row == 0 and header else separator
    if external and border.width >= 0:
        outline = [corners[0][0], corners[columns - 1][1], corners[-1][2], corners[-columns][3]]
        for index, start in enumerate(outline):
            yield start, outline[(index + 1) % 4], border


def draw_schematic_table(drawing: _SymbolDrawing, table: SchTable) -> None:
    from eedesigncore.render.symbols import _color

    boxes = layout(table)
    for cell_index, (cell, (start, end)) in enumerate(zip(table.cells, boxes, strict=True)):
        if min(cell.span) <= 0:
            continue
        box = SchTextBox(
            content=cell.content,
            position=start,
            size=(end[0] - start[0], end[1] - start[1]),
            angle=cell.angle,
            margins=cell.margins,
            fill=cell.fill,
            attributes=cell.attributes,
            stroke=Stroke(width=-0.0001),
        )
        previous_role = drawing.measurement_role
        drawing.measurement_role = f"cell:{cell_index}"
        draw_symbol_text_box(drawing, box, start, end, cell.angle)
        drawing.measurement_role = previous_role
    for start, end, stroke in borders(table, boxes):
        color, opacity = _color(stroke.color, drawing.style.body)
        drawing.element(
            SvgShape("path", (("d", f"M{drawing.point(start)} L{drawing.point(end)}"),)),
            color=color,
            opacity=opacity,
            stroke=stroke.model_copy(
                update={"width": max(0.0, stroke.width or drawing.style.line_width_mm)}
            ),
        )


def pcb_table_shapes(
    table: Table,
    to_board: PointMap,
    precision: int,
    font: object | None,
    *,
    dash_ratio: float = 12.0,
    gap_ratio: float = 3.0,
    font_for: Callable[[TextAttributes | None], object | None] | None = None,
) -> list[SvgShape]:
    boxes = layout(table)
    shapes = []
    for cell, (start, end) in zip(table.cells, boxes, strict=True):
        if min(cell.span or (1, 1)) <= 0:
            continue
        box = PcbTextBox(
            content=cell.content,
            start=start,
            end=end,
            angle=cell.angle,
            margins=cell.margins,
            attributes=cell.attributes,
        )
        selected_font = font_for(cell.attributes) if font_for is not None else font
        shapes.extend(pcb_text_box_shapes(box, to_board, precision, selected_font) or [])
    for start, end, stroke in borders(table, boxes):
        start, end = to_board(start), to_board(end)
        data = (
            f"M{format_mm(start[0], precision)},{format_mm(start[1], precision)} "
            f"L{format_mm(end[0], precision)},{format_mm(end[1], precision)}"
        )
        dash = tuple(
            pair
            for pair in stroke_attrs(stroke, precision, dash_ratio=dash_ratio, gap_ratio=gap_ratio)
            if pair[0] == "stroke-dasharray"
        )
        shapes.append(SvgShape("path", (("d", data),) + dash, stroke_width_mm=stroke.width))
    return shapes
