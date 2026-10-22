"""Create a first-attempt native demo without rendering or visual review."""

import argparse
from pathlib import Path
from uuid import uuid4

from eedesigncore import (
    Line,
    Paper,
    ProjectFile,
    RootInstancePath,
    Schematic,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
    add_sheet_pin,
    create_global_label,
    create_hierarchical_label,
    create_sheet,
    load_hierarchy,
)


def main() -> None:
    destination = (
        Path(__file__).resolve().parents[1] / ".scratch" / "schematic-helpers-first-attempt"
    )
    destination.mkdir(parents=True, exist_ok=True)
    root_path = destination / "schematic-helpers-demo.kicad_sch"
    if root_path.exists():
        raise FileExistsError(
            "Keep the first attempt. Use a different output folder for another demo."
        )
    root = Schematic(uuid=str(uuid4()), paper=Paper(), embedded_fonts=False)
    child = Schematic(uuid=str(uuid4()), paper=Paper(), embedded_fonts=False)
    root.sheet_instances = SheetInstances(paths=[RootInstancePath(page="1")])
    sheet = create_sheet(
        "Label and sheet pin demo", "label-examples.kicad_sch", at=(88.7, 63.2), size=(101.5, 88.8)
    )
    sheet.instances = [
        SheetInstanceProject(
            name=root_path.stem,
            paths=[SheetInstancePath(path=f"/{root.uuid}", page="2")],
        )
    ]
    root.sheets.append(sheet)
    shapes = ("input", "output", "passive", "bidirectional", "tri_state")
    directions = ("left", "right", "up", "down")
    sides = ("left", "right", "top", "bottom")
    vectors = ((-1, 0), (1, 0), (0, -1), (0, 1))
    for column, (direction, side, vector) in enumerate(
        zip(directions, sides, vectors, strict=True)
    ):
        for row, shape in enumerate(shapes):
            name = f"{side.upper()}_{shape.upper()}"
            at = (45.72 + column * 63.5, 30.48 + row * 33.02)
            label = create_hierarchical_label(name, at=at, direction=direction, shape=shape)
            child.labels.append(label)
            opposite = {"left": "right", "right": "left", "up": "down", "down": "up"}[direction]
            global_contact = (
                round(at[0] - vector[0] * 10.16, 10),
                round(at[1] - vector[1] * 10.16, 10),
            )
            child.labels.append(
                create_global_label(name, at=global_contact, direction=opposite, shape=shape)
            )
            child.lines.append(Line(start=at, end=global_contact, uuid=str(uuid4())))
            pin = add_sheet_pin(
                sheet, label, side=side, allow_top_bottom=True, offset_mm=20.32 + row * 12.7
            )
            contact = (
                round(pin.position[0] + vector[0] * 10.16, 10),
                round(pin.position[1] + vector[1] * 10.16, 10),
            )
            root.labels.append(
                create_global_label(name, at=contact, direction=direction, shape=shape)
            )
            root.lines.append(Line(start=pin.position, end=contact, uuid=str(uuid4())))
    child.to_file(destination / "label-examples.kicad_sch")
    root.to_file(root_path)
    write_project(root_path)
    assert Schematic.from_file(root_path).to_string() == root.to_string()
    saved_child = Schematic.from_file(destination / "label-examples.kicad_sch")
    assert saved_child.to_string() == child.to_string()
    verify(root_path)


def write_project(root_path: Path) -> None:
    """Enable native page-reference display for this demo project."""
    project = ProjectFile.from_string("""{
      "meta": {"version": 3},
      "schematic": {
        "top_level_sheets": [],
        "drawing": {
          "intersheets_ref_show": true,
          "intersheets_ref_own_page": false,
          "intersheets_ref_prefix": "[",
          "intersheets_ref_suffix": "]"
        }
      }
    }""")
    project.to_file(root_path.with_suffix(".kicad_pro"))


def verify(root_path: Path) -> None:
    """Check saved data without rendering, changing, or opening the files."""
    tree = load_hierarchy(root_path)
    nodes = list(tree.walk())
    assert len(nodes) == 2 and [node.page for node in nodes] == ["1", "2"]
    loaded_root = Schematic.from_file(root_path)
    loaded_child = Schematic.from_file(root_path.parent / "label-examples.kicad_sch")
    for schematic in (loaded_root, loaded_child):
        assert Schematic.from_string(schematic.to_string()) == schematic
    for pin in loaded_root.sheets[0].pins:
        matching = [
            label
            for label in loaded_child.labels
            if label.kind.value == "hierarchical_label" and label.content == pin.name
        ]
        assert len(matching) == 1 and matching[0].shape == pin.shape
        assert any(wire.start == pin.position for wire in loaded_root.lines)
        assert any(wire.start == matching[0].position for wire in loaded_child.lines)
    print(root_path)
    print("Sheet pages, matching names, shapes, and wire contacts passed. No visual review.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, help="Check an existing demo without changing it.")
    arguments = parser.parse_args()
    if arguments.check:
        verify(arguments.check)
    else:
        main()
