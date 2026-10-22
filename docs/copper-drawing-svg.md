# Copper drawing SVG contract

Use render_board_svg with LayerStyle to retain saved solid fills and layer colors.
The existing on_element callback identifies each source item and supplies its emitted SVG fragment.
Use on_skip to reject any drawing type that the consuming operation cannot omit.
Use the optional plot_text_context argument when text needs saved glyphs or selected font faces.

## Rectangle geometry

Board and footprint rectangles preserve their saved corner radius as circular SVG arcs.
The radius is clamped between zero and half the shorter side, matching KiCad ROUNDRECT construction.
Reversed rectangle endpoints are normalized when the radius is positive. Sharp-rectangle output remains unchanged.
The existing footprint transform applies to every line and arc point.
Selected drawing bounds use the same analytic arcs, so rotated rounded rectangles no longer use the larger sharp-corner bounds.
The default SVG precision and existing curve calculations remain unchanged.

## Dash patterns

render_board_svg reads setup.pcbplotparams.dashed_line_dash_ratio and dashed_line_gap_ratio.
An absent setup uses the normal ratios of 12 and 3.
Dash centerline length is max(dash_ratio - 1, 1) times stroke width.
Gap centerline length is max(gap_ratio + 1, 1) times stroke width.
Dot centerline length is 0.2 times stroke width.
The SVG wrapper retains round caps and joins.
This applies to drawing lines, arcs, circles, polygons, rectangles, Bezier paths, textbox borders, and table borders.
Tables pass the same ratios to their independently stored border strokes.
render_footprint_svg uses its optional setup argument for those board ratios as well as aperture settings.
Lower-level stroke_attrs and render_element accept optional dash_ratio and gap_ratio keywords. Their defaults remain unchanged.
Nonfinite ratios fail explicitly.

## Solid fills and text

LayerStyle preserves saved solid circle, rectangle, and polygon fills, including zero-width outlines.
A custom StylePolicy that returns fill='none' still disables rectangle and polygon fills by its own choice.
Use LayerStyle for copper area construction rather than replacing source fill with an outline-only style.
Source item and net_name values remain unchanged and available through the callback.
Named PCB text requires a loaded outline face or eligible saved glyphs.
The renderer does not substitute the built-in stroke font for a named face or silently drops a metrics-only font.
This correction does not introduce implicit font lookup or substitution.

## Font input across worker processes

Send font paths and variables as ordinary strings across a process boundary.
Load each required face once inside its worker, then reuse the context for selected layers.
No factory or font object must cross the process boundary.

```python
from pathlib import Path
from eedesigncore.render import OutlineFont, load_font, render_board_svg
from eedesigncore.render.bounds import PlotFontRequest, PlotTextContext

faces = {}
for entry in font_inputs:
    request = PlotFontRequest(entry["family"], entry["bold"], entry["italic"])
    face = load_font(Path(entry["path"]))
    if not isinstance(face, OutlineFont):
        raise ValueError("A named face needs an outline font file.")
    faces[request] = face
context = PlotTextContext(
    variables=project_variables,
    fonts=faces,
    use_saved_text_cache=unchanged_saved_text_geometry,
)
pages = render_board_svg(board, layers, view_box, style=style, plot_text_context=context)
```

The request records identify the intended style. Each path must name the chosen face for that style.
A family string alone is also accepted as a context key for regular text.
The existing resolve_font function supports explicit strict matching and an explicit Linux kicad-system policy.
The Linux policy reports the actual selected face and substitution. It does not modify the requested design font.
Extract embedded font files through the existing embedded-file API into caller-owned working storage.
Pass extracted paths explicitly. The drawing call does not extract files or search installed fonts.

Saved glyph use is an opt-in certification that text position, size, mirror, style, and footprint placement remain unchanged.
The saved cache lacks enough metadata to prove that certification automatically.
Matching non-knockout PCB text and fields use the saved board-coordinate polygons without a second footprint transform.
Changed text, changed angle, knockout text, or unsupported cache polygons require a loaded face.
Named-font absence is therefore a data requirement, not a rejection of the drawing type.

## Outline text geometry

Loaded faces support PCB text, multiline text, fields, text boxes, table cells, and dimension text.
Text boxes and table cells select their own requested faces from the context.
Non-knockout glyphs retain filled analytic SVG curves and nonzero winding.
Knockout text subtracts filled glyph contours, including glyph holes, from the text background.
Knockout text boxes subtract the same contours from their box background.
The result is one filled compound path with nonzero winding. Inner contours retain their opposite winding.
The glyph-hole islands remain material. The caller does not need white-paint subtraction.
Subtraction flattens glyph curves with a default chord-error budget of 0.001 mm plus coordinate rounding.
Subdivision depth and count limits fail explicitly rather than emitting unchecked contours.
This conversion applies only where polygon subtraction requires it. Ordinary glyphs remain curves.
No source item, UUID, net assignment, or layer changes during rendering.

Resolve document text and check selected-text diagnostics before rendering when project expressions or references are present.
The existing text_context argument still takes the resolved document and does not enforce those diagnostics.
The plot_text_context variables support the existing plot-text substitution rules for PCB text and fields.
The renderer does not claim complete native font-layout equivalence and does not add synthetic missing font styles.

## Saved outline contract

`PlotTextContext.use_saved_text_cache` defaults to False.
Set it to True only for a freshly loaded, unmodified board with its original text geometry.
With this setting, matching named text and cache angle use the saved board-coordinate glyph polygons.
Font requirement collection then omits those fonts, so a missing font does not fail early.
Changed resolved text, angle mismatch, empty cache, or knockout content requires regeneration.
Unresolved variables still raise an error. The renderer does not select a stale cache.

The cache does not record enough metadata to verify changed size, position, mirror state, or footprint placement.
Do not enable reuse on changed text unless the operation that changed it also keeps or clears the cache correctly.
Do not apply footprint rotation, translation, or mirroring again to the saved polygons. Their coordinates already include those transformations.
`render.board_text.saved_text_shapes(item, resolved_text)` exposes the same saved geometry for rendering.
`pcb_text_shapes(..., use_saved_cache=True)` uses that helper before text regeneration, under the same caller responsibility.

## Source evidence and limits

Reference source is KiCad tag 10.0.3.
common/render_settings.cpp GetDashLength, GetGapLength, and GetDotLength define the ratio correction and minima.
pcbnew/exporters/step/exporter_step.cpp:977 supplies board plot ratios during copper graphic construction.
pcbnew/plot_brditems_plotter.cpp:1100 uses the saved rectangle radius and normalized rectangle.
libs/kimath/src/geometry/roundrect.cpp clamps radius to half the shorter side and creates curved corners.
pcbnew/pcb_text.cpp TransformTextToPolySet subtracts rendered glyph geometry from the rotated text bounding hull for knockout text.
common/font/fontconfig.cpp:213 adds supplied embedded font files, sets the family and style fields, applies FcConfigSubstitute and FcDefaultSubstitute, then calls FcFontMatch.
It reports substitution separately and keeps the requested name.
common/font/outline_font.cpp:94 loads the selected file and face index and handles synthetic styling.
pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:862 associates saved polygons with the currently resolved font object.
common/eda_text.cpp:673 keeps that cache when text, angle, font, mirror, and offset remain valid.
So a system font substitution does not by itself require replacement of valid saved polygons.

The SVG contract uses analytic curves. It does not promise identical polygon approximation or dash subdivision to every native exporter.
PCB target output matches the KiCad plot path, not the polygon conversion path. The two KiCad paths draw targets differently.
