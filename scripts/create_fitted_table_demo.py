"""Create a scratch schematic with content-sized tables and collision evidence."""

import json
from pathlib import Path
from uuid import uuid4

from eedesigncore import (
    Schematic,
    ViewBox,
    create_schematic_table,
    find_schematic_overlaps,
    fit_schematic_table,
    load_font,
    measure_schematic,
    render_schematic_svg,
)


def main():
    target = Path(".scratch/fitted-table-compact-demo")
    target.mkdir(parents=True, exist_ok=True)
    sheet = Schematic(uuid=str(uuid4()))
    font = load_font("Cascadia Mono")
    first = create_schematic_table(
        [
            ["Abbreviation", "Meaning"],
            ["PWR", "Power supply"],
            ["NC", "No connection"],
            ["nFAIL", "Active-low fault indication"],
            ["IFC", "Interface contract\nDefines electrical requirements"],
        ],
        at=(25.4, 25.4),
        with_bounds=True,
        font=font,
        font_face="Cascadia Mono",
    )
    sheet.tables.append(first.item)
    second = create_schematic_table(
        [
            ["Rail", "Voltage", "Notes"],
            ["Battery", "12 V", "Protected input"],
            ["System", "5 V", "Regulated output"],
            ["Spare", "", ""],
        ],
        at=(25.4, first.measurement.bounds.max_y + 6),
        with_bounds=True,
        font=font,
        font_face="Cascadia Mono",
    )
    second.item.cells[5].content = "Protected input\nReverse-polarity protected"
    sheet.tables.append(
        fit_schematic_table(second.item, font=font, word_wrap=True, max_column_width_mm=20.32)
    )
    single_line = first.item.model_copy(deep=True)
    single_line.uuid = str(uuid4())
    for cell in single_line.cells:
        cell.uuid = str(uuid4())
        cell.content = cell.content.replace("\n", " ")
    sheet.tables.append(
        fit_schematic_table(
            single_line,
            at=(first.measurement.bounds.max_x + 7.62, 25.4),
            word_wrap=False,
            font=font,
        )
    )
    measures = measure_schematic(sheet, font=font)
    assert all(item.complete for item in measures)
    conflicts = find_schematic_overlaps(measures, clearance_mm=1.0)
    assert not conflicts
    sheet.to_file(target / "fitted-tables.kicad_sch")
    (target / "fitted-tables.svg").write_text(
        render_schematic_svg(sheet, view_box=ViewBox(15, 15, 150, 75), font=font), encoding="utf-8"
    )
    (target / "measurements.json").write_text(
        json.dumps(
            {
                "overlaps": len(conflicts),
                "tables": [
                    {
                        "bounds": m.bounds.model_dump(),
                        "column_widths": t.column_widths,
                        "row_heights": t.row_heights,
                    }
                    for t, m in zip(sheet.tables, measures, strict=True)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(target.resolve())


if __name__ == "__main__":
    main()
