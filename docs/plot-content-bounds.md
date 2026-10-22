# Selected plot content bounds

This operation measures selected drawing content from an already loaded board.
It does not export a drawing, start KiCad, or change the board.
It supplements `pcb.board_bbox`, whose stored-item measurement contract stays unchanged.

```python
from eedesigncore.render.bounds import (
    PlotBoundsPolicy,
    PlotTextContext,
    plot_content_bbox,
    plot_font_requirements,
)
from eedesigncore.render.fonts import load_font

policy = PlotBoundsPolicy(stroke_mode="dxf_centerline", hide_footprint_values=True)
requests = plot_font_requirements(board, selected_canonical_layers, policy=policy)
context = PlotTextContext(
    variables=project_variables,
    fonts={request: load_font(request.family, bold=request.bold, italic=request.italic)
           for request in requests},
)
box = plot_content_bbox(
    board,
    selected_canonical_layers,
    policy=policy,
    text_context=context,
)
extent = None if box is None else (box.min_x, box.max_x, box.min_y, box.max_y)
```

The result uses board millimeters, with downward-positive Y. An empty selected plot returns `None`.
Reuse the loaded board and text context for repeated measurements. Font loading needs the existing `fonts` extra.
Use local font-file paths for repeatable font selection. The bounds function receives loaded font objects.
The font helper returns `PlotFontRequest` records with `family`, `bold`, and `italic` fields.
It finds only selected visible named text and respects the footprint field visibility policy.
Use these records as the font mapping keys. Family strings are accepted only for regular styles.

## Plot policy

`dxf_centerline` excludes graphic and track stroke expansion, matching KiCad's DXF command without contour mode.
`visible_ink` includes round stroke widths. It is a separate contract, not a DXF compatibility switch.
Pads retain their per-layer area geometry in both modes.
Footprint values and references can be excluded independently. Hidden text does not contribute.
The default actual drill marks policy includes pad drills, including pads on unselected layers, and vias on selected copper layers.
It clamps drill dimensions to the front padstack size, as KiCad's default DXF plot does.
Set `actual_drill_marks=False` only when the plot omits drill marks.

## Supported geometry

The function measures lines, true arcs, circles, rectangles, polygons, tracks, vias, padstacks, filled zones, and supported dimensions.
Arc extrema are calculated after footprint rotation. Rectangles use all corners.
Zones contribute selected-layer fills. Unfilled zone outlines and rule areas do not contribute.
The function includes footprint zones and trailing drawings.
Stroke-font text and text boxes reuse the existing drawing geometry functions, including text-box wrapping.
Named single-line text uses loaded outline fonts and analytic Bezier extrema.
By default, saved glyph caches do not replace loaded fonts. A transformed board can contain stale or absent caches.
Set `use_saved_text_cache=True` on the text context only for an unmodified board. See the saved outline contract below.

## Limits

Unsupported selected content raises `UnsupportedPlotContent`. Callers must not accept an incomplete box.
Named multiline, named text-box, and named knockout text remain unsupported in this release.
The text context resolves supplied variables and footprint properties. Advanced project expression context needs a separate extension.
Custom aperture rules are not evaluated. Callers must establish that the supported local/setup margins represent the requested plot.
Custom pad and aperture geometry inherits the existing `pad_outline` limits.
Tables, images, barcodes, and Bezier board drawings are not yet supported by this entry point.
Small drill symbols and fine-width plot adjustments are outside the actual-drill policy in this release.
Do not claim support for a whole item class from one board's matching outer bounds.

## Accuracy

On the boards checked, the result matched the bounds of a full selected-layer
KiCad DXF export within 0.005 mm. This checks the outer bounds only. It does
not prove complete plot equivalence for every board.

## Saved text and missing fonts

Use the explicit [font resolution](svg-fonts.md#measure-and-draw-text-with-resolved-fonts) and the [saved outline contract](copper-drawing-svg.md#saved-outline-contract) when measuring unchanged saved text.
The default remains strict. Keep the same resolved face for bounds and regenerated text.
