# Schematic note blocks

Use these helpers to create, read, and update three kinds of note box: schematic notes, layout notes, and TODO notes. Each kind has a fixed style and a first-line marker that the reader uses to find it.
They return native `SchTextBox` objects. They do not save files or change the supplied schematic.

```python
from eedesigncore import (
    create_schematic_note, create_layout_note, create_todo_note,
    parse_schematic_notes, update_note_block,
)

schematic.text_boxes.append(create_schematic_note(
    "Supply limit", "Keep the input below the rated voltage.", at=(25.4, 25.4),
))
schematic.text_boxes.append(create_layout_note(
    "Return path", "Place the bypass capacitor beside the supply pins.", at=(80, 25.4),
))
schematic.text_boxes.append(create_todo_note(
    "Review", "Confirm the connector pin assignment.", at=(135, 25.4),
))
notes = parse_schematic_notes(schematic)
```

Each constructor takes `title`, `text`, `at`, and optional `size`, in millimeters.
The origin snaps to the nearest 50 mil grid point. Half-grid ties round away from zero.
Width and height round upward to the grid. Every corner lands on the grid.
Default sizes match the template: 49.53 by 30.48 mm for schematic and layout notes, and 50.8 by 30.48 mm for TODO notes.
These are fixed-size boxes. The helper does not fit long text or choose page positions.

Colors, opacity, border, margins, text size, and alignment match the template.
There are no color arguments. Each new note receives a fresh UUID.
The native objects remain editable in KiCad. The reader reports formatting changes without discarding the note.

## Text format

The existing first line stays unchanged, including its electrical-rule-check marker.
Schematic and layout notes retain their warning marker. TODO notes retain their error marker.
The helper adds a blank line, `Title: ` followed by the title, another blank line, and the body text.
A title must occupy one line. Body text can contain newlines and unresolved KiCad variables.
The helper does not resolve variables or interpret the body as instructions.

The original templates do not have a separate title. The reader returns an empty title and preserves their complete body.
For human edits, `Title: ` is the reserved title line immediately after the template heading and blank line.
A body that starts with that exact title-line form is interpreted as a title, including in older notes.

## Read and update

`parse_note_block(box)` returns a `SchematicNote`, or `None` for an ordinary text box.
`parse_schematic_notes(schematic)` returns marked notes in document order. It does not read child sheets.
Each result contains `kind`, `title`, `text`, `uuid`, `position`, `size`, `source_text`, and `diagnostics`.
`source_text` retains the exact saved text. Parsed line endings normalize CRLF to LF.
The UUID is the identity used to find an existing note. A copied note must receive a new UUID.

The reader identifies notes by their exact template marker, not by color or body keywords.
It reports changed headings, formatting, margins, rotation, and off-grid coordinates.
A recognizable but damaged or missing marker raises `ValueError` instead of silently omitting the note.
A completely replaced marker and heading cannot be identified reliably as a former note.
The reader does not modify the supplied objects.

`update_note_block(box, title=..., text=...)` returns a normalized copy.
It restores the template style, snaps the corners, and preserves the UUID and simulation exclusion flag.
It rejects rotated notes. The caller replaces the selected text box and saves through the existing guarded-save operation.
Existing note geometry does not need to match the default dimensions.

## Template source

The note style is fixed. It matches a project template that is not part of this package. The regression fixture holds the three original note boxes.
