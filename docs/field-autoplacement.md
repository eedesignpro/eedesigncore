# Automatic schematic field placement

Use `autoplace_fields` after a script builds or changes a schematic.
It places fields for schematic symbols, global labels, and hierarchical labels without reading nearby items.
It needs no running KiCad process and writes no files.

```python
from eedesigncore import Schematic, autoplace_fields

schematic = Schematic.from_file("controller.kicad_sch")
autoplace_fields(schematic)
schematic.to_file("controller.kicad_sch")
```

The operation is an isolated counterpart of the schematic editor's automatic field placement command, bound to the O key.
It is not an exact reproduction of KiCad placement.

## Placement choices

| Argument | Default | Meaning |
| --- | --- | --- |
| `style` | `"auto"` | Automatic placement or forced `"left"`, `"right"`, `"top"`, or `"bottom"` |
| `grid_mm` | `1.27` | Placement-axis grid and symbol field-row increment, in millimeters |
| `gap_mm` | `0.635` | Horizontal body padding. Symbol vertical padding uses 60 percent. Labels also use this between rows. |
| `definition` | `None` | Symbol definition when placing one symbol outside a schematic |

Forced sides refer to sheet directions, regardless of symbol rotation or mirroring.
Symbol fields and forced field groups use horizontal text.
Left-side fields align right. Right-side fields align left. Top and bottom fields center horizontally.
Grid rounding moves the group away from its owner on the selected axis.
Symbol rows use text heights rounded upward to the grid increment.
Each field centers within its row. The group centers along the other axis without additional rounding.
Label fields retain nearest-grid rounding on the other axis, with half-grid ties away from zero.
Negative coordinates use the same outward-placement behavior as positive coordinates.
Grid alignment applies to stored anchors, not every edge of a glyph.

Automatic symbol placement prefers sides with fewer of the symbol's own pins.
Shape, orientation, and power-symbol status break ties.
The group uses symbol body bounds, excluding external pin lengths.
Forced placement toward pins can overlap them. Use the collision checker after placement.
This is a deterministic policy, not KiCad's full preference algorithm.

Automatic global-label placement puts `Intersheetrefs` beyond the label body in its reading direction.
Other visible fields go below horizontal labels or to the right of vertical labels.
A forced style places all eligible fields together on the chosen side.
The label's own text offset contributes to placement because KiCad applies that offset when drawing its fields.
Automatic page references follow native KiCad orientation and justification.
Vertical labels have vertical references. Their positions do not snap to the grid.
The offset uses the label border length and native text margin.
Forced styles retain horizontal text and grid placement.

```python
autoplace_fields(schematic, style="right", grid_mm=1.27)
autoplace_fields(schematic.labels[0], style="bottom")
autoplace_fields(
    schematic.symbols[0],
    definition=schematic.lib_symbols[0],
    style="left",
)
```

For individual symbols, supply the matching definition with resolved inheritance.
Whole-schematic calls resolve embedded definitions and select each symbol's unit and body style.
They process the supplied schematic file only. They do not walk child sheets.
Global and hierarchical labels are supported. Other label kinds stay unchanged in whole-schematic calls.
Passing another label kind directly raises an error.

## Preserved data

The operation changes eligible field positions, angles, alignment, and the owner's automatic-placement flag.
It preserves field order and text content.
Hidden, private, empty, and excluded fields stay unchanged.
It does not create missing fields, including `Intersheetrefs`.
Use the [creation helpers](schematic-creation.md) to create formatted labels with the required fields.
Symbol positions, label anchors, label text, electrical shapes, net names, and UUIDs stay unchanged.
Repeated calls with the same inputs produce the same result.

All placements are planned before any target changes.
A missing definition, unsupported font, or invalid setting leaves the supplied schematic unchanged.

## Limits

The operation does not inspect wires, other symbols, other labels, page borders, or nearby graphics.
It can therefore overlap those items.
It does not move free schematic text or bind an editor key.
The default font is the KiCad stroke font. Pass `font=load_font("Cascadia Mono")` to use an installed outline font.
`create_global_label` accepts the same `font` argument, including when returning bounds.
Use the same font for placement, measurement, and rendering.
These helpers do not read local KiCad preferences automatically.
Text variables use their stored spelling for measurement. Resolve display values before requesting exact visual spacing.
Global-label geometry uses the renderer's default label expansion and text-offset ratios.
Custom editor settings can produce different spacing.

The grid must be finite and at least `0.0001` mm.
The gap must be finite and nonnegative.
The operation changes objects in memory. Use the normal `to_file` operation when ready to save.
