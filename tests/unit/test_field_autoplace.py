"""Field placement preserves electrical data and ignores surrounding items."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from eedesigncore import (
    JustifyH,
    Label,
    LabelKind,
    PlacedSymbol,
    Property,
    Schematic,
    SchRect,
    SchText,
    Symbol,
    SymbolUnit,
    TextAttributes,
    autoplace_fields,
)
from eedesigncore.connectivity.geometry import apply, symbol_transform
from eedesigncore.render.dnp import text_bounds
from eedesigncore.render.schematic_symbols import _field_center
from eedesigncore.render.symbols import SymbolStyle, _SymbolDrawing


def schematic() -> Schematic:
    definition = Symbol(
        name="Test:Part",
        units=[
            SymbolUnit(
                name="Part_1_1",
                unit_index=1,
                body_style_index=1,
                items=[SchRect(start=(-2.54, -2.54), end=(2.54, 2.54))],
            )
        ],
    )
    return Schematic(
        lib_symbols=[definition],
        symbols=[
            PlacedSymbol(
                lib_id=definition.name,
                position=(-12.1, -8.2),
                fields=[
                    Property(name="Reference", value="U1"),
                    Property(name="Value", value="Part"),
                ],
            )
        ],
    )


def displayed_box(symbol: PlacedSymbol, field: Property):
    attrs = field.attributes
    assert attrs is not None
    center = _field_center(_SymbolDrawing(SymbolStyle(), 6, None), field.value, attrs)
    angle = math.radians(field.angle)
    transform = symbol_transform(symbol.angle, symbol.mirror)
    offset = apply(
        transform,
        (
            center[0] * math.cos(angle) + center[1] * math.sin(angle),
            -center[0] * math.sin(angle) + center[1] * math.cos(angle),
        ),
    )
    centered = attrs.model_copy(update={"justify_h": JustifyH.center})
    return text_bounds(
        field.value,
        (field.position[0] + offset[0], field.position[1] + offset[1]),
        0,
        centered,
        None,
    )


@pytest.mark.parametrize("style", ["left", "right", "top", "bottom"])
@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("mirror", [None, "x", "y"])
def test_forced_symbol_side_grid_and_horizontal_text(style, angle, mirror):
    sheet = schematic()
    placed = sheet.symbols[0]
    placed.angle, placed.mirror = angle, mirror
    autoplace_fields(sheet, style=style)
    for field in placed.fields:
        axis = 0 if style in ("left", "right") else 1
        value = field.position[axis]
        assert value / 1.27 == pytest.approx(round(value / 1.27))
        assert (field.angle + (90 if angle % 180 else 0)) % 180 == 0
        box = displayed_box(placed, field)
        if style == "left":
            assert box.max_x < placed.position[0] - 2.54
        elif style == "right":
            assert box.min_x > placed.position[0] + 2.54
        elif style == "top":
            assert box.max_y < placed.position[1] - 2.54
        else:
            assert box.min_y > placed.position[1] + 2.54
    once = sheet.model_dump()
    autoplace_fields(sheet, style=style)
    assert sheet.model_dump() == once


def test_surroundings_and_excluded_fields():
    plain = schematic()
    placed = plain.symbols[0]
    placed.fields.extend(
        [
            Property(name="Hidden", value="x", visible=False),
            Property(name="Fixed", value="x", do_not_autoplace=True),
            Property(name="Private", value="x", private=True),
            Property(name="Empty", value=""),
        ]
    )
    excluded = [field.model_dump() for field in placed.fields[2:]]
    crowded = plain.model_copy(deep=True)
    crowded.texts.append(SchText(content="Surrounding text", position=(-8, -8)))
    crowded.labels.append(Label(kind=LabelKind.local, content="NET", position=(-8, -8)))
    autoplace_fields(plain)
    autoplace_fields(crowded)
    assert plain.symbols == crowded.symbols
    assert excluded == [field.model_dump() for field in placed.fields[2:]]


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("justify", [JustifyH.left, JustifyH.right])
@pytest.mark.parametrize("style", ["auto", "left", "right", "top", "bottom"])
def test_global_label_preserves_electrical_data(angle, justify, style):
    label = Label(
        kind=LabelKind.global_,
        content="POWER",
        shape="output",
        angle=angle,
        position=(-10.12, 18.9),
        uuid="label-id",
        attributes=TextAttributes(justify_h=justify),
        fields=[
            Property(name="Intersheetrefs", value="[2,3]"),
            Property(name="Netclass", value="POWER", visible=False),
            Property(name="Description", value="Power output"),
        ],
    )
    before = label.model_dump(exclude={"fields", "fields_autoplaced"})
    hidden = label.fields[1].model_dump()
    autoplace_fields(label, style=style)
    assert before == label.model_dump(exclude={"fields", "fields_autoplaced"})
    assert hidden == label.fields[1].model_dump()
    for field in [label.fields[2]] if style == "auto" else [label.fields[0], label.fields[2]]:
        assert all(v / 1.27 == pytest.approx(round(v / 1.27)) for v in field.position)
    once = label.model_dump()
    autoplace_fields(label, style=style)
    assert label.model_dump() == once


def test_invalid_later_item_leaves_whole_schematic_unchanged():
    sheet = schematic()
    sheet.symbols.append(
        PlacedSymbol(lib_id="Missing", fields=[Property(name="Value", value="x")])
    )
    before = sheet.model_dump()
    with pytest.raises(ValueError, match="Missing symbol definition"):
        autoplace_fields(sheet)
    assert sheet.model_dump() == before


@pytest.mark.parametrize("grid", [0, -1, float("nan"), float("inf")])
def test_invalid_grid(grid):
    with pytest.raises(ValueError, match="grid"):
        autoplace_fields(schematic(), grid_mm=grid)


def test_single_symbol_and_save_load():
    sheet = schematic()
    autoplace_fields(sheet.symbols[0], definition=sheet.lib_symbols[0], style="top")
    saved = sheet.to_string()
    loaded = Schematic.from_string(saved)
    assert loaded.symbols[0].fields == sheet.symbols[0].fields
    assert loaded.symbols[0].fields_autoplaced


def test_different_font_sizes_do_not_overlap():
    sheet = schematic()
    placed = sheet.symbols[0]
    placed.fields = [
        Property(name=f"Field{i}", value="Text", attributes=TextAttributes(font_size=(size, size)))
        for i, size in enumerate([0.8, 4.2, 1.27, 3.9])
    ]
    autoplace_fields(sheet, style="right")
    boxes = [displayed_box(placed, field) for field in placed.fields]
    assert all(a.max_y < b.min_y for a, b in zip(boxes, boxes[1:], strict=False))


def test_native_saved_passive_fields():
    path = Path(__file__).parents[1] / "fixtures/sch/native_autoplace/passive-fields.kicad_sch"
    sheet = Schematic.from_file(path)
    expected = [symbol.model_copy(deep=True) for symbol in sheet.symbols]
    autoplace_fields(sheet)
    for actual, native in zip(sheet.symbols, expected, strict=True):
        assert actual.fields == native.fields
    autoplace_fields(sheet)
    for actual, native in zip(sheet.symbols, expected, strict=True):
        assert actual.fields == native.fields


@pytest.mark.parametrize("page", ["placement-demo", "interface"])
def test_native_saved_page_references(page):
    from eedesigncore import create_global_label, load_font

    try:
        font = load_font("Cascadia Mono")
    except ValueError:
        pytest.skip("The native reference uses the installed Cascadia Mono font.")
    path = Path(__file__).parents[1] / "fixtures/sch/native_autoplace"
    sheet = Schematic.from_file(path / f"{page}-page-references.kicad_sch")
    for label in sheet.labels:
        if label.kind != LabelKind.global_:
            continue
        expected = label.model_copy(deep=True)
        autoplace_fields(label, font=font)
        assert label.fields == expected.fields
        direction = {0: "right", 90: "up", 180: "left", 270: "down"}[int(label.angle)]
        created = create_global_label(
            label.content, at=label.position, direction=direction, shape=label.shape, font=font
        )
        assert created.fields == expected.fields
        autoplace_fields(label, font=font)
        assert label.fields == expected.fields
