"""Compare constructed drawing sheets with the installed KiCad SVG exporter."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from eedesigncore import (
    DrawingSheet,
    Schematic,
    TitleBlock,
    drawing_sheet_graphics,
    render_drawing_sheet_svg,
)


def vertices(svg: str) -> list[list[tuple[float, float]]]:
    result = []
    for element in ET.fromstring(svg).iter():
        kind = element.tag.rsplit("}", 1)[-1]
        if kind == "path":
            values = list(map(float, re.findall(r"[-+]?\d+(?:\.\d+)?", element.attrib["d"])))
            points = list(zip(values[::2], values[1::2], strict=True))
        elif kind == "rect":
            x, y, width, height = (
                float(element.attrib[key]) for key in ("x", "y", "width", "height")
            )
            points = [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]
        else:
            continue
        result.append(sorted(set((round(x, 4), round(y, 4)) for x, y in points)))
    return sorted(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("kicad-cli")
    if cli is None:
        raise SystemExit("KiCad CLI was not found on PATH.")
    fixture_root = Path(__file__).resolve().parents[1] / "tests/fixtures/drawing_sheet"
    schematic = Schematic(uuid=str(uuid.uuid4()), title_block=TitleBlock(title="Probe"))
    schematic.to_file(root / "probe.kicad_sch")
    environment = dict(
        os.environ,
        KICAD_CONFIG_HOME=str(root / "config"),
        KICAD_DOCUMENTS_HOME=str(root / "documents"),
    )
    version = subprocess.run(
        [cli, "version"], capture_output=True, text=True, check=True, timeout=30, env=environment
    ).stdout.strip()
    results: dict[str, object] = {"kicad_version": version}
    failed = False
    for name in ("geometry", "text"):
        template = DrawingSheet.from_file(fixture_root / f"{name}.kicad_wks")
        # Force an edited-template write so native acceptance checks the writer.
        template.items[0].comment = "Native drawing-sheet comparison"
        target = root / f"{name}.kicad_wks"
        template.to_file(target)
        prepared = drawing_sheet_graphics(template, title_block=schematic.title_block)
        ours = render_drawing_sheet_svg(prepared)
        (root / f"{name}-ours.svg").write_text(ours, encoding="utf-8")
        command = [
            cli,
            "sch",
            "export",
            "svg",
            "--no-background-color",
            "--black-and-white",
            "--drawing-sheet",
            str(target),
            "--output",
            str(root / name),
            str(root / "probe.kicad_sch"),
        ]
        run = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
        (root / f"{name}-command.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        entry: dict[str, object] = {
            "command": command,
            "exit_code": run.returncode,
            "stderr": run.stderr,
        }
        results[name] = entry
        if run.returncode or run.stderr:
            failed = True
            continue
        native = (root / name / "probe.svg").read_text(encoding="utf-8")
        if name == "geometry":
            entry["native"] = vertices(native)
            entry["ours"] = vertices(ours)
        else:
            entry["native"] = [
                node.text
                for node in ET.fromstring(native).iter("{http://www.w3.org/2000/svg}text")
            ]
            entry["ours"] = [text.content for text in prepared.schematic.texts]
        entry["match"] = entry["native"] == entry["ours"]
        failed |= not bool(entry["match"])
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
