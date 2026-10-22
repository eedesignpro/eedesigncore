"""Compare text-box glyph bounds with native KiCad for the selected font."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path

from eedesigncore import (
    Schematic,
    SchTextBox,
    TextAttributes,
    ViewBox,
    load_font,
    render_schematic_svg,
)


def glyph_bounds(svg: str) -> tuple[float, float, float, float]:
    pen = BoundsPen(None)
    for node in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}path"):
        parse_path(node.attrib["d"], pen)
    if pen.bounds is None:
        raise ValueError("No glyph paths were exported.")
    return tuple(float(value) for value in pen.bounds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-mm", type=float, default=0.03)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("kicad-cli")
    if executable is None:
        parser.error("KiCad CLI was not found on PATH.")
    font = load_font(args.font)
    results = []
    for angle in (0, 90):
        for vertical in ("top", "center", "bottom"):
            folder = root / f"{angle}-{vertical}"
            folder.mkdir(exist_ok=True)
            sheet = Schematic(
                uuid="11111111-1111-4111-8111-111111111111",
                text_boxes=[
                    SchTextBox(
                        content=(
                            "Input filter inductors are to be laid out as a quadrapole\n\n"
                            "Inductor spacing should be 1.25mm center to center\n"
                        ),
                        position=(20, 20),
                        size=(30.48, 15.24),
                        angle=angle,
                        margins=(0.9525, 0.9525, 0.9525, 0.9525),
                        attributes=TextAttributes(
                            font_face=args.font,
                            font_size=(1.27, 1.27),
                            font_thickness=0.1588,
                            justify_v=vertical,
                        ),
                    )
                ],
            )
            sheet.to_file(folder / "probe.kicad_sch")
            command = [
                executable,
                "sch",
                "export",
                "svg",
                "--exclude-drawing-sheet",
                "--output",
                str(folder / "native"),
                str(folder / "probe.kicad_sch"),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(
                    os.environ,
                    KICAD_CONFIG_HOME=str(root / "config"),
                    KICAD_DOCUMENTS_HOME=str(root / "documents"),
                ),
            )
            (folder / "command.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode or result.stderr:
                raise RuntimeError("Native text-box export failed or reported a diagnostic.")
            ours = render_schematic_svg(sheet, view_box=ViewBox(0, 0, 80, 80), font=font)
            (folder / "ours.svg").write_text(ours, encoding="utf-8")
            expected = glyph_bounds((folder / "native/probe.svg").read_text(encoding="utf-8"))
            actual = glyph_bounds(ours)
            delta = [a - b for a, b in zip(actual, expected, strict=True)]
            results.append(
                dict(
                    angle=angle,
                    vertical=vertical,
                    ours=actual,
                    native=expected,
                    delta_mm=delta,
                    passed=max(abs(value) for value in delta) <= args.tolerance_mm,
                )
            )
    report = dict(font=args.font, tolerance_mm=args.tolerance_mm, cases=results)
    (root / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not all(case["passed"] for case in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
