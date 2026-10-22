# Custom design rule files

Read and write `.kicad_dru` files with `DrcFile`.
An unchanged file retains its bytes, including comments, spacing, unit spelling,
rule order, line endings, and the final newline state.
The reader covers KiCad custom rule constraints and their legacy aliases.

```python
from eedesigncore import DrcFile

document = DrcFile.from_file("design.kicad_dru")
document.to_file("design.kicad_dru")
```

`from_string` reads text in memory. `to_string` returns line-feed endings.
`to_file` preserves source endings unless `line_ending` selects `lf`, `crlf`,
or `native`. Files use UTF-8. An existing UTF-8 byte order mark is retained.

## Inspect and edit rules

Rules stay in file order. Each rule has a name and an ordered `clauses` list.
That list contains `DrcConstraint` and `DrcClause` objects.
A clause identifies a condition, layer, or severity through its `kind` field.
A constraint has a `type`, ordered `bounds`, and special-form `arguments`.

```python
from eedesigncore import DrcConstraint, DrcConstraintType, DrcFile

document = DrcFile.from_file("design.kicad_dru")
for rule in document.rules:
    if rule.name == "high voltage clearance":
        for clause in rule.clauses:
            if isinstance(clause, DrcConstraint):
                if clause.type == DrcConstraintType.CLEARANCE:
                    for bound in clause.bounds:
                        if bound.kind == "min":
                            bound.expression = "20mil"
document.to_file("design.kicad_dru")
```

Unchanged text stays intact. A changed value replaces its source span.
Removing a clause leaves surrounding comments in place.
Comments inside a replaced expression belong to that expression and are replaced with it.
New content uses a deterministic style with quoted rule names and tab-indented clauses.
The writer does not imitate every graphical editor save path.

Rule names need not be unique. The writer never merges rules by name or condition.
The writer never sorts rules. Order can change which constraint applies.
For an unmarked document, edit `rules` to add, delete, or deliberately reorder rules.
In a marked document, use block operations for insertion, replacement, and reordering.
Deleting a retained rule does not move the remaining rules into other blocks.

## Generate a rule

```python
from eedesigncore import (
    DrcBound, DrcClause, DrcConstraint, DrcConstraintType, DrcFile, DrcRule,
)

rule = DrcRule(
    name="high voltage clearance",
    clauses=[
        DrcClause(kind="condition", value="A.NetClass == 'HV'"),
        DrcConstraint(
            type=DrcConstraintType.CLEARANCE,
            bounds=[DrcBound(kind="min", expression="20mil")],
        ),
    ],
)
DrcFile(rules=[rule]).to_file("new-design.kicad_dru")
```

Bounds use `min`, `opt`, or `max`. A skew constraint can also contain
`DrcBound(kind="within_diff_pairs")`.
Expressions remain text, so arithmetic, project variables, and units retain their spelling.

Special constraints use `arguments` instead of `bounds`:

| Constraint | Example arguments |
| --- | --- |
| `disallow` | `["via", "track"]` |
| `zone_connection` | `["thermal_reliefs"]` |
| `min_resolved_spokes` | `["2"]` |
| `assertion` | `["A.NetName != 'HV'"]` |

## Independent plugin blocks

Use top-level comment markers to identify each generator's block.
Keep human rules in a separate block. Existing spacing-generator markers remain supported.

```text
(version 1)
# BEGIN USER RULES
# Human rules belong here.
# END USER RULES
# BEGIN GENERATED SPACING RULES
# The spacing generator replaces this content.
# END GENERATED SPACING RULES
# BEGIN VIA RULES
# The via generator replaces this content.
# END VIA RULES
```

`update_drc_block` reads the current file and replaces only the named block contents.
Every byte outside the block remains unchanged. The marker lines remain unchanged too.
Unmarked human rules also survive. Each generator supplies its own rule list or rule text.

```python
from eedesigncore import update_drc_block

update_drc_block(
    "design.kicad_dru",
    name="GENERATED SPACING RULES",
    rules=[rule],
)
```

For work in memory, use `document.replace_block(name, rules)`.
Use `document.block_names()` to list existing blocks.
Use `document.create_block(name, rules, before="VIA RULES")` to create a block at a selected position.
Omitting `before` explicitly appends the new block.
`replace_block` never invents a missing block.
An empty rule list clears the contents but preserves the markers.
Block operations refresh the document's rule objects. Read `document.rules` again after such an operation.

Missing, nested, duplicated, or mismatched markers cause an error.
Markers must stand on their own lines outside rules. A managed block cannot contain the version statement.
A replacement cannot introduce more block markers or a version statement.
Manual edits inside a generated block are replaced with that block.

KiCad assigns no ownership meaning to these comments.
KiCad's graphical rule editor can discard comments that sit outside rules.
If another editor removes the markers, an update fails instead of guessing ownership.
Graphical-editor byte preservation is not a promise of this interface.

## Save protection

`to_file` and `update_drc_block` use the same exclusive sibling `.lock` file.
Concurrent cooperating writers fail without changing the rule file.
`update_drc_block` reads the latest file while holding that lock.
`to_file` refuses to overwrite its loaded source if the source bytes have changed.
Saving to a different path is an explicit export and does not compare that path with the original.

A save writes a temporary sibling file, then atomically replaces the destination.
An ordinary failed save removes its temporary file and releases the lock.
A terminated process can leave a lock. Remove it only after confirming that no writer still uses it.
These protections do not lock KiCad or another editor that ignores the lock file.
Such an editor can still race a save after the final byte comparison.

## Validation boundary

The reader validates the outer rule grammar, constraint forms, and string escapes.
Unknown grammar raises `KicadParseError` with a line and column.
Constructed objects and changed lists are checked again before a save.
The reader consumes the whole file, including trailing text.

This is not a complete condition-expression compiler or a board design rule checker.
Expression syntax, unit compatibility, variable resolution, and layer existence still require KiCad context or further validation.
The reader does not evaluate expressions, execute code, or access a board.
Component-class assignment files use a different KiCad entry point and are outside `.kicad_dru` support.

Input limits prevent unbounded file and syntax-tree growth:

| Limit | Value |
| --- | --- |
| Encoded file size | 16 MiB |
| Retained syntax nodes | 250000 |
| Parenthesis depth | 100 |

The parser accepts the version number as stored. It does not infer format compatibility from that number alone.
