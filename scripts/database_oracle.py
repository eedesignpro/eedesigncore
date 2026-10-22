"""Check the row-to-definition transform against definitions KiCad wrote.

The oracle has three independent parts, none of them produced by this library:

The comparison separates three outcomes, and only the first is a defect:

- a FAULT, where both sides carry a field and disagree, or where a unit name
  would stop KiCad opening the file at all,
- DRIFT, where the ``.kicad_dbl`` mapping or the drawn symbol changed after
  KiCad wrote the schematic, and
- a STALE field, named in ``STALE_FIELDS``, which cannot match by construction.

1. ``provisional.kicad_dbl`` — the field mapping in force.
2. ``provisional.db`` — 7350 real rows, read here with the standard-library
   sqlite3 module. THIS SCRIPT connects; the library never does. That split is
   the whole point of ADR-100: a caller supplies rows, and the transform is
   backend-blind.
3. Every ``.kicad_sch`` under the corpus root. A schematic caches the
   definition it placed inside ``(lib_symbols)``, so a placement of
   ``provisional:<key>`` carries the definition KiCad built from that row.

For each placement the script rebuilds the definition with
``definition_from_row`` and compares it field for field with what KiCad wrote.

Run:

    .venv/Scripts/python.exe scripts/database_oracle.py --root C:/KiCad
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eedesigncore.common.sch_family.symbol import Symbol  # noqa: E402
from eedesigncore.library import definition_from_row, load_kicad_dbl  # noqa: E402
from eedesigncore.library.catalog import table_symbol_loader  # noqa: E402
from eedesigncore.sch.models import Schematic  # noqa: E402
from eedesigncore.sexpr.parser import parse  # noqa: E402
from eedesigncore.tables.loader import load_lib_table  # noqa: E402
from eedesigncore.tables.resolver import ResolvedLibrary, ResolvedLibTable  # noqa: E402

SKIP_DIRS = {".git", ".venv", "node_modules", ".history", ".scratch"}

PROVISIONAL_ROOT = Path("provisional-library")  # pass --provisional
NICKNAME = "provisional"

STALE_FIELDS = {
    # The database moved on after KiCad wrote the schematic. Stock on hand and
    # unit cost change every day, so the cached value is a photograph of a past
    # day and can never match today's row.
    "AXQtyOnHand",
    "AXStandardCost",
    # Reference is annotation, not row data. No .kicad_dbl maps a column onto
    # it; KiCad's cached definition carries the annotated designator (J2), and
    # annotation belongs to inject_symbol, not to this transform.
    "Reference",
}
"""Fields excluded from the fault count, by name, each with its reason.

Named here rather than hidden in the comparison, so a reader can see exactly
what the measurement does not cover. Pass ``--strict`` to count them.
"""


@dataclass
class Tally:
    strict: bool = False
    placements: int = 0
    compared: int = 0
    no_row: int = 0
    no_source_symbol: int = 0
    exact: int = 0
    drift_only: int = 0
    faulty: int = 0
    stale: int = 0
    unit_name_faults: int = 0
    field_faults: Counter[str] = field(default_factory=Counter)
    drift_fields: Counter[str] = field(default_factory=Counter)
    stale_fields: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)


def iter_schematics(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.kicad_sch"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def build_source_table(table_file: Path) -> ResolvedLibTable:
    """Build the provisional symbol namespace from its own nested table file.

    The table is built by hand, so it carries no confinement: the rows come
    from this script, not from a discovery walk (see ``ResolvedLibTable``).
    """
    table = load_lib_table(parse(table_file.read_text(encoding="utf-8")))
    return ResolvedLibTable(
        kind="sym",
        libraries=[
            ResolvedLibrary(
                nickname=row.name,
                type=row.type,
                uri=row.uri,
                raw_uri=row.uri,
                options=row.options,
                descr=row.descr,
                disabled=row.disabled,
                hidden=row.hidden,
                scope="global",
                source_table=table_file,
            )
            for row in table.rows
        ],
    )


def as_odbc_text(value: object) -> str | None:
    """Render a sqlite value the way an ODBC driver hands it to KiCad.

    This adapter belongs to the ORACLE, not to the library. KiCad reads a row
    value with ``std::any_cast<std::string>``, and the ODBC layer delivers
    every column as a string, so KiCad never sees a number. The sqlite3 module
    does return numbers, so without this the comparison would measure Python
    typing rather than the transform.

    The formatting is read off KiCad's own output: ``LibraryId`` 100647 is
    written ``100647`` and ``AXStandardCost`` is written ``0.00218``, so an
    integral float loses its decimal tail.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


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
    """Return ``sanitized key -> (sub-library name, row)`` for every table."""
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
            if not isinstance(key, str) or not key:
                continue
            rows[key] = (sub_library, row)

    connection.close()
    return rows


def unit_name_faults(symbol: Symbol) -> list[str]:
    """Return one entry per unit name KiCad's parser would refuse.

    Ports the check at ``sch_io_kicad_sexpr_parser.cpp:501-515``: the name must
    start with the parent's item name, and what follows the parent name plus one
    character must be exactly two numbers separated by an underscore.
    """
    item_name = symbol.name.split(":")[-1]
    faults: list[str] = []

    for unit in symbol.units:
        if not unit.name.startswith(item_name):
            faults.append(f"unit name prefix: {unit.name}")
            continue
        suffix = unit.name[len(item_name) + 1 :]
        parts = suffix.split("_")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            faults.append(f"unit name suffix: {unit.name}")

    return faults


def field_map(symbol: Symbol) -> dict[str, tuple[str, bool, bool]]:
    return {f.name: (f.value, f.visible, f.show_name) for f in symbol.fields}


def compare(expected: Symbol, derived: Symbol, tally: Tally, where: str) -> None:
    """Compare two definitions, separating mapping drift from a transform fault.

    A field name present on one side only is DRIFT: the ``.kicad_dbl`` mapping
    changed between the day KiCad wrote the schematic and today, which the
    assessment recorded as open item O3. Drift is counted and named, never
    hidden, but it is not a fault in the transform.

    Everything else is a FAULT: both sides carry the field and disagree on its
    value, its visibility or its name-shown flag, or a symbol-level member
    disagrees.
    """
    faults: list[str] = []
    drift: list[str] = []
    stale: list[str] = []

    def note(kind: str, name: str) -> None:
        if not tally.strict and name in STALE_FIELDS:
            stale.append(f"{kind}: {name}")
        else:
            faults.append(f"{kind}: {name}")

    if expected.name != derived.name:
        faults.append("name")
    # The load-blocking rule, checked strictly: KiCad's parser requires every
    # unit name to start with the parent's item name and to end in exactly two
    # underscore-separated numbers (sch_io_kicad_sexpr_parser.cpp:501-515). A
    # file that breaks it does not open AT ALL, so this is the one comparison
    # where a single failure is a release blocker.
    for fault in unit_name_faults(derived):
        faults.append(fault)

    # A difference in the unit LIST is drift, not a fault: it means the drawn
    # symbol on disk was restructured after KiCad cached this copy. Measured on
    # two IC parts, where KiCad's copy splits 24 pins into body style 0 and an
    # outline into body style 1 while today's file holds 43 items in one unit.
    if [u.name for u in expected.units] != [u.name for u in derived.units]:
        drift.append("unit structure changed in the drawn symbol")
    if (expected.keywords or "") != (derived.keywords or ""):
        faults.append("keywords")
    if list(expected.fp_filters) != list(derived.fp_filters):
        faults.append("fp_filters")
    if expected.exclude_from_sim != derived.exclude_from_sim:
        faults.append("exclude_from_sim")
    if expected.in_bom != derived.in_bom:
        faults.append("in_bom")
    if expected.on_board != derived.on_board:
        faults.append("on_board")

    want = field_map(expected)
    got = field_map(derived)

    for name in sorted(set(want) | set(got)):
        if name not in got:
            drift.append(f"only KiCad has: {name}")
        elif name not in want:
            drift.append(f"only derived has: {name}")
        else:
            if want[name][0] != got[name][0]:
                note("value", name)
            if want[name][1] != got[name][1]:
                note("visible", name)
            if want[name][2] != got[name][2]:
                note("show_name", name)

    tally.compared += 1
    tally.unit_name_faults += sum(1 for f in faults if f.startswith("unit name "))
    for entry in drift:
        tally.drift_fields[entry] += 1
    for entry in stale:
        tally.stale_fields[entry] += 1

    if not faults:
        if stale:
            tally.stale += 1
        elif drift:
            tally.drift_only += 1
        else:
            tally.exact += 1
        return

    tally.faulty += 1
    for fault in faults:
        tally.field_faults[fault] += 1
    if len(tally.examples) < 12:
        tally.examples.append(f"{where}: " + ", ".join(faults[:6]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="corpus root to scan")
    parser.add_argument("--provisional", type=Path, default=PROVISIONAL_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="stop after N placements")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="count the STALE_FIELDS exclusions as faults too",
    )
    args = parser.parse_args()

    dbl = load_kicad_dbl(args.provisional / "provisional.kicad_dbl")
    sub_libraries = {entry.name: entry for entry in dbl.libraries}
    rows = load_rows(
        args.provisional / "provisional.db",
        {name: entry.key for name, entry in sub_libraries.items()},
    )
    print(f"rows read: {len(rows)} across {len(sub_libraries)} sub-libraries")

    source_table = build_source_table(args.provisional / "provisional_sym_table")
    loader = table_symbol_loader(source_table)
    memo: dict[str, Symbol | None] = {}

    def cached_loader(lib_id: str) -> Symbol | None:
        if lib_id not in memo:
            memo[lib_id] = loader(lib_id)
        return memo[lib_id]

    tally = Tally(strict=args.strict)
    seen_keys: set[str] = set()

    for schematic_path in iter_schematics(args.root):
        try:
            schematic = Schematic.from_file(schematic_path)
        except Exception:  # noqa: BLE001 - a corpus file that will not parse is not this test
            continue

        for definition in schematic.lib_symbols:
            if not definition.name.startswith(f"{NICKNAME}:"):
                continue
            item_name = definition.name.split(":", 1)[1]
            tally.placements += 1
            seen_keys.add(item_name)

            found = rows.get(item_name)
            if found is None:
                tally.no_row += 1
                continue

            sub_library_name, row = found
            reference = row.get(sub_libraries[sub_library_name].symbols) or ""
            if cached_loader(reference) is None:
                tally.no_source_symbol += 1
            derived = definition_from_row(
                row,
                sub_libraries[sub_library_name],
                globally_unique_keys=dbl.globally_unique_keys,
                symbol_loader=cached_loader,
            )
            derived.name = f"{NICKNAME}:{item_name}"
            compare(definition, derived, tally, f"{item_name}")

        if args.limit and tally.placements >= args.limit:
            break

    print(f"placements seen:           {tally.placements}")
    print(f"distinct parts:            {len(seen_keys)}")
    print(f"no row in db:              {tally.no_row}")
    print(f"source symbol missing:     {tally.no_source_symbol}")
    print(f"compared:                  {tally.compared}")
    print(f"exact, every field:        {tally.exact}")
    print(f"exact but mapping drifted: {tally.drift_only}")
    print(f"exact but a stale field:   {tally.stale}")
    print(f"TRANSFORM FAULTS:          {tally.faulty}")
    print(f"unit names KiCad would refuse: {tally.unit_name_faults}")

    if tally.field_faults:
        print("\nfault counts, most common first:")
        for fault, count in tally.field_faults.most_common(30):
            print(f"  {count:5d}  {fault}")

    if tally.stale_fields:
        print("\nexcluded as stale or annotation-owned:")
        for entry, count in tally.stale_fields.most_common(10):
            print(f"  {count:5d}  {entry}")

    if tally.drift_fields:
        print(f"\nmapping drift, {len(tally.drift_fields)} distinct field names:")
        for entry, count in tally.drift_fields.most_common(15):
            print(f"  {count:5d}  {entry}")

    if tally.examples:
        print("\nexamples:")
        for example in tally.examples:
            print(f"  {example}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
