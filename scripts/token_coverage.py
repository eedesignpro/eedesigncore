#!/usr/bin/env python
"""Measure this library against KiCad's own token lists.

KiCad checks in one keyword file per format and generates its lexer from it.
That file is the complete legal vocabulary. ``build_truth.py`` in the shared
source-truth directory turns each one into a JSON table with file evidence.

This script compares those tables against ``src/``. A token counts as known when
it appears anywhere in the Python source as a word. The test is deliberately
generous, so it OVERSTATES coverage. Every gap it reports is therefore real,
and the true gap may be larger.

Read the evidence column before acting on a gap. A token whose evidence is
``parsed_only`` is read-side back-compatibility in KiCad, so a writer does not
need it. A token marked ``written_by_sexpr_writer`` is emitted by KiCad, so a
byte-identical writer does need it.

Point at the source-truth directory with ``--truth`` or ``$KICAD_TRUTH``.

Examples::

    python scripts/token_coverage.py
    python scripts/token_coverage.py --format pcb --show-gaps
    python scripts/token_coverage.py --report coverage.md

Exit code is 1 when any format has a gap in a token KiCad's sexpr writer emits.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TRUTH = Path(os.environ.get("KICAD_TRUTH", "kicad-token-tables"))
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The formats this library implements or plans to. Others in the truth
# directory belong to KiCad features outside this project's scope.
IN_SCOPE = {
    "pcb": ".kicad_pcb / .kicad_mod",
    "schematic": ".kicad_sch / .kicad_sym",
    "stroke_params": "shared stroke",
    "pcb_plot_params": "plot settings",
    "embedded_files": "embedded files",
    "lib_table": "fp-lib-table / sym-lib-table",
    "template_fieldnames": "field name templates",
    "drawing_sheet": ".kicad_wks",
    "drc_rules": ".kicad_dru",
}


def source_words(src: Path) -> set[str]:
    blob = []
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        blob.append(path.read_text(encoding="utf-8", errors="replace"))
    return set(WORD.findall("\n".join(blob)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--src", type=Path, default=REPO / "src")
    parser.add_argument("--format", help="limit to one format, for example pcb")
    parser.add_argument("--show-gaps", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    tokens_dir = args.truth / "tokens"
    if not tokens_dir.is_dir():
        sys.exit(f"no token tables at {tokens_dir}. Run build_truth.py first.")

    words = source_words(args.src)
    rows = []
    blocking = 0

    names = [args.format] if args.format else list(IN_SCOPE)
    for name in names:
        path = tokens_dir / f"{name}.json"
        if not path.exists():
            print(f"  skip, no table: {name}")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        gaps = [t for t in doc["tokens"] if t["token"] not in words]
        written = [t for t in gaps if t["evidence"] == "written_by_sexpr_writer"]
        blocking += len(written)
        total = doc["token_count"]
        # The sexpr-writer test only applies to formats those writers own. When
        # no token in the whole table carries that evidence, a zero in the
        # writer-gap column means the test did not apply, not that we are clean.
        applies = any(t["evidence"] == "written_by_sexpr_writer" for t in doc["tokens"])
        rows.append(
            {
                "format": name,
                "label": IN_SCOPE.get(name, name),
                "total": total,
                "known": total - len(gaps),
                "cover": 100.0 * (total - len(gaps)) / total if total else 100.0,
                "gaps": gaps,
                "written_gaps": written,
                "writer_test_applies": applies,
            }
        )

    print(f"{'FORMAT':<22} {'TOKENS':>6} {'KNOWN':>6} {'COVER':>7} {'WRITER GAPS':>12}")
    print("-" * 60)
    for r in rows:
        writer = str(len(r["written_gaps"])) if r["writer_test_applies"] else "n/a"
        print(
            f"{r['format']:<22} {r['total']:>6} {r['known']:>6} {r['cover']:>6.1f}% {writer:>12}"
        )

    unknown = [r["format"] for r in rows if not r["writer_test_applies"]]
    if unknown:
        print()
        print("n/a means no token in that table is written by a kicad_sexpr writer, so")
        print("the writer test does not apply. Judge those by the cover column and by")
        print("reading emitted_in. Formats: " + ", ".join(unknown))

    if args.show_gaps:
        for r in rows:
            if not r["gaps"]:
                continue
            print(f"\n=== {r['format']} ({r['label']}): {len(r['gaps'])} gaps ===")
            by_ev: dict[str, list[str]] = {}
            for t in r["gaps"]:
                by_ev.setdefault(t["evidence"], []).append(t["token"])
            for ev in sorted(by_ev, key=lambda k: -len(by_ev[k])):
                print(f"  [{len(by_ev[ev]):>3}] {ev}")
                print("        " + " ".join(sorted(by_ev[ev])))

    print()
    if blocking:
        print(f"{blocking} gap(s) in tokens a KiCad sexpr writer emits. Run with --show-gaps.")
    else:
        print("No gap in any token a KiCad sexpr writer emits.")

    if args.report:
        lines = [
            "# Token coverage against KiCad's own keyword lists",
            "",
            f"Source truth: `{args.truth}`",
            "",
            "| Format | Tokens | Known | Cover | Writer gaps |",
            "|---|---:|---:|---:|---:|",
        ]
        for r in rows:
            lines.append(
                f"| `{r['format']}` {r['label']} | {r['total']} | "
                f"{r['known']} | {r['cover']:.1f}% | "
                f"{len(r['written_gaps'])} |"
            )
        for r in rows:
            if not r["gaps"]:
                continue
            lines += ["", f"## {r['format']}", ""]
            by_ev2: dict[str, list[str]] = {}
            for t in r["gaps"]:
                by_ev2.setdefault(t["evidence"], []).append(t["token"])
            for ev, toks in sorted(by_ev2.items(), key=lambda kv: -len(kv[1])):
                lines.append(
                    f"- **{ev}** ({len(toks)}): " + " ".join(f"`{t}`" for t in sorted(toks))
                )
        args.report.write_text("\n".join(lines), encoding="utf-8")
        print(f"wrote {args.report}")

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
