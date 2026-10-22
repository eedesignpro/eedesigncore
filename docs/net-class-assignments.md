# Read saved net class assignments

Use a synchronized, saved board and project file. This operation reads saved
schematic assignments and evaluates every saved net-name pattern. It returns
class names for the nets listed in the board.

```python
from eedesigncore import Board, ProjectFile, read_netclass_assignments

board = Board.from_file("design.kicad_pcb")
project = ProjectFile.from_file("design.kicad_pro")
assignments = read_netclass_assignments(board, project)

for net_name, class_names in assignments.items():
    print(net_name, class_names)
```

Each value is a tuple of unique class names in alphabetical order. This order
does not express class priority. Names without configured class definitions
remain in the result. A net with no assignment returns `("Default",)`.
The empty net name also returns `("Default",)`.

All matching patterns contribute classes. Saved schematic assignments and
pattern assignments combine. Neither source replaces the other.
Patterns follow KiCad's case-sensitive wildcard and regular-expression paths.
Bus ranges and bus-group members expand before matching.

The operation does not calculate class settings, classes that supply missing
properties, bus display classes, or final design-rule constraints. It does not
rebuild assignments from schematic connections. Neither input changes.

The files have no shared synchronization marker. This operation cannot prove
that the board and project match the current schematic. File timestamps and
matching net names do not establish that fact. The caller must supply the
synchronized file set.

Missing assignment or pattern fields raise `KicadParseError`. Malformed data
also raises that error. A saved null assignment table is valid and means no
saved schematic assignments. It is distinct from a missing field.
Assignments for names absent from the board remain valid project data and do
not cause an error. A schematic can contain nets excluded from the board.

Patterns compile once per call. A later call reads current project data.
No separate cache survives project edits.

To read only the saved schematic assignment table, use
`project.saved_netclass_assignments`. This property returns a detached mapping.
It returns `None` when the assignment field is absent. It does not evaluate
patterns or add Default assignments.

## Input limits

The reader rejects oversized patterns and bus expansions before matching.
The pattern engine also limits matching work. Reaching a limit raises an error
instead of returning an incomplete set of assignments.

| Limit | Value |
| --- | --- |
| Saved pattern length | 4096 characters |
| Total expanded patterns per call | 10000 |
| Bus nesting depth | 32 |
| Pattern engine match limit | 100000 |
| Pattern engine depth limit | 1000 |

## Pattern compatibility

The reader uses PCRE2 with KiCad's matching options. The regression fixture
records class membership from installed KiCad, including wildcard, regular-expression,
bus-range, Unicode, and punctuation cases. No running KiCad installation is
required to use the reader or run those regression checks.
