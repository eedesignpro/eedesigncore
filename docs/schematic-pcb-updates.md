# Schematic and PCB updates

These helpers update a PCB from a schematic, or existing schematic symbols from a PCB.
They use parsed files and do not require a running KiCad session.
They return detached results. They never save files or modify the input objects.

The behavior follows the pinned KiCad source, with the explicit limits below.
This is not a complete replacement for KiCad's native copper connectivity and wire cleanup.
Native GUI golden files remain pending. Source audits and automated tests supply the current evidence.

## Simple calls

```python
from eedesigncore import (
    Board, Footprint, FootprintPlacement, PcbUpdateOptions,
    SchematicUpdateOptions, load_hierarchy,
    update_pcb_from_schematic, update_schematic_from_pcb,
)

source = load_hierarchy("design.kicad_pro")
board = Board.from_file("design.kicad_pcb")
footprint = Footprint.from_file("R_0603_1608Metric.kicad_mod")

forward = update_pcb_from_schematic(
    board,
    source,
    library_footprints={"Resistor_SMD:R_0603_1608Metric": footprint},
    placements={"R1": FootprintPlacement(position=(100, 80))},
    options=PcbUpdateOptions(delete_extra_footprints=False),
)
updated_board = forward.board

reverse = update_schematic_from_pcb(
    updated_board,
    source,
    options=SchematicUpdateOptions(update_net_names=False),
)
updated_schematic_documents = reverse.documents
```

Supply every definition needed for a new or replaced footprint.
A definition can have the qualified identifier or its item-name portion.
The helper does not resolve library files from a nickname or access the network.
New footprints require explicit placements keyed by reference. Coordinates use millimeters.
Existing footprints keep their position, orientation, side, lock, and parent UUID.

The source can also be a single `Schematic`.
For a single schematic, `reverse.schematic` returns that document.
For a hierarchy, `reverse.documents` maps each sheet file path to its updated document.
Repeated occurrences share one output document. Conflicting shared edits block application.
`reverse.board` contains any updated associations from reference matching.

## Preview and apply

```python
from eedesigncore import (
    plan_update_pcb_from_schematic, apply_update_pcb_from_schematic,
    plan_update_schematic_from_pcb, apply_update_schematic_from_pcb,
)

plan = plan_update_pcb_from_schematic(
    board, source, library_footprints=definitions, placements=placements,
)
if plan.blockers:
    raise ValueError(plan.blockers)
forward = apply_update_pcb_from_schematic(
    board, source, plan, library_footprints=definitions,
)

plan = plan_update_schematic_from_pcb(board, source)
if plan.blockers:
    raise ValueError(plan.blockers)
reverse = apply_update_schematic_from_pcb(board, source, plan)
```

The preview reports changes and issues. Each issue contains a code, severity, message, and item identity.
Errors block the complete application. Warnings remain in the result.
Apply compares the source, board, definitions, requested settings, changes, and resulting content with the preview.
An altered input or preview requires a new preview.
These checks detect stale edits. They do not authenticate untrusted plans.
The one-call helpers use the same preview and apply checks.

The caller controls saving. Use `to_file` to preserve recorded line endings.
For edits to existing files, use the existing guarded-save helpers with captured source dependencies.
Include all schematic files, the project file, the board, and any supplied library definitions.
An in-memory preview does not detect a separate edit to an on-disk file.
See [selected updates](schematic-board-update.md) for the guarded-save pattern.

## PCB update controls

All controls belong to `PcbUpdateOptions`.

| Control | Default | Effect |
|---|---|---|
| `match_by_reference` | `False` | Match full symbol paths. When enabled, match references without case sensitivity and update associations. |
| `replace_footprints` | `True` | Replace the geometry when the assigned footprint identifier changes. |
| `refresh_footprints` | `False` | Replace geometry even when the footprint identifier stays the same. |
| `delete_extra_footprints` | `False` | Delete unmatched footprints, subject to lock, group, and routed-contact checks. |
| `override_locks` | `False` | Permit replacement or deletion of locked footprints. |
| `update_fields` | `True` | Copy schematic fields other than Reference, Value, Footprint, and Component Class. |
| `remove_extra_fields` | `False` | Remove extra nonmandatory footprint fields, independently of field copying. |
| `transfer_symbol_groups` | `False` | Transfer supported schematic group membership to footprints. |
| `update_pad_nets` | `True` | Assign schematic nets, pin functions, and pin types to pads. |
| `update_attributes` | `True` | Copy do-not-populate, bill-of-materials exclusion, and position-file exclusion. |
| `update_references` | `True` | Copy the schematic reference. |
| `update_values` | `True` | Copy the schematic value. |
| `preserve_routed_contacts` | `True` | Block changed or deleted pads on nets that contain copper. |

Filters, jumper metadata, unit metadata, component-class fields, and associations update with the physical part.
A hierarchy also supplies the sheet name and file name.

### What KiCad 10 needs to link a footprint

The update writes what KiCad 10's Update PCB from Schematic writes, so KiCad links every footprint afterwards.

- The footprint path is the netlist sheet path, the sheet uuids below the root, then a symbol uuid. With one top-level sheet the root uuid is left out.
- KiCad links a footprint on any unit uuid. A path that already names one stays. A new path takes KiCad's first netlist unit: for two units, the unit with the higher uuid.
- For three or more units KiCad orders the units by an R-tree, which this library does not reproduce. A new path then still links, but it can name another unit than KiCad would.
- A 0.39.0 path, with the root uuid at its head, still matches and is rewritten.
- The sheet name is the full sheet path, `/COILS/PREAMP S1C1/`; the root sheet is `/`.
- A power symbol, or any reference that starts with `#`, gets no footprint. Nor does a symbol on a sheet that is excluded from the board.
- A pad pin function is the shown pin name, an underscore and the pin number. A pin type takes `+no_connect` only under a no-connect marker on a net of that pin alone.
- A replaced footprint's zones, drawings and points move with its placement.

`tests/unit/test_board_update_kicad_parity.py` cites the KiCad source for each rule.
On a 508-part test bed, KiCad 10 links all 508 footprints after an update, and every pad net, pin function and pin type equals the kicad-cli netlist.

The planner reads the schematic without its drawing. On a 46-sheet design a plan takes about 4 s.
The helper combines units into one physical part by their saved instance reference.
Conflicting unit assignments block the update.

New fields are hidden. Existing field formatting stays unchanged when only text changes.
Replacement reuses existing footprint insertion and side/orientation helpers.
Matched pads and fields retain their UUIDs. Ambiguous matches block replacement.
Other child UUID matching is deterministic but does not reproduce KiCad's full geometry-similarity algorithm.
External group or generator references to removed children block replacement.

Unmatched retained footprints keep their existing associations. This differs from native association clearing.
Board-only footprints stay on the board and retain their nets.
Locked extra footprints stay on the board unless lock override is enabled.
Transferred groups cannot reuse another item's identity or silently break parent group references.
Repeated-sheet groups and conflicting multi-unit group membership block transfer.

A uniform copper-net rename updates the existing copper through the established net-renaming operation.
Splitting an existing copper net, or removing its assignment, requires physical copper connectivity and blocks the update.
The helper does not route, refill zones, or repair dangling tracks.
Disabling `preserve_routed_contacts` permits changed contacts with a warning. It does not repair routing.

## Schematic update controls

All controls belong to `SchematicUpdateOptions`.

| Control | Default | Effect |
|---|---|---|
| `match_by_reference` | `False` | Match full symbol paths. When enabled, match references and relink the returned board. |
| `update_references` | `True` | Update references and matching sheet-instance references. Disabled by reference matching. |
| `update_footprints` | `True` | Copy the footprint identifier into the symbol Footprint field. |
| `update_values` | `True` | Copy the footprint Value field. |
| `update_net_names` | `True` | Apply supported unit swaps, pin swaps, and physical net naming edits. |
| `update_attributes` | `True` | Copy do-not-populate and bill-of-materials exclusion. |
| `update_other_fields` | `True` | Copy other footprint fields and remove absent nonmandatory fields. |
| `prefer_unit_swaps` | `True` | Apply complete unit permutations before net-label edits. |
| `prefer_pin_swaps` | `True` | Prefer eligible reciprocal pin swaps when unconstrained swapping is enabled. |
| `allow_unconstrained_pin_swaps` | `False` | Explicitly permit reciprocal pin geometry swaps within one placed symbol. |

The reverse operation does not add or remove symbols.
Unmatched symbols remain unchanged with a warning.
Existing fields containing text variables keep their expressions.
Duplicate target references and incompatible repeated-sheet fields or attributes block application.

Net edits use physical connection groups, not only merged named nets.
The helper changes the highest-priority supported local, global, hierarchical label, or sheet pin on that physical connection.
An unchanged resolved net preserves its existing naming text.
Ambiguous equal-priority naming sources block a requested rename.
A connection without a label can receive a local label at an ordinary pin.
The new label follows pin orientation and symbol transforms, with native justification.
Its text size uses the project's schematic default, or the native default when no project is supplied.

Complete unit swaps preserve symbol UUIDs and contact positions.
They update the placed unit and its matching saved sheet occurrence.
Native behavior skips normal field, attribute, and label changes for swapped units, but still updates other fields.

Explicit reciprocal pin swaps require unique pin numbers, equal pin lengths, and an unshared placed symbol.
They exchange pin positions and orientations within a detached embedded definition.
This preserves the set of wire contacts. It does not move or create wires.
Shared-symbol pin swaps fall back to label checks with a warning.
Derived definitions, ambiguous pins, and unequal lengths block geometry swapping.
Power naming pins do not participate in pin swaps.

## Shared limits

- Saved occurrence units that differ from the placed unit require occurrence-specific geometry and currently block both directions.
- Assembly variants require explicit variant selection and currently block both directions.
- Rule-area population and component-class inheritance currently block both directions.
- Stacked library pin notation enclosed in square brackets requires expansion and currently blocks both directions.
- Incomplete electrical coverage blocks requested net changes.
- Direct power naming changes require a separate explicit power-symbol update.
- Arbitrary pin permutations, native wire cleanup, and copper-island reconstruction are not implemented.

The older `plan_schematic_board_update` and `apply_schematic_board_update` remain supported.
They still operate on explicitly selected associations and pad nets, with their existing defaults and limits.

## Native GUI golden cases

Save a before and after project for each exact case name.
Include the project, every schematic sheet, the PCB, and any required custom library definitions.
Record the dialog selections with each case.

| Case name | Native action and observation |
|---|---|
| `pcb_update_add_and_relink_by_reference` | Add an assigned footprint, then relink a second footprint by reference. Check paths and pad nets. |
| `pcb_update_replace_rotated_backside_footprint` | Replace a rotated back-side footprint. Check placement, pad identities, fields, and embedded assets. |
| `pcb_update_fields_population_and_groups` | Change fields, population settings, component class, and group membership. Check independent field removal. |
| `pcb_update_locked_extra_and_board_only` | Delete extra footprints with lock override off and on. Keep a board-only footprint. |
| `pcb_update_uniform_routed_net_rename` | Rename one fully routed net without changing its contacts. Compare tracks, vias, and zones. |
| `schematic_update_fields_and_repeated_sheets` | Change references, values, footprint assignments, and user fields across repeated sheets. |
| `schematic_update_physical_labels_and_generated_names` | Change separate labeled and unlabeled physical connections, including a generated net name. |
| `schematic_update_complete_three_unit_cycle` | Exchange three unit net vectors. Check unit order, instance data, and skipped ordinary fields. |
| `schematic_update_reciprocal_pin_swap` | Enable unconstrained swapping for an equal-length pin pair. Compare embedded definitions and wire contacts. |
| `schematic_update_reference_relink` | Relink by reference. Check schematic references and PCB association paths. |

## Evidence

Source: KiCad tag `10.0.3`.
Focused tests are in `tests/unit/test_design_updates.py`.
