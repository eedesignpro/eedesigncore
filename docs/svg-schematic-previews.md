# Schematic SVG exports from memory

`render_schematic_svg` returns SVG text for one loaded schematic sheet.
It does not read files, start KiCad, follow sheet references, or change the supplied data.

Use `SchematicStyle(symbol=SymbolStyle(show_pin_types=True))` to show electrical pin type text.
Import `SymbolStyle` from `eedesigncore`. The default hides pin types.
The text uses the effective type after any selected pin alternate and follows hidden-pin visibility.
Set `SymbolStyle.pin_types` to change the text color.

```python
from eedesigncore import Schematic, SchematicStyle, ViewBox, render_schematic_svg

schematic = Schematic.from_string(schematic_text)
svg = render_schematic_svg(
    schematic,
    view_box=ViewBox(0, 0, 297, 210),
    style=SchematicStyle(wire="#008400", bus="#000084"),
    font=selected_font,  # Use None for the built-in stroke font.
)
```

The caller supplies the drawing window in millimeters. Y increases downward.
The window controls the SVG viewBox. It does not remove geometry outside that window.
The export does not derive a window from the paper size or drawing bounds.

## Supported content

| Content | Behavior |
| --- | --- |
| Placed symbols | Use embedded definitions, selected units and body styles, cardinal rotations, and mirrors. Common graphics join the selected unit. |
| Pins | Draw names, numbers, electrical shapes, and selected alternate definitions. Hidden pins require `show_hidden_pins=True`. |
| Symbol fields | Use saved sheet positions and the symbol's field orientation. Alignment follows KiCad's field bounds. |
| Connections | Draw wires, buses, bus entries, junctions, and no-connect marks. |
| Labels | Draw local, global, and hierarchical labels. Text angle and horizontal alignment determine direction. |
| Sheet blocks | Draw their border, fill, saved fields, and pins. Child files are not opened. |
| Graphics | Draw rectangles, circles, arcs, Bezier curves, and polylines. |
| Free text | Draw single-line and multiline text with saved size, angle, alignment, and style. |
| Text boxes and tables | Draw stored cell positions, merged cells, borders, fills, margins, and wrapped text. |
| Images | Embed saved PNG or JPEG data at its saved scale and position. No external image is loaded. |
| Rule areas | Draw the saved closed boundary and fill. |
| Net-class flags | Draw the saved flag shape, orientation, length, and fields. |
| Do-not-populate flags | Dim the symbol or sheet and draw its cross mark. |
| Hatch fills | Draw clipped hatch, reverse-hatch, and cross-hatch polygons. |

Symbol definitions must exist in `schematic.lib_symbols`.
Inherited parents must also exist there. Missing definitions and duplicate names raise `ValueError`.

## Font control

The `font` argument uses the existing `SymbolFont` contract.
The same font draws pins, symbol fields, symbol text, labels, sheet fields, sheet pins, and free text.
See [the font contract](svg-library-previews.md#font-control) for its method and coordinate units.
Load custom font data before calling the export. Saved font names do not start a font search.

A custom font can also supply the existing `text_box` method for field alignment and global-label width.
Without that method, the export measures the returned glyph strokes and includes the pen width.
That fallback can differ from the font's preferred advance width and text bounds.
Multiline free text uses the line spacing of a loaded `OutlineFont`.
Text boxes use the font's `line_pitch` method when the font has one.
A font without it uses KiCad stroke-font spacing.
The built-in font draws overbars, subscripts, superscripts, and stacked pin-number groups.
It covers the full KiCad Newstroke character table. A character outside the table draws as a question mark.

## Drawing defaults

`SchematicStyle` supplies colors, default wire and bus widths, junction diameter, no-connect size, and label spacing.
Its `symbol` member accepts `SymbolStyle` for symbol colors and the default drawing pen.
Explicit item colors and stroke widths take precedence where the file stores them.

No project settings or net class settings enter this operation.
Default widths, junction size, and label spacing therefore come from `SchematicStyle`.
The export does not enlarge junctions from a net class wire width.
Text variables stay as stored text unless the caller passes `text_context`. See [text resolution](text-resolution.md).
Variant-dependent values remain stored text.
Sheet filenames include KiCad's File display prefix.
The export does not derive multi-unit reference suffixes or select a hierarchy path for repeated sheets.

Text boxes inside embedded symbol definitions draw with their saved margins and alignment.
Their text remains readable when the symbol rotates or mirrors.

## Explicit limits

These inputs raise `ValueError` instead of silently producing an incomplete drawing:

- Invalid image data, inconsistent table cell arrays, and unsupported directive shapes.
- Missing embedded definitions, missing selected units, missing alternate pins, and unsupported placement angles.

Drawing sheets, automatic fitting, child-sheet export, and variant selection remain outside this operation.
The result is a deterministic preview. It is not a complete KiCad plotting replacement.
Hatch and knockout polygon vertices can differ from KiCad at rounded edges.
KiCad plots sheet cross marks twice. This export draws each cross once.
Custom fonts must supply text measurements for do-not-populate bounds and stacked pin labels.

## Verification

`tests/unit/render/test_schematic_svg.py` checks coordinates, visibility, font calls, XML, input preservation, and explicit failures.
`scripts/schematic_svg_oracle.py` constructs a schematic and compares its geometry with an installed KiCad SVG export.
The script requires an explicit executable path and output folder.
It records the constructed input, SVG files, comparison, and KiCad output in that folder.

## Project-level selection and highlighting

Use [schematic preparation for viewers](schematic-preparation.md) for resolved occurrences, source-item associations, symbol bounds, and electrical nets.
The single-sheet renderer remains a drawing operation. It does not resolve a project hierarchy.
