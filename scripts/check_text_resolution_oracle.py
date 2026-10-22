"""Compare saved expression cases with a discovered KiCad CLI on scratch files."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from eedesigncore import (
    Paper,
    Schematic,
    SchText,
    TextAttributes,
    TextResolutionContext,
    resolve_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixture = (
        Path(__file__).resolve().parents[1] / "tests/fixtures/text_resolution/expressions.json"
    )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    executable = shutil.which("kicad-cli")
    if executable is None:
        parser.error("KiCad CLI is unavailable.")
    text = [
        SchText(
            content=f"CASE{i:03}|" + case["raw"],
            position=(30, 10 + i * 3),
            uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, "text-case-" + str(i))),
            attributes=TextAttributes(font_size=(1.27, 1.27)),
        )
        for i, case in enumerate(data["cases"])
    ]
    sheet = Schematic(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, "text-resolution-oracle")),
        paper=Paper(paper_type="A3"),
        texts=text,
    )
    sheet.to_file(root / "cases.kicad_sch")
    environment = dict(
        os.environ,
        KICAD_CONFIG_HOME=str(root / "settings"),
        KICAD_DOCUMENTS_HOME=str(root / "documents"),
    )
    command = [
        executable,
        "sch",
        "export",
        "svg",
        "--exclude-drawing-sheet",
        "--no-background-color",
        "--output",
        str(root / "svg"),
    ]
    for key, value in data["variables"].items():
        command.extend(["--define-var", key + "=" + value])
    command.append(str(root / "cases.kicad_sch"))
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    (root / "command.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"KiCad export failed with exit status {result.returncode}.")
    xml = ET.parse(root / "svg/cases.svg")
    observed = {
        int(item.text[4:7]): item.text[8:]
        for item in xml.iter("{http://www.w3.org/2000/svg}text")
        if item.text and item.text.startswith("CASE")
    }
    context = TextResolutionContext(variables=data["variables"])
    rows = [
        {
            "raw": case["raw"],
            "expected": case["expected"],
            "kicad": observed.get(i),
            "file_io": resolve_text(case["raw"], context).resolved,
        }
        for i, case in enumerate(data["cases"])
    ]
    mismatches = [row for row in rows if not row["expected"] == row["kicad"] == row["file_io"]]
    version = subprocess.run(
        [executable, "version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    ).stdout.strip()
    report = {
        "kicad_version": version,
        "cases": len(rows),
        "mismatches": mismatches,
        "stderr": result.stderr,
        "results": rows,
    }
    (root / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("kicad_version", "cases", "mismatches", "stderr")}
        )
    )
    return int(bool(mismatches) or bool(result.stderr))


if __name__ == "__main__":
    raise SystemExit(main())
