# Drawing sheets

A drawing-sheet template stores graphics relative to page corners and margins.
It does not own the document title, page count, paper size, or project variables.
Supply these values when you prepare page graphics or export SVG.
No KiCad process is required.

## Read, edit, and save

```python
from eedesigncore import DrawingSheet

sheet = DrawingSheet.from_file("company.kicad_wks")
sheet.setup.left_margin = 12
for item in sheet.items:
    if item.kind == "tbtext" and item.name == "title":
        item.text = "${TITLE}"
sheet.to_file("company-edited.kicad_wks")
```

`from_string` and `to_string` provide the same operations without file access.
`load_wks` accepts text. `dump_wks` returns text. `write_wks` saves a template.
File operations accept local paths only.
Unchanged files retain their source text, comments, line endings, and UTF-8 byte-order mark.
After an edit, the writer emits the current format and converts legacy text notation.
The edited output does not retain source comments outside item comment fields.
Native-save byte identity for edited templates is not yet verified.

Supported roots are `page_layout`, `drawing_sheet`, and `kicad_wks`.
Unsupported future versions, unknown fields, and malformed data raise `KicadParseError`.
The reader does not apply the native repair for malformed legacy parentheses.
Invalid embedded image data raises an error instead of silently removing the image.

## Construct in memory

```python
from eedesigncore import DrawingSheet, DrawingSheetCoordinate, DrawingSheetItem

sheet = DrawingSheet(items=[
    DrawingSheetItem(
        kind="rect",
        position=DrawingSheetCoordinate(anchor="ltcorner"),
        end=DrawingSheetCoordinate(anchor="rbcorner"),
    ),
    DrawingSheetItem(
        kind="tbtext",
        name="title",
        text="${TITLE}  ${#}/${##}",
        position=DrawingSheetCoordinate(x=5, y=5, anchor="rbcorner"),
        justify_h="right",
        text_size=(2, 2),
    ),
])
```

Item kinds are `line`, `rect`, `tbtext`, `polygon`, and `bitmap`.
Coordinates, widths, text sizes, margins, and repeat increments use millimeters.
Angles use degrees. Corner anchors are `ltcorner`, `rtcorner`, `lbcorner`, and `rbcorner`.
Increments move away from the selected corner before page-coordinate conversion.
Polygons have local points and an independent rotation.
Bitmaps contain base64 PNG or JPEG bytes. They have a center position and scale.
An empty template produces an empty page. It does not substitute the KiCad default template.

`page_option` accepts `all`, `page1only`, or `notonpage1`.
The explicit `first_page` flag selects page filtering independently of the displayed page number.
The repeat range is 1 through 100. The default prepared-item limit is 100000.
Later repetitions outside the drawable page are omitted as KiCad does.
Polygon clipping uses the native integer-rounded local bounds.

## Prepare graphics and inspect text

```python
from eedesigncore import Paper, TitleBlock, TextResolutionContext, drawing_sheet_graphics

page = drawing_sheet_graphics(
    sheet,
    paper=Paper(paper_type="A4"),
    title_block=TitleBlock(title="Power supply", rev="B"),
    text_context=TextResolutionContext(
        page="2", page_count=4, filename="power.kicad_sch",
        sheet_name="Power", sheet_path="/Power/",
        variables={"PROJECT_CODE": "Example"},
    ),
    first_page=False,
)
for path, text in page.texts.items():
    print(path, text.raw, text.resolved, text.diagnostics)
```

`DrawingSheetGraphics` contains page dimensions, editable page graphics, source-item paths, and text results.
Its `schematic` member carries only the generated drawing graphics. It is not a project schematic.
Its `item_paths` preserves drawing order and maps generated graphics to template items and repetitions.
Edit existing prepared graphics directly. Rebuild from the template after adding or removing items.
Preparing or rendering a page does not change the template.

The text resolver handles title-block fields, paper, layer, page context, project variables, and expressions.
Supply `kicad_version` when a template uses `KICAD_VERSION`.
Missing context leaves a diagnostic and retains the unresolved text.
`complete` is false when any generated text has a diagnostic.
Legacy percent variables and overbar notation resolve without changing stored source text.
KiCad repeats labels from their base text without resolving each copy again.
Repeated unresolved labels retain that behavior and report a diagnostic.

## Export SVG

```python
from eedesigncore import render_drawing_sheet_svg

svg = render_drawing_sheet_svg(page)
```

The renderer also accepts a template directly, with the same page and text inputs.
It draws bitmaps first, then text and graphics in template order.
Standard page dimensions follow KiCad rounding to mils. Custom paper uses explicit millimeter dimensions.
The default plot pen width is 0.1524 mm. The caller can change `default_line_width`.
Use `color` for the default color and `precision` for coordinate decimal places.
Stored text colors override the default color.
Use `view_box` to align output with another document export.
This operation returns a separate SVG. It does not automatically add a drawing sheet to other SVG operations.

The optional `font` argument controls glyphs. It must provide `text_strokes`.
Constrained text also requires `text_box` so the renderer can measure and scale the text.
Stored font face names remain available, but do not cause font files to load.
Pass the same font to preparation and rendering when you prepare a page separately.
The default uses the built-in KiCad stroke font.

Image content stays embedded in the SVG. The renderer does not fetch external files or URLs.
Text and SVG attributes use the shared escaping rules.
