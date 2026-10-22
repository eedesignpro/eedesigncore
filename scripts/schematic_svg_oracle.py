"""Compare a constructed schematic SVG with the installed KiCad exporter."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from eedesigncore import (
    BusEntry,
    InstancePath,
    InstanceProject,
    Junction,
    Label,
    LabelKind,
    Line,
    NoConnect,
    Pin,
    PlacedSymbol,
    Property,
    Schematic,
    SchRect,
    SchText,
    Sheet,
    SheetPin,
    Symbol,
    SymbolUnit,
    SymText,
    TextAttributes,
    ViewBox,
    generate_uuids,
    render_schematic_svg,
)
from eedesigncore.render.compare import compare_svg


def fixture() -> Schematic:
    symbol = Symbol(
        name="Probe:Gate",
        units=[
            SymbolUnit.for_symbol(
                "Probe:Gate",
                items=[
                    SchRect(start=(-2, 2), end=(2, -2)),
                    Pin(
                        electrical_type="input",
                        shape="inverted_clock",
                        position=(-4.54, 0),
                        length=2.54,
                        name="IN",
                        number="1",
                    ),
                    SymText.from_degrees(
                        90,
                        content="V",
                        position=(1, 0),
                        attributes=TextAttributes(justify_h="right", justify_v="bottom"),
                    ),
                    SymText(
                        content="A",
                        position=(0, 1),
                        attributes=TextAttributes(justify_h="left", justify_v="top"),
                    ),
                ],
            )
        ],
    )
    schematic = Schematic()
    generate_uuids(schematic)
    for row, mirror in enumerate((None, "x", "y")):
        for column, angle in enumerate((0, 90, 180, 270)):
            x, y = 30.0 + column * 35, 30.0 + row * 30
            reference = f"U{row * 4 + column + 1}"
            schematic.symbols.append(
                PlacedSymbol(
                    lib_id=symbol.name,
                    position=(x, y),
                    angle=angle,
                    mirror=mirror,
                    fields=[
                        Property(
                            name="Reference",
                            value=reference,
                            position=(x, y + 10),
                            attributes=TextAttributes(justify_h="left", justify_v="top"),
                        )
                    ],
                    instances=[
                        InstanceProject(
                            name="probe",
                            paths=[
                                InstancePath(
                                    path=f"/{schematic.uuid}",
                                    reference=reference,
                                    unit=1,
                                )
                            ],
                        )
                    ],
                )
            )
    for row, kind in enumerate((LabelKind.local, LabelKind.global_, LabelKind.hierarchical)):
        for column, angle in enumerate((0, 90, 180, 270)):
            schematic.labels.append(
                Label(
                    kind=kind,
                    content="NET",
                    angle=angle,
                    position=(30 + column * 35, 125 + row * 20),
                    shape="input" if row else None,
                    attributes=TextAttributes(
                        font_size=(1.27, 1.27),
                        justify_h="left" if angle in (0, 90) else "right",
                        justify_v="bottom" if row == 0 else "center",
                    ),
                )
            )
    schematic.lines = [
        Line(start=(15, 10), end=(45, 10)),
        Line(line_type="bus", start=(50, 10), end=(65, 10)),
    ]
    schematic.bus_entries = [BusEntry(position=(45, 10), size=(5, 5))]
    schematic.junctions = [Junction(position=(30, 10), diameter=0.9144)]
    schematic.no_connects = [NoConnect(position=(70, 10))]
    schematic.texts = [SchText(content="SCHEMATIC\nSECOND", position=(90, 10))]
    schematic.sheets = [
        Sheet(
            position=(175, 25),
            size=(30, 30),
            fields=[
                Property(name="Sheetname", value="Child", visible=False),
                Property(name="Sheetfile", value="child.kicad_sch", visible=False),
            ],
            pins=[
                SheetPin(name="LEFT", shape="input", position=(175, 40), angle=180),
                SheetPin(name="RIGHT", shape="output", position=(205, 40), angle=0),
                SheetPin(name="TOP", shape="input", position=(190, 25), angle=90),
                SheetPin(name="BOTTOM", shape="output", position=(190, 55), angle=270),
            ],
        )
    ]
    generate_uuids(schematic)
    schematic.lib_symbols = [symbol]
    return schematic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.cli.is_file():
        parser.error("The supplied KiCad executable does not exist.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    schematic = fixture()
    schematic.to_file(output / "probe.kicad_sch")
    child = Schematic()
    generate_uuids(child)
    child.to_file(output / "child.kicad_sch")
    (output / "MANIFEST.json").write_text(
        json.dumps(
            {
                "kind": "CONSTRUCTED",
                "purpose": "Symbol placements, label directions, and connections",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["KICAD_CONFIG_HOME"] = str(output / "settings")
    environment["KICAD_DOCUMENTS_HOME"] = str(output / "documents")
    (output / "kicad/probe.svg").unlink(missing_ok=True)
    result = subprocess.run(
        [
            str(args.cli),
            "sch",
            "export",
            "svg",
            "--exclude-drawing-sheet",
            "--no-background-color",
            "-o",
            str(output / "kicad"),
            str(output / "probe.kicad_sch"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
        check=False,
    )
    (output / "kicad.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode or "error:" in (result.stdout + result.stderr).lower():
        raise RuntimeError("KiCad reported an export error. Read kicad.log.")
    ours = render_schematic_svg(schematic, view_box=ViewBox(0, 0, 297, 210))
    (output / "ours.svg").write_text(ours, encoding="utf-8")
    theirs = (output / "kicad/probe.svg").read_text(encoding="utf-8")
    comparison = compare_svg(ours, theirs, tolerance=0.001)
    (output / "comparison.json").write_text(
        json.dumps(
            {
                "matched": comparison.matched,
                "only_in_memory": comparison.only_in_left,
                "only_in_kicad": comparison.only_in_right,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(comparison.summary())
    return int(not comparison.agrees)


if __name__ == "__main__":
    raise SystemExit(main())
