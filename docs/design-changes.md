# Design changes: labelled change records

Use `compare_designs` to compare two boards, schematics, footprints, symbol libraries, or project files.
The result is a list of change records. Each record names the item, the field, the old value, the new value, and a category.
Filter by category to separate electrical changes from geometry, part data, settings, and noise.
An empty list means the two designs are equal.

This is a second view of `compare_content` (see [content comparison](content-comparison.md)).
It reads the same normalised content, so it uses the same order rules and the same identifier matching.
`compare_content`, `ContentChange`, and `content_signature` do not change.

```python
from eedesigncore import compare_designs, format_change_report

result = compare_designs("before.kicad_pcb", "after.kicad_pcb")
print(result.equal)
print(result.counts())
for change in result.only("electrical").changes:
    print(change.item, change.field, change.before, change.after)
print(format_change_report(result))
```

Each side is a file path or a model loaded from a file.
To compare two revisions, load each revision and pass the two models, or use the git form below.
Two sides of different types cause an error.

## The record

`DesignComparison` holds `before_signature`, `after_signature`, `tolerance`, and `changes`.
`equal` is true when `changes` is empty. `counts()` returns the number of changes per category.
`only(*categories)` returns a copy with those categories only. `to_json()` returns JSON text.

Each `DesignChange` holds these fields:

| Field | Content |
| --- | --- |
| `category` | `electrical`, `part`, `geometry`, `settings`, `cosmetic`, `derived`, or `other` |
| `kind` | `added`, `removed`, or `changed` |
| `item_type` | The model type, for example `Pad`, `TrackSegment`, `PlacedSymbol`, or `Net` |
| `item` | A name a person reads, for example `R12 pad 2`, `track on GND`, or `net VBUS` |
| `identity` | The stored identifier the match used, usually a UUID. Empty when the item has none. |
| `field` | The dotted field path in the item. Empty when the whole item was added or removed. |
| `before`, `after` | JSON text. `None` means absent. |
| `path` | The full path from the document root |

The JSON layout is named by `CHANGE_FORMAT`, now `eedesign-changes-v1`.
A change to a record field or to the meaning of a category takes a new version.

## Matching

Items match by their stored identifier, as in `compare_content`.
A move or a field edit of a matched item is one `changed` record, not a removal and an addition.
Pads, pins, and fields that have no stored identifier match by pad number, pin number, or field name.
This match happens only when the number or name is unique on both sides.
A library footprint stores no pad identifiers, so this rule matches its pads.
An item that has a stored identifier matches by that identifier only.
After these steps, when one unmatched item of a type remains on each side, the two items match.
For example, a library footprint line whose width changed is one change to that line.
Every other unmatched item is one `added` or `removed` record that names its own type.

A list that `compare_content` keeps in order, and whose items all carry a unique identifier, number, or name, is matched by it.
Symbol and footprint fields are such a list.
A new order of the same items is then one `cosmetic` record with the field `order`.

## Categories

The category is a library rule set by the item type and the changed field.
It is not a KiCad rule and it does not run a connection check.

| Category | Examples |
| --- | --- |
| `electrical` | Pad nets, numbers, and copper layers. Track, via, and zone nets. Board nets. Schematic wires, buses, junctions, no-connects, bus entries, and net labels. Symbol pins. |
| `part` | Footprint and symbol library identifiers. Reference, Value, and other field values. Attributes such as DNP. 3D models. Sheet names. |
| `geometry` | Positions, angles, sizes, shapes, layers, and line widths. |
| `settings` | Board setup, layer definitions, title block, rule margins, and project files. |
| `cosmetic` | Text effects, visibility, colour, locks, groups, identifiers, item order, and file version stamps. |
| `derived` | Zone fills and stored text outlines. KiCad recalculates these. |
| `other` | A field no rule covers. Review it. |

A track moved so that it touches another net is `geometry`. Only its `net` field is `electrical`.
The net summary below shows the effect on each net.
A schematic wire sets connections by its ends, so a move of a wire end is `electrical`.
`compare_schematic_electrical` gives the connection proof for a schematic.

## Board net summary

For a board, `compare_designs` adds one record per net whose pads or routing changed. The `item_type` is `Net`.

- `pads`: the sorted list of `Reference.pad` entries on the net. Pads match by footprint identifier and pad number, so a new Reference alone changes no net.
- `routing`: the count of segments, arcs, vias, and zones on the net, and the total track length in millimetres.
- `name`: a net that keeps the same pads under a new name is one rename record.

The summary reads the net names stored on pads, tracks, vias, and zones.
It does not rebuild copper connections from geometry. Pass `nets=False` to leave it out.

## Tolerance

`tolerance` applies to every number: millimetres for a length, degrees for an angle. The default is zero, which is exact.
It must be below 1, because it also applies to counts and indexes such as a unit number.
KiCad stores whole nanometres, so a file round trip needs no tolerance.
Items with no stored identifier and no number or name key match within the tolerance only when every number agrees.
The two signatures stay exact, so they can differ when `equal` is true.

## Text views

`format_change_report(result)` prints the count per category and one line per change, grouped by category.
`format_change_diff(result, before_label=..., after_label=...)` prints a git-style diff with one hunk per item.
A whole added or removed item prints as one line, not as its full content.
Both views clip long values. Pass `value_limit=0` to print values whole.
Both views show a control character in a name as `\xNN`, so text from a file cannot change the layout of the output.

## Command and git diff

```
python -m eedesigncore.design_changes OLD NEW [--report | --json] [--category electrical] [--tolerance T] [--no-nets] [--exit-code]
```

The default output is the diff view. `--exit-code` exits with 1 when a change is found.

The command also accepts the seven arguments git passes to a diff command.
It uses the repository path to choose the file reader, because git hands over temporary files.
Set it up once per repository:

```
git config diff.kicad.command "python -P -m eedesigncore.design_changes"
```

and add to `.gitattributes`:

```
*.kicad_pcb diff=kicad
*.kicad_sch diff=kicad
```

The `python` on the path must have this package installed.
Keep the `-P`. Git runs the command in the top folder of the repository, and without `-P` Python loads a module file found there before its own.
The command also accepts the nine arguments git passes for a renamed file, and the one argument it passes for an unmerged file.
Under git, a file that cannot be read prints one line and the diff continues.
Outside git, a file that cannot be read exits with 2.

## Older change records

Two functions translate older records into `DesignChange` records, so one report reads all of them.
The older records stay as they are.

- `changes_from_schematic_electrical(comparison)` translates a `SchematicElectricalComparison`.
- `changes_from_update(changes)` translates the `DesignUpdateChange` list of a schematic-to-board or board-to-schematic update.
  The values of footprint and symbol updates are model JSON. Every other value, such as a net name, becomes a JSON string.

## Limits

- The categories are library rules. They are not a proof of electrical equality.
- The board net summary uses stored net names. It does not rebuild copper connections.
- Compare two files saved by the same KiCad version. A file upgrade shows as changes: fields new in KiCad 10 change from absent to their default, and the reader drops the net numbers of tracks in an older board.
- A pad `net_code` is a stored index, so a change to it alone is `cosmetic`. The net name is in `net_name`.
- The limits of `compare_content` apply: one file at a time, no child sheets, no project settings, no refill, no connection rebuild.
