# Common primitives

## Read this when

Always. Every format reuses the models below. Read `docs/agent/README.md`
first for the invariants.

```python
from eedesigncore.common.models import (
    Color, EmbeddedFile, Fill, Property, Stroke, TextAttributes,
)

print(Property(name="Reference", value="R1").visible)
```

## Properties and field order

`Property` is the `(property "Name" "Value" ...)` node. Every format uses it:
symbol fields, footprint properties, board properties, and placed-symbol
fields. Key fields are `name`, `value`, `visible`, `position`, `angle`,
`attributes`, and, in PCB context only, `layer`, `knockout`, and `id`.

The writer sorts properties by an ordinal the file never stores.

| Property kind | Ordinal source |
|---|---|
| Symbol mandatory: Reference, Value, Footprint, Datasheet, Description | 1 to 5, by name, case-insensitive |
| Symbol user field | 42 upward, in file position order |
| Footprint mandatory: Reference, Value, Datasheet, Description | 0 to 3, by name, case-insensitive |
| Footprint user field | after the mandatory four, in file position order |

Two consequences. A mandatory field leads wherever the file put it. Editing
`value` on any property never moves it, because the key is the name or the
position, never the text.

`ki_locked`, `ki_keywords`, and `ki_fp_filters` never appear in
`symbol.fields`. They are model members and they emit after the whole sorted
block (`docs/agent/symbols.md § DCM data`).

## Text effects

`TextAttributes` is the `(effects ...)` node: font size, thickness, bold,
italic, justification, and hide.

`Property.attributes` defaults to `None`, and a property with `None` still
writes a full `(effects ...)` block holding the KiCad default, 1.27 mm square.
KiCad writes an effects block for every field, so the library does too. Other
text items with `attributes=None` write no effects block yet. Set `attributes`
explicitly on a hand-built `SymText`, `SymTextBox`, `PcbText`, label, table
cell, or dimension.

## Stroke and fill

`Stroke` carries `width` in millimeters and `type` from `StrokeType`
(`default`, `solid`, `dash`, `dot`, `dash_dot`, `dash_dot_dot`). `Fill`
carries `type` from `FillType` (`none`, `outline`, `background`, `color`) and
an optional `Color`. `Color` holds four integers, `r`, `g`, `b`, `a`.

## Coordinates and units

Every model field holds the file value verbatim. No conversion runs at load or
at save.

| Quantity | Unit |
|---|---|
| Position, size, width, length | millimeters, float |
| Angle | degrees, float |
| Color channel | 0 to 255, integer |

The Y axis points down in `.kicad_mod`, `.kicad_pcb`, and `.kicad_sch` files.
A `.kicad_sym` file stores the negated Y, so Y points up there. See
`docs/agent/symbols.md § Y grows upward`.

A footprint child on a board stores its position relative to the parent
footprint. See `docs/agent/footprints.md § Coordinates`.

## Three write paths

| Call | Returns | Layout | Line endings |
|---|---|---|---|
| `dump_*` (`dump_mod`, `dump_sch`, ...) | `str` | flat, one long line | none |
| `write_*` and `model.to_string()` | `str` | prettified | line feed |
| `model.to_file(path)` | `None` | prettified | preserves the source file |

`dump_*` output is not a valid file layout. `write_*` adds the Prettify pass
that KiCad itself runs. `to_file` takes `line_ending` as `"preserve"` (the
default), `"lf"`, `"crlf"`, or `"native"`.

The `load_*` functions take an already parsed tree, not a path. Call `parse`
first, or use `Model.from_file`, which parses for you.

## Library tables

`fp-lib-table` and `sym-lib-table` map a library nickname to a URI. One
resolver serves both inject operations, because a `.kicad_mod` file holds a
bare name and a board needs `nickname:name`.

- `resolve_lib_table("fp", project_dir=".")` returns a `ResolvedLibTable`. Pass
  `"sym"` for symbols. The kind must match the inject call.
- The resolver merges the global tier and the project tier, project wins, and
  it follows nested table rows recursively.
- Reads are confined to allowed roots, and expansion covers KiCad variables
  only. Widen with `allowed_roots`.
- `qualify_item_name(name, lib_nickname=..., lib_table=...)` applies the same
  precedence without an inject call.

`docs/operations.md § Library nickname resolution` holds the precedence order,
the confinement rules, and the failure modes.

## Embedded files

`EmbeddedFile` holds a base64 payload with a KiCad checksum.

- `EmbeddedFile.from_bytes(name, raw)` compresses and encodes.
- `file.to_bytes()` verifies the checksum and decompresses.
- Both cap at 1 GiB, on the encoded size and on the decoded size. Pass a
  lower `max_size` to `to_bytes` for untrusted input.
- The zstd backend is `compression.zstd` on Python 3.14 and later, and the
  `zstandard` package on 3.11 to 3.13. Neither present raises
  `ZstdBackendUnavailableError`.

## Exceptions

| Exception | Base | Raised when |
|---|---|---|
| `KicadParseError` | `ValueError` | malformed file, unknown token, non-finite number |
| `ZstdDecompressionError` | `KicadParseError` | corrupt zstd frame, or over the decode cap |
| `EmbeddedFileChecksumError` | `KicadParseError` | payload does not match the stored checksum |
| `LibTableResolutionError` | `ValueError` | library-table discovery fails |
| `SheetHierarchyError` | `ValueError` | the sheet tree cannot be walked |
| `ZstdBackendUnavailableError` | `RuntimeError` | no zstd backend imports |

Catch `KicadParseError` for file problems. Pydantic raises `ValidationError`
for a model you build wrong, which is a different failure.

## SaveContext

`SaveContext` picks the serialization context: `LIBRARY` for a standalone
`.kicad_mod` or `.kicad_sym`, `BOARD` for a footprint inside a board,
`SCHEMATIC` for a symbol inside `lib_symbols`. `to_file` picks it for you. Pass
it only to a `dump_*` or `write_*` call.

## Gaps in this scope

- No unit conversion helper. Millimeters are the only unit the models speak.
- Geometry helpers cover boards and footprints: move, turn, flip, and bounding
  box. See `docs/operations.md § Board manipulation`. Schematic measurement and
  overlap checks are in `docs/schematic-measurements.md`.
- A hand-built arc keeps the winding you give it. When KiCad loads an arc whose
  direction disagrees with its own convention, it swaps the start and end
  points. The loader here does the same, so a loaded file writes back
  unchanged. A constructor does not, so KiCad can swap the points of a built
  arc on its next save.

## Editing constraints

- **Do not hand-edit S-expression text.** Load the file, change the model,
  write the file. Text edits break the Prettify layout and the node order.
- **Do not expect byte identity from a file another tool wrote.** Only a
  KiCad-written file is a valid oracle. The library reads the other file and
  then writes KiCad layout, which is a real diff.
- **Do not strip uuids from a board or a schematic.** A group holds its members
  by uuid, and KiCad drops a member whose uuid is gone.
- **Do not derive unit indices from a name yourself.** The models carry
  `unit_index` and `body_style_index`.
