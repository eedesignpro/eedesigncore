# Project files: `.kicad_pro` and `.kicad_prl`

## Read this when

You read or change a `.kicad_pro` or `.kicad_prl` file. Read
`docs/agent/README.md` and `docs/agent/common.md` first. These two formats are
JSON, not S-expressions, and they use their own parser and writer.

## Preserve-only JSON

`ProjectFile` and `ProjectLocalSettings` keep every key they read, including
keys the library does not model. The writer emits exactly what it read, in the
order it read it.

KiCad does the opposite. It writes every registered default on save, so a KiCad
load-then-write GROWS a project file. Emitting only what was read is what makes
byte identity possible here. It is a deliberate divergence.

The whole file is a `JsonTree` on `.root`. `JsonInt` and `JsonFloat` preserve
the numeric spelling the file used, so an integer never turns into `1.0`.

## Typed accessors

```python
from eedesigncore import ProjectFile

project = ProjectFile.from_file("board.kicad_pro")
print(project.meta.version, len(project.text_variables))
print(project.value_at("schematic.annotate_start_num"))
```

| Accessor | Returns |
|---|---|
| `.meta` | `MetaBlock`, with `version` and `filename` |
| `.text_variables` | `dict[str, str]`, a SNAPSHOT copy |
| `.bus_aliases` | the alias definitions |
| `.saved_netclass_assignments` | saved schematic assignments, or None when absent |
| `.top_level_sheets` | the sheets `load_hierarchy` starts from |
| `.section(name)` | one top-level section as a `JsonTree` |
| `.value_at("a.b.c")` | any value by dotted path |
| `.section_names()` | every top-level key |

Write through the tree, because the accessors return copies:

```python
from eedesigncore import ProjectFile

project = ProjectFile.from_file("board.kicad_pro")
variables = project.text_variables
variables["REVISION"] = "C"
project.root.set("text_variables", dict(variables))
project.to_file("board.kicad_pro")
```

`JsonTree` also offers `get`, `remove`, `path`, `has_path`, and the checked
readers `get_str`, `get_int`, `get_bool`, `get_object`, and `get_list`.

## Saved net class assignments

`read_netclass_assignments(board, project)` returns the saved and pattern-assigned
class names for each board net. It requires synchronized, saved inputs.
It does not calculate class settings or prove synchronization.
See `docs/net-class-assignments.md` for the contract and example.

## Write-time sorting

Three arrays sort on write, matching the KiCad container behind each one.

| Path | Order |
|---|---|
| `net_settings.classes` | `Default` first, then by name |
| Each `netclass_assignments` inner array | ascending |
| `hidden_netclasses` | ascending |

Every other array keeps file order.

## The text-variable snapshot

A `.kicad_pcb` file carries a COPY of the project text variables as board
properties. KiCad refreshes that copy whenever it opens or saves the board.
This library does not.

Editing `text_variables` in the `.kicad_pro` file therefore leaves the board
copy stale, and board text such as `${PCB_STACKUP}` keeps rendering the old
value until KiCad re-saves the board.

Update both sides when you change a text variable:

```python
from eedesigncore import Board, ProjectFile
from eedesigncore.common.models import Property

project = ProjectFile.from_file("board.kicad_pro")
variables = project.text_variables
variables["REVISION"] = "C"
project.root.set("text_variables", dict(variables))
project.to_file("board.kicad_pro")

board = Board.from_file("board.kicad_pcb")
board.properties = [
    Property(name=name, value=value) for name, value in sorted(variables.items())
]
board.to_file("board.kicad_pcb")
```

Rewriting `board.properties` changes those lines of the board file. Every other
line stays byte-identical.

## Gaps in this scope

- No schema validation. An unknown or misspelled key is preserved, not
  rejected.
- No defaults. A key the file omits stays absent, and no accessor invents one.
- No typed model for the settings sections: netclasses, DRC severities, plot
  parameters, and the rest are plain `JsonTree` data.
- Drawing-sheet templates and SVG: `docs/drawing-sheets.md`.
  Custom rule files use `DrcFile`. See `docs/custom-design-rules.md`.
- No project creation. There is no template writer.
