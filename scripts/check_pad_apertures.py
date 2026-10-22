"""Compare constructed pad apertures with KiCad CLI exports, without source edits."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from make_pad_probe import cases

from eedesigncore.mod.models import (
    Footprint,
    FootprintPlacement,
    Pad,
    PrimArc,
    PrimCircle,
    PrimCurve,
    PrimLine,
    PrimRect,
)
from eedesigncore.pcb.models import Board, Setup
from eedesigncore.render import BoardContent, CliParityStyle, ViewBox, render_board_svg
from eedesigncore.render.compare import compare_holes, compare_svg, read_primitives


def boundary_gap(left: str, right: str) -> float | None:
    """Measure polygon boundary separation at vertices and edge midpoints."""
    first = [
        p[1:] for p, count in read_primitives(left, 0.0001).items() if p[0] == "seg" and count % 2
    ]
    second = [
        p[1:] for p, count in read_primitives(right, 0.0001).items() if p[0] == "seg" and count % 2
    ]
    if not first or not second:
        return None

    def distance(x: float, y: float, segment: tuple) -> float:
        a, b, c, d = map(float, segment)
        length = (c - a) ** 2 + (d - b) ** 2
        t = max(0, min(1, ((x - a) * (c - a) + (y - b) * (d - b)) / length)) if length else 0
        return math.hypot(x - a - t * (c - a), y - b - t * (d - b))

    def directed(source: list, target: list) -> float:
        return max(
            min(
                distance(
                    float(s[0]) * (1 - t) + float(s[2]) * t,
                    float(s[1]) * (1 - t) + float(s[3]) * t,
                    other,
                )
                for other in target
            )
            for s in source
            for t in (0, 0.25, 0.5, 0.75, 1)
        )

    return max(directed(first, second), directed(second, first))


def bounds_delta(left: str, right: str) -> list[float] | None:
    """Return signed polygon bound differences: left, top, right, bottom."""
    bounds = []
    for svg in (left, right):
        segments = [p for p in read_primitives(svg, 0.0001) if p[0] == "seg"]
        if not segments:
            return None
        xs = [float(p[i]) for p in segments for i in (1, 3)]
        ys = [float(p[i]) for p in segments for i in (2, 4)]
        bounds.append((min(xs), min(ys), max(xs), max(ys)))
    return [round(a - b, 6) for a, b in zip(*bounds, strict=True)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-cli", action="store_true")
    args = parser.parse_args()
    if not args.cli.is_file():
        parser.error("The KiCad CLI path does not exist.")
    args.output.mkdir(parents=True, exist_ok=True)
    layers = ["F.Mask", "B.Mask", "F.Paste", "B.Paste"]
    results = []
    extra = [
        ("custom-ring", PrimCircle(center=(0, 0), end=(2, 0), width=0.5)),
        ("custom-line", PrimLine(start=(-2, 0), end=(2, 0), width=0.5)),
        ("custom-arc", PrimArc(start=(-2, 0), mid=(0, -2), end=(2, 0), width=0.5)),
        ("custom-curve", PrimCurve(pts=[(-2, 0), (-1, -2), (1, 2), (2, 0)], width=0.5)),
        ("custom-roundrect", PrimRect(start=(-2, -1), end=(2, 1), corner_radius=0.3, fill=True)),
    ]
    all_cases = cases() + [
        (name, Pad(shape="custom", size=(0.01, 0.01), layers=["F.Cu"], primitives=[primitive]))
        for name, primitive in extra
    ]
    for mode in ("expand", "contract", "inherit", "zero"):
        for name, original in all_cases:
            pad = original.model_copy(deep=True)
            if name.startswith("npth"):
                continue
            folder = args.output / f"{mode}-{name}"
            folder.mkdir(exist_ok=True)
            pad.layers = ["F.Cu", "B.Cu", *layers]
            pad.solder_mask_margin = 0.2 if mode == "expand" else -0.1
            pad.solder_paste_margin = 0.1 if mode == "expand" else -0.05
            pad.solder_paste_margin_ratio = 0.05 if mode == "expand" else -0.05
            if mode in {"inherit", "zero"}:
                pad.solder_mask_margin = None if mode == "inherit" else 0
                pad.solder_paste_margin = None if mode == "inherit" else 0
                pad.solder_paste_margin_ratio = None if mode == "inherit" else 0
            board = Board(
                setup=Setup(
                    pad_to_mask_clearance=0.35,
                    pad_to_paste_clearance=-0.1,
                    pad_to_paste_clearance_ratio=-0.02,
                ),
                footprints=[
                    Footprint(
                        fp_id=f"probe:{name}",
                        layer="F.Cu",
                        pads=[pad],
                        at=FootprintPlacement(position=(30, 30)),
                        solder_paste_margin=-0.15,
                    )
                ],
            )
            source = folder / "probe.kicad_pcb"
            same_source = (
                source.exists() and source.read_text(encoding="utf-8") == board.to_string()
            )
            board.to_file(source)
            command = [
                str(args.cli),
                "pcb",
                "export",
                "svg",
                "--exclude-drawing-sheet",
                "--page-size-mode",
                "1",
                "--drill-shape-opt",
                "0",
                "--mode-multi",
                "--layers",
                ",".join(layers),
                "--output",
                str(folder),
                str(source),
            ]
            if (
                not args.reuse_cli
                or not same_source
                or not all(
                    (folder / f"probe-{layer.replace(chr(46), chr(95))}.svg").exists()
                    for layer in layers
                )
            ):
                completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
                if completed.returncode or completed.stderr.strip():
                    raise RuntimeError(
                        f"KiCad export failed: {completed.returncode}\n{completed.stderr}"
                    )
            pages = render_board_svg(
                board,
                layers,
                ViewBox(0, 0, 297, 210),
                style=CliParityStyle({layer: "#ffffff" for layer in layers}),
                content={BoardContent.pads},
            )
            for layer, svg in pages.items():
                reference = (folder / f"probe-{layer.replace('.', '_')}.svg").read_text()
                ET.fromstring(reference)
                ET.fromstring(svg)
                (folder / f"file-io-{layer}.svg").write_text(svg)
                comparison = (
                    compare_holes(svg, reference)
                    if pad.shape.value in {"circle", "oval"}
                    else compare_svg(svg, reference)
                )
                gap = boundary_gap(svg, reference) if not comparison.agrees else 0
                results.append(
                    dict(
                        case=f"{mode}-{name}",
                        layer=layer,
                        exact=comparison.agrees,
                        boundary_gap_mm=gap,
                        bounds_delta_mm=bounds_delta(svg, reference),
                        detail=comparison.summary(),
                    )
                )
    (args.output / "results.json").write_text(json.dumps(results, indent=2))
    failures = [
        r
        for r in results
        if not r["exact"] and (r["boundary_gap_mm"] is None or r["boundary_gap_mm"] > 0.01)
    ]
    print(
        json.dumps(
            dict(comparisons=len(results), failures=failures[:12], failure_count=len(failures)),
            indent=2,
        )
    )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
