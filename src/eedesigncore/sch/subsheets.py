"""Create child sheets from the verified template page content."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from uuid import uuid4

from eedesigncore.common.models import Color
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.creation import create_sheet
from eedesigncore.sch.models import Schematic, Sheet
from eedesigncore.text_resolution import TextResolutionContext


@dataclass(frozen=True)
class CreatedSubsheet:
    """A parent sheet reference and its new child document. Neither is saved."""

    sheet: Sheet
    schematic: Schematic


def create_sheet_schematic() -> Schematic:
    """Create a child page with the template heading, Git table, and sheet title."""
    source = files("eedesigncore.sch").joinpath("templates/child_sheet.kicad_sch")
    schematic = Schematic.from_string(source.read_text(encoding="utf-8"))
    schematic.uuid = str(uuid4())
    for text in schematic.texts:
        text.uuid = str(uuid4())
        if text.content == '@{upper("${PROJECT_TITLE}")}':
            text.content = '@{upper("${SHEETNAME}")}'
    for table in schematic.tables:
        table.uuid = str(uuid4())
        for cell in table.cells:
            cell.uuid = str(uuid4())
    return schematic


def create_subsheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    text_context: TextResolutionContext | None = None,
) -> CreatedSubsheet:
    """Create the formatted parent reference and seeded child page without file edits."""
    return CreatedSubsheet(
        sheet=create_sheet(
            title,
            filename,
            at=at,
            size=size,
            font=font,
            fill_color=fill_color,
            border_color=border_color,
            text_context=text_context,
        ),
        schematic=create_sheet_schematic(),
    )
