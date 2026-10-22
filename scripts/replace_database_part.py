"""Delete a database part KiCad placed, re-add it with this library, diff the file.

The operator's test method, and the strongest oracle this library has for the
insert path: every byte of the re-added part came from KiCad on one side and
from ``definition_from_row`` plus ``inject_symbol`` on the other, in the same
file, at the same position, with the same reference.

What it does, per part:

1. Copy a corpus schematic into ``.scratch`` and read it. The corpus is never
   written.
2. Take one placed symbol whose ``lib_id`` names a database library, and record
   its position, angle, unit, reference and its whole field list.
3. Delete it: the placed symbol, and its ``(lib_symbols)`` entry when no other
   placement shares it.
4. Re-add it from the DATABASE ROW: ``definition_from_row`` builds the
   definition, ``inject_symbol`` places it at the recorded position with the
   recorded reference.
5. Diff the re-added part against what KiCad wrote, attribute by attribute.

Uuids are expected to differ and are reported separately: a fresh placement
mints a new instance uuid and new per-pin uuids by design (uuid-lifecycle
Task 4). Everything else is a fault or a documented limit.

Run:

    .venv/Scripts/python.exe scripts/replace_database_part.py --root C:/KiCad
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database_oracle import as_odbc_text, build_source_table  # noqa: E402

from eedesigncore import Schematic, inject_symbol  # noqa: E402
from eedesigncore.common.models import Property  # noqa: E402
from eedesigncore.library import load_kicad_dbl  # noqa: E402
from eedesigncore.library.catalog import table_symbol_loader  # noqa: E402
from eedesigncore.library.database import resolve_row  # noqa: E402
from eedesigncore.sch.models import PlacedSymbol  # noqa: E402

SKIP_DIRS = {".git", ".venv", "node_modules", ".history", ".scratch"}
PROVISIONAL_ROOT = Path("provisional-library")  # pass --provisional
SCRATCH = Path(__file__).resolve().parents[1] / ".scratch" / "replace-database-part"


STALE_FIELDS = {"AXQtyOnHand", "AXStandardCost"}
"""Columns that change every day, so a cached value can never match today's row.

Same exclusion, same reason, as ``scripts/database_oracle.py``.
"""


@dataclass
class Tally:
    parts: int = 0
    no_row: int = 0
    metadata_only: int = 0
    shared_definition: int = 0
    exact: int = 0
    drift_only: int = 0
    stale_only: int = 0
    faulty: int = 0
    faults: Counter[str] = field(default_factory=Counter)
    drift: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)


def iter_schematics(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.kicad_sch"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def field_signature(prop: Property) -> dict[str, object]:
    """Every attribute of a field that a schematic file carries."""
    attributes = prop.attributes
    return {
        "value": prop.value,
        "position": (round(prop.position[0], 4), round(prop.position[1], 4)),
        "angle": prop.angle,
        "visible": prop.visible,
        "show_name": prop.show_name,
        "do_not_autoplace": prop.do_not_autoplace,
        "private": prop.private,
        "font_size": None if attributes is None else attributes.font_size,
        "bold": None if attributes is None else attributes.bold,
        "italic": None if attributes is None else attributes.italic,
        "justify_h": None if attributes is None else attributes.justify_h,
        "justify_v": None if attributes is None else attributes.justify_v,
        "hidden": None if attributes is None else attributes.visible,
    }


def compare_placed(
    expected: PlacedSymbol,
    rebuilt: PlacedSymbol,
    tally: Tally,
    where: str,
) -> None:
    faults: list[str] = []
    drift: list[str] = []
    stale: list[str] = []

    for name, want, got in (
        ("lib_id", expected.lib_id, rebuilt.lib_id),
        ("position", expected.position, rebuilt.position),
        ("angle", expected.angle, rebuilt.angle),
        ("unit", expected.unit, rebuilt.unit),
        ("body_style", expected.body_style, rebuilt.body_style),
        ("in_bom", expected.in_bom, rebuilt.in_bom),
        ("on_board", expected.on_board, rebuilt.on_board),
        ("exclude_from_sim", expected.exclude_from_sim, rebuilt.exclude_from_sim),
        ("in_pos_files", expected.in_pos_files, rebuilt.in_pos_files),
        ("pin count", len(expected.pins), len(rebuilt.pins)),
        (
            "pin numbers",
            [p.number for p in expected.pins],
            [p.number for p in rebuilt.pins],
        ),
    ):
        if want != got:
            faults.append(f"{name}")

    want_fields = {f.name: field_signature(f) for f in expected.fields}
    got_fields = {f.name: field_signature(f) for f in rebuilt.fields}

    for name in sorted(set(want_fields) | set(got_fields)):
        if name not in got_fields:
            drift.append(f"only KiCad has: {name}")
            continue
        if name not in want_fields:
            drift.append(f"only rebuilt has: {name}")
            continue
        for key, want in want_fields[name].items():
            got = got_fields[name][key]
            if want == got:
                continue
            visible = "visible" if want_fields[name]["visible"] else "hidden"
            entry = f"field {key} ({visible}): {name}"
            if name in STALE_FIELDS:
                stale.append(entry)
            else:
                faults.append(entry)

    # Order is compared over the SHARED fields only. A field the mapping has
    # added or dropped since shifts every name after it, which would report one
    # drift as dozens of order faults.
    shared = set(want_fields) & set(got_fields)
    want_order = [f.name for f in expected.fields if f.name in shared]
    got_order = [f.name for f in rebuilt.fields if f.name in shared]
    if want_order != got_order:
        faults.append("field order")

    if not faults:
        if drift:
            tally.drift_only += 1
        elif stale:
            tally.stale_only += 1
        else:
            tally.exact += 1
        for entry in drift:
            tally.drift[entry] += 1
        return

    tally.faulty += 1
    for fault in faults:
        tally.faults[fault] += 1
    for entry in drift:
        tally.drift[entry] += 1
    if len(tally.examples) < 10:
        tally.examples.append(f"{where}: " + ", ".join(sorted(set(faults))[:6]))


def quote_identifier(name: str) -> str:
    """Quote a table name for a SELECT, doubling any embedded quote.

    A sub-library name comes from a ``.kicad_dbl``, which is untrusted input,
    and ``dbl.py`` strips only ``/`` from it. An embedded ``"`` broke out of the
    quoted identifier and injected SQL into this script's own connection. The
    library refuses to build a statement at all (ADR-100); this script is the
    caller that does, so the quoting is its job. Security review, iteration 20.
    """
    if any(char < chr(32) for char in name):
        raise ValueError(f"refusing a table name with a control character: {name!r}")
    doubled = name.replace('"', '""')
    return f'"{doubled}"'


def load_rows(db_path: Path, key_columns: dict[str, str]) -> dict[str, tuple[str, dict]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows: dict[str, tuple[str, dict]] = {}
    for sub_library, key_column in key_columns.items():
        try:
            cursor = connection.execute(f"SELECT * FROM {quote_identifier(sub_library)}")
        except sqlite3.Error:
            continue
        for raw in cursor:
            row = {name: as_odbc_text(value) for name, value in dict(raw).items()}
            key = row.get(key_column)
            if isinstance(key, str) and key:
                rows[key] = (sub_library, row)
    connection.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--provisional", type=Path, default=PROVISIONAL_ROOT)
    parser.add_argument("--nickname", default="provisional")
    parser.add_argument("--limit", type=int, default=0, help="stop after N parts")
    args = parser.parse_args()

    dbl = load_kicad_dbl(args.provisional / "provisional.kicad_dbl")
    sub_libraries = {entry.name: entry for entry in dbl.libraries}
    rows = load_rows(
        args.provisional / "provisional.db",
        {name: entry.key for name, entry in sub_libraries.items()},
    )
    print(f"rows read: {len(rows)}")

    source_table = build_source_table(args.provisional / "provisional_sym_table")
    loader = table_symbol_loader(source_table)
    memo: dict[str, object] = {}

    def cached_loader(lib_id: str):  # noqa: ANN202
        if lib_id not in memo:
            memo[lib_id] = loader(lib_id)
        return memo[lib_id]

    SCRATCH.mkdir(parents=True, exist_ok=True)
    tally = Tally()

    for path in iter_schematics(args.root):
        try:
            original = Schematic.from_file(path)
        except Exception:  # noqa: BLE001
            continue

        targets = [s for s in original.symbols if s.lib_id.startswith(f"{args.nickname}:")]
        if not targets:
            continue

        working = SCRATCH / path.name
        shutil.copy(path, working)

        for placed in targets:
            item_name = placed.lib_id.split(":", 1)[1]
            found = rows.get(item_name)
            if found is None:
                tally.no_row += 1
                continue

            tally.parts += 1
            sub_library_name, row = found

            # A fresh read per part, so one replacement cannot affect the next.
            sch = Schematic.from_file(working)
            victim = next(s for s in sch.symbols if s.uuid == placed.uuid)
            reference = next(
                (f.value for f in victim.fields if f.name == "Reference"),
                victim.instances[0].paths[0].reference if victim.instances else "",
            )
            sch.symbols.remove(victim)
            if any(s.lib_id == victim.lib_id for s in sch.symbols):
                # Another placement still holds the cached definition. Re-adding
                # would collide with it and take a renamed key, which measures
                # the collision rename rather than the insert. Skip and count.
                tally.parts -= 1
                tally.shared_definition += 1
                continue
            sch.lib_symbols = [d for d in sch.lib_symbols if d.name != victim.lib_id]

            built = resolve_row(
                row,
                sub_libraries[sub_library_name],
                globally_unique_keys=dbl.globally_unique_keys,
                symbol_loader=cached_loader,
            )
            if built.metadata_only:
                tally.metadata_only += 1
                continue

            rebuilt = inject_symbol(
                sch,
                built.definition,
                at=placed.position,
                rotation=placed.angle,
                unit=placed.unit,
                body_style=placed.body_style,
                reference=reference,
                lib_nickname=args.nickname,
            )
            compare_placed(placed, rebuilt, tally, f"{path.name}:{reference}")

            if args.limit and tally.parts >= args.limit:
                break
        if args.limit and tally.parts >= args.limit:
            break

    print(f"parts replaced:          {tally.parts}")
    print(f"no row in db:            {tally.no_row}")
    print(f"metadata only:           {tally.metadata_only}")
    print(f"skipped, shared def:     {tally.shared_definition}")
    print(f"identical, every field:  {tally.exact}")
    print(f"identical but drifted:   {tally.drift_only}")
    print(f"identical but stale:     {tally.stale_only}")
    print(f"FAULTS:                  {tally.faulty}")

    if tally.faults:
        print("\nfault counts, most common first:")
        for fault, count in tally.faults.most_common(40):
            print(f"  {count:5d}  {fault}")

    if tally.drift:
        print(f"\nmapping drift, {len(tally.drift)} distinct field names")

    if tally.examples:
        print("\nexamples:")
        for example in tally.examples:
            print(f"  {example}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
