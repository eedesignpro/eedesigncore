# Font choice for schematic and symbol SVG

Both SVG operations accept a loaded font through `font`.
The default is KiCad Newstroke, including its complete bundled Unicode glyph table.
Glyphs decode on first use and remain available for later exports.
Characters outside the native table still use its question-mark fallback.

## Select an outline font

Install the optional `fonts` dependencies with the same dependency source used for the Python package.
The dependency name is `eedesigncore[fonts]`.
The default KiCad font does not require these extra dependencies.

```python
from eedesigncore import load_font, render_schematic_svg, render_symbol_svg

font = load_font("Consolas")
schematic_svg = render_schematic_svg(schematic, view_box=view_box, font=font)
symbol_svg = render_symbol_svg(symbol, view_box=view_box, font=font)
```

A local `.ttf`, `.otf`, or `.ttc` path also works:

```python
from pathlib import Path
from eedesigncore import load_font

font = load_font(Path("fonts/Example-Regular.ttf"))
```

Reuse the loaded object across exports. It retains the font bytes and recent shaped text.
Use `load_font("KiCad Font")` to select the default explicitly.
A font collection accepts `font_index` for a specific face.
The loaded object is an `OutlineFont`. It provides measurements and glyph outlines.
A caller-written `SymbolFont` remains supported.

## What the exporter does

Outline text becomes filled SVG paths. The viewer does not need the font installed.
The exporter uses HarfBuzz for character shaping and fontTools for the saved glyph curves.
Spacing, wrapping, alignment, rotation, mirroring, and text markup use the selected font.
Installed families select their available bold and italic faces.
If a requested style is absent, the exporter applies a synthetic bold or italic treatment.
Selecting a font file uses that face without searching for sibling files.
Explicit `bold` and `italic` load options select an installed style.

The same chosen font applies to text, text boxes, fields, pins, labels, and table cells.
Stored face names do not trigger automatic font-file access.
The caller chooses whether to use one export font or create separate exports with different fonts.

## Limits and errors

Font lookup is explicit. It never derives a font path from a schematic field.
Windows lookup reads registered fonts. Linux and macOS lookup checks standard font directories.
Installed-name lookup currently checks the first face in a font collection.
Use a collection file and explicit index for other faces.
A missing name raises an error. The exporter does not silently select a different family.
A missing glyph uses the selected font's missing-glyph outline.

Only local outline fonts are supported. Remote paths and URLs are rejected before file access.
Each font file has a size limit of 64 MiB. Shaped text has a limit of 100000 characters per run.
Color bitmap fonts and SVG-only fonts are not supported.
Variable fonts use their default variation. Custom variation axes are not exposed.

Outline exports retain curves instead of KiCad's polygon approximation.
Font scaling follows KiCad, but FreeType hinting and polygon approximation can produce small coordinate differences.
Do not treat outline SVG paths as byte-identical KiCad plot output.
The default stroke-font path retains the existing geometric comparisons.

Text boxes retain interior blank lines. A final newline does not add an extra line for vertical placement.
Outline-font line spacing follows KiCad's integer face-height ratio and the selected bold or italic face.
Use `scripts/check_text_box_font_oracle.py` to compare glyph bounds against native KiCad for an installed font.

## Prepared schematic views and selection bounds

Pass the same loaded font to schematic preparation:

```python
from eedesigncore import load_font, prepare_schematic_view

font = load_font("Cascadia Mono")
view = prepare_schematic_view(schematic, document_id="design", font=font)
```

For attached native pages, use `workspace.schematic_view(font=font)`.
The font controls both the SVG paths and the bounds used to select schematic items.
Bounds enclose the emitted glyph paths and their stroke widths.
Bezier bounds use control-point enclosures, so they can exceed the visible curve.
Omitting the font retains the KiCad stroke-font default.
The generated assembly overview retains its existing browser monospace text.
Embedded fonts remain unsupported. Explicit font selection does not load embedded font data.

## Measure and draw text with resolved fonts

`resolve_font` finds an outline face for a requested family and style.
Its default policy is strict and matches `load_font`.
The `kicad-system` policy uses Linux Fontconfig substitution, as KiCad does.
Use this function when board text must be regenerated and the exact named face can be absent.
For unchanged saved board text, see the saved outline contract in [copper drawing SVG](copper-drawing-svg.md).

Resolve project variables before you collect font requirements.
Use the same text context for requirement collection and bounds.

```python
from dataclasses import replace
from eedesigncore.render.bounds import (
    PlotTextContext, plot_font_requirements, plot_content_bbox,
)
from eedesigncore.render.font_resolution import resolve_font

context = PlotTextContext(variables=variables, use_saved_text_cache=True)
for request in plot_font_requirements(board, layers, policy=policy, text_context=context):
    if request not in fonts:
        result = resolve_font(
            request.family, bold=request.bold, italic=request.italic,
            policy="kicad-system",
        )
        fonts[request] = result.font
        # Keep the result metadata in your build report.
context = replace(context, fonts=fonts)
box = plot_content_bbox(board, layers, policy=policy, text_context=context)
```

On Windows and macOS, use strict `load_font`, or use `resolve_font` with its default strict policy.
The `kicad-system` policy requires Linux Fontconfig. It does not use an arbitrary fallback on other platforms.
Use one font dictionary per document and font environment. Do not reuse it across different embedded-font collections.

`FontResolution` records the requested family and style, the resolved family and style, the file path, and the face index.
It also records whether a substitution occurred, whether the face is installed or embedded, and the loaded face.
Reuse its `font` object for both regenerated text bounds and rendering.
Log substitutions from this metadata. Do not change the saved design.
`embedded_files` accepts local font paths that the caller already extracted. The function does not extract board attachments or copy fonts.
Registered embedded faces join a private Fontconfig configuration and follow KiCad matching rules. There is no fixed replacement table for named fonts.
Synthetic styles and named variable-font instances that this implementation does not support fail with an error.
