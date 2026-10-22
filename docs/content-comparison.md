# Footprint, symbol, schematic, board, and project content comparison

Use `compare_content` and `content_signature` with a `Footprint`, `Symbol`, `SymbolLib`, `Schematic`, `Board`, or `ProjectFile`.
These operations compare the content represented by the supplied Python objects.
They do not change the objects, write files, or resolve referenced files.

```python
from eedesigncore import Footprint, compare_content, content_signature

before = Footprint.from_file("before.kicad_mod")
after = Footprint.from_file("after.kicad_mod")
result = compare_content(before, after)

print(result.equal)
print(content_signature(after))
for change in result.changes:
    print(change.kind, change.path, change.before, change.after)
```

For symbol files, load each file with `SymbolLib.from_file`.
Compare the whole `SymbolLib`, or select a `Symbol` from each file.
The comparison retains symbol names and inheritance references. It does not resolve inherited content.

For schematic files, load each file with `Schematic.from_file` and pass the results to the same operations.
The comparison covers one file. It does not open child sheets, load project settings, or recalculate connectivity.
Stored child-sheet paths, embedded symbol definitions, fields, and per-sheet references participate.

For board files, load each file with `Board.from_file`.
The comparison includes tracks, vias, footprints, zones, stored fills, stackup, and saved board settings.
It does not open project settings or custom design rule files, refill zones, or recalculate connectivity.

For project files, load each `.kicad_pro` file with `ProjectFile.from_file`.
Project comparison ignores object-key order and retains every array in its stored order.
All stored settings participate, including unknown keys, metadata, and references to other files.
Equivalent numeric values ignore their saved spelling. Booleans remain distinct from numbers.
Comparison cannot recover precision lost when the reader converts a decimal value to a floating-point number.
Absent keys remain distinct from explicit null values. No KiCad defaults are inserted.
Difference paths start with `root`, followed by setting names and array indexes.
The comparison does not open referenced files. Local settings files are not supported.

## Equality contract

The signature and difference report use the same comparison definition.
The signature starts with `kicad-content-v1:sha256:`.
Store that prefix with the digest. A future comparison definition can change the prefix.
Do not compare digests from different definitions.
`result.equal` compares normalized content through the difference operation. It does not rely on hash equality.

All public stored fields participate. These include identifiers, names, metadata, properties, and geometry.
Private source line-ending information does not participate.
Whitespace between file tokens disappears during reading. Whitespace inside text fields remains content.
Numeric values compare exactly without a geometry tolerance. Equivalent integer and floating values compare equally.
Negative zero compares equally to zero. Nonfinite numbers cause an error.
Computed properties do not add duplicate derived content.

Only these collections ignore order:

| Object | Collection |
| --- | --- |
| Footprint | Pads, drawings, point items, zones, and groups |
| Symbol unit | Typed drawing items and pins |
| Symbol | Units with distinct identities, consistent encoded names, and compatible unit display names |
| Symbol file | Symbols with distinct effective KiCad item names |
| Board | Stored net names, with duplicate counts preserved |
| Board | Footprints, drawings, trailing drawings, point items, tracks, zones, groups, generators, and targets when identifiers and membership are unambiguous |
| Schematic | Root drawing shapes, texts, text boxes, junctions, no-connect flags, bus entries, lines, labels, images, tables, rule areas, groups, placed symbols, and sheets |
| Schematic | Embedded symbol definitions with distinct full KiCad names, including the library nickname |

Each collection retains complete items and duplicate counts.
Repeated pad numbers or identifiers never cause an item to disappear.
Symbol-name collisions and ambiguous unit identities retain their input order.
If schematic root items repeat an identifier, all schematic root item collections retain order.
Identifier collision detection ignores letter case. The stored identifier still participates unchanged in the signature.
Legacy timestamps, compact identifiers, and other noncanonical identifier spellings also retain root item order.
This protects group references whose target would otherwise be ambiguous.
Schematic embedded definitions use full names, so `LibraryA:R` and `LibraryB:R` remain distinct.
Legacy slash escapes participate in the embedded-name collision check.
Empty names, invalid item-name characters, and uncertain colon forms retain embedded-definition order.
Title-block comments compare by their numbered keys, independent of dictionary insertion order.

Board identifier checks include root items, direct footprint children, and cells of root tables.
Repeated or noncanonical identifiers preserve board item order and nested footprint item order.
Shared group or generator members also preserve these orders because KiCad assigns the last resolved owner.
Cyclic membership and noncanonical member references also retain item order.
The board keeps drawings and trailing drawings in separate collections.
Moving an item between these collections remains a difference.
Empty generators and stored legacy pad net codes remain content, even when a writer omits them.
The board reader discards some legacy net declarations and net-class declarations before comparison.
These board rules follow how KiCad 10.0.3 reads and writes board item lists and resolves group members.

All other sequences remain ordered.
These include properties, polygon vertices, curve control points, stackup layers, body styles, pin alternates, and 3D models.
Schematic table cells, rule-area vertices, fields, pin lists, and sheet-instance paths remain ordered.

## Difference records

`ContentComparison` contains `before_signature`, `after_signature`, `changes`, and the `equal` property.
Each immutable `ContentChange` contains a path, a change kind, and before-and-after values.
The kinds are `added`, `removed`, and `changed`.
Values use JSON text. Python `None` means absent. The JSON text `null` means a present empty value.
Paths describe normalized object fields. They are not source-file line numbers or original collection indexes.
Tuple values, such as coordinates, appear together.

Within independent collections, the report first pairs identical content with its duplicate count.
It then pairs changed items only when their stored identifier is unique in each complete collection.
Symbols and units can also pair by unique stored names.
It does not pair pads or pins by number because those numbers can repeat.
Items without an unambiguous match appear as removals and additions.

A pad with a unique identifier can report a changed `position` or `net_name` field.
Pad positions remain footprint-local coordinates. The report does not calculate board-space motion.
Placed schematic symbol positions use sheet coordinates. Changed values appear under their stored property fields.
It does not infer electrical connectivity or rename intent.
Display before-and-after values as plain text. Escape them when embedding them in HTML.

## Limits

This is represented-content comparison, not complete file-source comparison or electrical-equivalence proof.
File readers can normalize values and discard comments or accepted legacy fields before comparison.
This operation cannot recover that discarded source text.
Do not use its result to authorize overwriting a file or to detect concurrent source edits.
Use original source bytes for those purposes.

Unknown values in symbol item lists cause an error. Unsupported object types cause an error.
Reference cycles and excessive nesting cause an error.
Each input has a depth limit of 64 and a limit of 2,000,000 visited values.
A conservative encoding budget of 256 MiB also bounds strings and repeated content.
These work limits do not state the maximum process memory use.
Arbitrary Python objects do not become strings through a fallback conversion.
Objects must remain unchanged by callers while an operation runs.

External 3D file contents, parent symbol definitions outside the supplied objects, and project settings outside the supplied ProjectFile are outside this comparison.
Their stored paths and references still participate.
The operations do not support custom rule files, whole-project comparisons, or safe file replacement.
Board and schematic support keep the existing footprint and symbol signature definition unchanged.
