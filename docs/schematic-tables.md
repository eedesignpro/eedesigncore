# Schematic tables that fit their text

```python
from eedesigncore import create_schematic_table

created = create_schematic_table(
    [["Abbreviation", "Meaning"], ["PWR", "Power"], ["NC", "No connection"]],
    at=(25.4, 38.1),
    with_bounds=True,
)
schematic.tables.append(created.item)
bounds = created.measurement.bounds
```

Each column fits its widest cell. Each row fits its tallest cell.
The helper preserves explicit newlines. Additional word wrapping is off by default.
It does not reduce the font size. It uses the existing font advances and rendered table-cell bounds.
The default text size is 1.27 mm. Default padding is 0.3 mm.
The first row is bold by default. Set header=False for a table without a heading row.
Text is left aligned and vertically centered. Borders and row/column separators are enabled.
The position is the table's upper-left corner in sheet millimeters.
Origins snap to the nearest 50 mil grid point, or 1.27 mm.
Row heights and column widths round upward to that grid to retain text clearance.
Every cell corner therefore lands on the grid. Blank cells keep a minimum text-height row.
Glyph overhang can increase an individual margin so text stays inside its cell.

Without with_bounds=True, creation returns SchTable directly.
With it, creation returns MeasuredSchematicItem with item and measurement.
The measurement includes the visible border stroke and separate cell-text parts.
Pass it to find_schematic_overlaps with other item measurements to check text, graphics, and wires.
It is a snapshot. Measure or refit again after text or context changes.

```python
from eedesigncore import fit_schematic_table

old = schematic.tables[0]
old.cells[3].content = "A longer description"
schematic.tables[0] = fit_schematic_table(old)
```

Refitting returns a detached copy. It preserves table/cell UUIDs, text, font styles, and border settings.
It recomputes cell positions, sizes, margins, row heights, and column widths.
It can both increase and decrease dimensions. It snaps the first cell's position to the grid unless at supplies another origin.
The input table remains unchanged, including when validation fails.
Creation assigns fresh UUIDs only to the new table and its cells.

Use font= with the same explicit loaded font used for rendering.
For new tables, font_face= saves that family name in every cell.
For example, use font_face="Cascadia Mono" with font=load_font("Cascadia Mono").
Without a saved font name, KiCad can select a different user-default font.
That can change wrapping and invalidate the measured fit.
A named font without that input is rejected. The default uses the existing KiCad stroke font.
Use text_context= to fit resolved variables. The saved cells retain their original variable text.
Unresolved variables are rejected because their final size is unknown.

The first version requires a nonempty rectangular array and unmerged, unrotated, unmirrored cells.
Merged cells and rotated tables are rejected without changing the source.
Set word_wrap=True to allow wrapping between words.
max_column_width_mm sets the preferred column limit, with a default of 25.4 mm.
The limit rounds upward to the grid. Short columns remain fitted to their content.
Rows grow to fit the resulting wrapped text. Saved cell text stays unchanged.
An unbroken word wider than the limit expands the column rather than clipping.
With word_wrap=False, columns grow to fit full lines, regardless of the limit.
There is no font shrinking or automatic page placement.

```python
wrapped = create_schematic_table(
    [["Notes"], ["A long description with several words"]],
    at=(25.4, 38.1), word_wrap=True, max_column_width_mm=25.4,
    with_bounds=True,
)
```
These are drawing bounds, not electrical-rule or physical-connectivity checks.

Demo source: scripts/create_fitted_table_demo.py.
