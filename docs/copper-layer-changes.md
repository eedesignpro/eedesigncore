# Copper layer changes

These operations add, remove, or renumber copper layers on a loaded board and remap every item that names a copper layer.

The root exports inspect_copper_layer_usage, plan_copper_layer_change,
apply_copper_layer_change, and validate_copper_layer_references.
CopperLayerReference identifies each native reference. CopperLayerChangePlan
binds an explicit old-to-new map to the source board content.

Supply new_layers in native order and a mapping entry for every old copper
layer. A None target requests removal. Apply requires the complete resulting
BoardStackup. The caller owns material choices and dielectric thickness.

The operation returns a detached board. It retains positions, nets, object
identifiers, and retained layer metadata. It remaps native content references,
backdrill endpoints, pad layer sets, and plot masks. It clears stale zone fills.

Occupied removed layers block apply. Merged or reordered layers, removed outer
faces, nonadjacent microvias, and generated objects need separate decisions.
The operation does not delete routed objects, place vias, calculate connections,
refill zones, or edit project settings and rule files. The caller must coordinate
those files and use KiCad for electrical checks.

Content and setup zone-default references appear in the inventory. The stackup, plot masks,
project files, and stale fill records are outside that inventory. Apply handles
the native layer table, supplied stackup, and plot masks separately.

Focused tests are in tests/unit/test_copper_layers.py.

## Validation and refusals

Malformed mappings raise ValueError. Content problems appear in plan.blockers.
Apply rejects blockers, altered plans, and stale plans with ValueError.
No failed operation changes the input board or the supplied stackup.

Layer names and ordinals must agree with a supported KiCad layer table.
Copper and dielectric stackup entries must alternate. Structural thickness must be positive and finite.
Surface thickness can be zero. No fabrication minimum or material preference is imposed.
The even count from two through thirty-two is the pinned KiCad format range.

The map covers tracks, arcs, zones, pads, vias, drawings, private layers, zone properties,
setup zone defaults, pad zone connections, and per-layer padstack entries.
Both plot masks remap enabled old copper bits. Other bits remain unchanged.
Disabled-layer plot selections are presets and do not count as occupied copper.

Pad wildcard sets remain wildcard sets. *.Cu follows enabled board copper.
F&B.Cu remains the face pair. *In.Cu follows enabled inner copper.
The Inner padstack entry remains the shared inner-layer setting.
Inspection reports these literal tokens rather than expanding them into occupied-layer claims.
New layers use the unchanged base or shared-inner pad geometry unless the caller supplies further pad changes.

Through vias must span both faces. Blind and buried vias retain ordered endpoints.
Microvia endpoints must remain adjacent. No operation replaces, splits, or moves a via.
Secondary and tertiary drill endpoints follow the map. Collapsed or disabled endpoints are invalid.
These structural checks do not establish manufacturability or electrical connectivity.

Fixed footprint stackups, generated objects, and copper wildcards outside pad layer sets are unsupported.
They produce diagnostics. Resolve them before using these operations.
Unknown layer names, duplicate layer definitions, and duplicate padstack entries are invalid.
There is no occupied-layer deletion or layer-merging disposition in this release.
No operation selects symmetry, construction defaults, routing, project-file edits, or design rules.

## Example

The caller can insert middle layers while retaining content near both faces:

```python
mapping = {
    "F.Cu": "F.Cu",
    "In1.Cu": "In1.Cu",
    "In2.Cu": "In2.Cu",
    "In3.Cu": "In5.Cu",
    "In4.Cu": "In6.Cu",
    "B.Cu": "B.Cu",
}
plan = plan_copper_layer_change(
    board,
    new_layers=["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "In5.Cu", "In6.Cu", "B.Cu"],
    mapping=mapping,
)
if plan.blockers:
    raise ValueError(" ".join(plan.blockers))
changed = apply_copper_layer_change(board, plan=plan, stackup=proposed_stackup)
```

The caller supplies proposed_stackup. This package does not choose the layer mapping.
