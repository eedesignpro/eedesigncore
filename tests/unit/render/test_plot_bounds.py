"""Plot extents retain true arc crossings and selected text geometry."""

import math

import pytest

from eedesigncore.common.models import Property, Stroke, TextAttributes
from eedesigncore.common.pcb_family.board_items import Zone, ZoneFilledPolygon, ZoneOutlinePolygon
from eedesigncore.common.pcb_family.enums import PadShape, PadType
from eedesigncore.common.pcb_family.footprint_scoped import Padstack, PadstackLayer, PadstackMode
from eedesigncore.common.pcb_family.shapes import PcbArc, PcbLine, PcbRect, PcbText
from eedesigncore.mod.models import DrillInfo, Footprint, FootprintPlacement, Pad
from eedesigncore.pcb.models import Board, BoardLayer
from eedesigncore.render.bounds import (
    PlotBoundsPolicy,
    UnsupportedPlotContent,
    plot_content_bbox,
)

POLICY = PlotBoundsPolicy(stroke_mode="dxf_centerline")


def test_empty_and_unselected() -> None:
    board = Board(drawings=[PcbLine(start=(0, 0), end=(5, 7), layer="Edge.Cuts")])
    assert plot_content_bbox(board, [], policy=POLICY) is None
    assert plot_content_bbox(board, ["F.Cu"], policy=POLICY) is None


def test_rotated_rectangle_uses_all_corners() -> None:
    fp = Footprint(
        fp_id="rectangle",
        at=FootprintPlacement(position=(10, 20), angle=45),
        drawings=[PcbRect(start=(-1, -1), end=(1, 1), layer="F.Fab")],
    )
    box = plot_content_bbox(Board(footprints=[fp]), ["F.Fab"], policy=POLICY)
    assert box is not None
    assert box.min_x == pytest.approx(10 - math.sqrt(2), abs=0.00001)
    assert box.max_y == pytest.approx(20 + math.sqrt(2), abs=0.00001)


def test_rotated_arc_uses_board_axis_crossings() -> None:
    fp = Footprint(
        fp_id="arc",
        at=FootprintPlacement(position=(10, 20), angle=45),
        drawings=[PcbArc(start=(1, 0), mid=(0, 1), end=(-1, 0), layer="Edge.Cuts")],
    )
    box = plot_content_bbox(Board(footprints=[fp]), ["Edge.Cuts"], policy=POLICY)
    assert box is not None
    assert box.max_x == pytest.approx(11, abs=0.00001)
    assert box.max_y == pytest.approx(21, abs=0.00001)


def test_stroke_modes() -> None:
    board = Board(drawings=[PcbLine(start=(1, 2), end=(3, 4), stroke=Stroke(width=0.4))])
    center = plot_content_bbox(board, ["F.SilkS"], policy=POLICY)
    ink = plot_content_bbox(board, ["F.SilkS"], policy=PlotBoundsPolicy())
    assert center is not None and ink is not None
    assert center.min_x == 1
    assert ink.min_x == 0.8
    assert ink.max_y == 4.2


def test_hidden_values_and_resolved_reference() -> None:
    fp = Footprint(
        fp_id="text",
        properties=[
            Property(name="Reference", value="R123", visible=False),
            Property(name="Value", value="far", position=(100, 100), layer="F.SilkS"),
        ],
        drawings=[PcbText(content="${REFERENCE}", position=(0, 0))],
    )
    box = plot_content_bbox(
        Board(footprints=[fp]), ["F.SilkS"], policy=PlotBoundsPolicy(hide_footprint_values=True)
    )
    assert box is not None
    assert box.max_x < 10
    assert box.width > 1


def test_unsupported_font_and_unresolved_text_raise() -> None:
    for text in [
        PcbText(content="${MISSING}"),
        PcbText(content="T", attributes=TextAttributes(font_face="Arial")),
    ]:
        with pytest.raises(UnsupportedPlotContent):
            plot_content_bbox(Board(drawings=[text]), ["F.SilkS"], policy=POLICY)


def test_padstack_uses_only_selected_layer() -> None:
    pad = Pad(
        number="1",
        pad_type=PadType.thru_hole,
        drill=DrillInfo(width=0.5),
        shape=PadShape.rect,
        size=(2, 2),
        layers=["*.Cu"],
        padstack=Padstack(
            mode=PadstackMode.front_inner_back,
            layers=[
                PadstackLayer(layer_name="Inner", size=(6, 6)),
                PadstackLayer(layer_name="B.Cu", size=(4, 4)),
            ],
        ),
    )
    board = Board(
        footprints=[Footprint(pads=[pad])],
        layers=[
            BoardLayer(ordinal=0, canonical_name="F.Cu"),
            BoardLayer(ordinal=2, canonical_name="B.Cu"),
        ],
    )
    front = plot_content_bbox(board, ["F.Cu"], policy=POLICY)
    back = plot_content_bbox(board, ["B.Cu"], policy=POLICY)
    assert front is not None and back is not None
    assert front.width == 2
    assert back.width == 4


def test_zone_fills_exclude_other_layers_and_unfilled_outline() -> None:
    zone = Zone(
        layers=["F.Cu", "B.Cu"],
        polygons=[ZoneOutlinePolygon(pts=[(-100, -100), (100, -100), (0, 100)])],
        filled_polygons=[
            ZoneFilledPolygon(layer="F.Cu", pts=[(1, 1), (3, 1), (1, 3)]),
            ZoneFilledPolygon(layer="B.Cu", pts=[(10, 10), (30, 10), (10, 30)]),
        ],
    )
    board = Board(zones=[zone])
    box = plot_content_bbox(board, ["F.Cu"], policy=POLICY)
    assert box is not None
    assert (box.min_x, box.max_x, box.min_y, box.max_y) == (1, 3, 1, 3)
    zone.filled_polygons.clear()
    assert plot_content_bbox(board, ["F.Cu"], policy=POLICY) is None


def test_hidden_named_text_does_not_require_font() -> None:
    text = PcbText(
        content="hidden", visible=False, attributes=TextAttributes(font_face="Unavailable")
    )
    assert plot_content_bbox(Board(drawings=[text]), ["F.SilkS"], policy=POLICY) is None


def test_font_curve_bounds_use_extrema_not_control_points() -> None:
    pytest.importorskip("fontTools")
    from eedesigncore.common.bbox import Extent
    from eedesigncore.render.bounds import _path_bounds
    from eedesigncore.render.geometry import SvgShape

    extent = Extent()
    _path_bounds(extent, SvgShape("path", (("d", "M0,0 C0,10 10,10 10,0"),)), 0)
    box = extent.to_bbox()
    assert box is not None
    assert box.max_y == 7.5


def test_text_box_reuses_wrapping_and_preserves_source() -> None:
    from eedesigncore.common.pcb_family.shapes import PcbTextBox

    text = PcbTextBox(
        content="R12 R34 R56",
        start=(10, 20),
        end=(14, 30),
        layer="F.Fab",
        attributes=TextAttributes(font_size=(1, 1)),
    )
    before = text.model_dump()
    box = plot_content_bbox(Board(drawings=[text]), ["F.Fab"], policy=POLICY)
    assert box is not None
    assert box.height > 1.5
    assert text.model_dump() == before


def test_drill_marks_on_unselected_pads_and_disable_option() -> None:
    pad = Pad(
        number="1",
        pad_type=PadType.thru_hole,
        shape=PadShape.circle,
        size=(2, 2),
        layers=["*.Cu"],
        drill=DrillInfo(width=4),
        position=(20, 30),
    )
    board = Board(footprints=[Footprint(pads=[pad])])
    box = plot_content_bbox(board, ["F.Fab"], policy=POLICY)
    assert box is not None
    assert box.width == pytest.approx(2, abs=0.00001)
    assert box.center == pytest.approx((20, 30), abs=0.00001)
    assert (
        plot_content_bbox(board, ["F.Fab"], policy=PlotBoundsPolicy(actual_drill_marks=False))
        is None
    )


def test_rotated_slot_drill_bounds() -> None:
    pad = Pad(
        number="1",
        pad_type=PadType.thru_hole,
        shape=PadShape.rect,
        size=(6, 6),
        layers=["*.Cu"],
        drill=DrillInfo(width=4, height=2, oval=True),
        angle=45,
    )
    box = plot_content_bbox(Board(footprints=[Footprint(pads=[pad])]), ["F.Fab"], policy=POLICY)
    assert box is not None
    assert box.width == pytest.approx(2 + math.sqrt(2), abs=0.00001)
    assert box.height == pytest.approx(box.width)


def test_font_requirements_filter_visibility_and_preserve_style() -> None:
    from eedesigncore.render.bounds import PlotFontRequest, plot_font_requirements

    board = Board(
        drawings=[
            PcbText(content="a", attributes=TextAttributes(font_face="Arial")),
            PcbText(content="b", attributes=TextAttributes(font_face="Arial", bold=True)),
            PcbText(content="c", visible=False, attributes=TextAttributes(font_face="Absent")),
        ],
        footprints=[
            Footprint(
                properties=[
                    Property(
                        name="Value",
                        value="hide",
                        layer="F.SilkS",
                        attributes=TextAttributes(font_face="Tahoma"),
                    )
                ]
            )
        ],
    )
    requests = plot_font_requirements(
        board, ["F.SilkS"], policy=PlotBoundsPolicy(hide_footprint_values=True)
    )
    assert requests == (PlotFontRequest("Arial"), PlotFontRequest("Arial", bold=True))
