# Changelog

All notable changes to this project will be documented in this file.

## [0.39.0] - 2026-10-29

- Add PCB updates from schematics and schematic updates from PCB footprints.
- Add independent controls, detached previews, multi-unit handling, and guarded application.
- Transfer footprint definitions, fields, groups, pad nets, and population settings.
- Support physical net-label edits, complete unit swaps, and explicit reciprocal pin swaps.
- Block unsupported copper splits, assembly variants, stacked pins, and rule-area inheritance.

- Add schematic symbol updates from libraries and changes to other library identifiers.
- Add independent field and attribute controls, preserved-custom-field and atomic-part choices.
- Add detached batch previews, pin mismatch reports, and embedded-definition cache handling.
- Preserve sheet instances and expose source-derived behavior with pending GUI golden cases.

## [0.38.0] - 2026-10-20

Release candidate. Not tagged or published.

- Add whole-board reflection about one explicit datum for supported unrouted content.
- Fit sheet widths to titles, use a darker border, and require explicit top/bottom pin placement.
- Add fixed-template schematic, layout, and TODO note creation, reading, and updates.
- Seed new child sheets with the template Git table and a sheet-name heading.
- Change the default namespace text for `derive_uuids`. A board or schematic made again from the same input now gets different derived uuids than 0.37.4 produced. Uuids already stored in saved files do not change.
- Remove the hardcoded package-source address from the `inspection_package_source` field every netbom file carries. The field now reads a generic note instead of a registry URL.

## [0.37.4] - 2026-10-20

- Add optional word wrapping with a column-width limit and automatic row heights.
- Use compact default table spacing matched to the saved demonstration.
- Include a single-line table copy in the demonstration.
- Snap table origins and every cell corner to the 50 mil grid.

- Add native schematic tables with text-sized rows, columns, and optional collision bounds.
- Add detached table refitting with preserved identifiers, text styles, and explicit multiline text.

- Add explicit existing-footprint association and pad-net plans with detached Board results.
- Preserve PCB geometry and block unsupported footprint refreshes and routed-net changes.
- Support explicit reference updates and association-only mechanical footprints.
- Extend exact-source guarded saves to board files.

- Add append-only global-label class fields with current-coverage checks and native export evidence.
- Add current-schematic electrical inspection and comparisons independent of wire segmentation.
- Report class memberships from connected labels, bus labels, and project patterns with provenance and coverage.
- Add guarded schematic and project saves with exact-source checks, unchanged-byte preservation, and explicit partial failures.
- Reuse the staged file replacement operation for custom-rule saves.


## [0.36.0] - 2026-10-16

- Add isolated symbol and label field placement with forced-side options and explicit font control.
- Add global labels, hierarchical labels, grid-aligned sheets, and matching sheet pins with drawing defaults.
- Add item and field bounds, page limits, and collection collision checks, including wire-to-text and wire-to-body checks.
- Resolve global page-reference display settings and match native placement with the selected font.
- Add native-save regression fixtures and a mixed schematic demonstration.

## [0.35.2] - 2026-10-15

Prepared for review. Not tagged or published.

- Preserve rounded rectangle arcs and board dash ratios in SVG output and drawing bounds.
- Add explicit plot-text contexts to board and footprint rendering for saved glyphs and loaded font faces.
- Preserve named-font fills, multiline spacing, table-cell faces, and knockout glyph holes.
- Reject missing named-font data instead of substituting stroke text or omitting glyphs.
- Keep saved-glyph reuse opt-in for unchanged text geometry. Existing callers with named text must supply the context or a loaded face.
- Reuse successful local export and Linux runner checks without repeating native exports.

## [0.35.1] - 2026-10-14

Published and tagged. Anonymous downloads and a fresh installation verified on 2026-10-14.

- Preserve solid fill on footprint and board circles, rectangles, and polygons in SVG output.
- Preserve circle outline extent, layer colors, opacity, and footprint placement.

## [0.35.0] - 2026-10-13

Published. Registry presence verified on 2026-10-14.

- Resolve backdrill depth from supplied physical copper intervals and an explicit depth policy.
- Identify custom design-rule dependencies that can affect mask and paste apertures.
- Document source inputs and caller responsibilities for direct colored exports without a CAD dependency.
- Measure selected plot content from a loaded board without a KiCad or DXF export.
- Identify the fonts required by selected visible text for reuse across drawing measurements.
- Include actual drill marks, rotated geometry, and analytic curve limits in drawing measurements.
- Reject unsupported selected drawing content explicitly. See the plot-content bounds guide for current limits.
- Reuse saved text outlines when callers confirm unchanged drawing geometry.
- Resolve missing outline fonts through an explicit Linux Fontconfig policy and report substitutions.
- Preserve strict font loading as the default.

## [0.34.1] - 2026-10-13

Published. Registry and tag presence verified on 2026-10-13.

- Match clock pin triangles to KiCad editor proportions, with an optional plot style.
- Add optional electrical pin type text to symbol and schematic SVG exports.

## [0.34.0] - 2026-10-05

Published. Registry presence verified on 2026-10-06.

- Report each rendered SVG fragment with its source item and layer through an optional callback.
- Accept caller-approved path variables during library-table discovery while preserving directory restrictions.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.39.0] - 2026-10-29

- Match root-relative PCB symbol paths to full schematic occurrence paths in NetBOM.
- Preserve ambiguity checks across repeated sheets, multiple roots, and multi-unit symbols.

## [0.33.0] - 2026-09-29

- Add versioned NetBOM evidence for exact input bytes, relative file identities, and schematic dependencies.
- Add bounded source-evidence queries and focused inspection help.
- Distinguish absent files, uninspected files, and older exports without file evidence.
- Preserve extraction with repeated references to a missing schematic file.

## [0.32.4] - 2026-09-29

Preserve design-block library links in PCB groups during reads, writes, and edits.

## [0.32.3] - 2026-09-28

Match KiCad text-box placement when text ends with a newline. Preserve interior blank lines.
Match KiCad outline-font line spacing, including Cascadia Mono, and use the selected bold or italic face for text-box spacing.

## [0.32.2] - 2026-09-28

Preserve nets, buses, and item membership when only drawing text has unresolved variables.
Continue blocking uncertain electrical names, including labels, sheet pins, references, and power names.

## [0.32.1] - 2026-09-28

Pass selected fonts through schematic preparation and attached native assembly pages.
Measure outline-font paths for selection bounds, including quadratic curves and horizontal or vertical segments.

## [0.32.0] - 2026-09-28

Add drawing-sheet template loading, file edits, in-memory page graphics, and SVG export.
Support corner anchors, repeats, page filters, text constraints, polygons, and embedded images.
Keep raw text beside resolved variables and expressions. Allow explicit font control.
Preserve unchanged template files. See [drawing sheets](docs/drawing-sheets.md) for edited-file limits.

## [0.31.0] - 2026-09-28

Match KiCad text evaluation when the final closing brace is absent.
Include the complete Newstroke glyph table so bullets and other supported Unicode characters draw correctly.
Add local font-file and installed-name selection for schematic and symbol SVG exports.
Export selected fonts as filled glyph outlines. Keep the default font independent of optional font readers.
Document the project and Git context required for saved text variables.

## [0.30.0] - 2026-09-28

Add raw and resolved text results with diagnostics for variables and expressions.
Resolve owner fields, sheet context, cross references, table cells, and supplied runtime values.
Add optional text resolution to board, footprint, symbol, and schematic SVG exports.
Expose resolved text records in native schematic pages without changing source documents.
See [text resolution](docs/text-resolution.md) for context requirements and limits.

Add a shared native and assembly schematic page format with source identities and navigation links.
Generate a flat assembly root with separate board blocks, connector bundles, and declared stack order.
Retain resolved contact mappings, inferred evidence, and unresolved connections.
Add optional native source inspection to in-memory assemblies without changing the portable NetBOM format.
Add read-only project schematic preparation with occurrence-qualified drawings, symbol bounds, net memberships, bus memberships, and explicit coverage.
Preserve selected alternate pin functions while retaining KiCad automatic net names.
Viewer integration remains in development. Component-level flattening is deferred.

## [0.29.0] - 2026-09-25

Add explicit copper-layer inspection, planning, changes, and validation.
The caller supplies the layer mapping and resulting stackup.
Changes return a separate board and retain object identifiers, nets, and geometry.
Tracks, zones, pads, vias, secondary drill endpoints, zone defaults, and plot selections follow the mapping.
Occupied layer deletion, invalid spans, unsupported references, and invalid stackups block changes.
Zone fills become stale and require refilling in KiCad.
The configurator retains construction choices, symmetry, project export, and design-rule generation.
See [copper-layer changes](docs/copper-layer-changes.md) for supported cases and limits.

## [0.28.0] - 2026-09-25

Add compact zone-area estimates and paged totals by net and copper layer.
Include board and footprint zones. Separate outline area from saved-fill area.
Retain missing-geometry and fill-freshness limits. Exclude rule areas without merging overlapping zones.

## [0.27.0] - 2026-09-25

Add circuit-context queries with component pin tables across local nets and connector mates.
Expose pad coordinates, layers, and saved functions in pin queries and complete paged pad queries.
Add explicit board-only selection for unmatched schematic references.
Bound command-line reply bytes and support indented single-query output.
Include tool-discovery guidance and the approved package source in exported netbom conventions.
Expand command-line and query help for installed and offline inspection.

Add standalone netbom assemblies with deduplicated board designs and distinct board instances.
Derive system nets from identified connector mates and reusable pin-mapping definitions.
Add paged system inspection, connector views, mate diagnostics, and cross-board structural paths.
Compare connector hypotheses and check aggregate placement. Retain inferred mapping evidence and conditional system-net status.
Retain local net names, source evidence, rules, and coordinates. Do not join nets by matching names.
Support assembly editing, JSON save/load, and the portable inspection helper.

## [0.26.0] - 2026-09-25

Add read-only inspection commands for netbom and native KiCad projects.
Add paged electrical queries, exact native field reads, layout filters and source links.
Provide the same portable query code as a standard-library Python helper for offline distribution.

Add a standalone board-first netbom export with JSON and newline-delimited JSON output.
Retain component fields, placement, net assignments, selected symbol pin functions, and source evidence.
Add bounded component views, net views, indexes, and structural relationship tracing.
Report missing data, ambiguous associations, and conflicts. Preserve all input objects.

## [0.25.0] - 2026-09-24

Fix SVG export failures for symbol text boxes and skipped footprint text boxes.
Text boxes now include wrapped text, margins, alignment, rotation, and borders.
Symbol text boxes also retain fills and draw inside placed schematic symbols.
A hidden or zero-width border does not hide the text.
Custom fonts control glyphs and supply text advance for word wrapping.
Schematic exports now draw sheet text boxes, tables, images, rule areas, net-class flags, and do-not-populate marks.
Text supports multiline library text, symbol fields, labels, overbars, subscripts, superscripts, and stacked pin-number groups.
PCB exports now draw curves, targets, tables, reference images, barcodes, and knockout text.
Hatch fills draw clipped polygons. Custom pad copper includes merged shapes and Bezier primitives.
Barcode drawing adds the zxing-cpp dependency. Image and barcode drawing does not read external files.

## [0.24.0] - 2026-09-24

Compare saved project settings with the existing signatures and structured differences.
Object-key order and numeric spelling do not affect equality. Array order and unknown settings remain significant.

## [0.23.0] - 2026-09-24

Export a loaded schematic sheet to SVG without files or a KiCad process.
Draw embedded symbols, fields, connections, labels, sheet blocks, and multiline text.
Schematic font control applies to all text. The caller selects the drawing window and colors.
Unsupported visible items raise an error. Child sheets and project settings remain outside this operation.

Export in-memory symbols and footprints to SVG without files or a KiCad process.
Footprint exports include pads, drill holes, and mask and paste overrides through the existing board renderer.
Symbol exports select a unit and body style, resolve supplied parents, and draw body shapes, pins, fields, and text.
Symbol font control applies to pin names, pin numbers, fields, and free text.
Text boxes and hatched symbol fills report unsupported content instead of omitting it.

Compare footprint, symbol, schematic, and board content with compare_content and content_signature.
Signatures and structured differences share one versioned comparison definition.
Verified independent collections ignore order while preserving duplicate items.
Geometry sequences and properties retain order. All represented fields remain significant.
The operations do not detect comments or source text discarded by file readers.
Schematic comparison covers one file and preserves ambiguous item order, sheet paths, and embedded symbol definitions.

Board comparison includes stored fills, net assignments, generators, and stackup order.
Ambiguous identifiers and shared group members retain item order.

## [0.22.0] - 2026-09-23

Read and write custom design rule files with DrcFile.
Unchanged files retain their bytes. Edits preserve rule order, comments, expressions, and units.
Plugins can replace named comment blocks without changing human rules or other generated blocks.
Saves detect stale source copies and use a shared writer lock with atomic file replacement.
Validation covers file structure and constraint forms. It does not evaluate conditions or check a board.

## [0.21.0] - 2026-09-23

Read saved net class assignments for each board net with read_netclass_assignments.
The result combines saved schematic assignments with all matching project patterns.
Wildcard, regular-expression, and bus-range matching follow KiCad.
The operation preserves both files and requires synchronized saved inputs.
It does not calculate net class settings or rebuild schematic assignments.
Pattern matching adds the PCRE2 runtime dependency.
Board net-name collection now includes footprint zones, footprint drawings, and trailing board drawings.

## [0.20.0] - 2026-09-23

SVG exports now apply pad, footprint, and board solder mask and solder paste overrides.
Paste clearance and paste ratio inherit independently. Explicit zero prevents inheritance in current KiCad files.
The export expands or contracts the opening without changing copper or the source board.
Custom apertures combine their shapes before the offset and preserve holes.
Rounded rectangles use KiCad 10.0.4's corrected corner offset.
Custom design rules and minimum solder mask web merging remain outside this export.
Polygon offsets add the pyclipper runtime dependency.

## [0.19.5] - 2026-09-22

Fix invalid SVG when a zero-width outline also receives a layer stroke color.
The renderer now keeps stroke disabled for that outline and writes each stroke attribute once.
Fill, opacity, geometry, and element identity remain unchanged. Nonzero-width rendering is unchanged.

## [0.19.4] - 2026-09-22

Correct the measured bounds of footprint point markers and zones.
These items already store board coordinates. The bounds calculation applied the footprint placement again.
Panel-tab markers could therefore make a board outline appear much larger than the board.
The correction applies to board_bbox, footprint_bbox, and items_bbox. Local graphics, fields, and pads retain their placement transform.
No board files or geometry are changed by measurement.

## [0.19.3] - 2026-09-16

A formal test pass: two independent reviews, eleven findings, all fixed.

**Field text, the operator's report, in two parts.** The first part was a
missing text style: every field this library created for a database part
carried no size and no alignment, where KiCad writes a style block on every
placed field. The second part was found by the review and is worse: a placed
field's Y coordinate was wrong by twice its offset whenever the library gave it
one. KiCad flips the sign of Y when it reads a field out of a library and does
not when it reads one out of a schematic, so the offset must be SUBTRACTED. It
now is. Measured over 579 corpus schematics: of the hidden fields with a
non-zero offset, 4095 sit where this library now puts them and 48 do not, the
48 being fields an engineer moved by hand.

**A security hole this release opened, and closed.** 0.19.1 widened the read
confinement so the in-house libraries would resolve. It widened too far: a
project file could name any folder on the disk, a drive root included, as a
place this library may read. A tool that lists a library for a project someone
else wrote could then have been pointed at anything. Now the widening applies
only to the settings your own KiCad holds, never to anything a project file
says, and the check travels with the library row so no caller can forget it.

**A search word could hang the process.** A query wrapped in slashes is treated
as a pattern, which is KiCad's own behaviour. KiCad needs no guard because a
person is typing; a library does. One nine-character word ran for over five
minutes. Such a word is now refused and matched as plain text instead.

**Caps where there were none**, on a part database file and on a row from it:
the number of tables, the number of fields per table, the length of one value,
the number of footprint filters, and how deeply the file may nest. Each is at
least two orders of magnitude above the largest real value measured. Building a
part from a table of 20000 fields went from quadratic to 0.08 seconds.

**Two new test harnesses, and both libraries exercised**, per the operator's
own method: delete a part KiCad placed, re-add it from the database row, and
diff it attribute by attribute; and place every row of a database and check the
rules that decide whether KiCad will open the file. 892 provisional parts and
33 in-house parts placed, 0 faults of that class in either.

**What is still not right, with a number.** A VISIBLE field â€” the reference
designator and the value â€” does not land where KiCad would put it. KiCad tidies
those two after placing a part, and that tidying is 800 lines this library does
not have. The hidden fields are exact. The measurement and what it would take
to close the gap are in
`context/audit/placed-field-geometry-2026-09-16.md`.

## [0.19.2] - 2026-09-16

Field text, measured two ways and fixed where it was ours.

**The fault that was ours.** Every field this library created for a database
part carried no text style at all: no size, no alignment, nothing. KiCad writes
a style block on every placed field, so the file differed from KiCad's on every
custom field of every database part. Fixed at the source: a created field, and
the five fields `Symbol.new` seeds, now carry KiCad's own default, which writes
exactly `(effects (font (size 1.27 1.27)))`. The writer is unchanged, so a file
that legitimately carries no style block still round-trips.

**What is exact, with a number.** Measured against the definition each corpus
schematic cached, over 579 schematics and every database part in them: a HIDDEN
field matches KiCad on font size, text angle and alignment 100 percent of the
time, and on position 97 percent, the remainder being fields an engineer
dragged.

**What is not, with a number.** A VISIBLE field â€” the reference designator and
the value, the two an engineer looks at â€” matches KiCad's position 2 percent of
the time. One cause: when KiCad places a part it puts each field at the library
offset and then TIDIES the visible ones, moving the designator clear of the body
and re-aligning it. That tidying is 800 lines of KiCad this library does not
have. A part placed by this library opens, and is the right part, with its
designator where the library drew it rather than where KiCad would put it. The
measurement and what it would take to close the gap are in
`context/audit/placed-field-geometry-2026-09-16.md`.

**Two new test harnesses, and both libraries exercised.**
`scripts/replace_database_part.py` does the operator's own test: delete a part
KiCad placed, re-add it from the database row, diff it attribute by attribute.
`scripts/insert_database_parts.py` places every supplied row and checks the
rules that decide whether KiCad will open the file at all; it needs no oracle,
so it covers the in-house database too. Results: 892 provisional parts and 33
in-house parts placed, 0 faults of that class in either, 268 symbol libraries
resolved with none skipped.

**Two things a reader cannot reproduce, now recorded rather than chased.** The
order of the pin list and of the custom fields in a placed part is KiCad's own
in-memory order. The file stores the result, not the rule, so no reader can
replay it. Same class as the copper-stream re-sort recorded for the flip in
0.18.0. Neither affects what KiCad reads back.

## [0.19.1] - 2026-09-16

Five faults a consumer reported against 0.19.0, which was never tagged and
never published. Use 0.19.1.

**The file would not open.** Renaming a symbol renamed the parent and left its
unit children on the old name. KiCad requires a unit name to start with its
parent's name and refuses the WHOLE schematic when it does not, every sheet of
it, with `Invalid symbol unit name prefix`. Both rename paths were affected:
placing a database part, and the collision rename that runs when two different
definitions want the same name. `Symbol.renamed` is now the only way to rename
a symbol and it carries the units with it. Measured: 0 unit names KiCad would
refuse, across the 310 database parts in the corpus.

**Fields landed at the sheet corner.** A placed field position is a sheet
coordinate, not an offset from the part, and the placement was writing the
library offset unchanged. So a part placed at (120, 180) had 28 of its fields
sitting at (0, 0). They now land where KiCad puts them, by the same one-line
rule KiCad uses. A turn still does not move the field offsets, which is also
what KiCad's own turn does; what tidies a rotated part in the editor is the
field autoplacer, and this library has none.

**A library chain vanished, silently.** A row in the user's own global library
table pointing at a folder elsewhere on the disk was dropped by the read
confinement, with no error and no record. It cost 22 symbol libraries and 21
footprint libraries on the operator's machine, and the only way to see it was
to re-run with `strict=True`. Two changes: a row in the user's own GLOBAL table
is now followed, because the global tables ARE the user's configuration; and
every skip is recorded on `ResolvedLibTable.skipped` with a reason. Measured:
246 symbol libraries before, 268 after; 177 footprint libraries before, 198
after. `trust_table_chain=False` restores the old confinement, and then the
loss is recorded instead of silent.

**A part with no body looked like a real part.** When a database row's drawn
symbol cannot be found, KiCad builds a symbol carrying the metadata alone and
shows the error in its chooser. A library has no chooser, so that symbol was
placed and looked complete in the file. `resolve_row` now reports
`metadata_only` and says why, and `definition_from_row(..., require_source=True)`
refuses outright.

**A resistor was annotated U2.** That one was a symptom, not a fault: the
designator prefix is read off the definition, and a metadata-only definition
carries the `U` that KiCad's own constructor seeds. The two fixes above remove
the cause. The rule is now stated where the prefix is read, and it names what
to check before placing a part built from a row.

## [0.19.0] - 2026-09-15

This release lets a tool find a part and place it the way KiCad does.

Before it, the only question you could ask a library was "does this exact name
exist". Two of the answers were wrong on this machine, silently.

**Two silent faults, both measured and both fixed.**

- **A symbol library can be a folder, and folders were not read.** KiCad
  accepts one `.kicad_sym` file holding every part, OR a directory holding one
  file per part. Every in-house symbol library uses the directory form, so every
  lookup into one returned nothing at all.
- **The read confinement dropped the whole in-house chain.** Reads were limited
  to the KiCad install folder and the KiCad settings folder. The operator's
  table points at `${PARTS_PATH}`, which is neither, so the libraries behind it
  resolved and then read nothing, with no error. Measured before: 223 symbol
  libraries. After: 245, with all 22 in-house libraries. Every KiCad path
  variable that names a real folder is now a read root, which grants what your
  own KiCad grants and nothing more. `trust_path_variables=False` restores the
  old behaviour.

**Four new things.**

- **List a library.** `library_item_names` and `iter_library_records` say what
  a library holds, part by part, with every field. `iter_table_records` walks a
  whole merged table. Listing parses, so name the libraries you want: measured
  0.24 s per megabyte, which is 3 ms for an in-house library and about a minute
  for all 244 MB of the KiCad stock set.
- **Search and filter.** `search_records` is KiCad's own part search, ported
  weight for weight, plus two deliberate differences. Every field is
  searchable, because KiCad reads no custom field and the in-house data is all
  custom fields. And `key>10k` is not read as a number here, because reading
  `10k` as a resistance is part knowledge this library does not carry; use a
  `FieldTest` for a numeric comparison. Every match reports WHY it matched, so
  a tool can say "matched Tolerance = 1% and SEPackage = 0201".
- **Read a part database declaration.** `load_kicad_dbl` reads a `.kicad_dbl`
  file and reports what a caller needs to run the read: the driver, the server,
  the database, and per table the key column, the symbol column, the footprint
  column and the column list. **This library never connects.** Every password
  reads `<withheld>`, so a declaration is safe to log.
- **Place a part from a row.** You run the read; `definition_from_row` turns
  one row into a symbol the way KiCad's own database plugin does, and
  `inject_symbol` places it.

**The dividing line, and it is deliberate.** This library decides how part
information lands in a schematic. It does not decide which part. So it does not
connect to anything, does not write SQL, does not keep rows on disk, does not
hold a password, and does not judge a part. Knowing that one 1% 0402 resistor
substitutes for another belongs to the tool that owns the connection.

**Measured against KiCad's own output.** `scripts/database_oracle.py` compares
the row transform with 309 parts KiCad itself placed across the corpus, using
7350 real rows and the drawn symbols on disk. 294 of 309 match field for field.
The 15 that differ were each opened: in every case the database row or the
drawn symbol changed after KiCad wrote the schematic. None is a fault in the
code. Stock on hand, unit cost and the reference designator are excluded by
name, not silently.

## [0.18.0] - 2026-09-11

This release flips a footprint to the other side of the board.

`flip_footprint` is a port of KiCad's own `Flip` command, and it is measured
against boards KiCad 10.0.4 itself produced. There is no command-line flip in
KiCad, so the reference boards were made by hand: a control save with no change,
then one flip per board. The control came back byte-identical, so every
difference in the other two files is the flip.

The flipped footprint is byte-exact. Side, placement, orientation, every pad,
every field and every graphic match what KiCad wrote.

Three limits are stated rather than hidden.

- **The board around the footprint may re-sort.** KiCad orders the copper stream
  by a net code that exists only in memory. The file stores no net code, so no
  reader can replay one. A flip changes what touches what, KiCad renumbers, and
  the stream re-sorts. Six nets changed number on the measured board.
- **A copper pour keeps its stored fill.** KiCad refills on save when the copper
  changed. This library has no filler, so the fill records it leaves are the
  ones the file already held. KiCad rebuilds them on its next save.
- **Mirroring, and flipping a selection, are still absent.** Single footprints
  only.
- **Only the left-right direction is measured.** Three KiCad-made boards pin it
  byte for byte. The top-bottom direction is a source port with no reference
  board behind it, because KiCad's shipped default is left-right.

Two facts about KiCad that the documentation now records, because both are easy
to get backwards:

- **The shipped flip direction is left-right, not top-bottom.** `FLIP_DIRECTION`
  orders `LEFT_RIGHT` first, so it casts to `false`, and the settings key that
  stores it is named `editing.flip_left_right` with `false` meaning left-right.
  The name reads as the opposite of its value.
- **The opposite of a layer is computed from the board, not stored.** KiCad
  rebuilds the pairing in three passes, and two of them pair user layers by the
  names that one board declares. So the same layer can flip to different places
  on two boards.

Also fixed: the board layer table keyed off the raw ordinal column, which is not
a layer id. A board written with legacy numbering paired the front solder mask
with a user layer. The corpus found it; no test had.

## [0.17.0] - 2026-09-11

This release draws pad copper and fixes three defects, one of which let a
crafted file hang the renderer.

Coverage of the SVG exporter is now a SCRIPT rather than a claim.
`scripts/render_coverage.py` reads KiCad's own plot sources and this package's
code, and exits non-zero on any KiCad case nobody has classified. It grades 48
cases and reports 38 covered.

Every number here was measured against KiCad 10.0.3, by reading its source or by
running `kicad-cli`. Nothing is graded against this library's own output.

`qa-engineer` and `security-reviewer` both ran against this tree. It is the first
release in five to carry an independent review.

This release adds 2 names to the package root. It removes none.

### Added

- **Pad copper in the renderer**, opt-in through `BoardContent.pads`. Six of the
  seven pad shapes match `kicad-cli` vertex for vertex: circle, rect, oval,
  trapezoid, roundrect and chamfered rect. `EXACT_PAD_SHAPES` states that set and
  `pad_geometry_is_exact` answers for one pad. A CUSTOM pad is the exception,
  because KiCad unions the anchor and every primitive with a boolean add.
- **KiCad's rounded rectangle polygon**, in `eedesigncore.common.polygon`.
  KiCad does not draw a rounded rectangle as arcs. Every plot path but Gerber
  converts the shape first, so a 5 by 3 mm pad at ratio 0.25 is a 32-vertex path.
  Drawing true arcs would be geometrically better and would agree with nothing,
  so this ports the approximation instead.
- **A via diameter option.** `ViaDiameter.per_layer` reads the via's padstack for
  the layer being drawn, which is what KiCad plots. `ViaDiameter.largest` draws
  the widest ring of the padstack on every layer.
- **A rule-area stream**, `BoardContent.rule_areas`. KiCad's plot skips a rule
  area, so this draws content no KiCad export holds. `KICAD_CONTENT` is the set
  that stays comparable against `kicad-cli`. `ALL_CONTENT` adds this one.
- **`scripts/render_coverage.py`**, which grades the renderer against KiCad's
  plot switch across seven axes. Its anti-staleness rule is exercised rather than
  asserted: a test removes a table entry, breaks a source citation, and claims a
  model the renderer lacks, and the audit must refuse each time.
- **`scripts/make_pad_probe.py`** and a committed 18-case pad fixture. A corpus
  board carries whatever shapes its footprints happened to use, and a trapezoid,
  a chamfered rect and a custom pad are the rarest in practice.

### Fixed

- **A crafted pad size could hang the renderer.** `arc_to_segment_count` had no
  upper bound on the radius. A radius of 1e19 internal units asked for 94,285,467
  segments on one corner, and past about 1e20 the arithmetic raised
  `ZeroDivisionError`. A board a few hundred bytes long hung the render past 90
  seconds. The radius now clamps to the largest a C++ `int` holds, which no KiCad
  file can exceed, so no real geometry moved. The same board renders in 0.003 s.
- **Layer selection dropped every wildcard item.** `on_layers` compared the raw
  token, so `on_layers(pad, {"F.Cu"})` was FALSE for a pad whose list is
  `["*.Cu"]`. Since `*.Cu` is how KiCad writes every through-hole pad, any caller
  selecting pads by copper layer got none, with no error. That reached
  `items_on_layers`, `items_bbox` and every `layers=` argument. The wildcard table
  moved into `common/layers.py` beside the layer names, so the reader that selects
  by name and the reader that sorts by ordinal share one table. `*In.Cu` joined
  it: KiCad's parser accepts that token and its writer never emits it.
- **A pad's bounding box was too small, three ways.** KiCad merges the copper of
  every unique padstack layer and then merges the HOLE
  (`PAD::BuildEffectiveShapes`). This measured the top-level size alone, so it
  missed an inner layer wider than the front, missed a drill wider than its
  copper, and centred the copper on the pad position rather than on the shape
  position that `(offset ...)` moves it to. `items_bbox` also raised `TypeError`
  for a bare pad, so no caller could measure one at all.
- **A via ignored its padstack and its unused-layer rule.** The diameter came
  from the top-level `size` on every layer. `PCB_VIA::FlashLayer` was not applied.
  Note that a via and a pad do NOT share that rule: a pad keeps
  `remove_except_start_end` on an external copper layer, and a via keeps it on its
  own drill start and end, which differ for a blind or buried via.

### Changed

- **`ALL_CONTENT` now means every stream, including the ones past KiCad.** Use
  `KICAD_CONTENT` for a render that must stay comparable against `kicad-cli`.
- **The pad padstack resolution moved** to `eedesigncore.mod.pad_geometry`, so
  the renderer and the bounding box read one resolution and cannot disagree.

### Verification

4175 tests pass. `ruff`, `ruff format` and `mypy --strict` clean. The
`kicad-cli` parity oracle passes, now including 18 pad cases on a committed
fixture.

The roundrect polygon was checked vertex for vertex against `kicad-cli`: worst
disagreement 63 nanometres, against an SVG that writes 100 nanometre digits.

Three rules had to be exactly right, and each was measured rather than inferred.
The arc error is 5080 internal units, and 4064 gives a 36-vertex polygon where
KiCad gives 32. `SHAPE_LINE_CHAIN::Append` drops a point equal to its
predecessor but never compares the first against the last, which is why a
circular pad is 45 vertices and not 44 or 48. `CornerListRemoveDuplicates` walks
backward, seeded from corner zero.

Two `test_mutation_fidelity` failures are pre-existing and unrelated. A schematic
property round-trips as `"Netclass"` against an input of `"Net Class"`.

## [0.16.0] - 2026-09-10

This release makes the library read what KiCad writes. Loading KiCad's own
`qa/data` tree went from 90 of 119 modern-format boards to all 119, and from 92
of 95 schematics and 23 of 24 footprints to all of both. The save path is 1.9x
faster with no output byte changed. The renderer draws copper, text and
dimensions.

Every number here was measured against KiCad 10.0.3, by reading its source or by
running `kicad-cli`. Nothing is graded against this library's own output.

This release adds 5 names to the package root. It removes none.

### Added

- **`eedesigncore.__version__`** (UF-012). The release string as the imported
  CODE reports it. `src/eedesigncore/_version.py` is now the single version
  authority: hatchling derives the distribution version from it and the package
  root re-exports it, so metadata and module cannot disagree. The root `VERSION`
  file is a mirror, and a test fails if the two drift.
- **Copper in the renderer**, opt-in per call. `render_board_svg` takes
  `content`, and `BoardContent` names the streams: `drawings`, `tracks`, `zones`.
  `ALL_CONTENT` and `DEFAULT_CONTENT` are the two useful sets. The default is
  drawings only, so no existing render moves.
- **Text in the renderer.** Board text, footprint fields such as reference
  designators, and a dimension's measurement label. All three were skipped
  before, so a silkscreen preview carried no labels at all.
- **Dimensions in the renderer**, including the gap KiCad cuts in the crossbar
  for the text. `TextMetrics` lets a caller override the font.
- **KiCad's stroke font.** `eedesigncore.common.font` ports Newstroke, the font
  KiCad draws with when a text item names no other. 95 glyphs and about 2 KB,
  generated from the pinned clone by `scripts/build_stroke_font.py`.

### Fixed

- **A protection value has three states** (UF-016). `tenting`, `covering`,
  `plugging`, `capping` and `filling` take `yes`, `no` or `none`. Reading `none`
  raised, so 22 modern-format boards could not load.
- **The legacy bare-word protection spelling loaded SILENTLY WRONG.**
  `(tenting front back)` read as unset on both sides and wrote back
  `(front no) (back no)`. 35 boards in KiCad's test data carry that spelling, and
  each one lost the tenting on every via with no error.
- **The board setup `tenting` default was inverted.** KiCad defaults both sides
  to tented; this library defaulted to untented. 139 of 215 qa boards carry no
  `(tenting â€¦)` token at all, so every one of them loaded as untented.
- **Numeric atoms now match KiCad's own lexer rule.** An exponent with no decimal
  point (`-1e-06`) and a leading plus (`+5`) are numbers to KiCad and were
  symbols here. The widening is at the numeric ACCESSORS, not the classifier,
  because that rule also calls a legacy tstamp such as `586432E5` a number and
  converting one would destroy the identifier.
- **A shape accepts a legacy numeric net code**, `(net 1)` inside a `gr_line` or
  `gr_poly`. Tracks and zones already did; the six shape loaders did not.
- **`curve_points`**, the legacy teardrop spelling, maps onto `curved_edges`.
- **`bus_alias` and `symbol_instances`** load at the schematic root. Both are
  read-side only in KiCad, which writes neither, so both are dropped on save
  exactly as KiCad drops them.
- **A hole pad with no `(drill â€¦)` token** takes KiCad's default instead of
  raising: 1 nm for `thru_hole`, 30 mils for `np_thru_hole`.
- **Text drew at the point-marker width.** A text item carries no `(stroke â€¦)`,
  so the width fell through to the render policy. It now derives the pen the way
  `EDA_TEXT::GetEffectiveTextPenWidth` does.

### Changed

- **`prettify` is 1.9x faster**, and byte-identical. On a 2.77 MB board the
  whole save went from 0.90 s to 0.57 s and `Board.to_string` from 3.08 MB/s to
  4.97 MB/s. Three mechanical changes, no change to the algorithm or to any
  decision it makes. Equivalence proven over 2392 distinct files in all three
  format modes, 7266 comparisons, zero differences, and separately by
  `corpus_test.py` run back to back with prettify as the only variable.
- **The version authority moved** from the root `VERSION` file into the package.
  The release manager now edits `src/eedesigncore/_version.py`; `VERSION`
  remains as a mirror the tooling reads.
- **The agent document budgets rose**, hub 12288 to 16384 and each spoke 6144 to
  8192. Both were full to within a handful of bytes.

### Verification

3997 tests pass. `ruff`, `ruff format` and `mypy --strict` clean. The
`kicad-cli` render parity oracle passes.

Dimensions and text were measured against `kicad-cli pcb export svg`: 233 of 233
stroke segments matched to 0.003 mm, across a dimension with text, plain text,
rotated text and left-justified text.

Two `test_mutation_fidelity` failures are pre-existing, confirmed by running that
file on a clean `origin/main` worktree.

## [0.15.0] - 2026-09-05

Holes join the SVG renderer. A hole is not on a layer, so it arrives as its own
stackable document rather than as content of a copper layer.

This release adds 5 names to the package root. It removes none.

### Added

- **Hole rendering, as a pseudo-layer.** Ask for `HOLES` beside the canonical
  layer names and one document comes back holding every hole on the board: pad
  drills round and oval, plated and non-plated, and via holes of every via type.
  `HOLES_PLATED` and `HOLES_NON_PLATED` return the two halves separately, so a
  caller that wants them in different colors writes no policy at all. A caller
  that wants one document reads `Hole.plated` from its own policy instead.
- **True hole geometry.** A round hole is a true circle. A slot is two straight
  sides and two true arc caps, turned by the pad angle. Neither is polygonized.
- **`Hole.touches`, the layer span of a hole.** KiCad draws a drill mark only
  when the item's layer span meets the layer being plotted, so a single-layer
  export holds a SUBSET of the board's holes. A hole document holds them all,
  including a blind or buried via that no single layer shows. A caller rebuilds
  KiCad's subset from a policy when it wants to compare the two.
- **`compare_holes`, a hole-aware oracle.** KiCad draws a slot as one thick
  round-capped segment, the centre line. This renderer draws the outline, so
  every hole in the document is one solid area a caller styles one way. Both are
  the same stadium, and a primitive comparison cannot see that. `compare_holes`
  reduces either form to two cap centres and a radius.

### Measured

- On a real board of 348 holes, the 134 that touch F.Cu all match a drill mark
  in kicad-cli's own F.Cu export, with none drawn that kicad-cli does not draw.
- A constructed board of four slots at four orientations plus one round hole
  matches kicad-cli exactly, comparing outline against centre line.

### Notes

The hole sits at the pad position. The `(offset ...)` inside `(drill ...)` does
NOT move it: KiCad feeds that value to `PAD::SetOffset`, which places the copper
(`pcbnew/pad.cpp:1558-1570`), and the drill pass plots at `GetPosition()`.

KiCad clamps a drawn drill mark to just inside its pad
(`plot_brditems_plotter.cpp:1284-1286`). This renderer emits the size the board
stores. The two differ by one nanometre on a pad whose drill equals its size,
which is below the printed precision, and differ visibly only when a drill
exceeds its own pad.

## [0.14.1] - 2026-09-05

Three defects were reported. Two were real and are fixed. The third is correct
behaviour on input that was not what the report assumed, and the evidence is
below so the question does not have to be reopened.

### Fixed

- **A footprint-hosted zone and point now move and turn with the footprint.**
  The file stores both in BOARD coordinates, so nothing else moves them. A
  translate left them at the old position while the footprint moved away. A
  panel of six copies stacked all 24 footprint-hosted keepout zones on the first
  copy. `translate_board`, `translate_footprint`, `translate_items`,
  `rotate_board`, `rotate_footprint`, `rotate_items`, `set_footprint_orientation`
  and `inject_board` all carry the fix, because they share one helper.

  The root cause was a wrong row in
  `context/audit/footprint-transform-frame-2026-09-02.md`, which recorded both
  types as footprint-local. That row is corrected and now cites the call it was
  read from, as every other row does. `set_footprint_orientation` also never
  turned an embedded dimension, reference image or barcode; it does now.

- **A degenerate arc no longer raises from the SVG renderer.** Three points on
  one line raised `OverflowError` from `arc_center`. The renderer promises that
  an item it cannot draw reaches the caller's skip report rather than an
  exception, so a raise was a broken contract.

  The guard measured the bow of the curve in millimetres on the raw input, then
  computed the centre from nanometre-rounded points. A curve rising half a
  nanometre passed the first test and was straight by the second. Both now ask
  one function, `is_straight`, on the nanometre grid. A search of 200000
  near-collinear arcs raised 4 times before the fix and 0 after. The same search
  produced 13353 radii above one kilometre before, and 3037 after; those
  remaining are genuinely shallow arcs whose radius is correct.

### Not a defect

- **Drawing order on a first write.** The reported board is not in KiCad
  canonical form. Its arcs carry a winding KiCad swaps on load, and the load path
  reproduces that swap, which changes the sort key and moves the item.

  `kicad-cli pcb upgrade --force` on that board makes KiCad perform the identical
  swap and write the identical recomputed mid point. Feeding KiCad's own output
  back through this package reproduces it byte for byte. The round-trip guarantee
  has always been stated for a file KiCad wrote, and it holds. A new test pins
  the reported arc against the values KiCad produced.

  One real difference remains, and it is not the reported one: KiCad rounds a
  coordinate to the nanometre grid on write, and this package preserves the extra
  digits the input carried. It shows only on input that is already off-grid.

## [0.14.0] - 2026-09-04

This release draws a board to SVG straight from the model. The entry point is
`render_board_svg`. It performs no file access and it starts no subprocess.

This is the first feature in the project where matching KiCad exactly is not the
goal. SVG is an output format, not a KiCad file, so the byte-identity rule does
not apply to it. The rule here is determinism: one board and one set of
arguments always give the same bytes. Agreement with `kicad-cli` is a test
oracle. ADR-098 records the ruling.

This release adds 8 names to the package root. It removes none.

### Added

- **`render_board_svg`, board to SVG.** It returns one SVG document per
  requested layer, which mirrors `kicad-cli --mode-multi` usage. The caller
  supplies the window in millimeters, and the renderer never crops to content,
  so several renders stack into one frame.
- **Stroked graphics, board-hosted and footprint-hosted.** Line, arc,
  rectangle, circle and polygon outlines, with stroke width, dash pattern and
  round caps. A footprint child maps into board coordinates first.
- **The `point` item.** KiCad's plot path exports nothing for it, so this is one
  place where the renderer goes past `kicad-cli` on purpose. The marker is a
  cross inside a ring, taken from the on-screen painter.
- **Per-item element identity.** Set `emit_ids` and every element carries the
  item uuid in its `id` attribute. A live preview uses it to tell which element
  the user clicked. `kicad-cli` cannot do this at all.
- **A style policy that also filters.** `style_for` returns `None` to drop an
  item, so one hook does both jobs. `LayerStyle` covers color and opacity per
  layer, and it keeps the opacity the caller asks for. `CliParityStyle` is the
  permanent oracle preset.
- **`render.compare`, a geometry comparison.** It reads two SVG documents back
  into primitives and compares them inside a tolerance. It rasterizes nothing,
  because a raster hides which primitive moved and fails on anti-aliasing that
  carries no meaning.
- **Skip reporting.** An item this renderer cannot draw is skipped, never fatal,
  and `on_skip` hears about every one. Footprint fields reach the report too,
  because a preview that loses a reference designator without a word looks like
  a defect in the board.

### Measured

- Stroked geometry agrees exactly with `kicad-cli` on a real board of 90
  footprints. The renderer emits no primitive that `kicad-cli` does not.
- A board of 261 footprints renders one layer in about 9 ms and six layers in
  about 30 ms. `kicad-cli` needs about 3.8 s for two layers on the same board,
  and it needs the board written to disk first.

### Out of scope

Text, dimensions, teardrops, pads, filled polygons, zone fill records, Bezier
curves, and paint order fidelity. `kicad-cli` stays the renderer of record for
full-fidelity output.

## [0.13.0] - 2026-09-03

This release adds a connectivity engine for schematic files. The engine derives
nets from geometry. It does not need a running KiCad instance.

The engine is internal. No name from it enters the package root. The public API
is identical to 0.12.1.

This release adds no name to the package root. It also removes no name. Version
0.12.0 added 23 names and version 0.12.1 fixed one function. This branch did not
touch either change.

Iteration numbers and version numbers are independent. Iteration 12.1 targets
0.13.0. A parallel clone owns 0.12.0 and 0.12.1.

### Added

- **Net derivation for `.kicad_sch` files, internal to this package.** The
  engine ports KiCad `CONNECTION_GRAPH` one phase at a time. It reads a root
  sheet. It returns a name and a pin set for each net.

  The engine covers hierarchy propagation, bus expansion, bus aliases, power
  symbols, and net class labels. It does not model net codes. A net code value
  depends on file item order, so it is not reproducible.

  Measurement against netlists from the KiCad command line tool: 1752 of 1752
  nets match exactly. All 19 test designs match. Both grouping and naming reach
  100 percent.

- **Text variable resolution in `common/text_variables.py`, also internal.**
  Project variables and sheet variables resolve before any name comparison.

### Notes

- No existing file changed. Every change in `src` is a new file.
- Twenty new modules hold the engine. Each module names the KiCad source
  function that it ports.
- Two tie breaks differ from KiCad on purpose. KiCad iterates an unordered
  container in both places, so KiCad has no defined answer. ADR-095 records each
  choice with its source citation.
## [0.12.1] - 2026-09-03

One fix. `strip_uuids` destroyed two kinds of reference that it was written to
protect.

### Fixed

- **`strip_uuids` did not protect a schematic group or a board generator.**
  ADR-019 point 3 says a strip must keep every uuid that a group references,
  because KiCad drops a member whose uuid resolves to nothing and then drops the
  emptied owner on the next save. The protection tested
  `isinstance(model, PcbGroup)`, so it covered a board or footprint group only.

  Two owners went unprotected. A schematic `Group` lost its members on
  `strip_uuids(schematic)`. A `PcbGenerator`, which names the tracks that a
  tuning pattern owns, lost its members on `strip_uuids(board)`. In both cases
  the strip left the `members` list pointing at uuids that no longer existed,
  which is the exact failure that the group-aware default exists to prevent.

  The protection now matches on the `members` FIELD, the same way the 0.12.0
  remap does. All three owners are covered.

  0.12.0 fixed the REGENERATE path and left the STRIP path half done. This
  completes it.

### Notes

- `strip_uuids`, `generate_uuids` and `derive_uuids` walk a model tree and match
  on field name, so they work on a `Footprint`, a `Board` and a `Schematic`
  alike. The docstrings said "Both operate on a `Footprint`", which was three
  releases stale. They now state the real reach. `.kicad_sym` stays a documented
  no-op, because the format carries no uuid.

## [0.12.0] - 2026-09-02

Board manipulation primitives: translation, rotation, bounding box,
board-into-board injection with deterministic uuids and net renaming, and zone
fill record operations. Twenty-three names joined the package root `__all__` and
no name left it.

**Read the three fixes below before you raise a pin.** Each one shipped wrong in
0.11.2 and each one changes output. All three produce geometrically or
structurally wrong files today, so a caller this breaks was already writing a
wrong file.

### Fixed

- **`inject_footprint(rotation=â€¦)` placed every pad and every label at the wrong
  angle.** It set the placement angle and left the footprint's children alone. A
  pad angle and a footprint text angle are stored in BOARD terms, so the
  placement orientation has to carry them. A KiCad resave could not reveal this,
  because KiCad's parse and its write are inverse for every angle field; only a
  plot shows it. A gerber of the old output puts every pad in the right PLACE
  with the wrong ROTATION.

  Output changes for any non-zero `rotation`. Nothing changes at zero.

- **`extract_footprint` baked the board placement into the part it wrote.** A
  footprint extracted from a rotated placement carried that rotation in its pad
  and text angles, so the `.kicad_mod` was not the library part. KiCad's own
  `FootprintSave` calls `SetOrientation( ANGLE_0 )` before it writes a library
  file, which de-rotates every child angle.

  Output changes for a footprint whose placement angle is not zero. The two
  fixes above are exact inverses, so extract and inject still round-trip.

- **`generate_uuids(only_missing=False)` left two kinds of reference
  dangling.** It remapped a board or footprint group and nothing else. A
  `PcbGenerator` names the tracks it owns by uuid, and a schematic `Group` names
  its members; both kept the OLD uuids after a regeneration. KiCad drops a
  member whose uuid resolves to nothing, then drops the emptied owner on the
  next save. Every uuid reference list is remapped now.

- **`docs/agent/boards.md` named a dead field.** It documented `Board.nets` as
  the net view. `Board.nets` is never populated; `Board.net_names` is the derived
  view.

### Added

- `translate_board`, `translate_footprint` and `translate_items` move board
  content by a millimetre offset, in place. Every coordinate moves in internal
  units, so a moved value is one KiCad could have written and a KiCad resave
  leaves it alone. Coverage is every board item type.

- `rotate_board`, `rotate_footprint` and `rotate_items` turn board content about
  a centre. A quarter turn is exact. Any other angle is refused when the
  selection holds a rectangle, because KiCad turns a non-cardinally rotated
  rectangle into a polygon and this library does not reproduce that
  tessellation. A turn drops every `render_cache`: KiCad clears the same cache
  and rebuilds the glyphs from an outline font, which needs a font engine this
  library does not have.

- `set_footprint_orientation` gives a placed footprint a new orientation without
  moving it. KiCad has TWO turn operations and they differ on the file:
  `FOOTPRINT::Rotate` keeps every text upright, which can add a half turn and
  swap both justifications, and `SetOrientation` does not. This is the second.

- `board_bbox`, `footprint_bbox` and `items_bbox` return a `BoundingBox` in
  millimetres, or `None` when nothing was measured. `layers=` restricts the
  measurement, which is how a caller asks for the board outline.
  `include_width=False` drops the half-stroke-width growth. An arc contributes
  its true extent, not just its three stored points. A text with no
  `render_cache` contributes its anchor point only, because this library loads no
  font.

- `inject_board` copies the CONTENT of one board into another at a placement and
  returns an `InjectedContent` holding the items it added. `new_board_from`
  returns an empty board carrying a source board's SETTINGS. The two are
  complements.

- `derive_uuids` replaces every uuid with `uuid5(namespace, f"{old}|{key}")`,
  so the same source and the same key give the same bytes on every run and on
  every machine. `DERIVED_UUID_NAMESPACE` is the fixed default namespace.

- `rename_nets` and `rename_footprint_nets` apply a net-name map to every item
  that references a net, and clear the legacy numeric code beside every name
  they rename, so no item is left holding one name and one stale code.

- `extract_fills`, `replace_fills` and `flatten_fills` read a zone's stored fill
  records, transplant them onto another zone, and turn them into board-level
  copper graphics that no later refill can move. None of them fills a zone:
  computing a fill needs the clearance engine and the DRC model.

- `item_layers` and `items_on_layers` expose the layer selection that every
  `layers=` argument already applied. A multi-layer shape emits `(layers â€¦)`
  instead of `(layer â€¦)`, and restating that rule in a consumer is how it gets
  got wrong.

- `BoundingBox` and `InjectedContent` in the package root `__all__`, by the
  0.11.0 reachability rule.

### Notes

- **A net is a NAME in KiCad 10.** Injecting content that names `GND` into a
  board that already names `GND` JOINS the two nets. Pass `net_map` to keep two
  copies apart.

- **A footprint child is stored footprint-local and de-rotated**, so moving or
  turning a placed footprint changes its `at` and nothing else. Four kinds of
  child are the exception, because they hold board coordinates whatever their
  parent: a `Dimension`, a `ReferenceImage`, a `Barcode`, and the `render_cache`
  glyph outlines of any text. Full frame table and its KiCad citations:
  `context/audit/footprint-transform-frame-2026-09-02.md`.

- **A flattened island can inherit a stale gerber net attribute.** A net-less
  copper graphic plotted after a net-carrying one keeps the earlier `%TO.N`,
  because the zone plot path clears the attribute between the main area and the
  islands and the graphic path does not. The copper is identical and the
  consequence is metadata only: a flattened island reads as the zone's net in a
  fab netlist check, where a zone's own island reads unattributed. No field on
  the graphic reaches it. Measured in
  `context/audit/zone-fill-flatten-rules-2026-09-02.md`.

- Mirroring is not implemented. KiCad has two operations there too, `Mirror` and
  `Flip`, and they will be modelled together when a consumer needs them.

- The known round-trip issue on schematic symbol property order that 0.11.2
  shipped is unchanged. See BS-11.12-2.

## [0.11.2] - 2026-08-31

`parse` and the S-expression node types are now public. Closes the half of
UF-011 that found a real fault.

Every change is additive. Three names joined the package root `__all__`, no name
left it, and no signature, model, or output changed.

### Fixed

- **Five exported loaders had no supported way to call them.** `load_mod`,
  `load_pcb`, `load_sch`, `load_sym`, and `load_lib_table` each take an
  `SExprNode`. Neither `parse` nor `SExprNode` was exported, so nothing in the
  root API could build the argument. Since 0.11.0 the root `__all__` is the whole
  supported API, so those five functions were exported and uncallable.

  The JSON side never had this gap. `parse_json` is exported, and `load_pro` and
  `load_prl` take its result. The S-expression side now matches:

  ```python
  from eedesigncore import load_sch, parse

  sch = load_sch(parse(text))
  ```

- **A shipped document sent a caller outside the supported API.**
  `docs/agent/common.md` told a caller to import `eedesigncore.sexpr.parser`. No
  shipped document names a submodule now.

### Added

- `parse`, `SExprNode`, and `SExprNodeType` in the package root `__all__`.
  `SExprNodeType` follows from the 0.11.0 reachability rule, because `parse`
  returns `SExprNode` and its `node_type` field names the enum.

### Note for a caller who wanted a path round trip

`parse` is the lower-level path. It is not the one most callers want. Every root
model already reads and writes a path, and that route preserves the source line
endings:

```python
fp = Footprint.from_file(path)
fp.to_file(path)
```

`to_string()` plus a manual write emits line feeds, so it rewrites every line of
a CRLF file. Reach for `parse` only when you already hold text and want the tree.

### Known issue

One corpus schematic does not round-trip byte-identically in this release. The
schematic writer sorts `lib_symbols` properties with the five mandatory ones
first. KiCad preserves the stored order, confirmed by a KiCad 10.0 GUI save that
reproduced the stored file byte for byte. A file that lists user properties first
therefore changes on a round trip.

Nothing in 0.11.2 caused this and nothing in 0.11.2 fixes it. The fault predates
0.11.1. It is recorded as BS-11.12-2 with the evidence and the work needed. Four
corpus files were excluded for this class on evidence that a later check found
invalid, so the true count is five files.

If you round-trip a schematic whose symbols carry user properties, compare the
output before you commit it.

## [0.11.1] - 2026-08-26

A wrong field name on a model constructor now raises instead of being
discarded. Closes UF-010, raised by the footprint-generation consumer.

No public name, no signature, and no field changed. No loader and no writer
changed. All four corpus gates report byte-identical output. The only behavior
that changed is the treatment of a call that was already wrong.

### Fixed

- **A model constructor rejects an unknown keyword.** Every model now sets
  `extra="forbid"`, so construction raises `ValidationError` naming the
  unexpected keyword. Attribute assignment already rejected an unknown name, so
  the two paths now agree. Before this release, construction discarded the
  keyword in silence and the model kept its default:

  ```python
  Pad(number="1", type="smd", shape="rect", layers=["F.Cu"], at=(1.5, 2.5))
  # 0.11.0: accepted. `at` discarded, position stays (0.0, 0.0)
  # 0.11.1: ValidationError, "at" is not a permitted field
  ```

  The real field names in the three cases the report named are `Pad.position`,
  `Stroke.type`, and `PcbText.content`.

- **One instance of the same fault inside this project's own tests.** A
  `Stroke` built with `stroke_type` took the default stroke type. The test
  asserted prefix parity only, so it passed. Corrected.

### Changed

- 142 models now inherit `KicadModel`, a new internal base class that carries
  the rule. The base class is not exported and a consumer never names it. The
  four `.kicad_pro` and `.kicad_prl` models keep `frozen` and
  `arbitrary_types_allowed` beside the new setting.

### Upgrade note

**A caller that passes a wrong field name starts to fail.** That caller was
writing a wrong file before this release, so the new `ValidationError` reports a
defect that already existed in it. The failure now appears at construction
rather than at write, or not at all.

Read the exception message. It names the keyword the model did not accept.

Do not log `exc.errors()` or `exc.json()` verbatim for input that comes from
outside your program. The structured error carries the value you passed, and
Pydantic does not truncate it. Prefer `str(exc)` for a short diagnostic.

## [0.11.0] - 2026-08-19

The support boundary moves, and nothing breaks. Every change is additive. No
model, no signature, no loader, and no writer changed, and all four corpus
gates report the counts 0.10.2 reported.

`docs/agent/README.md Â§ Support boundaries` has always said the package root
`__all__` is the whole supported API and that the list is complete. The list
was not complete. `SymbolLib` was exported and `Symbol` was not, while
`SymbolLib.symbols` hands a caller a `Symbol`. The same held for `Pad` inside
`Footprint` and for `PlacedSymbol` inside `Schematic`.

**The root list now CLOSES UNDER REACHABILITY.** If a public field or a public
return can hand you an object, the type of that object is exported. The rule is
measured on every test run, not asserted.

This is a MINOR release rather than a patch because 150 names gained a
compatibility promise. A later removal of any one of them is BREAKING.

### Added

- 150 names joined the package root `__all__`, which grew from 65 to 215. No
  name left it. 138 are Pydantic models and enums a public field or a public
  return already hands to a caller. 12 are the type aliases a public field
  annotation spells: `SymbolItem`, `PadPrimitive`, `GeneratorValue`,
  `TrackItem`, `PcbDrawItem`, `GraphicShape`, `PtsElement`, `LayerTypeToken`,
  `JsonValue`, `LibTableKind`, `LibTableScope`, and `JumperPadGroups`.
  By category: shared parts and enums (`Stroke`, `Fill`, `TextAttributes`,
  `Color`, `Property`, `EmbeddedFile`, `Paper`, `TitleBlock`, `PtsArc`,
  `RenderCache`, `RenderCachePolygon`), board and footprint graphics (`Pcb*`),
  board and footprint items (`Zone*`, `Dimension*`, `Table*`, `Barcode*`,
  `ReferenceImage`), footprint members (`Pad`, `Padstack*`, `Prim*`, `Variant*`,
  `Stackup*`, `Model3D`, `DrillInfo`, `TeardropParams`), board members
  (`Board*`, `Setup`, `Track*`, `Via*`, `PcbGenerator`, `Gen*`, `PcbTarget`),
  symbol members (`Symbol`, `SymbolUnit`, `Pin`, `PinAlternate`, `SymText`,
  `SymTextBox`, `Sch*`), and schematic members (`PlacedSymbol`, `Sheet*`,
  `Instance*`, `Line`, `Label`, `Image`, `Group`, `Junction`, `NoConnect`,
  `BusEntry`, `RuleArea`, `SchTable*`).
- 16 of the 150 sat in NO export list anywhere in the package before this
  release: `GenNumber`, `GenBool`, `GenPoint`, `GenPts`, `GenString`,
  `PrimLine`, `PrimRect`, `PrimArc`, `PrimCircle`, `PrimCurve`, `PrimPoly`,
  `PrimVector`, `PrimBbox`, `PtsArc`, `RenderCache`, and `RenderCachePolygon`.
  A caller could hold each one and had no supported way to name its type.
- `docs/agent/README.md Â§ Every public name` is now a census table that names
  every one of the 215 exports, one row per category.

### Changed

- `tests/unit/test_export_surface.py` was rewritten. It computes the reachable
  set on every run and fails for any reachable type that is absent from the
  ROOT `__all__`. It supersedes the 0.10.2 guard, which collected enums only
  and accepted a sub-package `__all__` as sufficient.

### Warning for `import *`

The root namespace is flat, and it now carries generic short names: `Line`,
`Table`, `Image`, `Property`, `Group`, `Setup`, `Color`, `Variant`, `Zone`,
`Sheet`, `Label`, `Symbol`, `Pin`, `Pad`, `Stroke`, `Fill`, `Paper`, `Via`,
`Dimension`, and `Barcode`. `from eedesigncore import *` will shadow any
same-named object in the importing module. Import the names you need instead.
No name was renamed and none carries an underscore prefix, because `import *`
is a consumer choice and this library documents the consequence.

## [0.10.2] - 2026-08-19

An upstream-feedback release, and it is NOT breaking. Every API change is
additive, and the one behavior change is a cost fix that leaves every result
byte for byte the same. Two consumer reports asked for names the library
defined and never exported. A source audit of KiCad's escape module
(`context/audit/escape-string-module-2026-08-18.md`) confirmed both and found
that the recorded reasoning behind one of them was wrong. No writer changed, no
model changed, and all four corpus gates report the counts 0.10.1 reported.

### Added

- Nine names joined the package root `__all__`, which
  `docs/agent/README.md Â§ Support boundaries` defines as the whole supported
  API. Five are enums a public model field already names: `StrokeType`,
  `FillType`, `JustifyH`, `JustifyV`, and `PowerType`. Four are footprint-filter
  functions: `split_fp_filters`, `join_fp_filters`, `semantic_fp_filters`, and
  `unescape_string`. All nine are now supported API, so a later removal is
  BREAKING.
- `eedesigncore.unescape_string` is a faithful port of KiCad's
  `UnescapeString` (`common/string_utils.cpp:313-394`), including the length
  shortcut, the result of KiCad's depth-counted brace scan, the unterminated branch, the
  `$~^_` markup guard, the 14-entry token table, and the unknown-token branch.
  The port makes one left-to-right pass and never recurses, so a deeply nested
  value raises no `RecursionError`.
- `eedesigncore.semantic_fp_filters` splits a `ki_fp_filters` value the way
  KiCad splits it in memory. **It returns a DIFFERENT list from
  `split_fp_filters` for any value carrying an escape token other than a plain
  `{space}`, and for a `{space}` preceded by `$`, `~`, `^` or `_`.** That is by
  design. Load with `split_fp_filters` and write with `join_fp_filters` when
  the file bytes matter, which is what the loaders and the writers still do.
- `tests/unit/test_export_surface.py` walks the annotation graph from every
  root-exported model, collects every enum it reaches, and fails for one that
  sits in no `__all__`. It closes the fault class the two reports are instances
  of, not only the five names.
- Three assertions close `context/security-findings-iter-11.9.md Â§ MEDIUM-7`:
  the `dev` extra hatchling bound must equal the `[build-system]` bound, the
  `constraints.txt` pin must fall inside it, and the built wheel must carry
  `Metadata-Version: 2.4`, which is the field that broke the v0.9.1 publish.

### Fixed

- `unescape_string` cost quadratic time and quadratic peak memory on two
  inputs, a balanced brace nest and a run of open braces. It now costs linear
  time and linear peak memory on both, because it makes one left-to-right pass
  and holds no per-level frame. A 32000-character run of `{` fell from 521 MB
  to under 2 KB of peak live data, and a 16001-character nest fell from 21.8
  seconds to under 12 milliseconds. The output is unchanged for every input: a
  SHA-256 digest over 300003 generated values matches the 0.10.1 digest, and a
  200000-case differential against a recursive transliteration of KiCad's own
  function reports 0 mismatches
  (`context/security-findings-iter-11.10.md Â§ MEDIUM-8`,
  `context/decisions.md Â§ ADR-084`).
- The `unescape_string` cost note named the wrong resource. It described time
  only, so a consumer sizing a guard from it would size it for the wrong thing.
  It now names time and peak memory, and states that nesting depth raises
  neither (`context/security-findings-iter-11.10.md Â§ LOW-28`).
- The `split_fp_filters` docstring stated a promise it does not keep. It
  justified its narrow reverse with a write-side fact that does not bound the
  read side, where a stored value may carry any escape token. It now states the
  real promise, a byte-preserving split rather than KiCad's in-memory value,
  and it names the markup-guard divergence: KiCad reads `A_{space}B` as
  `A_{space}B`, while this function reads it as `A_ B`.
- `context/decisions.md Â§ ADR-074` item 1 carries a dated correction block.
  Its claim that no KiCad path stores an escaped filter token is false, at
  least nine paths exist, its "fixed point" claim holds for bytes only, and one
  line citation was off by two.
- `docs/agent/schematics.md` records the `mirror` trap. KiCad's schematic
  parser reads `(justify â€¦ mirror)` and discards it, while its board parser
  stores it. This library shares one effects loader and one effects writer, so
  it preserves the token on every path. Setting the flag on schematic text
  writes a token KiCad ignores.

### Unchanged, deliberately

- `split_fp_filters` and `join_fp_filters` behave exactly as they did in
  0.10.1, byte for byte. The audit proves that a full unescape on load breaks
  byte identity, and it grades `join_fp_filters` an exact port of
  `EscapeString( â€¦, CTX_NO_SPACE )` with no defect. Both are frozen
  (`context/decisions.md Â§ ADR-083`).

## [0.10.1] - 2026-08-18

A documentation and packaging release. The library itself is unchanged: no file
under `src/` was edited, and all four corpus gates report the same counts as
0.10.0.

v0.10.0 shipped a BREAKING change with a stale consumer surface. `README.md`
still pinned 0.9.0 in both install commands, said nothing about the break, and
carried no link to this file. `README.md` is the package long description, so
that text is the registry landing page a consumer copies from.

### Fixed

- Both `README.md` install commands and the one in `eedesigncore-AGENTS.md`
  now pin the release being shipped. The pins read 0.9.0 and 0.9.2.
- `README.md` states the corpus scores this release measured. The previous
  text was three iterations old.

### Added

- `README.md Â§ Upgrading` carries a short migration note for the 0.10.0
  symbol-member break and links to this file.
- `[project.urls]` in `pyproject.toml` declares Homepage, Repository,
  Changelog, Documentation, and Issues, so the registry page links back to the
  source and to the release notes.
- The wheel now ships the support set: `docs/`, `eedesigncore-AGENTS.md`,
  `README.md`, and `CHANGELOG.md`, all under `eedesigncore/`, mirroring the
  repository layout so every relative pointer resolves offline
  (`context/decisions.md Â§ ADR-079`). The wheel gains no new top-level entry.
- `tests/integration/test_consumer_surface.py` enforces all of the above:
  pinned versions equal `VERSION`, `[project.urls]` targets that name a
  repository path resolve, `README.md` links to `CHANGELOG.md`, `CHANGELOG.md`
  carries a heading for the current `VERSION`, and the built wheel carries the
  intended manifest. The wheel is built in process through the hatchling
  backend, so the test needs no network and adds about a quarter second
  (`context/decisions.md Â§ ADR-080`).
- `hatchling` joined the `dev` extra and `constraints.txt`, because the
  wheel-manifest test calls the build backend directly.

## [0.10.0] - 2026-08-18

An upstream-feedback patch. A consumer reported that this library keeps
`ki_locked`, `ki_keywords`, and `ki_fp_filters` wherever the source file put
them, while KiCad writes all three last. A source audit and a `kicad-cli`
oracle run confirmed the report and corrected it on four points
(`context/decisions.md Â§ ADR-073`).

### Changed - BREAKING

- **Symbol DCM data left `Symbol.fields`.** `ki_locked`, `ki_keywords`, and
  `ki_fp_filters` are now first-class members on
  `eedesigncore.common.sch_family.symbol.Symbol`:

  | Was, in `symbol.fields` | Is now |
  |---|---|
  | `Property(name="ki_locked", â€¦)` | `symbol.units_locked: bool` |
  | `Property(name="ki_keywords", â€¦)` | `symbol.keywords: str \| None` |
  | `Property(name="ki_fp_filters", â€¦)` | `symbol.fp_filters: list[str]` |

  Code that scans `symbol.fields` for one of the three names now finds
  nothing. Read the member instead. This mirrors KiCad, which stores the
  three on `LIB_SYMBOL` members and synthesizes a throwaway field for each on
  every save. The same `Symbol` model backs a `.kicad_sym` library and a
  schematic `lib_symbols` entry, so the change reaches both.

  `fp_filters` is a LIST on the symbol side. `Footprint.fp_filters` stays a
  plain string, because the two KiCad members differ in type.

- **A placed symbol no longer inherits the three.** `inject_symbol` copies the
  definition's fields onto the placed instance, and the three names used to
  ride along. KiCad's placed-symbol writer synthesizes none of them, so the
  old behavior emitted tokens KiCad never writes.

### Fixed

- The writer emits the three after every real property, in the fixed order
  `ki_locked`, `ki_keywords`, `ki_fp_filters`, on both `.kicad_sym` write
  paths and, by delegation, in a schematic `lib_symbols` block. Appending a
  user property to a symbol that carries any of the three used to place it
  after them, which KiCad reorders on its next save. Each emission is gated
  per name: the lock flag, a non-empty keyword string, a non-empty filter
  list. Nothing is written unconditionally.
- A derived symbol never writes `ki_locked`, whatever its lock flag holds.
  When its `keywords` or `fp_filters` is empty it now materializes the
  parent's value at write time, walking the `extends` chain with a cycle
  guard, exactly as `LIB_SYMBOL::GetKeyWords` and `GetFPFilters` do.
- A space inside one symbol footprint filter now escapes to `{space}` on
  write and reverses on load, so a filter list survives a round trip.
- The loader accepts and discards the legacy `(id N)` property child token. A
  non-integer payload raises `KicadParseError`.
- **Symbol parent resolution now tracks KiCad on all four axes, through one
  shared rule** (`context/decisions.md Â§ ADR-078`). Nothing here changes the
  output of any KiCad-written file; all four corrections narrow behaviour on
  input KiCad itself cannot round-trip.

  | Shape | Was | Is now |
  |---|---|---|
  | `(symbol "R")` with `(extends "Device:R")` | resolved, by reducing the `extends` value | does not resolve, as in KiCad |
  | legacy `(symbol "A/B")` with `(extends "A{slash}B")` | did not resolve | resolves, as in KiCad |
  | a `.kicad_sch` `lib_symbols` entry carrying `extends` | inherited keywords and filters from a sibling entry | inherits nothing |
  | the inheritance-depth sort | used a second, exact-only rule | uses the one rule |

  The `{slash}` unescape governs matching only. `Symbol.name` and
  `Symbol.extends` still hold, and still write, exactly the text the file
  carried.

  Two divergences from KiCad are kept on purpose. A schematic `lib_symbols`
  entry keeps its `extends` token, which KiCad drops on save. And an
  ambiguous bare parent name resolves to nothing here, where KiCad takes the
  last definition in file order and destroys the other symbol.

### Performance

Two costs the security review of this patch measured, both closed here. No
output changes: the resolution rules and the field order are identical, proved
by the four corpus gates, by a hash compare of every fixture before and after,
and by 600 randomized differential cases.

- **Writing a `.kicad_sym` was cubic in the symbol count** when the `extends`
  values needed the bare-name fallback. Every hop of the parent walk rescanned
  the whole library, and the two resolvers walked independently once per symbol.
  A 24 KB, 400-symbol chain took 3.4 s to write; it now takes 0.008 s, and the
  cost grows linearly. The library map is now built once per write and carries a
  bare-name index plus a per-symbol memo of the resolved keywords and filters.
- **Loading a `.kicad_sym` was quadratic in the property count** when a symbol
  repeated the `Description` property. Each repeat rescanned the accumulated
  field list. A 555 KB file took 1.32 s to load; it now takes 0.19 s. The loader
  carries the position of the Description field instead of searching for it.

The parent-resolution corrections above cost one to three percent on the four
real fixture libraries, which is noise. One synthetic shape is genuinely
slower: a deep chain whose keys carry a `Lib:` prefix now resolves, where the
inheritance-depth sort used to miss it and report every symbol as a root. The
sort pays the same per-symbol chain walk KiCad pays in `sortByInheritance`. An
equivalent chain with bare names cost the same before this change.

### Round-trip exception, deliberate

A pre-2023 `.kicad_sym` file that carries `ki_description` or `(id N)` is NOT
byte-identical after a round trip. The loader folds `ki_description` into the
mandatory `Description` property and drops `(id N)`, which is the upgrade
KiCad itself performs, and no writer in KiCad 10 emits either token. Byte
identity for KiCad 10 input is untouched: `ki_description` disappeared at
symbol-library format version 20230620, and every KiCad 10 file is far past
it. The legacy fixture under `tests/fixtures/sym_legacy/` asserts the
upgrade, never byte identity.

### Tests

- 2243 tests passing, coverage at 92.06 percent (gate: 80 percent).
- New fixture `tests/fixtures/sym/Relay.kicad_sym`, the stock library
  regenerated with `kicad-cli sym upgrade --force` for the reason ADR-070
  records, and new legacy fixture
  `tests/fixtures/sym_legacy/Transmission_Line.kicad_sym`.
- External corpus: mod/sym 6292 of 6292 byte-identical; boards 40 of 40
  byte-identical, plus 3 files in `not_kicad_written` and 3 named
  exclusions; schematics 201 of 201 graded byte-identical, plus 40
  exclusions; project files 299 of 299 byte-identical, plus 7 files in
  `not_kicad_written`.
- One schematic joined the named-exclusion table. KiCad rewrites it at the
  same offset this harness reports, and the resaved file then agrees with
  our output on every non-uuid line, so the stored file is not a
  byte-identity oracle (`context/decisions.md Â§ ADR-075`).
- The parent-resolution corrections carry their own differential proof: 44
  round-trip hash streams over every symbol and schematic fixture match the
  pre-change code exactly, and 2400 randomized `extends`-graph streams show 0
  changes across all 894 streams whose shape KiCad can write.
- `ruff check`, `ruff format --check`, and `mypy --strict` all clean.

## [0.9.3] - 2026-08-14

### Added

- Agent-facing documentation set, shipped in the source distribution.
  `eedesigncore-AGENTS.md` is a self-contained bootstrap for AI agents: the
  consumer install line, the contributor clone path, and the reading order.
  `docs/agent/` holds the hub (invariants, the support boundaries, the
  Not-implemented table, the do-not list) and six scope documents, so an
  agent reads only its file type. Enforcement tests lock every path
  reference, every heading pointer, the `__all__` coverage, the size
  budgets, and the code fences (175 tests).

### Changed

- The source distribution now includes `docs/` and the bootstrap file.

## [0.9.2] - 2026-08-14

A consumer-feedback patch. Every item traces to the
`kicad-symbol-generation` consumer's field use of the library (UF-001,
UF-003).

### Fixed

- UF-003: schematic-context unit indices now come from the unit name's own
  trailing suffix, not from a slice of the parent name. The old slice
  silently reported `unit_index` 0 for every unit of every multi-unit
  `lib_symbols` entry, and this corrupted rename workflows downstream. An
  unparseable unit name now raises `KicadParseError`, matching KiCad's own
  parser rejection. Raised by the kicad-symbol-generation consumer.

### Added

- UF-001: the package ships a PEP 561 `py.typed` marker. A consumer now
  type-checks against this library under `mypy --strict` with no override.
  Raised by the kicad-symbol-generation consumer.

### Build

- `[build-system]` bounds `hatchling` to `>=1.27,<1.32`, so an isolated
  build emits Metadata-Version 2.4. The pinned `twine` accepts that
  version. The v0.9.1 publish failure cannot recur.

### Tests

- 1964 tests passing, coverage at 91.93 percent (gate: 80 percent).
- External corpus: mod/sym 6292 of 6292 byte-identical; boards 36 of 36
  byte-identical, plus 2 files excluded as `not_kicad_written` and 3 files
  excluded under the named-exclusion rule; schematics 188 of 188 graded
  byte-identical, plus 39 files excluded; project files 296 of 296
  byte-identical, plus 5 files excluded as `not_kicad_written`.
- `ruff check`, `ruff format --check`, and `mypy --strict` all clean.

## [0.9.1] - 2026-08-13

### Changed

Every writer now sorts sibling nodes into the order KiCad itself uses at save
time. This library already wrote byte-identical output for an unmutated
round trip, because sorted input is a fixed point of both a stable sort and
KiCad's own order. **Output for a MUTATED model now changes.** A mutated
model saves the bytes KiCad 10.0.3 would save, instead of the caller's
encounter order.

- `.kicad_sym`: draw items sort per unit by type rank, then `StrNumCmp` pin
  number, then position. Units sort by unit and body-style index. Properties
  sort by ordinal. Library symbols sort by inheritance depth, then name
  (ADR-055, R1, R2).
- `.kicad_mod` and `.kicad_pcb`: pads, drawings, zones, groups, and 3D models
  sort inside a footprint. Footprints, tracks, zones, groups, and generators
  sort inside a board (ADR-057, ADR-058, R4).
- `.kicad_sch`: the `(lib_symbols)` map sorts by lib id and reuses the symbol
  comparator above. Placed-symbol fields sort by ordinal, and instance blocks
  sort, though KiCad never regroups a duplicate path (ADR-063, R3, R5).
- `.kicad_pro` and `.kicad_prl`: three array paths now sort at write time,
  matching the KiCad container behind each one. `hidden_netclasses` sorts
  ascending. `net_settings.classes` puts `Default` first, then sorts by name.
  Each `netclass_assignments` inner array sorts ascending (ADR-065, R6).
- Arc winding now normalizes at load, the same swap KiCad's own
  `EDA_SHAPE::SetArcGeometry` applies. A loaded arc with a disagreeing
  winding gets its `start` and `end` swapped once, on the way in (ADR-067).

### Added

- A new mutation-fidelity test class (`tests/integration/test_mutation_fidelity.py`,
  R7). It loads a golden file, mutates it through the models, writes the
  result, and resaves a copy with the `kicad-cli` oracle (`sym upgrade`,
  `fp upgrade`, `pcb upgrade`, `sch upgrade`, all `--force`). It asserts the
  two outputs match byte for byte. Each oracle test carries a skip marker
  when `kicad-cli` is absent, and an inertness control test proves the
  oracle changes nothing on already-canonical input (ADR-069).
- Corpus exclusion rules in `scripts/corpus_test.py`: one global rule for
  editor-artifact directories, and one named list for files the operator has
  confirmed are stale. Every excluded file prints with its reason on every
  run, so the rule cannot hide a regression (ADR-066).
- A finiteness check on every numeric value a file supplies. A non-finite
  coordinate or angle now raises `KicadParseError` naming the token, instead
  of an `OverflowError` that escapes the library's error contract.

### Fixed

- A hand-built `Property` now writes its `(effects ...)` block exactly as
  KiCad does, even when the caller supplies no text attributes (ADR-068).
- `StrNumCmp`, the natural-sort comparator behind the pin-number and
  pad-number order above, now wraps a long digit run to a 32-bit integer
  the same way the KiCad binary does, instead of growing an unbounded
  Python integer. The fix also removes a superlinear cost the old port
  carried on a hostile digit run (ADR-071).

### Known Limitations

1. A directly-constructed arc with a disagreeing winding normalizes at
   LOAD, not at construction. A caller who builds a `SchArc`, `PcbArc`, or
   `PrimArc` by hand still gets the three points as given, and a KiCad
   resave would swap them (ADR-067 scope, staged for the next
   constructability pass).
2. The effects default in ADR-068 covers `Property` only. A hand-built
   `SymText`, `SymTextBox`, `PcbText`, schematic text, label, table cell,
   dimension text, or pin name and number block may still omit its
   `(effects ...)` block (staged for the next constructability pass).
3. A hand-built model with no `uuid` writes no `(uuid ...)` node, BY
   DESIGN. UUIDs stay one hundred percent caller-controlled. This is the
   operator's sole sanctioned deviation from KiCad-exact output (ADR-068,
   ruling 2026-08-13).

### Tests

- 1944 tests passing, coverage at 91.92 percent (gate: 80 percent).
- External corpus: mod/sym 6292 of 6292 byte-identical; boards 36 of 36
  byte-identical, plus 2 files excluded as `not_kicad_written` and 3 files
  excluded under the new exclusion rule; schematics 188 of 188 graded
  byte-identical, plus 39 files excluded; project files 296 of 296
  byte-identical, plus 5 files excluded as `not_kicad_written`.
- `ruff check`, `ruff format --check`, and `mypy --strict` all clean.

## [0.9.0] - 2026-07-30

### Breaking

- `extract_footprint`: the `ref` argument is renamed to `reference`. `inject_footprint`
  and `inject_symbol` already used `reference`, so the name now matches across all
  six operations (ADR-045).
- `extract_symbol`: the `lib_id` argument is now keyword-only. Every argument except
  the model object is keyword-only under the new rule (ADR-045).
- `inject_footprint`: the `fp` argument is renamed to `footprint`, matching the
  `symbol` argument name on `inject_symbol` (ADR-045).
- `Pad()`, `Padstack()`, `PcbCurve()`, `PrimCurve()`, and `SchBezier()` no longer
  construct with no arguments. Each field that KiCad always writes to disk is now
  required or validated at construct time (ADR-048).
- The custom-pad primitives rule is dropped. A custom pad with no primitives is
  valid input. KiCad's own writer emits it as a bare `(primitives)` block, so the
  prior rejection was wrong (ADR-048).
- The `zstandard` dependency now carries a marker:
  `zstandard==0.25.0; python_version < "3.14"`. Python 3.14 and later use the
  standard library `compression.zstd` module and install no `zstandard` wheel (R13).
- `pydantic` now declares a range, `pydantic>=2.10,<3`, in place of an exact pin.
  `constraints.txt` still pins the exact development version, `pydantic==2.13.4`
  (operator ruling, 2026-07-28).

### Added

- Six public operations export from the package root under one calling contract:
  `extract_footprint`, `inject_footprint`, `extract_symbol`, `inject_symbol`,
  `strip_uuids`, and `generate_uuids`. The model object is positional. Every other
  argument is keyword-only (ADR-045, R1).
- `inject_symbol` assigns the lowest free `<Prefix><n>` reference when the caller
  omits `reference`, instead of copying the library definition's reference
  verbatim. This changes the default behavior for a caller that relied on the old
  bare-prefix result (ADR-046, R2).
- Lib-table discovery and resolution for `fp-lib-table` and `sym-lib-table`:
  recursive nested-table loading, environment expansion limited to the KiCad
  variable set, and path confinement to the caller's allowed roots by default.
  `inject_footprint` and `inject_symbol` both resolve a bare name to the real
  KiCad nickname through a resolved table (ADR-047, R3, R4).
- A full `.kicad_pro` and `.kicad_prl` JSON pipeline: parser, tree, loader, and
  writer. Every project file round-trips byte-identical, and every key the
  library does not model is preserved on write (ADR-049, R8).
- `load_hierarchy` follows the `Sheetfile` reference of every sub-sheet from a
  `.kicad_pro` or a root `.kicad_sch` file and returns the whole project tree.
  Every resolved reference is confined to the project directory by default
  (ADR-051, R10).
- `EmbeddedFile.to_bytes` decodes zstd-compressed embedded data. It checks the
  stored checksum and enforces a 1 GiB decompression cap on both backends (R16).
- Packaging: the wheel builds with `python -m build`, `VERSION` is the single
  source of the package version (R15), and `constraints.txt` pins the exact
  versions the development environment and CI install.

### Security

- Three Medium findings from the T12 security review are fixed and
  re-verified before this release. The lib-table resolver now refuses a UNC
  path and a URL scheme before it resolves a nested-table reference, limits
  environment expansion to the KiCad variable set, and confines every scanned
  library read to the same allowed roots as the table read (MEDIUM-1,
  MEDIUM-2). The tier-2 zstd decoder now feeds the frame to `decompressobj` in
  slices and counts the output instead of reserving the cap up front, closing
  the memory-exhaustion path a hostile frame could trigger (MEDIUM-3,
  ADR-053).
- The tier-2 zstd backend refuses an incomplete frame with the same message
  tier 1 uses (LOW-4, fixed in the same wave). The sheet-hierarchy walk now
  refuses a UNC or remote `Sheetfile` reference before it resolves, matching
  the resolver rule (LOW-5, ADR-054).
- Zero Critical and zero High findings. `context/security-findings.md` and
  `context/security-findings-iter-11.md` carry the full T12 report and the
  re-verification.

### Architecture

Ten ADRs from iteration 11 are Accepted: ADR-045 (one calling contract for the
six operations), ADR-046 (lowest-free-number auto-annotation), ADR-047
(lib-table discovery and resolution), ADR-048 (construct-time validators, the
custom-pad primitives rule dropped), ADR-049 (`pro/` preserves the parsed
tree), ADR-050 (`--ext pro` corpus grading), ADR-051 (hierarchy confinement),
ADR-052 (documentation examples become tests), ADR-053 (the tier-2 zstd decode
feeds `decompressobj` in slices), and ADR-054 (path guards move to
`common/paths.py`). ADR-043 (the registry publication procedure lives in
`docs/publishing.md`) is Rejected by the operator (2026-07-30). The procedure
moves to `context/release-publishing.md`, and the consumer install steps move
to `README.md`, as part of this release.

### Tests

- 1664 tests passing, coverage at 94.06 percent (gate: 80 percent).
- External corpus: mod/sym 6292 of 6292 byte-identical; boards 38 of 38
  byte-identical, plus 2 hand-edited files excluded as `not_kicad_written`
  (ADR-044); schematics 224 of 224 byte-identical; project files 295 of 295
  byte-identical, plus 6 hand-edited files excluded as `not_kicad_written`
  (ADR-050).
- `ruff check`, `ruff format --check`, and `mypy --strict` all clean.

## [0.8.0] - 2026-07-17

### Added

- **Full `.kicad_sch` schematic format support** â€” new `sch/` package (`models`,
  `loader`, `writer`, `operations`) implementing the complete v10 schematic
  grammar targeting file version `20260306` (generator `eeschema`):
  - Schematic root grammar: header, `(paper)`/`(title_block)` (reusing the
    common-tier page emitter/loader lift, see below), `(lib_symbols)`, the
    ordered schematic item stream, `(sheet_instances)` (R1/R2)
  - `(lib_symbols)` reuse of `common/sch_family` `Symbol`/pin/shape/text with
    the top-level `Lib:Name` qualification carried verbatim in `Symbol.name`
    â€” zero sym-writer branching required; `SaveContext.SCHEMATIC` proven
    unnecessary for round-trip (ADR-037)
  - Placed `PlacedSymbol` instance node with per-pin/instances blocks,
    including the nested `(variant (name)(on_board)...)` list form (not a
    bare string) on instance paths (R4)
  - sch-only high-frequency items (R5): wire/bus/graphical line, junction,
    no_connect, bus_entry, labels (local/global/hierarchical/netclass),
    text/text_box; root graphic shapes (arc/circle/rectangle/bezier/polyline)
    reusing `common/sch_family` shapes, with 2-point-polyline-vs-line and
    >2-point-polyline-vs-shape disambiguation matching KiCad's
    `saveLine`/`saveShape` split (ADR-038); bus-to-bus entries modeled as
    plain lines, matching their on-disk indistinguishability from bus lines
    (ADR-039)
  - sch-only lower-frequency items (R6): rule_area, image/bitmap, group, table
  - `Sheet` node + `(sheet_instances)` (R7); single-file `Schematic.from_file`
    load semantics â€” sub-sheet `Sheetfile` references are preserved as opaque
    data, never resolved or opened; project-level multi-sheet hierarchy
    helper explicitly deferred (ADR-040)
  - Embedded files: per-symbol inline data + separate schematic-root block,
    no consolidation (R8)
  - `SaveContext.SCHEMATIC` activated in the sym writer; the LIBRARY
    (`.kicad_sym`) code path is proven byte-inert by the unchanged mod/sym
    corpus (R9)
- **`extract_symbol` / `inject_symbol`** (`sch/operations.py`, R11) â€”
  construct a LIBRARY-clean `Symbol` from a placed schematic symbol and
  inject a symbol into a schematic (bounded collision-rename per Â§5.2, no
  regex/S-expression injection on hostile `lib_id`/`name` input); optional
  `lib_nickname` qualifies a bare incoming name. When `lib_nickname` is
  omitted, a bare name now defaults to the placeholder nickname
  `eedesigncore:<name>` instead of staying unqualified â€” until lib-table-
  driven nickname resolution lands in iter-11 (ADR-041, operator amendment 2)
  â€” applied symmetrically to `inject_footprint`'s `fp_id`
- **`scripts/corpus_test.py` `.kicad_sch` support** â€” M1 (parse-only) / M2
  (write-back) scoreboard for the schematic corpus, following the iter-9
  board-corpus grading pattern (non-target-version files counted as
  version-skipped, not failures) (R10)
- **`href` support in the common text-effects emitter** (`dump_effects`) â€”
  hyperlink attribute on schematic text/label effects, emitted after
  `justify`
- **`Paper`/`TitleBlock` emitters AND loaders lifted to `common`** â€”
  `dump_paper`/`dump_title_block`/`title_block_has_content` and the matching
  `load_paper`/`load_title_block` now live in `common/writers.py` /
  `common/loaders.py`; both `pcb` and `sch` share the same page-setup code
  (byte-inert lift; avoids a `sch` -> `pcb` cross-format import) (ADR-037)

### Fixed

- Placed-symbol instance `(path (variant ...))` grammar â€” corrected from a
  bare `(variant "...")` string to the real nested list form
  (`(variant (name)(on_board)(dnp)(exclude_from_sim)(in_bom)(in_pos_files)...)`),
  fixing an M1 parse blocker discovered by the schematic corpus harness

### Architecture

Four ADRs from iteration 10 are `Status: Accepted (operator-approved
2026-07-17)`: **ADR-037** (`Lib:Name` carried verbatim in `Symbol.name`, no
`SaveContext` threading needed for round-trip; page emitters+loaders lifted to
`common`; `(sheet_instances)` preserved verbatim until T4), **ADR-038** (full
sch-root graphic-shape set modeled in T3a alongside `saveLine`), **ADR-039**
(bus-to-bus entries not distinguished from bus lines â€” matches KiCad's own
round-trip behavior), **ADR-041** (`inject_symbol`'s added `lib_nickname`
parameter and synthesis defaults, accepted with two operator amendments: (1)
lib-table-driven nickname resolution promoted to the iter-11 backlog for both
`inject_symbol` and `inject_footprint`; (2) until then, a bare incoming name
with no `lib_nickname` is qualified with the placeholder `eedesigncore`
rather than left bare â€” implemented in this release). **ADR-040** (single-file
`.kicad_sch` load semantics; hierarchy helper deferred) required no operator
review â€” pre-decided in the iteration-10 plan.

### Tests

- 986 tests passing (up from 875), 93.26% coverage (gate >=80%)
- External corpus: mod/sym 6,292/6,292 byte-identical (no regression â€” direct
  proof that the new `SaveContext.SCHEMATIC` branch leaves the LIBRARY path
  byte-inert); boards 37/37 byte-identical (grown from 21, no regression);
  schematics 220/220 byte-identical at target version `20260306` (285
  pre-target-version files version-skipped, not failures)
- Milestones met: **M1** (parse-only) â€” all 505 scanned schematic files load
  without a hard parse error; **M2** (write-back) â€” all 220 target-version
  schematics round-trip byte-identically
- `ruff check`/`ruff format --check`/`mypy --strict` all clean throughout â€”
  no pre-release formatting drift this iteration (closing the iter-9 gap)
- Security review: LOW-only (0 Critical/High/Medium); 2 accepted-open carry-
  overs (GAP-029 unbounded list growth, GAP-032 `from_file` path validation)
  unchanged by this iteration, plus 1 net-new LOW (`Image.data` lacks the
  base64/size validator its `EmbeddedFile.data` sibling has â€” staged to
  backlog-suggestions for iter-11 hardening, non-blocking)

## [0.7.0] - 2026-07-17

### Breaking

- **`Fp*` deprecated aliases removed** â€” `FpArc`, `FpCircle`, `FpCurve`, `FpLine`,
  `FpPoly`, `FpRect`, `FpText`, `FpTextBox`, `FpDrawItem`, and `Group` no longer
  resolve from `eedesigncore.mod`; import the `Pcb*` names instead
  (`FpLine`->`PcbLine`, ..., `Group`->`PcbGroup`) from `eedesigncore.mod` or
  `common.pcb_family.shapes`. This is the scheduled one-release removal per
  ADR-020 point 2 (ADR-036).
- **`common/pcb_models.py` deleted** â€” the deprecated re-export shim introduced in
  v0.6.0 is removed entirely; import shared PCB-family models from
  `common.pcb_family.{shapes,board_items,footprint_scoped}` (ADR-036). Note:
  `sym/enums.py` is a *different*, unscheduled shim and is **not** removed this
  release (ADR-024/ADR-036 set no removal schedule for it).

### Added

- **Full `.kicad_pcb` board format support** â€” new `pcb/` package (`models`,
  `loader`, `writer`, `operations`) implementing the complete v10 board grammar
  targeting file version `20260206`:
  - Board root: `(kicad_pcb (version)(generator)(generator_version))`, `general`,
    `paper`/`title_block` (now shared via `common`, see below), `(layers)`
    dual-pass copper+tech/user table, `(setup)` (stackup, via-finishing defaults,
    `zone_defaults`, origins, `pcbplotparams`), board-root `(property)`,
    `(variants)` (R1/R2)
  - Nets-by-name: the writer never emits a net table or numeric net codes;
    the loader synthesizes the net-name set from item references and tolerates
    legacy `(net code name)` / `(net_class)` / leading-numeric-code input without
    re-emitting it (R3, ADR-028)
  - Tracks/arcs/vias (R4): full via padstack support including backdrill,
    tertiary_drill, `front_post_machining`/`back_post_machining`,
    `zone_layer_connections`, teardrops, and free vias
  - Board drawings stream reusing `common/pcb_family` shapes/text/dimension/
    table/image/barcode/point/zone/group with `gr_` prefix emission
    (prefix-parameterized shared writers, ADR-029)
  - Generators (`(generated ...)`) as a discriminated-union property bag
    (number/bool/point/pts/string) and targets (`(target ...)`) as a separate
    stream (R5, ADR-030)
  - Board-context footprints via `SaveContext.BOARD` â€” footprint name/uuid/at/
    path/sheetname/sheetfile, pad net/pinfunction/pintype, zone net, all emitted
    per the CTL-delta spec while the LIBRARY (`.kicad_mod`) branch stays
    byte-inert (R7, ADR-031)
  - Board-level embedded-files consolidation on write, deferral (checksum-only
    stub) for embedded footprints, and hydrate-on-load `FixupEmbeddedData`
    analog (R8)
- **`extract_footprint` / `inject_footprint`** (`pcb/operations.py`) â€” construct a
  LIBRARY-clean `Footprint` from a board footprint (uuids stripped group-aware
  per ADR-019) and inject a footprint into a board (uuids minted, nets resolved
  via a name-keyed `net_map`, embedded data deferred to the board root); optional
  `lib_nickname` to qualify a bare `fp_id` (R9, ADR-035)
- **Arcs in `pts` chains** â€” `(pts ...)` now accepts an `(arc (start)(mid)(end))`
  element interleaved with plain `(xy ...)` points, modeled as a
  `tuple[float, float] | PtsArc` union (`common/models.py`), applied to polygons,
  textboxes, curves, dimensions, zone fill/outline polygons, pad primitives, and
  generator `pts` values (ADR-032)
- **Pad `gr_vector`/`gr_bbox` custom-primitive proxies** (GAP-010) â€” modeled as
  `PrimVector`/`PrimBbox` on `PadPrimitive` with byte-identical round-trip
- **`scripts/corpus_test.py` `.kicad_pcb` support** â€” real-board corpus checking
  with a migration-bucket split separating pre-target-version demos/templates
  from the graded `20260206` set (R11)
- **`Paper` / `TitleBlock` promoted to `common/`** â€” shared between `.kicad_pcb`
  board root and existing formats

### Fixed

- Pad `(zone_layer_connections ...)` now emitted in `SaveContext.BOARD` for
  PTH pads with `remove_unused_layers` set, matching `format(const PAD*)`
  (previously deferred out of T7's CTL-delta scope; ADR-031 resolution)
- **GAP-013** â€” zone `(thermal_gap)`/`(thermal_bridge_width)` are now suppressed
  for teardrop-area zones (`teardrop_type is not None`), matching KiCad's
  `IsTeardropArea()` fill-emission guard (ADR-033)
- Zone filled-polygon `(island)` now emits `(island yes)` instead of the bare
  keyword, matching `FormatBool(..., "island", true)` (found alongside the
  ADR-032 arc fix)
- `(setup)` `zone_defaults` and related board-header blocks corrected during the
  M2 gap-closure wave to reach 21/21 byte-identical corpus boards (ADR-026,
  ADR-034)
- **GAP-031** â€” `scripts/corpus_test.py` no longer has a machine-local default
  corpus root; `--root` or `$KICAD_FILEIO_CORPUS` is now required, with a clear
  error otherwise

### Architecture

Eight ADRs from iteration 9 remain `Status: Flagged (pending review)` and are
carried forward for operator disposition (see `context/decisions.md` for full
context): **ADR-026** (board header blocks emitted only when present, not
unconditionally), **ADR-029** (prefix-parameterized shared drawing writers stay
in `mod/writer.py`; zone net emits v10 name-token form), **ADR-030** (generator
property bag as a discriminated union; targets modeled as a separate stream),
**ADR-032** (arcs in `pts` chains as a `tuple | PtsArc` union), **ADR-033** (M2
commit-4 root cause was GAP-013, not `island_removal_mode`), **ADR-034** (board
drawings after `embedded_fonts` preserved via a new `Board.trailing_drawings`
list), **ADR-035** (extract/inject live in `pcb/operations.py`; `net_map` is
nets-by-name; `lib_nickname` is additive), **ADR-036** (v0.7.0 removes `Fp*`/
`Group`/`pcb_models` per schedule; `sym/enums.py` shim kept, unscheduled).
ADR-027 and ADR-031 were both functionally resolved during the iteration;
ADR-027's status label has been corrected from a stale "Flagged" to "RESOLVED"
to match its already-resolved body text (this release).

### Tests

- 875 tests passing (up from 714), 93.35% coverage (gate >=80%)
- External corpus: mod/sym 6,292/6,292 byte-identical (no regression from the
  new board-writer/`SaveContext.BOARD` code path, proving LIBRARY-context
  inertness); boards 21/21 byte-identical at target version `20260206`
  (193 pre-target-version files version-skipped, not failures)
- Milestones met: **M1** (parse-only) â€” all 214 scanned board files load
  without a hard parse error; **M2** (write-back) â€” all 21 target-version
  boards round-trip byte-identically
- `ruff format` cosmetic drift (10 files, flagged by QA) resolved in a
  dedicated pre-release commit; `ruff check` and `mypy --strict` clean

## [0.6.0] - 2026-07-13

### Breaking

- **`Fp*` -> `Pcb*` rename (10 names)** â€” the board-shared shape/text/group payloads
  misfiled in `mod/models.py` are renamed and relocated to `common/pcb_family/shapes.py`:
  `FpLine`->`PcbLine`, `FpRect`->`PcbRect`, `FpCircle`->`PcbCircle`, `FpArc`->`PcbArc`,
  `FpPoly`->`PcbPoly`, `FpCurve`->`PcbCurve`, `FpText`->`PcbText`, `FpTextBox`->`PcbTextBox`,
  `Group`->`PcbGroup`, plus their shared base. **Deprecated aliases retained in
  `mod/__init__` until v0.7.0** â€” the old `Fp*` names still resolve (identity-equal) to
  the new classes for one minor release (ADR-020 point 2).
- **Footprint `dimension`/`table`/`image`/`barcode` lists replaced by the ordered
  `drawings` stream** â€” these four settable list fields are removed from `Footprint`;
  the single `drawings` collection is now the source of truth for correct interleaved
  emission order (GAP-001). Four **read-only** compat `@property` views
  (`tables`/`images`/`barcodes`/`dimensions`, filtered from `drawings`) are kept for
  existing read accesses; there is no setter â€” callers must append to `drawings` to
  control ordering (ADR-025).
- **`TableCell` grammar: `(at)/(size)` -> `(start)/(end)`** â€” cells now route through
  the `PCB_TEXTBOX` emitter; `(size)` no longer exists, `(locked)` is emitted immediately
  after content (GAP-005).
- **`SymbolLib.embedded_fonts` / `SymbolLib.embedded_files` removed** â€” dead fields;
  `.kicad_sym` has no lib-level embedded block (GAP-027).
- **`common/pcb_models.py` is now a deprecated re-export shim** â€” all shared PCB-family
  models moved to `common/pcb_family/`; the old flat module still resolves imports for
  one minor release, per ADR-020.

### Added

- **`common/pcb_family/` + `common/sch_family/` tiered packages** â€” shared-tier
  migration (ADR-020) splitting the flat `common/pcb_models.py` into board-root /
  footprint-scoped / shared-child modules for PCB, and a new `sch_family/` housing the
  `.kicad_sym` `Symbol` sub-grammar (shapes renamed `Sch*`) for reuse by the future
  `.kicad_sch` domain
- **Public API surfaces** â€” populated `sym/__init__.py` and the top-level
  `eedesigncore/__init__.py`; `dump_sym` promoted to public
- **`SaveContext` enum scaffold** (`common/context.py`: LIBRARY/BOARD/SCHEMATIC,
  default LIBRARY preserves byte-identity) â€” no parent-document IO yet; activation
  deferred to iteration 9 (ADR-019 point 2)
- **`strip_uuids()` / `generate_uuids()`** (`common/uuid_ops.py`) â€” group-aware pure
  model transforms; the mandatory group-aware guard collects the protected uuid set
  from every `(group (members ...))` list plus the owning group's own uuid before any
  removal, preventing silent group deletion on strip (ADR-019)
- **`EmbeddedFile.from_bytes(name, data, kind=...)`** (`common/models.py`) â€”
  zstd -> base64 -> MurmurHash3 (seed `0xABBA2345` over decompressed bytes) construction
  path, checksum verified byte-for-byte against a real corpus embedded file; pure
  round-trip preserves the original checksum text unchanged (GAP-014)
- **`fp-lib-table` / `sym-lib-table` loader/writer** â€” new `tables/` package,
  `LIBRARY_TABLE` prettify mode, byte-identical round-trip on real fixtures (R8)
- **Multi-layer shape support** â€” `layers: list[str] | None` added to all six
  `Pcb*` shape models; loader/writer round-trip `(layers ...)` alongside single
  `(layer ...)`; `PcbCurve` gains `solder_mask_margin` and `net_name` for parity with
  its siblings (ADR-023)
- **Board-context footprint `uuid` / `(at)` fidelity** â€” `Footprint.uuid` and
  `Footprint.at` (`FootprintPlacement`) modeled and stored on load instead of discarded,
  so a footprint round-tripped from a `.kicad_pcb` keeps its identity and placement
  (GAP-033)

### Fixed

- **GAP-001** â€” `dimension`/`table`/`image`/`barcode` now emit inside the single
  sorted GraphicalItems `drawings` stream, in correct interleaved position before
  pads/zones/groups, instead of trailing after `(model)`
- **GAP-002** â€” reference-image `(data ...)` now emits quoted <=76-char base64 chunks
  matching KiCad's `kicad_io_utils.cpp` chunking (subsumes GAP-030 pipe-safety)
- **GAP-003** â€” non-field `fp_text` no longer writes a spurious `(hide yes)` guard
- **GAP-005** â€” `table_cell` now emits `(start)/(end)` (no `(size)`), `(locked)`
  immediately after content
- **GAP-006** â€” zone per-layer `(property (layer "...")(hatch_position (xy ...)))`
  correctly modeled; non-emitting `filled_areas_thickness` dropped; `hatch_position`
  now parsed instead of silently discarded
- **GAP-008** â€” corrected the post_machining angle comment (disk value is
  internal-units/10; the Ã·10 loader / Ã—10 writer pair was already an exact inverse,
  no behavior change)
- **GAP-011** â€” `fmt_double` small-value precision fix: `{:.16f}`-stripped for
  `0 < |v| <= 0.0001` matching `FormatDouble2Str`, applied to roundrect_rratio,
  chamfer_ratio, solder_paste_margin_ratio, teardrop ratios, model offset/scale/rotate,
  zone hatch fields, and post_machining `(angle)`; `fmt_iu` retains its own `{:.10f}`
  small-value band per `FormatInternalUnits` (ADR-022); `fmt_angle` is pure `{:.10g}`
  and is not an alias of `fmt_iu`
- **GAP-027** â€” removed dead `SymbolLib.embedded_fonts`/`embedded_files` fields
- **GAP-028** â€” zone bare-symbol writer fields (`source_type`, `smoothing`,
  `hatch_border_algorithm`, `hatch_style`, `connect_pads_mode`, `teardrop_type`) are
  now `Literal`-constrained, closing the unquoted bare-symbol injection vector
- **GAP-033** â€” see Added; footprint board-context `uuid`/`(at)` no longer discarded
- **effects-internal `(hide yes)`** â€” removed the dead write-side branch from the
  unified `dump_effects` (KiCad's `EDA_TEXT::Format` never emits `hide` inside
  `(effects ...)` for either family); the corresponding loader tolerance for legacy
  files is retained unchanged (ADR-021)

### Dependencies

- `zstandard==0.25.0` â€” new, pinned exact version, in both `[project].dependencies`
  and the `dev` extra; backs `EmbeddedFile.from_bytes` (GAP-014)

### Architecture

- ADR-019: UUID lifecycle policy â€” caller-controlled, group-aware, strip-by-default
  for library saves (library-save MODE deferred to iteration 9; explicit
  `strip_uuids`/`generate_uuids` API lands now)
- ADR-020: Shared-tier module layout â€” `common/pcb_family/` + `common/sch_family/`
  packages, as-built (includes the ADR-022 `fmt_iu`/`fmt_double` correction and the
  ADR-024 pin-enum relocation)
- ADR-016 amended: GAP-007 reconciled â€” retracted the defensive non-emit-field clause;
  those fields do not exist on `Padstack`/`PadstackLayer`/`Pad` (KiCad never emits them
  for a footprint pad â€” VIA-only); the ADR-017 hard-error guard is the actual
  forward-compat mechanism
- ADR-021 (Flagged): `dump_effects` drops effects-internal `(hide yes)`; loader
  tolerance retained â€” pending operator review
- ADR-022 (Flagged): `fmt_iu` keeps the `{:.10f}` small-value branch, corrects the
  arch-plan's "pure `{:.10g}`" wording â€” pending operator review
- ADR-023 (Flagged): multi-layer shapes modeled (not hard-error); `PcbCurve` gains
  `net_name` for loop parity â€” pending operator review
- ADR-024 (Flagged): pin enums moved to `common/sch_family`; `sym/enums.py` becomes a
  re-export shim â€” pending operator review
- ADR-025 (Flagged): GAP-001 merges the four trailing lists into `drawings`; read-only
  compat views kept â€” pending operator review

### Tests

- 714 tests passing (up from 562), 93.09% coverage (gate >=80%)
- External corpus: 6,292/6,292 files byte-identical (2,914 `.kicad_mod` +
  3,378 `.kicad_sym`); 0 parse errors, 0 byte mismatches
- Migration bisectability verified: S1-S6 each its own commit; S2 (`fmt_iu`/
  `fmt_double` precision split) isolated as a single bisectable commit per R2

## [0.5.0] - 2026-06-05

### Added

- **`KicadFileMixin`** (`common/io_mixin.py`) â€” centralized `from_file`, `from_string`,
  `to_string`, and `to_file` as a single Pydantic-compatible base class; `Footprint` and
  `SymbolLib` inherit it; no duplicated classmethods (ADR-014)
- **EOL preserve mode** â€” `to_file(path, line_ending="preserve"|"native"|"lf"|"crlf")`;
  default `"preserve"` re-applies the source file's original line ending on write
  (byte-identical on any host OS); `from_file` sniffs raw bytes and records `"lf"` or
  `"crlf"` as a Pydantic `PrivateAttr`; `from_string` leaves it `None`; preserve with no
  recorded EOL falls back to a fixed `"crlf"` (ADR-014, supersedes ADR-009)
- **Hard-error contract for unknown tokens** â€” every `if/elif` token-dispatch loop in
  `mod/loader.py`, `sym/loader.py`, and `common/loaders.py` now ends with
  `else: raise KicadParseError`; `_load_drawing` and `_load_pad_primitive` raise instead
  of returning `None`; 56 + 20 raise sites enforced (ADR-017)
- **Footprint root gap coverage** â€” `zone` (footprint-embedded copper zone, including
  keepout/fill/hatch/placement source-type fix for `sheetname`/`component_class`/`group`),
  `table` (`PCB_TABLE` with columns, rows, and cells), `image` (`pcb_reference_image`),
  `barcode`, `dimension` (all five subtypes: `aligned`, `orthogonal`, `leader`, `center`,
  `radial`), `point`, `variant`, `component_classes`, `unit_info`, `stackup`,
  `jumper_pad_groups` â€” all parsed as typed Pydantic models and round-tripped (ADR-015)
- **Complete padstack model** â€” mode + per-layer geometry (`PadstackLayer` with
  shape/size/offset/rratio/chamfer/primitives), `backdrill`, `tertiary_drill`,
  `front_post_machining` / `back_post_machining` (COUNTERBORE/COUNTERSINK), outer-layer
  flags; `remove_unused_layers` and `keep_end_layers` kept as two-bool form matching
  KiCad's serializer exactly (ADR-016)
- **Shared PCB-family models in `common/pcb_models.py`** â€” `Zone`, `Dimension`, `Table`,
  `ReferenceImage`, `Barcode`, `Variant`, `ComponentClasses`, `FootprintUnitInfo`,
  `Stackup`, `PcbPoint`, `DrillProps`, `PostMachining`, `PadstackLayer`, `Padstack` â€”
  designed for reuse by future `pcb/` and `sch/` format modules (ADR-015)
- **Symbol shape coverage** â€” arc, circle, rect, bezier, polyline, text, textbox with
  uuid handling and private-body-style fields; power symbol and body-style (DeMorgan)
  attributes; `load_sym` raises on unknown tokens
- **Process-pooled corpus round-trip harness** (`scripts/corpus_test.py`) â€”
  `max(1, cpu_count - 2)` workers, strict byte-identical comparison via `to_file("preserve")`,
  per-file failure reporting, token-coverage histogram; 6,286 / 6,286 files pass
- **Token-coverage deferral registry** (`context/corpus-deferral-registry.md`) â€” modeled
  tokens not exercised by the corpus are listed for iteration 7 follow-up (ADR-018)
- **Grammar reference artifact** (`context/research/kicad-footprint-symbol-grammar.md`) â€”
  full token enumeration with nesting, types, emission order, conditional-write rules, and
  shared-vs-specific classification for both `.kicad_mod` and `.kicad_sym`
- **`ReferenceImage.data` size cap** â€” `@field_validator` enforces a 50 MB limit,
  mirroring the `EmbeddedFile` precedent

### Fixed

- **`Footprint.from_string` on embedded-data files** â€” previously raised
  `AttributeError: 'str' has no attribute 'node_type'` on footprints containing
  `(embedded_files ...)` blocks; unified `from_string` and `from_file` parse paths
  via `KicadFileMixin._from_text` (N1 fix)
- **`fp_filters` (`ki_fp_filters`) round-trip** â€” `load_mod` now correctly extracts
  `ki_fp_filters` from the properties list and populates `Footprint.fp_filters`;
  previously the field was never populated, silently dropping filter data on round-trip
- **`front_post_machining` / `back_post_machining` not emitted (BUG-001)** â€” the pad
  writer stub incorrectly omitted these fields; wired `_dump_post_machining` into the
  pad emitter at the correct position; writer and loader angle scaling (Ã·10 / Ã—10) are
  now symmetric
- **Zone placement source-type fields** â€” `sheetname`, `component_class`, and `group`
  source-type tokens now parsed and emitted correctly

### Architecture

- ADR-014: EOL preserve mode â€” `to_file` default is `"preserve"`; supersedes ADR-009
- ADR-015: Shared-vs-specific classification â€” shared PCB-family models live in `common/`
- ADR-016: Padstack design â€” two-bool `remove_unused_layers` / `keep_end_layers` form;
  defensive non-emit for fields absent from KiCad 10 serializer
- ADR-017: Hard-error contract â€” every token loop ends with `else: raise KicadParseError`
- ADR-018: Coverage increment shipped as v0.5.0; full grammar completeness audit deferred
  to iteration 7
- ADR-BUG-001: `front/back_post_machining` emitter bug resolved

### Tests

- 272 new tests; 562 total, 92.03% coverage
- New test modules: `test_io_mixin` (23), `test_hard_error` (15), `test_mod_from_string` (3),
  `test_mod_padstack` (27), `test_mod_misc_items` (46), `test_mod_zone` (24),
  `test_mod_dimension`, `test_mod_drawing_items`, `test_sym_shape_guards`,
  `test_common_loaders`
- External corpus: 6,286 / 6,286 files byte-identical (2,912 `.kicad_mod` +
  3,374 `.kicad_sym`); 0 parse errors, 0 byte mismatches

## [0.4.0] - 2026-06-03

### Added
- Embedded file (`|...|` pipe-delimited) data now parsed and round-tripped correctly
  for both `.kicad_sym` and `.kicad_mod` files â€” previously `EmbeddedFile.data` was
  always empty, causing embedded 3D models and fonts to be silently dropped
- `common/writers.py` â€” new module with shared `dump_embedded_files()` for all formats
- `load_embedded_file()` and `load_embedded_files()` consolidated in `common/loaders.py`
  â€” future formats (pcb, sch, wks) get embedded file support for free (ADR-012)
- Real fixture with embedded STEP data (`CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod`)
  verified byte-identical round-trip

### Fixed
- `-0` (KiCad serialization of `-0.0` via `%.10g`) now round-trips correctly as `-0`
  rather than being silently converted to `0`

### Tests
- 13 new tests; 290 total, 88.42% coverage

## [0.3.1] - 2026-06-03

### Fixed
- `\r` escape sequence in S-expression string values now correctly parses to CR (0x0D)
  rather than the letter `r` â€” eliminates data loss on round-trip for any string
  containing `\r` or `\r\n` sequences (e.g. multi-line Description fields)

### Verified
- Confirmed against KiCad 10 source (`richio.cpp::Quotes()`, `sexpr_parser.cpp`) that
  the parser/writer pair now handles all four escape sequences KiCad uses:
  `\n`, `\r`, `\\`, `\"` (ADR-011)

### Tests
- 5 new tests (3 unit `TestCREscapeSequence` + 2 integration round-trips for real-world
  fixtures); 277 total, 86.08% coverage
- Added real-world regression fixtures: `V7223T-049.152M.kicad_sym` and `474811-E.kicad_sym`

## [0.3.0] - 2026-06-03

### Fixed
- `to_file()` now writes platform-native line endings (CRLF on Windows, LF on Linux/macOS),
  matching KiCad's `"wt"` file mode â€” eliminates EOL diffs when comparing files written by
  KiCad vs. this library (ADR-009)
- `\n` and `\r` escape sequences in string values now correctly round-trip â€” previously literal
  newlines were written into output files instead of `\n` escape sequences
- `(face "FontName")` token inside font blocks now parsed and emitted â€” previously silently
  dropped, causing diffs on footprints using non-default fonts
- `_esc()` in sym writer now escapes `\n` and `\r` (was missing, only present in sexpr writer)

### Added
- `render_cache` support on `FpText`, `FpTextBox`, and `Property` in `.kicad_mod` files â€”
  pre-rendered outline-font polygon caches are now preserved on round-trip (ADR-010)
- `RenderCache` and `RenderCachePolygon` models in `common/models.py`
- Real KiCad CLI-converted fixture (`TEST_POINT_MINI_BLK_render_cache.kicad_mod`) sourced
  from Altium import for render-cache round-trip testing
- 22 new tests; 272 total, 84.29% coverage

## [0.2.0] - 2026-05-21

### Added
- `.kicad_mod` footprint domain models (`mod/models`) covering all KiCad 10.0.x token groups including pads, drawing items, 3D models, teardrops, and pad primitives
- Footprint loader (`mod/loader`) â€” `Footprint.from_file()` and `Footprint.from_string()`
- Footprint writer (`mod/writer`) â€” `Footprint.to_string()` and `Footprint.to_file()` with byte-identical round-trip
- Shared loader helpers refactored to `common/loaders.py` â€” reused by both sym and mod loaders
- `PadType`, `PadShape`, `PadProperty` enums verified against KiCad 10.0.x source
- 135 new tests (130 unit + 5 byte-identical round-trip integration); 250 total, 83.76% coverage
- `Literal` type constraints on `custom_shape_clearance` and `custom_anchor` preventing S-expression injection
- Proper union types (`list[FpDrawItem]`, `list[PadPrimitive]`) replacing `list[Any]`

### Fixed
- Source-verified unconditional tokens: `(fill no)`, `(embedded_fonts no)`, `(duplicate_pad_numbers_are_jumpers no)`, `(remove_unused_layers no)` on THT pads â€” all always written matching KiCad behaviour
- `fp_text` angle always written even when zero: `(at 0 0 0)`

### Architecture
- ADR-008: pts-block normalisation â€” generator-script files use one `(xy ...)` per line; library writer produces KiCad-canonical single-line form. Round-trip tests normalise pts blocks for non-pcbnew fixtures.

## [0.1.0] - 2026-05-20

### Added
- Generic S-expression parser (`sexpr/parser`) with `SExprNode` tree and lossless float `raw` preservation
- Prettify writer (`sexpr/writer`) - faithful port of KiCad's `KICAD_FORMAT::Prettify` for byte-identical output
- Common Pydantic v2 models (`common/`) - `Color`, `Stroke`, `Fill`, `TextAttributes`, `Property`, `EmbeddedFile`
- `.kicad_sym` symbol library domain models (`sym/models`) covering all KiCad 10.0.x token groups
- Symbol library loader (`sym/loader`) - `SymbolLib.from_file()` and `SymbolLib.from_string()`
- Symbol library writer (`sym/writer`) - `SymbolLib.to_string()` and `SymbolLib.to_file()` with byte-identical round-trip
- 115 tests (104 unit + 5 byte-identical round-trip integration + 6 security tests); 80.11% coverage
- Parser recursion depth limit (100 levels) with `KicadParseError` on exceeded depth
- `EmbeddedFile.data` base64 format and size (50 MB) validation

### Architecture
- Two-phase write pipeline: domain writer emits flat tokens -> Prettify post-pass (ADR-001)
- Byte-identical round-trip fidelity as the project cornerstone (ADR-004)
- Float serialisation uses `{:.10g}` matching KiCad's `fmt::format` (ADR-007)
- `.kicad_sym` uses `FORMAT_MODE::NORMAL` prettify, not COMPACT (ADR-005)
