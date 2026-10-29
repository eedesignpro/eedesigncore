# Update and change schematic symbols

`update_symbol_from_library` reapplies a placed symbol's own library identifier.
`change_symbol` replaces it with another library identifier.
Both return detached schematic copies and structured change records.
They do not read libraries, write files, or move wires.
Import the helpers and option types from `eedesigncore`.

```python
from eedesigncore import (
    SymbolLibraryUpdateOptions,
    change_symbol,
    update_symbol_from_library,
)

result = change_symbol(
    schematic,
    symbol_uuid=selected_uuid,
    new_lib_id="Device:R",
    library_symbols=definitions,
    options=SymbolLibraryUpdateOptions.preserve_custom_fields(),
)
if not any(issue.severity == "error" for issue in result.issues):
    result.schematic.to_file(output_path)
```

`definitions` maps qualified library identifiers to loaded `Symbol` definitions.
Use canonical escaped identifiers, such as those saved in schematic `lib_id` records.
Bare item names are not valid replacement identifiers.
Supply parent definitions too when a symbol inherits from another symbol.
Existing library-table resolution and `find_symbol` can provide those definitions.
Database-backed parts can use `definition_from_row` with their source definitions.
A missing definition, missing parent, or inheritance cycle blocks the affected target.
The helpers never substitute a different library or read an external file implicitly.

## Grouped choices

| Choice | Behavior |
| --- | --- |
| `gui_defaults("update")` | Reset selected field text. Preserve field appearance, positions, attributes, and valid alternate functions. |
| `gui_defaults("change")` | Also reset field appearance, positions, attributes, and alternate functions. |
| `preserve_custom_fields()` | Replace the drawing and pins. Preserve all placed fields, including a manually assigned footprint and private fields. |
| `atomic_part()` | Reset all library fields except Reference, including empty values. Remove extra user fields. Reset base attributes and alternate functions. |

The single-symbol helpers use their corresponding fresh dialog defaults.
Saved KiCad preferences can differ from these fresh defaults.
All choices preserve position, angle, mirror, placed unit, body style, reference, and DNP by default.
Variant records and instance paths remain unchanged.
The default checklist excludes Reference and Value. The atomic choice includes Value.

## Independent controls

`SymbolLibraryUpdateOptions.fields` accepts `SymbolFieldUpdateOptions`.
Each named field can receive a complete `SymbolFieldUpdatePolicy` through `overrides`.
An override replaces the global field policy for that name. It does not select an excluded field.

| Field control | Behavior |
| --- | --- |
| `selected_fields` | Explicit field names. `None` selects the union of eligible placed and library fields. |
| `excluded_fields` | Remove these names from that selection. Defaults to Reference and Value. |
| `reset_text` | Adopt nonempty library text. |
| `reset_empty` | Adopt empty library text, independently of `reset_text`. |
| `reset_visibility` | Adopt effective library visibility, including either saved hide form. |
| `reset_effects` | Adopt font, size, raw angle, justification, and other text effects. Preserve position and current visibility. |
| `reset_positions` | Transform library field positions through the placed symbol's rotation and mirror. |
| `remove_extra` | Remove selected missing user fields. Never remove mandatory fields. |
| `add_missing` | Add selected new library user fields with their full library content and appearance. |
| `update_private` | Process existing private fields even when not selected. |

The last two switches provide finer control than the dialog.
New private fields require explicit selection. Existing private fields bypass selection by default.

```python
from eedesigncore import SymbolFieldUpdatePolicy, SymbolLibraryUpdateOptions

options = SymbolLibraryUpdateOptions.atomic_part()
options.fields.overrides["Footprint"] = SymbolFieldUpdatePolicy(
    reset_text=False,
    reset_empty=False,
    reset_visibility=False,
    reset_effects=False,
    reset_positions=False,
)
```

`attributes` separates reset controls for `exclude_from_sim`, `in_bom`, `on_board`, and `in_pos_files`.
These switches reset base attributes only. They never reset DNP or variant overrides.
`reset_alternate_pins` controls alternate-function cleanup.
`reset_custom_power_value` permits selected power-symbol Value resets.
`reset_reference_prefix` permits explicit selected Reference resets.
That reset preserves the parsed reference number and changes its prefix. Leading zeroes can disappear.

`reset_pin_name_visibility` and `reset_pin_number_visibility` separately adopt library visibility.
Both default to true. KiCad adopts these values even when its corresponding checkbox is off.
Setting either switch false provides a true preservation extension.

Shape, pins, keywords, and footprint filters always follow the new definition, as the dialog requires.
Field positions use millimeters. Stored field angles keep the library angle when text effects reset.

## Batch selection and previews

```python
from eedesigncore import (
    apply_symbol_library_updates,
    plan_symbol_library_updates,
    select_symbol_library_targets,
)

documents = {"root": root_schematic, "child": child_schematic}
targets = select_symbol_library_targets(
    documents,
    reference_pattern="U*",
    new_lib_id="Logic:Replacement",
)
plan = plan_symbol_library_updates(
    documents, definitions, targets,
    options=SymbolLibraryUpdateOptions.atomic_part(),
)
# Inspect plan.changes and plan.issues before applying.
result = apply_symbol_library_updates(documents, definitions, plan)
updated_child = result.documents["child"]
```

Use one document entry per physical schematic file, including repeatedly used child sheets.
The selection helper supports UUID lists, reference wildcards, value wildcards, and exact library identifiers.
Without filters it selects all symbols. Multiple filters intersect.
Wildcards ignore letter case. A selection never expands to other placed units automatically.
A missing symbol UUID is not eligible for wildcard selection.

Explicit `SymbolLibraryUpdateTarget` records can select instance paths and a `display_instance_path`.
Reference resets affect the selected paths.
A shared child symbol's drawing, pins, and other fields change for every occurrence of that child file.
The result reports this shared-sheet effect.
For a partial path selection, the displayed Reference changes only when its explicit display path is selected.
Without a display path, the cached Reference remains unchanged for partial path selections.
With all paths selected, the displayed reference prefix changes too.

Batch previews default to the fresh update controls. Supply explicit options for change-mode batches.
The plan records exact represented input content, including pin order.
Apply rejects changed inputs and edited result records. It returns new copies each time.
New pin UUIDs stay stable when the same plan is applied again.
This guard is an accidental-change check, not a security signature.
No file save occurs. Use the existing guarded save operations when source files need conflict protection.

Invalid targets receive errors while other valid targets continue, as in the dialog.
Check errors before saving when the application requires an all-or-nothing batch.

## Pins, units, cache, and reports

The replacement retains same-number pin UUIDs first.
It reuses unmatched old pin records for new numbers, then allocates new records.
Surplus old records disappear. All units participate, with the placed body-style filter.
Alternate functions retain their names when possible.
A missing name can map to an alternate with the same electrical type and shape.
Cleanup follows the selected instance units.

A replacement without the placed unit is rejected.
A changed unit count with that unit still available succeeds and produces a warning.
An unavailable unit in another saved instance also produces a warning.
Moved, added, and removed pins produce coordinate records and warnings.
Wires, junctions, no-connect markers, and other schematic items remain unchanged.
These warnings are added diagnostics. The native dialog does not warn about moved pins.

Accepted targets release their old embedded definitions before any new definitions are registered.
An old definition drops only when no remaining symbol uses it.
Unselected users retain their definition.
A conflict creates or reuses a separate embedded name while retaining the external library identifier.
Unrelated unused cache entries remain untouched.

Each change reports old and new identifiers, cache keys, unit counts, fields, pin coordinates,
pin records, affected sheet paths, base attributes, definition properties, and cache changes.
Pin records include UUIDs and alternate selections.
A library definition can change even when its pin coordinates do not change.

## Source and verification limits

Behavior comes from the exact KiCad 10.0.3 tag, not memory or the newer local source checkout.
Constructed source-behavior tests and read/write stability tests cover the helpers.
Native before-and-after GUI pairs are still pending.

When reset positions meets a saved autoplaced state, `field_placement="isolated"` uses the existing isolated helper.
The result reports this difference from KiCad's placement around surrounding items.
`field_placement="library"` retains transformed library positions and reports that native autoplacement was skipped.
The file format does not preserve a distinction between native AUTO and MANUAL states.

Native pointer order can choose different old pin UUIDs for duplicate numbers or unmatched pin reuse.
FileIO uses file order and reports ambiguous reuse.
New UUIDs also differ from an independent GUI run.
Cache comparison reuses the existing normalized definition comparator.
Reordered drawing records can create an extra cache entry. Full native cache equality remains a golden-file check.
These limits prevent a claim that independent native and FileIO replacement runs are byte-identical.
Existing unmodified-file round-trip guarantees remain unchanged.

## Golden files to save

Use each exact folder name below. Save `before.kicad_sch` and `after.kicad_sch`,
the source library, and the selected dialog settings inside that folder.
For repeated sheets, include the root and child schematic files.
Keep native UUID differences visible for review rather than replacing them manually.

| Exact case name | Before and after operation |
| --- | --- |
| `update_keep_manual_footprint_and_user_fields` | Update a changed drawing and pins with Footprint and user fields unselected. |
| `change_atomic_part_reset_selected_fields` | Change identifier with all non-Reference fields selected and all field reset controls enabled. |
| `update_empty_fields_independent_of_text_reset` | Reset empty fields with text reset disabled. Include a nonempty field as a control. |
| `change_rotated_mirrored_field_styles_and_positions` | Change rotated and mirrored symbols. Compare separate style and position resets. |
| `update_alternate_pin_functions_preserve_remap_clear` | Include a retained alternate, renamed equivalent, removed alternate, and base-name alternate. |
| `change_moved_added_removed_and_renumbered_pins` | Change to moved pins, different numbers, fewer pins, and additional pins. |
| `change_multiunit_and_repeated_sheet_instances` | Include multiple units and repeated child paths. Try a replacement with too few units. |
| `update_selected_definition_cache_split_and_cleanup` | Update selected shared-definition users, then the remaining users. Save both stages. |
| `update_power_value_attributes_and_private_fields` | Include custom power Value, DNP, exclusions, existing private fields, and new private fields. |
| `change_reference_prefix_and_autoplace_state` | Select Reference explicitly. Include annotated, unannotated, and previously autoplaced symbols. |
