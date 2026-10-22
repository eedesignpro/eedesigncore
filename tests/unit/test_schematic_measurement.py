"""Placement checks distinguish electrical contacts from drawing collisions."""

import base64
import binascii
import struct
import zlib
from dataclasses import replace

import pytest

from eedesigncore import (
    BoundingBox,
    Image,
    Junction,
    Line,
    NoConnect,
    Schematic,
    SchText,
    SchTextBox,
    TextAttributes,
    TextResolutionContext,
    add_sheet_pin,
    create_global_label,
    create_hierarchical_label,
    create_sheet,
    find_schematic_overlaps,
    measure_schematic,
    measure_schematic_item,
)
from eedesigncore.common.sch_family.shapes import SchCircle, SchRect
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import PlacedSymbol, SchTable, SchTableCell


def test_parts_and_context_preserve_source():
    label = create_global_label("SUPPLY", at=(25.4, 25.4))
    before = label.model_copy(deep=True)
    unresolved = measure_schematic_item(label)
    assert not unresolved.complete
    with pytest.raises(ValueError, match="complete"):
        find_schematic_overlaps([unresolved])
    measured = measure_schematic_item(
        label,
        text_context=TextResolutionContext(
            variables={"INTERSHEET_REFS": "[2,7]"}, resolve_connections=False
        ),
    )
    assert measured.complete and measured.bounds
    assert any("field:0:Intersheetrefs" in part.name for part in measured.parts)
    assert any(part.name == "body:text" for part in measured.parts)
    assert label == before


def test_creation_can_return_measurements():
    made = create_hierarchical_label("IN", at=(0, 0), with_bounds=True)
    assert made.item.content == "IN" and made.measurement.bounds
    sheet = create_sheet("Child", "child.kicad_sch", at=(50.8, 50.8), with_bounds=True)
    pin = add_sheet_pin(sheet.item, "IN", with_bounds=True)
    assert pin.item in sheet.item.pins and pin.measurement.bounds


@pytest.mark.parametrize(
    "direction,endpoint",
    [
        ("right", (-10.16, 0)),
        ("left", (10.16, 0)),
        ("up", (0, 10.16)),
        ("down", (0, -10.16)),
    ],
)
def test_allowed_label_contact_and_wire_through_body(direction, endpoint):
    label = create_hierarchical_label("INPUT", at=(0, 0), direction=direction)
    schematic = Schematic(labels=[label], lines=[Line(start=(0, 0), end=endpoint)])
    assert not find_schematic_overlaps(measure_schematic(schematic))
    schematic.lines[0].end = (-endpoint[0], -endpoint[1])
    assert find_schematic_overlaps(measure_schematic(schematic))


def test_wire_through_text_and_diagonal_near_miss():
    text = SchText(content="WIRE MUST NOT CROSS", position=(25.4, 25.4))
    bounds = measure_schematic_item(text).bounds
    assert bounds
    wire = Line(
        start=(bounds.min_x - 5, bounds.center[1]), end=(bounds.max_x + 5, bounds.center[1])
    )
    assert find_schematic_overlaps(measure_schematic(Schematic(texts=[text], lines=[wire])))
    wire = Line(
        start=(bounds.min_x - 10, bounds.min_y - 10), end=(bounds.max_x + 10, bounds.min_y - 2)
    )
    assert not find_schematic_overlaps(measure_schematic(Schematic(texts=[text], lines=[wire])))


def test_wire_crossings_are_allowed_with_and_without_junction():
    schematic = Schematic(
        lines=[Line(start=(-10, 0), end=(10, 0)), Line(start=(0, -10), end=(0, 10))]
    )
    assert not find_schematic_overlaps(measure_schematic(schematic))
    schematic.junctions.append(Junction(position=(0, 0)))
    assert not find_schematic_overlaps(measure_schematic(schematic))


def test_sheet_container_pin_contact_and_title_collision():
    sheet = create_sheet("Sheet title", "child.kicad_sch", at=(50.8, 50.8))
    pin = add_sheet_pin(sheet, "ENABLE", offset_mm=12.7)
    wire = Line(start=pin.position, end=(pin.position[0] - 10.16, pin.position[1]))
    schematic = Schematic(
        sheets=[sheet], lines=[wire], texts=[SchText(content="Inside", position=(76.2, 63.5))]
    )
    assert not find_schematic_overlaps(measure_schematic(schematic))
    measured = measure_schematic_item(sheet)
    title = next(p for p in measured.parts if "Sheetname" in p.name)
    schematic.lines.append(
        Line(
            start=(title.bounds.center[0], title.bounds.min_y - 5),
            end=(title.bounds.center[0], title.bounds.max_y + 5),
        )
    )
    assert any(
        "Sheetname" in (c.first_part + (c.second_part or ""))
        for c in find_schematic_overlaps(measure_schematic(schematic))
    )


def test_clearance_page_limits_and_duplicate_ids():
    a = measure_schematic_item(SchText(content="A", position=(0, 0)), item_id="a")
    b = replace(a, item_id="b", bounds=a.bounds.model_copy())
    assert find_schematic_overlaps([a, b])
    with pytest.raises(ValueError, match="distinct"):
        find_schematic_overlaps([a, a])
    assert (
        find_schematic_overlaps(
            [a], page_bounds=BoundingBox(min_x=10, min_y=10, max_x=100, max_y=100)
        )[0].kind
        == "outside_page"
    )
    with pytest.raises(ValueError, match="Clearance"):
        find_schematic_overlaps([a], clearance_mm=-1)


@pytest.mark.parametrize(
    "item",
    [
        SchTextBox(content="Notes", position=(20, 20), size=(20, 10)),
        SchCircle(center=(20, 20), radius=5),
        NoConnect(position=(10, 10)),
        Junction(position=(10, 10)),
    ],
)
def test_other_items_have_bounds(item):
    measured = measure_schematic_item(item)
    assert measured.complete and measured.bounds and measured.parts


def test_image_bounds_follow_saved_position():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data))
        )

    payload = base64.b64encode(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    ).decode()
    result = measure_schematic_item(Image(data=payload, position=(100, 75)))
    assert result.bounds and result.bounds.center == pytest.approx((100, 75))


def test_named_font_without_font_is_incomplete():
    text = SchText(content="A", attributes=TextAttributes(font_face="Missing font"))
    assert not measure_schematic_item(text).complete


def test_separate_symbol_graphics_do_not_reserve_empty_gap():
    definition = Symbol(
        name="Test:Separate",
        units=[
            SymbolUnit(
                name="Separate_1_1",
                items=[
                    SchRect(start=(-5, 5), end=(-2, 2)),
                    SchRect(start=(2, -2), end=(5, -5)),
                ],
            )
        ],
    )
    placed = PlacedSymbol(lib_id=definition.name, position=(25.4, 25.4))
    schematic = Schematic(
        lib_symbols=[definition], symbols=[placed], lines=[Line(start=(15, 25.4), end=(35, 25.4))]
    )
    assert not find_schematic_overlaps(measure_schematic(schematic))
    schematic.lines[0].start = (15, 22)
    schematic.lines[0].end = (35, 22)
    assert find_schematic_overlaps(measure_schematic(schematic))


def test_table_cells_are_measured_separately():
    table = SchTable(
        column_count=2,
        column_widths=[20, 20],
        row_heights=[10],
        cells=[
            SchTableCell(content="First", position=(20, 20), size=(20, 10)),
            SchTableCell(content="Second", position=(40, 20), size=(20, 10)),
        ],
    )
    measured = measure_schematic_item(table)
    assert measured.complete
    assert any(part.name.startswith("cell:0") for part in measured.parts)
    assert any(part.name.startswith("cell:1") for part in measured.parts)


def test_clearance_checks_nearby_separate_items():
    from eedesigncore import SchematicMeasurement, SchematicPartBounds

    first = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=10)
    second = BoundingBox(min_x=10.5, min_y=0, max_x=20, max_y=10)
    items = [
        SchematicMeasurement("a", "test", first, (SchematicPartBounds("body", first),)),
        SchematicMeasurement("b", "test", second, (SchematicPartBounds("body", second),)),
    ]
    assert not find_schematic_overlaps(items)
    assert find_schematic_overlaps(items, clearance_mm=0.6)[0].kind == "clearance"


def test_parallel_diagonal_graphics_do_not_collide_through_their_boxes():
    from eedesigncore import LineType

    schematic = Schematic(
        lines=[
            Line(start=(0, 0), end=(10, 10)),
            Line(start=(0, 2), end=(10, 12), line_type=LineType.polyline),
        ]
    )
    assert not find_schematic_overlaps(measure_schematic(schematic))
    assert find_schematic_overlaps(measure_schematic(schematic), clearance_mm=2)


def test_round_directive_contact_does_not_reserve_empty_space():
    from eedesigncore import Label, LabelKind

    schematic = Schematic(
        labels=[Label(kind=LabelKind.netclass_flag, position=(0, 0))],
        lines=[Line(start=(-10, 0), end=(10, 0))],
    )
    assert not find_schematic_overlaps(measure_schematic(schematic))


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), 1e12])
def test_collision_rejects_invalid_segment_coordinates(coordinate):
    from eedesigncore import BoundingBox, SchematicMeasurement, SchematicPartBounds

    box = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1)
    part = SchematicPartBounds("body", box, segment=((coordinate, 0), (1, 1)))
    measurement = SchematicMeasurement("bad", "wire", box, (part,))
    with pytest.raises(ValueError, match="finite range"):
        find_schematic_overlaps([measurement])
