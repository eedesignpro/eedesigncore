# Netbom design content

A netbom describes a saved KiCad design without external libraries or manual annotations.
It combines board net assignments, component fields, placement, schematic pin functions, and project settings.
The export leaves all input files and supplied objects unchanged.
It needs no running KiCad process, database connection, external service, or human input.

The board supplies the saved pad-to-net assignments. These assignments do not prove copper continuity.
The schematic supplies embedded symbol definitions, selected pin functions, fields, notes, and sheet occurrences.
A missing schematic does not remove board facts.

## Export

Use loaded objects when an export pipeline already has the design.
Only supplied objects contribute data. This call reads no files.

```python
from eedesigncore import build_netbom

netbom = build_netbom(board, schematic=hierarchy, project=project, design_name="controller")
text = netbom.to_json()
```

A schematic argument can be one loaded `Schematic` or a loaded `SheetHierarchy`.
A single schematic describes one occurrence. Supply a hierarchy for repeated sheets and child sheets.
Board-only, schematic-only, and project-only inputs are valid.
Schematic-only exports retain metadata but do not derive nets from wires.

Use the file helper when the pipeline starts with a path.

```python
from pathlib import Path
from eedesigncore import load_netbom

netbom = load_netbom("controller.kicad_pcb")
Path("controller.netbom.json").write_text(netbom.to_json(), encoding="utf-8")
```

The helper reads local board, schematic, and project files with the same stem.
It follows project top-level sheets and child sheet references within the project directory.
It does not search installed libraries, resolve database rows, or read external datasheet URLs.
Missing child sheets produce issues. Missing top-level sheets leave an explicit incomplete schematic result.
Malformed files and unsafe paths raise errors. The export does not silently accept malformed source content.

Command-line pipelines can use standard output.

```text
python -m eedesigncore.netbom controller.kicad_pcb > controller.netbom.json
python -m eedesigncore.netbom controller.kicad_pcb --format jsonl > controller.netbom.jsonl
```

## Discover content progressively

Read `coverage`, `summary`, and `conventions` first. Then use `indexes` to select records.
Complete coverage means complete collection within the stated scope. It does not mean the design is electrically correct.
Each index value contains a list. Duplicate references and ambiguous names remain visible.
The export includes indexes for record kind, reference, net name, effective pin name, and sheet name.
Index lookups use exact, case-sensitive text.

```python
components = [r for r in netbom.find("reference", "U7") if r.kind == "component"]
component_records = netbom.component_view(components[0].id)
net = netbom.find("net_name", "/Power/VOUT")[0]
net_records = netbom.net_view(net.id)
nearby_records = netbom.neighborhood(net.id, depth=1, max_records=2000)
```

A component view includes its symbols, pins, pads, definitions, connected nets, source evidence, and linked issues.
It does not expand every other component on those nets.
A net view includes assigned pins, pads, their components, symbol-pin details, zones, and linked issues.
Both methods have explicit record limits. An exceeded limit raises an error instead of truncating the result.

```python
from eedesigncore import NetbomDocument

loaded = NetbomDocument.from_json(text)
for line in loaded.iter_jsonl(kind="component"):
    print(line)
```

Newline-delimited JSON starts with a manifest, followed by one complete record per line.
A kind filter omits other records. It is a discovery extract, not a standalone replacement for the full document.
A consumer can use ordinary JSON tools or Python without this Python package after export.

## Core structure

| Record kind | Meaning |
| --- | --- |
| `source` | Input format, relative locator, and parsed-content signature |
| `source_manifest` | Versioned evidence for exact file bytes read during extraction |
| `board` | Layer list, stackup, outline, title block, general settings, and variants |
| `component` | One board footprint or an unmatched schematic occurrence |
| `footprint_definition` | Shared pad geometry without placement-specific net assignments |
| `pad` | One physical pad, including repeated numbers and calculated board coordinates |
| `pin` | One component pin number, with its pad, symbol-pin, and net links |
| `net` | One saved board net name and its endpoint links |
| `sheet` | One sheet occurrence with its UUID path and source file |
| `symbol_definition` | Shared embedded pin definitions and symbol fields |
| `symbol_instance` | One placed symbol occurrence with unit, body style, fields, and alternate selections |
| `symbol_pin` | One selected library pin in that occurrence |
| `no_connect` | One saved schematic no-connect marker |
| `note`, `text_box`, `label`, `board_note` | Source text with a sheet or board association |
| `zone` | Saved layer, priority, assignment, and fill-count summary |
| `project_settings` | One stored project section, including unknown keys and values |
| `bom_group` | Board components with matching fields, footprint, and assembly flags |
| `relationship` | An explicitly labeled inference between nets through a component |
| `issue` | A missing association, ambiguity, conflict, or extraction limitation |

Pins belong to components. Symbol-pin records retain the separate schematic occurrences and their selected functions.
Repeated pad numbers retain all physical pads. Unnumbered pads remain separate.
Common-unit pins and common-body pins remain available alongside the selected unit and body style.
Repeated sheets share definitions but retain separate occurrence paths, references, and unit selections.

The bill of materials groups physical board components only. Its quantity includes the stored assembly flags.
A do-not-populate component remains visible. Consumers must select their assembly variant before treating quantities as purchasing requirements.
Schematic fields contribute to grouping when an association exists. Grouping does not establish purchasing equivalence.

## Association rules

An exact match accepts the full sheet occurrence path and symbol UUID, or that path without the root-sheet prefix.
The child-sheet occurrence path must remain intact. A matching symbol UUID alone cannot join different child occurrences.
Both forms retain `exact_path` evidence. The exporter leaves the source UUIDs and saved paths unchanged.
All matching footprint candidates are considered together.
If a saved board path identifies multiple schematic occurrences, the exporter keeps the association conflicting.
A conflicting path cannot anchor additional symbol units through a reference.
The exporter validates UUID path syntax and compares canonical UUID spelling.
A reference disagreement does not erase an exact path match. It produces an issue.

Additional units can join an exactly matched unit through a shared reference, compatible library identifier, and distinct unit selections.
When a footprint has no saved path, a unique reference and compatible unit set can supply an inferred association.
A stale, invalid, or conflicting saved path does not silently become a reference match.
Ambiguous associations retain candidate links and separate unmatched schematic records.

Within an associated component, literal pin numbers connect symbol pins to physical pads.
Alternate selections preserve the base name, selected name, effective name, electrical type, and shape.
An invalid selection remains unresolved and produces an issue.
Stacked pin-number notation remains explicit and produces an unsupported-expansion issue in this version.
The exporter does not guess an expansion or invent a pad association.

A no-connect marker matches a pin at its calculated sheet position.
The exporter retains the marker separately from the board assignment.
A marked pin with a board net assignment produces a conflict issue.
An unassigned board pad does not imply an intentional no-connect declaration.

## Evidence and missing information

Each evidence entry identifies its source and location.
`derivation` distinguishes copied facts, calculated values, and inferred relationships.
Calculated and inferred entries name their rule and relevant input records when available.

`representation` distinguishes explicitly present source content from parsed values that can include reader defaults.
`authorship` remains unknown. A saved value or label spelling cannot prove who entered it.
The export does not classify a name as automatic merely because it resembles a KiCad-generated name.

Component and symbol metadata separate populated fields, empty fields, duplicate fields, and absent standard fields.
Unknown populated fields remain intact. Project settings preserve nulls and unknown content.
Unresolved associations and conflicting assignments have distinct statuses and issue records.
The export does not replace unknown values with guessed defaults.

## Electrical and layout limits

Saved board assignments define the electrical endpoint graph.
Schematic wires, junctions, label scope, and bus geometry do not participate in this graph.
The export cannot prove that the board and schematic are synchronized.

Available saved net-class assignments and pattern assignments use the existing net-class reader.
Class names have no implied priority. Missing or malformed assignment data produces an issue.
The export retains project settings but does not evaluate final rule constraints or custom design-rule files.

Placement includes side, layer, rotation, and calculated pad coordinates.
Layout summaries include the board outline, stackup, straight segment length, arc count, via count, and zone summary.
Straight segment length excludes arcs. It does not represent a pin-to-pin route length.
Copper continuity, route completeness, plane connectivity, return paths, and zone-fill freshness remain unevaluated.
Full track geometry, drawing geometry, images, and schematic wire geometry are outside this compact content format.

Generic `Device:R`, `Device:C`, and `Device:L` symbols can produce inferred resistive, capacitive, and inductive relationships.
Each relationship requires two unambiguously assigned endpoints on different nets.
A caller must explicitly choose which relationship kinds a trace can cross.

```python
path = netbom.trace(
    input_net_id,
    output_net_id,
    relationship_kinds={"resistive_connection", "inductive_connection"},
)
```

A returned path alternates net and relationship identifiers. An empty list means no path under the selected relationship rules.
The exporter does not short every pin of an integrated circuit or infer current flow through a component.
Device internals, operating states, undocumented purpose, power domains, and component ratings remain unknown unless source fields describe them.

## Identity and serialization

The schema version is `netbom-1`.
A caller-supplied design name defines the identity scope. Use a consistent, unique design name across revisions.
Footprint and symbol UUIDs identify occurrences. A repeated sheet also contributes its full occurrence path.
Missing or duplicate UUIDs use occurrence indexes and produce issues.
Such fallback identities can change when the source list order changes.
Pin identities include the component and pin number. Net identities include the saved name.
Renaming a net creates a different net identity.

Definition identities use content hashes. Instance records link to definitions without duplicating their geometry.
Every record has a fingerprint. The document revision includes record fingerprints, coverage, and conventions.
Fingerprints describe exported records, not recursive electrical equivalence or verified design intent.
The same supplied objects and design name produce the same serialized output.
The exporter does not include wall-clock timestamps or absolute workspace paths in normal source locators.
Source errors can retain file paths to identify the failed read.

`to_json` emits ASCII-compatible JSON and escapes angle brackets and ampersands.
Source strings remain untrusted data. A presentation layer must not execute them as instructions, scripts, or markup.
This feature does not generate HTML or provide an HTML renderer.
`from_json` rejects duplicate keys, non-finite numbers, unsupported schema versions, and dangling links.

## Bounds

| Area | Limit |
| --- | --- |
| Default exported records | 250000 |
| Maximum caller-selected exported records | 1000000 |
| Sheet occurrences during file loading | 4096 |
| Distinct schematic files during loading | 512 |
| Sheet hierarchy depth | 32 |
| One companion or schematic file | 64 MiB |
| Total distinct schematic input | 256 MiB |
| Imported JSON text | 256 MiB of characters |
| Imported JSON values | 2000000 |
| Imported JSON depth | 64 |
| Discovery depth | 16 |
| Query link scans | Selected record or visited-net limit multiplied by 32 |

Limits produce explicit errors. They do not produce silently incomplete exports.
Loaded-object exports also apply the existing parsed-content signature bounds to each source.


## Inspection-tool discovery

Exported conventions include instructions for installed inspection tools and direct JSON access.
They also identify the optional standalone helper and the package name. The netbom does not name a package index.
A documentation tool can expose this guidance beside its embedded NetBOM dataset entry.
The documentation tool supplies the HTML extraction instructions. This exporter makes no assumptions about HTML structure.
See [design inspection](design-inspection.md) for the starting commands, queries, and output limits.


## Zone area summaries

Zone records include estimated outline area and per-layer saved-fill area in square millimeters.
The summaries cover board and footprint zones without adding polygon geometry to the netbom.
Outline cutouts subtract from the outer boundary. Saved filled islands contribute to the saved-fill estimate.
Rule areas and non-copper layers do not contribute to the inspection totals.
Missing or unsupported geometry stays distinct from zero. Overlaps are not merged, and fill freshness remains unknown.
Use the `zone_areas` inspection query for paged totals by net and copper layer.


## File evidence

`load_netbom` records the bytes read by the existing file readers.
`open_inspection` uses the same extraction path for native files.
Neither operation rereads a file to calculate its hash.

`document.source_manifest()` returns a public `NetbomSourceManifest`.
Its `files` entries use the public `NetbomSourceFile` type.
Older documents without the record return `None`.
The manifest uses `source-evidence-1` within a normal `source_manifest` record.
The enclosing `netbom-1` schema and existing engineering record identities remain unchanged.
Consumers must ignore record kinds they do not use.

| Field | Meaning |
| --- | --- |
| `schema_version` | Source-evidence format version |
| `extraction_version` | Extraction contract version, currently `netbom-extraction-1` |
| `tool`, `tool_version` | Extractor name and the version that produced the evidence |
| `mode` | `files` for path extraction, or `in_memory` for loaded objects |
| `hash_algorithm`, `hash_basis` | SHA-256 over exact bytes supplied to the reader |
| `files[].path` | Identity relative to the input project directory, with forward slashes |
| `files[].role` | `board`, `project`, `root_schematic`, or `schematic_dependency` |
| `files[].status` | `read` or explicitly checked `absent` |
| `files[].sha256`, `size_bytes` | Hash and byte count for a read file, otherwise null |
| `files[].read_count` | Reader calls for that file during this extraction |
| `files[].source_ids` | Links to existing parsed-source records, when available |
| `schematic_dependencies` | `complete`, `partial`, or `not_inspected` |
| `not_inspected` | Other source roles that this extraction does not inspect |
| `limitations` | Limits on the claims supported by this manifest |

The identity root is the parent directory of the supplied native file.
Existing path confinement applies to all schematic dependencies.
Repeated sheet occurrences retain their separate electrical identities and share one file entry.
A file can have several read calls but only one hash.
Different bytes on a repeated read cause an error.
This check does not establish an atomic snapshot across different files.

An absent entry means an expected regular file was missing when checked.
It has no hash or byte count.
An unlisted path means the manifest provides no inspection evidence for that path.
A missing dependency makes schematic dependency coverage partial.
KiCad's supported root-file fallback can read the same-stem schematic instead of a missing named root.
The manifest records that named root as absent and marks dependency coverage partial.
A file read before an incomplete hierarchy fails can have no parsed-source record link.

`build_netbom` and `inspect_design` with loaded objects report `in_memory`.
They do not infer file reads from object history or source names.
They do not claim that any file is absent.
Custom rules, external symbol libraries, footprint libraries, drawing sheets, and 3D files remain `not_inspected`.
Embedded definitions do not prove that an external library file was read.

The existing `source.content_signature` compares parsed content.
It is not the byte hash in this manifest.
Whitespace or line-ending changes can alter byte hashes without changing electrical records.
A changed manifest changes the document revision.
Hashes establish neither authorship nor Git state, remote origin, freshness, or electrical correctness.

The manifest contains at most 4099 entries.
This includes absent paths within the existing sheet occurrence limit and same-stem companions.
Unsafe identities, duplicate file paths, invalid hashes, conflicting reads, and unsupported manifest versions raise `ValueError`.
Malformed KiCad input retains its existing `KicadParseError` behavior.
Source files remain unchanged.

For bounded file queries and focused help, see [design inspection](design-inspection.md#source-evidence-queries).
