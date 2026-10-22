# Rendered element callback

`render_board_svg` and `render_footprint_svg` accept optional `on_element`.
The callback receives a `RenderItem` and the final SVG fragment for each emitted shape.
The same fragment appears in the returned page. Rendering uses the existing geometry and style functions.

```python
fragments = []
pages = render_board_svg(
    board, layers, view_box, style=style, content=KICAD_CONTENT,
    on_element=lambda item, svg: fragments.append((item, svg)),
)
```

`RenderItem.layer` names the actual drawing layer. `item` retains the drawing item and its net fields.
`footprint` identifies the host footprint. `uuid` can be absent.
One item can emit multiple shapes. Each shape triggers a separate callback in page order.
Repeated requested layers still produce one page and one callback sequence.
Hidden and unsupported items emit no callbacks. Unsupported items still use `on_skip`.
The callback does not change normal output. Callback exceptions propagate.

Fragments use the coordinate system of the page wrapper. Preserve that wrapper when assembling another page.
With text resolution, callback items belong to the resolved copy. Use stored identifiers for associations across copies.
Treat callback items as read-only. The renderer does not protect against mutations made by caller code.
This callback does not classify objects, compute connectivity, or change fill behavior.
The caller still selects a style that preserves the intended filled regions.
