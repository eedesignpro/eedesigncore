#!/usr/bin/env python
"""Score the derived connectivity against KiCad's own netlists.

The comparison is STRUCTURAL, never textual, for three reasons the audits give.

1. The expected file embeds an absolute path, a timestamp, and the writing tool
   version. None of those are connectivity.
2. Net codes are non-deterministic by design (`connection_graph.cpp:2277-2280`).
3. Node references and pin numbers sort differently from net names in the
   exporter (audit 4 contradiction C-1).

So a net is compared as a name against a set of (reference, pin number) members.

The expected netlists were captured once, when the corpus was frozen, and they
live beside the frozen inputs. This script therefore needs no KiCad install and
cannot drift when KiCad updates.

Usage::

    python scripts/connectivity_score.py --root <connectivity-corpus-dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eedesigncore.connectivity.pipeline import derive_from_sheet  # noqa: E402

_NAME = re.compile(r'\(name "([^"]*)"\)')
_REF = re.compile(r'\(ref "([^"]*)"\)')
_PIN = re.compile(r'\(pin "([^"]*)"\)')


def parse_expected(text: str) -> dict[str, frozenset[tuple[str, str]]]:
    """Read the expected netlist into name to member-set form.

    A line scan rather than one large pattern. The exporter writes the netlist
    multi-line and the nesting depth varies, so a single expression over the
    whole file is fragile. This walks the nets section only.
    """
    out: dict[str, set[tuple[str, str]]] = {}
    in_nets = False
    current: str | None = None
    pending_ref: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("(nets"):
            in_nets = True
            continue
        if not in_nets:
            continue
        if line.startswith("(net"):
            current = None
            pending_ref = None
            continue
        hit = _NAME.search(line)
        if hit and current is None:
            current = hit.group(1)
            out.setdefault(current, set())
            continue
        hit = _REF.search(line)
        if hit:
            pending_ref = hit.group(1)
            continue
        hit = _PIN.search(line)
        if hit and current is not None and pending_ref is not None:
            out[current].add((pending_ref, hit.group(1)))
            pending_ref = None

    return {name: frozenset(members) for name, members in out.items() if members}


def score_one(root_sheet: Path, expected_path: Path) -> dict[str, object]:
    """Compare one design. Reports differences rather than a pass or fail only."""
    derived_nets, _report = derive_from_sheet(root_sheet)
    derived = {n.name: n.members for n in derived_nets}
    expected = parse_expected(expected_path.read_text(encoding="utf-8", errors="replace"))

    # Membership agreement, which is the real question. A name can differ while
    # the grouping is right, and that is a naming fault rather than a graph one.
    exp_sets = {frozenset(m) for m in expected.values()}
    der_sets = {frozenset(m) for m in derived.values()}

    exact = {n for n in derived if n in expected and derived[n] == expected[n]}
    return {
        "expected_nets": len(expected),
        "derived_nets": len(derived),
        "name_and_members_match": len(exact),
        "grouping_match": len(exp_sets & der_sets),
        "grouping_only_in_expected": len(exp_sets - der_sets),
        "grouping_only_in_derived": len(der_sets - exp_sets),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    manifest = json.loads((args.root / "MANIFEST.json").read_text(encoding="utf-8"))
    rows = []
    for entry in manifest["entries"]:
        design = entry["project"]
        root_sheet = args.root / design / entry["root_sheet"]
        expected = args.root / "_expected" / f"{design}.net"
        if not root_sheet.exists() or not expected.exists():
            print(f"SKIP {design}: missing input")
            continue
        try:
            row = score_one(root_sheet, expected)
        except Exception as exc:  # a crash is a result, not a reason to stop
            print(f"ERROR {design}: {type(exc).__name__}: {exc}")
            rows.append({"design": design, "error": str(exc), "sheets": entry["sheets"]})
            continue
        row["design"] = design
        row["sheets"] = entry["sheets"]
        rows.append(row)

    flat = [r for r in rows if r.get("sheets") == 1 and "error" not in r]
    multi = [r for r in rows if r.get("sheets", 0) > 1 and "error" not in r]

    print("\n# Connectivity score\n")
    print("The engine handles ONE flat sheet. A design with sub-sheets cannot pass")
    print("yet, because hierarchy propagation is deferred. Both groups are reported")
    print("so no aggregate hides that.\n")

    def show(label: str, group: list[dict[str, object]]) -> None:
        if not group:
            print(f"## {label}: none\n")
            return
        print(f"## {label}\n")
        print(f"{'design':<46} {'exp':>5} {'der':>5} {'exact':>6} {'group':>6} {'miss':>5}")
        for r in sorted(group, key=lambda x: str(x["design"])):
            print(
                f"{str(r['design'])[:46]:<46} {r['expected_nets']:>5} {r['derived_nets']:>5} "
                f"{r['name_and_members_match']:>6} {r['grouping_match']:>6} "
                f"{r['grouping_only_in_expected']:>5}"
            )
        exp = sum(int(r["expected_nets"]) for r in group)
        exact = sum(int(r["name_and_members_match"]) for r in group)
        grp = sum(int(r["grouping_match"]) for r in group)
        print(
            f"\n  totals: expected {exp}, exact name+members {exact} "
            f"({100 * exact / exp:.1f}%), grouping only {grp} ({100 * grp / exp:.1f}%)\n"
        )

    show("Flat designs", flat)
    show("Designs with sub-sheets", multi)
    errors = [r for r in rows if "error" in r]
    if errors:
        print(f"## Crashed: {len(errors)}\n")
        for r in errors:
            print(f"  {r['design']}: {r['error'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
