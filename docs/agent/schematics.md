# Schematics: `.kicad_sch`

## Read this when

You read or change a `.kicad_sch` file, or you walk a sheet hierarchy. Read
`docs/agent/README.md` and `docs/agent/common.md` first. A `(lib_symbols)`
definition follows `docs/agent/symbols.md`.

## One file at a time

`Schematic.from_file` loads exactly ONE sheet file. A sub-sheet reference is
kept as data, never opened, never resolved, never checked against the disk.
Byte identity is per file.

Walk a whole project with `load_hierarchy`, which is read-only.

## Model tree

```
Schematic                       eedesigncore
  .version, .generator, .generator_version, .uuid, .paper, .title_block
  .lib_symbols: list[Symbol]    the embedded definitions, keyed by full lib id
  .symbols: list[PlacedSymbol]  placed instances
  .sheets: list[Sheet]          sub-sheet nodes, with the Sheetfile reference
  .sheet_instances: SheetInstances | None
  .lines, .labels, .junctions, .no_connects, .bus_entries
  .texts, .text_boxes, .graphic_shapes, .images, .tables, .rule_areas, .groups
  .embedded_files, .embedded_fonts
```

`PlacedSymbol`, `Sheet`, `Label`, `Line`, and the rest live in
`eedesigncore.sch.models`. `Symbol` comes from
`eedesigncore.common.sch_family.symbol`, the same model a `.kicad_sym` file
uses.

`Line.line_type` separates a wire from a bus and from a plain graphic line.
`Label.kind` separates a local, global, hierarchical, and netclass label.

## lib_symbols naming

Inside a schematic, `Symbol.name` holds the FULL library id, `Lib:Name`. In a
`.kicad_sym` file the same field holds the bare name. Unit names stay bare in
both, so a unit of `Device:R` is `R_1_1`.

Read `unit_index` and `body_style_index` from the model. Do not subtract the
parent name from the unit name.

The `(lib_symbols)` block sorts by full library id, as a plain string compare.
It is case-sensitive and it is NOT natural order.

Known exception: KiCad keeps the stored property order of each `lib_symbols`
entry when it saves a schematic. This library writes the five standard
properties first. An entry that lists a custom property first changes order on
a round trip.

## Sheet hierarchy

```python
from eedesigncore import load_hierarchy

tree = load_hierarchy("project.kicad_pro")
for node in tree.walk():
    print(node.page, node.name, node.path.name)
```

- Start at a `.kicad_pro` file or at a root `.kicad_sch` file.
- Each `SheetNode` carries the file path, the loaded `Schematic`, the sheet
  name, the full KIID path, and the page number of that instance.
- One file used twice is two nodes sharing one loaded `Schematic`.
- A cycle, a missing top-level sheet, or a reference outside the project
  directory raises `SheetHierarchyError`. `strict=False` collects missing-file
  messages in `tree.errors` and keeps walking.
- Widen the confinement with `allowed_roots`.

## Operations

| Call | Result |
|---|---|
| `extract_symbol(sch, lib_id="Device:R")` | The `(lib_symbols)` entry as a bare-named library `Symbol`. |
| `inject_symbol(sch, symbol, at=(x, y))` | Registers the definition, places an instance, returns the `PlacedSymbol`. |

Three behaviors of `inject_symbol` matter.

1. **Nickname.** A pre-qualified name passes through. Otherwise
   `lib_nickname` wins, then a `lib_table` match, then the placeholder
   `eedesigncore`. Build the table with `resolve_lib_table("sym", ...)`, see
   `docs/agent/common.md § Library tables`.
2. **Auto-annotation.** Omit `reference` and the call assigns the lowest free
   `<Prefix><n>`, counted over this ONE file. It is library policy, not a copy
   of the KiCad annotator (`docs/operations.md § Reference auto-annotation`).
3. **Collision rename.** A clashing key with a different definition is
   inserted under a fresh bare `<Name>_<n>`
   (`docs/operations.md § Collision rename`).

A schematic uses uuids for identity and for group membership. The uuid rules in
`docs/agent/boards.md § UUIDs and groups` apply unchanged.

## Trap: `mirror` on schematic text

KiCad's schematic parser READS `(justify … mirror)` and throws it away
(KiCad 10.0.3 source, `sch_io_kicad_sexpr_parser.cpp:862-863`, `case T_mirror: break;`, under the
comment "Do not set mirror property for schematic text elements"). The board
parser stores it (`pcb_io_kicad_sexpr_parser.cpp:829`).

This library uses ONE shared effects loader and ONE shared effects writer for
both formats, so `TextAttributes.mirror` is stored and re-emitted on every
path. Two consequences.

- **Setting `mirror = True` on schematic text writes a token KiCad ignores.**
  Nothing mirrors. Rotate or flip the item instead.
- **A hand-authored schematic that already carries the token still round-trips
  unchanged.** That is deliberate. The read path must NOT start discarding the
  token: the library carries no dirty tracking, so a discarded token would
  disappear from every file it saves, KiCad-written files included.

## Gaps in this scope

- No electrical rule checking. `prepare_schematic_view` reports which net each
  pin belongs to, where the library can resolve it.
- Electrical content the library cannot resolve is listed in the coverage
  record of the result. It is not dropped silently.
- No annotation across sheets. Auto-annotation sees one file.
- Forward and back annotation: see `docs/agent/README.md § Not implemented`.
- `load_hierarchy` never writes. There is no whole-project save.

## SVG from memory

`render_schematic_svg(schematic, view_box=..., font=...)` returns SVG text for one loaded sheet.
It uses embedded symbol definitions and preserves the supplied schematic.
See [schematic SVG exports](../svg-schematic-previews.md) for settings and unsupported items.

## Project preparation

`prepare_schematic_view` accepts a project, root schematic, loaded sheet, or loaded hierarchy.
It returns occurrence-qualified drawings, symbol bounds, electrical memberships, and coverage.
See [public schematic preparation](../schematic-preparation.md).

## Built-in sheet and note styles

The creation helpers apply fixed defaults taken from one in-house project
template.

- `create_sheet` draws a sheet symbol with a pale green fill and a dark green
  border. Pass `fill_color` and `border_color` to change them.
- `create_subsheet` and `create_sheet_schematic` seed a new child page: landscape
  B paper, a title that shows the sheet name, and a GIT INFO table with branch,
  date, and commit rows. The package ships that layout as a template file.
  KiCad resolves the variables in the table, not this library.
  `CreatedSubsheet` holds the result.
- `create_note_block`, `create_schematic_note`, `create_layout_note`, and
  `create_todo_note` write fixed-size text boxes in one fixed style, with no
  color arguments. `parse_note_block`, `parse_schematic_notes`,
  `update_note_block`, `NoteKind`, and `SchematicNote` read and edit them.

See `docs/schematic-creation.md` and `docs/schematic-notes.md`.
