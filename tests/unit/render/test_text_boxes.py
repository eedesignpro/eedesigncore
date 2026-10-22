"""Text boxes retain text independently of the border and match KiCad layout."""

import builtins
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest

from eedesigncore import (
    Board,
    Color,
    Fill,
    Footprint,
    FootprintPlacement,
    LayerStyle,
    SchCircle,
    Stroke,
    Symbol,
    SymbolUnit,
    SymTextBox,
    TextAttributes,
    ViewBox,
    render_board_svg,
    render_footprint_svg,
    render_symbol_svg,
)
from eedesigncore.common.font import StrokeFont
from eedesigncore.common.pcb_family.shapes import PcbTextBox
from eedesigncore.render.compare import compare_svg
from eedesigncore.render.text_boxes import pcb_text_box_shapes, wrapped_lines
from eedesigncore.render.traversal import identity_map

WINDOW = ViewBox(-30, -30, 60, 60)
SVG = "{http://www.w3.org/2000/svg}"


def symbol(box: SymTextBox) -> Symbol:
    return Symbol(
        name="Probe",
        units=[
            SymbolUnit.for_symbol(
                "Probe", items=[SchCircle(radius=0.125, stroke=Stroke(width=0.1524)), box]
            )
        ],
    )


@pytest.mark.parametrize("angle,h,v", [(0, "left", "top"), (90, "right", "bottom")])
def test_symbol_text_box_matches_kicad(angle: int, h: str, v: str) -> None:
    box = SymTextBox(
        content="ABC DEF GHI JKL\nXYZ",
        position=(-6, 5),
        size=(12, -10),
        angle=angle,
        margins=(0.2, 0.3, 0.4, 0.5),
        stroke=Stroke(width=-0.0001),
        attributes=TextAttributes(font_size=(1, 1), justify_h=h, justify_v=v),
    )
    root = ET.fromstring(render_symbol_svg(symbol(box), view_box=WINDOW))
    for group in root:
        for element in list(group):
            if element.get("stroke") == "none" and element.get("fill") == "none":
                group.remove(element)
    for element in root.iter():
        element.tag = element.tag.removeprefix(SVG)
    expected = (
        Path(__file__).parents[2] / "fixtures" / "svg_text_boxes" / f"box_{angle}_{h}_{v}.svg"
    ).read_text()
    assert compare_svg(ET.tostring(root, encoding="unicode"), expected, tolerance=0.002).agrees


@pytest.mark.parametrize("border", [False, True])
@pytest.mark.parametrize("width", [0.0, 0.3])
def test_footprint_text_pen_is_independent_of_border(border: bool, width: float) -> None:
    box = PcbTextBox(
        content="A",
        start=(0, 0),
        end=(10, 5),
        border_enabled=border,
        stroke=Stroke(width=width, type="dash"),
        attributes=TextAttributes(font_thickness=0.15),
    )
    skipped = []
    pages = render_footprint_svg(
        Footprint(fp_id="Probe", drawings=[box]),
        layers=["F.SilkS"],
        view_box=WINDOW,
        style=LayerStyle({}, default_color="#fff"),
        on_skip=skipped.append,
    )
    elements = list(ET.fromstring(pages["F.SilkS"]).findall(f"{SVG}g/*"))
    assert not skipped
    if border:
        outline = elements.pop(0)
        assert (outline.get("stroke") == "none") == (width == 0)
        assert (outline.get("stroke-dasharray") is not None) == (width > 0)
    assert elements
    assert all(e.get("stroke-width") == "0.1500" and e.get("stroke") == "#fff" for e in elements)
    assert all(e.get("fill") == "none" and e.get("stroke-dasharray") is None for e in elements)


def test_wrapping_preserves_long_words_and_blank_lines() -> None:
    attrs = TextAttributes(font_size=(1, 1))
    assert wrapped_lines("ABC DEF\n\nLONGWORD", 2, attrs, 0.15, None) == [
        "ABC",
        "DEF",
        "",
        "LONGWORD",
    ]
    assert wrapped_lines(" A  B  ", 100, attrs, 0.15, None) == [" A  B"]


def test_custom_font_controls_wrapping_and_glyphs() -> None:
    class WideFont(StrokeFont):
        def __init__(self):
            super().__init__()
            self.lines = []

        def advance(self, text, size):
            return len(text) * size[0] * 2

        def text_strokes(self, text, *args, **kwargs):
            self.lines.append(text)
            return super().text_strokes(text, *args, **kwargs)

    font = WideFont()
    render_symbol_svg(symbol(SymTextBox(content="A B", size=(5, -8))), view_box=WINDOW, font=font)
    assert font.lines == ["A", "B"]


def test_text_box_font_without_width_measurement_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="advance"):
        render_symbol_svg(
            symbol(SymTextBox(content="A", size=(5, -5))), view_box=WINDOW, font=object()
        )


def test_footprint_box_transform_and_no_mutation_or_files(monkeypatch) -> None:
    footprint = Footprint(
        fp_id="Probe",
        at=FootprintPlacement(position=(10, 20), angle=90),
        drawings=[PcbTextBox(content="A B", start=(0, 0), end=(5, 8), border_enabled=True)],
    )
    original = footprint.model_dump()
    blocked = Mock(side_effect=AssertionError("Unexpected file or process access"))
    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    first = render_footprint_svg(
        footprint, layers=["F.SilkS"], view_box=WINDOW, style=LayerStyle({}, default_color="#000")
    )
    assert first == render_footprint_svg(
        footprint, layers=["F.SilkS"], view_box=WINDOW, style=LayerStyle({}, default_color="#000")
    )
    assert footprint.model_dump() == original
    root = ET.fromstring(first["F.SilkS"])
    assert (
        root.find(f"{SVG}g/{SVG}path").get("d")
        == "M10.0000,20.0000 L10.0000,15.0000 L18.0000,15.0000 L18.0000,20.0000 Z"
    )
    blocked.assert_not_called()


def test_knockout_has_filled_outline_with_text_holes() -> None:
    box = PcbTextBox(content="A", knockout=True, start=(0, 0), end=(5, 5))
    shapes = pcb_text_box_shapes(box, identity_map, 4, None)
    assert shapes and shapes[0].filled
    assert dict(shapes[0].attrs)["d"].count("M") > 1


def test_hidden_box_text_retains_border() -> None:
    box = PcbTextBox(
        content="A",
        start=(0, 0),
        end=(5, 5),
        border_enabled=True,
        attributes=TextAttributes(visible=False),
    )
    assert len(pcb_text_box_shapes(box, identity_map, 4, None)) == 1


def test_board_and_placed_footprint_text_boxes_match_kicad() -> None:
    fixtures = Path(__file__).parents[2] / "fixtures" / "svg_text_boxes"
    board = Board.from_file(fixtures / "constructed.kicad_pcb")
    skipped = []
    actual = render_board_svg(
        board,
        ["F.SilkS"],
        WINDOW,
        style=LayerStyle({}, default_color="#000"),
        on_skip=skipped.append,
    )["F.SilkS"]
    assert not skipped
    assert compare_svg(actual, (fixtures / "board.svg").read_text(), tolerance=0.002).agrees


def test_symbol_text_box_fill_and_text_visibility_are_independent() -> None:
    box = SymTextBox(
        content="A",
        size=(5, -5),
        stroke=Stroke(width=-0.0001),
        fill=Fill(type="color", color=Color(r=255, g=255, b=255, a=1)),
        attributes=TextAttributes(visible=False),
    )
    root = ET.fromstring(render_symbol_svg(symbol(box), view_box=WINDOW))
    rectangles = root.findall(f"{SVG}g/{SVG}rect")
    assert len(rectangles) == 2
    assert rectangles[0].get("fill") == "#ffffff"
    assert rectangles[0].get("stroke") == "none"
    assert not root.findall(f"{SVG}g/{SVG}path")


def test_measurement_only_font_does_not_silently_hide_text() -> None:
    class MeasurementOnly:
        def advance(self, text, size):
            return len(text) * size[0]

    with pytest.raises(ValueError, match="text_strokes"):
        pcb_text_box_shapes(
            PcbTextBox(content="A", start=(0, 0), end=(5, 5)), identity_map, 4, MeasurementOnly()
        )


@pytest.mark.parametrize(
    "color,expected", [(None, "#840000"), (Color(r=0, g=64, b=128, a=1), "#004080")]
)
def test_symbol_text_box_uses_device_color_unless_explicit(color, expected) -> None:
    box = SymTextBox(content="A", size=(5, -5), attributes=TextAttributes(color=color))
    root = ET.fromstring(render_symbol_svg(symbol(box), view_box=WINDOW))
    assert all(e.get("stroke") == expected for e in root.findall(f"{SVG}g/{SVG}path"))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("A\n", ["A"]),
        ("A\n\n", ["A", ""]),
        ("A\n\nB\n", ["A", "", "B"]),
        ("\n", [""]),
        ("", []),
    ],
)
def test_trailing_separator_does_not_add_a_phantom_line(text, expected):
    assert wrapped_lines(text, 100, TextAttributes(), 0, None) == expected


@pytest.mark.parametrize("angle", [0, 90])
@pytest.mark.parametrize("vertical", ["top", "center", "bottom"])
def test_trailing_newline_does_not_shift_symbol_text_box(angle, vertical):
    box = SymTextBox(
        content="A\nB",
        position=(-5, 5),
        size=(10, -10),
        angle=angle,
        attributes=TextAttributes(justify_v=vertical),
    )
    first = render_symbol_svg(symbol(box), view_box=WINDOW)
    box.content += "\n"
    assert render_symbol_svg(symbol(box), view_box=WINDOW) == first
