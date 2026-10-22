# Schematic bounds and collision checks

Measure existing items or newly created items before choosing their final positions.
The helpers reuse the schematic renderer, the existing `BoundingBox`, and the existing Pyclipper dependency.
They do not read files, change geometry, route wires, or run electrical rule checks.

## Check a sheet

```python
from eedesigncore import (
    BoundingBox, context_for_sheet, find_schematic_overlaps,
    load_hierarchy, measure_schematic,
)

hierarchy = load_hierarchy("controller.kicad_pro")
for node in hierarchy.walk():
    context = context_for_sheet(hierarchy, node.kiid_path)
    measurements = measure_schematic(node.schematic, text_context=context)
    conflicts = find_schematic_overlaps(
        measurements,
        clearance_mm=0.5,
        page_bounds=BoundingBox(min_x=10, min_y=10, max_x=287, max_y=200),
    )
```

Check each sheet occurrence separately. Do not combine measurements from different pages.
Use a drawing window that matches the paper size and desired margins.
The page check reports geometry beyond that window. It does not infer title-block keepout regions.
`measure_schematic` resolves the display copy once, then measures each drawable item.
The source schematic remains unchanged.

## Measure one item

```python
from eedesigncore import measure_schematic_item

measurement = measure_schematic_item(
    schematic.symbols[0], schematic=schematic, text_context=context,
)
box = measurement.bounds
if box is not None:
    width, height = box.width, box.height
```

Supply `schematic` when measuring a symbol so the helper can read its embedded definition.
Supply `text_context` for page references and other variable text.
Global page-reference visibility and formatting follow the explicit project settings.
For an isolated item with no project context, unresolved variables remain explicit diagnostics.

`SchematicMeasurement` contains `item_id`, `kind`, `bounds`, `parts`, `contacts`, and `diagnostics`.
Its `complete` property is false when geometry or visible text cannot be measured reliably.
An empty visible item can have no bounds while still being complete.

Bounds use sheet millimeters with X rightward and Y downward.
`min_x`, `min_y`, `max_x`, and `max_y` mean left, top, right, and bottom.
Width, height, center, and size come from the existing `BoundingBox`.
Measurements include visible stroke width and a small drawing-rounding allowance.
Rotations, mirrors, justification, and saved field positions use the renderer's existing behavior.

Each `SchematicPartBounds` contains a name, bounds, contacts, optional segment geometry, stroke width, and obstacle flag.
Parts separate fields from the body, pin text from pin graphics, and disconnected symbol graphics from each other.
Table cells also have separate text parts.
Sheet backgrounds appear as non-obstacle containers. Their borders, fields, and pins remain obstacles.
Part names identify drawing parts within a measurement. They are not permanent source identifiers.

## Return measurements from creation

```python
from eedesigncore import create_hierarchical_label

created = create_hierarchical_label(
    "ENABLE", at=(50.8, 25.4), direction="right", shape="input", with_bounds=True,
)
schematic.labels.append(created.item)
measurement = created.measurement
```

`create_global_label`, `create_hierarchical_label`, `create_sheet`, and `add_sheet_pin` accept `with_bounds=True`.
They then return `MeasuredSchematicItem`, which contains `item` and `measurement`.
Their default return values remain unchanged.
The optional `text_context` supplies display inputs for the measurement.
For existing placement operations, call `measure_schematic_item` after the operation.
Measure again after changes to geometry, text, display settings, page numbers, or sheet occurrences.
The returned measurement is a snapshot, not a live view.

## Collision behavior

`find_schematic_overlaps` accepts a collection of measurements and returns `SchematicCollision` records.
Each record names both items, both parts, the conflict kind, and the bounding region of the conflict candidate.
A page-limit record has no second item. Kinds are `overlap`, `clearance`, and `outside_page`.
Every item identifier must be distinct. Whole-sheet measurement creates distinct identifiers automatically.

Wire and bus crossings are permitted with or without a junction.
Wires crossing text, fields, labels, graphics, or symbol bodies remain conflicts.
Connections at actual pin and label contacts are allowed only within the small contact region.
This exception never exempts the remainder of the wire.
Junction contacts are allowed along a wire. No-connect marks can touch their matching pin.
These are drawing checks. They do not prove electrical correctness.

The broad comparison sorts part bounds along X and rejects separated pairs.
Wire comparisons then clip the actual segments against the other part's bounds with Pyclipper.
Segment-to-segment comparisons use buffered segment geometry.
This prevents a diagonal wire's large bounding rectangle from reporting unrelated items as collisions.

Body text inside its own shape and pins inside their own symbol are intentional.
The checker still checks placed fields against other parts of their owner.
It also checks sheet-pin text against other sheet pins and sheet fields.

## Limits

These checks reserve bounding rectangles for text and graphic parts. They are not pixel-perfect shape intersections.
Curves use conservative control bounds where the renderer does so.
A circle's corner regions or a complex graphic's empty interior can produce conservative warnings.
The collision region is a bounding candidate, not a claim that every point in it is occupied.
This tool favors avoiding overlap during placement.

Missing definitions, unsupported geometry, unresolved visible text, and named fonts without an explicit font produce diagnostics.
The checker rejects incomplete measurements by default.
`allow_incomplete=True` checks only the available geometry. An empty result then does not establish clearance.
Supply the same `style` and `font` used for drawing when defaults differ.
The helper does not load fonts or local KiCad preferences automatically.

Supported items include symbols, all label kinds, sheets, sheet pins, free text, text boxes, tables,
wires, buses, bus entries, junctions, no-connect marks, graphic shapes, rule areas, and images.
Groups are membership records and add no separate drawn geometry.

## Demo

`scripts/create_collision_checked_demo.py` builds a mixed native schematic from installed KiCad symbols.
It saves each page, reloads the hierarchy, resolves text, and checks all measurements and page limits.
It rejects a demo with collisions or incomplete measurements.
Native SVG export and visual review supplement the check.
