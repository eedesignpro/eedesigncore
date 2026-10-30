# Boards: `.kicad_pcb`

## Read this when

You read or change a `.kicad_pcb` file. Read `docs/agent/README.md` and
`docs/agent/common.md` first. A footprint on a board follows
`docs/agent/footprints.md`.

## Model tree

```
Board                           eedesigncore
  .version, .generator, .generator_version
  .general, .paper, .title_block
  .layers: list[BoardLayer]     the copper and technical layer stack
  .setup: Setup                 design settings, stackup, plot params
  .properties: list[Property]   the text-variable snapshot, see below
  .net_names: set[str]          derived; nets are names, not a table
  .footprints: list[Footprint]
  .drawings: list[PcbDrawItem]  one ordered list, as in a footprint
  .tracks: list[TrackSegment | TrackArc | Via]
  .zones, .groups, .generators, .targets, .points, .variants
  .embedded_files, .embedded_fonts
```

`Board`, `Setup`, `BoardLayer`, `TrackSegment`, `TrackArc`, and `Via` live in
`eedesigncore.pcb.models`. `Zone`, `PcbGroup`, and the drawing classes live in
`eedesigncore.common.pcb_family`.

## Nets

KiCad 10 references a net by NAME, not by code. There is no net table to keep
in step.

- Set `pad.net_name` to attach a pad. `inject_footprint` takes `net_map` for
  the same job.
- `board.net_names` is a derived `set[str]`, synthesized from tracks, zones,
  copper drawings, and footprint pads. It is a read-only utility. The library
  never emits it as a table.
- `Board.nets` is a dead field. It is never populated. Use `board.net_names`.

The library assigns no nets and does not work out board copper connectivity.

## File version

`Board.version` defaults to `20260206`, the KiCad 10.0.3 board format. No
loader rejects a file by version. An older board's footprints load; other old
tokens raise `KicadParseError`. Treat that as out of range.

`scripts/corpus_test.py --root <folder>` round-trips every KiCad file in a
folder you supply. It grades only files at the target version and reports the
rest as `version_skipped`.

## UUIDs and groups

Groups reference members by uuid. `lib_id` preserves the design-block library link.

- Never call `strip_uuids(board)` unless you intend to discard board identity.
  With the default `group_aware=True` it keeps every uuid a group references,
  so groups survive. `group_aware=False` DESTROYS groups.
- `generate_uuids(board)` fills missing values by default, and
  `only_missing=False` regenerates everything and remaps group members
  together.
- The library never mints a uuid on its own.

## Text variables

`Board.properties` carries a copy of the project text variables. Editing a
variable in the `.kicad_pro` file leaves that copy stale. See
`docs/agent/projects.md § The text-variable snapshot`.

## Manipulating a board

Every operation below changes the model IN PLACE and returns `None`, unless the
row says otherwise. `docs/operations.md § Board manipulation` holds the
coordinate-frame rules, the box rule, the fill rules, and the KiCad citations.

| Operation | What it does |
|---|---|
| `translate_board`, `translate_footprint`, `translate_items` | move by a millimeter offset |
| `rotate_board`, `rotate_footprint`, `rotate_items` | turn about a center |
| `set_footprint_orientation` | give a placement a new angle, without the upright correction |
| `board_bbox`, `footprint_bbox`, `items_bbox` | return a `BoundingBox` in millimeters, or `None` |
| `item_layers`, `items_on_layers` | the layer selection every `layers=` argument applies |
| `new_board_from` | return an EMPTY board with a source board's settings |
| `inject_board` | copy a source board's CONTENT in at a placement |
| `derive_uuids` | give a copy reproducible uuids from a caller key |
| `rename_nets`, `rename_footprint_nets` | apply a net-name map to every reference |
| `extract_fills`, `replace_fills`, `flatten_fills` | read, transplant, or graphic-ise a zone fill |

Four rules matter most.

- A coordinate moves in INTERNAL UNITS, so a changed value is one KiCad could
  have written and a KiCad resave leaves it alone.
- Changing a placed footprint changes its `at` and NOTHING else, because KiCad
  stores a child footprint-local and de-rotated. A turn also changes the angles
  that are board-absolute: a pad angle and a footprint text angle.
- A quarter turn is exact. Any other angle is refused when the selection holds a
  rectangle, because KiCad turns one into a polygon.
- A net is a NAME, so injecting content that names `GND` into a board that names
  `GND` JOINS the two nets. Pass `net_map`.

## Drawing a board to SVG

Copper is OPT-IN through `content`:

```python
from eedesigncore import (ALL_CONTENT, Board, BoardContent, LayerStyle,
                          ViewBox, board_bbox, render_board_svg)

board = Board.from_file("board.kicad_pcb")
bb = board_bbox(board)
box = ViewBox(min_x=bb.min_x, min_y=bb.min_y, width=bb.width, height=bb.height)
style = LayerStyle(colors={"F.Cu": "#c83434"})

render_board_svg(board, ["F.Cu"], box, style=style)                    # drawings only
render_board_svg(board, ["F.Cu"], box, style=style, content=ALL_CONTENT)
render_board_svg(board, ["F.Cu"], box, style=style,
                 content={BoardContent.drawings, BoardContent.tracks})
```

`drawings` is graphics, fields and points. `tracks` adds segments, arcs and
vias, `zones` adds pours, `pads` adds pad copper.

A via's layer pair is a SPAN: it draws on every copper layer between the two it
names. A filled zone draws its `filled_polygons` for that layer; an unfilled one
falls back to a stroked outline.

### Pads

`BoardContent.pads`. A pad is the only item whose geometry is PER LAYER, because it
carries a padstack. `pad_layer_geometry(pad, layer)` resolves it. It and
`pad_draws_on`, `pad_flash`, and `pad_geometry_is_exact` import from
`eedesigncore.render`, outside the supported root API.

Three details are easy to get wrong.

- A pad POSITION is footprint-relative and needs the host's map. A pad ANGLE is
  board-absolute, so the footprint orientation must NOT be added.
- A pad's layers carry wildcards (`*.Cu`). `on_layers` expands them. Call
  `pad_draws_on` for the whole question, which adds the flash rule.
- The `(offset ...)` inside `(drill ...)` moves the COPPER, not the hole.

A rounded rectangle is a POLYGON, not arcs: KiCad polygonizes first, so a 5x3 pad
at ratio 0.25 is 32 vertices. An oval pad is ONE stroked segment at the minor
axis. Six of seven shapes are vertex-exact against `kicad-cli`;
`pad_geometry_is_exact` names the exception, a CUSTOM pad, whose primitives KiCad
unions and this library draws separately.

A plated through-hole pad with `remove_unused_layers` needs board connectivity
for inner copper, which this library has for schematics only. It DRAWS, and
`pad_flash(...).certain` is `False`.

### Text and dimensions

Text draws, with KiCad's built-in stroke font: board text, footprint fields such
as reference designators, and a dimension's measurement. Pass `font=` a
`TextMetrics` from `eedesigncore.render` to override it; needed only for text naming an installed TrueType
face this library cannot read.

Dimension line work depends on the text, because KiCad cuts a gap in the
crossbar where the text sits.

Verified against `kicad-cli pcb export svg`: 233 of 233 stroke segments matched
to 0.003 mm across a dimension, plain text, rotated text and justified text.

### What the renderer still misses

Run `scripts/render_coverage.py --kicad-root <KiCad source clone>`. It reads
the KiCad 10.0.3 board plot source and lists each case this renderer does not
draw, with a reason. It exits non-zero on a case that has no reason.

## Gaps in this scope

- No board copper connectivity. The library does not work out which pads
  connect through tracks and zones. Schematic connectivity is in
  `docs/schematic-electrical-inspection.md`.
- [Design updates](../schematic-pcb-updates.md) provide both directions with explicit safety limits.
- No DRC, no zone filling, and no track routing. Not planned. Use `kicad-cli`.
- Custom rule files use `DrcFile`. See `docs/custom-design-rules.md`.
- No plotting, gerber, drill, or 3D export. Not planned. Use `kicad-cli`.
