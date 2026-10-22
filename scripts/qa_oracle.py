#!/usr/bin/env python
"""Round-trip the KiCad qa/data corpus, after normalising it with kicad-cli.

This complements ``corpus_test.py`` rather than repeating it. ``corpus_test.py``
grades files that are already at the target version. Most of KiCad's own qa
fixtures are not: they span 2020 to 2026 on purpose, because they test the
parser's back-compatibility.

This script makes those files usable as writer oracles. For each fixture it:

1. copies the containing directory into a workspace, so sub-sheets come along
2. runs ``kicad-cli <sch|pcb> upgrade`` REPEATEDLY, until two saves in a row
   produce the same bytes
3. round-trips that settled file through this library
4. classifies the first difference, instead of only reporting that one exists

Step 2 is what earns the corpus. KiCad's output is the byte-identity bar by
definition, so an upgraded fixture is a valid oracle whatever version it started
at.

The repeat in step 2 is not caution. It is required. KiCad's FIRST save of a
legacy file can hold state the file itself cannot carry, so that save is not a
fixed point and not an oracle. The measured case is a library symbol arc. The
old radius form states an exact centre, KiCad sorts on that centre, then writes
start, mid and end. Those three points cannot recover the exact centre, so
KiCad's own next save sorts the arcs differently.

An earlier version of this script graded against the first save. It reported
three defects that did not exist, and sent a source audit after one of them.
Every fixture measured so far settles by the second save.

A fixture that never settles lands in ``oracle_unstable``. It carries no
verdict, because KiCad has no single answer to compare against.

Step 4 is what makes a failure actionable. A byte diff says two files differ. A
classified diff names the failure mode.

The fixture list comes from the shared source-truth directory. Point at it with
``--truth`` or ``$KICAD_TRUTH``. Point at kicad-cli with ``$KICAD_CLI``.

Examples::

    python scripts/qa_oracle.py --format sch --limit 40
    python scripts/qa_oracle.py --format pcb --no-upgrade
    python scripts/qa_oracle.py --format sch --report qa-oracle.md

Exit code is 0 when every graded file round-trips byte-identically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eedesigncore.common.io_mixin import (  # noqa: E402
    KicadFileMixin,
    _resolve_line_ending,
)
from eedesigncore.exceptions import KicadParseError  # noqa: E402
from eedesigncore.pcb.models import Board  # noqa: E402
from eedesigncore.sch.models import Schematic  # noqa: E402

DEFAULT_TRUTH = Path(os.environ.get("KICAD_TRUTH", "kicad-token-tables"))
DEFAULT_CLI = Path(os.environ.get("KICAD_CLI", r"C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"))

FORMATS = {
    "sch": (".kicad_sch", Schematic, "sch"),
    "pcb": (".kicad_pcb", Board, "pcb"),
}

OK = "ok"
BYTE_MISMATCH = "byte_mismatch"
PARSE_ERROR = "parse_error"
WRITE_ERROR = "write_error"
UPGRADE_FAILED = "upgrade_failed"
ORACLE_UNSTABLE = "oracle_unstable"

# Buckets that say nothing about this library. KiCad refused the file, or KiCad
# never settled on one answer, so no verdict is possible either way.
NOT_A_VERDICT = (UPGRADE_FAILED, ORACLE_UNSTABLE)


# --------------------------------------------------------------------------
# diff triage
# --------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_HEAD = re.compile(r"\(\s*([a-z][a-z0-9_]*)")


def classify_diff(expected: str, produced: str) -> tuple[str, str]:
    """Name the failure mode, and show the first line that differs.

    Each test runs on the whole file, so the answer describes the file and not
    only one line.

    Test ORDER is load bearing. A pure reordering whose moved lines differ only
    in numbers also satisfies the number test. Ask the reordering question
    first, or a moved element gets reported as a bad value and sends the reader
    hunting a defect that is not there.
    """
    exp_lines = expected.splitlines()
    prod_lines = produced.splitlines()

    idx = next(
        (i for i in range(min(len(exp_lines), len(prod_lines))) if exp_lines[i] != prod_lines[i]),
        min(len(exp_lines), len(prod_lines)),
    )
    detail = ""
    if idx < len(exp_lines) and idx < len(prod_lines):
        detail = (
            f"line {idx + 1}\n  kicad: {exp_lines[idx][:150]}\n  ours : {prod_lines[idx][:150]}"
        )
    elif len(exp_lines) != len(prod_lines):
        detail = f"line count {len(exp_lines)} vs {len(prod_lines)}"

    if [line.strip() for line in exp_lines] == [line.strip() for line in prod_lines]:
        return "indentation", detail

    # Ask this BEFORE the number test. Both files hold the same lines, so
    # something moved rather than changed.
    if Counter(exp_lines) == Counter(prod_lines):
        return "element_order", detail

    exp_flat = _NUMBER.sub("N", expected)
    prod_flat = _NUMBER.sub("N", produced)
    if exp_flat == prod_flat:
        return "number_format", detail

    exp_tokens = Counter(_HEAD.findall(expected))
    prod_tokens = Counter(_HEAD.findall(produced))
    if exp_tokens != prod_tokens:
        missing = exp_tokens - prod_tokens
        extra = prod_tokens - exp_tokens
        parts = []
        if missing:
            parts.append("we omit " + " ".join(sorted(missing)[:6]))
        if extra:
            parts.append("we add " + " ".join(sorted(extra)[:6]))
        return "token_set", "; ".join(parts) + "\n" + detail

    if expected.replace('"', "") == produced.replace('"', ""):
        return "quoting", detail

    return "content", detail


# --------------------------------------------------------------------------


@dataclass
class Result:
    path: str
    status: str
    category: str = ""
    detail: str = ""
    version_before: int | None = None
    version_after: int | None = None
    upgrade_passes: int = 0


@dataclass
class Workspace:
    """One copied qa directory, upgraded once and reused by every file in it."""

    root: Path
    upgraded: bool = False
    failures: dict[str, str] = field(default_factory=dict)


_VERSION_RE = re.compile(rb"\(version\s+(\d+)\s*\)")


def file_version(raw: bytes) -> int | None:
    match = _VERSION_RE.search(raw)
    return int(match.group(1)) if match else None


def expected_bytes(obj: KicadFileMixin) -> bytes:
    """Reproduce exactly what to_file(line_ending='preserve') would write."""
    content = obj.to_string()
    target = _resolve_line_ending("preserve", obj._source_line_ending)
    if target == "crlf":
        return content.replace("\n", "\r\n").encode("utf-8")
    return content.encode("utf-8")


def run_upgrade(cli: Path, kind: str, path: Path, timeout: int) -> str | None:
    """Rewrite one file at the current version. Return an error string or None."""
    try:
        proc = subprocess.run(
            [str(cli), kind, "upgrade", "--force", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError as exc:
        return f"cannot run kicad-cli: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return tail[-1][:200] if tail else f"exit {proc.returncode}"
    return None


def upgrade_to_fixed_point(
    cli: Path, kind: str, path: Path, timeout: int, max_passes: int
) -> tuple[str | None, int, bool]:
    """Save through KiCad until two runs in a row produce the same bytes.

    A single save is NOT a safe oracle. KiCad's first save of a legacy file can
    hold state the file itself cannot carry. A library symbol arc is the
    measured case: the old radius form states an exact centre, KiCad sorts on
    that centre, then writes start, mid and end. Those three points cannot
    recover the exact centre, so KiCad's own next save sorts differently.

    Grading against that first save marks a correct writer wrong. Every fixture
    measured so far settles by the second save.

    Returns the error text or None, the pass count, and whether it settled.
    """
    previous: bytes | None = None
    for attempt in range(1, max_passes + 1):
        error = run_upgrade(cli, kind, path, timeout)
        if error:
            return error, attempt, False
        current = path.read_bytes()
        if current == previous:
            return None, attempt, True
        previous = current
    return None, max_passes, False


def grade(path: Path, loader: type[KicadFileMixin], before: int | None) -> Result:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Result(str(path), WRITE_ERROR, detail=f"read failed: {exc!r}")

    try:
        obj = loader.from_file(path)
    except KicadParseError as exc:
        return Result(
            str(path),
            PARSE_ERROR,
            detail=str(exc)[:300],
            version_before=before,
            version_after=file_version(raw),
        )
    except Exception as exc:  # noqa: BLE001 - a sweep must not crash
        return Result(
            str(path),
            PARSE_ERROR,
            detail=f"{type(exc).__name__}: {exc}"[:300],
            version_before=before,
            version_after=file_version(raw),
        )

    try:
        produced = expected_bytes(obj)
    except Exception as exc:  # noqa: BLE001
        return Result(
            str(path),
            WRITE_ERROR,
            detail=f"{type(exc).__name__}: {exc}"[:300],
            version_before=before,
            version_after=file_version(raw),
        )

    if produced == raw:
        return Result(str(path), OK, version_before=before, version_after=file_version(raw))

    category, detail = classify_diff(
        raw.decode("utf-8", errors="replace"), produced.decode("utf-8", errors="replace")
    )
    return Result(
        str(path),
        BYTE_MISMATCH,
        category=category,
        detail=detail,
        version_before=before,
        version_after=file_version(raw),
    )


def load_fixtures(truth: Path, suffix: str, args: argparse.Namespace) -> list[dict]:
    index = truth / "fixtures.json"
    if not index.exists():
        sys.exit(f"{index} not found. Run build_truth.py in {truth} first.")
    rows = [r for r in json.loads(index.read_text(encoding="utf-8")) if r["format"] == suffix]
    if args.min_version:
        rows = [r for r in rows if (r["version"] or 0) >= args.min_version]
    if args.max_bytes:
        rows = [r for r in rows if r["bytes"] <= args.max_bytes]
    rows.sort(key=lambda r: r["bytes"])
    return rows[: args.limit] if args.limit else rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--format", choices=sorted(FORMATS), required=True)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument(
        "--kicad-source",
        type=Path,
        default=Path(os.environ.get("KICAD_SOURCE", "kicad-source/kicad")),
    )
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument(
        "--no-upgrade",
        action="store_true",
        help="grade the fixtures as they are, with no kicad-cli run",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 means every fixture")
    parser.add_argument("--min-version", type=int)
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--max-passes",
        type=int,
        default=4,
        help="how many KiCad saves to allow before giving up on "
        "a fixed point. Every fixture measured so far "
        "settles by the second save.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    suffix, loader, cli_kind = FORMATS[args.format]
    if not args.no_upgrade and not args.cli.exists():
        sys.exit(f"kicad-cli not found at {args.cli}. Set $KICAD_CLI or use --no-upgrade.")

    rows = load_fixtures(args.truth, suffix, args)
    if not rows:
        sys.exit("no fixtures match the filters")
    print(f"{len(rows)} {suffix} fixtures, upgrade={'off' if args.no_upgrade else 'on'}")

    by_dir: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_dir[str(Path(row["path"]).parent)].append(row)

    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="qa_oracle_") as tmp:
        work = Path(tmp)
        for n, (rel_dir, group) in enumerate(sorted(by_dir.items()), 1):
            src_dir = args.kicad_source / rel_dir
            dst_dir = work / f"d{n}"
            try:
                shutil.copytree(src_dir, dst_dir)
            except OSError as exc:
                for row in group:
                    results.append(
                        Result(row["path"], UPGRADE_FAILED, detail=f"copy failed: {exc}")
                    )
                continue
            for row in group:
                target = dst_dir / Path(row["path"]).name
                if not target.exists():
                    results.append(
                        Result(row["path"], UPGRADE_FAILED, detail="missing after copy")
                    )
                    continue
                passes = 0
                if not args.no_upgrade:
                    error, passes, settled = upgrade_to_fixed_point(
                        args.cli, cli_kind, target, args.timeout, args.max_passes
                    )
                    if error:
                        results.append(
                            Result(
                                row["path"],
                                UPGRADE_FAILED,
                                detail=error,
                                version_before=row["version"],
                                upgrade_passes=passes,
                            )
                        )
                        continue
                    if not settled:
                        results.append(
                            Result(
                                row["path"],
                                ORACLE_UNSTABLE,
                                detail=(
                                    f"KiCad still changed the file after "
                                    f"{args.max_passes} saves, so it is not a "
                                    f"byte-identity oracle"
                                ),
                                version_before=row["version"],
                                upgrade_passes=passes,
                            )
                        )
                        continue
                result = grade(target, loader, row["version"])
                result.path = row["path"]
                result.upgrade_passes = passes
                results.append(result)
            done = len(results)
            print(f"\r  {done}/{len(rows)}", end="", flush=True)
    print()

    return report(results, args)


def report(results: list[Result], args: argparse.Namespace) -> int:
    status_counts = Counter(r.status for r in results)
    category_counts = Counter(r.category for r in results if r.category)
    total = len(results)
    ok = status_counts[OK]

    # A fixture KiCad refused, or never settled on, carries no verdict. Scoring
    # it as a failure would blame this library for KiCad's own behaviour.
    excluded = [r for r in results if r.status in NOT_A_VERDICT]
    graded = total - len(excluded)
    percent = 100.0 * ok / graded if graded else 100.0

    print()
    print(f"=== {graded} fixtures carry a verdict, {ok} byte-identical ({percent:.1f}%) ===")
    for status, count in status_counts.most_common():
        note = "  <- no verdict possible" if status in NOT_A_VERDICT else ""
        print(f"  {status:<16} {count:>5}{note}")
    if category_counts:
        print("\nmismatch categories:")
        for category, count in category_counts.most_common():
            print(f"  {category:<16} {count:>5}")

    multi = [r for r in results if r.upgrade_passes > 2]
    if multi:
        print(f"\n{len(multi)} fixture(s) needed more than one KiCad save to settle.")
        print("KiCad's first save of those files is not a byte-identity oracle:")
        for r in multi[:10]:
            print(f"  {r.upgrade_passes} saves  {r.path}")

    worst = [r for r in results if r.status not in (OK, *NOT_A_VERDICT)]
    if worst:
        print("\nfirst 10 real failures:")
        for r in worst[:10]:
            head = (
                f"  [{r.status}/{r.category}] {r.path}"
                if r.category
                else f"  [{r.status}] {r.path}"
            )
            print(head)
            for line in (r.detail or "").splitlines()[:3]:
                print("      " + line)
    if excluded:
        print(f"\n{len(excluded)} fixture(s) excluded, no verdict:")
        for r in excluded[:10]:
            print(f"  [{r.status}] {r.path}")

    if args.report:
        lines = [
            f"# qa oracle report: {args.format}",
            "",
            f"- fixtures attempted: {total}",
            f"- fixtures carrying a verdict: {graded}",
            f"- byte-identical: {ok} ({percent:.1f}%)",
            f"- excluded, no verdict: {len(excluded)}",
            f"- upgrade: {'off' if args.no_upgrade else 'on'}",
            "",
            "## status",
            "",
        ]
        lines += [f"- `{s}`: {c}" for s, c in status_counts.most_common()]
        if category_counts:
            lines += ["", "## mismatch categories", ""]
            lines += [f"- `{c}`: {n}" for c, n in category_counts.most_common()]
        if multi:
            lines += [
                "",
                "## fixtures KiCad did not settle on first save",
                "",
                "KiCad's first save of these files is not a byte-identity "
                "oracle. The grade uses the settled form.",
                "",
            ]
            lines += [f"- `{r.path}`: {r.upgrade_passes} saves" for r in multi]
        if excluded:
            lines += ["", "## excluded, no verdict", ""]
            lines += [f"- `{r.path}`: {r.status}, {r.detail}" for r in excluded]
        lines += ["", "## failures", ""]
        for r in worst:
            lines.append(f"### {r.path}")
            lines.append(
                f"status `{r.status}` category `{r.category or '-'}` "
                f"version {r.version_before} -> {r.version_after}"
            )
            lines.append("```")
            lines.append(r.detail or "")
            lines.append("```")
        args.report.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nwrote {args.report}")

    return 0 if ok == graded else 1


if __name__ == "__main__":
    sys.exit(main())
