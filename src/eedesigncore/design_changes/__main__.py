"""Compare two KiCad files from the command line, or as a git diff command.

Forms::

    python -m eedesigncore.design_changes OLD NEW [options]
    python -m eedesigncore.design_changes PATH OLD-FILE OLD-HEX OLD-MODE NEW-FILE NEW-HEX NEW-MODE

Git calls a configured diff command with the seven-argument form, with nine
arguments for a renamed file, and with one argument for an unmerged file. Git
names a missing side ``/dev/null``. Set up, once per repository::

    git config diff.kicad.command "python -P -m eedesigncore.design_changes"

and in ``.gitattributes``::

    *.kicad_pcb diff=kicad
    *.kicad_sch diff=kicad

``-P`` keeps Python from loading modules out of the repository being compared.
Git runs the command in the top folder of that repository.

Exit status: 0, or 1 with ``--exit-code`` when a change is found, or 2 when a
file cannot be read. Under git, a file that cannot be read prints one line and
exits 0, so git continues with the next file.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from eedesigncore.design_changes.compare import compare_designs, load_design
from eedesigncore.design_changes.printouts import format_change_diff, format_change_report
from eedesigncore.design_changes.records import CHANGE_CATEGORIES

_MISSING = {"/dev/null", "nul"}


def _tolerance(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from None
    if not math.isfinite(value) or not 0.0 <= value < 1.0:
        raise argparse.ArgumentTypeError("must be at least 0 and below 1")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eedesigncore.design_changes",
        description="Compare two KiCad files and print the changes.",
    )
    parser.add_argument("files", nargs="+", help="OLD NEW, or the arguments git passes")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--report", action="store_true", help="print the short report")
    view.add_argument("--json", action="store_true", help="print the change records as JSON")
    parser.add_argument(
        "--tolerance", type=_tolerance, default=0.0, help="number tolerance (mm, degrees)"
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=CHANGE_CATEGORIES,
        help="keep only this category (repeat for more)",
    )
    parser.add_argument("--no-nets", action="store_true", help="omit the per-net board summary")
    parser.add_argument(
        "--exit-code", action="store_true", help="exit with 1 when a change is found"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0911
    """Run the command. Return the exit status."""
    parser = _parser()
    args = parser.parse_args(argv)
    files = args.files
    if len(files) == 2:
        old, new = files
        old_kind = new_kind = None
        before_label, after_label = old, new
    elif len(files) in (7, 9):
        old, new = files[1], files[4]
        old_kind = files[0]
        new_kind = files[7] if len(files) == 9 else files[0]
        before_label, after_label = f"a/{old_kind}", f"b/{new_kind}"
    elif len(files) == 1:
        print(f"{files[0]}: unmerged")
        return 0
    else:
        parser.error("give OLD NEW, or the arguments git passes to a diff command")
    from_git = old_kind is not None
    name = new_kind or new

    if old.lower() in _MISSING or new.lower() in _MISSING:
        state = "added" if old.lower() in _MISSING else "removed"
        shown = (new_kind or new) if state == "added" else (old_kind or old)
        print(f"{shown}: file {state}")
        return 1 if args.exit_code else 0

    try:
        comparison = compare_designs(
            load_design(Path(old), kind=old_kind),
            load_design(Path(new), kind=new_kind),
            tolerance=args.tolerance,
            nets=not args.no_nets,
        )
    except (ValueError, TypeError, OSError) as error:
        message = f"{name}: cannot compare: {error}"
        if from_git:
            print(message)
            return 0
        print(message, file=sys.stderr)
        return 2
    if args.category:
        comparison = comparison.only(*args.category)
    if args.json:
        text = comparison.to_json() + "\n"
    elif args.report:
        text = format_change_report(comparison)
    else:
        text = format_change_diff(comparison, before_label=before_label, after_label=after_label)
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader, such as a pager, closed early. Python's documented recipe.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    return 1 if args.exit_code and not comparison.equal else 0


if __name__ == "__main__":
    sys.exit(main())
