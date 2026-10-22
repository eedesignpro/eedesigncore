"""Probe native page-reference settings and existing bounds without changing the demo."""

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from eedesigncore import (
    Paper,
    ProjectFile,
    RootInstancePath,
    Schematic,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
    context_for_sheet,
    create_global_label,
    create_sheet,
    load_hierarchy,
    prepare_schematic_view,
    resolve_document_text,
)


def main() -> None:
    base = Path(__file__).resolve().parents[1] / ".scratch"
    output = base / "schematic-helper-investigation"
    output.mkdir(exist_ok=True)
    executable = shutil.which("kicad-cli")
    if executable is None:
        raise RuntimeError("KiCad CLI is required for the native comparison.")
    results = {}
    for name, own, short, show, prefix, suffix in [
        ("long_own", True, False, True, "[", "]"),
        ("long_other", False, False, True, "[", "]"),
        ("short_other", False, True, True, "[", "]"),
        ("custom", False, False, True, "<", ">"),
        ("hidden", False, False, False, "[", "]"),
    ]:
        folder = output / name
        folder.mkdir(exist_ok=True)
        root = Schematic(
            uuid=str(uuid4()),
            paper=Paper(),
            labels=[create_global_label("SHARED", at=(30.48, 25.4))],
            sheet_instances=SheetInstances(paths=[RootInstancePath(page="1")]),
        )
        child = Schematic(
            uuid=str(uuid4()),
            paper=Paper(),
            labels=[create_global_label("SHARED", at=(30.48, 25.4))],
        )
        for index, page in enumerate(("2", "7", "A1")):
            sheet = create_sheet(
                f"Child {page}", "child.kicad_sch", at=(25.4 + index * 76.2, 76.2)
            )
            sheet.instances = [
                SheetInstanceProject(
                    name="probe", paths=[SheetInstancePath(path=f"/{root.uuid}", page=page)]
                )
            ]
            root.sheets.append(sheet)
        child.to_file(folder / "child.kicad_sch")
        root.to_file(folder / "probe.kicad_sch")
        project = ProjectFile.from_string(
            json.dumps(
                {
                    "meta": {"version": 3},
                    "schematic": {
                        "top_level_sheets": [],
                        "drawing": {
                            "intersheets_ref_show": show,
                            "intersheets_ref_own_page": own,
                            "intersheets_ref_short": short,
                            "intersheets_ref_prefix": prefix,
                            "intersheets_ref_suffix": suffix,
                        },
                    },
                }
            )
        )
        project.to_file(folder / "probe.kicad_pro")
        subprocess.run(
            [
                executable,
                "sch",
                "export",
                "svg",
                "--exclude-drawing-sheet",
                "--output",
                str(folder / "svg"),
                str(folder / "probe.kicad_sch"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        texts = [
            element.text
            for element in ElementTree.parse(folder / "svg/probe.svg").iter()
            if element.tag.endswith("}text")
        ]
        hierarchy = load_hierarchy(folder / "probe.kicad_pro")
        node = next(hierarchy.walk())
        resolved = resolve_document_text(
            node.schematic, context_for_sheet(hierarchy, node.kiid_path)
        )
        field = resolved.document.labels[0].fields[0]
        results[name] = {
            "native_text": texts,
            "resolved_field": field.value,
            "resolved_field_visible": field.visible,
        }

    view = prepare_schematic_view(
        base / "schematic-helpers-first-attempt/schematic-helpers-demo.kicad_sch",
        document_id="helper-probe",
    )
    bounds_results = []
    for page in view.pages.values():
        (output / f"library-{page.page_number}.svg").write_text(page.svg, encoding="utf-8")
        items = [item for item in page.items.values() if item.kind == "label" and item.bounds]
        overlaps = []
        for index, first in enumerate(items):
            for second in items[index + 1 :]:
                a, b = first.bounds, second.bounds
                width = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
                height = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
                if width > 0 and height > 0:
                    overlaps.append([first.label, second.label, round(width * height, 4)])
        bounds_results.append(
            {
                "page": page.page_number,
                "bounded_kinds": dict(
                    Counter(item.kind for item in page.items.values() if item.bounds)
                ),
                "label_overlap_pairs": overlaps,
                "issues": sorted({issue.code for issue in page.coverage.issues}),
            }
        )
    result = {"native_page_reference_text": results, "existing_bounds": bounds_results}
    (output / "probe-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
