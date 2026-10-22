"""Compare constructed in-memory symbol previews with installed KiCad geometry.

The command writes only to the required output directory. The caller supplies
the installed kicad-cli path. KiCad centers each export on its own page, so an
independent circle at the symbol origin measures the translation to remove.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from eedesigncore import (
    Pin,
    PinShape,
    Property,
    SchCircle,
    Stroke,
    Symbol,
    SymbolLib,
    SymbolStyle,
    SymbolUnit,
    SymText,
    ViewBox,
    render_symbol_svg,
)
from eedesigncore.render.compare import compare_svg

_NAMESPACE = "{http://www.w3.org/2000/svg}"
_COORDINATE = re.compile(r"([ML])\s*([-\d.]+)[,\s]+([-\d.]+)")


def fixtures() -> list[Symbol]:
    """Construct isolated pin and text examples with known origin markers."""
    symbols: list[Symbol] = []
    for offset in (0.0, 0.508):
        for angle in (0, 90, 180, 270):
            for shape in PinShape:
                name = f"{shape.value}_{angle}_{'inside' if offset else 'outside'}"
                symbols.append(
                    Symbol(
                        name=name,
                        pin_name_offset=offset,
                        fields=[Property(name="Reference", value="U", visible=False)],
                        units=[
                            SymbolUnit.for_symbol(
                                name,
                                items=[
                                    SchCircle(radius=0.125, stroke=Stroke(width=0.1524)),
                                    Pin(
                                        electrical_type="input",
                                        shape=shape,
                                        position=(-5, 3),
                                        angle=angle,
                                        name="ABC",
                                        number="12",
                                    ),
                                ],
                            )
                        ],
                    )
                )
    for angle in (0, 90):
        name = f"text_{angle}"
        symbols.append(
            Symbol(
                name=name,
                fields=[Property(name="Reference", value="U", visible=False)],
                units=[
                    SymbolUnit.for_symbol(
                        name,
                        items=[
                            SchCircle(radius=0.125, stroke=Stroke(width=0.1524)),
                            SymText.from_degrees(angle, content="ABC123", position=(4, 5)),
                        ],
                    )
                ],
            )
        )
    return symbols


def normalized_svg(xml: str) -> str:
    """Remove the independently measured page translation from simple geometry."""
    root = ET.fromstring(xml)
    anchor = next(e for e in root.iter(f"{_NAMESPACE}circle") if float(e.attrib["r"]) == 0.125)
    dx, dy = float(anchor.attrib["cx"]), float(anchor.attrib["cy"])
    pieces: list[str] = []
    for element in root.iter():
        if element.tag == f"{_NAMESPACE}circle":
            pieces.append(
                f'<circle cx="{float(element.attrib["cx"]) - dx}" '
                f'cy="{float(element.attrib["cy"]) - dy}" r="{element.attrib["r"]}"/>'
            )
        elif element.tag == f"{_NAMESPACE}path":
            data = element.attrib["d"]
            if re.search(r"[ACHQSTVZachlmqstvz]", data):
                raise ValueError("The oracle requires absolute M and L paths.")
            data = _COORDINATE.sub(
                lambda m: f"{m[1]}{float(m[2]) - dx},{float(m[3]) - dy}",
                data,
            )
            pieces.append(f'<path d="{data}"/>')
    return "<svg>" + "".join(pieces) + "</svg>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.cli.is_file():
        parser.error("The supplied kicad-cli executable does not exist.")
    args.output.mkdir(parents=True, exist_ok=True)
    symbols = fixtures()
    source = args.output / "constructed-symbols.kicad_sym"
    SymbolLib(symbols=symbols).to_file(source)
    (args.output / "MANIFEST.json").write_text(
        json.dumps(
            {
                "kind": "CONSTRUCTED",
                "purpose": "Pin geometry and labels in every orientation",
                "origin_marker_radius_mm": 0.125,
                "symbol_count": len(symbols),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    cli_output = args.output / "kicad"
    result = subprocess.run(
        [str(args.cli), "sym", "export", "svg", str(source), "-o", str(cli_output)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    (args.output / "kicad.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode or "error:" in (result.stdout + result.stderr).lower():
        raise RuntimeError("KiCad reported an export error. Read kicad.log.")
    results: list[dict[str, object]] = []
    for symbol in symbols:
        ours = render_symbol_svg(
            symbol, view_box=ViewBox(-20, -20, 40, 40), style=SymbolStyle(clock_style="plot")
        )
        theirs = (cli_output / f"{symbol.name}_unit1.svg").read_text(encoding="utf-8")
        normalized = normalized_svg(theirs)
        difference = compare_svg(ours, normalized, tolerance=0.001)
        (args.output / f"{symbol.name}.ours.svg").write_text(ours, encoding="utf-8")
        results.append(
            {
                "symbol": symbol.name,
                "agrees": difference.agrees,
                "matched": difference.matched,
                "only_in_memory": difference.only_in_left,
                "only_in_kicad": difference.only_in_right,
            }
        )
    (args.output / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    failures = sum(not bool(result["agrees"]) for result in results)
    print(f"Measured {len(results)} symbol exports; {failures} geometry mismatches.")
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
