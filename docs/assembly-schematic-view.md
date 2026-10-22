# Native and assembly schematic pages

The file-IO page format serves native schematic sheets and generated assembly roots.
The root is flat at board level. Every board occurrence remains visible as its own block.
This is not a flattened component schematic or an editable KiCad combined project.

## Generate an assembly root

```python
from eedesigncore import NetbomAssembly, prepare_assembly_view

assembly = NetbomAssembly.load("system.assembly.json")
view = prepare_assembly_view(assembly)
view.save("system.schematic-view.json")
root = view.pages[view.roots[0]]
svg = root.svg
```

No source files or network access are needed for a NetBOM-only assembly root.
The assembly resolver supplies all connections. Drawing crossings never create electrical connections.
The initial layout uses a vertical board column and separate orthogonal routing lanes.
This deterministic layout favors visible identities and non-overlapping blocks over minimum drawing area.
One logical page grows with the assembly. It is not constrained to a printed paper size.

Connector bundles represent mates, not single nets or KiCad buses.
Every resolved bundle retains each contact pair, qualified pin identifiers, local net identifiers, and system-net identifiers.
Unresolved mates retain their declared mapping but expose no resolved contact pairs.
Inferred mates retain their evidence status. Dashed bundles denote inferred connections and red bundles denote unresolved connections.
Expected unmated connectors remain visible. Undeclared connectors are outside assembly completeness claims.
A mate that names a missing board remains in metadata and diagnostics without inventing a board block.

## Declared mechanical stacks

```python
from eedesigncore import AssemblyEvidence, AssemblyStack, prepare_assembly_view

stack = AssemblyStack(
    name="main stack",
    instances=["baseboard", "controller", "sensor"],
    evidence=AssemblyEvidence(source="Mechanical assembly", locator="stack order", revision="C"),
)
view = prepare_assembly_view(assembly, stacks=[stack])
```

Stack order is caller-supplied. Electrical connections and matching XY positions do not prove mechanical stacking.
The generator spreads each declared stack into ordered, labeled blocks. No board is hidden behind another.
The drawing does not assert physical scale, mating height, collision clearance, or installation.
Unspecified boards follow in identifier order and state that mechanical placement is unspecified.

## Native and in-memory assemblies

```python
from eedesigncore import AssemblyWorkspace, NetbomAssembly

workspace = AssemblyWorkspace(NetbomAssembly(assembly_id="system"))
workspace.open_design("baseboard", "baseboard/baseboard.kicad_pro")
workspace.open_design("sensor", "sensor/sensor.kicad_pro")
# Add caller-supplied mate definitions and mates through workspace.assembly.
inspection = workspace.inspect()
result = inspection.execute("board_query", {
    "instance": "baseboard", "operation": "native_sources",
})
view = workspace.schematic_view()
```

Use add_design instead of open_design when objects are already loaded:

```python
workspace.add_design("controller", board, schematic=hierarchy, project=project)
```

add_design accepts a NetBOM, board, schematic, or sheet hierarchy.
Inputs are copied. Source files and caller-owned objects remain unchanged.
Adding native data to an existing occurrence requires its NetBOM snapshot to match exactly.
After replacing or removing an attached occurrence, create a new workspace. Stale attachments fail explicitly.
Native geometry and source queries use the selected board's own coordinate frame.
Existing NetBOM assembly save/load remains portable and does not serialize native attachments.
The generated schematic view saves prepared source drawings, navigation metadata, and the assembly root.

## Native schematic pages

```python
from eedesigncore import load_hierarchy, prepare_schematic_view

hierarchy = load_hierarchy("controller.kicad_pro")
view = prepare_schematic_view(hierarchy, document_id="controller", instance="controller")
```

Native and assembly pages use SchematicView and SchematicPage.
Native sheets retain their hierarchy. Repeated sheet and board occurrences receive distinct identifiers.
Per-occurrence references, units, and saved alternate pin selections remain available.
Native SVG groups identify source geometry and link to resolved electrical nets.
Symbol items carry body bounds and complete visible bounds.
See [schematic preparation for viewers](schematic-preparation.md) for coverage, identities, errors, and the viewer example.
The same operation accepts a project path or root schematic path.

Paper dimensions determine the initial native window. Supply sheet_bounds by occurrence path when a different window is needed.
A sheet without known paper dimensions requires explicit bounds. Drawing frames and automatic content fitting are not supplied.
Coverage records identify missing content and unsupported drawings.

## Viewer contract

The schema is schematic-view-1. It is independent of the viewer's command and application-revision formats.
The consumer entry point should accept the same document type for native sheets and assemblies.

| Field | Meaning |
| --- | --- |
| document_id | Caller-selected logical document identity |
| source_revision | Content identity of the prepared source snapshot, not a GUI revision counter |
| units | Millimeters. X increases right. Y increases down |
| roots | Initial page identifiers |
| pages | Page identifiers mapped to drawings and metadata |
| page.bounds | x, y, width, height in the same frame as the SVG viewBox |
| page.parent | Parent page identifier for hierarchy navigation |
| page.items | Selection identities mapped to source, navigation, and electrical metadata |
| page.capabilities | Explicit available features. Do not infer unsupported features from page kind |
| item.target_pages | Source page destinations. Empty means no prepared destination |
| item.contacts | Resolved mate contact pairs with qualified pins and system nets |
| item.declared_pairs | Original contact-number mapping, including unresolved mates |
| item.route | Orthogonal bundle points. They do not encode electrical junctions |
| item.source | Board occurrence and optional sheet path, source UUID, or NetBOM record identifier |

SVG data-item-id values identify entries in page.items.
Multiple groups can share one identity because a native symbol has foreground and background graphics.
Nested connector groups take selection precedence over their enclosing board group.
A bundle can contain several distinct system nets. Highlight only its associated contact or bundle context as appropriate.
Never imply that highlighting the bundle shorts its constituent nets.

The viewer owns loading, rendering, navigation, hover, selection, and command names.
Its host owns stale-job rejection and monotonically increasing application revisions.
File-IO owns connection resolution, qualified source identities, and the root layout.
The root's white route underlay separates crossing bundles. No junction dots are generated.

prepare_assembly_view can accept native_views keyed by board occurrence for low-level presentation composition.
That function validates occurrence ownership and navigation identities, not electrical agreement with separately supplied drawings.
Prefer AssemblyWorkspace for native input preparation from one coherent snapshot.

## Portable data and validation

SchematicView.save and load work without original KiCad sources after preparation.
Drawings contain no external resources. Source locators are evidence, never file-opening instructions.
Readers reject active SVG elements, remote image references, unsupported schemas, dangling page links, and cyclic parent links.
Only embedded PNG and JPEG image references are permitted.
The JSON reader retains existing bounded input limits. Each SVG page is limited to 64 MiB.
Documents allow at most 4096 pages and 1000000 selection items.
Bounds and coordinates must be finite. Oversized output fails explicitly.

The view contains prepared graphics and metadata, not all NetBOM records.
Distribute the assembly NetBOM beside the view when full system inspection is required.
System-net identifiers and qualified pin records join the two datasets.

## Limits

This package does not include a browser viewer.
Component-level flattening and a KiCad-openable assembly export are not supported.

## Selected fonts

Schematic preparation accepts the same loaded font as direct SVG export.
Pass `font=load_font("Cascadia Mono")` to `prepare_schematic_view` or `AssemblyWorkspace.schematic_view`.
The selected font controls native schematic drawing and item selection bounds.
See [font selection](svg-fonts.md) for dependencies, measurements, and limits.
