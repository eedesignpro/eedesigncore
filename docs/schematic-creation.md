# Create schematic labels and sheet references

These helpers supply drawing settings while the caller chooses the electrical content.
They return items in memory. They do not open KiCad or write files.

```python
from eedesigncore import (
    Schematic, create_global_label, create_hierarchical_label, create_sheet,
)

schematic = Schematic()
schematic.labels.append(create_global_label(
    "SUPPLY_ENABLE", at=(50.8, 25.4), direction="right", shape="input",
))
schematic.labels.append(create_hierarchical_label(
    "POWER_GOOD", at=(101.6, 50.8), direction="left", shape="output",
    net_class="Logic",
))
schematic.sheets.append(create_sheet(
    "Power control", "power-control.kicad_sch", at=(30.0, 60.0), size=(50.0, 25.0),
))
```

Use the normal schematic save operation after adding the required project content.

## Labels

Both label helpers accept the following choices.

| Argument | Default | Meaning |
| --- | --- | --- |
| `net_name` | Required | Net name displayed inside the label |
| `at` | Required | Exact wire contact coordinates in millimeters |
| `direction` | `"right"` | Text extends `"left"`, `"right"`, `"up"`, or `"down"` from the contact |
| `shape` | `"passive"` | `"input"`, `"output"`, `"passive"`, `"bidirectional"`, or `"tri_state"` |
| `net_class` | `None` | Optional hidden `Netclass` field |
| `properties` | `None` | Additional named text fields, hidden by default |
| `text_size_mm` | `1.27` | Label and field text size |
| `uuid` | `None` | Existing unique identifier, or allocate a new one |

Direction describes the text position. Shape describes the electrical type.
The helpers set text angle and justification together.
They preserve the exact wire contact, including contacts outside the grid.
Snapping that contact could disconnect an existing wire.

Global labels include the native `Intersheetrefs` field with `${INTERSHEET_REFS}` as its value.
The helper places this field beyond the label body through `autoplace_fields`.
Use `show_intersheet_refs=False` to hide it without removing its placement.
Page-reference fields remain horizontal on vertical labels.
Placement ignores nearby items and uses the default KiCad stroke-font geometry.
Project display settings and resolved text variables can affect the final appearance in KiCad.

Hierarchical labels do not include page references.
The helper does not create a matching pin on the parent sheet reference.
The optional net-class field does not create a project net-class definition.

## Sheet references

`create_sheet` takes a title, relative child filename, top-left coordinates, and optional dimensions in millimeters.
The default dimensions are `50.8` by `30.48` millimeters.
The width grows as needed to contain the complete title, including its displayed prefix.
Pass `font` when measurements must match a selected rendering font.
The filename must end in `.kicad_sch` and cannot escape the parent project directory.

| Setting | Helper behavior |
| --- | --- |
| Grid | Every corner lies on the 50 mil grid, equivalent to `1.27` mm |
| Top-left corner | Nearest grid point, with exact half-grid ties away from zero |
| Dimensions | Round upward to the grid, with at least one grid step per dimension |
| Sheet title | Left justified, bold, 75 mil text, above the upper-left corner |
| Sheet filename | Left justified, 35 mil text, below the lower-left corner |
| Fill | Pale light green, `#E8F5E9`, opaque |
| Border | Dark green, `#43784C`, opaque, `0.254` mm wide |

The optional `fill_color` and `border_color` arguments accept `Color` values.
The optional `uuid` argument retains a supplied identifier. Otherwise, the helper allocates one.
The helper creates only the sheet reference rectangle and its fields.
It does not create the child file, sheet pins, page assignments, or project hierarchy paths.

## Sheet pins

Use `add_sheet_pin` to add a hierarchical connection to the edge of a sheet reference.
Pass the child hierarchical label to copy its net name and electrical shape.

```python
from eedesigncore import add_sheet_pin

child_label = create_hierarchical_label("ENABLE", at=(25.4, 50.8), shape="input")
child.labels.append(child_label)
pin = add_sheet_pin(sheet, child_label, side="left")
# Use pin.position as the parent wire endpoint.
```

The helper appends the pin and returns it. It does not change the child schematic.
The default allowed sides are `left` and `right`. Text faces into the sheet.
Set `allow_top_bottom=True` to permit an explicit `top` or `bottom` side.
Sheet-pin text uses a middle vertical anchor.
Without an offset, the helper selects the first free 200 mil slot along that edge.
Use `offset_mm` to select a distance from the top of a vertical edge or left of a horizontal edge.
The helper snaps that distance to the 50 mil grid.
It rejects corners, occupied contacts, duplicate names, and a sheet with corners outside the grid.
It does not resize the sheet or move existing pins.
The returned `pin.position` gives the exact contact after snapping.
Passing a net name instead of a child label accepts an optional `shape`, which defaults to `passive`.
The helper does not prevent long pin text from overlapping other text inside a small sheet.

## Existing items

Use [automatic field placement](field-autoplacement.md) to reposition fields on existing symbols and labels.
It supports an automatic side or a forced left, right, top, or bottom side.
Creation helpers do not change existing items.
Use `with_bounds=True` to return the item with its [drawing measurements](schematic-measurements.md).

## Note blocks

Use the [note helpers](schematic-notes.md) for schematic, layout, and TODO note boxes, including normalized reading and updates.
Use [schematic tables](schematic-tables.md) to create tables that fit their text.

## Child page content

`create_subsheet(title, filename, at=...)` returns a `CreatedSubsheet` with `sheet` and `schematic` members.
Append `sheet` to the parent schematic. Save `schematic` at the relative filename through the existing native save operations.
The operation does not write files, overwrite a child file, assign page numbers, or insert a reference into the parent.
It uses the sheet size, title-width fitting, and border defaults described above.

The new child contains a Git information block and a three-row table for branch, date, and commit. This package ships that layout in the template file sch/templates/child_sheet.kicad_sch.
The table retains the BRANCH, DATE, and COMMIT rows, their Git variables, dimensions, and formatting.
The child title retains the template position and style but uses `@{upper("${SHEETNAME}")}`.
The page retains the template's landscape B paper size. All native identifiers are fresh.
No other template contents are copied.

`create_sheet_schematic()` returns the same seeded child document without a parent reference.
The existing `create_sheet()` still returns only a parent reference.
These helpers do not resolve the sheet-name or Git variables. KiCad receives them unchanged.
