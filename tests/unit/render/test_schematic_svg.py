"""Schematic previews retain sheet coordinates and accept caller-supplied fonts."""

from __future__ import annotations

import builtins
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest

from eedesigncore import (
    BusEntry,
    Color,
    Fill,
    Junction,
    Label,
    LabelKind,
    Line,
    NoConnect,
    Pin,
    PinAlternate,
    PlacedSymbol,
    PlacedSymbolPin,
    Property,
    Schematic,
    SchematicStyle,
    SchRect,
    SchText,
    Sheet,
    SheetPin,
    Symbol,
    SymbolUnit,
    SymText,
    TextAttributes,
    ViewBox,
    render_schematic_svg,
)

WINDOW = ViewBox(0, 0, 200, 200)
SVG = "{http://www.w3.org/2000/svg}"


class RecordingFont:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, int], dict[str, object]]] = []

    def text_strokes(self, text, size, position, thickness, **kwargs):
        self.calls.append((text, position, kwargs))
        return [[position, (position[0] + size[0], position[1])]]


def schematic_with(*items: object, **placement: object) -> Schematic:
    symbol = Symbol(name="Probe", units=[SymbolUnit.for_symbol("Probe", items=list(items))])
    return Schematic(lib_symbols=[symbol], symbols=[PlacedSymbol(lib_id="Probe", **placement)])


def elements(schematic: Schematic, **kwargs: object) -> list[ET.Element]:
    return list(
        ET.fromstring(render_schematic_svg(schematic, view_box=WINDOW, **kwargs)).findall(
            f"{SVG}g/*"
        )
    )


def test_no_files_processes_or_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    schematic = schematic_with(SchRect(start=(-1, 2), end=(1, -2)), position=(20, 30))
    schematic.sheets = [
        Sheet(fields=[Property(name="Sheetfile", value="missing.kicad_sch", visible=False)])
    ]
    before = schematic.model_dump()
    blocked = Mock(side_effect=AssertionError("Unexpected file or process access"))
    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    first = render_schematic_svg(schematic, view_box=WINDOW)
    assert first == render_schematic_svg(schematic, view_box=WINDOW)
    ET.fromstring(first)
    assert schematic.model_dump() == before
    blocked.assert_not_called()


@pytest.mark.parametrize(
    ("angle", "mirror", "path"),
    [
        (0, None, "M8.0000,17.0000 L9.0000,17.0000"),
        (90, None, "M7.0000,22.0000 L7.0000,21.0000"),
        (180, None, "M12.0000,23.0000 L11.0000,23.0000"),
        (270, None, "M13.0000,18.0000 L13.0000,19.0000"),
        (0, "x", "M8.0000,23.0000 L9.0000,23.0000"),
        (0, "y", "M12.0000,17.0000 L11.0000,17.0000"),
    ],
)
def test_pin_positions_apply_the_placement_once(angle, mirror, path) -> None:
    schematic = schematic_with(
        Pin(electrical_type="input", shape="line", position=(-2, 3), length=1),
        position=(10, 20),
        angle=angle,
        mirror=mirror,
    )
    expected = set(path.replace("M", "").replace("L", "").split())
    actual = elements(schematic)[0].get("d", "")
    assert set(actual.replace("M", "").replace("L", "").split()) == expected


def test_fields_remain_at_saved_sheet_positions_and_rotate_upright() -> None:
    font = RecordingFont()
    schematic = schematic_with(
        position=(100, 100),
        angle=90,
        mirror="y",
        fields=[Property(name="Reference", value="U1", position=(20, 30))],
    )
    elements(schematic, font=font)
    assert font.calls == [
        (
            "U1",
            (20_000_000, 30_000_000),
            dict(angle=90, mirror=False, italic=False, halign="center", valign="center"),
        )
    ]


def test_font_reaches_every_text_category() -> None:
    font = RecordingFont()
    schematic = schematic_with(
        SymText(content="BODY"),
        Pin(electrical_type="input", shape="line", name="INPUT", number="1"),
        fields=[Property(name="Value", value="PART")],
    )
    schematic.texts = [SchText(content="FIRST{return}SECOND")]
    schematic.labels = [
        Label(kind=LabelKind.local, content="LOCAL"),
        Label(
            kind=LabelKind.global_, content="GLOBAL", fields=[Property(name="Note", value="FIELD")]
        ),
    ]
    schematic.sheets = [
        Sheet(
            fields=[Property(name="Sheetname", value="CHILD")],
            pins=[SheetPin(name="SHEETPIN", shape="input")],
        )
    ]
    elements(schematic, font=font)
    assert {call[0] for call in font.calls} >= {
        "BODY",
        "INPUT",
        "1",
        "PART",
        "FIRST",
        "SECOND",
        "LOCAL",
        "GLOBAL",
        "FIELD",
        "CHILD",
        "SHEETPIN",
    }
    first = next(call for call in font.calls if call[0] == "FIRST")
    second = next(call for call in font.calls if call[0] == "SECOND")
    assert (second[1][1] - first[1][1]) / 1e6 == pytest.approx(2.0446, abs=0.0001)


@pytest.mark.parametrize(
    ("angle", "justify", "expected"),
    [
        (0, "left", (11.4605, 20)),
        (0, "right", (8.5395, 20)),
        (90, "left", (10, 18.5395)),
        (90, "right", (10, 21.4605)),
    ],
)
def test_hierarchical_label_direction_comes_from_angle_and_alignment(
    angle, justify, expected
) -> None:
    font = RecordingFont()
    label = Label(
        kind=LabelKind.hierarchical,
        content="NET",
        position=(10, 20),
        angle=angle,
        attributes=TextAttributes(justify_h=justify),
    )
    elements(Schematic(labels=[label]), font=font)
    assert font.calls[-1][1] == pytest.approx(tuple(value * 1e6 for value in expected))


def test_global_label_fields_share_label_offset() -> None:
    font = RecordingFont()
    label = Label(
        kind=LabelKind.global_,
        content="NET",
        shape="input",
        position=(10, 20),
        fields=[Property(name="Note", value="FIELD", position=(30, 40))],
    )
    elements(Schematic(labels=[label]), font=font)
    assert font.calls[-1][1] == pytest.approx((31_428_750, 40_090_805), abs=1)


def test_connections_graphics_and_style() -> None:
    schematic = Schematic(
        lines=[
            Line(start=(1, 2), end=(3, 4)),
            Line(line_type="bus", start=(5, 6), end=(7, 8)),
            Line(line_type="polyline", start=(9, 10), end=(11, 12)),
        ],
        junctions=[Junction(position=(3, 4))],
        no_connects=[NoConnect(position=(20, 20))],
        bus_entries=[BusEntry(position=(3, 4), size=(1, 2))],
        graphic_shapes=[SchRect(start=(40, 50), end=(42, 53), fill=Fill(type="outline"))],
    )
    entries = elements(schematic, style=SchematicStyle(wire="red", bus="blue", graphics="green"))
    paths = {entry.get("d"): entry for entry in entries if entry.tag == f"{SVG}path"}
    assert paths["M1.0000,2.0000 L3.0000,4.0000"].get("stroke") == "red"
    assert paths["M5.0000,6.0000 L7.0000,8.0000"].get("stroke-width") == "0.3048"
    assert paths["M9.0000,10.0000 L11.0000,12.0000"].get("stroke") == "green"
    circle = next(entry for entry in entries if entry.tag == f"{SVG}circle")
    assert circle.get("r") == "0.4572"
    rectangle = next(entry for entry in entries if entry.tag == f"{SVG}rect")
    assert (rectangle.get("x"), rectangle.get("y")) == ("40.0000", "50.0000")


def test_alternate_pin_and_hidden_pin_selection() -> None:
    pin = Pin(
        electrical_type="input",
        shape="line",
        name="NORMAL",
        number="1",
        visible=False,
        alternates=[PinAlternate(name="ALTERNATE", electrical_type="output", shape="inverted")],
    )
    schematic = schematic_with(pin, pins=[PlacedSymbolPin(number="1", alternate="ALTERNATE")])
    font = RecordingFont()
    assert elements(schematic, font=font) == []
    elements(schematic, font=font, show_hidden_pins=True)
    assert "ALTERNATE" in [call[0] for call in font.calls]
    assert "NORMAL" not in [call[0] for call in font.calls]


def test_common_units_and_inheritance() -> None:
    parent = Symbol(
        name="Parent",
        units=[
            SymbolUnit.for_symbol(
                "Parent",
                unit_index=0,
                body_style_index=0,
                items=[SchRect(start=(0, 0), end=(1, 1))],
            ),
            SymbolUnit.for_symbol(
                "Parent", unit_index=2, items=[SchRect(start=(2, 2), end=(3, 3))]
            ),
        ],
    )
    child = Symbol(name="Child", extends="Parent")
    schematic = Schematic(
        lib_symbols=[parent, child], symbols=[PlacedSymbol(lib_id="Child", unit=2)]
    )
    assert len(elements(schematic)) == 2
    schematic.symbols[0].unit = 3
    with pytest.raises(ValueError, match="unit or body style"):
        elements(schematic)


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "angle", "mirror"],
)
def test_explicit_failures(case) -> None:
    schematic = schematic_with(SchRect(start=(0, 0), end=(1, 1)))
    if case == "missing":
        schematic.lib_symbols = []
    elif case == "duplicate":
        schematic.lib_symbols *= 2
    elif case == "angle":
        schematic.symbols[0].angle = 45
    elif case == "mirror":
        schematic.symbols[0].mirror = "invalid"
    with pytest.raises(ValueError):
        elements(schematic)


def test_label_outline_color_width_and_xml_escaping() -> None:
    attrs = TextAttributes(font_thickness=0.3, color=Color(r=10, g=20, b=30, a=0.5))
    entries = elements(
        Schematic(
            labels=[Label(kind=LabelKind.global_, content="A<&", attributes=attrs)],
            texts=[SchText(content="TEXT")],
        ),
        style=SchematicStyle(text='red" onload="bad'),
    )
    outline = next(entry for entry in entries if entry.get("stroke") == "#0a141e")
    assert outline.get("stroke-width") == "0.3000"
    assert outline.get("opacity") == "0.5000"
    assert outline.get("stroke") == "#0a141e"
    assert all("onload" not in entry.attrib for entry in entries)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_style_rejects_invalid_dimensions(value) -> None:
    with pytest.raises(ValueError):
        SchematicStyle(wire_width_mm=value)


@pytest.mark.parametrize(
    ("angle", "mirror", "horizontal", "vertical"),
    [
        (0, "x", "left", "bottom"),
        (0, "y", "right", "top"),
        (90, "x", "right", "top"),
        (90, "y", "left", "top"),
        (270, "x", "left", "top"),
        (270, "y", "right", "top"),
    ],
)
def test_mirrored_library_text_uses_kicad_alignment(angle, mirror, horizontal, vertical) -> None:
    font = RecordingFont()
    schematic = schematic_with(
        SymText(content="TEXT", attributes=TextAttributes(justify_h="left", justify_v="top")),
        angle=angle,
        mirror=mirror,
    )
    elements(schematic, font=font)
    assert font.calls[0][2]["halign"] == horizontal
    assert font.calls[0][2]["valign"] == vertical
