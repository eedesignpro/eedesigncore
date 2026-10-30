# Schematic preparation for viewers

The public operation prepares a native project for symbol selection and net highlighting.
Import it from the Python package root.

## Calling contract

```python
from eedesigncore import prepare_schematic_view

view = prepare_schematic_view(
    "controller.kicad_pro",
    document_id="controller-design",
    instance="controller",
)
view.save("controller.schematic-view.json")
```

The source accepts a local project path, a root schematic path, a loaded SheetHierarchy, or a loaded Schematic.
A schematic path also loads its sibling project file when present.
A loaded Schematic supplies only that sheet. Missing child drawings appear in coverage records.
A loaded hierarchy retains its project settings. No further files load from an in-memory source.

The operation copies loaded inputs. It does not write source files, start KiCad, read external symbol definitions, or use a graphical editor.
The optional save call writes only the caller-selected result file.

Pass `drawing=False` to skip the page drawings. Each page then keeps an empty SVG and a `drawing_not_requested` coverage issue, and no item carries drawn bounds.
Nets, buses, items and resolved text do not change.

The caller supplies document_id and keeps it stable across exports of the same design.
Use a distinct instance for each board occurrence in an assembly.
The default instance is design.

The operation returns SchematicView, shared with generated assembly roots.
SchematicPage, ViewItem, ViewSource, ViewBounds, SchematicNet, SchematicBus, SchematicCoverage, and SchematicIssue are supported root exports.
Existing exported names and field meanings retain the package compatibility promise.
The drawing format remains schematic-view-1. Consumers must inspect coverage rather than infer support from that version alone.

## Hierarchy and source context

Every sheet occurrence has a page, including repeated uses of one file.
The source sheet path qualifies symbols, pins, drawing items, and navigation links.
The page_number field retains KiCad's saved occurrence page number. None means the source did not supply one.
References and selected units resolve from occurrence records before drawing and electrical calculation.
Ambiguous occurrence records raise ValueError.

A symbol item retains its resolved reference, unit, source UUID, position, and saved alternate pin selections.
Each pin item retains its selected name and electrical type, number, parent symbol item, and transformed connection point.
Hidden pins retain electrical membership. Their drawn flag is false and their drawing bounds are absent.
The netlist_member flag identifies pins included in KiCad's board netlist view.
Power-symbol pins and board-excluded pins still participate in electrical calculation and highlighting.

## Drawing and selection

The page SVG comes from the existing schematic renderer.
The data-item-id attribute points directly into that page's items table.
It identifies symbols, sheet blocks, wires, buses, pins, labels, junctions, sheet pins, bus entries, and no-connect marks.
A source item can own several SVG groups. Select every matching group.
Pin groups can occur inside symbol groups. A viewer can choose the nearest selectable ancestor for pin selection.

All coordinates and bounds use millimeters. X increases rightward. Y increases downward.
Page bounds use the saved paper size unless sheet_bounds supplies an occurrence-specific window.
A page window does not clip or recalculate the reported source bounds.

Symbol body_bounds enclose visible symbol-body graphics and body text, excluding pins and placed fields.
Symbol bounds enclose all visible symbol geometry, including pin graphics, pin text, placed fields, and DNP marks.
Bounds come from the same emitted shapes that form the drawing.
They include stroke width and a small rounding allowance.
Cubic curves use their control hull, so their bounds can exceed the curve's exact extent.
A symbol without visible body graphics has no body_bounds. Use its visible bounds when present.
An item without visible geometry has no visible bounds. Do not treat its position as a hitbox.

Project and inherited sheet text variables resolve before drawing.
Unknown variables remain literal and appear in coverage records where they affect electrical names.
Unresolved label variables make project electrical memberships unavailable, so literal placeholders cannot cause false joins.
Drawing-sheet borders and title blocks are not rendered.
Embedded fonts use the renderer's stroke font and produce an explicit substitution record.
Drawing coverage therefore distinguishes schematic content from unsupported page decorations.

## Electrical membership

The existing KiCad-derived calculation supplies electrical connections.
It carries source identities through geometric adjacency, net-name resolution, hierarchy propagation, and bus resolution.
It does not read electrical connections from SVG contact.

The view.nets table maps net identities to names and source-item identities.
Each item.net_ids list supplies the reverse association.
A net can include wires, pins, labels, junctions, sheet pins, and wire-side bus entries.
An unnamed wire group retains a net identity with name set to None.

Repeated sheets retain separate local nets.
Global and hierarchical connections join through the supported electrical calculation.
Names remain display data. Never use a displayed label or reference as a replacement for the returned net identity.
Automatic KiCad net names use the library pin name, even when the instance selects an alternate function.
The pin item separately exposes the selected function.

The view.buses table retains bus source items, proven member net identities, and unresolved member names.
Each item.bus_ids list supplies the reverse association.
A bus is not a scalar net. Selecting a bus can highlight its member nets without merging them.
Project bus aliases participate in the calculation.
A bus member without a resolved source net remains explicit in unresolved_members.
Pins connected directly to a bus retain bus membership and an unsupported-content record. No scalar net is invented.

Native electrical records remain board-local inside an assembled view.
Assembly connector bundles separately retain the assembly resolver's system-net and contact relationships.
The prepared pages do not assert that arbitrary caller-supplied native drawings match a NetBOM snapshot.

## Identity and changes

Page and source-item identities include the caller's document and board-occurrence identities.
Source UUIDs distinguish ordinary source items within their sheet occurrence.
Pin identities also distinguish repeated pin numbers at different definition positions.
Identical stacked pin definitions at one position share an electrical selection identity.

Net identities derive from their resolved source-item membership, not names or temporary net codes.
Reordering source items or changing reference text does not change identities for unchanged membership.
Adding or removing a member, splitting a net, or joining nets can change net identities.
Changing the caller's document_id or instance changes all qualified identities.

A missing source UUID uses source content where possible and produces an uncertainty record.
Content-derived identities can change after edits.
The source_revision identifies the prepared input snapshot. Do not use it as an electrical net identity.

## Coverage and errors

Inspect view.coverage and each page.coverage before claiming a complete result.
Drawing and electrical coverage are separate.
Their values are complete, partial, unavailable, and not_calculated.
The last value also supports older prepared documents that contain no electrical calculation.

Issues carry a code, scope, status, message, and available item or sheet context.
The status distinguishes unsupported content, uncertain interpretation, and omitted content.
Missing symbol definitions make drawing coverage unavailable for that page and electrical coverage partial.
Missing or duplicate annotation makes project-wide electrical identities unavailable. Drawings remain inspectable.
An unsupported drawing reports its cause and returns an empty page drawing rather than silently dropping individual items.

strict=True is the default for file loading. Missing child sheets raise SheetHierarchyError.
strict=False retains available occurrences and records incomplete hierarchy coverage.
Missing roots, path escapes, cycles, and resource limits still raise errors.
Unknown file tokens and malformed files raise KicadParseError.
Invalid occurrence selections, duplicate source identities, and unknown paper dimensions raise ValueError.
Ordinary file-access errors retain their Python exception type.
Remote source paths are refused before file access.

File preparation limits hierarchy depth, distinct files, sheet occurrences, and total source bytes.
Standalone JSON loading also limits document size, nesting, page count, item count, and SVG size.
SVG validation refuses executable elements and external resource references.
Bus expansion also limits depth, text length, vector size, and expanded member count.
None of these limits silently discard content.

## Viewer example

```python
from eedesigncore import SchematicView, prepare_schematic_view

view = prepare_schematic_view("controller.kicad_sch", document_id="controller-design")
page = view.pages[view.roots[0]]
svg_text = page.svg

# Pass svg_text to the viewer's drawing surface.
# Resolve a selected SVG data-item-id directly in page.items.
symbols = [item for item in page.items.values() if item.kind == "symbol"]
if symbols:
    symbol = symbols[0]
    body_hitbox = symbol.body_bounds
    complete_hitbox = symbol.bounds
    pins = [item for item in page.items.values() if item.parent_item == symbol.id]
    selected_pin = next((pin for pin in pins if pin.net_ids), None)
    if selected_pin is not None:
        net = view.nets[selected_pin.net_ids[0]]
        highlight_ids = set(net.item_ids)
        page_highlights = {
            page_id: [key for key in candidate.items if key in highlight_ids]
            for page_id, candidate in view.pages.items()
        }
        readable_name = net.name

issues = view.coverage.issues
encoded = view.to_json()
restored = SchematicView.from_json(encoded)
```

The viewer supplies navigation, selection policy, rendering, and highlighting.
The prepared result supplies source identity, bounds, and electrical truth.
The viewer must not infer missing connections from drawing contact or matching text.

## Validation evidence

Constructed fixtures include repeated sheets, multiple units, hierarchical labels, sheet pins, global labels, junctions, local buses, hierarchical buses, and alternate pins.
Captured KiCad netlists supply independent expected pin groupings and names.
Additional tests verify source preservation, drawing associations, bounds, stable identities, missing content, and remote-path refusal.
KiCad runs only while recording test evidence. It is not a dependency of preparation or the normal test suite.

## Selected fonts

Schematic preparation accepts the same loaded font as direct SVG export.
Pass `font=load_font("Cascadia Mono")` to `prepare_schematic_view` or `AssemblyWorkspace.schematic_view`.
The selected font controls native schematic drawing and item selection bounds.
See [font selection](svg-fonts.md) for dependencies, measurements, and limits.

## Unresolved display text and electrical names

Unresolved free text, table cells, title-block text, and ordinary descriptive fields remain drawing diagnostics.
They do not remove valid nets, buses, or source-item membership. Missing Git context follows this rule.
Unresolved net labels, sheet pins, sheet names, symbol references, and power-symbol values remain electrical diagnostics.
These can change electrical names, so preparation withholds uncertain project-wide membership.
Inspect each issue scope when deciding whether an unresolved-text warning affects drawing or electrical results.
