"""Content-sized native schematic tables using the existing font and drawing tools."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal, overload
from uuid import uuid4

from eedesigncore.common.escape import unescape_string
from eedesigncore.common.font import default_font
from eedesigncore.common.font.markup import words
from eedesigncore.common.models import JustifyH, JustifyV, Stroke, TextAttributes
from eedesigncore.common.transform import point_to_iu, to_mm
from eedesigncore.render.schematics import SchematicStyle
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.render.text_boxes import text_pen, wrapped_lines
from eedesigncore.sch.measurement import (
    MeasuredSchematicItem,
    measure_schematic_item,
    measured_item,
)
from eedesigncore.sch.models import Schematic, SchTable, SchTableCell
from eedesigncore.text_resolution import TextResolutionContext, resolve_document_text

_STYLE = SchematicStyle()
_GRID_MM = 1.27


def _grid_size(value: float) -> float:
    return round(math.ceil(value / _GRID_MM - 1e-9) * _GRID_MM, 6)


def _positive(value: float, name: str, *, zero: bool = False) -> None:
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}.")


@overload
def fit_schematic_table(
    table: SchTable,
    *,
    at: tuple[float, float] | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: Literal[False] = False,
) -> SchTable: ...


@overload
def fit_schematic_table(
    table: SchTable,
    *,
    at: tuple[float, float] | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: Literal[True],
) -> MeasuredSchematicItem[SchTable]: ...


@overload
def fit_schematic_table(
    table: SchTable,
    *,
    at: tuple[float, float] | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: bool,
) -> SchTable | MeasuredSchematicItem[SchTable]: ...


def fit_schematic_table(
    table: SchTable,
    *,
    at: tuple[float, float] | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: bool = False,
) -> SchTable | MeasuredSchematicItem[SchTable]:
    """Return a fitted copy. Preserve IDs, content, font styles, and border settings.

    Recompute positions, sizes, and margins on a 50 mil grid. Optional word wrapping
    uses a column-width limit. Reject unsupported cells before edits.
    """
    _positive(padding_mm, "Padding", zero=True)
    _positive(max_column_width_mm, "Column width limit")
    columns = table.column_count
    if columns <= 0 or not table.cells or len(table.cells) % columns:
        raise ValueError("A table requires a nonempty rectangular cell array.")
    origin = at if at is not None else table.cells[0].position
    if not all(math.isfinite(value) for value in origin):
        raise ValueError("Table coordinates must be finite.")
    snapped = [round(math.floor(value / _GRID_MM + 0.5) * _GRID_MM, 6) for value in origin]
    origin = (snapped[0], snapped[1])
    result = table.model_copy(deep=True)
    displayed = result
    if text_context is not None:
        displayed = resolve_document_text(
            Schematic(tables=[result]), text_context
        ).document.tables[0]
    widths = [0.0] * columns
    heights = [0.0] * (len(table.cells) // columns)
    for index, (cell, display) in enumerate(zip(result.cells, displayed.cells, strict=True)):
        attrs = display.attributes or TextAttributes()
        if cell.span != (1, 1) or cell.angle % 360 or attrs.mirror:
            raise ValueError("Table fitting requires unmerged, unrotated, unmirrored cells.")
        for size in attrs.font_size:
            _positive(size, "Text size")
        if attrs.font_face and font is None:
            raise ValueError("Named table fonts require an explicit measurement font.")
        metrics = font or default_font()
        select = getattr(metrics, "for_style", None)
        selected = select(attrs.bold, attrs.italic) if callable(select) else metrics
        advance = getattr(selected, "advance", None)
        if not callable(advance):
            raise ValueError("Table fitting requires font advance measurements.")
        pen = text_pen(attrs, _STYLE.symbol.line_width_mm)
        lines = wrapped_lines(display.content, 1e6, attrs, pen, font)
        advance_width = (
            max(
                (to_mm(int(advance(line, point_to_iu(attrs.font_size)))) for line in lines),
                default=0.0,
            )
            + pen
            + 0.001
        )
        probe_width = max(10.0, advance_width + 10.0)
        probe_height = 100.0
        probe = SchTable(
            column_count=1,
            column_widths=[probe_width],
            row_heights=[probe_height],
            cells=[
                display.model_copy(
                    update={
                        "position": (0.0, 0.0),
                        "size": (probe_width, probe_height),
                        "margins": (0.0, 0.0, 0.0, 0.0),
                    },
                    deep=True,
                )
            ],
        )
        text = measure_schematic_item(probe, font=font)
        if not text.complete:
            raise ValueError("Cannot fit table text: " + "; ".join(text.diagnostics))
        boxes = [part.bounds for part in text.parts if ":text" in part.name]
        anchor_x = {
            JustifyH.left: 0.0,
            JustifyH.center: probe_width / 2,
            JustifyH.right: probe_width,
        }[attrs.justify_h]
        anchor_y = {
            JustifyV.top: 0.0,
            JustifyV.center: probe_height / 2,
            JustifyV.bottom: probe_height,
        }[attrs.justify_v]
        min_x = min((box.min_x - anchor_x for box in boxes), default=0.0)
        max_x = max((box.max_x - anchor_x for box in boxes), default=0.0)
        min_y = min((box.min_y - anchor_y for box in boxes), default=0.0)
        max_y = max((box.max_y - anchor_y for box in boxes), default=0.0)
        # Ink can extend beyond the alignment anchor, especially for italic fonts.
        left = right = top = bottom = padding_mm
        if attrs.justify_h == JustifyH.left:
            left += max(0.0, -min_x)
            width = left + max(max_x, advance_width) + right
        elif attrs.justify_h == JustifyH.right:
            right += max(0.0, max_x)
            width = left + max(-min_x, advance_width) + right
        else:
            width = 2 * max(abs(min_x), abs(max_x), advance_width / 2) + left + right
        if attrs.justify_v == JustifyV.top:
            top += max(0.0, -min_y)
            height = top + max(0.0, max_y) + bottom
        elif attrs.justify_v == JustifyV.bottom:
            bottom += max(0.0, max_y)
            height = top + max(0.0, -min_y) + bottom
        else:
            height = 2 * max(abs(min_y), abs(max_y)) + top + bottom
        height = max(height, attrs.font_size[1] + 2 * padding_mm)
        row, col = divmod(index, columns)
        widths[col] = max(widths[col], _grid_size(width))
        heights[row] = max(heights[row], _grid_size(height))
        cell.margins = (left, top, right, bottom)
    if word_wrap:
        widths = [min(width, _grid_size(max_column_width_mm)) for width in widths]
        # Grow a narrow column for an indivisible word, then measure final wrapping.
        for col in range(columns):
            for index in range(col, len(result.cells), columns):
                cell = result.cells[index]
                display = displayed.cells[index]
                attrs = display.attributes or TextAttributes()
                metrics = font or default_font()
                select = getattr(metrics, "for_style", None)
                selected = select(attrs.bold, attrs.italic) if callable(select) else metrics
                advance = getattr(selected, "advance", None)
                if not callable(advance):
                    raise ValueError("Table fitting requires font advance measurements.")

                longest = max(
                    (
                        to_mm(int(advance(word.rstrip(), point_to_iu(attrs.font_size))))
                        for line in unescape_string(display.content).split("\n")
                        for word in words(line)
                        if word.strip()
                    ),
                    default=0.0,
                )
                required = longest + text_pen(attrs, _STYLE.symbol.line_width_mm) + 0.001
                widths[col] = max(
                    widths[col], _grid_size(required + cell.margins[0] + cell.margins[2])
                )
        heights = [0.0] * len(heights)
        for index, cell in enumerate(result.cells):
            row, col = divmod(index, columns)
            display = displayed.cells[index]
            attrs = display.attributes or TextAttributes()
            left, top, right, bottom = cell.margins
            # Size the native wrapped cell, not a separately reflowed string.
            probe = SchTable(
                column_count=1,
                column_widths=[widths[col]],
                row_heights=[100.0],
                cells=[
                    display.model_copy(
                        update={
                            "position": (0.0, 0.0),
                            "size": (widths[col], 100.0),
                            "margins": (left, 0.0, right, 0.0),
                        },
                        deep=True,
                    )
                ],
            )
            text = measure_schematic_item(probe, font=font)
            if not text.complete:
                raise ValueError("Cannot fit wrapped table text: " + "; ".join(text.diagnostics))
            boxes = [part.bounds for part in text.parts if ":text" in part.name]
            anchor_y = {JustifyV.top: 0.0, JustifyV.center: 50.0, JustifyV.bottom: 100.0}[
                attrs.justify_v
            ]
            min_y = min((box.min_y - anchor_y for box in boxes), default=0.0)
            max_y = max((box.max_y - anchor_y for box in boxes), default=0.0)
            top = bottom = padding_mm
            if attrs.justify_v == JustifyV.top:
                top += max(0.0, -min_y)
                height = top + max(0.0, max_y) + bottom
            elif attrs.justify_v == JustifyV.bottom:
                bottom += max(0.0, max_y)
                height = top + max(0.0, -min_y) + bottom
            else:
                height = 2 * max(abs(min_y), abs(max_y)) + top + bottom
            cell.margins = (left, top, right, bottom)
            heights[row] = max(
                heights[row], _grid_size(max(height, attrs.font_size[1] + 2 * padding_mm))
            )
    result.column_widths, result.row_heights = widths, heights
    for index, cell in enumerate(result.cells):
        row, col = divmod(index, columns)
        cell.position = (
            round(origin[0] + sum(widths[:col]), 6),
            round(origin[1] + sum(heights[:row]), 6),
        )
        cell.size = (widths[col], heights[row])
    return measured_item(result, text_context, font=font) if with_bounds else result


@overload
def create_schematic_table(
    rows: Sequence[Sequence[str]],
    *,
    at: tuple[float, float],
    header: bool = True,
    text_size_mm: float = 1.27,
    font_face: str | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: Literal[False] = False,
) -> SchTable: ...


@overload
def create_schematic_table(
    rows: Sequence[Sequence[str]],
    *,
    at: tuple[float, float],
    header: bool = True,
    text_size_mm: float = 1.27,
    font_face: str | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: Literal[True],
) -> MeasuredSchematicItem[SchTable]: ...


@overload
def create_schematic_table(
    rows: Sequence[Sequence[str]],
    *,
    at: tuple[float, float],
    header: bool = True,
    text_size_mm: float = 1.27,
    font_face: str | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: bool,
) -> SchTable | MeasuredSchematicItem[SchTable]: ...


def create_schematic_table(
    rows: Sequence[Sequence[str]],
    *,
    at: tuple[float, float],
    header: bool = True,
    text_size_mm: float = 1.27,
    font_face: str | None = None,
    padding_mm: float = 0.3,
    word_wrap: bool = False,
    max_column_width_mm: float = 25.4,
    font: SymbolFont | None = None,
    text_context: TextResolutionContext | None = None,
    with_bounds: bool = False,
) -> SchTable | MeasuredSchematicItem[SchTable]:
    """Create a fitted native table from row-major text. No document is changed."""
    _positive(text_size_mm, "Text size")
    if (
        not rows
        or isinstance(rows, str)
        or not rows[0]
        or any(isinstance(row, str) or len(row) != len(rows[0]) for row in rows)
        or any(not isinstance(text, str) for row in rows for text in row)
    ):
        raise ValueError("Table rows must be a nonempty rectangular array of strings.")
    table = SchTable(
        column_count=len(rows[0]),
        border_external=True,
        border_header=header,
        border_stroke=Stroke(width=0.1524),
        separators_rows=True,
        separators_cols=True,
        separators_stroke=Stroke(width=0.1524),
        uuid=str(uuid4()),
        cells=[
            SchTableCell(
                content=text,
                uuid=str(uuid4()),
                attributes=TextAttributes(
                    font_size=(text_size_mm, text_size_mm),
                    font_face=font_face,
                    bold=header and row_index == 0,
                    justify_h=JustifyH.left,
                    justify_v=JustifyV.center,
                ),
            )
            for row_index, row in enumerate(rows)
            for text in row
        ],
    )
    return fit_schematic_table(
        table,
        at=at,
        padding_mm=padding_mm,
        word_wrap=word_wrap,
        max_column_width_mm=max_column_width_mm,
        font=font,
        text_context=text_context,
        with_bounds=with_bounds,
    )
