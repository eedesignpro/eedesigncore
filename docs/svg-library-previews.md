# Symbol and footprint SVG previews from memory

Use these operations after loading or constructing a symbol or footprint.
Both return SVG text. Neither operation reads files, starts KiCad, or changes its input.
The caller supplies the drawing window in millimeters.

## Footprint export

```python
from eedesigncore import (
    Footprint, LayerStyle, Pad, ViewBox, render_footprint_svg,
)

footprint = Footprint(
    fp_id="Preview",
    pads=[Pad(number="1", layers=["F.Cu", "F.Paste", "F.Mask"], size=(2, 1))],
)
pages = render_footprint_svg(
    footprint,
    layers=["F.Cu", "F.Paste", "F.Mask"],
    view_box=ViewBox(-5, -5, 10, 10),
    style=LayerStyle({}, default_color="#ffffff"),
)
svg = pages["F.Cu"]
```

Each requested layer returns one SVG string. Pads draw by default.
The operation uses the existing board drawing support, including mask and paste overrides.
Pass `setup=board.setup` when board-level mask and paste defaults must apply.
Pass `layers=["Holes"]` to draw drill holes.
The operation retains a supplied footprint placement. A library footprint without placement uses its local origin.
Existing board drawing limits also apply. Use `on_skip` to collect unsupported drawing items.

### Filled board and footprint graphics

Use `Fill(type=FillType.outline)` for a solid circle, rectangle, or polygon.
The name `outline` is the stored fill enum. There is no `FillType.solid`.
`FillType.none` draws only the outline.
`LayerStyle` and `CliParityStyle` preserve solid interiors and the outline extent.
Solid circles include half the outline width in their visible radius, as KiCad does.
Layer color and opacity also apply to the fill. Hidden layers remain hidden.
Custom rectangle and polygon styles retain their fill and stroke choices.

## Symbol export

```python
from eedesigncore import (
    Pin, SchRect, Symbol, SymbolUnit, SymbolStyle, ViewBox, render_symbol_svg,
)

symbol = Symbol(
    name="Preview",
    units=[SymbolUnit.for_symbol("Preview", items=[
        SchRect(start=(-2, 2), end=(2, -2)),
        Pin(electrical_type="input", shape="line", position=(-4.54, 0),
            length=2.54, name="IN", number="1"),
    ])],
)
svg = render_symbol_svg(
    symbol,
    view_box=ViewBox(-6, -5, 12, 10),
    unit=1,
    body_style=1,
    style=SymbolStyle(body="#ffffff", pins="#ffffff"),
)
```

The result contains the selected unit and body style, plus common items.
Symbol file coordinates use upward Y. The SVG window uses downward Y.
The export converts geometry and keeps text readable.

Pass `symbols={item.name: item for item in library.symbols}` for derived symbols.
Missing parents and inheritance cycles raise `ValueError`.
The operation takes geometry and pin settings from the root symbol.
Derived fields override parent fields through the inheritance chain.

Visible fields show their stored values. The export does not add reference suffixes or expand project variables.
Use `include_fields=False` to omit fields. Use `show_hidden_pins=True` to include hidden pins.
Private drawing items remain hidden.

Set `SymbolStyle(show_pin_types=True)` to display electrical pin types outside the connection ends.
The default hides this text. Set `pin_types` to change its color.
Labels use KiCad's English type names, text size, and orientation.
They follow hidden-pin visibility and use the selected font.
The export does not add dangling-pin circles or their extra text clearance.
Clock, inverted-clock, and clock-low triangles use KiCad editor proportions.
KiCad plot output uses wider triangles for these shapes.
Use `SymbolStyle(clock_style="plot")` when the output must keep the KiCad plot proportions.

## Font control

`render_symbol_svg(..., font=your_font)` selects the font for all symbol text.
This includes pin names, pin numbers, visible fields, and free text.
The default is the existing KiCad stroke font implementation.
It covers the full KiCad Newstroke character table. A character outside the table draws as a question mark.

The supplied object must implement the public `SymbolFont` protocol:

```python
from eedesigncore import SymbolFont

def make_preview(font: SymbolFont) -> str:
    return render_symbol_svg(symbol, view_box=ViewBox(-6, -5, 12, 10), font=font)
```

`SymbolFont.text_strokes` receives these arguments:

| Argument | Meaning |
| --- | --- |
| `text` | The displayed text. |
| `size` | Horizontal and vertical text sizes in integer nanometers. |
| `position` | Text anchor in integer nanometers, with Y downward. |
| `thickness` | Effective pen width in integer nanometers. |
| `angle` | Counterclockwise text rotation in degrees. |
| `mirror`, `italic` | Saved text effects. |
| `halign`, `valign` | Horizontal and vertical alignment. |

The method returns a list of point lists, one list per continuous glyph stroke.
Points use the same nanometer coordinates as the anchor.
The font applies rotation, alignment, mirroring, and italic effects before returning points.
The exporter converts the points to SVG paths and applies the text color and pen width.
Bold text affects the effective pen width.

For text boxes, the font must also supply `advance(text, size)`.
It returns the horizontal text advance in integer nanometers.
The exporter uses it to wrap words at the saved box width.
The built-in `StrokeFont` supplies both methods.
Text box line spacing uses the font's line_pitch method when the font has one. A font without it uses KiCad stroke-font spacing.

Load external font data before calling the export. The operation does not search installed fonts by name.
An input `font_face` name does not override the supplied font.
A font that measures text but cannot return glyph strokes raises `ValueError` when visible text needs it.

## Current drawing limits

Supported symbol body shapes are arcs, circles, rectangles, rounded rectangles, polylines, and cubic Bezier curves.
All standard pin shapes draw in the four cardinal directions.
Zero symbol stroke width uses the default schematic pen. Negative width suppresses the outline.
This differs from the board zero-width behavior.

Symbol text boxes draw their borders, fills, and wrapped text.
Margins, alignment, rotation, and hidden text use the saved settings.
Footprint text boxes also draw through the board export.
Knockout text and text boxes draw filled backgrounds with glyph-shaped holes.
Hatched fills draw clipped polygons.
The built-in font formats overbars, subscripts, superscripts, and stacked pin-number groups.
PCB drawing exports also cover curves, targets, tables, images, and every saved barcode type.
Custom pad copper merges its anchor and primitives, including Bezier primitives.
Curved polygon boundaries can use different vertices from KiCad.
Barcode drawing uses the in-memory encoder from the zxing-cpp Python dependency.
Custom fonts can interpret text markup through the supplied text argument.
The export does not load external or embedded fonts automatically.
The operation produces a deterministic preview, not a byte-identical KiCad SVG export.

## Verification

The focused tests check XML validity, unchanged inputs, unit selection, inheritance, coordinates, colors, visibility, and font control.
They also prohibit file access and process creation during both export calls.
`scripts/library_svg_oracle.py` compares generated pin and text examples with the installed KiCad SVG exporter.
The script measures and removes KiCad's page translation before comparing geometry.
It selects the plot clock style to compare against KiCad plot output.
