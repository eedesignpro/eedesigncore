"""Sheet pins use native side angles and exact grid-aligned edge contacts."""

import pytest

from eedesigncore import Schematic, add_sheet_pin, create_hierarchical_label, create_sheet


@pytest.mark.parametrize(
    "side,angle,position",
    [
        ("left", 180, (25.4, 38.1)),
        ("right", 0, (76.2, 38.1)),
        ("top", 90, (38.1, 25.4)),
        ("bottom", 270, (38.1, 55.88)),
    ],
)
@pytest.mark.parametrize("shape", ["input", "output", "passive", "bidirectional", "tri_state"])
def test_child_label_to_sheet_edge(side, angle, position, shape):
    sheet = create_sheet("Child", "child.kicad_sch", at=(25.4, 25.4))
    child = create_hierarchical_label("NET", at=(10.16, 10.16), shape=shape)
    pin = add_sheet_pin(sheet, child, side=side, offset_mm=12.8, allow_top_bottom=True)
    assert pin.name == child.content and pin.shape == child.shape
    assert pin.angle == angle and pin.position == position
    assert pin.attributes.justify_v.value == "center"
    assert sheet.pins == [pin]
    schematic = Schematic(sheets=[sheet])
    assert Schematic.from_string(schematic.to_string()) == schematic


def test_automatic_slots_and_atomic_errors():
    sheet = create_sheet("Child", "child.kicad_sch", at=(0, 0))
    first = add_sheet_pin(sheet, "ONE")
    second = add_sheet_pin(sheet, "TWO")
    assert first.position == (0, 5.08) and second.position == (0, 10.16)
    before = sheet.model_copy(deep=True)
    for name, options in [
        ("ONE", {}),
        ("THREE", {"offset_mm": 5.08}),
        ("THREE", {"offset_mm": -1}),
        ("THREE", {"offset_mm": 0.1}),
        ("THREE", {"offset_mm": 30.48}),
        ("THREE", {"side": "invalid"}),
        ("THREE", {"side": "top"}),
        ("THREE", {"side": "bottom"}),
        ("THREE", {"text_size_mm": 0}),
    ]:
        with pytest.raises(ValueError):
            add_sheet_pin(sheet, name, **options)
        assert sheet == before


def test_shape_mismatch_and_off_grid_sheet_rejected():
    sheet = create_sheet("Child", "child.kicad_sch", at=(0, 0))
    label = create_hierarchical_label("NET", at=(0, 0), shape="input")
    with pytest.raises(ValueError, match="match"):
        add_sheet_pin(sheet, label, shape="output")
    sheet.position = (0.1, 0)
    with pytest.raises(ValueError, match="grid"):
        add_sheet_pin(sheet, label)
    assert not sheet.pins


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_horizontal_edges_require_explicit_permission(side):
    sheet = create_sheet("Power", "power.kicad_sch", at=(0, 0))
    before = sheet.model_copy(deep=True)
    with pytest.raises(ValueError, match="allow_top_bottom"):
        add_sheet_pin(sheet, "POWER", side=side)
    assert sheet == before
    pin = add_sheet_pin(sheet, "POWER", side=side, allow_top_bottom=True)
    assert pin in sheet.pins
