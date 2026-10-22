# Shared global-label borders

`global_label_outline` returns a closed border without constructing a KiCad board, schematic, or label.
The schematic SVG renderer uses the same helper.
The caller supplies measured text width and nominal text height in millimeters.

```python
from eedesigncore import global_label_outline

border = global_label_outline(
    text_width_mm=8.0,
    text_height_mm=1.27,
    shape="output",
    anchor=(20.0, 15.0),
    body_direction="right",
    pen_width_mm=0.1524,
    label_size_ratio=0.375,
)
```

The result is a tuple of XY pairs. The last point repeats the first.
Coordinates use millimeters with Y downward.
The helper does not center the page, change its scale, place text, or infer electrical direction.
Text measurement, text placement, pad drawing, and collision checks remain with the caller.
No center marker is added.

## Native border meanings

The anchor is KiCad's electrical connection point.
`body_direction` selects which side of that anchor contains the label text.
It accepts `left`, `right`, `top`, and `bottom`.
It does not state electrical flow.

| Native shape | Border |
| --- | --- |
| `input` | Pointed at the anchor end |
| `output` | Pointed at the far end |
| `bidirectional` | Pointed at both ends |
| `tri_state` | Same border as bidirectional, with distinct native meaning |
| `passive` | Rectangle |

Native input and output names must not be assigned from a diagram arrow description alone.
First define the design boundary and which end faces the connector.

## Connector diagrams

A caller that draws connector diagrams must choose its own mapping from signal direction to border shape.
Do not infer direction from a net name, copper, pad dimensions, or a 3D model.
The helper accepts only the native shapes in the table above. It rejects any other value.
Changing a native schematic label shape also changes stored electrical metadata. A drawing choice alone does not authorize that change.

Pin numbers stay beside physical contacts. Net borders do not replace pad outlines or move pin numbers into label centers.
The caller controls bottom-view mirroring separately from electrical direction.
After a page rotation or mirror, transform the border and label placement consistently while retaining the fixed connector origin.

## Native orientation

KiCad derives label body direction from text angle and horizontal justification.

| Body direction | Text angle | Horizontal justification |
| --- | --- | --- |
| Right | Horizontal | Left |
| Left | Horizontal | Right |
| Top | Vertical | Left |
| Bottom | Vertical | Right |

An angle alone does not determine the direction.
The helper accepts body direction explicitly so a diagram does not need to reproduce native label state.

## Limits and source

Text width must be nonnegative. Text height must be positive.
Pen width and expansion ratio must be nonnegative. All coordinates and dimensions must be finite.
The border follows KiCad's global-label geometry, including its small outline allowance.
It does not include stroke expansion outside the returned centerline or select an SVG fill.
Text offsets and font selection are separate from this border-only helper.

The standalone source is `src/eedesigncore/render/label_geometry.py` and uses only Python standard modules.
The supported installed import is the Python package root shown above.

Source references, KiCad tag 10.0.3:

```text
eeschema/sch_label.cpp:395  SCH_LABEL_BASE::SetSpinStyle
eeschema/sch_label.cpp:2172 SCH_GLOBALLABEL::CreateGraphicShape
eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp:4677
```

The file token `passive` maps to native `L_UNSPECIFIED` for these label borders.
