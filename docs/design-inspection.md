# Design inspection

The inspection tools answer focused questions from a netbom or native KiCad files.
They leave source files, loaded objects, and viewer state unchanged.
They do not fetch a URL or contact GitLab.

## Access levels

| Available information | Inspection route | Added evidence |
| --- | --- | --- |
| Netbom only, no package access | Standard JSON tools or the optional portable Python helper | Electrical assignments, metadata, pin functions, coverage, and source evidence |
| Netbom and standalone HTML, no package access | The same portable helper for the netbom | HTML inspection belongs to the documentation tools |
| The same files with package access | Installed inspection commands or Python calls | The same answers with the installed interface |
| Native design repository and package access | Open a board, schematic, or project file | Native geometry and exact stored source fields |
| Full source repository access | The same tools plus the agent's authorized repository reader | Source files, history, issues, and review context at an explicit revision |

More access does not change the meaning of existing facts.
A drawing snapshot does not prove copper continuity. Repository access does not prove design intent.
The tools identify their evidence and refuse operations whose required evidence is absent.

## Guidance for standalone documentation

The exporter includes inspection-discovery text in the netbom `conventions` object.
It includes the installed command, the direct JSON route, the optional helper route, and the package name.
This text survives JSON and complete JSONL exports. Older exports may omit these additional convention keys.

A documentation tool should expose this guidance beside its NetBOM dataset entry.
It should also document its own extraction route. This Python package does not read or define the HTML format.
The guidance requires no embedded Python source, local machine path, or automated installation.

With the package installed, save the extracted JSON and request help:

```text
python -m eedesigncore.inspection design.netbom.json usage
python -m eedesigncore.inspection --help
```

Replace the example filename with the extracted filename.
The first command describes queries available for that file. The second needs no design file.
Query help includes accepted inputs, selection rules, examples, and limits.
Read usage from the installed version rather than assuming that every new query exists in an older release.

Without the package or helper, read coverage, conventions, summary, indexes, and linked records with a JSON reader.
If the distribution includes the optional helper, request its usage with the extracted file.
Neither offline route needs package or network access.

Install the package from the package index that distributes it. The netbom does not name an index.
Offline readers should use the direct JSON or helper route.
Inspection never installs software or contacts a package index.

## Start with discovery

```python
from eedesigncore import open_inspection

inspection = open_inspection("controller.netbom.json")
usage = inspection.execute("usage")
overview = inspection.execute("overview")
```

`DesignInspector`, `inspect_design`, `open_inspection`, and `portable_inspection_source` are public package exports.
`open_inspection` accepts netbom JSON, complete netbom JSONL, assembly JSON, or a native KiCad file.
Assembly files use system queries. See [netbom assemblies](netbom-assemblies.md).
Native inputs can be `.kicad_pcb`, `.kicad_sch`, or `.kicad_pro`.
Supply a file path. The tool does not guess which project in a directory you intend to inspect.

An inspection session loads its input once and builds its lookup indexes once.
All later queries use that saved snapshot. Create another session after source files change.
Responses contain the operation, inspection schema version, snapshot identifier, and result data.
The snapshot identifier covers the netbom. It differs from the netbom revision identifier.

## High-value queries

| Operation | Question it answers |
| --- | --- |
| `overview` | What information exists, and what remains incomplete? |
| `search` | Which components, nets, notes, or fields match this text or exact value? |
| `component` | What is this component, and which nets connect to its pins? |
| `pin` | What function and net assignment does this numbered pin have? |
| `net` | Which component pins connect to this saved net? |
| `circuit` | Which components touch this net, and where do their other pins connect? |
| `pads` | Where are the physical pads, and what functions and nets do they carry? |
| `zone_areas` | What are the estimated zone-outline and saved-fill areas for each net and copper layer? |
| `common_nets` | Which saved nets do these components share? |
| `neighbors` | Which component origins lie within this radius? |
| `trace` | Is there a structural path through the explicitly selected passive relationships? |
| `issues` | Which associations, fields, or assignments need further evidence? |
| `records` | What does this exact exported record contain? |
| `sources` | Which source locations support these records? |
| `source_manifest` | Which extraction versions and source coverage apply? |
| `source_files` | Which files were read, and what are their exact byte hashes? |
| `geometry` | Which native tracks, arcs, vias, zones, or footprints match a net, layer, or region? |
| `native_sources` | Which loaded native files can I inspect directly? |
| `native_record` | What are the exact stored fields at this source location? |

```python
part = inspection.execute("component", {"reference": "U7"})
pin = inspection.execute("pin", {"reference": "U7", "number": "12"})
net = inspection.execute("net", {"name": "/Power/VOUT", "limit": 20})
shared = inspection.execute("common_nets", {"references": ["U7", "J3"]})
nearby = inspection.execute("neighbors", {"reference": "U7", "radius_mm": 8, "side": "top"})
missing = inspection.execute("search", {"kind": "component", "field": "Datasheet", "missing": True})
```

References and net names require exact, case-sensitive matches.
Duplicate references cause an ambiguity error with candidate identifiers. Supply the intended identifier instead.
Search text uses case-insensitive literal matching. It does not evaluate regular expressions or source text.
Exact field filters preserve the distinction between booleans and numbers.
Missing properties differ from empty properties. An empty field can be selected with an empty-string equality filter.

A component or net response includes a page of pin details.
Pin details retain effective schematic functions, selected alternates, saved nets, no-connect evidence, and issue identifiers.
The tool constructs only the requested page of pin details, including on large shared nets.
A net assignment does not establish copper continuity or board/schematic agreement.

Neighbors measure Euclidean distance between stored footprint origins.
They do not measure package spacing, pad clearance, or nearest copper distance.
Native geometry region filters use axis-aligned bounds in design-file millimeters.
Their order is minimum X, minimum Y, maximum X, maximum Y.
Zone geometry returns stored outlines. It does not certify fill freshness.

## Trace a power stage without repeated pin lookups

Use `circuit` to read each connected component with its pin-to-net table in one query.
Each row retains record identifiers for follow-up questions and evidence lookup.
Pins on the selected net appear first. Other pins show the next nets to inspect.
The query does not infer conduction through a transistor, transformer, connector, or integrated circuit.
Use the stored functions and topology to assess each stage. A missing external jumper remains a gap.

```python
stage = inspection.execute("circuit", {
    "name": "+BATT", "scope": "board", "limit": 8, "pin_limit": 16,
})
pads = inspection.execute("pads", {
    "reference": "Q5", "scope": "board", "number": "5", "limit": 20,
})
```

Component, pin, pad, and circuit queries accept `scope`.
The default `all` retains ambiguity checks. `board` selects components with a board association.
`schematic_only` selects components without a board association.
This separates unmatched schematic references from placed components without merging them by name.
Repeated references within the selected scope still require a record identifier.

Circuit results page components, with a default page size of eight.
Each component includes up to sixteen pins by default. Set `pin_limit` from one through sixty-four.
A truncated pin table sets `pins_truncated`. Use the component identifier with `component` to read all pin pages.
Circuit component fields include selected electrical descriptions and values. Use `component` for all saved fields.
Relationship identifiers describe exported relationships. They do not establish operating conditions or direction of current.

Pin details include up to eight physical pads and an explicit truncation flag.
Use `pads` for complete paged results, optionally filtered by pin number.
Multiple physical pads with the same number remain separate records.
Locations include board XY and footprint-local XY coordinates in millimeters, layers, and saved board pin functions.
The board frame uses the saved board origin. It does not use the auxiliary origin or an assembly transform.
The local frame uses the footprint origin. Missing board positions remain unavailable.
Circuit rows declare these frames once for their pad previews.
Saved board pin functions remain distinct from effective schematic functions and alternate selections.

## Pages and repeated questions

List results include `items`, `total`, and `next_cursor`.
To continue, send the same operation and inputs with the returned cursor.
Changing the snapshot, filter, operation, or page size invalidates the cursor.
Cursors are query continuation metadata. They are not authentication tokens.

```python
query = {"name": "GND", "limit": 20}
first = inspection.execute("net", query)
cursor = first["data"]["next_cursor"]
if cursor is not None:
    second = inspection.execute("net", {**query, "cursor": cursor})
```

The command-line interface supports one question or a batch of questions.
Batch mode reads one JSON request per line and loads the design once.
Each batch reply states success or returns a query error. A query error does not execute another operation.

```text
python -m eedesigncore.inspection controller.netbom.json usage
python -m eedesigncore.inspection controller.kicad_pro --batch < questions.jsonl
```

Use `--inputs` for a single operation's JSON input object.
Command-line replies have a default byte budget of 65536 bytes each.
An oversized reply returns an error before printing result data. Reduce the page or pin limit and retry.
Use `--max-output-bytes` to select a budget from 1024 through 2097152 bytes.
The Python interface retains its existing response limit.
Use `--pretty` for indented single-query JSON. Batch mode retains one reply per line.
Do not preview compact source JSON with a first-lines command. Its first line can contain the entire design.
Use `overview`, `usage`, and paged queries. Keep one batch session open for repeated questions.
The `usage` response describes accepted inputs, required values, and alternative selectors.
Unknown commands and unknown input names cause errors.

## Portable distribution without package access

A bare netbom remains standard JSON. An agent can read its records and indexes with ordinary Python or JSON tools.
It does not require these inspection tools to remain useful.

A documentation generator can include the optional helper beside its data files.
The helper is the same source used by the installed inspection interface.
It uses only Python's standard library. It does not install packages or contact a registry.

```python
from pathlib import Path
from eedesigncore import portable_inspection_source

Path("inspect_design.py").write_text(portable_inspection_source(), encoding="utf-8")
```

A recipient can then use it without package access.

```text
python inspect_design.py controller.netbom.json overview
python inspect_design.py controller.netbom.json --batch < questions.jsonl
```

The optional sidecar provides the higher-level queries without requiring each agent to write them again.
Native KiCad reading requires the installed Python package.

## Native source inspection

Use loaded objects when another export stage already read the design.

```python
from eedesigncore import inspect_design

inspection = inspect_design(board, schematic=hierarchy, project=project)
sources = inspection.execute("native_sources")
pad = inspection.execute("native_record", {
    "source": "board",
    "path": "footprints/0/pads/0",
    "fields": ["number", "net_name", "position", "layers"],
})
geometry = inspection.execute("geometry", {
    "net": "GND", "layer": "F.Cu", "bbox": [20, 30, 40, 50], "limit": 10,
})
```

Native source identifiers include `board`, `project`, and one `sheet:` occurrence path per loaded sheet.
A directly supplied schematic uses `schematic`.
Without a fields selection, a native record query reports available fields, list length, or a scalar value.
Object paths address declared fields, dictionary keys, and list positions. They do not address methods or filesystem paths.
Repeated sheet occurrences retain shared schematic content inside the inspection snapshot.

Opening native files reuses the netbom loader's confinement, file limits, and hierarchy limits.
Board and schematic content remains consistent within the loaded in-memory snapshot.
The files do not carry a shared synchronization marker, so the tool cannot certify their saved agreement.
Schematic-only native inputs expose their stored geometry and fields through source inspection.
They do not gain a resolved wire-derived net graph in this version.

## Repository context

The `sources` operation returns source records and the evidence locations used by selected records.
An agent with repository access can use those locations to retrieve the corresponding saved files and history.
An optional HTTPS repository URL, explicit revision, and source-path mapping produce file links.
The link uses the GitLab file form, `<repository>/-/blob/<revision>/<path>`.
The caller supplies that mapping. The inspection tool does not fetch or verify the link.

```python
evidence = inspection.execute("sources", {
    "ids": [record_id],
    "repository_url": "https://gitlab.example/team/design",
    "revision": commit_id,
    "paths": {source_id: "hardware/controller.kicad_pcb"},
})
```

Credentials remain with the agent's authorized repository tools.
Do not place credentials, tokens, or private service requirements in portable design data.
Issue discussions and historical design intent remain external context with their own provenance.
They do not silently replace saved electrical facts.

## Bounds and errors

| Limit | Value |
| --- | --- |
| Input file read | 256 MiB |
| Netbom records | 250000 |
| JSON values | 4000000 |
| JSON nesting | 64 |
| Default page / largest page | 25 / 200 |
| Conservative reply budget | 2 MiB |
| Exact record request | 32 identifiers |
| Native selected fields / path depth | 32 / 32 |
| Native selected values | 10000 |
| Pin-detail associations | 512 |
| Batch request line / request count | 64 KiB / 10000 |

The conservative size budget includes repeated values, integer magnitude, and escaped text.
It can refuse a response before its actual JSON reaches the stated byte limit.
Narrow the selection or follow a child source path after a limit error.
Oversized output never silently truncates a result.
Duplicate JSON keys, dangling links, invalid numbers, ambiguous selections, and unsupported schemas cause explicit errors.
Source strings remain data. The reader never treats them as agent instructions.


## Zone area estimates

Use `zone_areas` with an optional net name or identifier and copper layer.
Without a net selector, the query pages all net and layer groups.
The same query works with an exported netbom or a native board.
For assemblies, use `board_query` to retain the board and layer scope.

```python
areas = inspection.execute("zone_areas", {"name": "GND", "layer": "F.Cu", "limit": 20})
```

Each row reports square millimeters, zone count, and source zone identifiers.
Outline area subtracts the stored cutouts from the outer boundary.
Saved-fill area sums the stored filled polygon areas, including stored islands.
These are polygon estimates. The query does not refill zones or merge overlapping copper.
Overlapping zones can count the same area more than once. Layers remain separate.
Clearances already present in stored fills affect their area. No new clearance or boundary-thickness calculation runs.

Rule areas, including placement rule areas, do not contribute. Non-copper layers do not contribute.
Both board zones and footprint zones contribute. Area does not depend on footprint translation or rotation.
Wildcard zone layers use the board's enabled layer list when available.
An unavailable layer assignment remains null. An unassigned net also remains null.

A missing fill is unknown, not zero. The outline does not substitute for a missing fill.
If a group lacks any complete fill estimate, its total is null.
The known subtotal and missing-estimate counts remain available.
Curved or degenerate polygons have no estimate in this first implementation.
Saved-fill freshness is always unknown. These results do not prove copper continuity or manufacturability.

The netbom stores only area summaries, not the polygon point lists used for this calculation.
Older netboms remain readable but require re-export to obtain these estimates.


## Source evidence queries

Use the existing `DesignInspector` for embedded NetBOM data.
No HTML reader or separate integration is required.

```python
from eedesigncore import DesignInspector

inspection = DesignInspector(embedded_netbom)
help_result = inspection.execute("usage", {"operation": "source_files"})
summary = inspection.execute("source_manifest")
page = inspection.execute("source_files", {"role": "schematic_dependency", "limit": 25})
while True:
    for source in page["data"]["items"]:
        consume_source_evidence(source)
    cursor = page["data"]["next_cursor"]
    if cursor is None:
        break
    page = inspection.execute(
        "source_files",
        {"role": "schematic_dependency", "limit": 25, "cursor": cursor},
    )
```

`embedded_netbom` is the decoded NetBOM JSON object.
`consume_source_evidence` represents the consumer's own display or review step.
The same queries work with installed inspection commands and the optional standard-library helper.
Focused `usage` accepts any supported operation name.

`source_manifest` returns versions, hash meaning, counts, and coverage limits.
It omits the file list.
`source_files` accepts exact `path`, `role`, and `status` filters.
Filters combine. The `status` filter accepts `read` or `absent`.
The default page contains at most 25 rows. The maximum is 200.
Keep filters and the limit unchanged when supplying a continuation cursor.
Cursors belong to one inspection snapshot and query.
The existing response size limit remains 2 MiB.

| Result status | Meaning |
| --- | --- |
| `recorded` | The extraction recorded file evidence. Read each row's own status. |
| `not_inspected` | The input was in memory, or the requested path has no evidence entry. |
| `not_recorded` | An older NetBOM has no source-evidence manifest. Absence is unknown. |

A filtered result can contain no rows even when its status is `recorded`.
This means no recorded entry matched all filters. It does not establish file absence.
Only a file row with `status="absent"` establishes an explicit missing-file observation.

These additions retain the `design-inspection-1` response schema.
File evidence remains available after JSON or complete JSONL export.
A documentation tool continues to own Git evidence, remote links, browser state, and HTML packaging.
See [file evidence](netbom.md#file-evidence) for the public types and extraction limits.
