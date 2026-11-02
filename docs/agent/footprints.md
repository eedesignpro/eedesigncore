# Footprints: `.kicad_mod`

## Read this when

You read, build, or change a `.kicad_mod` file, or a footprint inside a board.
Read `docs/agent/README.md` and `docs/agent/common.md` first.

## Model tree

```
Footprint                       eedesigncore
  .fp_id                        "Library_Name" in a file, "nickname:Name" on a board
  .version, .generator, .generator_version, .layer, .uuid, .at
  .description, .keywords, .attributes, .private_layers
  .properties: list[Property]   Reference, Value, Datasheet, Description, then user
  .pads: list[Pad]              eedesigncore.mod.models
  .drawings: list[PcbDrawItem]  ONE ordered list, interleaving is significant:
                                PcbLine, PcbRect, PcbCircle, PcbArc, PcbPoly,
                                PcbCurve, PcbText, PcbTextBox, Dimension, Table,
                                ReferenceImage, Barcode
  .zones, .groups, .points, .variants
  .models: list[Model3D]
  .path, .sheetname, .sheetfile board context only, empty in a library file
```

Every drawing class lives in `eedesigncore.common.pcb_family`. `Pad` carries
`number`, `pad_type`, `shape`, `position`, `angle`, `size`, `layers`, `drill`,
`net_code`, `net_name`, `pinfunction`, and a padstack. The typed accessors
`fp.tables()`, `fp.images()`, `fp.barcodes()`, and `fp.dimensions()` filter
`.drawings` by kind.

Build a footprint with Reference and Value already seeded:

```python
from eedesigncore import Footprint

fp = Footprint.new("MyLib:R_0402", value="R_0402")
print(fp.properties[0].name, fp.properties[1].value)
```

A bare `Footprint(fp_id=...)` writes no Reference and no Value.

## Older files

A footprint file older than the current format, including a KiCad 5
`module` file, loads the way KiCad 10 loads it. `eedesigncore.mod.legacy`
rewrites the parse tree first, and each rule cites the KiCad 10 parser. Among
the rules:

- `fp_text reference` and `fp_text value` become the Reference and Value
  fields. A hidden user text becomes a user field named `Field<n>`.
- The four mandatory fields are added when the file lacks them.
- `tstamp` is the uuid, `(at x y unlocked)` is `(unlocked yes)`, and a bare
  `hide` or `locked` is `(hide yes)` or `(locked yes)`.
- Legacy arcs, `(width)` strokes, the old fill flags, inch 3D offsets and
  `~...~` overbars convert. A zero margin before 9.0 means "inherit".
- A library footprint drops its lock and its position. KiCad clears both
  outside a board.

A current file is kept as written. The writer then writes the KiCad 10
format, so `to_file` upgrades the file. On 538 older footprints of one user
library, the output equals `kicad-cli fp upgrade` apart from KiCad's random
uuids and, for five files, the footprint name: KiCad takes the name from the
file name.

A footprint inside a board has no version of its own. It takes the board's.

## Pad order

The writer sorts pads the way KiCad sorts them at save. Keys, in sequence:
the pad NUMBER by natural order, then the footprint-relative position, then
size and shape per padstack layer, then the layer set, then the uuid.

Renumbering a pad MOVES it in the file. That is what KiCad writes.

Two pad numbers that differ as strings but tie under natural order, such as
`"01"` and `"1"`, fall through to the position key.

Drawings, zones, groups, points, variants, and 3D models each sort under their
own KiCad rule. Ties keep encounter order.

## Property order

Reference, Value, Datasheet, and Description lead, in that order, wherever the
file put them. User properties follow in file order. See
`docs/agent/common.md § Properties and field order`.

## Coordinates

A standalone `.kicad_mod` file sits at the origin with no rotation, so file
coordinates are board coordinates there.

A footprint on a board stores each child position RELATIVE to the parent
footprint `at`, while KiCad compares text and drawings in ABSOLUTE board
coordinates. The writer rebuilds that frame from `Footprint.at`, so a rotated
board footprint sorts its drawings as KiCad does. Pads are the exception:
KiCad compares them footprint-relative, which is the file value.

## Operations

| Call | Result |
|---|---|
| `extract_footprint(board, reference="C10")` | A library-clean copy: no placement, no path, no pad nets, no nickname, uuids stripped group-aware. |
| `inject_footprint(board, fp, at=(x, y))` | A placed copy on the board, with fresh uuids by default. |
| `render_footprint_svg(fp, layers=..., view_box=..., style=...)` | One SVG string per requested layer, keyed by layer name. See `docs/svg-library-previews.md`. |
| `footprint_bbox(fp)` | A `BoundingBox` in millimeters, or `None`. |

`net_map` assigns pad nets by name, keyed on the pad `pinfunction` when the pad
has one and the pad `number` otherwise. Full signatures in
`docs/operations.md § extract_footprint`.

## Gaps in this scope

- No DRC and no clearance checking. Not planned. Use `kicad-cli pcb drc`.
- No courtyard, silkscreen, or KLC convention checking. Not planned here.
- No 3D model resolution. `Model3D.path` stays the text the file holds,
  variables unexpanded.
- No update-from-library and no library-wide rename.
- Pad shape geometry per layer comes from `pad_layer_geometry` in
  `eedesigncore.render`. That module is outside the supported root API.
