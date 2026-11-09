"""Create child sheets from the verified template page content."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.resources import files
from uuid import uuid4

from eedesigncore.common.models import Color
from eedesigncore.project.hierarchy import SheetHierarchy, SheetNode
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.creation import create_sheet
from eedesigncore.sch.models import Schematic, Sheet, SheetInstancePath, SheetInstanceProject
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


def next_page_number(hierarchy: SheetHierarchy) -> str:
    """The page number KiCad gives the next new sheet: the highest numeric page plus one.

    Page numbers that are not whole numbers, and sheets with no page, do not
    count. A hierarchy with no numeric page gives ``"1"``.
    """
    highest = 0
    for node in hierarchy.walk():
        if node.page is not None and node.page.strip().isdigit():
            highest = max(highest, int(node.page.strip()))
    return str(highest + 1)


def create_subsheet(
    title: str,
    filename: str,
    *,
    at: tuple[float, float],
    hierarchy: SheetHierarchy | None = None,
    parent: SheetNode | None = None,
    project_name: str | None = None,
    parent_path: str | None = None,
    page: str | None = None,
    size: tuple[float, float] = (50.8, 30.48),
    font: SymbolFont | None = None,
    fill_color: Color | None = None,
    border_color: Color | None = None,
    text_context: TextResolutionContext | None = None,
) -> CreatedSubsheet:
    """Create the parent sheet reference, with its page number, and the seeded child page.

    The sheet gets the instance data KiCad writes: one ``(project (path (page)))``
    entry for each instance of the parent file. Give the place in one of two ways:

    - ``hierarchy``: the project from :func:`load_hierarchy`. ``parent`` is the
      node of the file that will hold the sheet, by default the root. Every
      instance of that file gets an entry. The project name is the project
      file name; ``project_name`` overrides it. Each page is the next free
      page number of the hierarchy (:func:`next_page_number`); ``page`` sets
      the first one.
    - ``project_name``, ``parent_path`` and ``page``, all three: one entry.
      ``parent_path`` is the KIID path of the parent sheet instance, such as
      ``"/<root uuid>"`` for the root.

    No file changes: append ``sheet`` to the parent schematic, then save both.

    Raises:
        ValueError: Neither form is complete, or ``parent`` is not in ``hierarchy``.
    """
    sheet = create_sheet(
        title,
        filename,
        at=at,
        size=size,
        font=font,
        fill_color=fill_color,
        border_color=border_color,
        text_context=text_context,
    )
    sheet.instances = _sheet_instances(hierarchy, parent, project_name, parent_path, page)
    return CreatedSubsheet(sheet=sheet, schematic=create_sheet_schematic())


def _sheet_instances(
    hierarchy: SheetHierarchy | None,
    parent: SheetNode | None,
    project_name: str | None,
    parent_path: str | None,
    page: str | None,
) -> list[SheetInstanceProject]:
    if hierarchy is None:
        if parent is not None:
            raise ValueError("create_subsheet: give 'hierarchy' with 'parent'")
        if project_name is None or parent_path is None or page is None:
            raise ValueError(
                "create_subsheet needs 'hierarchy', or all of 'project_name', "
                "'parent_path' and 'page', so that KiCad gets the sheet page number"
            )
        return [
            SheetInstanceProject(
                name=project_name, paths=[SheetInstancePath(path=parent_path, page=page)]
            )
        ]
    nodes = list(hierarchy.walk())
    if parent is None:
        parent = hierarchy.root
    elif not any(node is parent for node in nodes):
        raise ValueError("create_subsheet: 'parent' is not a node of 'hierarchy'")
    if project_name is None:
        source = hierarchy.project_path or next(n for n in nodes if n.is_root).path
        project_name = source.stem
    key = os.path.normcase(str(parent.path))
    paths = [n.kiid_path for n in nodes if os.path.normcase(str(n.path)) == key]
    first = int(next_page_number(hierarchy)) if page is None else None
    entries = []
    for index, path in enumerate(paths):
        if first is not None:
            number = str(first + index)
        elif index == 0:
            number = page
        else:
            number = _following(page, index)
        entries.append(SheetInstancePath(path=path, page=number))
    return [SheetInstanceProject(name=project_name, paths=entries)]


def _following(page: str, offset: int) -> str:
    """The page ``offset`` after ``page``, or ``page`` itself when it is not a number."""
    text = page.strip()
    return str(int(text) + offset) if text.isdigit() else page
