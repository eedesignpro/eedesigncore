# Netbom assemblies

A netbom assembly describes board instances and their mated connectors.
It retains each board's netbom and derives system nets from declared connector mates.
Matching net names or pin numbers never create a connection.

The assembly file is standalone JSON with schema `netbom-assembly-1`.
It contains embedded designs, board instances, reusable mate definitions, identified mates, and optional expected connectors.
It needs no original KiCad files, external packages, HTML, or network access for portable inspection.

## Electrical contract

A connector endpoint identifies a board instance and a local component record.
A mate identifies left and right connector endpoints and a reusable mate definition.
The definition maps left pin numbers to right pin numbers. Orientation is explicit.
The same definition can serve many mates. Repeated board instances remain separate.

A mapping describes ideal conductive connections only.
A cable containing resistors, switches, or other circuitry requires its own represented design and explicit relationships.
Comparison tools rank connector hypotheses. They do not certify compatibility, cable wiring, mating orientation, or physical continuity.
A component reference such as J1 does not prove that the component is a connector.
The caller supplies that role by selecting the component as a connector endpoint.

Pin maps must be one-to-one. Repeated pin numbers within either side of a definition are invalid.
A mapped number must identify exactly one physical pin record owned by that connector.
That pin must have exactly one saved net assignment and no explicit no-connect declaration.
Multiple pads represented by one physical pin remain one endpoint.

A missing definition, connector, pin, or net assignment blocks the whole mate.
An ambiguous pin or a no-connect declaration also blocks the whole mate.
No valid pair from a blocked mate contributes a system connection.
A connector used by multiple mates blocks all those mates, regardless of input order.

Unmapped connector pins generate issues. Valid mapped pairs remain connected.
The tools cannot determine whether an omitted pin was intentionally unused.
Expected connectors without mates generate separate issues.
Unlisted connectors and omitted cables remain unknown. A resolved assembly does not certify completeness.

Each system net contains qualified board-local nets joined by resolved mate pairs.
Board-local names, netclasses, rules, coordinates, and source records remain intact.
Resistors and other passive relationships never merge system nets.
Structural tracing can traverse selected passive relationships without declaring electrical equivalence or current flow.

## Investigate unknown mates

Use `mate_candidates` to compare one selected connector with connectors on other boards.
Candidates require board pads and either a J/P reference or a footprint name containing connector.
The query tests same-number mappings and ranks matching leaf net names.
GND, EARTH, VCC, VDD, and VSS matches rank after other names.
It removes sheet prefixes and compares case-insensitively. Raw net names remain in detailed comparisons.
Names beginning with unconnected- or Net-( do not contribute matching evidence.
This is a search heuristic, not an authorship classifier or an electrical equivalence rule.

Use `compare_connectors` to test same-number, reverse-numeric, or swapped-pair hypotheses for a selected connector pair.
It reports signal-name agreements, different names, connector footprints, and a rigid fit of available pad coordinates.
Different names do not prove a wrong mate. A real interface can rename a signal across boards.
A single connector can fit mechanically under several orientations. Compare the electrical patterns and all connectors together.

Use `placement_check` after recording tentative mates to fit all their contact positions between two board instances.
The default comparison tolerance is 0.1 mm. It is a query threshold, not a manufacturing requirement.
The result distinguishes a fit, a conflict, and missing coordinate evidence.
The fit covers XY coordinates only. It cannot certify mating height, collision clearance, or connector construction.
These inspection queries never add mates or join nets.

Record a chosen hypothesis with `AssemblyEvidence(derivation="inferred", method=...)` on its definition and mate.
The method must describe the reasoning or evidence used. Source descriptions retain the originating design locations.
The assembly emits an inferred-mate issue. Affected system-net summaries identify their dependence on inferred mates.
Stored inferred mates participate in conditional system connectivity. They do not become verified facts when saved.
The default evidence derivation is specified. Specified means caller-supplied, not necessarily human-authored or independently verified.

```python
candidates = inspection.execute("mate_candidates", {
    "instance": "baseboard", "id": base_connector_id, "other_instance": "timebase"
})
comparison = inspection.execute("compare_connectors", {
    "left_instance": "baseboard", "left_id": base_connector_id,
    "right_instance": "timebase", "right_id": module_connector_id,
    "mapping": "same_number", "limit": 20,
})
placement = inspection.execute("placement_check", {
    "left_instance": "baseboard", "right_instance": "timebase", "tolerance_mm": 0.1
})
```

If a saved schematic does not match the board, a netbom can retain separate component records with the same reference.
Select the board-resident record by its identifier. Do not collapse those records or treat the unresolved schematic association as confirmed.

## Build an assembly

Use `build_netbom` or `load_netbom` to prepare the input designs.
Then identify the board instances and their connector mates.

```python
from eedesigncore import (
    AssemblyEvidence,
    ConnectorMate,
    ConnectorMateDefinition,
    ConnectorPinPair,
    NetbomAssembly,
    load_netbom,
)

assembly = NetbomAssembly(assembly_id="sensor-system", name="Sensor and controller")
assembly.add_board("sensor", load_netbom("sensor.kicad_pcb"))
assembly.add_board("controller", load_netbom("controller.kicad_pcb"))

assembly.add_mate_definition(
    "sensor-cable",
    ConnectorMateDefinition(
        pin_pairs=[
            ConnectorPinPair(left="1", right="3"),
            ConnectorPinPair(left="2", right="2"),
            ConnectorPinPair(left="3", right="1"),
        ],
        evidence=AssemblyEvidence(
            source="Cable connection table", locator="sensor cable pin map", revision="B"
        ),
    ),
)
assembly.mate_connectors(
    "sensor-to-controller",
    ConnectorMate(
        definition="sensor-cable",
        left=assembly.connector("sensor", "J1"),
        right=assembly.connector("controller", "J4"),
        evidence=AssemblyEvidence(source="System assembly", locator="sensor cable installation"),
    ),
)
assembly.expected_connectors.append(assembly.connector("controller", "J5"))
assembly.save("sensor-system.assembly.json")
```

The example connector numbers and references must exist in the supplied designs.
The connector helper requires an exact, unambiguous reference.
Use `ConnectorEndpoint(instance=..., component_id=...)` when selecting by a known record identifier.
Source descriptions and locators are evidence text. The tools never open them as files or URLs.
They do not prove human authorship, actual installation, or source authority.

An exporter can generate definitions and mates from machine-readable assembly or cable data.
Board files alone do not establish how separate boards connect.
No interactive authoring step is required by this API.

## Edit and save

`add_board` copies input design data. Identical snapshots share one stored design definition.
`replace_board` changes only the selected occurrence and removes unused stored definitions.
Stale connector identifiers produce unresolved mate issues. The tools do not guess replacement references.

`add_mate_definition` adds a new mapping. `replace_mate_definition` explicitly changes a shared mapping.
`mate_connectors` adds an identified mate. Remove an existing mate before replacing that mate's endpoints.
`remove_mate` removes a link. The next inspection recalculates the system nets.
`remove_board` refuses a board still referenced by a mate or an expected connector.
Remove those references first.

`inspect` creates an owned, read-only snapshot. Later assembly edits do not alter that snapshot.
Create another inspector to see changes.
`to_json` and `save` validate the assembly before writing it.
`from_json` and `load` reject malformed JSON, unsupported schemas, dangling design references, and changed design content digests.
Semantically unresolved mates remain saveable so their declarations and diagnostics are not lost.

The file stores assembly inputs, not trusted saved resolution results.
Inspection recalculates system nets and mate diagnostics when it loads the assembly.
System-net identifiers derive from assembly identity and sorted qualified member-net identities.
Their identifiers stay stable when mate insertion order changes.
Snapshot identifiers also cover source data and mate definitions, so changed evidence invalidates old query cursors.

## Inspect the system

```python
from eedesigncore import open_inspection

inspection = open_inspection("sensor-system.assembly.json")
overview = inspection.execute("overview")
usage = inspection.execute("usage")
instances = inspection.execute("instances")
problems = inspection.execute("issues")
mate = inspection.execute("mate", {"id": "sensor-to-controller", "limit": 20})
connector = inspection.execute("connector", {"instance": "controller", "reference": "J4"})
```

The connector view includes each pin's mapping status, remote endpoint, and system-net identifiers.
The mate view returns a page of derived pin pairs and the mate and definition source evidence.
The system overview counts unresolved mates separately from resolved mates.
Board-local issues and extraction coverage remain available through `board_query` and `instances`.

```python
members = inspection.execute("system_net", {
    "instance": "sensor", "name": "SIGNAL", "view": "members", "limit": 20
})
endpoints = inspection.execute("system_net", {
    "instance": "sensor", "name": "SIGNAL", "view": "endpoints", "limit": 20
})
path = inspection.execute("trace", {
    "from_instance": "sensor", "from_net": "SIGNAL",
    "to_instance": "controller", "to_net": "INPUT",
    "relationship_kinds": [],
})
```

A system-net query accepts a system identifier or an instance with one local net name or identifier.
The trace accepts board-local net identifiers or exact names in its selected instances.
Trace output alternates qualified local nets with the actual mate pairs or selected board relationships.
An empty relationship-kind list permits only declared connector connections.
A work-limit error differs from a completed search that finds no path.

| Operation | Purpose |
| --- | --- |
| `mate_candidates`, `compare_connectors` | Investigate connector pairing and pin-map hypotheses |
| `placement_check` | Check one rigid XY fit across all resolved mates between two boards |
| `overview`, `usage` | Discover coverage, counts, limits, and supported requests |
| `instances` | Discover board instances, source revisions, and extraction coverage |
| `search` | Find records across boards by text and record kind |
| `mates`, `mate` | Inspect declared mates and their derived pin pairs |
| `connector` | Inspect local pins, remote endpoints, and mapping status |
| `system_nets`, `system_net` | Discover system nets and inspect members or endpoint pins |
| `issues` | Find assembly mapping problems and unmated expected connectors |
| `trace` | Follow a declared structural path across boards |
| `circuit` | Read components and their other pin assignments across the selected system net |
| `board_query` | Use existing inspection queries within one board's scope |

```python
part = inspection.execute("board_query", {
    "instance": "controller", "operation": "component", "inputs": {"reference": "U1"}
})
```

Board-local queries retain their original meaning. Neighbor distances never compare origins from different boards.
Embedded netboms do not supply native geometry or native source-file fields.
Open the original KiCad file separately when a native inspection query is required.

## Power-stage inspection

The `circuit` query starts from one local net and follows its resolved connector mates.
It returns paged component pin tables across the resulting system net.
Each row identifies its board instance. Each pin retains local net names and system-net identifiers.
Mapped pins include mate identifiers. The system-net summary states whether it depends on inferred mates.
Other pins on a component are context, not inferred internal connections.
Thus a header with separate amplifier-output and measurement-input contacts does not silently become a jumper.

```python
stage = inspection.execute("circuit", {
    "instance": "baseboard", "name": "+BATT", "scope": "board",
    "limit": 8, "pin_limit": 16,
})
locations = inspection.execute("board_query", {
    "instance": "baseboard", "operation": "pads",
    "inputs": {"reference": "J17", "scope": "board"},
})
```

Pad coordinates remain local to the named board instance, in millimeters.
No assembly placement changes them. Local pad coordinates refer to the footprint origin.
Use component, pin, and pad pages for any table marked truncated.
The shared command-line output budget also applies to assembly replies.
See [design inspection](design-inspection.md) for limits and batch operation.

## Portable inspection

The `portable_inspection_source` helper supports both netbom and assembly files.
It runs with Python standard libraries alone and uses the same system resolution code as installed inspection.
The installed command also detects assembly JSON automatically.

```text
python inspect_design.py sensor-system.assembly.json overview
python inspect_design.py sensor-system.assembly.json --batch < questions.jsonl
```

Paged responses use `items`, `total`, and `next_cursor`.
Continue with the same operation, filters, and page size plus the returned cursor.
A query or snapshot change invalidates the cursor.

## Bounds and excluded work

The reader bounds input bytes, JSON structure, embedded designs, instances, mates, and expanded connectivity.
Current caps include 256 distinct designs, 1024 instances, 10000 mates, and 250000 expanded connector pin pairs.
Expanded board records cannot exceed 1000000. Expanded record links cannot exceed 4000000.
Page size cannot exceed 200. Replies use the existing conservative size budget.
These limits fail explicitly. They never truncate electrical connections silently.

This feature creates no combined KiCad schematic, placement, wire routing, PCB merge, HTML adapter, or viewer behavior.
Separate board stackups and rules remain separate. System rule precedence is not evaluated.

## Generated schematic views

The assembly schematic interface retains optional native sources and generates a flat board-level root.
See [native and assembly schematic pages](assembly-schematic-view.md) for its contract and current limits.
