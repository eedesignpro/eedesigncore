# Symbol libraries: `.kicad_sym`

## Read this when

You read, build, or change a `.kicad_sym` file, or a symbol definition inside
a schematic `(lib_symbols)` block. Read `docs/agent/README.md` and
`docs/agent/common.md` first.

## Model tree

```
SymbolLib                       eedesigncore
  .version, .generator, .generator_version
  .symbols: list[Symbol]        eedesigncore.common.sch_family.symbol
      .name, .extends, .power_type
      .units_locked: bool       -> ki_locked
      .keywords: str | None     -> ki_keywords
      .fp_filters: list[str]    -> ki_fp_filters
      .fields: list[Property]   the five mandatory fields, then user fields
      .units: list[SymbolUnit]
          .name, .unit_index, .body_style_index, .unit_display_name
          .items: Pin | SchArc | SchCircle | SchRect | SchPolyline
                  | SchBezier | SymText | SymTextBox
```

`Pin` lives in `eedesigncore.common.sch_family.pins`, the five shapes in
`...sch_family.shapes`, and the two text items in `...sch_family.text`.
`SymbolLib` imports from the package root. The same `Symbol` model serves both
a library file and a schematic `(lib_symbols)` entry.

Build a symbol with the mandatory fields already seeded:

```python
from eedesigncore import SymbolLib
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit

symbol = Symbol.new("R", reference="R", description="Resistor")
symbol.units.append(SymbolUnit.for_symbol("R", unit_index=1, body_style_index=1))
SymbolLib(symbols=[symbol]).to_file("Built.kicad_sym")
```

A bare `Symbol(name="R")` carries no fields, so it writes no Reference and no
Value. `Symbol.new` seeds Reference, Value, Footprint, Datasheet, and
Description.

## DCM data

`ki_locked`, `ki_keywords`, and `ki_fp_filters` are NOT fields. Read and set
them on `units_locked`, `keywords`, and `fp_filters`. Looking for them in
`.fields` finds nothing, on load or after a build.

- The writer emits them last, after every real field, always in the order
  `ki_locked`, `ki_keywords`, `ki_fp_filters`. Appending a user property puts
  it ahead of all three, which is what KiCad does.
- Each has its own gate: the lock flag, a non-empty string, a non-empty list.
  All eight presence combinations occur in the stock libraries.
- A derived symbol (`extends`) never writes `ki_locked`. When its `keywords` or
  `fp_filters` is empty it inherits the parent's at write time, in a
  `.kicad_sym` only. A schematic `lib_symbols` block inherits nothing: KiCad
  resolves no parent there and every entry it writes is already flat.
- The parent match drops any `Lib:` prefix from each symbol NAME and any
  `{slash}` escape from both sides. The `extends` value keeps its prefix, so
  `(extends "Device:R")` misses a symbol named `R`, as in KiCad. An ambiguous
  or missing parent inherits nothing.
- `fp_filters` is a LIST on the symbol side and a plain string on the footprint
  side. The two KiCad members differ; this is not an inconsistency. A space
  inside one symbol filter is stored plain and written as `{space}`.
- `ki_description` is legacy input only. The loader folds it into the mandatory
  Description property and never writes it back, exactly as KiCad does. A
  pre-2023 file is therefore upgraded, not copied, so it is not byte-identical
  after a round trip. Every KiCad 10 file is unaffected.

## Unit names

`SymbolUnit.name` is the FULL encoded name, `<item>_<unit>_<body style>`, for
example `R_1_1`. The writer emits `name` verbatim and ignores `unit_index` and
`body_style_index`, so setting the two indices alone changes nothing on disk.

- `SymbolUnit.encode_name("Device:R", 2, 1)` returns `"R_2_1"`. A nickname is
  dropped, because KiCad encodes the item name only.
- `SymbolUnit.for_symbol(...)` builds a unit whose name and indices agree. Use
  it. A hand-typed name that disagrees writes a mislabeled unit, and no
  validator catches it.
- Read the indices from the model. Do not re-derive them from the name.
- Unit 0 is the shared unit, drawn in every unit. Body style 2 is the De Morgan
  alternative.

## Y grows upward

A `.kicad_sym` file stores the negated screen Y. The models hold that file
value verbatim, so in symbol scope a larger Y is higher on the sheet. Every
other format stores Y growing downward. A mirror reverses arc winding, which is
why the loader normalizes symbol arcs with the inverted sign.

## Draw item order

The writer sorts every unit block the way KiCad sorts it at save. Order keys,
in sequence:

1. Item type: shapes, then text, then text boxes, then pins.
2. The private flag, private last.
3. Per type: a pin sorts on its NUMBER first, by natural order, then position,
   length, orientation, shape, electrical type, visibility, both text sizes,
   and the alternates. A shape sorts on position, then geometry, stroke, fill.

Renumbering a pin therefore MOVES it in the file. That is correct output, not a
defect: KiCad writes the same order.

Units sort by `unit_index`, then `body_style_index`. Fields sort by ordinal
(`docs/agent/common.md § Properties and field order`). Library symbols sort by
inheritance depth, then name, so a child of `extends` follows its parent.

Ties keep encounter order, because KiCad breaks its last tie on a runtime uuid
the file never stores.

## No uuids

A `.kicad_sym` file carries no uuid tokens at all. `strip_uuids` and
`generate_uuids` are no-ops on the symbol family.

## Operations

`extract_symbol` and `inject_symbol` move symbols between a library and a
schematic. Both live on the schematic side: see
`docs/agent/schematics.md § Operations`.

## Gaps in this scope

- No KLC validation. Nothing checks pin grid, naming, or field conventions.
- No reference annotation across libraries or sheets. `inject_symbol`
  auto-annotates within ONE schematic file only
  (`docs/operations.md § Reference auto-annotation`).
- No symbol-to-footprint checking, and no pin-to-pad map.
- No ERC. Electrical types are stored, never checked. Not planned.
- No bounding box for a library symbol. `render_symbol_svg` draws one to SVG.
  See `docs/svg-library-previews.md`.
