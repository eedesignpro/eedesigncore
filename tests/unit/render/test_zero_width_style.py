"""Stroke suppression must take precedence over layer color."""

import xml.etree.ElementTree as ET

import pytest

from eedesigncore import Board, PcbCircle, PcbLine, PcbRect, Stroke
from eedesigncore.render import LayerStyle, RenderItem, ViewBox, render_board_svg
from eedesigncore.render.document import render_element
from eedesigncore.render.geometry import SvgShape
from eedesigncore.render.traversal import identity_map


@pytest.mark.parametrize("kind", ["line", "rectangle", "circle"])
@pytest.mark.parametrize("width", [0.0, 0.2])
def test_layer_color_preserves_zero_width_and_nonzero_strokes(kind: str, width: float) -> None:
    if kind == "line":
        item = PcbLine(start=(0, 0), end=(2, 2))
    elif kind == "rectangle":
        item = PcbRect(start=(0, 0), end=(2, 2))
    else:
        item = PcbCircle(center=(0, 0), end=(2, 0))
    item.stroke = Stroke(width=width)
    item.layer = "B.Paste"
    item.id = "shape"
    style = LayerStyle({}, default_color="#ffffff", default_opacity=0.5)
    page = render_board_svg(Board(drawings=[item]), ["B.Paste"], ViewBox(0, 0, 5, 5), style=style)[
        "B.Paste"
    ]
    element = ET.fromstring(page).find(".//*[@id='shape']")
    assert element is not None
    assert element.attrib["stroke"] == ("none" if width == 0 else "#ffffff")
    assert element.attrib["fill"] == "none"
    assert element.attrib["stroke-opacity"] == "0.5000"
    if width == 0:
        assert "stroke-width" not in element.attrib
    else:
        assert element.attrib["stroke-width"] == "0.2000"
        assert 'stroke-width="0.2000" stroke="#ffffff" stroke-opacity="0.5000" fill="none"' in page


@pytest.mark.parametrize("filled", [False, True])
@pytest.mark.parametrize("width", [0.0, 0.2])
def test_shape_width_and_solid_fill_keep_their_meaning(filled: bool, width: float) -> None:
    policy = LayerStyle({}, default_color="#ffffff", default_opacity=0.5)
    item = RenderItem(
        PcbLine(stroke=Stroke(width=0.4)), "B.Paste", ("B.Paste",), None, "shape", identity_map
    )
    style = policy.style_for(item)
    assert style is not None
    shape = SvgShape("path", (("d", "M0,0 L2,0 L2,2 Z"),), filled=filled, stroke_width_mm=width)
    text = render_element(shape, item, style, policy, 4)
    element = ET.fromstring(text)
    if filled:
        assert element.attrib == {
            "d": "M0,0 L2,0 L2,2 Z",
            "stroke": "none",
            "fill": "#ffffff",
            "fill-opacity": "0.5000",
            "id": "shape",
        }
    elif width == 0:
        assert element.attrib["stroke"] == "none"
        assert element.attrib["fill"] == "none"
        assert "stroke-width" not in element.attrib
    else:
        assert (
            text == '  <path d="M0,0 L2,0 L2,2 Z" stroke-width="0.2000" stroke="#ffffff" '
            'stroke-opacity="0.5000" fill="none" id="shape"/>'
        )
