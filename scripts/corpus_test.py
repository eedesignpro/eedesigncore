#!/usr/bin/env python
"""Process-pooled round-trip test over a KiCad library / board corpus.

Walks a corpus directory for ``.kicad_mod`` / ``.kicad_sym`` (``--ext both``,
default), ``.kicad_pcb`` (``--ext pcb``, the M2 board gate), ``.kicad_sch``
(``--ext sch``), or the two project files ``.kicad_pro`` and ``.kicad_prl``
(``--ext pro``, the M3 and M4 project gate) and, for each, checks strict
byte-identical round-trip:

    raw bytes -> from_file -> to_string + preserve EOL -> compare to raw bytes

The library corpus is assumed 100% KiCad-generated (byte-identity is the bar).
Boards are graded only at the target version (``20260206``); boards at any other
version are counted in a ``version_skipped`` bucket (not a failure), so the board
gate can be pointed at a mixed-version tree.

Results are bucketed into: ok, byte_mismatch, parse_error (unknown token /
``KicadParseError``), other_error, version_skipped, not_kicad_written, and
excluded. A frequency-ranked report is printed and optionally written to
markdown.

Two exclusion rules keep non-oracle input out of the graded set (ADR-066). The
GLOBAL rule drops every file under an editor-artifact directory, meaning any
path component named ``.history`` or ending in ``-backups``: those trees hold
editor backup copies, not the file the last KiCad save wrote. The NAMED rule
drops the individual files listed in :data:`NAMED_EXCLUSIONS`, each with its
reason. Both rules report into the ``excluded`` bucket, so an excluded file is
always visible in the report and never silently absent.

A file that does not round-trip gets one extra check before it counts as a
failure. KiCad writes every S-expression file through ``KICAD_FORMAT::Prettify``
(``common/io/kicad/kicad_io_utils.cpp``), so KiCad output is a fixed point of
that pass. A file whose own text changes under Prettify was edited after the
last KiCad save, so it is not a valid byte-identity oracle. Such a file lands in
the ``not_kicad_written`` bucket, which does not affect the exit code.

Project files get the JSON equivalent of that test (ADR-050). Prettify does not
apply to JSON. Instead the raw text is parsed and re-serialised with the
canonical nlohmann layout, with every number rendered from its value rather than
from its source literal. KiCad JSON output is a fixed point of that pass.

Project files also use a structural version gate, not ``meta.version``: KiCad 9
and KiCad 10 share project schema versions 3 and 5 (audit
``json-save-path-2026-07-28.md`` contradiction C3). The marker is
``schematic.top_level_sheets`` in the ``.kicad_pro``, and a ``.kicad_prl``
inherits the verdict of the ``.kicad_pro`` beside it. Unlike boards, a project
file is graded first and demoted to ``version_skipped`` only when it both fails
and lacks the marker, so every project file in the tree is exercised.

The corpus root is required — pass ``--root <dir>`` or set
``$KICAD_FILEIO_CORPUS`` (GAP-031: no machine-local default). Examples::

    python scripts/corpus_test.py --root <library-dir>
    python scripts/corpus_test.py --root C:/KiCad --ext pcb        # M2 board gate
    python scripts/corpus_test.py --root C:/KiCad --ext pro        # M3/M4 gate
    python scripts/corpus_test.py --root <dir> --ext mod --limit 200

Exit code is 0 only when every graded file round-trips byte-identically
(version-skipped and not-KiCad-written files do not affect the exit code).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from eedesigncore.common.io_mixin import KicadFileMixin, _resolve_line_ending
from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.pro.models import V10_MARKER_PATH, ProjectFile, ProjectLocalSettings
from eedesigncore.pro.parser import parse_json
from eedesigncore.pro.tree import JsonTree
from eedesigncore.pro.writer import dump_json
from eedesigncore.sch.models import Schematic
from eedesigncore.sexpr.writer import prettify
from eedesigncore.sym.models import SymbolLib

# GAP-031: no hardcoded machine-local path. The corpus root must be supplied
# explicitly via --root or $KICAD_FILEIO_CORPUS; main() errors clearly if unset.
DEFAULT_ROOT: str | None = None

# Round-trip target versions (v10-corpus-catalog-2026-07-13.md §3). Files at any
# other version are counted in the VERSION_SKIPPED bucket, not treated as errors
# (they carry not-yet-modeled legacy tokens and are not byte-identity targets).
TARGET_BOARD_VERSION = 20260206
TARGET_SCH_VERSION = 20260306
_VERSION_RE = re.compile(rb"\(version\s+(\d+)\)")


def default_workers() -> int:
    """Detected CPU count minus 2, floored at 1 (always leave a core free)."""
    return max(1, (os.cpu_count() or 1) - 2)


OK = "ok"
BYTE_MISMATCH = "byte_mismatch"
PARSE_ERROR = "parse_error"
OTHER_ERROR = "other_error"
VERSION_SKIPPED = "version_skipped"
NOT_KICAD_WRITTEN = "not_kicad_written"
EXCLUDED = "excluded"

_STATUSES = (
    OK,
    BYTE_MISMATCH,
    PARSE_ERROR,
    OTHER_ERROR,
    VERSION_SKIPPED,
    NOT_KICAD_WRITTEN,
    EXCLUDED,
)
# Statuses that do not count as failures for the exit code.
_NON_FAILURES = (OK, VERSION_SKIPPED, NOT_KICAD_WRITTEN, EXCLUDED)
_EXT_LABEL = {
    "mod": ".kicad_mod",
    "sym": ".kicad_sym",
    "pcb": ".kicad_pcb",
    "sch": ".kicad_sch",
    "pro": ".kicad_pro",
    "prl": ".kicad_prl",
}
# Report column order.
_EXT_ORDER = ("mod", "sym", "pcb", "sch", "pro", "prl")
# Suffix of the file extension to bucket name. An unlisted suffix grades as a
# board, which keeps the historical default of check_file.
_EXT_OF_SUFFIX = {
    ".kicad_mod": "mod",
    ".kicad_sym": "sym",
    ".kicad_pcb": "pcb",
    ".kicad_sch": "sch",
    ".kicad_pro": "pro",
    ".kicad_prl": "prl",
}

# GLOBAL exclusion rule (ADR-066). A directory named ``.history``, or named with
# the ``-backups`` suffix, holds editor backup copies of a project. A backup copy
# is not the file the last KiCad save wrote, so it is not a byte-identity oracle.
# The rule is principled and needs no per-file entry.
_ARTIFACT_DIR_NAMES = frozenset({".history"})
_ARTIFACT_DIR_SUFFIX = "-backups"

# NAMED exclusion rule (ADR-066). One entry per file the operator removed from
# the graded set, each with its reason. Keys are path SUFFIXES, matched case
# insensitively with either slash direction, because the corpus lives on Windows.
# A suffix carries no drive letter and no operator directory tree, so the table
# is portable across machines and holds no path outside the corpus itself
# (LOW-11). Each entry still names one file uniquely.
#
# The entries name private corpus files, so they live in
# ``corpus_named_exclusions.json`` beside this script, and the public copy
# leaves that file out. Without it the table is empty and every file is graded.
NAMED_EXCLUSIONS_FILE = Path(__file__).with_name("corpus_named_exclusions.json")


def _load_named_exclusions(path: Path) -> dict[str, str]:
    """Read the named exclusion table. A missing or malformed file gives none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(reason) for key, reason in data.items()}


NAMED_EXCLUSIONS: dict[str, str] = _load_named_exclusions(NAMED_EXCLUSIONS_FILE)


def _normalized(path: Path) -> str:
    """Absolute path in one comparable form: forward slashes, case folded."""
    try:
        text = str(path.resolve())
    except OSError:
        text = str(path)
    return text.replace("\\", "/").casefold()


def _normalized_suffix(suffix: str) -> str:
    """A named-exclusion key in the same comparable form, WITHOUT resolving it.

    ``_normalized`` resolves against the working directory, which would turn a
    relative suffix into an absolute path. A key is compared as text only.
    """
    return suffix.replace("\\", "/").casefold().lstrip("/")


_NAMED_EXCLUSIONS_NORMALIZED = {
    _normalized_suffix(key): reason for key, reason in NAMED_EXCLUSIONS.items()
}


def _is_editor_artifact(path: Path) -> bool:
    """True when any directory on the path is an editor backup directory."""
    for part in path.parts[:-1]:
        name = part.casefold()
        if name in _ARTIFACT_DIR_NAMES or name.endswith(_ARTIFACT_DIR_SUFFIX):
            return True
    return False


def _named_exclusion_reason(path: Path) -> str | None:
    """Reason from the named table, matched on a path SUFFIX.

    The match is anchored at a directory boundary, so the key
    ``module/power.kicad_sch`` cannot match ``.../other-power.kicad_sch``.
    """
    text = _normalized(path)
    for suffix, reason in _NAMED_EXCLUSIONS_NORMALIZED.items():
        if text == suffix or text.endswith("/" + suffix):
            return reason
    return None


def exclusion_reason(path: Path) -> str | None:
    """Reason this file never grades, or None when it grades normally."""
    if _is_editor_artifact(path):
        return "editor-artifact directory (.history or *-backups): not a KiCad save"
    return _named_exclusion_reason(path)


# Per-extension byte-identity target version; extensions absent here are not
# version-gated (mod/sym: the whole corpus is a byte-identity target).
_TARGET_VERSION = {"pcb": TARGET_BOARD_VERSION, "sch": TARGET_SCH_VERSION}


@dataclass
class Result:
    path: str
    ext: str  # "mod" | "sym" | "pcb" | "sch"
    status: str
    detail: str = ""  # token / exception repr / diff summary


def _file_version(raw: bytes) -> int | None:
    """First ``(version NNNNN)`` in a board/schematic file, or None if absent."""
    m = _VERSION_RE.search(raw)
    return int(m.group(1)) if m else None


def _expected_bytes(obj: KicadFileMixin) -> bytes:
    """Reproduce exactly what ``to_file(line_ending='preserve')`` would write."""
    content = obj.to_string()
    target = _resolve_line_ending("preserve", obj._source_line_ending)
    if target == "crlf":
        return content.replace("\n", "\r\n").encode("utf-8")
    return content.encode("utf-8")


def _first_diff(original: bytes, produced: bytes) -> str:
    """Human-readable summary of the first divergence between two byte strings."""
    n = min(len(original), len(produced))
    idx = next((i for i in range(n) if original[i] != produced[i]), n)
    lo = max(0, idx - 40)
    hi = idx + 40
    orig_win = original[lo:hi].decode("utf-8", errors="replace")
    prod_win = produced[lo:hi].decode("utf-8", errors="replace")
    return (
        f"len(orig)={len(original)} len(out)={len(produced)} first_diff@{idx}\n"
        f"      orig: ...{orig_win!r}...\n"
        f"      out : ...{prod_win!r}..."
    )


def _is_prettify_fixed_point(raw: bytes) -> bool:
    """True when the file text already matches what KiCad Prettify would emit.

    KiCad writes every S-expression file through ``KICAD_FORMAT::Prettify``, so
    KiCad output is a fixed point of that pass. A file that changes under
    Prettify was edited after the last KiCad save. Byte identity against such a
    file measures the edit, not the writer.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    text = text.replace("\r\n", "\n")
    return prettify(text, mode="NORMAL") == text


def _is_json_fixed_point(raw: bytes) -> bool:
    """True when the file text already matches the layout KiCad would emit.

    This is the JSON counterpart of :func:`_is_prettify_fixed_point`. KiCad
    dumps every project file through one fixed nlohmann layout, so KiCad output
    is a fixed point of that layout. Numbers are rendered from their values, not
    from their source literals, so a hand-written number such as ``1.50`` marks
    the file as edited.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    text = text.replace("\r\n", "\n")
    try:
        tree = parse_json(text)
    except KicadParseError:
        # KiCad emits valid JSON, so unparseable text is not KiCad output.
        return False
    return dump_json(tree, canonical_numbers=True) == text


def _is_valid_json(raw: bytes) -> bool:
    """True when the standard library accepts the file as JSON.

    This separates a malformed file from a defect in this library's own parser.
    """
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return True


def _has_v10_marker(raw: bytes) -> bool:
    """True when a ``.kicad_pro`` carries the KiCad 10 structural marker."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    return JsonTree(parsed).has_path(V10_MARKER_PATH)


def _project_is_v10(path: Path, raw: bytes) -> bool:
    """True when this project file belongs to a KiCad 10 project.

    A ``.kicad_prl`` carries no version marker of its own, so it inherits the
    verdict of the ``.kicad_pro`` beside it.
    """
    if path.suffix == ".kicad_pro":
        return _has_v10_marker(raw)
    try:
        return _has_v10_marker(path.with_suffix(".kicad_pro").read_bytes())
    except OSError:
        return False


def _check_json_file(path: Path) -> Result:
    """Round-trip one ``.kicad_pro`` or ``.kicad_prl`` and classify it."""
    ext = "pro" if path.suffix == ".kicad_pro" else "prl"
    loader: type[KicadFileMixin] = ProjectFile if ext == "pro" else ProjectLocalSettings
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Result(str(path), ext, OTHER_ERROR, f"read failed: {exc!r}")

    try:
        obj = loader.from_file(path)
    except KicadParseError as exc:
        # A file the standard library also rejects is not valid JSON at all, so
        # KiCad never wrote it. Only a file this library alone rejects is a bug.
        status = PARSE_ERROR if _is_valid_json(raw) else NOT_KICAD_WRITTEN
        return Result(str(path), ext, status, str(exc))
    except Exception as exc:  # noqa: BLE001 - corpus sweep must not crash
        return Result(str(path), ext, OTHER_ERROR, f"{type(exc).__name__}: {exc}")

    try:
        produced = _expected_bytes(obj)
    except Exception as exc:  # noqa: BLE001
        return Result(str(path), ext, OTHER_ERROR, f"write {type(exc).__name__}: {exc}")

    if produced == raw:
        return Result(str(path), ext, OK)
    if not _is_json_fixed_point(raw):
        return Result(str(path), ext, NOT_KICAD_WRITTEN, _first_diff(raw, produced))
    if not _project_is_v10(path, raw):
        return Result(str(path), ext, VERSION_SKIPPED, _first_diff(raw, produced))
    return Result(str(path), ext, BYTE_MISMATCH, _first_diff(raw, produced))


def check_file(path_str: str) -> Result:
    """Worker: round-trip one file and classify the outcome. Never raises."""
    path = Path(path_str)
    reason = exclusion_reason(path)
    if reason is not None:
        return Result(path_str, _EXT_OF_SUFFIX.get(path.suffix, "pcb"), EXCLUDED, reason)
    loader: type[KicadFileMixin]
    if path.suffix in (".kicad_pro", ".kicad_prl"):
        return _check_json_file(path)
    if path.suffix == ".kicad_mod":
        ext, loader = "mod", Footprint
    elif path.suffix == ".kicad_sym":
        ext, loader = "sym", SymbolLib
    elif path.suffix == ".kicad_sch":
        ext, loader = "sch", Schematic
    else:
        ext, loader = "pcb", Board
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return Result(path_str, ext, OTHER_ERROR, f"read failed: {exc!r}")

    # Boards/schematics not at the target version are skipped (not byte-identity
    # targets; may carry unmodeled legacy tokens). Detect before load to avoid
    # parse noise. mod/sym have no version gate (whole corpus is a target).
    target = _TARGET_VERSION.get(ext)
    if target is not None:
        version = _file_version(raw)
        if version != target:
            return Result(path_str, ext, VERSION_SKIPPED, f"version={version}")

    try:
        obj = loader.from_file(path)
    except KicadParseError as exc:
        return Result(path_str, ext, PARSE_ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001 - corpus sweep must not crash
        return Result(path_str, ext, OTHER_ERROR, f"{type(exc).__name__}: {exc}")

    try:
        produced = _expected_bytes(obj)
    except Exception as exc:  # noqa: BLE001
        return Result(path_str, ext, OTHER_ERROR, f"write {type(exc).__name__}: {exc}")

    if produced == raw:
        return Result(path_str, ext, OK)
    if not _is_prettify_fixed_point(raw):
        return Result(path_str, ext, NOT_KICAD_WRITTEN, _first_diff(raw, produced))
    return Result(path_str, ext, BYTE_MISMATCH, _first_diff(raw, produced))


def discover(root: Path, ext: str) -> list[str]:
    files: list[str] = []
    if ext in ("mod", "both"):
        files += [str(p) for p in root.rglob("*.kicad_mod")]
    if ext in ("sym", "both"):
        files += [str(p) for p in root.rglob("*.kicad_sym")]
    if ext in ("pcb", "boards"):
        files += [str(p) for p in root.rglob("*.kicad_pcb")]
    if ext == "sch":
        files += [str(p) for p in root.rglob("*.kicad_sch")]
    if ext == "pro":
        # One flag covers both project files: they are one project layer, they
        # share one loader design, and they are always graded together (ADR-050).
        files += [str(p) for p in root.rglob("*.kicad_pro")]
        files += [str(p) for p in root.rglob("*.kicad_prl")]
    files.sort()
    return files


def _bucketed(results: list[Result], status: str, ext: str) -> list[Result]:
    return [r for r in results if r.status == status and r.ext == ext]


def render_report(results: list[Result], elapsed: float, workers: int, examples: int) -> str:
    lines: list[str] = []
    out = lines.append
    out("# Corpus Round-Trip Report")
    out("")
    out(f"- Files: **{len(results)}**  |  workers: {workers}  |  elapsed: {elapsed:.1f}s")
    out("")

    # Totals table split by extension (only extensions present in the run).
    exts = [e for e in _EXT_ORDER if any(r.ext == e for r in results)]
    out("## Totals")
    out("")
    out("| Status | " + " | ".join(_EXT_LABEL[e] for e in exts) + " | total |")
    out("|---|" + "---:|" * (len(exts) + 1))
    for status in _STATUSES:
        counts = [len(_bucketed(results, status, e)) for e in exts]
        row = " | ".join(str(c) for c in counts)
        out(f"| {status} | {row} | {sum(counts)} |")
    out("")

    # Unknown-token / parse-error histogram (the modeling gap list).
    for ext in exts:
        label = _EXT_LABEL[ext]
        errs = _bucketed(results, PARSE_ERROR, ext)
        if not errs:
            continue
        hist: dict[str, list[str]] = defaultdict(list)
        for r in errs:
            hist[r.detail].append(r.path)
        out(f"## Parse-error histogram — {label} ({len(errs)} files)")
        out("")
        out("| count | message | examples |")
        out("|---:|---|---|")
        for msg, paths in sorted(hist.items(), key=lambda kv: -len(kv[1])):
            ex = "<br>".join(Path(p).name for p in paths[:examples])
            out(f"| {len(paths)} | `{msg}` | {ex} |")
        out("")

    # Byte mismatches (real fidelity bugs on files that DO parse).
    mism = [r for r in results if r.status == BYTE_MISMATCH]
    if mism:
        out(f"## Byte mismatches ({len(mism)} files)")
        out("")
        for r in mism[: examples * 4]:
            out(f"- `{r.path}`")
            out("  ```")
            for ln in r.detail.splitlines():
                out(f"  {ln}")
            out("  ```")
        out("")

    # Files that Prettify itself would rewrite: edited after the last KiCad save,
    # so they are not byte-identity oracles.
    edited = [r for r in results if r.status == NOT_KICAD_WRITTEN]
    if edited:
        out(f"## Not KiCad-written ({len(edited)} files)")
        out("")
        out("These files change under the layout pass that KiCad itself applies")
        out("(Prettify for S-expressions, the nlohmann layout for JSON), so they")
        out("were edited after the last KiCad save. They do not count as failures.")
        out("")
        for r in edited[: examples * 4]:
            out(f"- `{r.path}`")
            out("  ```")
            for ln in r.detail.splitlines():
                out(f"  {ln}")
            out("  ```")
        out("")

    # Files the two exclusion rules keep out of the graded set (ADR-066). They
    # are always listed, so an exclusion is visible rather than silent.
    excluded = [r for r in results if r.status == EXCLUDED]
    if excluded:
        out(f"## Excluded ({len(excluded)} files)")
        out("")
        out("These files never grade. Each line carries the rule that removed it:")
        out("the global editor-artifact rule, or a named entry with its reason.")
        out("")
        for r in excluded:
            out(f"- `{r.path}` - {r.detail}")
        out("")

    # Other errors (validation / unexpected).
    other = [r for r in results if r.status == OTHER_ERROR]
    if other:
        ohist: dict[str, list[str]] = defaultdict(list)
        for r in other:
            ohist[r.detail].append(r.path)
        out(f"## Other errors ({len(other)} files)")
        out("")
        out("| count | error | examples |")
        out("|---:|---|---|")
        for msg, paths in sorted(ohist.items(), key=lambda kv: -len(kv[1])):
            ex = "<br>".join(Path(p).name for p in paths[:examples])
            out(f"| {len(paths)} | `{msg}` | {ex} |")
        out("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("KICAD_FILEIO_CORPUS", DEFAULT_ROOT),
        help="corpus root (required; or set $KICAD_FILEIO_CORPUS)",
    )
    parser.add_argument(
        "--ext",
        choices=("mod", "sym", "both", "pcb", "sch", "pro"),
        default="both",
        help="'both' = mod+sym; 'pcb' = boards, 'sch' = schematics, "
        "'pro' = .kicad_pro plus .kicad_prl "
        "(M2/M3/M4 gates; non-target versions skipped)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers(),
        help="process-pool size (default: detected CPU count - 2, min 1)",
    )
    parser.add_argument("--limit", type=int, default=0, help="cap files (0 = all)")
    parser.add_argument("--examples", type=int, default=3, help="example files per bucket")
    parser.add_argument("--report", type=Path, default=None, help="write markdown report here")
    args = parser.parse_args(argv)

    if args.root is None:
        print(
            "error: no corpus root. Pass --root <dir> or set $KICAD_FILEIO_CORPUS "
            "(GAP-031: no machine-local default).",
            file=sys.stderr,
        )
        return 2
    root = Path(args.root)
    if not root.is_dir():
        print(f"corpus root not found: {root}", file=sys.stderr)
        return 2

    files = discover(root, args.ext)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"no corpus files under {root}", file=sys.stderr)
        return 2

    print(
        f"detected {os.cpu_count()} CPUs; checking {len(files)} files "
        f"with {args.workers} workers...",
        file=sys.stderr,
    )
    start = time.perf_counter()
    results: list[Result]
    if args.workers <= 1:
        results = [check_file(f) for f in files]
    else:
        # Import here so a --workers 1 run needs no pool machinery.
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(check_file, files, chunksize=32))
    elapsed = time.perf_counter() - start

    report = render_report(results, elapsed, args.workers, args.examples)
    print(report)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
        print(f"\nreport written to {args.report}", file=sys.stderr)

    # VERSION_SKIPPED is not a failure (non-target boards are out of scope).
    # NOT_KICAD_WRITTEN is not a failure either (the file is not a KiCad oracle),
    # nor is EXCLUDED (the file is out of the graded set by rule, ADR-066).
    skipped = sum(1 for r in results if r.status == VERSION_SKIPPED)
    edited = sum(1 for r in results if r.status == NOT_KICAD_WRITTEN)
    excluded_count = sum(1 for r in results if r.status == EXCLUDED)
    failures = sum(1 for r in results if r.status not in _NON_FAILURES)
    graded = len(results) - skipped - edited - excluded_count
    ok = graded - failures
    print(
        f"\n{ok}/{graded} byte-identical; {failures} failures; "
        f"{skipped} version-skipped; {edited} not-KiCad-written; "
        f"{excluded_count} excluded",
        file=sys.stderr,
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
