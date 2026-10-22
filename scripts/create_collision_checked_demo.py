"""Build a mixed schematic and check every page before native visual review."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import shutil
import struct
import zlib
from functools import partial
from pathlib import Path
from uuid import uuid4

from eedesigncore import (
    BoundingBox,
    BusEntry,
    Image,
    Junction,
    Label,
    LabelKind,
    Line,
    LineType,
    NoConnect,
    Paper,
    ProjectFile,
    RootInstancePath,
    Schematic,
    SchTable,
    SchTableCell,
    SchText,
    SchTextBox,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
    Stroke,
    SymbolLib,
    TextAttributes,
    ViewBox,
    add_sheet_pin,
    autoplace_fields,
    context_for_sheet,
    create_global_label,
    create_hierarchical_label,
    create_sheet,
    find_schematic_overlaps,
    inject_symbol,
    load_font,
    load_hierarchy,
    measure_schematic,
    measure_schematic_item,
    render_schematic_svg,
)
from eedesigncore.common.models import JustifyH, JustifyV
from eedesigncore.common.sch_family.shapes import SchCircle


def uid():
    return str(uuid4())


def text(sheet, value, at, size=1.27):
    sheet.texts.append(
        SchText(
            content=value,
            position=at,
            uuid=uid(),
            attributes=TextAttributes(
                font_size=(size, size), justify_h=JustifyH.left, justify_v=JustifyV.center
            ),
        )
    )


def wire(sheet, start, end, kind=LineType.wire):
    sheet.lines.append(Line(start=start, end=end, line_type=kind, uuid=uid()))


def thumbnail():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data))
        )

    return base64.b64encode(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 8, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x70\xb0\x90" * 16) * 8))
        + chunk(b"IEND", b"")
    ).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--font", default="KiCad Font")
    args = parser.parse_args()
    font = load_font(args.font)
    global_label = partial(create_global_label, font=font)
    destination = args.output or (
        Path(__file__).resolve().parents[1] / ".scratch" / "collision-checked-demo"
    )
    if (destination / "placement-demo.kicad_sch").exists():
        raise FileExistsError("Use a new output directory to preserve existing schematic edits.")
    destination.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("kicad-cli")
    if executable is None:
        raise RuntimeError("An installed KiCad is needed for its symbol library.")
    device_path = Path(executable).parents[1] / "share/kicad/symbols/Device.kicad_sym"
    devices = {symbol.name: symbol for symbol in SymbolLib.from_file(device_path).symbols}
    root = Schematic(
        uuid=uid(),
        paper=Paper(paper_type="A3"),
        embedded_fonts=False,
        sheet_instances=SheetInstances(paths=[RootInstancePath(page="1")]),
    )
    child = Schematic(uuid=uid(), paper=Paper(paper_type="A3"), embedded_fonts=False)
    text(root, "SCHEMATIC PLACEMENT DEMO", (25.4, 20.32), 2.54)
    text(root, "Measured fields, wire contacts, sheet pins, and drawing items", (25.4, 27.94))
    text(root, "Installed KiCad symbols", (25.4, 43.18), 1.905)
    for index, (name, reference) in enumerate((("R", "R1"), ("C", "C1"), ("LED", "D1"))):
        placed = inject_symbol(
            root,
            devices[name],
            at=(50.8 + index * 50.8, 76.2),
            lib_nickname="Device",
            reference=reference,
            project_name="placement-demo",
            sheet_path=f"/{root.uuid}",
        )
        autoplace_fields(placed, definition=root.lib_symbols[-1], font=font)
        measurement = measure_schematic_item(placed, schematic=root, font=font)
        for pin_index, contact in enumerate(measurement.contacts):
            dx, dy = contact[0] - placed.position[0], contact[1] - placed.position[1]
            if abs(dx) > abs(dy):
                endpoint = (contact[0] + (10.16 if dx > 0 else -10.16), contact[1])
                direction = "right" if dx > 0 else "left"
            else:
                endpoint = (contact[0], contact[1] + (10.16 if dy > 0 else -10.16))
                direction = "down" if dy > 0 else "up"
            wire(root, contact, endpoint)
            root.labels.append(
                global_label(
                    f"{reference}_{pin_index + 1}",
                    at=endpoint,
                    direction=direction,
                    show_intersheet_refs=False,
                )
            )
    sheet = create_sheet(
        "Control interface", "interface.kicad_sch", at=(240.2, 60.1), size=(101.4, 88.8)
    )
    sheet.instances = [
        SheetInstanceProject(
            name="placement-demo", paths=[SheetInstancePath(path=f"/{root.uuid}", page="2")]
        )
    ]
    root.sheets.append(sheet)
    text(child, "HIERARCHICAL AND GLOBAL LABELS", (25.4, 20.32), 2.54)
    for index, shape in enumerate(("input", "output", "passive", "bidirectional", "tri_state")):
        name = {
            "input": "ENABLE",
            "output": "READY",
            "passive": "REFERENCE",
            "bidirectional": "DATA",
            "tri_state": "BUS_OUT",
        }[shape]
        y = 50.8 + index * 30.48
        label = create_hierarchical_label(name, at=(152.4, y), direction="right", shape=shape)
        child.labels.append(label)
        child.labels.append(global_label(name, at=(127, y), direction="left", shape=shape))
        wire(child, (127, y), label.position)
        pin = add_sheet_pin(sheet, label, offset_mm=15.24 + index * 12.7)
        contact = (pin.position[0] - 15.24, pin.position[1])
        root.labels.append(global_label(name, at=contact, direction="left", shape=shape))
        wire(root, contact, pin.position)
    for index, (side, direction, child_direction, offset, vector) in enumerate(
        (
            ("top", "up", "up", 88.9, (0, -1)),
            ("right", "right", "left", 40.64, (1, 0)),
            ("bottom", "down", "down", 88.9, (0, 1)),
        )
    ):
        name = f"{side.upper()}_PORT"
        at = (254, 63.5 + index * 60.96)
        label = create_hierarchical_label(name, at=at, direction=child_direction)
        child.labels.append(label)
        pin = add_sheet_pin(sheet, label, side=side, allow_top_bottom=True, offset_mm=offset)
        contact = (pin.position[0] + vector[0] * 10.16, pin.position[1] + vector[1] * 10.16)
        wire(root, pin.position, contact)
        root.labels.append(global_label(name, at=contact, direction=direction))
        # The child wire extends away from the hierarchical label body.
        vector_child = {"up": (0, 1), "left": (1, 0), "down": (0, -1)}[child_direction]
        end = (at[0] + vector_child[0] * 12.7, at[1] + vector_child[1] * 12.7)
        wire(child, at, end)
        child.labels.append(
            global_label(
                name,
                at=end,
                direction={"up": "down", "left": "right", "down": "up"}[child_direction],
            )
        )
    text(root, "Notes and table", (25.4, 144.78), 1.905)
    root.text_boxes.append(
        SchTextBox(
            content="Visible text and border\nBoth reserve space.",
            position=(25.4, 152.4),
            size=(66.04, 20.32),
            margins=(2.54, 2.54, 2.54, 2.54),
            stroke=Stroke(width=0.254),
            uuid=uid(),
        )
    )
    root.tables.append(
        SchTable(
            column_count=2,
            column_widths=[25.4, 25.4],
            row_heights=[10.16, 10.16],
            border_external=True,
            separators_rows=True,
            separators_cols=True,
            border_stroke=Stroke(width=0.254),
            uuid=uid(),
            cells=[
                SchTableCell(
                    content=value,
                    position=(114.3 + (i % 2) * 25.4, 152.4 + (i // 2) * 10.16),
                    size=(25.4, 10.16),
                    margins=(2.54, 2.54, 2.54, 2.54),
                )
                for i, value in enumerate(("Item", "State", "Bounds", "Checked"))
            ],
        )
    )
    text(root, "Graphic and image", (190.5, 165.1))
    root.graphic_shapes.append(
        SchCircle(center=(203.2, 182.88), radius=7.62, stroke=Stroke(width=0.254), id=uid())
    )
    root.images.append(Image(position=(246.38, 182.88), scale=10, data=thumbnail(), uuid=uid()))
    text(root, "Allowed wire crossings", (292.1, 165.1))
    for x in (304.8, 355.6):
        if x == 304.8:
            for endpoint in ((x - 10.16, 185.42), (x + 10.16, 185.42), (x, 175.26), (x, 195.58)):
                wire(root, (x, 185.42), endpoint)
        else:
            wire(root, (x - 10.16, 185.42), (x + 10.16, 185.42))
            wire(root, (x, 175.26), (x, 195.58))
    root.junctions.append(Junction(position=(304.8, 185.42), uuid=uid()))
    text(root, "Local label, bus entry, and no-connect mark", (25.4, 208.28))
    root.labels.append(
        Label(
            kind=LabelKind.local,
            content="LOCAL_NET",
            position=(50.8, 228.6),
            attributes=TextAttributes(justify_h=JustifyH.left, justify_v=JustifyV.bottom),
            uuid=uid(),
        )
    )
    wire(root, (50.8, 228.6), (76.2, 228.6))
    wire(root, (114.3, 228.6), (165.1, 228.6), LineType.bus)
    root.bus_entries.append(BusEntry(position=(139.7, 228.6), size=(5.08, 5.08), uuid=uid()))
    wire(root, (144.78, 233.68), (144.78, 243.84))
    root.no_connects.append(NoConnect(position=(203.2, 228.6), uuid=uid()))
    root.to_file(destination / "placement-demo.kicad_sch")
    child.to_file(destination / "interface.kicad_sch")
    ProjectFile.from_string(
        json.dumps(
            {
                "meta": {"version": 3},
                "schematic": {
                    "top_level_sheets": [],
                    "drawing": {
                        "intersheets_ref_show": True,
                        "intersheets_ref_own_page": False,
                        "intersheets_ref_short": False,
                        "intersheets_ref_prefix": "[",
                        "intersheets_ref_suffix": "]",
                    },
                },
            }
        )
    ).to_file(destination / "placement-demo.kicad_pro")
    hierarchy = load_hierarchy(destination / "placement-demo.kicad_pro")
    report = []
    for node in hierarchy.walk():
        context = context_for_sheet(hierarchy, node.kiid_path)
        measurements = measure_schematic(node.schematic, text_context=context, font=font)
        conflicts = find_schematic_overlaps(
            measurements, page_bounds=BoundingBox(min_x=10, min_y=10, max_x=410, max_y=287)
        )
        summary = {
            "page": node.page,
            "items": len(measurements),
            "incomplete": [m.item_id for m in measurements if not m.complete],
            "conflicts": [
                {
                    "first": c.first_item,
                    "first_part": c.first_part,
                    "second": c.second_item,
                    "second_part": c.second_part,
                    "kind": c.kind,
                }
                for c in conflicts
            ],
        }
        report.append(summary)
        (destination / f"library-page-{node.page}.svg").write_text(
            render_schematic_svg(
                node.schematic, view_box=ViewBox(0, 0, 420, 297), text_context=context, font=font
            ),
            encoding="utf-8",
        )
    (destination / "collision-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if any(page["conflicts"] or page["incomplete"] for page in report):
        raise RuntimeError("The demo contains collisions or incomplete measurements.")


if __name__ == "__main__":
    main()
