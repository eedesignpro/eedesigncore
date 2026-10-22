"""Create and read the fixed schematic note templates."""

from __future__ import annotations

import math
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Color, Fill, FillType, Stroke, StrokeType, TextAttributes
from eedesigncore.sch.models import Schematic, SchTextBox

NoteKind = Literal["schematic", "layout", "todo"]
_NAMES: dict[NoteKind, str] = {
    "schematic": "SCHEMATIC NOTE",
    "layout": "LAYOUT NOTE",
    "todo": "TODO",
}
_COLORS: dict[NoteKind, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "schematic": ((0, 0, 90), (177, 177, 220)),
    "layout": ((0, 90, 0), (177, 220, 177)),
    "todo": ((90, 0, 0), (220, 177, 177)),
}
_GRID = 1.27


class SchematicNote(KicadModel):
    """A read-only interpretation of a native note, with its exact source text."""

    model_config = ConfigDict(frozen=True)
    kind: NoteKind
    title: str
    text: str
    uuid: str | None
    position: tuple[float, float]
    size: tuple[float, float]
    source_text: str
    diagnostics: tuple[str, ...] = ()


def _header(kind: NoteKind) -> str:
    name = _NAMES[kind]
    level = "ERROR" if kind == "todo" else "WARNING"
    return "${ERC_" + level + " " + name + " " + "#" * 30 + " " + name + "}" + name + ":"


def create_note_block(
    kind: NoteKind,
    title: str,
    text: str,
    *,
    at: tuple[float, float],
    size: tuple[float, float] | None = None,
) -> SchTextBox:
    """Create a native note with fixed template colors and grid-aligned corners."""
    if kind not in _NAMES:
        raise ValueError("Unknown note kind.")
    if not isinstance(title, str) or any(c in title for c in "\r\n\x00"):
        raise ValueError("The note title must be a single line without null characters.")
    if not isinstance(text, str) or "\x00" in text:
        raise ValueError("The note text must be a string without null characters.")
    dimensions = size if size is not None else (50.8 if kind == "todo" else 49.53, 30.48)
    if (
        len(at) != 2
        or len(dimensions) != 2
        or not all(math.isfinite(v) for v in (*at, *dimensions))
    ):
        raise ValueError("Note coordinates and dimensions must be finite pairs.")
    if any(v <= 0 for v in dimensions):
        raise ValueError("Note dimensions must be positive.")
    position = tuple(
        round(math.copysign(math.floor(abs(v) / _GRID + 0.5), v) * _GRID, 6) for v in at
    )
    extent = tuple(round(max(1, math.ceil(v / _GRID - 1e-9)) * _GRID, 6) for v in dimensions)
    dark, light = _COLORS[kind]
    return SchTextBox(
        content=_header(kind) + "\n\nTitle: " + title + "\n\n" + text,
        position=(position[0], position[1]),
        size=(extent[0], extent[1]),
        margins=(0.9525,) * 4,
        stroke=Stroke(
            width=0, type=StrokeType.solid, color=Color(r=dark[0], g=dark[1], b=dark[2], a=1)
        ),
        fill=Fill(type=FillType.color, color=Color(r=light[0], g=light[1], b=light[2], a=0.8)),
        attributes=TextAttributes(
            font_size=(1.27, 1.27),
            color=Color(r=dark[0], g=dark[1], b=dark[2], a=1),
        ),
        uuid=str(uuid4()),
    )


def create_schematic_note(
    title: str, text: str, *, at: tuple[float, float], size: tuple[float, float] | None = None
) -> SchTextBox:
    """Create a blue schematic note."""
    return create_note_block("schematic", title, text, at=at, size=size)


def create_layout_note(
    title: str, text: str, *, at: tuple[float, float], size: tuple[float, float] | None = None
) -> SchTextBox:
    """Create a green layout note in a schematic."""
    return create_note_block("layout", title, text, at=at, size=size)


def create_todo_note(
    title: str, text: str, *, at: tuple[float, float], size: tuple[float, float] | None = None
) -> SchTextBox:
    """Create a red TODO note."""
    return create_note_block("todo", title, text, at=at, size=size)


def parse_note_block(box: SchTextBox) -> SchematicNote | None:
    """Read an exact template marker without resolving variables or changing the box.

    Legacy notes have no title. Their entire body remains text. An altered marker
    raises an error rather than allowing a note inventory to silently omit it.
    """
    content = box.content.replace("\r\n", "\n")
    kind = next((k for k in _NAMES if content.startswith(_header(k).split("}")[0] + "}")), None)
    if kind is None:
        if (any(name in content for name in _NAMES.values()) and "${ERC_" in content) or any(
            content.startswith(name + ":") for name in _NAMES.values()
        ):
            raise ValueError(
                "A possible template note has an unrecognized marker: " + str(box.uuid)
            )
        return None
    marker = _header(kind).split("}")[0] + "}"
    remainder = content[len(marker) :]
    heading, separator, body = remainder.partition("\n\n")
    diagnostics: list[str] = []
    if heading != _NAMES[kind] + ":":
        diagnostics.append("The template heading was changed.")
        body = remainder
    elif not separator:
        body = ""
    title = ""
    if body.startswith("Title: "):
        first, separator, rest = body.partition("\n\n")
        if "\n" not in first:
            title, body = first[len("Title: ") :], rest if separator else ""
    expected = create_note_block(kind, "", "", at=(0, 0))
    if (
        box.stroke != expected.stroke
        or box.fill != expected.fill
        or box.attributes != expected.attributes
    ):
        diagnostics.append("The note formatting differs from the template.")
    if box.margins != expected.margins:
        diagnostics.append("The note margins differ from the template.")
    if box.angle % 360:
        diagnostics.append("The note is rotated.")
    if any(
        not math.isfinite(v) or abs(v / _GRID - round(v / _GRID)) > 1e-7
        for v in (*box.position, *box.size)
    ):
        diagnostics.append("The note corners are not on the 50 mil grid.")
    return SchematicNote(
        kind=kind,
        title=title,
        text=body,
        uuid=box.uuid,
        position=box.position,
        size=box.size,
        source_text=box.content,
        diagnostics=tuple(diagnostics),
    )


def parse_schematic_notes(schematic: Schematic) -> list[SchematicNote]:
    """Read marked text boxes in document order. Do not search child sheets."""
    return [note for box in schematic.text_boxes if (note := parse_note_block(box)) is not None]


def update_note_block(box: SchTextBox, *, title: str, text: str) -> SchTextBox:
    """Return a normalized copy while preserving its identity and simulation flag."""
    note = parse_note_block(box)
    if note is None:
        raise ValueError("The text box is not a template note.")
    if box.angle % 360:
        raise ValueError("Rotate the note to zero before normalizing it.")
    result = create_note_block(note.kind, title, text, at=box.position, size=box.size)
    result.uuid = box.uuid
    result.exclude_from_sim = box.exclude_from_sim
    return result
