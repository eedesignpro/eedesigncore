"""Compare one installed font against KiCad on generated schematic files."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path

from eedesigncore import (
    Schematic,
    SchText,
    TextAttributes,
    ViewBox,
    load_font,
    render_schematic_svg,
)


def bounds(svg: str) -> tuple[float, float, float, float]:
    pen = BoundsPen(None)
    for node in ET.fromstring(svg).iter():
        if node.tag.endswith("}path") and "d" in node.attrib:
            parse_path(node.attrib["d"], pen)
    if pen.bounds is None:
        raise ValueError("The SVG has no glyph outlines.")
    return tuple(float(value) for value in pen.bounds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-mm", type=float, default=0.02)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("kicad-cli")
    if executable is None:
        parser.error("KiCad CLI was not found on PATH.")
    sheet = Schematic(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, "outline-font-oracle")),
        texts=[
            SchText(
                content="ABC \u2022",
                position=(20, 20),
                attributes=TextAttributes(
                    font_size=(1.27, 1.27),
                    font_face=args.font,
                ),
            ),
        ],
    )
    sheet.to_file(root / "font.kicad_sch")
    environment = dict(
        os.environ,
        KICAD_CONFIG_HOME=str(root / "config"),
        KICAD_DOCUMENTS_HOME=str(root / "documents"),
    )
    command = [
        executable,
        "sch",
        "export",
        "svg",
        "--exclude-drawing-sheet",
        "--output",
        str(root / "svg"),
        str(root / "font.kicad_sch"),
    ]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    (root / "command.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode or result.stderr:
        raise RuntimeError("The native font export failed or reported a diagnostic.")
    ours = render_schematic_svg(sheet, view_box=ViewBox(0, 0, 40, 40), font=load_font(args.font))
    (root / "ours.svg").write_text(ours, encoding="utf-8")
    native = (root / "svg/font.svg").read_text(encoding="utf-8")
    actual, expected = bounds(ours), bounds(native)
    delta = [a - b for a, b in zip(actual, expected, strict=True)]
    report = dict(
        font=args.font,
        command=command,
        ours=actual,
        native=expected,
        delta_mm=delta,
        tolerance_mm=args.tolerance_mm,
        passed=max(abs(v) for v in delta) <= args.tolerance_mm,
    )
    (root / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
