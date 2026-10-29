# Operations reference

This page describes the operations that change or draw KiCad content in memory.
They move footprints and symbols between files, copy and transform board
content, manage uuids, resolve library tables, find parts, and draw boards to
SVG. The caller decides when to save a file.

Contents:

- [One calling contract](#one-calling-contract) for the six part-moving operations
- Footprints and boards: [`extract_footprint`](#extract_footprint),
  [`inject_footprint`](#inject_footprint), [`inject_board`](#inject_board),
  [`new_board_from`](#new_board_from),
  [`rename_nets`](#rename_nets-and-rename_footprint_nets), and
  [`derive_uuids`](#derive_uuids)
- [Board manipulation](#board-manipulation): move, turn, flip, measure, and
  zone fill records
- Symbols: [`extract_symbol`](#extract_symbol), [`inject_symbol`](#inject_symbol),
  [reference auto-annotation](#reference-auto-annotation), and
  [collision rename](#collision-rename)
- [`strip_uuids` and `generate_uuids`](#strip_uuids-and-generate_uuids)
- [Library nickname resolution](#library-nickname-resolution) and
  [finding a part](#finding-a-part), including part databases
- [A worked round trip](#a-worked-round-trip)
- [Board to SVG rendering](#board-to-svg-rendering),
  [schematic SVG](#schematic-svg-from-memory), and
  [raw and resolved text](#raw-and-resolved-text)

The six part-moving operations and the two uuid operations import from the
package root:

```python
from eedesigncore import (
    extract_footprint,
    extract_symbol,
    generate_uuids,
    inject_footprint,
    inject_symbol,
    strip_uuids,
)
```

## One calling contract

All six operations follow the same calling rules.

- The container model comes first, positionally. A `Board` or a `Schematic`.
- The item model comes second, positionally, for the two inject calls.
- Every other argument is keyword-only.
- `extract_*` returns a detached copy and never changes the source container.
- `inject_*` appends to the container and returns the appended model. The
  incoming library model is never changed, because inject copies it first.
- `strip_uuids` and `generate_uuids` change the model in place and return
  `None`.

None of the six touches the disk. They are pure model transforms, so the caller
decides when to write.

## `extract_footprint`

```
extract_footprint(
    board: Board,
    *,
    reference: str | None = None,
    uuid: str | None = None,
    clear_placement_state: bool = True,
) -> Footprint
```

Lift one footprint out of a board and return a library-clean copy, ready to
save as a `.kicad_mod` file.

Pass exactly one of `reference` and `uuid`. Passing both, or neither, raises
`ValueError`. An unknown reference or uuid raises `KeyError`.

The transform removes everything that belongs to the board rather than to the
library: the placement, the path, the sheet name and sheet file, the pad nets,
the zone nets, and the library nickname on the footprint identifier. It also
strips uuid values, group-aware, so a grouped footprint survives.
`clear_placement_state=False` keeps the `locked` and `placed` flags.

```python
from eedesigncore import Board, extract_footprint

board = Board.from_file("board.kicad_pcb")
part = extract_footprint(board, reference="C10")
print(part.fp_id, part.at, len(part.pads))
part.to_file("C_0201_0603Metric.kicad_mod")
```

## `inject_footprint`

```
inject_footprint(
    board: Board,
    footprint: Footprint,
    *,
    at: tuple[float, float],
    rotation: float = 0.0,
    reference: str | None = None,
    net_map: dict[str, str] | None = None,
    path: str | None = None,
    lib_nickname: str | None = None,
    lib_table: ResolvedLibTable | None = None,
    regenerate_uuids: bool = True,
) -> Footprint
```

Place a library footprint on a board and return the placed copy.

`at` is the position in millimetres and `rotation` is degrees. `reference` sets
the Reference property, which stays as the library value when omitted.

`net_map` assigns pad nets by name, because KiCad 10 references nets by name.
The key is the pad `pinfunction` when the pad has one, and the pad `number`
otherwise. A freshly extracted footprint carries no pin functions, so the pad
number is the reliable key.

`path` is the KiCad `(path)` token. It holds the full KIID path of the matching
schematic symbol. It is not the plain sheet path that `inject_symbol` takes.

`regenerate_uuids=True`, the default, mints a fresh uuid for the footprint and
for every child item. `False` keeps the library child uuids and still mints the
footprint uuid.

```python
from eedesigncore import Board, Footprint, inject_footprint

board = Board.from_file("board.kicad_pcb")
part = Footprint.from_file("C_0402_1005Metric.kicad_mod")

placed = inject_footprint(
    board,
    part,
    at=(100.0, 50.0),
    rotation=90.0,
    reference="C99",
    lib_nickname="Capacitor_SMD",
    net_map={"1": "GND", "2": "+3V3"},
)
print(placed.fp_id, placed.at.position, placed.pads[0].net_name)
board.to_file("board.kicad_pcb")
```

## `inject_board`

```
inject_board(target: Board, source: Board, *, offset=(0.0, 0.0), rotation=0.0,
             rotation_center=(0.0, 0.0), net_map=None, instance_key=None,
             uuid_namespace=DERIVED_UUID_NAMESPACE,
             regenerate_uuids=True) -> InjectedContent
```

Copies the CONTENT of `source` into `target` at a placement, and returns the
items it added. `source` is not modified.

Content means the item streams: footprints, drawings, points, tracks, zones,
groups, generators and targets, plus any embedded file those items need. Board
SETTINGS do not travel; `new_board_from` carries those.

The copy is turned by `rotation` degrees about `rotation_center`, then moved by
`offset` millimetres. Both steps follow the rules `rotate_board` and
`translate_board` follow, so a footprint child keeps its footprint-local frame
and a quarter turn stays exact. A turn that is not a multiple of 90 is refused
when the source holds a rectangle, and nothing on the target changes.

A net is a NAME in KiCad 10. Copying content that names `GND` into a board that
already names `GND` JOINS the two nets electrically. Pass `net_map` to rename
the copy's nets on the way in; the target is untouched.

Every KiCad item needs its own uuid, so the copy cannot keep the source's.
Three behaviours:

| Arguments | Uuids |
|---|---|
| `instance_key="A"` | derived: `uuid5(namespace, f"{old}\|A")`, reproducible |
| default | a fresh random uuid for every item |
| `regenerate_uuids=False` | the source uuids ride along unchanged |

`instance_key` with `regenerate_uuids=False` raises: the two contradict.

Group and generator member references are remapped with the rest, so a group
still names its members after the copy.

```python
from eedesigncore import Board, inject_board, new_board_from

source = Board.from_file("board.kicad_pcb")
stepped = new_board_from(source)
for column in range(2):
    inject_board(
        stepped,
        source,
        offset=(column * 120.0, 0.0),
        instance_key=f"{column}|0",
        net_map={name: f"{name}_{column}" for name in source.net_names},
    )
stepped.to_file("stepped.kicad_pcb")
```

The same source, the same keys and the same offsets give the same bytes on
every run and on every machine.

## `new_board_from`

```
new_board_from(source: Board) -> Board
```

Returns an EMPTY board carrying the settings of `source`, deep-copied: the file
version and generator, the `general` block, the page, the title block, the board
properties, the layer table, the `setup` block and its stackup, the
`embedded_fonts` flag, and the board variants.

Content does not travel. Neither do embedded files; `inject_board` carries the
ones its items need.

The PAGE travels with the rest, and a board built from many copies of one source
is usually bigger than that page. Set `paper` on the result when it is.

Netclasses are not on the board. They live in the `.kicad_pro` project file,
which this library round-trips separately: copy that file.

## `rename_nets` and `rename_footprint_nets`

```
rename_nets(board: Board, mapping: dict[str, str]) -> None
rename_footprint_nets(footprint: Footprint, mapping: dict[str, str]) -> None
```

Applies an old-name to new-name map to every item that references a net: tracks,
arcs and vias; board zones and footprint zones; footprint pads; and copper
graphics at board level and inside a footprint. A name absent from the map is
left alone, so a partial map renames only what it names.

Two items also hold a LEGACY numeric net code beside the name, `Zone.net` and
`Pad.net_code`. A stale code beside a renamed name is a mixed reference, so a
rename clears it.

`board.net_names` reads the result back.

Suffixing, prefixing and every other naming scheme is the caller's policy. These
operations only apply the map they are given.

## `derive_uuids`

```
derive_uuids(obj, *, key: str, namespace: UUID = DERIVED_UUID_NAMESPACE) -> None
```

Replaces every uuid in `obj` with `uuid5(namespace, f"{old}|{key}")`, in place.
The result is a function of its inputs alone, so the same source and the same
key give the same uuids on every run and on every machine. That is the whole
difference from `generate_uuids`, which mints a random uuid4.

`key` is a caller string that names the copy. The library never invents one.

An item with NO uuid keeps none, because there is nothing to derive from. Call
`generate_uuids(obj)` afterwards to fill those in.

Every uuid reference list is remapped with the rest, so a group still names its
members and a generator still names the items it owns.

KiCad accepts a version-5 uuid: its reader takes any well-formed value and never
inspects the version nibble (`common/kiid.cpp`).

`DERIVED_UUID_NAMESPACE` is fixed within a release. Release 0.38.0 changed it
once, so derived uuids made by 0.37.4 or earlier differ from those made now.
Pass your own `namespace` to keep derived values stable across releases, or
when you need a separate derivation space.

## Board manipulation

Every operation in this section changes the model in place and returns `None`,
unless its own heading says otherwise. Coordinates are millimetres and angles
are degrees.

### The coordinate frames

The rules below come from the KiCad 10.0.3 board writer, which decides the frame
per item type. They were checked against 256 saved boards.

MOST footprint children are stored footprint-local and DE-ROTATED, so the
placement already carries the move and the turn for them. Six kinds of child hold
BOARD coordinates whatever their parent, and each one moves and turns on its own:

- a `Zone`, including its outline and every fill record,
- a `PcbPoint`,
- a `Dimension`,
- a `ReferenceImage`,
- a `Barcode`,
- the `render_cache` glyph outlines of any text.

A turn changes the angles that are board-absolute. A pad angle and a footprint
text angle take the turn. A text-box angle is footprint-local and does not.

### `translate_board`, `translate_footprint`, `translate_items`

```
translate_board(board: Board, delta: tuple[float, float]) -> None
translate_footprint(footprint: Footprint, delta: tuple[float, float]) -> None
translate_items(items: Iterable[object], delta: tuple[float, float]) -> None
```

Moves every coordinate by `delta`. Coverage is every board item type: footprint
placements, drawings, points, tracks, arcs, vias, zone outlines and fill records,
dimensions, tables, reference images, barcodes, targets, generator geometry, and
the cached glyph outlines of every text.

Every coordinate converts to internal units, moves as an integer, and converts
back, so a moved value is one KiCad could have written. `setup.aux_axis_origin`
and `setup.grid_origin` do not move: they are board-frame settings, not content.

### `rotate_board`, `rotate_footprint`, `rotate_items`

```
rotate_board(board: Board, angle: float, center=(0.0, 0.0)) -> None
rotate_footprint(footprint: Footprint, angle: float, center=(0.0, 0.0)) -> None
rotate_items(items: Iterable[object], angle: float, center=(0.0, 0.0)) -> None
```

Turns by `angle` degrees about `center`. A quarter turn is EXACT: KiCad's own
`RotatePoint` takes a branch with no trigonometry for 0, 90, 180 and 270, so no
coordinate picks up a rounding error. Any other angle takes the same
trigonometric branch KiCad takes, with the same rounding.

A turn that is not a multiple of 90 is REFUSED when the selection holds a
rectangle, and nothing changes before the refusal. KiCad tessellates a
non-cardinally rotated rectangle into a polygon with a curve-flattening error
budget this library does not reproduce.

A turn DROPS every `render_cache`. KiCad clears the same cache on an angle change
and rebuilds the glyphs from the outline font before it writes; this library has
no font engine, so it writes no cache, and KiCad rebuilds it on its next save.
The cache is derived data and the file is valid without it.

`rotate_footprint` is KiCad's `FOOTPRINT::Rotate`: move, turn, then keep every
text upright. The upright step can add a half turn to a text and swap BOTH its
justifications, so a label still reads left to right.

### `flip_footprint`

Flips one footprint to the other side of the board, the way KiCad's `Flip`
command does. Ported from `FOOTPRINT::Flip` (`pcbnew/footprint.cpp:2931-3000`)
and checked byte for byte against boards that KiCad 10.0.4 flipped.

```
flip_footprint(footprint: Footprint, direction="left_right", center=None, board=None) -> None
```

`direction` takes KiCad's own two words, `"left_right"` or `"top_bottom"`.
**`"left_right"` is KiCad's shipped default**, whatever its settings key
suggests: `FLIP_DIRECTION` orders `LEFT_RIGHT` first
(`include/core/mirror.h:26-30`), so it casts to `false`, and the key that stores
it is named `editing.flip_left_right` with `false` meaning left-right
(`pcbnew/pcbnew_settings.cpp:151-153`). The name reads as the opposite of its
value.

`center` defaults to the footprint's own placement, which is what KiCad uses for
a single selected item (`edit_tool.cpp:2843-2849`). A footprint flipped about
its own anchor does not move. KiCad uses the bounding-box centre for a multiple
selection instead, which this library does not reproduce.

`board` supplies the opposite-layer table. KiCad computes that table from the
board rather than storing it, so the same layer can flip to different places on
two boards. Pass the board whenever you have one. Without it the fixed table
applies and no user layer pairs.

The order of the operation is not an implementation detail:

1. Mirror the placement Y about `center` and negate the orientation. This runs
   for BOTH directions.
2. Flip every child in the footprint-local frame, top to bottom.
3. For `"left_right"` only, turn the whole footprint a half circle about
   `center`. That turn carries `KeepUpright`, so a LOCKED field can come back at
   the angle it started from while an `unlocked` field keeps the half turn.

#### What this operation does not promise

**The flipped footprint is byte-exact. The board around it may re-sort.** KiCad
orders the copper stream by a net code that exists only in memory. The board
file stores no net code, so no reader can replay one. A flip moves pads to the
other side, which changes what touches what, so KiCad rebuilds connectivity and
renumbers. Six nets changed number on the measured exemplar and the copper
stream re-sorted underneath.

Reproducing that renumbering needs KiCad's in-session net assignment, which is a
property of the editing history rather than of the file.

**A copper pour keeps its stored fill.** KiCad refills a pour on save when the
copper changed, and this library has no filler. The fill records a flip leaves
behind are the ones the file already held. They are stale, and KiCad rebuilds
them on its next save. This library leaves the stale fill records in place. It
does not delete data the caller did not ask to lose.

**Only `"left_right"` is measured.** Three boards KiCad 10.0.4 produced pin the
left-right flip byte for byte. `"top_bottom"` is a source port with no exemplar
behind it, because KiCad's shipped default is left-right and the reference
boards were made with the default setting. The same is true of a trapezoid pad
and a pad with an offset drill: the code handles both and no reference board
carries either.

**Mirroring is not provided.** KiCad has no footprint mirror operation
(`pcbnew/board_item.cpp:391-394`). Its Flip command is the only way it moves a
footprint to the other side. To reflect a whole unrouted board, use
`flip_board`, described below.

### `flip_board`

```
flip_board(board: Board, *, center: tuple[float, float], direction="left_right") -> None
```

Reflects supported board content about one datum and swaps its layers. The
caller must supply one board-coordinate datum. The operation includes root
drawings and all footprints. It does not save a file. Use `rotate_board`
afterward when a quarter turn is needed.

The helper supports basic pads and line, arc, circle, rectangle, polygon, and
text items. It rejects routing, zones, advanced pads, and other unsupported
graphics before it changes anything. It stages the changes before it replaces
the board content lists. Get nested item references again after the operation.
Board settings, UUIDs, nets, groups, and model records remain unchanged. It does
not replace `flip_footprint` for a single footprint.

### `set_footprint_orientation`

```
set_footprint_orientation(footprint: Footprint, angle: float) -> None
```

KiCad's OTHER turn operation, `SetOrientation`: it turns the children about the
unmoved placement and applies no upright correction. KiCad reaches for this one
when it writes a footprint to a library, and so do `inject_footprint(rotation=)`
and `extract_footprint`, which is what makes those two inverse.

### `board_bbox`, `footprint_bbox`, `items_bbox`

```
board_bbox(board, *, layers=None, include_width=True) -> BoundingBox | None
footprint_bbox(footprint, *, layers=None, include_width=True) -> BoundingBox | None
items_bbox(items, *, layers=None, include_width=True) -> BoundingBox | None
```

Returns the extent in millimetres, or `None` when nothing was measured.
`layers={"Edge.Cuts"}` is how a caller asks for the board outline.
`include_width=False` drops the half-stroke-width growth.

The box is a STATED rule, not KiCad's own box, which needs glyph metrics from a
font this library does not load. For the extent of plotted content on selected
layers, see [plot content bounds](plot-content-bounds.md).

Included: shape geometry, with the TRUE extent of an arc rather than its three
stored points; half the stroke or track width; the cached glyph outlines of a
text that has a `render_cache`; the turned `size` rectangle of a barcode; and a
pad, measured as below.

#### A pad

`PAD::BuildEffectiveShapes` is the rule. A pad's extent is the union of three
things, and reading the top-level `size` alone gets each one wrong:

| Part | Why it matters |
|---|---|
| copper on EVERY padstack layer | nothing says the front is the widest. An inner layer often is |
| the HOLE | a drill can be wider than its copper, which is the common case for a mechanical slot |
| every custom primitive | a custom pad's shape lives there, not in `size` |

The copper and the hole are measured about DIFFERENT centres. The `(offset ...)`
inside `(drill ...)` moves the copper away from the pad position, and the hole
stays put.

`items_bbox` accepts a bare pad. A pad stores a footprint-relative position, so
measuring it alone gives the footprint frame. Pass the footprint to measure it on
the board.

Excluded: the glyph extent of a text with no cache, which contributes its anchor
point only; the pixel extent of a reference image, whose size lives inside the
encoded data; and the curve of a Bezier, which its four control points bound but
do not bound tightly.

A placed footprint is measured in BOARD coordinates: every child maps through the
placement first. A library footprint, which has no placement, is measured in its
own frame.

### `item_layers` and `items_on_layers`

```
item_layers(item) -> list[str]
items_on_layers(items, layers) -> list[...]
```

`item_layers` returns the layer names an item sits on, empty when it names none.
`items_on_layers` filters a collection, and `layers=None` means no filter.

This is the selection every `layers=` argument above applies, exposed so a caller
never restates the rule. The rule is one line of KiCad and easy to get wrong: a
MULTI-LAYER shape emits `(layers …)` instead of `(layer …)`, and `layer` means
nothing then. A via names its start and end layers the same way.

### `extract_fills`, `replace_fills`, `flatten_fills`

```
extract_fills(zone: Zone) -> list[ZoneFilledPolygon]
replace_fills(zone: Zone, fills: list[ZoneFilledPolygon]) -> None
flatten_fills(zone: Zone) -> list[PcbPoly]
```

A zone's fill is STORED, one `(filled_polygon …)` record per outline, and this
library reads and writes those records verbatim. That makes three operations
possible without a zone filler. NONE of them fills a zone: computing a fill needs
the clearance engine, the thermal-relief rules and the whole DRC model.

`extract_fills` returns detached deep copies, safe to hand to another board.

`replace_fills` is the transplant. It deep-copies the records in and flags the
zone filled, because no corpus zone carries a record while flagged unfilled:
11714 zones on 166 boards, zero exceptions. An empty list clears the records and
leaves the flag, because a filled zone whose fill came to nothing is a real state
that 296 corpus zones are in. Nothing checks that a record's layer suits the
zone; the caller owns that.

`flatten_fills` returns board-level copper graphics that no later refill can
move. The caller appends them and mints their uuids. Five rules, all from the
KiCad 10.0.3 plot path:

1. ONE graphic per stored outline, no merging.
2. Stroke width EXACTLY zero.
3. Solid fill.
4. An ISLAND record carries NO net, which puts it on the NonConductor aperture
   attribute exactly as the zone's own island plots.
5. BOARD level only, and never a solder-mask property. A copper graphic inside a
   footprint takes the component and ETCHEDCMP attributes instead, and a
   solder-mask property would widen the shape onto the mask layer.

A gerber plot of a flattened fill carries the same layer, the same vertices and
the same Conductor and NonConductor attribution as the zone it came from. ONE
difference is not reproducible: a net-less graphic plotted after a net-carrying
one inherits the earlier `%TO.N` net attribute, because the zone path clears the
attribute between the main area and the islands and the graphic path does not.
The consequence is metadata only. It is measured in
`tests/integration/test_mutation_fidelity.py`.

## `extract_symbol`

```
extract_symbol(sch: Schematic, *, lib_id: str) -> Symbol
```

Lift one `(lib_symbols)` entry out of a schematic and return a copy ready to
nest into a `SymbolLib`.

`lib_id` is the full `Lib:Name` key exactly as the schematic stores it. An
unknown key raises `KeyError`. The returned symbol carries the bare name,
because a `.kicad_sym` entry holds no nickname.

```python
from eedesigncore import Schematic, SymbolLib, extract_symbol

sch = Schematic.from_file("schematic.kicad_sch")
lib_id = sch.lib_symbols[0].name          # the full "Lib:Name" key, as stored
symbol = extract_symbol(sch, lib_id=lib_id)

lib = SymbolLib(symbols=[symbol])
lib.to_file("Extracted.kicad_sym")
print(lib_id, "->", symbol.name)
```

## `inject_symbol`

```
inject_symbol(
    sch: Schematic,
    symbol: Symbol,
    *,
    at: tuple[float, float],
    rotation: float = 0.0,
    unit: int = 1,
    body_style: int = 1,
    reference: str | None = None,
    project_name: str | None = None,
    sheet_path: str | None = None,
    lib_nickname: str | None = None,
    lib_table: ResolvedLibTable | None = None,
) -> PlacedSymbol
```

Register a symbol definition in `sch.lib_symbols` and place an instance of it.
The call returns the placed symbol and appends it to `sch.symbols`.

`at` is the position in millimetres and `rotation` is degrees. `unit` selects
the unit of a multi-unit symbol, and `body_style` selects the body style, where
1 is the normal style and 2 is the De Morgan alternative.

`project_name` defaults to the project name of an existing placed symbol, and
falls back to an empty string. `sheet_path` defaults to the root path `/`.

```python
from eedesigncore import Schematic, SymbolLib, inject_symbol

lib = SymbolLib.from_file("Device.kicad_sym")
resistor = next(s for s in lib.symbols if s.name == "R")

sch = Schematic.from_file("schematic.kicad_sch")
placed = inject_symbol(
    sch,
    resistor,
    at=(120.0, 90.0),
    rotation=90.0,
    lib_nickname="Device",
)
print(placed.lib_id, placed.fields[0].value)
sch.to_file("schematic.kicad_sch")
```

## `strip_uuids` and `generate_uuids`

```
strip_uuids(obj: BaseModel, *, group_aware: bool = True) -> None
generate_uuids(obj: BaseModel, *, only_missing: bool = True) -> None
```

Both walk the whole model tree and change it in place.

`strip_uuids` clears uuid values. With `group_aware=True`, the default, it
keeps every uuid a group references. Passing `group_aware=False` **destroys
groups**: KiCad drops a member whose uuid is gone, and it then deletes the
emptied group on the next save. Pass `False` only for a model you know carries
no groups.

`generate_uuids` mints uuid version 4 values. With `only_missing=True`, the
default, it fills gaps and leaves existing values alone. With
`only_missing=False` it regenerates every uuid and remaps group member
references together, so no reference dangles.

Both calls are a no-op on the symbol family, because a `.kicad_sym` file holds
no uuid values.

The field carrying the value is `uuid` on a root item such as a footprint, and
`id` on a child item such as a pad. Both calls handle either name.

```python
from eedesigncore import Board, extract_footprint, generate_uuids, strip_uuids

board = Board.from_file("board.kicad_pcb")
part = extract_footprint(board, reference="C10")
print(part.uuid, all(pad.id is None for pad in part.pads))

generate_uuids(part)
print(all(pad.id is not None for pad in part.pads))

strip_uuids(part)
print(all(pad.id is None for pad in part.pads))
```

## Library nickname resolution

A `.kicad_mod` file holds a bare footprint name, and a board needs
`nickname:name`. The same asymmetry applies to symbols. Three sources can
supply the nickname, and both inject calls apply them in this order.

1. A pre-qualified name passes through untouched. A name containing a colon is
   already qualified.
2. An explicit `lib_nickname` wins next.
3. A `lib_table` match comes third. The resolver searches the discovered
   libraries and takes the first one holding the item.
4. The placeholder nickname `eedesigncore` covers the no-match case.

Build the table with `resolve_lib_table`. It discovers the global tables under
the KiCad user settings directory and the project tables beside the
`.kicad_pro` file, expands the URI variables, and merges the two tiers with the
project tier winning.

```python
from eedesigncore import resolve_lib_table

footprints = resolve_lib_table("fp", project_dir=".")
symbols = resolve_lib_table("sym", project_dir=".")
print(footprints.kind, len(footprints), symbols.kind, len(symbols))
```

Pass the result straight to an inject call:

```python
from eedesigncore import Board, Footprint, inject_footprint, resolve_lib_table

table = resolve_lib_table("fp", project_dir=".")
board = Board.from_file("board.kicad_pcb")
part = Footprint.from_file("C_0402_1005Metric.kicad_mod")

placed = inject_footprint(board, part, at=(10.0, 10.0), lib_table=table)
print(placed.fp_id)
```

Six details matter in practice.

- A nested table row, of type `Table`, points at another table file. The
  resolver loads it recursively into one flat nickname namespace. A global
  table that holds only nested rows resolves to zero libraries without
  recursion.
- A symbol library has two shapes and both are read. One `.kicad_sym` file
  holding every definition, or a DIRECTORY holding one `.kicad_sym` per
  definition. The directory is not searched recursively, and the files are read in sorted
  order, so a name defined in two files resolves the same way every run.
- A missing, unreadable, or malformed table contributes nothing and raises
  nothing, which is what KiCad does. Pass `strict=True` to turn each of those
  cases into `LibTableResolutionError`.
- The `${KICAD10_*}` variables are compiled into KiCad and never written to
  disk. The resolver hardcodes the same defaults from the install root.
  User-defined variables come from `kicad_common.json`.
- Project text variables outrank environment variables in KiCad. They live in
  the `.kicad_pro` file, and the resolver takes them as the
  `project_text_variables` argument. Left unset, a URI naming one stays
  unexpanded.
- The resolver reads inside a confinement, and it expands KiCad variables only.
  See the section below.

### Confinement and variable expansion

A library table is data, and a project directory is often a repository you did
not write. Two guards follow from that, and both are on by default.

Expansion reads the KiCad variable set only: any name with the `KICAD` prefix,
plus `KIPRJMOD`, `KISYS3DMOD`, and `KISYSMOD`. A URI naming anything else, such
as `${CI_JOB_TOKEN}`, keeps its text and expands to no value. Define a name of
your own in `kicad_common.json` or pass it as a project text variable.

Reads are confined to a root set per tier. The global tier reads under the KiCad
install root and the user settings directory. The project tier reads under the
project directory. Both tiers also read every KiCad path variable that names an
existing local directory, which is what makes a library under `${PARTS_PATH}`
readable with no extra argument. The confinement covers nested table files and
the library files an item search opens. A UNC path or a URL scheme is refused
before the path is resolved, because resolving one opens a network connection on
Windows.

The path-variable roots are on by default. Without them, a library under a path
variable resolves and then reads nothing. The variables come from your own
`kicad_common.json`, so this grants what your KiCad grants and nothing more. A
value that carries an unexpanded reference, a URL scheme, a UNC path, or that
does not name a directory is refused. Pass `trust_path_variables=False` to turn
these roots off.

#### Explicit path variables

`LibTableEnvironment.discover`, `LibTableResolver.discover`, and
`resolve_lib_table` accept optional `path_variables`. Supply a mapping of string
names to string values captured from a trusted KiCad configuration.

```python
from eedesigncore import resolve_lib_table

table = resolve_lib_table(
    "fp",
    project_dir=".",
    path_variables={"PARTS_PATH": "."},
)
```

The mapping overrides saved values and filtered process values before path
expansion. The supplied project directory still determines `KIPRJMOD`. Project
text variables keep their higher precedence. The call copies the mapping and
does not change the process environment. Invalid mapping types, variable names,
and NUL characters raise errors.

The directory restrictions above still apply. With `trust_path_variables=True`,
a local absolute path value that names an existing directory can become a
global read root. Use `trust_path_variables=False` and explicit `allowed_roots`
when the caller must constrain those roots. The mapping does not allow
project-table reads outside their roots. Remote addresses and network-share
paths do not become trusted roots through this mapping. Automatic process
discovery still imports only the KiCad names. Do not pass process secrets or
untrusted project text as path variables.

Widen the set with `allowed_roots`, which adds to the default and never
subtracts from it. A shared library store outside both default roots needs one:

```python
from eedesigncore import resolve_lib_table

table = resolve_lib_table(
    "fp",
    project_dir=".",
    allowed_roots=["D:/libraries/shared"],
)
```

A table file outside the roots is skipped, like any other unreadable table, and
`strict=True` turns the skip into `LibTableResolutionError`. A library file
outside the roots reports that it holds nothing, so a lookup falls through to
the next library.

**Every skip is recorded.** `ResolvedLibTable.skipped` names each table file and
nested row the resolve did not read, with the tier and a reason. Check it
whenever a nickname you expected is missing: an absent file, a malformed file
and a confinement miss all subtract from the namespace without raising.

```python
from eedesigncore import resolve_lib_table

table = resolve_lib_table("sym")
for skip in table.skipped:
    print(skip.scope, skip.path, skip.reason)
```

A nested table row of the GLOBAL tier is followed even when it points outside
the roots, and the folder holding it becomes readable for the libraries it
names. The global tables are your own KiCad configuration, so a row in them is
granted the same standing as a path variable. A PROJECT table is not widened,
because a project is often a repository you did not write, and a UNC path or a
URL scheme is refused on both tiers. `trust_table_chain=False` refuses such
rows. Each refused row is then recorded in `skipped`.

Passing the wrong table kind raises `ValueError`. `inject_footprint` needs an
`fp` table, and `inject_symbol` needs a `sym` table.

## Finding a part

`resolve_lib_table` answers one question: which library holds this exact name.
`eedesigncore.library` answers the other two. What is in a library, and which
of those parts fits a description.

### List what a library holds

```python
from eedesigncore.library import iter_library_records, library_item_names

names = library_item_names("Device.kicad_sym")
print(len(names), names[:3])

for record in iter_library_records("Device.kicad_sym", nickname="Device"):
    if record.name == "R":
        print(record.lib_id, record.description, record.keywords)
        break
```

Point the same two calls at a directory and they read the folder form.
`library_item_names` then returns one name per `.kicad_sym` file in the folder.

A `PartRecord` is the one shape a part takes, whether it came from a file or
from a database row: the name, the library nickname, every field by name, the
keywords, the footprint filters, and the file it was parsed from.

### Walk a whole table

```python
from eedesigncore import resolve_lib_table
from eedesigncore.library import iter_table_records

table = resolve_lib_table("sym")
records = list(iter_table_records(table, nicknames=["Device"]))
print(len(records))
```

Drop `nicknames` and the walk covers every readable library in the table. For
the whole KiCad stock set, that takes about a minute.

**Listing parses, so name the libraries you want.** Measured with
`SymbolLib.from_file`: 0.61 s for `Device.kicad_sym` (2.59 MB, 538 symbols),
1.31 s for `Connector.kicad_sym` (5.44 MB, 401 symbols), so about 0.24 s per
megabyte. The whole KiCad stock set is 222 files and 244 MB, which is about a
minute.

Every function is a generator and nothing is cached. KiCad keeps its library
cache in memory only and writes no cache file, and this library holds no cache
at all. If you need the list twice, keep the list.

Three kinds of row contribute nothing and raise nothing: a row that is not a
`KiCad` row, a row outside the read confinement, and a row whose path does not
exist.

### Rank and filter

Two halves, because there are two kinds of question.

```python
from eedesigncore.library import FieldTest, iter_library_records, search_records

parts = list(iter_library_records("Device.kicad_sym", nickname="Device"))

# Free text, ranked.
for match in search_records(parts, "thermistor", limit=3):
    print(match.score, match.lib_id, [(r.term, r.kind) for r in match.reasons])

# Exact, as field tests. Every field of every record is testable by name.
powered = search_records(
    parts,
    tests=[FieldTest(field="Reference", op="equals", value="R")],
)
print(len(powered))
```

Against a part database the same call reads its parametric columns:

```text
tests=[
    FieldTest(field="Package", op="equals", value="0402"),
    FieldTest(field="Tolerance", op="equals", value="1%"),
    FieldTest(field="lifecycle_status", op="one_of", values=["Active"]),
]
```

The free-text half is KiCad's own search, ported exactly: the library id scores
16, the name 8, the nickname 4, each keyword token 4, and the whole keyword
string, the description and the footprint field 1 each. A query word equal to a
whole term scores 8 times its weight, a match at the start 2 times, a match
anywhere else 1 times. Several words are an AND — a word that matches nothing
drops the part.

Two deliberate differences from KiCad:

- **Every field is searchable.** KiCad reads the six things above and no custom
  field, so `IPN`, `ManufacturerPartNumber`, `Tolerance` and `Package` are
  invisible to it. Here each of them contributes a term at weight 1, and
  `field_weights={"IPN": 12}` raises any of them. KiCad's behaviour is the
  floor, not the ceiling.
- **`key>10k` is not a numeric comparison.** KiCad reads the `k` as a
  multiplier. Reading `10k` as a resistance is part knowledge, and this library
  does not carry part knowledge. Use a `FieldTest` with `gt`, `ge`, `lt` or
  `le`; it reads the field as a plain number, so `10000` compares and
  `10K Ohm` does not.

`/^R_[0-9]+$/` and `R_*` work the way they do in KiCad's chooser.

Every match says why it matched. `match.reasons` names the term, the text, the
kind (exact, prefix or substring) and the points, so a tool can tell an
engineer "matched Tolerance = 1% and Package = 0201" instead of just handing
over a part number.

### Read a part database

A `Database` row in a `sym-lib-table` points at a `.kicad_dbl` file, which
declares an ODBC or SQLite source. **This library never connects.** It reads
the declaration, names the driver you need, and stops.

```python
import json
from pathlib import Path

from eedesigncore.library import load_kicad_dbl

# A .kicad_dbl is JSON. This is the shape, trimmed to one table and one field.
Path("partsdb.kicad_dbl").write_text(
    json.dumps(
        {
            "name": "Example",
            "source": {
                "type": "odbc",
                "connection_string": (
                    "Driver={ODBC Driver 18 for SQL Server};Server=sql-server-01;"
                    "Database=ComponentLibrary;PWD=never-logged;"
                    "Trusted_Connection=yes"
                ),
            },
            "globally_unique_keys": True,
            "libraries": [
                {
                    "name": "Resistors",
                    "table": "Resistors",
                    "key": "KiCadKey",
                    "symbols": "SymbolRef",
                    "footprints": "FootprintRef",
                    "properties": {"description": "AXDescription"},
                    "fields": [
                        {"column": "value", "name": "Value", "visible_on_add": True},
                        {"column": "IPN", "name": "IPN"},
                    ],
                }
            ],
        }
    ),
    encoding="utf-8",
)

declaration = load_kicad_dbl("partsdb.kicad_dbl").declaration()
print(declaration.driver)                 # ODBC Driver 18 for SQL Server
print(declaration.source.server)          # sql-server-01
print(declaration.source.database)        # ComponentLibrary
print(declaration.source.withheld_keys)   # ['PWD'], and the value is withheld

read = declaration.table_for("Resistors")
print(read.table, read.key_column, read.columns)
```

`columns` is the list to select. KiCad itself issues only two statement shapes,
both listed in the module documentation, and the quote character comes from the
driver, which is why this library hands over names instead of writing the
statement.

Every secret reads `<withheld>`, so a declaration is safe to log or paste into
a report.

### Place a part from a row

You run the read; this library turns a row into a symbol the way KiCad does.

```python
from eedesigncore import (
    DatabaseFieldMapping,
    DatabaseProperties,
    DatabaseSubLibrary,
    Schematic,
    inject_symbol,
    resolve_lib_table,
)
from eedesigncore.library import definition_from_row
from eedesigncore.library.catalog import table_symbol_loader

# Normally this comes from load_kicad_dbl(...).sub_library("Resistors").
resistors = DatabaseSubLibrary(
    name="Resistors",
    table="Resistors",
    key="KiCadKey",
    symbols="SymbolRef",
    footprints="FootprintRef",
    properties=DatabaseProperties(description="AXDescription"),
    fields=[
        DatabaseFieldMapping(column="value", name="Value", visible_on_add=True),
        DatabaseFieldMapping(column="IPN", name="IPN"),
    ],
)

row = {  # whatever your driver returned, column name to value
    "KiCadKey": "PN-00457__1788472695932",
    "SymbolRef": "Device:R",
    "FootprintRef": "Resistor_SMD:R_0402_1005Metric;Resistor_SMD:R_0603_1608Metric",
    "AXDescription": "RES_10K_1%_0.05W_0201",
    "value": "10k",
    "IPN": "PN-00457",
}

definition = definition_from_row(
    row,
    resistors,
    globally_unique_keys=True,
    symbol_loader=table_symbol_loader(resolve_lib_table("sym")),
)
print(definition.name)          # PN-00457__1788472695932
print(definition.fp_filters)    # both footprint tokens

schematic = Schematic.from_file("schematic.kicad_sch")
placed = inject_symbol(schematic, definition, at=(50.0, 50.0), lib_nickname="partsdb")
print(placed.lib_id)            # partsdb:PN-00457__1788472695932
```

What the transform does, in KiCad's order:

1. Loads the symbol the row's symbol column names, and copies it.
2. A missing or malformed reference still yields a placeable symbol carrying
   the metadata alone. KiCad does the same and shows the error in its chooser.
3. Renames it to the sanitized key. `PN-01582:116981` becomes
   `PN-01582_116981`, which is the form the corpus shows KiCad writing.
4. Splits the footprint column on `;`, tab, carriage return and newline. The
   first token becomes the Footprint field, every token becomes a footprint
   filter.
5. Applies the description, the keywords, and the three exclude flags, reading
   a boolean permissively. A value that will not read as a boolean is ignored,
   not an error.
6. Walks the field mapping. A column absent from the row is skipped. `Value`
   and `Datasheet` write the built-in fields. Anything else finds or creates a
   user field and takes its hide flag and name-shown flag from the mapping,
   unless the mapping inherits and the field already exists.

**Row values must be strings.** KiCad reads a row value as a string and the
ODBC layer delivers every column as one, so a number reads as empty rather
than being formatted by a rule with no source behind it. Convert numeric
columns before handing the row over.

**Measured** against 309 parts that KiCad placed from a real parts database of
7350 rows: 294 match field for field. In the other 15, the row or the drawn
symbol changed after KiCad wrote the schematic. None is a fault in the
transform.

### Refuse a part with no body

When a row's drawn symbol cannot be found, KiCad builds a symbol carrying the
metadata alone and shows the error in its chooser. A library has no chooser, so
that symbol is placeable and looks complete in the file. Use `resolve_row`
whenever the result is going to be placed:

```python
from eedesigncore import (
    DatabaseFieldMapping,
    DatabaseProperties,
    DatabaseSubLibrary,
    resolve_lib_table,
)
from eedesigncore.library.catalog import table_symbol_loader
from eedesigncore.library.database import resolve_row

resistors = DatabaseSubLibrary(
    name="Resistors",
    table="Resistors",
    key="KiCadKey",
    symbols="SymbolRef",
    footprints="FootprintRef",
    properties=DatabaseProperties(description="AXDescription"),
    fields=[DatabaseFieldMapping(column="value", name="Value", visible_on_add=True)],
)
row = {"KiCadKey": "PN-00457", "SymbolRef": "NoSuchLibrary:NoSuchPart", "value": "10k"}

built = resolve_row(
    row,
    resistors,
    globally_unique_keys=True,
    symbol_loader=table_symbol_loader(resolve_lib_table("sym")),
)
print(built.metadata_only, built.has_pins)
print(built.unresolved_reason)
```

`definition_from_row(..., require_source=True)` raises instead, which is the
one-liner when a bodyless part is never acceptable. The default stays KiCad's
behaviour.

A part placed from a metadata-only definition also annotates as `U`, because
the designator prefix is read off the definition and that definition carries
the `U` KiCad's own constructor seeds. So `metadata_only` is the flag to check
before placing, not the designator.

### Where a placed field sits

A library definition stores a field position as an offset from the symbol
origin. A placed symbol stores a sheet coordinate. `inject_symbol` converts the
way KiCad does: it ADDS the X offset and SUBTRACTS the Y offset. The asymmetry
is KiCad's, not a sign error — its reader flips the sign of Y when it takes a
field out of a library and does not when it takes one out of a schematic, so a
library offset in this model is the file value while KiCad's is the negation.

A hidden field at offset (0, 0) therefore lands exactly on the part, and one
with an offset lands where KiCad puts it. Measured over 579 corpus schematics,
on hidden fields with a non-zero Y offset: 4095 sit where this rule puts them,
48 do not, and those 48 are fields an engineer moved by hand.

Every field also carries a text-style block, because KiCad writes one on every
placed field. A field this library creates takes KiCad's own default, which is
size 1.27 by 1.27 and centred.

**A visible field will not be where KiCad would put it.** When KiCad places a
part it puts each field at the library offset, as above, and then TIDIES the
visible ones: it moves the designator clear of the body, moves the value below
it, and re-aligns both. Call `autoplace_fields` after injection for isolated placement.
See [automatic field placement](field-autoplacement.md).
The helper does not inspect surrounding items. Use [collision checks](schematic-measurements.md) after placement.

Measured against the definition each corpus schematic cached, over 579
schematics and every database part in them:

| field | attribute | agrees with KiCad |
|---|---|---|
| hidden | font size, text angle, alignment | 100.0% |
| hidden | position, before the Y-sign fix | 97.0% |
| hidden | position, after it, non-zero offsets only | 98.8% |
| visible | text angle | 81.0% |
| visible | font size | 87.5% |
| visible | alignment | 49.3% |
| visible | position | 2.0% |

So a part this library places opens, is the right part, and has correct
electrical content and hidden data. Its reference designator sits where the
library drew it. Set the visible positions yourself if the sheet must look
tidy.

Two further differences are recorded rather than fixed, because no reader can
reproduce them: the order of the pin list and the order of the custom fields in
a placed part come from KiCad's in-memory containers, and the file stores the
result without storing the rule. Neither changes what KiCad reads back.

### What this does not do

The boundary: this library decides how part information LANDS in a schematic.
It does not decide which part to use. So it does not connect to anything,
does not write SQL, does not cache rows on disk, does not hold a credential,
and does not judge a part. Knowing that a 1% 0402 from one maker substitutes
for another is choosing, and it belongs to the tool that owns the connection.

## Reference auto-annotation

Omit `reference` on `inject_symbol`, and the call assigns `<Prefix><n>`.

- The prefix comes from the Reference field of the library definition, through
  the KiCad rule that drops every trailing digit and question mark. `R?` gives
  `R`.
- An empty prefix falls back to `U`, which is what KiCad does.
- The number is the lowest free one from 1 upward, counted over every reference
  already in the schematic. A gap is filled before the sequence extends.

```python
from eedesigncore import Schematic, SymbolLib, inject_symbol

lib = SymbolLib.from_file("Device.kicad_sym")
resistor = next(s for s in lib.symbols if s.name == "R")

sch = Schematic.from_file("schematic.kicad_sch")
first = inject_symbol(sch, resistor, at=(10.0, 10.0), lib_nickname="Device")
second = inject_symbol(sch, resistor, at=(20.0, 10.0), lib_nickname="Device")
third = inject_symbol(sch, resistor, at=(30.0, 10.0), lib_nickname="Device")
print(first.fields[0].value, second.fields[0].value, third.fields[0].value)
```

Two limits apply. This is library policy, not a copy of the KiCad annotator, so
a number may differ from the one KiCad would pick. The scan also sees one
schematic file, so it cannot detect a collision on another sheet of the same
project. Pass `reference` explicitly when the caller needs exact control.

The resolved reference lands in both places KiCad writes it: the Reference
field of the placed symbol, and the `(instances … (reference …))` entry.

## Collision rename

`inject_symbol` registers the incoming definition in `sch.lib_symbols` under
its full `Lib:Name` key. Three cases follow, and they match
`SCH_SCREEN::Append` exactly.

1. **The key is free.** The definition is inserted under it.
2. **The key exists and the definition matches.** Nothing is inserted, and the
   placed symbol reuses the cached definition. The comparison ignores the
   top-level name, because that name is context-dependent.
3. **The key exists and the definition differs.** The incoming definition is
   inserted under a fresh **bare** name, `<ItemName>_<n>`, with no nickname.
   `Device:R` becomes `R_1`, then `R_2`, and so on. If an earlier rename
   already holds an identical definition, that entry is reused instead of
   minting a new number.

The placed symbol always points at the resolved key, so a renamed definition
stays wired to its instance.

```python
from eedesigncore import Schematic, SymbolLib, inject_symbol

lib = SymbolLib.from_file("Device.kicad_sym")
resistor = next(s for s in lib.symbols if s.name == "R")
altered = resistor.model_copy(deep=True)
altered.fields[1].value = "10k"

sch = Schematic.from_file("schematic.kicad_sch")
first = inject_symbol(sch, resistor, at=(10.0, 10.0), lib_nickname="Device")
second = inject_symbol(sch, altered, at=(20.0, 10.0), lib_nickname="Device")
print(first.lib_id, second.lib_id)
```

## A worked round trip

Extract a footprint from one board, save it as a library file, and place it on
another board.

```python
from eedesigncore import Board, Footprint, extract_footprint, inject_footprint

source = Board.from_file("board.kicad_pcb")
part = extract_footprint(source, reference="C10")
part.to_file("harvested.kicad_mod")

target = Board.from_file("target.kicad_pcb")
placed = inject_footprint(
    target,
    Footprint.from_file("harvested.kicad_mod"),
    at=(75.0, 40.0),
    reference="C500",
    lib_nickname="harvested",
)
target.to_file("target.kicad_pcb")

print(placed.fp_id, placed.uuid is not None)
```

The source board is unchanged in memory and on disk. The target board gains
exactly one footprint, so its diff shows one added block.

## Board to SVG rendering

For in-memory symbol and footprint previews, see [Symbol and footprint SVG previews](svg-library-previews.md).
Use `render_symbol_svg` and `render_footprint_svg`. Symbol previews accept a caller-supplied font.

`render_board_svg` draws a board to SVG straight from the model. It performs no
file access and it starts no subprocess, so it is a pure function of the board
and its arguments.

See [copper drawing SVG](copper-drawing-svg.md) for saved fills, dash patterns,
and outline text. See [rendered element callback](rendered-element-callback.md)
to receive each emitted SVG fragment with its source item.

```python
from eedesigncore import Board, LayerStyle, ViewBox, load_font, render_board_svg

board = Board.from_file("board.kicad_pcb")
style = LayerStyle(
    colors={"F.SilkS": "#FFFFFF", "Edge.Cuts": "#D0D000"},
    opacity={"F.SilkS": 0.6},
)
pages = render_board_svg(
    board,
    ["F.SilkS", "Edge.Cuts"],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=style,
    font=load_font("Arial"),
)
```

This example uses Arial. Supply the font selected for the board text.

The result maps each requested layer name to one SVG document. This mirrors
`kicad-cli --mode-multi` usage. A caller stacks the documents into one frame.

### The rule is determinism, not byte-identity

Round-trip byte-identity governs `.kicad_pcb` and every other KiCad format,
because engineers diff those files in git. SVG is an output format. Nobody diffs
a generated preview.

The rule here is determinism. One board and one set of arguments always give the
same bytes. Agreement with `kicad-cli` is a test oracle, not a product
requirement.

One consequence is visible in the output. `kicad-cli` writes the current date
into a `<title>` element. This renderer writes none, because that line would
make two renders of one board differ.

### Four layers, and which of them may change

| Layer | Owns | Status |
|---|---|---|
| `render.geometry` | millimeters to SVG path data, at fixed precision | FROZEN |
| `render.traversal` | which items exist, and the host transform | stable |
| `render.style` | color, opacity, filtering, element identity | open |
| `render.document` | the window, the wrapper group, the elements | stable |

Put a new capability in the style layer. The geometry layer is what the
`kicad-cli` oracle measures, so freezing it keeps that oracle valid against
every policy added later.

### The style policy also filters

One hook does both jobs. `style_for` returns `None` to drop an item.

```python
from eedesigncore import ElementStyle


class OneLibraryOnly:
    """Draw the footprints of one library, and nothing else."""

    emit_ids = True
    point_stroke_width_mm = 0.05

    def style_for(self, render_item):
        host = render_item.footprint
        if host is None or not host.fp_id.startswith("Capacitor_SMD:"):
            return None
        return ElementStyle(stroke="#FF0000", stroke_opacity=1.0, fill="none")
```

`LayerStyle` covers the common case. A layer with no color in `colors` drops its
items, so a caller selects content by naming only the layers it wants.

`LayerStyle` keeps the opacity the caller asks for. Nothing collapses an alpha to
1.0.

### Element identity

Set `emit_ids` to write the item uuid into the element `id` attribute. A live
preview uses it to tell which element the user clicked. `kicad-cli` cannot do
this at all.

`CliParityStyle` writes no ids, because `kicad-cli` writes none.

### Content streams

A board keeps its content in separate streams, and one preview rarely wants all
of them. `content` names the streams to draw, and `BoardContent` names the
streams:

| Member | Draws |
|---|---|
| `drawings` | board and footprint graphics, footprint fields, board points |
| `tracks` | copper track segments, track arcs and vias |
| `zones` | copper zones, as filled polygons or as an outline when unfilled |
| `pads` | footprint pad copper, resolved per layer through the padstack |
| `rule_areas` | keep-out zones, which KiCad's own plot skips |

Three sets are provided:

| Set | Holds |
|---|---|
| `DEFAULT_CONTENT` | `drawings` alone. What this renderer has always drawn |
| `KICAD_CONTENT` | every stream KiCad's board plot draws, and nothing else |
| `ALL_CONTENT` | every stream, including the ones past KiCad |

`KICAD_CONTENT` is the set to use for a render that must stay comparable against
`kicad-cli`. `ALL_CONTENT` adds `rule_areas`, which no KiCad export contains.

Copper is opt-in rather than default for two reasons. It is far larger — one
corpus board holds 3188 tracks against 160 footprints — and turning it on
silently would change every render a caller already relies on.

```python
from eedesigncore import ALL_CONTENT, BoardContent, Board, LayerStyle, ViewBox
from eedesigncore import render_board_svg

board = Board.from_file("board.kicad_pcb")
style = LayerStyle(colors={"F.Cu": "#C83434"})

# Copper only: pads and tracks, no silkscreen graphics.
pages = render_board_svg(
    board,
    ["F.Cu"],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=style,
    content=frozenset({BoardContent.pads, BoardContent.tracks}),
)
```

### What this renderer draws

In scope, both board-hosted and footprint-hosted:

- `gr_line`, `gr_arc`, `gr_rect`, `gr_circle` and `gr_poly` outlines, with their
  stroke width, dash pattern and round caps.
- Text: board text, footprint fields such as a reference designator, and a
  dimension's measurement label. Drawn with KiCad's own stroke font unless the
  caller passes `font`.
- Dimensions, including the gap KiCad cuts in the crossbar for the text.
- Copper tracks, track arcs and vias, with `content`.
- Zones, as their filled polygons, with `content`.
- Pads, with `content`. See below.
- Rule areas, with `content`. KiCad draws none, so this goes past KiCad.
- The `point` item. This one goes past `kicad-cli`, which exports nothing for it.

Text boxes support borders, margins, alignment, rotation, wrapped text, and
knockout. Tables, barcodes, PCB targets, Bezier curves, and reference images
also draw.

Out of scope: teardrops and paint order fidelity.

An out-of-scope item is SKIPPED, never fatal. Pass `on_skip` to hear about every
one.

`scripts/render_coverage.py` grades this list against KiCad's own plot path and
prints what is missing. It reads the KiCad sources rather than trusting this
table, so run it instead of trusting this table.

### Pads

Pad copper arrives with `BoardContent.pads`. A pad is the only board item whose
geometry changes per layer, because it carries a padstack, so the same pad can be
a rounded rectangle on the front and a circle on the inner layers.

**For copper, six of the seven pad shapes match `kicad-cli` vertex for vertex**: circle,
rect, oval, trapezoid, roundrect and chamfered rect. `EXACT_PAD_SHAPES` holds
that set and `pad_geometry_is_exact` answers for one pad.

A CUSTOM pad is the exception. A custom pad merges its anchor and every
primitive into one outline before drawing. Curved boundaries are sampled, so a
custom pad can differ from `kicad-cli` by a few vertices.

Saved zone fills do not replace the board connectivity graph that unused-copper
removal needs. The pad and via flashing helpers can report uncertain results for
those settings. The renderer does not compute board connectivity.

Two behaviours are worth knowing because they are easy to get wrong:

- **A rounded rectangle is a POLYGON, not arcs.** KiCad never emits an arc for
  one: every plot path but Gerber converts the shape first, so a 5 x 3 mm pad at
  ratio 0.25 is a 32-vertex path. `common.polygon` ports that approximation
  rather than drawing something geometrically nicer that would agree with nothing.
- **An oval pad is one stroked segment**, its width the minor axis, its ends
  round-capped by the wrapper group. That is what KiCad emits, and it is one
  primitive where an outline would be eight.

```python
from eedesigncore import BoardContent, Board, LayerStyle, ViewBox, render_board_svg
from eedesigncore.render.pads import pad_layer_geometry, pad_geometry_is_exact

board = Board.from_file("board.kicad_pcb")
pages = render_board_svg(
    board,
    ["F.Cu"],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=LayerStyle(colors={"F.Cu": "#C83434"}),
    content=frozenset({BoardContent.pads}),
)

# Which pads on this layer are NOT vertex-exact against kicad-cli.
for footprint in board.footprints:
    for pad in footprint.pads:
        geometry = pad_layer_geometry(pad, "F.Cu")
        if not pad_geometry_is_exact(geometry):
            print(footprint.fp_id, pad.number, geometry.shape.value)
```

### Pad solder mask and solder paste openings

Request `F.Mask`, `B.Mask`, `F.Paste`, or `B.Paste` with `BoardContent.pads`.
The renderer resolves the opening from the pad, footprint, and board settings.
The renderer does not change the board or its copper dimensions.

| Setting | First available value |
| --- | --- |
| Mask clearance | Pad `solder_mask_margin`, footprint `solder_mask_margin`, board `setup.pad_to_mask_clearance`, zero |
| Paste clearance | Pad `solder_paste_margin`, footprint `solder_paste_margin`, board `setup.pad_to_paste_clearance`, zero |
| Paste ratio | Pad `solder_paste_margin_ratio`, footprint `solder_paste_margin_ratio`, board `setup.pad_to_paste_clearance_ratio`, zero |

An absent value inherits. Explicit zero stops inheritance in current files.
For board versions through `20240201`, zero pad and footprint overrides inherit, as KiCad's loader specifies.
The absolute paste clearance and paste ratio resolve independently.
Per-side paste clearance equals the absolute clearance plus the ratio times the pad dimension.
X and Y use their own dimensions before pad rotation. The back uses the back padstack geometry.
Pads with no copper layer assignment receive no clearance expansion.

Circles, ovals, and rectangles use the changed dimensions. Positive rectangle clearance rounds the corners.
Rounded rectangles with equal X/Y clearance change their corner radius by that clearance.
This follows the correction in KiCad 10.0.4. The pinned 10.0.3 source preserves the radius ratio instead.
Unequal X/Y clearances retain the radius ratio, as KiCad does.
Trapezoids and custom pads use the X clearance for a polygon offset.
Chamfered rectangles use a polygon offset for positive equal clearances and changed dimensions otherwise.
A standard aperture disappears when contraction consumes either dimension.
Mask contraction is limited by the anchor dimensions, including for custom pads.
Custom paste contraction has no such clamp.

Custom mask and paste apertures combine their anchor and primitives before the offset.
Compound SVG paths preserve holes and separate regions. Curves and polygon offsets are approximations.
The comparison script checks polygon boundaries to a tolerance of `0.01 mm`.
`pad_geometry_is_exact` describes copper geometry only. It does not promise exact aperture vertices.

The operation reads only the supplied board. It does not load a project file or evaluate custom design rules.
KiCad checks local pad and footprint overrides before custom design rules.
Without a local override, it checks rules for the pad, rules for the footprint, and then board defaults.
Minimum solder mask web merging between nearby openings is also outside this operation.
These limits apply even when the saved board names those settings.
Use a KiCad export when the output must follow custom design rules.
To list the custom rules that can change an aperture, and to get pad outlines
as geometry, see [pad apertures and backdrill depth](pad-apertures-and-backdrill-depth.md).

Run `scripts/check_pad_apertures.py` with `--cli` and `--output` to repeat the constructed-board comparison.
The script saves both exports and measured boundary differences. It rejects CLI errors and invalid XML.

### Vias

A via carries a padstack too, so its diameter can differ per layer.
`via_diameter` picks which width to draw:

| Mode | Draws |
|---|---|
| `ViaDiameter.per_layer` | the width the padstack gives for the layer drawn. The default, and what KiCad plots |
| `ViaDiameter.largest` | the widest ring in the padstack, on every layer |

A via with no padstack draws the same circle either way, which covers almost
every board.

A via also obeys the unused-layer rule through `unconnected_layer_mode`.
`via_flash` applies the part this library can decide. An inner layer under
`remove_all` or `remove_except_start_end` needs board connectivity, so the via
draws and `via_flash(...).certain` returns `False`.

```python
from eedesigncore import BoardContent, Board, LayerStyle, ViaDiameter, ViewBox
from eedesigncore import render_board_svg

board = Board.from_file("board.kicad_pcb")
pages = render_board_svg(
    board,
    ["F.Cu"],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=LayerStyle(colors={"F.Cu": "#C83434"}),
    content=frozenset({BoardContent.tracks}),
    via_diameter=ViaDiameter.largest,
)
```

### Rule areas

A rule area, also called a keep-out zone, is a zone carrying `(keepout ...)` or
`(placement ...)`. `render.traversal.is_rule_area` answers for one zone.

KiCad's plot skips every rule area, so drawing one puts geometry on the page that
no KiCad export holds. That is why it has its own stream. Ask for
`BoardContent.rule_areas` and the render stops matching `kicad-cli` by exactly
those outlines, and by nothing else.

#### Pad selection, and the one answer this library cannot give

A pad draws on a layer when its `(layers ...)` list names that layer AND the pad
FLASHES there. `pad_draws_on` applies both.

The layer list carries wildcards. `*.Cu` is how KiCad writes a through-hole pad,
and `on_layers` expands it. Call `render.traversal.pad_layer_names` when you need
the layer NAMES of one pad rather than a yes or no.

Flashing has one case this library cannot decide. A plated through-hole pad with
`(remove_unused_layers yes)` keeps inner-layer copper only where it CONNECTS, and
KiCad answers that from the board's connectivity graph. This library derives
connectivity for schematics, not boards. So:

- The renderer DRAWS such a pad, which is what KiCad itself returns when it
  cannot consult connectivity.
- `pad_flash(...).certain` is `False` there, so a caller that needs to know can
  ask.

A preview missing copper misleads worse than one showing copper KiCad might
remove, which is why the default leans that way.

```python
from eedesigncore import Board, LayerStyle, ViewBox, load_font, render_board_svg

board = Board.from_file("board.kicad_pcb")
style = LayerStyle(colors={"F.SilkS": "#FFFFFF"})

skipped = []
render_board_svg(
    board,
    ["F.SilkS"],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=style,
    on_skip=skipped.append,
    font=load_font("Arial"),
)
print(len(skipped), "items this renderer cannot draw")
```

### Holes

A hole is not on a layer. KiCad draws drill marks as their own pass over
everything else, so holes arrive as one more stackable document rather than as
content of a copper layer.

Ask for a pseudo-layer beside the canonical names:

| Name | Holds |
|---|---|
| `HOLES` | every hole on the board |
| `HOLES_PLATED` | vias, and pads whose type is `thru_hole` |
| `HOLES_NON_PLATED` | pads whose type is `np_thru_hole` |

```python
from eedesigncore import HOLES, Board, LayerStyle, ViewBox, render_board_svg

board = Board.from_file("board.kicad_pcb")
style = LayerStyle(colors={"F.Cu": "#C83434", HOLES: "#000000"})
pages = render_board_svg(
    board,
    ["F.Cu", HOLES],
    ViewBox(0.0, 0.0, 297.0, 210.0),
    style=style,
)
print(sorted(pages))
```

Three names rather than one so that a caller who wants plated and non-plated in
different colors gets two stackable documents and writes no policy. A caller who
wants one document asks for `HOLES` and reads `Hole.plated` in its own policy.

A hole is drawn as a solid area, because KiCad plots a drill mark solid to knock
a real opening out of the pad beneath it. A round hole is a true circle. A slot
is two straight sides and two true arc caps, never a polygon.

The hole sits at the pad position. The `(offset ...)` inside `(drill ...)` does
NOT move it, whatever the token name suggests: KiCad feeds that value to
`PAD::SetOffset`, which places the copper.

### Comparing holes against kicad-cli

KiCad draws a slot as one thick round-capped SEGMENT, the centre line, with the
stroke width carrying the slot width. This renderer draws the OUTLINE instead, so
every hole in the document is one solid area a caller styles one way. Both are
the same shape, so a primitive comparison cannot check them against each other.

`compare_holes` reduces either form to two cap centres and a radius:

```python
from eedesigncore import HOLES, Board, LayerStyle, ViewBox, render_board_svg
from eedesigncore.render.compare import compare_holes

board = Board.from_file("board.kicad_pcb")
window = ViewBox(0.0, 0.0, 297.0, 210.0)
style = LayerStyle(colors={HOLES: "#FFFFFF"})

ours = render_board_svg(board, [HOLES], window, style=style)[HOLES]
again = render_board_svg(board, [HOLES], window, style=style)[HOLES]
print(compare_holes(ours, again).summary())
```

One asymmetry matters when the other document is a copper export. KiCad draws a
drill mark only when the item's layer span meets the layer being plotted, so a
single-layer export holds a SUBSET of the board's holes. A hole document holds
them all, including a blind or buried via that no single layer shows. Use
`Hole.touches` from a policy to rebuild the same subset:

```python
from eedesigncore import HOLES, Board, ElementStyle, ViewBox, render_board_svg


class OnFrontCopper:
    """Keep only the holes KiCad would draw for an F.Cu plot."""

    emit_ids = False
    point_stroke_width_mm = 0.05

    def style_for(self, render_item):
        if not render_item.item.touches("F.Cu"):
            return None
        return ElementStyle(stroke="#FFFFFF", stroke_opacity=1.0, fill="none")


board = Board.from_file("board.kicad_pcb")
page = render_board_svg(
    board, [HOLES], ViewBox(0.0, 0.0, 297.0, 210.0), style=OnFrontCopper()
)[HOLES]
print(page.count("<circle"), "holes touch F.Cu")
```

Measured on a real board of 348 holes: 134 touch F.Cu, and every one of those
matches a drill mark in kicad-cli's own F.Cu export.

### Coordinates and precision

Coordinates are millimeters at 4 decimal places, which resolves 0.1 micron. That
is KiCad's own precision, and KiCad records the reason: six digits broke
`stroke-width` for very small non-zero values. Pass `precision` to change it.

An arc is a true SVG `A` command. This renderer never polygonizes one.

A footprint child is stored footprint-local and de-rotated, so the renderer maps
it into board coordinates first. The `point` item is the exception: it stores
board coordinates in both hosts.

A stroke width of zero draws nothing, which is what KiCad does. It is not a
request for a default width.

### Checking the output against kicad-cli

`render.compare` reads two SVG documents back into geometric primitives and
compares them inside a tolerance. It rasterizes nothing.

```python
from eedesigncore import Board, CliParityStyle, ViewBox, render_board_svg
from eedesigncore.render.compare import compare_svg

board = Board.from_file("board.kicad_pcb")
window = ViewBox(0.0, 0.0, 297.0, 210.0)
style = CliParityStyle(colors={"Edge.Cuts": "#D0D000"})

ours = render_board_svg(board, ["Edge.Cuts"], window, style=style)["Edge.Cuts"]
again = render_board_svg(board, ["Edge.Cuts"], window, style=style)["Edge.Cuts"]

difference = compare_svg(ours, again)
print(difference.summary())
assert difference.agrees
```

The comparison ignores element decomposition, direction and order, because none
of those carry geometry. It does not ignore position, radius or width.

Measured on a real board of 90 footprints, the stroked layers agree exactly, and
this renderer emits no primitive that `kicad-cli` does not.

### Speed

The reason to own this renderer is the cost per frame. Measured on a board of
261 footprints and 4.9 MB:

| Work | Cost |
|---|---|
| `kicad-cli`, two layers, cold | about 3.8 s |
| `render_board_svg`, one layer | about 9 ms |
| `render_board_svg`, six layers | about 30 ms |
| `Board.from_file`, once per board | about 275 ms |

The parse happens once. Every later frame pays only the render.

## Schematic SVG from memory

Use `render_schematic_svg` for one loaded sheet. See [schematic SVG exports](svg-schematic-previews.md) for font control, supported items, and limits.

## Raw and resolved text

Use `resolve_text` for strings and `resolve_document_text` for loaded documents.
Both retain raw text and report unresolved values. SVG exports accept `text_context`.
See [text resolution](text-resolution.md) for sheet selection, runtime providers, and limits.

## Update a design from its schematic or PCB

See [schematic and PCB updates](schematic-pcb-updates.md) for both high-level operations.
The existing selected-footprint update API keeps its original behavior.
