#!/usr/bin/env python
"""Measure where a save spends its time, and how item ORDER changes that.

Saving a board runs two phases. ``dump_board`` turns models into flat text and
does every canonical sort on the way. ``prettify`` re-scans that text character
by character and indents it. This script times the two separately, because they
respond to completely different things.

The order trick is the point of this script. Every board in the corpus is
already in KiCad's canonical order, which is the BEST case for Timsort: an
already-sorted list costs n-1 comparisons instead of n log n. A board that a
program just built by step-and-repeat is NOT in that order. Measuring only
corpus files therefore hides whatever the sorts cost a real consumer, so this
script measures each board twice, once as loaded and once with every stream
shuffled.

Usage::

    python scripts/prettify_bench.py --board path/to/panel.kicad_pcb
    python scripts/prettify_bench.py --root C:/KiCad --limit 12
    python scripts/prettify_bench.py --board panel.kicad_pcb --profile

There is no machine-local default path (GAP-031). Pass ``--board`` or ``--root``.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eedesigncore import Board  # noqa: E402
from eedesigncore.pcb.writer import dump_board  # noqa: E402
from eedesigncore.sexpr.writer import prettify  # noqa: E402

SHUFFLE_SEED = 7


@dataclass
class Measurement:
    """One timed save of one board."""

    name: str
    order: str
    load_s: float
    dump_s: float
    prettify_s: float
    flat_bytes: int
    items: int

    @property
    def save_s(self) -> float:
        return self.dump_s + self.prettify_s

    @property
    def prettify_share(self) -> float:
        return self.prettify_s / self.save_s if self.save_s else 0.0


def item_count(board: Board) -> int:
    return (
        len(board.footprints)
        + len(board.drawings)
        + len(board.tracks)
        + len(board.zones)
        + len(board.groups)
        + len(board.generators)
        + len(board.targets)
        + len(board.points)
    )


def shuffled(board: Board, seed: int = SHUFFLE_SEED) -> Board:
    """Return a deep copy with every item stream in a random order.

    This is what a freshly built panel looks like before its first save. The
    writer sorts on emit, so the result is byte-identical to the ordered input;
    only the time changes.
    """
    rng = random.Random(seed)
    out = board.model_copy(deep=True)
    for stream in (
        out.footprints,
        out.drawings,
        out.trailing_drawings,
        out.points,
        out.tracks,
        out.zones,
        out.groups,
        out.generators,
        out.targets,
    ):
        rng.shuffle(stream)
    return out


def measure(path: Path, board: Board, order: str, load_s: float) -> Measurement:
    start = time.perf_counter()
    flat = dump_board(board)
    dump_s = time.perf_counter() - start

    start = time.perf_counter()
    prettify(flat, mode="NORMAL")
    prettify_s = time.perf_counter() - start

    return Measurement(
        name=path.name,
        order=order,
        load_s=load_s,
        dump_s=dump_s,
        prettify_s=prettify_s,
        flat_bytes=len(flat),
        items=item_count(board),
    )


def report(rows: list[Measurement]) -> None:
    header = (
        f"{'board':<30} {'order':<8} {'items':>7} {'flat MB':>8} "
        f"{'load':>7} {'dump':>7} {'pretty':>7} {'save':>7} {'pretty%':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.name[:29]:<30} {row.order:<8} {row.items:7d} "
            f"{row.flat_bytes / 1024 / 1024:8.2f} {row.load_s:6.2f}s {row.dump_s:6.2f}s "
            f"{row.prettify_s:6.2f}s {row.save_s:6.2f}s {row.prettify_share * 100:7.1f}%"
        )

    pairs = {}
    for row in rows:
        pairs.setdefault(row.name, {})[row.order] = row
    both = [p for p in pairs.values() if len(p) == 2]
    if both:
        dump_ratio = sum(p["shuffled"].dump_s / max(p["sorted"].dump_s, 1e-9) for p in both)
        pretty_ratio = sum(
            p["shuffled"].prettify_s / max(p["sorted"].prettify_s, 1e-9) for p in both
        )
        print()
        print(f"mean dump slowdown when unsorted:     {dump_ratio / len(both):5.2f}x")
        print(f"mean prettify change when unsorted:   {pretty_ratio / len(both):5.2f}x")
        print("Prettify near 1.00x means it does not care about item order, only about size.")


def profile_save(board: Board, limit: int = 14) -> None:
    profiler = cProfile.Profile()
    profiler.enable()
    board.to_string()
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(limit)
    print("\n".join(stream.getvalue().splitlines()[4:]))


def boards_under(root: Path, limit: int) -> list[Path]:
    found = []
    for path in root.rglob("*.kicad_pcb"):
        text = str(path)
        if ".history" in text or "-backups" in text:
            continue
        found.append(path)
    found.sort(key=lambda p: p.stat().st_size, reverse=True)
    return found[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, help="one board to measure")
    parser.add_argument("--root", type=Path, help="a tree to sweep, largest boards first")
    parser.add_argument("--limit", type=int, default=8, help="boards to take from --root")
    parser.add_argument("--profile", action="store_true", help="profile one save")
    parser.add_argument("--no-shuffle", action="store_true", help="skip the unsorted pass")
    args = parser.parse_args()

    if not args.board and not args.root:
        parser.error("pass --board or --root. There is no machine-local default (GAP-031).")

    paths = [args.board] if args.board else boards_under(args.root, args.limit)
    rows: list[Measurement] = []
    for path in paths:
        start = time.perf_counter()
        try:
            board = Board.from_file(path)
        except Exception as error:  # noqa: BLE001 - a corpus sweep must not stop
            print(f"skip {path.name}: {type(error).__name__}")
            continue
        load_s = time.perf_counter() - start

        rows.append(measure(path, board, "sorted", load_s))
        if not args.no_shuffle:
            rows.append(measure(path, shuffled(board), "shuffled", load_s))

    report(rows)

    if args.profile and paths:
        print()
        print("=== profile of one save ===")
        profile_save(Board.from_file(paths[0]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
