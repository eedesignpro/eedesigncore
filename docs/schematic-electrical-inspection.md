# Schematic electrical inspection and guarded edits

Import every operation and result type from `eedesigncore`.
`inspect_schematic_electrical(source, document_id=..., instance="design")` accepts the same native sources as `prepare_schematic_view`.
It reuses that preparation and connectivity calculation. By default it also prepares drawing geometry.
Pass `drawing=False` for electrical facts only. Nets, parts, pins, labels and resolved text stay the same; items carry no drawn bounds, and drawing coverage reads `unavailable`.
The PCB and schematic update planners pass `drawing=False`. On a 46-sheet design that took one plan from 10.5 s to 3.6 s, with the text-resolution changes of 0.40.0.
No second connectivity reader or saved PCB assignment source is involved.
Use the same document and instance identifiers when comparing revisions.

## Facts and coverage

The `SchematicElectricalSnapshot` has a JSON-compatible Pydantic representation.
Use `model_dump(mode="json")` or `model_dump_json()` and `model_validate_json()`.

- `items` retains the existing occurrence-qualified `ViewItem` identities, connections, references, source UUIDs, and pin functions.
- `nets` includes pin, label, and all source-item membership, names, class memberships, and class evidence.
- `labels` identifies source file, sheet occurrence, UUID, kind, resolved text, shape, fields, and net or bus membership.
  `source_text` and `source_field_values` retain raw values for guarded edits.
  `field_values` preserves repeated resolved field names. `class_fields` preserves every Netclass field value.
- `parts` includes selected library identity, resolved fields, reference, unit, body style, population flags, and saved instance/variant records.
- `sheet_files` maps sheet occurrences to physical files. Repeated occurrences can share one file.
- `no_connect_pins` identifies contacts with an explicit mark. `unbound_no_connect_ids` reports unmatched marks.
- `source_hashes` captures exact files read from a path input. In-memory input returns no claimed source hashes.
- `coverage` preserves native electrical and identity limitations. `class_coverage` and `class_issues` describe additional class limits.

Class names are membership evidence, not effective clearance or width settings.
Class evidence names the attached label, bus label, matching project pattern, or default fallback.
The operation ignores stale saved `netclass_assignments` and combines current schematic attachments with matching project patterns.
Undefined class names remain in the result. The operation does not invent class definitions or apply interface policy.
Use the append-only helper below for explicitly chosen global-label memberships.
Rule-area class attachment is not yet implemented and makes class coverage partial.
Missing project patterns, unresolved class text, and unresolved target membership also prevent complete class coverage.

## Comparison

`compare_schematic_electrical(before, after)` compares pin groups instead of prepared net IDs.
An extra wire segment can change net IDs without changing electrical facts.
Changes have a category, an identity, and deterministic `before_json` and `after_json` values.
Categories separate connectivity, net names, parts, pins, no-connect marks, classes, labels, and drawing differences.
Net splits and merges change pin groups. Name changes remain visible separately.
Drawing signatures include stored drawing order and geometry. They are not a proof of visual equivalence.
The operation does not classify circuit intent or electrical operating behavior.

`equivalent` is false when a reported non-drawing fact differs.
It is true only when no such fact differs and electrical and class coverage are complete.
It is null when incomplete coverage prevents that conclusion.
Missing source identities can prevent stable comparisons. Coverage exposes this condition.
Pin identities retain the existing native definition identity rules.
A pin-definition change can appear as removed and added contacts.

## Append-only global-label classes

`append_global_label_netclasses(label, class_names, inspection=..., label_id=...)` adds only missing literal names.
The helper appends separate canonical Netclass fields, as KiCad's label dialog does.
Existing field values, visibility, positions, order, label graphics, and project patterns remain unchanged.
New fields are hidden and located at the label anchor. The helper returns the added names.
It requires complete electrical and class coverage, one scalar net, matching identity/text/fields, and resolved literal names.
Bus labels and unresolved text are rejected. No class assignment is inferred.
The helper validates the selected label against the supplied inspection. It cannot detect unrelated edits made after that inspection.
Use a current inspection.
A sheet file that repeats in the hierarchy carries one set of label fields for every occurrence. Check that the change suits every occurrence.
Inspect the edited hierarchy again before the guarded save.
When names and classes both change, append classes against the original inspection first, or reinspect after the name change.
Reloading and repeating the same request adds no fields.

## Guarded edits

```python
from eedesigncore import load_guarded_document, save_guarded_documents

edit = load_guarded_document("design.kicad_sch")
# Select explicit labels through UUIDs before changing their owned fields.
result = save_guarded_documents([edit])
assert result.files[0].status == "unchanged"
```

`load_guarded_document` returns an editable native document and an immutable `SourceSnapshot` containing exact original bytes.
`GuardedDocument(source, document)` also accepts a document parsed from those exact captured bytes.
Supported editable file types are `.kicad_sch` and `.kicad_pro`.
`SourceSnapshot.capture(path)` can capture other unchanged dependencies, including contract files.
Pass these through `save_guarded_documents(documents, dependencies=...)`.
Capture and parse the same bytes. Do not attach a newly captured baseline to an older edited document.
Include every source that informed the edit, not only the files that will change.
Do not list the same source as both an edited document and a dependency.

The operation acquires cooperative locks, checks all sources, serializes and reparses all outputs, and stages all changed files.
It checks expected bytes before each replacement and after the last replacement.
An unchanged model preserves original bytes and timestamps, including noncanonical formatting.
Changed output retains the original LF or CRLF convention and uses the existing native writers.
A successful replacement leaves the original snapshot unchanged. Reload before another edit cycle.

`GuardedSaveError.result.status` distinguishes rejection before any replacement from partial completion.
Per-file results identify saved, unchanged, and pending files with before/after SHA-256 hashes.
On partial failure, retain the original snapshots and inspect current files before recovery.
The operation does not roll back successful replacements over possible intervening human edits.
Multiple file replacements are not one atomic project transaction.
An editor that ignores locks can race a check. Unsaved editor changes cannot be detected.
Symlink paths are rejected. This is cooperative edit protection, not an adversarial filesystem boundary.

## Validation scope

A KiCad netlist export, saved in tests/fixtures/schematic_classes/native_membership.json, checks the class memberships.
