"""Saved solid fills retain their painted extent in board and footprint SVGs."""

import xml.etree.ElementTree as ET

import pytest

from eedesigncore import (
    Board,
    CliParityStyle,
    Fill,
    Footprint,
    FootprintPlacement,
    LayerStyle,
    PcbCircle,
    PcbPoly,
    PcbRect,
    Stroke,
    ViewBox,
    render_board_svg,
    render_footprint_svg,
)

WINDOW = ViewBox(-20, -20, 40, 40)
COLOR = "#123456"


@pytest.mark.parametrize("kind", ["circle", "rectangle", "polygon"])
@pytest.mark.parametrize("width", [0, 0.12, 0.4])
@pytest.mark.parametrize("filled", [False, True])
@pytest.mark.parametrize("host", ["board", "footprint", "placed_footprint"])
@pytest.mark.parametrize("cli_style", [False, True])
def test_saved_fill(kind, width, filled, host, cli_style):
    options = dict(
        stroke=Stroke(width=width),
        fill=Fill(type="outline" if filled else "none"),
        layer="F.SilkS",
        id="graphic-id",
    )
    item = {
        "circle": lambda: PcbCircle(center=(1, 2), end=(1.1, 2), **options),
        "rectangle": lambda: PcbRect(start=(0, 0), end=(2, 3), **options),
        "polygon": lambda: PcbPoly(pts=[(0, 0), (2, 0), (0, 3)], **options),
    }[kind]()
    style = (
        CliParityStyle({"F.SilkS": COLOR})
        if cli_style
        else LayerStyle({"F.SilkS": COLOR}, opacity={"F.SilkS": 0.35})
    )
    if host == "board":
        source = Board(drawings=[item])
    else:
        source = Footprint(fp_id="FillProbe", drawings=[item])
        if host == "placed_footprint":
            source.at = FootprintPlacement(position=(10, 5), angle=90)
    before = source.model_dump()
    if isinstance(source, Board):
        svg = render_board_svg(source, ["F.SilkS"], WINDOW, style=style)["F.SilkS"]
    else:
        svg = render_footprint_svg(source, layers=["F.SilkS"], view_box=WINDOW, style=style)[
            "F.SilkS"
        ]
    nodes = list(ET.fromstring(svg).find("{*}g"))
    assert len(nodes) == 1
    node = nodes[0]
    disc = kind == "circle" and (filled or width > 0.2)
    assert node.get("fill") == (COLOR if filled or disc else "none")
    assert node.get("stroke") == ("none" if disc or width == 0 else COLOR)
    opacity = 1 if cli_style else 0.35
    if filled or disc:
        assert float(node.get("fill-opacity")) == opacity
    if not disc and width:
        assert float(node.get("stroke-width")) == width
        assert float(node.get("stroke-opacity")) == opacity
    if kind == "circle":
        assert float(node.get("r")) == pytest.approx(0.1 + (width / 2 if disc else 0))
        center = (12, 4) if host == "placed_footprint" else (1, 2)
        assert (float(node.get("cx")), float(node.get("cy"))) == center
    assert source.model_dump() == before


def test_layer_filter_still_hides_solid_graphics():
    board = Board(drawings=[PcbCircle(fill=Fill(type="outline"), end=(1, 0))])
    svg = render_board_svg(board, ["F.SilkS"], WINDOW, style=LayerStyle({}))["F.SilkS"]
    assert not list(ET.fromstring(svg).find("{*}g"))
