"""In-memory library previews, including symbol coordinate and pin conventions."""

from __future__ import annotations

import builtins
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import get_type_hints
from unittest.mock import Mock

import pytest

from eedesigncore import (
    ALL_CONTENT,
    HOLES,
    Board,
    DrillInfo,
    Fill,
    FillType,
    Footprint,
    FootprintPlacement,
    LayerStyle,
    Pad,
    PadShape,
    PadType,
    Pin,
    PinElectricalType,
    PinShape,
    Property,
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
    Setup,
    Stroke,
    Symbol,
    SymbolStyle,
    SymbolUnit,
    SymText,
    TextAttributes,
    ViewBox,
    render_board_svg,
    render_footprint_svg,
    render_symbol_svg,
)
from eedesigncore.common.font import StrokeFont

WINDOW = ViewBox(-20, -20, 40, 40)
SVG = "{http://www.w3.org/2000/svg}"


def symbol_with(*items: object) -> Symbol:
    return Symbol(name="Probe", units=[SymbolUnit.for_symbol("Probe", items=list(items))])


def elements(symbol: Symbol, **kwargs: object) -> list[ET.Element]:
    xml = render_symbol_svg(symbol, view_box=WINDOW, **kwargs)  # type: ignore[arg-type]
    return list(ET.fromstring(xml).findall(f"{SVG}g/*"))


def test_no_files_no_processes_and_no_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    symbol = symbol_with(
        SchRect(start=(-1, 2), end=(1, -2)),
        Pin(electrical_type="input", shape="line", name="IN", number="1"),
    )
    footprint = Footprint(fp_id="Probe", pads=[Pad(layers=["F.Cu"], size=(2, 1))])
    original_symbol = symbol.model_dump()
    original_footprint = footprint.model_dump()
    blocked = Mock(side_effect=AssertionError("File or process access during rendering"))
    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    first = render_symbol_svg(symbol, view_box=WINDOW)
    assert first == render_symbol_svg(symbol, view_box=WINDOW)
    pages = render_footprint_svg(
        footprint, layers=["F.Cu"], view_box=WINDOW, style=LayerStyle({}, default_color="#fff")
    )
    ET.fromstring(first)
    assert ET.fromstring(pages["F.Cu"]).find(f"{SVG}g/*") is not None
    assert symbol.model_dump() == original_symbol
    assert footprint.model_dump() == original_footprint
    blocked.assert_not_called()


def test_footprint_matches_board_with_placement_and_apertures() -> None:
    footprint = Footprint(
        fp_id="Probe",
        at=FootprintPlacement(position=(12, 7), angle=90),
        pads=[
            Pad(layers=["F.Cu", "F.Paste", "F.Mask"], size=(2, 1), solder_paste_margin=-0.1),
            Pad(
                pad_type=PadType.thru_hole,
                shape=PadShape.circle,
                size=(2, 2),
                drill=DrillInfo(width=0.8),
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    setup = Setup(pad_to_mask_clearance=0.1)
    layers = ["F.Cu", "B.Cu", "F.Mask", "F.Paste", HOLES]
    style = LayerStyle({}, default_color="#fff")
    actual = render_footprint_svg(
        footprint, layers=layers, view_box=WINDOW, style=style, setup=setup
    )
    expected = render_board_svg(
        Board(footprints=[footprint], setup=setup),
        layers,
        WINDOW,
        style=style,
        content=ALL_CONTENT,
    )
    assert actual == expected
    for xml in actual.values():
        assert ET.fromstring(xml).find(f"{SVG}g/*") is not None


def test_coordinate_conversion_and_symbol_zero_width() -> None:
    shapes = elements(
        symbol_with(
            SchRect(start=(2, 3), end=(6, 7)),
            SchCircle(center=(4, 5), radius=1, stroke=Stroke(width=-1)),
        )
    )
    assert shapes[0].attrib == {
        "x": "2.0000",
        "y": "-7.0000",
        "width": "4.0000",
        "height": "4.0000",
        "rx": "0.0000",
        "stroke-width": "0.1524",
        "stroke": "#840000",
        "fill": "none",
    }
    assert shapes[1].get("cy") == "-5.0000"
    assert shapes[1].get("stroke") == "none"


def test_background_is_behind_every_foreground_item() -> None:
    shapes = elements(
        symbol_with(
            SchPolyline(pts=[(-2, 0), (2, 0)]),
            SchRect(start=(-1, -1), end=(1, 1), fill=Fill(type=FillType.background)),
        )
    )
    assert [shape.tag for shape in shapes] == [SVG + "rect", SVG + "path", SVG + "rect"]
    assert shapes[0].get("stroke") == "none"
    assert shapes[0].get("fill") == "#ffffc2"
    assert shapes[-1].get("fill") == "none"


def test_shapes_and_outline_fill() -> None:
    shapes = elements(
        symbol_with(
            SchArc(start=(1, 0), mid=(0, 1), end=(-1, 0)),
            SchBezier(pts=[(0, 0), (1, 2), (2, 2), (3, 0)]),
            SchPolyline(pts=[(0, 0), (1, 1), (2, 0)], fill=Fill(type=FillType.outline)),
        )
    )
    assert "A" in shapes[0].get("d", "")
    assert shapes[1].get("d") == "M0.0000,0.0000 C1.0000,-2.0000 2.0000,-2.0000 3.0000,0.0000"
    assert shapes[2].get("fill") == "#840000"


def test_unit_and_body_selection_include_common_items() -> None:
    symbol = Symbol(
        name="Probe",
        units=[
            SymbolUnit.for_symbol(
                "Probe",
                unit_index=u,
                body_style_index=b,
                items=[SchCircle(center=(u, b), radius=0.1)],
            )
            for u, b in [(0, 0), (1, 1), (1, 2), (2, 1)]
        ],
    )
    shapes = elements(symbol, unit=1, body_style=2)
    assert [(shape.get("cx"), shape.get("cy")) for shape in shapes] == [
        ("0.0000", "0.0000"),
        ("1.0000", "-2.0000"),
    ]
    with pytest.raises(ValueError, match="does not exist"):
        elements(symbol, unit=3)
    with pytest.raises(ValueError, match="positive"):
        elements(symbol, unit=0)


@pytest.mark.parametrize(
    "angle,body",
    [
        (0, "12.5400,-7.0000"),
        (90, "10.0000,-9.5400"),
        (180, "7.4600,-7.0000"),
        (270, "10.0000,-4.4600"),
    ],
)
def test_pin_connection_and_body_end(angle: int, body: str) -> None:
    pin = Pin(
        electrical_type=PinElectricalType.input, shape=PinShape.line, position=(10, 7), angle=angle
    )
    assert elements(symbol_with(pin))[0].get("d") == f"M{body} L10.0000,-7.0000"


@pytest.mark.parametrize("shape", list(PinShape))
@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_all_pin_shapes_are_valid_xml(shape: PinShape, angle: int) -> None:
    pin = Pin(electrical_type=PinElectricalType.input, shape=shape, angle=angle)
    shapes = elements(symbol_with(pin))
    assert len(shapes) == (
        1
        if shape == PinShape.line
        else 3
        if shape in (PinShape.inverted_clock, PinShape.clock_low, PinShape.non_logic)
        else 2
    )


def test_inversion_bubble_and_clock_geometry() -> None:
    shapes = elements(
        symbol_with(
            Pin(electrical_type="input", shape="inverted_clock", position=(-5.08, 0), length=2.54)
        )
    )
    assert (shapes[0].get("cx"), shapes[0].get("r")) == ("-3.1750", "0.6350")
    assert shapes[1].get("d") == "M-3.8100,0.0000 L-5.0800,0.0000"
    assert shapes[2].get("d") == "M-2.5400,0.6350 L-1.9050,0.0000 L-2.5400,-0.6350"


def test_private_and_hidden_items() -> None:
    symbol = symbol_with(
        SchCircle(radius=1, private=True),
        SymText(content="SECRET", private=True),
        Pin(electrical_type="input", shape="line", visible=False),
    )
    symbol.fields = [Property(name="Value", value="HIDDEN", visible=False)]
    assert elements(symbol) == []
    assert len(elements(symbol, show_hidden_pins=True)) == 1


def test_inheritance_and_errors() -> None:
    parent = symbol_with(SchCircle(radius=1))
    parent.fields = [Property(name="Value", value="A")]
    child = Symbol(name="Child", extends="Probe", fields=[Property(name="Value", value="B")])
    expected = parent.model_copy(deep=True)
    expected.fields = child.fields
    before = parent.model_dump()
    assert render_symbol_svg(
        child, view_box=WINDOW, symbols={"Probe": parent}
    ) == render_symbol_svg(expected, view_box=WINDOW)
    assert parent.model_dump() == before
    with pytest.raises(ValueError, match="parent symbol"):
        elements(child)
    parent.extends = "Child"
    with pytest.raises(ValueError, match="cycle"):
        elements(child, symbols={"Probe": parent, "Child": child})


def test_text_is_not_mirrored_and_tenths_angle_is_respected() -> None:
    base = elements(symbol_with(SymText.from_degrees(0, content="F", position=(3, 4))))
    rotated = elements(symbol_with(SymText.from_degrees(90, content="F", position=(3, 4))))
    assert base and rotated and [e.attrib for e in base] != [e.attrib for e in rotated]
    assert all(e.get("fill") == "none" for e in base)


def test_hatched_symbol_has_fill_and_outline() -> None:
    shapes = elements(
        symbol_with(SchRect(start=(0, 0), end=(10, 10), fill=Fill(type=FillType.hatch)))
    )
    assert any(shape.get("opacity") == "0.4000" for shape in shapes)
    assert any(shape.get("fill") == "none" for shape in shapes)


def test_style_escapes_xml_attributes() -> None:
    shapes = elements(symbol_with(SchCircle(radius=1)), style=SymbolStyle(body='"/><script>'))
    assert len(shapes) == 1
    assert shapes[0].get("stroke") == '"/><script>'
    with pytest.raises(ValueError, match="finite and positive"):
        SymbolStyle(line_width_mm=float("nan"))


def test_annotations_resolve_for_public_operations() -> None:
    assert get_type_hints(render_symbol_svg)["return"] is str
    assert get_type_hints(render_footprint_svg)["return"] == dict[str, str]


def test_font_control_reaches_every_symbol_text_kind() -> None:
    font = Mock(wraps=StrokeFont())
    symbol = symbol_with(
        Pin(
            electrical_type="input",
            shape="line",
            name="INPUT",
            number="7",
            name_text_size=1.0,
            number_text_size=0.8,
        ),
        SymText.from_degrees(
            90,
            content="NOTE",
            position=(2, 3),
            attributes=TextAttributes(font_size=(2, 3), italic=True),
        ),
    )
    symbol.fields = [
        Property(
            name="Value",
            value="VALUE",
            position=(0, -4),
            attributes=TextAttributes(font_size=(1.5, 1.5)),
        )
    ]
    render_symbol_svg(symbol, view_box=WINDOW, font=font)
    calls = {call.args[0]: call for call in font.text_strokes.call_args_list}
    assert set(calls) == {"INPUT", "7", "NOTE", "VALUE"}
    assert calls["INPUT"].args[1] == (1000000, 1000000)
    assert calls["7"].args[1] == (800000, 800000)
    assert calls["NOTE"].args[1:3] == ((2000000, 3000000), (2000000, -3000000))
    assert calls["NOTE"].kwargs["angle"] == 90
    assert calls["NOTE"].kwargs["italic"] is True
    assert calls["VALUE"].args[1] == (1500000, 1500000)


def test_metrics_only_font_is_rejected_before_text_disappears() -> None:
    with pytest.raises(ValueError, match="text_strokes"):
        render_symbol_svg(symbol_with(SymText(content="Visible")), view_box=WINDOW, font=object())  # type: ignore[arg-type]
