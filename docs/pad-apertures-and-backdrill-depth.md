# Pad apertures and backdrill depth

These functions describe board geometry for a caller that builds its own output, such as a 3D model.
They read a loaded board and do not need a CAD program.
They do not change the board.

## Backdrill depth

```python
from eedesigncore.pcb.machining import (
    CopperDepthInterval,
    resolve_backdrill_depth,
)

depth = resolve_backdrill_depth(
    via.backdrill,
    copper_intervals,
    policy="native",
    surface_bottom_mm=physical_bottom,
    surface_top_mm=physical_top,
    entry_extension_mm=outside_clearance,
    axial_margin_mm=native_axial_margin,
)
```

The first argument is a saved `BackdrillInfo` record, from a via or a pad.
Supply `CopperDepthInterval` records from front to back.
Their fields are `layer`, `bottom_mm`, and `top_mm`.
They must be finite, distinct, ordered, nonoverlapping, and inside the supplied surface bounds.
Z increases toward the board front. The caller chooses the datum.
Do not mix KiCad display elevations with consecutive physical layer elevations.

The result is a `BackdrillDepth`. It contains the entry layer, stop layer, diameter, bottom, top, policy, and optional next layer.
Keep the source via or pad UUID with it. Secondary and tertiary drills need separate feature identities.
The caller checks primary diameter, slot support, and material spans.
This function resolves axial depth only.

The `native` policy reaches both endpoint copper bounds and applies the explicit axial margin.
An outer endpoint extends outside the supplied surface bounds by the entry clearance.
The `between-layers` policy places a flat floor midway toward the next copper layer.
It accepts no axial margin, because a margin would move the selected floor.
Both policies use flat-ended cylindrical openings for circular holes.

## Aperture rules

```python
from eedesigncore.dru import DrcFile
from eedesigncore.render.aperture_requirements import aperture_rule_dependencies

rules = DrcFile.from_file("board.kicad_dru")
dependencies = aperture_rule_dependencies(rules, "F.Paste")
```

The function accepts a parsed `DrcFile` and a front or back mask or paste layer.
Each dependency contains its rule index, rule name, and constraint type.
The function does not evaluate conditions or severity. It reports every constraint that can apply.
Parse errors propagate. An unknown rule does not become an empty dependency list.

An empty result means only that the parsed rules contain no aperture constraints.
It does not prove complete geometry support or replace the board minimum mask width.
Hole, via, and clearance rules do not affect the result.
Minimum mask width, unsupported graphics, and unresolved text remain separate checks.

## Pad aperture geometry

```python
from eedesigncore.mod.models import Pad
from eedesigncore.render.apertures import pad_aperture_margin
from eedesigncore.render.pads import pad_outline
from eedesigncore.render.traversal import KICAD_CONTENT, iter_render_items

for entry in iter_render_items(board, layer, KICAD_CONTENT):
    if isinstance(entry.item, Pad):
        margin = pad_aperture_margin(
            entry.item,
            layer,
            footprint=entry.footprint,
            setup=board.setup,
            version=board.version,
        )
        outline = pad_outline(entry.item, layer, entry.to_board, margin=margin)
```

The example shows the pad branch only. Send drawings and filled areas in the same loop to their own handlers.
Keep the source UUID, footprint UUID, layer, and resolved margin with each outline.

`PadOutline` supplies filled polygons, circular discs, stroked segments, stroked arcs, and compound polygons in board millimeters.
Compound polygons contain holes. The SVG renderer fills them with the even-odd rule.
Keep this containment when you convert them to faces. Do not fill every ring as a separate solid.
The function merges custom pad primitives and applies the clearance offset before placement.
It does not need an SVG file or a KiCad export.

Some curves stay analytic. Rounded and chamfered pad outlines and custom offsets can contain sampled polygons.
The outline does not claim that all curves are analytic or that every offset vertex matches KiCad.
Do not infer an original arc from sampled vertices without an explicit reconstruction rule.
See [operations](operations.md#pad-solder-mask-and-solder-paste-openings) for how the margin values inherit.
