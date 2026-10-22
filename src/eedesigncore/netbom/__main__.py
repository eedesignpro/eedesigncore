"""Export a netbom to standard output for command-line pipelines."""

from __future__ import annotations

import argparse
import sys

from eedesigncore.netbom import load_netbom


def main() -> None:
    """Read one design path and print JSON or newline-delimited JSON."""
    parser = argparse.ArgumentParser(description="Export board-first KiCad design facts.")
    parser.add_argument("path", help="Local board, schematic, or project file")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--max-records", type=int, default=250000)
    arguments = parser.parse_args()
    try:
        document = load_netbom(arguments.path, max_records=arguments.max_records)
        if arguments.format == "jsonl":
            for line in document.iter_jsonl():
                print(line)
        else:
            print(document.to_json())
    except BrokenPipeError:
        sys.exit(0)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Netbom export failed: {error}\n")


if __name__ == "__main__":
    main()
