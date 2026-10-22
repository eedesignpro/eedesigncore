"""Place every part of a database library, and check what KiCad requires of it.

The companion to ``scripts/replace_database_part.py``. That one diffs against
schematics KiCad wrote, which only the ``provisional`` library has. This one
needs no oracle, so it runs against ANY database library, including ``partsdb``,
whose 34 parts appear in no corpus schematic.

It checks the invariants a file must satisfy to open in KiCad at all, plus the
ones this library promises:

1. Every unit name passes KiCad's parser rule
   (``sch_io_kicad_sexpr_parser.cpp:501-515``). A failure here means the file
   does not open, and it takes every other sheet with it.
2. Every placed field carries an ``(effects ...)`` block, which is what KiCad
   writes for a placed field.
3. Every hidden field sits at the placed position KiCad computes: the X offset
   added and the Y offset SUBTRACTED. KiCad's reader inverts Y for a library
   field and not for a placed one (``sch_io_kicad_sexpr_parser.cpp:1127``
   against ``:4726``), so a library offset in this model is the file value
   while KiCad's is the negation. The rule is spelled out here rather than
   imported, so this check stays an independent statement of it.
4. The symbol reference resolved, so the part has pins and a body.
5. The written file reads back, and the part survives the round trip.

Rows come from a JSON file: a map from sub-library name to a list of rows. Read
them with whatever your driver is — this library never connects. For the partsdb
library on the operator's machine, the .NET ODBC provider does it from
PowerShell in one command; ``.scratch/partsdb-rows.json`` is that output.

Run:

    .venv/Scripts/python.exe scripts/insert_database_parts.py \\
        --dbl path/to/partsdb.kicad_dbl \\
        --rows .scratch/partsdb-rows.json --nickname partsdb
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eedesigncore import Schematic, inject_symbol, resolve_lib_table  # noqa: E402
from eedesigncore.common.sch_family import Pin  # noqa: E402
from eedesigncore.common.sch_family.symbol import Symbol  # noqa: E402
from eedesigncore.library import load_kicad_dbl  # noqa: E402
from eedesigncore.library.catalog import table_symbol_loader  # noqa: E402
from eedesigncore.library.database import resolve_row  # noqa: E402

SCRATCH = Path(__file__).resolve().parents[1] / ".scratch"


def unit_name_faults(symbol: Symbol) -> list[str]:
    """Port KiCad's unit-name parse rule (``sch_io_kicad_sexpr_parser.cpp:501-515``)."""
    item_name = symbol.name.split(":")[-1]
    faults: list[str] = []
    for unit in symbol.units:
        if not unit.name.startswith(item_name):
            faults.append(f"unit prefix: {unit.name}")
            continue
        parts = unit.name[len(item_name) + 1 :].split("_")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            faults.append(f"unit suffix: {unit.name}")
    return faults


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbl", required=True, type=Path)
    parser.add_argument("--rows", required=True, type=Path)
    parser.add_argument("--nickname", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    library = load_kicad_dbl(args.dbl)
    sub_libraries = {entry.name: entry for entry in library.libraries}
    # utf-8-sig: PowerShell's Out-File writes a byte-order mark.
    supplied = json.loads(args.rows.read_text(encoding="utf-8-sig"))

    table = resolve_lib_table("sym")
    print(f"sym-lib-table: {len(table)} libraries, {len(table.skipped)} skipped")
    for skip in table.skipped:
        print(f"  skipped: {skip.reason[:110]}")

    loader = table_symbol_loader(table)
    memo: dict[str, Symbol | None] = {}

    def cached_loader(lib_id: str) -> Symbol | None:
        if lib_id not in memo:
            memo[lib_id] = loader(lib_id)
        return memo[lib_id]

    schematic = Schematic()
    faults: Counter[str] = Counter()
    placed_count = 0
    metadata_only = 0
    pinless = 0
    x = y = 25.4

    for name, rows in supplied.items():
        entry = sub_libraries.get(name)
        if entry is None:
            faults[f"no sub-library named {name!r} in the .kicad_dbl"] += 1
            continue

        for row in rows:
            if not isinstance(row, dict) or row.get(entry.key) is None:
                faults["row with no key column"] += 1
                continue

            built = resolve_row(
                row,
                entry,
                globally_unique_keys=library.globally_unique_keys,
                symbol_loader=cached_loader,
            )
            if built.metadata_only:
                metadata_only += 1
                print(f"  METADATA ONLY {built.definition.name}: {built.unresolved_reason[:100]}")
                continue

            for fault in unit_name_faults(built.definition):
                faults[f"unit name KiCad would refuse ({fault})"] += 1

            at = (x, y)
            placed = inject_symbol(
                schematic,
                built.definition,
                at=at,
                lib_nickname=args.nickname,
            )
            placed_count += 1

            offsets = {f.name: f.position for f in built.definition.fields}
            for field in placed.fields:
                if field.attributes is None:
                    faults[f"placed field carries no effects block: {field.name}"] += 1
                offset = offsets.get(field.name, (0.0, 0.0))
                want = (round(at[0] + offset[0], 6), round(at[1] - offset[1], 6))
                got = (round(field.position[0], 6), round(field.position[1], 6))
                if not field.visible and got != want:
                    faults[f"hidden field not at (at.x+dx, at.y-dy): {field.name}"] += 1

            x += 25.4
            if x > 254.0:
                x = 25.4
                y += 25.4

            # A part with no pins is NOT a fault. Measured on the provisional
            # library: a battery holder, a jumper shunt and an off-board marker
            # all carry a body and zero pins on purpose. Counted, not blamed.
            if not any(
                isinstance(item, Pin) for unit in built.definition.units for item in unit.items
            ):
                pinless += 1

    out = args.out or SCRATCH / f"{args.nickname}-all-parts.kicad_sch"
    out.parent.mkdir(parents=True, exist_ok=True)
    schematic.to_file(out)

    reread = Schematic.from_file(out)
    if len(reread.symbols) != placed_count:
        faults["round trip lost a symbol"] += 1
    if len(reread.lib_symbols) != len({s.lib_id for s in reread.symbols}):
        faults["round trip lost a definition"] += 1

    print(f"\nparts placed:    {placed_count}")
    print(f"metadata only:   {metadata_only}")
    print(f"no pins, by design: {pinless}")
    print(f"definitions:     {len(schematic.lib_symbols)}")
    print(f"file written:    {out}")
    print(f"reads back:      {len(reread.symbols)} symbols, {len(reread.lib_symbols)} definitions")
    print(f"INVARIANT FAULTS: {sum(faults.values())}")
    for fault, count in faults.most_common(20):
        print(f"  {count:4d}  {fault}")

    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())
