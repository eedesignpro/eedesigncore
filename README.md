# eedesigncore

Read, edit, and write KiCad 10 files from Python. No running KiCad instance is
needed.

**Status: early.** No package registry carries this library yet. Install it
from a source checkout.

## What it does

The library reads a KiCad file into Python objects. You change the objects,
and the library writes the file back.

Load a file and write it back, and the bytes do not change. Change one value,
and `git diff` shows one line. The output follows what KiCad 10.0.x writes:
tab indentation, token order, and number formatting.

```python
from eedesigncore import Footprint

fp = Footprint.from_file("C_0402_1005Metric.kicad_mod")
fp.to_file("C_0402_1005Metric.kicad_mod")   # same bytes, empty git diff
```

Four limits apply:

- The source file must be one that KiCad wrote. A file that another tool
  rewrote can come back in KiCad layout, and that gives a real diff.
- Byte identity is per file. A hierarchical design goes through one schematic
  file at a time.
- `.to_file` keeps the line endings of the source file. `.to_string` always
  returns line feeds.
- A schematic or symbol library that KiCad 9 wrote comes back in the KiCad 9
  layout, byte for byte. Raise its `version` to get the KiCad 10 layout.
- A footprint file from KiCad 5 to KiCad 9 loads as KiCad 10 loads it, and
  writes in the KiCad 10 format, as `kicad-cli fp upgrade` does.

## Supported files

| File | Status |
|---|---|
| `.kicad_sym`, symbol library | Read and write |
| `.kicad_mod`, footprint | Read and write |
| `.kicad_pcb`, board | Read and write |
| `.kicad_sch`, schematic | Read and write |
| `.kicad_pro`, project | Read and write |
| `.kicad_prl`, project local settings | Read and write |
| `fp-lib-table`, `sym-lib-table` | Read and write |
| `.kicad_wks`, drawing sheet | Edits, graphics, and SVG output |
| `.kicad_dru`, custom design rules | Read, write, and edits to a marked block |

The library also derives a netlist from schematic geometry, moves and turns
board content, and moves footprints and symbols between files. The documents
in `docs/` describe each operation. Start with `docs/operations.md`.

## Documentation

General:

- [Operations reference](docs/operations.md): move parts between files, copy
  and transform board content, resolve library tables, find parts, and draw
  boards to SVG
- [Content comparison](docs/content-comparison.md)
- [Raw text and resolved text](docs/text-resolution.md)
- [Agent guide](docs/agent/README.md), a compact reference for AI agents

Boards and footprints:

- [Copper layer changes](docs/copper-layer-changes.md)
- [Custom design rule files](docs/custom-design-rules.md)
- [Drawing sheets](docs/drawing-sheets.md)
- [Read saved net class assignments](docs/net-class-assignments.md)
- [Selected schematic-to-PCB updates](docs/schematic-board-update.md)
- [Update the PCB from the schematic, and the schematic from the PCB](docs/schematic-pcb-updates.md)
- [Update Symbol from Library and Change Symbol](docs/symbol-library-updates.md)
- [Pad apertures and backdrill depth](docs/pad-apertures-and-backdrill-depth.md)
- [Selected plot content bounds](docs/plot-content-bounds.md)

Schematics:

- [Create schematic labels and sheet references](docs/schematic-creation.md)
- [Schematic note blocks](docs/schematic-notes.md)
- [Schematic tables that fit their text](docs/schematic-tables.md)
- [Automatic schematic field placement](docs/field-autoplacement.md)
- [Schematic bounds and collision checks](docs/schematic-measurements.md)
- [Shared global-label borders](docs/global-label-geometry.md)
- [Schematic electrical inspection and guarded edits](docs/schematic-electrical-inspection.md)
- [Schematic preparation for viewers](docs/schematic-preparation.md)
- [Native and assembly schematic pages](docs/assembly-schematic-view.md)

SVG output:

- [Symbol and footprint SVG previews](docs/svg-library-previews.md)
- [Schematic SVG exports](docs/svg-schematic-previews.md)
- [Font choice for SVG](docs/svg-fonts.md)
- [Copper drawing SVG contract](docs/copper-drawing-svg.md)
- [Rendered element callback](docs/rendered-element-callback.md)

Netbom and inspection:

- [Netbom design content](docs/netbom.md)
- [Netbom assemblies](docs/netbom-assemblies.md)
- [Design inspection](docs/design-inspection.md)

## Install from source

You need Python 3.11 to 3.14 and git.

```bash
git clone https://github.com/eedesignpro/eedesigncore.git
cd eedesigncore
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

On Linux or macOS, use `.venv/bin/python` in place of
`.venv/Scripts/python.exe`.

To install the optional font support, use `-e ".[fonts]"`. To install the
development tools, use `-e ".[dev]"`. The file `constraints.txt` holds the
exact versions that the tests use:

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]" -c constraints.txt
```

## Quick start

```python
from eedesigncore import Board, Schematic, SymbolLib

lib = SymbolLib.from_file("Device.kicad_sym")
print(len(lib.symbols), "symbols")

board = Board.from_file("board.kicad_pcb")
print(len(board.footprints), "footprints")

sch = Schematic.from_file("schematic.kicad_sch")
print(len(sch.symbols), "placed symbols")
sch.to_file("schematic.kicad_sch")
```

Each root object has the same four calls: `from_file`, `from_string`,
`to_string`, and `to_file`. Write to disk with `to_file`. It is the only call
that keeps the source line endings.

## Upgrading

`CHANGELOG.md` records every release. Read it before you move to a newer
version. Some releases change output or rename fields.

## Tests

```bash
.venv/Scripts/python.exe -m pytest
```

Some tests need a local KiCad install, the `kicad-cli` program, or a private
collection of design files. Those tests skip when the item is absent.

## License

GNU General Public License, version 3 or later. See `LICENSE`.

Large parts of this library are a translation of named functions from
KiCad's own source code, which carries this same license. Licensing this
library under the same terms avoids a derivative-work question rather than
leaving it open.

## Support

This code is provided as-is. SeeScan, Inc. makes no commitment to support it,
maintain it, or answer issues and pull requests.
