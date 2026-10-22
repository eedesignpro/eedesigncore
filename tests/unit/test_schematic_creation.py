"""Creation helpers keep contacts fixed and make drawing choices consistently."""

from __future__ import annotations

from uuid import UUID

import pytest

from eedesigncore import (
    JustifyH,
    JustifyV,
    LabelKind,
    Schematic,
    create_global_label,
    create_hierarchical_label,
    create_sheet,
)
from eedesigncore.render.dnp import text_bounds
from eedesigncore.render.schematic_labels import draw_label
from eedesigncore.render.symbols import SymbolStyle, _SymbolDrawing


@pytest.mark.parametrize(
    "factory,kind",
    [
        (create_global_label, LabelKind.global_),
        (create_hierarchical_label, LabelKind.hierarchical),
    ],
)
@pytest.mark.parametrize(
    "direction,angle,justify",
    [
        ("right", 0, JustifyH.left),
        ("up", 90, JustifyH.left),
        ("left", 180, JustifyH.right),
        ("down", 270, JustifyH.right),
    ],
)
@pytest.mark.parametrize("shape", ["input", "output", "passive", "bidirectional", "tri_state"])
def test_label_defaults_and_round_trip(factory, kind, direction, angle, justify, shape):
    label = factory(
        "TEST_NET",
        at=(-13.15, 24.33),
        direction=direction,
        shape=shape,
        net_class="Power",
        properties={"Design note": "Specified"},
    )
    assert label.position == (-13.15, 24.33)
    assert label.content == "TEST_NET" and label.kind == kind and label.shape == shape
    assert label.angle == angle and label.attributes.justify_h == justify
    assert label.attributes.justify_v == JustifyV.center
    assert label.fields_autoplaced
    assert UUID(label.uuid)
    metadata = {field.name: field for field in label.fields}
    assert metadata["Netclass"].value == "Power" and not metadata["Netclass"].visible
    assert not metadata["Design note"].visible
    if kind == LabelKind.global_:
        assert metadata["Intersheetrefs"].value == "${INTERSHEET_REFS}"
        assert metadata["Intersheetrefs"].visible
    else:
        assert "Intersheetrefs" not in metadata
    sheet = Schematic(labels=[label])
    loaded = Schematic.from_string(sheet.to_string())
    assert loaded.labels[0] == label


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
@pytest.mark.parametrize("shape", ["input", "output", "passive", "bidirectional", "tri_state"])
def test_rendered_page_references_clear_label_body(direction, shape):
    label = create_global_label(
        "LONG_NET_NAME", at=(30.13, 40.27), direction=direction, shape=shape
    )
    drawing = _SymbolDrawing(SymbolStyle(), 6, None)
    drawing.measured_bounds = []
    calls = []

    def record_text(content, position, angle, attrs, color, **kwargs):
        calls.append((content, position, angle, attrs))

    drawing.text = record_text
    draw_label(drawing, label, "#000000", text_offset_ratio=0.15, label_size_ratio=0.375)
    border = drawing.measured_bounds[0]
    assert len(calls) == 2
    content, position, angle, attrs = calls[1]
    refs = text_bounds(content, position, angle, attrs, None)
    assert (
        refs.max_x < border.min_x
        or refs.min_x > border.max_x
        or refs.max_y < border.min_y
        or refs.min_y > border.max_y
    )


@pytest.mark.parametrize(
    "at,size",
    [((10.1, 20.1), (22.3, 15.2)), ((-10.1, -20.1), (0.1, 0.1)), ((0.635, -0.635), (50.8, 25.4))],
)
def test_sheet_grid_format_and_round_trip(at, size):
    sheet = create_sheet("Power supply", "pages/power.kicad_sch", at=at, size=size)
    x, y = sheet.position
    width, height = sheet.size
    for value in (x, y, x + width, y + height):
        assert value / 1.27 == pytest.approx(round(value / 1.27))
    assert width >= size[0] and height >= size[1]
    title, filename = sheet.fields
    assert title.name == "Sheetname" and filename.name == "Sheetfile"
    assert title.attributes.font_size == (1.905, 1.905) and title.attributes.bold
    assert filename.attributes.font_size == (0.889, 0.889) and not filename.attributes.bold
    assert title.attributes.justify_h == filename.attributes.justify_h == JustifyH.left
    assert title.position[0] == filename.position[0] == x
    assert title.position[1] < y and filename.position[1] > y + height
    assert sheet.fill_color.model_dump() == {"r": 232, "g": 245, "b": 233, "a": 1.0}
    assert sheet.stroke.color.model_dump() == {"r": 67, "g": 120, "b": 76, "a": 1.0}
    saved = Schematic(sheets=[sheet]).to_string()
    assert Schematic.from_string(saved).sheets[0] == sheet


def test_new_identity_and_supplied_identity():
    first = create_global_label("A", at=(0, 0))
    second = create_global_label("A", at=(0, 0))
    assert first.uuid != second.uuid
    assert create_global_label("A", at=(0, 0), uuid=first.uuid).uuid == first.uuid
    assert create_sheet("Page", "page.kicad_sch", at=(0, 0), uuid=first.uuid).uuid == first.uuid


def test_hidden_page_reference_is_still_positioned():
    hidden = create_global_label("A", at=(0, 0), show_intersheet_refs=False)
    shown = create_global_label("A", at=(0, 0))
    assert not hidden.fields[0].visible
    assert hidden.fields[0].position == shown.fields[0].position


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "diagonal"},
        {"shape": "guessed"},
        {"text_size_mm": 0},
        {"text_size_mm": float("nan")},
        {"properties": {"Netclass": "X"}},
        {"uuid": "bad"},
    ],
)
def test_invalid_label_choices(kwargs):
    with pytest.raises(ValueError):
        create_global_label("A", at=(0, 0), **kwargs)


@pytest.mark.parametrize("filename", ["../outside.kicad_sch", "C:/outside.kicad_sch", "x.txt"])
def test_sheet_reference_path_checks(filename):
    with pytest.raises(ValueError):
        create_sheet("Page", filename, at=(0, 0))


def test_native_saved_sheet_field_gaps():
    sheet = create_sheet(
        "Control interface", "interface.kicad_sch", at=(240.2, 60.1), size=(101.4, 88.8)
    )
    assert sheet.fields[0].position == (240.03, 58.6101)
    assert sheet.fields[1].position == (240.03, 149.073)


def test_sheet_title_fits_expanded_grid_aligned_width():
    from eedesigncore import measure_schematic_item

    sheet = create_sheet("Long sheet title " * 8, "child.kicad_sch", at=(10.1, 20.2))
    assert sheet.size[0] > 50.8
    assert sheet.size[1] == 30.48
    assert sheet.size[0] / 1.27 == pytest.approx(round(sheet.size[0] / 1.27))
    title = next(p for p in measure_schematic_item(sheet).parts if "field:0:Sheetname" in p.name)
    assert title.bounds.max_x < sheet.position[0] + sheet.size[0]
    assert (sheet.stroke.color.r, sheet.stroke.color.g, sheet.stroke.color.b) == (67, 120, 76)
