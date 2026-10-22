"""Constructed copper drawing fixtures for public SVG output."""

import math
import xml.etree.ElementTree as ET

import pytest

from eedesigncore.common.models import Fill, FillType, Stroke, StrokeType
from eedesigncore.common.pcb_family.board_items import Table, TableBorder, TableCell
from eedesigncore.common.pcb_family.shapes import PcbArc, PcbCircle, PcbCurve, PcbLine, PcbRect
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import Board, PcbTarget, PlotParams, Setup
from eedesigncore.render import LayerStyle, ViewBox, render_board_svg, render_footprint_svg
from eedesigncore.render.bounds import PlotBoundsPolicy, plot_content_bbox
from eedesigncore.render.geometry import shapes_for, stroke_attrs
from eedesigncore.render.traversal import identity_map

STYLE = LayerStyle({}, default_color="#000000")
WINDOW = ViewBox(-30, -30, 80, 80)
SETUP = Setup(pcbplotparams=PlotParams(dashed_line_dash_ratio=8, dashed_line_gap_ratio=2))


def elements(board):
    page = render_board_svg(board, ["F.Cu"], WINDOW, style=STYLE)["F.Cu"]
    return list(ET.fromstring(page).iter())[2:]


@pytest.mark.parametrize("fill", [FillType.none, FillType.outline])
@pytest.mark.parametrize("angle", [0, 37, 90, 180])
def test_rounded_rectangle_keeps_arcs_and_tight_rotated_bounds(fill, angle):
    item = PcbRect(
        start=(-3, -2),
        end=(3, 2),
        corner_radius=1,
        layer="F.Cu",
        fill=Fill(type=fill),
        stroke=Stroke(width=0.2),
    )
    fp = Footprint(at=FootprintPlacement(position=(10, 20), angle=angle), drawings=[item])
    board = Board(footprints=[fp])
    before = board.model_dump()
    emitted = elements(board)
    assert len(emitted) == 1
    assert emitted[0].attrib["d"].count("A") == 4
    assert emitted[0].attrib["d"].endswith("Z")
    bounds = plot_content_bbox(board, ["F.Cu"], policy=PlotBoundsPolicy(actual_drill_marks=False))
    theta = math.radians(angle)
    half_x = 2 * abs(math.cos(theta)) + abs(math.sin(theta)) + 1.1
    half_y = 2 * abs(math.sin(theta)) + abs(math.cos(theta)) + 1.1
    assert bounds is not None
    assert (bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y) == pytest.approx(
        (10 - half_x, 10 + half_x, 20 - half_y, 20 + half_y), abs=0.001
    )
    assert board.model_dump() == before
    page = render_footprint_svg(fp, layers=["F.Cu"], view_box=WINDOW, style=STYLE)["F.Cu"]
    assert ET.fromstring(page).find(".//{*}path").attrib == emitted[0].attrib


@pytest.mark.parametrize("radius, expected", [(100, 2), (2, 2), (1, 1), (-1, 0), (0, 0)])
def test_board_rectangle_reversed_endpoints_and_radius_limits(radius, expected):
    item = PcbRect(start=(6, 4), end=(0, 0), corner_radius=radius, layer="F.Cu")
    data = elements(Board(drawings=[item]))[0].attrib["d"]
    assert data.count("A") == (4 if expected else 0)
    if expected:
        assert f"A{expected:.4f} {expected:.4f}" in data


@pytest.mark.parametrize(
    "shape",
    [
        PcbLine(start=(0, 0), end=(8, 0)),
        PcbRect(start=(0, 0), end=(8, 4), corner_radius=1),
        PcbArc(start=(0, 0), mid=(1, 1), end=(2, 0)),
        PcbCircle(center=(0, 0), end=(2, 0)),
        PcbCurve(pts=[(0, 0), (1, 2), (3, 2), (4, 0)]),
    ],
)
@pytest.mark.parametrize(
    "stroke_type, expected",
    [
        (StrokeType.dash, "1.4000,0.6000"),
        (StrokeType.dot, "0.0400,0.6000"),
        (StrokeType.dash_dot, "1.4000,0.6000,0.0400,0.6000"),
        (StrokeType.dash_dot_dot, "1.4000,0.6000,0.0400,0.6000,0.0400,0.6000"),
    ],
)
@pytest.mark.parametrize("in_footprint", [False, True])
def test_board_dash_ratios_reach_all_paths(shape, stroke_type, expected, in_footprint):
    item = shape.model_copy(
        update={"layer": "F.Cu", "stroke": Stroke(width=0.2, type=stroke_type)}
    )
    fp = Footprint(at=FootprintPlacement(position=(10, 20), angle=37), drawings=[item])
    board = (
        Board(setup=SETUP, footprints=[fp])
        if in_footprint
        else Board(setup=SETUP, drawings=[item])
    )
    before = board.model_dump()
    assert elements(board)[0].attrib["stroke-dasharray"] == expected
    assert board.model_dump() == before
    if in_footprint:
        page = render_footprint_svg(
            fp, layers=["F.Cu"], view_box=WINDOW, style=STYLE, setup=SETUP
        )["F.Cu"]
        assert list(ET.fromstring(page).iter())[2].attrib["stroke-dasharray"] == expected


def test_table_borders_use_board_ratios():
    table = Table(
        layer="F.Cu",
        column_count=1,
        column_widths=[4],
        row_heights=[2],
        cells=[TableCell(start=(0, 0), end=(4, 2))],
        border=TableBorder(stroke=Stroke(width=0.2, type=StrokeType.dash)),
    )
    borders = [
        item
        for item in elements(Board(setup=SETUP, drawings=[table]))
        if "stroke-dasharray" in item.attrib
    ]
    assert len(borders) == 4
    assert all(item.attrib["stroke-dasharray"] == "1.4000,0.6000" for item in borders)


def test_default_ratios_and_minimum_lengths():
    stroke = Stroke(width=0.2, type=StrokeType.dash)
    assert dict(stroke_attrs(stroke))["stroke-dasharray"] == "2.2000,0.8000"
    assert (
        dict(stroke_attrs(stroke, dash_ratio=-5, gap_ratio=-5))["stroke-dasharray"]
        == "0.2000,0.2000"
    )
    with pytest.raises(ValueError, match="finite"):
        stroke_attrs(stroke, dash_ratio=float("nan"))


def test_reflected_roundrect_changes_arc_sweep():
    item = PcbRect(start=(0, 0), end=(6, 4), corner_radius=1)
    normal = shapes_for(item, identity_map)
    reflected = shapes_for(item, lambda point: (-point[0], point[1]))
    assert normal and reflected
    assert dict(normal[0].attrs)["d"].count(" 0 0 1 ") == 4
    assert dict(reflected[0].attrs)["d"].count(" 0 0 0 ") == 4


def test_target_retains_kicad_plot_arm_extent():
    target = PcbTarget(position=(10, 20), size=6, width=0.2, is_x=True, layer="F.Cu")
    paths = [
        item.attrib["d"] for item in elements(Board(targets=[target])) if item.tag.endswith("path")
    ]
    assert paths == ["M7.0000,17.0000 L13.0000,23.0000", "M7.0000,23.0000 L13.0000,17.0000"]


@pytest.mark.parametrize(
    "shape",
    [PcbRect(start=(0, 0), end=(4, 2), corner_radius=0.5), PcbCircle(center=(0, 0), end=(1, 0))],
)
def test_zero_width_solid_uses_public_fill_policy_and_callback(shape):
    item = shape.model_copy(
        update={"layer": "F.Cu", "fill": Fill(type=FillType.outline), "stroke": Stroke(width=0)}
    )
    board = Board(drawings=[item])
    fragments = []
    skipped = []
    page = render_board_svg(
        board,
        ["F.Cu"],
        WINDOW,
        style=STYLE,
        on_element=lambda entry, svg: fragments.append((entry, svg)),
        on_skip=skipped.append,
    )["F.Cu"]
    assert not skipped
    assert len(fragments) == 1
    assert fragments[0][0].item is item
    assert fragments[0][1] in page
    element = ET.fromstring(fragments[0][1])
    assert element.attrib["fill"] == "#000000"
    assert element.attrib["stroke"] == "none"
