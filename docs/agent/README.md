# Library reference guide

Read this file, `docs/agent/common.md`, and the guide for your file type.

## Invariants

| Rule | Detail |
|---|---|
| **Round trips preserve bytes** for a file that KiCad wrote, one file at a time. A file that another tool wrote comes back in KiCad layout. | `docs/agent/common.md § Three write paths` |
| **Write with `to_file`.** It reproduces the line endings of the source file. `to_string()` plus a manual `open().write()` rewrites every line of a CRLF file on Windows. | `docs/agent/common.md § Three write paths` |
| **The writer sorts items the way KiCad sorts them at save.** A changed model writes the order KiCad would write, so the next KiCad save changes nothing. Ties keep file order. Custom rule files keep source order, because rule order sets precedence. | `docs/agent/symbols.md § Draw item order`; `docs/agent/footprints.md § Pad order`; `docs/custom-design-rules.md` |
| **Known exception: schematic library symbol properties.** KiCad keeps the stored property order of a symbol embedded in a schematic. This library puts the five standard properties first, so an embedded symbol that lists a custom property first does not round-trip byte for byte. | `docs/agent/schematics.md § lib_symbols naming` |
| **Load and save preserve UUIDs.** An item built without a uuid writes no `(uuid ...)` node. KiCad would write one. Leaving it out keeps new random values out of a diff. Creation and injection helpers can make identifiers. Use `generate_uuids` for existing items. | `docs/operations.md § strip_uuids and generate_uuids` |
| **Unknown tokens raise `KicadParseError`.** The loader does not skip or copy a token it does not model, because a skipped token would disappear on the next save with no warning. An error on a file from a newer KiCad means a token the library does not model yet, not a corrupt file. | `docs/agent/common.md § Exceptions` |
| **Geometry uses millimeters.** Custom rule bounds retain expressions and unit suffixes as text. Symbol files negate Y. | `docs/agent/common.md § Coordinates and units`; `docs/custom-design-rules.md` |
| **`KicadParseError` is the error to catch.** It subclasses `ValueError`. | `docs/agent/common.md § Exceptions` |
| **Field order is computed, not stored.** Mandatory fields lead, in a fixed order. User fields keep file position. Editing a `value` never reorders anything. | `docs/agent/common.md § Properties and field order` |
| **Renumbering a pin or a pad DOES reorder the file.** The order key of both starts at the number. Change a number and the item moves in the output. | `docs/agent/symbols.md § Draw item order` |

## Primary API

Root methods:

| Call | Result |
|---|---|
| `Model.from_file(path)` | Read from disk. Records the source line endings. |
| `Model.from_string(text)` | Parse text held in memory. |
| `model.to_string()` | The whole file as text, line-feed endings. |
| `model.to_file(path)` | Write to disk. Preserves the source line endings. |

| Root model | File | Spoke |
|---|---|---|
| `SymbolLib` | `.kicad_sym` | `docs/agent/symbols.md` |
| `Footprint` | `.kicad_mod` | `docs/agent/footprints.md` |
| `Board` | `.kicad_pcb` | `docs/agent/boards.md` |
| `Schematic` | `.kicad_sch` | `docs/agent/schematics.md` |
| `ProjectFile` | `.kicad_pro` | `docs/agent/projects.md` |
| `ProjectLocalSettings` | `.kicad_prl` | `docs/agent/projects.md` |
| `LibTable` | `fp-lib-table`, `sym-lib-table` | `docs/agent/common.md` |
| `DrcFile` | `.kicad_dru` | `docs/custom-design-rules.md` |

```python
from eedesigncore import Footprint

fp = Footprint.from_file("C_0402_1005Metric.kicad_mod")
fp.properties[0].value = "C99"
fp.to_file("C_0402_1005Metric.kicad_mod")
```

## The six operations

Import all six from the package root. The model is positional, every other
argument is keyword-only, and none of the six touches the disk.

| Operation | Result |
|---|---|
| `extract_footprint(board, *, reference=...)` | A detached, library-clean `Footprint`. |
| `inject_footprint(board, footprint, *, at=...)` | Places a copy on the board and returns it. |
| `extract_symbol(sch, *, lib_id=...)` | A `lib_symbols` entry as a library `Symbol`. |
| `inject_symbol(sch, symbol, *, at=...)` | Places a copy, auto-annotates the reference, returns it. |
| `strip_uuids(model)` | Clears uuid values in place, group-aware. Returns `None`. |
| `generate_uuids(model)` | Mints uuid version 4 values in place. Returns `None`. |

Full signatures, nickname resolution, and the collision rule live in
`docs/operations.md § One calling contract`.

## Support boundaries

The package-root `__all__` defines the supported API and includes all types reachable through public fields and returns.
`tests/unit/test_export_surface.py` verifies that closure.
Sub-package exports can be wider and do not expand the support promise.
Import from `eedesigncore`. The package ships `py.typed`; verify exact signatures in source.

Removing or renaming a supported name is breaking. Additions are permanent.
Internal base classes and text-tree internals remain private, except `parse`, `SExprNode`, and `SExprNodeType`.
A type alias is supported when a public field or return names it.

## Every public name

Import only the root names you need. Generic names can collide with caller names.

| Category | Names |
|---|---|
| Drawing sheets | `DrawingSheet`, `DrawingSheetCoordinate`, `DrawingSheetSetup`, `DrawingSheetItem`, `DrawingSheetGraphics`, `load_wks`, `dump_wks`, `write_wks`, `drawing_sheet_graphics`, `render_drawing_sheet_svg` |
| Root models | `Footprint`, `SymbolLib`, `Board`, `Schematic`, `ProjectFile`, `ProjectLocalSettings`, `LibTable` |
| Operations | `extract_footprint`, `inject_footprint`, `extract_symbol`, `inject_symbol`, `strip_uuids`, `generate_uuids`, `derive_uuids`, `DERIVED_UUID_NAMESPACE`, `inject_board`, `InjectedContent`, `new_board_from`, `rename_nets`, `rename_footprint_nets`, `extract_fills`, `replace_fills`, `flatten_fills`, `item_layers`, `items_on_layers`, `translate_board`, `translate_footprint`, `translate_items`, `rotate_board`, `rotate_footprint`, `rotate_items`, `set_footprint_orientation`, `flip_footprint`, `flip_board`, `board_bbox`, `footprint_bbox`, `items_bbox` |
| Loaders and writers | `load_mod`, `dump_mod`, `write_mod`, `load_sym`, `dump_sym`, `write_sym`, `load_pcb`, `dump_board`, `write_board`, `load_sch`, `dump_sch`, `write_sch`, `load_lib_table`, `dump_lib_table`, `write_lib_table`, `parse_json`, `load_pro`, `load_prl`, `dump_json` |
| S-expression tree | `parse`, `SExprNode`, `SExprNodeType` |
| Version | `__version__` |
| Hierarchy | `load_hierarchy`, `SheetHierarchy`, `SheetNode` |
| Saved net classes | `read_netclass_assignments` |
| Custom design rules | `DrcFile`, `DrcRule`, `DrcClause`, `DrcConstraint`, `DrcConstraintType`, `DrcBound`, `update_drc_block` |
| Library tables | `LibTableRow`, `LibTableKind`, `LibTableScope`, `LibTableEnvironment`, `LibTableResolver`, `ResolvedLibTable`, `ResolvedLibrary`, `SkippedLibTable`, `resolve_lib_table`, `qualify_item_name`, `default_user_settings_dir`, `default_kicad_install_root`, `PLACEHOLDER_NICKNAME` |
| Project JSON | `JsonTree`, `JsonInt`, `JsonFloat`, `JsonValue`, `MetaBlock` |
| Footprint filters | `split_fp_filters`, `join_fp_filters`, `semantic_fp_filters`, `unescape_string` |
| Shared parts | `BoundingBox`, `Stroke`, `Fill`, `TextAttributes`, `Color`, `Property`, `EmbeddedFile`, `Paper`, `TitleBlock`, `PtsArc`, `PtsElement`, `RenderCache`, `RenderCachePolygon` |
| Shared enums | `StrokeType`, `FillType`, `JustifyH`, `JustifyV`, `PowerType`, `PinElectricalType`, `PinShape`, `PadType`, `PadShape`, `PadProperty`, `ChamferPosition`, `CustomAnchor`, `CustomShapeClearance`, `PadstackMode`, `PostMachiningMode`, `BarcodeKind`, `BarcodeEcc`, `DimensionType`, `ArrowDirection`, `LineType`, `LabelKind`, `FrontBack` |
| Board and footprint graphics | `PcbPoint`, `PcbLine`, `PcbRect`, `PcbCircle`, `PcbArc`, `PcbPoly`, `PcbCurve`, `PcbText`, `PcbTextBox`, `PcbGroup`, `PcbDrawItem` |
| Board and footprint items | `Zone`, `ZoneKeepout`, `ZonePlacement`, `ZoneFill`, `ZoneLayerProperty`, `ZoneFilledPolygon`, `ZoneOutlinePolygon`, `Dimension`, `DimensionFormat`, `DimensionStyle`, `DimensionText`, `Table`, `TableCell`, `TableBorder`, `TableSeparators`, `Barcode`, `ReferenceImage` |
| Footprint members | `FootprintAttrs`, `FootprintPlacement`, `Model3D`, `Pad`, `DrillInfo`, `TeardropParams`, `Padstack`, `PadstackLayer`, `BackdrillInfo`, `PostMachining`, `PrimLine`, `PrimRect`, `PrimArc`, `PrimCircle`, `PrimCurve`, `PrimPoly`, `PrimVector`, `PrimBbox`, `PadPrimitive`, `Variant`, `VariantField`, `ComponentClasses`, `FootprintGateUnit`, `FootprintUnitInfo`, `Stackup`, `StackupLayer`, `JumperPadGroups` |
| Board members | `BoardGeneral`, `BoardLayer`, `LayerTypeToken`, `BoardStackup`, `BoardStackupLayer`, `BoardStackupSubLayer`, `BoardVariant`, `PlotParams`, `Setup`, `TrackSegment`, `TrackArc`, `TrackItem`, `Via`, `ViaFrontBackOpt`, `ViaPadstack`, `ViaPadstackLayer`, `PcbGenerator`, `GenNumber`, `GenBool`, `GenPoint`, `GenPts`, `GenString`, `GeneratorValue`, `PcbTarget` |
| Symbol members | `Symbol`, `SymbolUnit`, `SymbolItem`, `Pin`, `PinAlternate`, `SymText`, `SymTextBox`, `SchArc`, `SchBezier`, `SchCircle`, `SchPolyline`, `SchRect`, `GraphicShape` |
| Schematic members | `PlacedSymbol`, `PlacedSymbolPin`, `InstancePath`, `InstanceProject`, `InstanceVariant`, `InstanceVariantField`, `Junction`, `NoConnect`, `BusEntry`, `Line`, `SchText`, `Label`, `SchTextBox`, `RuleArea`, `Image`, `Group`, `SchTable`, `SchTableCell`, `Sheet`, `SheetPin`, `SheetVariant`, `SheetVariantField`, `SheetInstancePath`, `SheetInstanceProject`, `SheetInstances`, `RootInstancePath` |
| Exceptions | `KicadParseError`, `EmbeddedFileChecksumError`, `LibTableResolutionError`, `SheetHierarchyError`, `ZstdBackendUnavailableError`, `ZstdDecompressionError` |
| Part navigation | `PartRecord`, `PartMatch`, `MatchReason`, `FieldTest`, `search_records`, `library_item_names`, `iter_library_records`, `iter_table_records` |
| Part databases | `load_kicad_dbl`, `DatabaseLibrary`, `DatabaseDeclaration`, `DatabaseSubLibrary`, `DatabaseTableRead`, `DatabaseFieldMapping`, `DatabaseProperties`, `DatabaseSource`, `DatabaseCache`, `definition_from_row`, `record_from_row`, `database_records` |
| Electrical inspection, `docs/schematic-electrical-inspection.md` | `append_global_label_netclasses`, `inspect_schematic_electrical`, `compare_schematic_electrical`, `SchematicClassEvidence`, `SchematicElectricalNet`, `SchematicElectricalLabel`, `SchematicElectricalPart`, `SchematicElectricalSnapshot`, `SchematicElectricalChange`, `SchematicElectricalComparison` |
| Guarded saves | `SourceSnapshot`, `GuardedDocument`, `GuardedFileResult`, `GuardedSaveResult`, `GuardedSaveError`, `load_guarded_document`, `save_guarded_documents` |
| Save context | `SaveContext` |
| Copper-layer changes, `docs/copper-layer-changes.md` | `CopperLayerChangePlan`, `CopperLayerReference`, `apply_copper_layer_change`, `inspect_copper_layer_usage`, `plan_copper_layer_change`, `validate_copper_layer_references` |
| Schematic views, `docs/schematic-preparation.md`, `docs/assembly-schematic-view.md` | `AssemblyStack`, `AssemblyWorkspace`, `NativeAssemblyInspector`, `SchematicBus`, `SchematicCoverage`, `SchematicIssue`, `SchematicNet`, `SchematicPage`, `SchematicView`, `ViewBounds`, `ViewContact`, `ViewItem`, `ViewPinSelection`, `ViewSource`, `prepare_assembly_view`, `prepare_schematic_view`, `view_identity` |
| Font selection | `OutlineFont`, `load_font` |
| SVG rendering | `render_schematic_svg`, `SchematicStyle`, `render_symbol_svg`, `render_footprint_svg`, `SymbolFont`, `SymbolStyle`, `render_board_svg`, `ViewBox`, `LayerStyle`, `CliParityStyle`, `ElementStyle`, `StylePolicy`, `RenderItem`, `SvgShape`, `Hole`, `HOLES`, `HOLES_PLATED`, `HOLES_NON_PLATED`, `HOLE_LAYERS`, `BoardContent`, `DEFAULT_CONTENT`, `KICAD_CONTENT`, `ALL_CONTENT`, `ViaDiameter` |
| Text resolution, `docs/text-resolution.md` | `TextResolutionContext`, `TextDiagnostic`, `ResolvedText`, `ResolvedDocumentText`, `resolve_text`, `resolve_document_text`, `context_for_sheet`, `capture_text_context`, `GitTextResolver` |
| Content comparison, `docs/content-comparison.md` | `compare_content`, `content_signature`, `ContentComparison`, `ContentChange` |
| Netbom, `docs/netbom.md` | `build_netbom`, `load_netbom`, `NetbomDocument`, `NetbomRecord`, `NetbomEvidence`, `NetbomCoverage`, `NetbomSourceManifest`, `NetbomSourceFile` |
| Design inspection, `docs/design-inspection.md` | `DesignInspector`, `inspect_design`, `open_inspection`, `portable_inspection_source` |
| Netbom assemblies, `docs/netbom-assemblies.md` | `NetbomAssembly`, `AssemblyInspector`, `AssemblyEvidence`, `ConnectorEndpoint`, `ConnectorMate`, `ConnectorMateDefinition`, `ConnectorPinPair` |
| Schematic creation, `docs/schematic-creation.md`, `docs/field-autoplacement.md`, `docs/global-label-geometry.md` | `create_global_label`, `create_hierarchical_label`, `create_sheet`, `add_sheet_pin`, `create_subsheet`, `CreatedSubsheet`, `create_sheet_schematic`, `autoplace_fields`, `global_label_outline` |
| Schematic tables, `docs/schematic-tables.md` | `create_schematic_table`, `fit_schematic_table` |
| Schematic notes, `docs/schematic-notes.md` | `create_note_block`, `create_schematic_note`, `create_layout_note`, `create_todo_note`, `parse_note_block`, `parse_schematic_notes`, `update_note_block`, `NoteKind`, `SchematicNote` |
| Schematic measurement, `docs/schematic-measurements.md` | `measure_schematic`, `measure_schematic_item`, `find_schematic_overlaps`, `SchematicMeasurement`, `SchematicPartBounds`, `SchematicCollision`, `MeasuredSchematicItem` |
| Selected board updates, `docs/schematic-board-update.md` | `BoardUpdateSelection`, `BoardPadNetUpdate`, `BoardFootprintUpdate`, `SchematicBoardUpdatePlan`, `plan_schematic_board_update`, `apply_schematic_board_update` |

### Finding a part

`resolve_lib_table` answers "which library holds this exact name".
`eedesigncore.library` answers "what is in this library" and "which of these
parts fits". A symbol library may be one `.kicad_sym` file or a folder with
one file per part. Both are read.

`search_records` is KiCad's own part search, ported weight for weight, with two
deliberate differences: every field is searchable, and `key>10k` is not read as
a number. Every match reports why it matched.

A `Database` row needs rows a caller supplies. `load_kicad_dbl` reports the
driver, tables, and columns that a `.kicad_dbl` file names. Your code connects,
runs the query, and passes the rows in. **This library never connects, never
writes SQL, and never holds a credential.** SQL quoting depends on the driver.
Each password in a connection string becomes `<withheld>`. The library decides
how part information lands in a schematic, not which part to use. See
`docs/operations.md § Finding a part`.

## Not implemented

| Capability | Status |
|---|---|
| Update PCB from schematic, forward annotation | Partial. `plan_schematic_board_update` updates footprints the caller selects. It does not add or remove footprints. See `docs/schematic-board-update.md`. |
| Update schematic from PCB, back annotation | Not implemented. |
| Automatic project drawing-sheet lookup | Not planned. Supply the template explicitly |
| Update footprint or symbol from library | Not planned |
| KiCad netlist file export | Not planned. Use `kicad-cli` |
| ERC checking | Not planned |
| DRC checking | Not planned. Use `kicad-cli` |
| KLC library-convention validation | Not planned |
| Any GUI-equivalent action: plot, gerber, 3D, autoroute | Not planned. Use `kicad-cli` |

## Do not

Follow the [editing constraints](common.md#editing-constraints) before changing a file.

## The spokes

| Spoke | Read this when |
|---|---|
| `docs/agent/common.md` | Always. Properties, effects, stroke, fill, units, write paths, library tables, embedded files, exceptions. |
| `docs/agent/symbols.md` | You touch a `.kicad_sym` file or a `lib_symbols` definition. |
| `docs/agent/footprints.md` | You touch a `.kicad_mod` file or a board footprint. |
| `docs/agent/boards.md` | You touch a `.kicad_pcb` file. |
| `docs/agent/schematics.md` | You touch a `.kicad_sch` file or a sheet tree. |
| `docs/agent/projects.md` | You touch a `.kicad_pro` or `.kicad_prl` file. |
