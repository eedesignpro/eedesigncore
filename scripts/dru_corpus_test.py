"""Check unchanged custom rule bytes without writing to the source files."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from eedesigncore import DrcFile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--file", type=Path, action="append", default=[])
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("The corpus root must be an existing directory")
    files = sorted(set(args.root.rglob("*.kicad_dru")) | set(args.file))
    if not files or any(not path.is_file() for path in files):
        parser.error("Supply existing custom rule files")
    failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="dru-corpus-") as temporary:
        output = Path(temporary) / "output.kicad_dru"
        for path in files:
            try:
                document = DrcFile.from_file(path)
                original = path.read_bytes()
                document.to_file(output)
                if output.read_bytes() != original:
                    failures.append({"path": str(path), "error": "Changed file bytes"})
            except (ValueError, OSError) as error:
                failures.append({"path": str(path), "error": str(error)})
    print(json.dumps({"files": len(files), "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
