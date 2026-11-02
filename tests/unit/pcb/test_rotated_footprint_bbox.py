"""The extent of a footprint turned on the board, at quarter turns and between them.

Two frames meet in a placed footprint. A child POSITION is footprint-relative and
takes the footprint turn. A pad ANGLE is board-absolute on disk
(``pcb_io_kicad_sexpr.cpp:1686-1690``), so a pad corner turns by ``pad.angle``
alone. ``PAD::buildEffectiveShape`` (``pad.cpp:1143-1291``) and ``ShapePos``
(``pad.cpp:1558-1570``) build the copper that way.

Every expected value here comes from the LIBRARY footprint and the placement angle
alone, turned with the formula of ``RotatePoint`` (``trigo.cpp:229-264``) in
floating point. None of it reads the turned board model or the code under test.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from eedesigncore import Board, footprint_bbox, set_footprint_orientation
from eedesigncore.common.pcb_family.enums import PadShape, PadType
from eedesigncore.common.pcb_family.shapes import PcbArc, PcbRect
from eedesigncore.inspection import inspect_design
from eedesigncore.mod.models import (
    DrillInfo,
    Footprint,
    FootprintPlacement,
    Pad,
    PrimCircle,
    PrimRect,
)

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "mod" / "R_1206_3216Metric.kicad_mod"
ORIGIN = (100.0, 100.0)
ANGLES = (0.0, 38.0, 90.0, 135.0, 270.0)

Box = tuple[float, float, float, float]


def turn(point: tuple[float, float], degrees: float) -> tuple[float, float]:
    """``RotatePoint``: positive is counter-clockwise on screen, with Y down."""
    radians = math.radians(degrees)
    x, y = point
    return (
        y * math.sin(radians) + x * math.cos(radians),
        y * math.cos(radians) - x * math.sin(radians),
    )


def bounds(points: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def corners(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = start, end
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def on_board(point: tuple[float, float], degrees: float) -> tuple[float, float]:
    turned = turn(point, degrees)
    return (ORIGIN[0] + turned[0], ORIGIN[1] + turned[1])


def library() -> Footprint:
    return Footprint.from_file(FIXTURE)


def placed(degrees: float) -> Footprint:
    footprint = library()
    footprint.at = FootprintPlacement(position=ORIGIN, angle=0.0)
    set_footprint_orientation(footprint, degrees)
    return footprint


def measured(footprint: Footprint, layer: str) -> Box:
    box = footprint_bbox(footprint, layers={layer}, include_width=False)
    assert box is not None, f"nothing measured on {layer}"
    return (box.min_x, box.min_y, box.max_x, box.max_y)


def expected_rects(layer: str, degrees: float) -> Box:
    points = [
        on_board(corner, degrees)
        for item in library().drawings
        if isinstance(item, PcbRect) and item.layer == layer
        for corner in corners(item.start, item.end)
    ]
    assert points, f"the fixture has no rectangle on {layer}"
    return bounds(points)


def expected_pads(degrees: float) -> Box:
    points = []
    for pad in library().pads:
        centre = on_board(pad.position, degrees)
        half_x, half_y = pad.size[0] / 2.0, pad.size[1] / 2.0
        for corner in corners((-half_x, -half_y), (half_x, half_y)):
            offset = turn(corner, pad.angle + degrees)
            points.append((centre[0] + offset[0], centre[1] + offset[1]))
    return bounds(points)


@pytest.mark.parametrize("degrees", ANGLES)
def test_rectangle_counts_all_four_corners(degrees: float) -> None:
    """The courtyard is one ``fp_rect`` and nothing else, so it is the whole layer."""
    assert measured(placed(degrees), "F.CrtYd") == pytest.approx(
        expected_rects("F.CrtYd", degrees), abs=1e-5
    )


@pytest.mark.parametrize("degrees", ANGLES)
def test_pad_turns_once(degrees: float) -> None:
    assert measured(placed(degrees), "F.Cu") == pytest.approx(expected_pads(degrees), abs=1e-5)


def test_values_measured_by_hand_at_38_degrees() -> None:
    """The figures from the defect report, worked out by hand from the drawing."""
    footprint = placed(38.0)
    assert measured(footprint, "F.CrtYd") == pytest.approx(
        (97.508, 97.706, 102.492, 102.294), abs=5e-4
    )
    assert measured(footprint, "F.Cu") == pytest.approx(
        (97.866, 98.064, 102.134, 101.936), abs=5e-4
    )


def test_non_square_pad_at_90_degrees_is_not_turned_twice() -> None:
    """A 1.125 by 1.75 mm pad turned 90 degrees is 1.75 mm wide in X, not 1.125 mm."""
    assert measured(placed(90.0), "F.Cu") == pytest.approx(
        (99.125, 97.975, 100.875, 102.025), abs=1e-6
    )


def test_arc_extent_is_taken_after_the_turn() -> None:
    """A quarter arc turned 30 degrees reaches X = 1 between its stored points."""
    arc = PcbArc(
        start=(1.0, 0.0), mid=(math.sqrt(0.5), math.sqrt(0.5)), end=(0.0, 1.0), layer="F.SilkS"
    )
    footprint = Footprint(drawings=[arc], at=FootprintPlacement(position=(0.0, 0.0), angle=30.0))
    sweep = [
        turn((math.cos(math.radians(step / 100.0)), math.sin(math.radians(step / 100.0))), 30.0)
        for step in range(9001)
    ]
    assert measured(footprint, "F.SilkS") == pytest.approx(bounds(sweep), abs=1e-4)


def test_slot_hole_turns_by_the_pad_angle_only() -> None:
    """A 3 by 1 mm slot in a small pad: the hole sets the extent."""
    pad = Pad(
        number="1",
        pad_type=PadType.thru_hole,
        shape=PadShape.circle,
        size=(0.5, 0.5),
        drill=DrillInfo(oval=True, width=3.0, height=1.0),
        layers=["*.Cu"],
        position=(2.0, 0.0),
        angle=38.0,
    )
    footprint = Footprint(pads=[pad], at=FootprintPlacement(position=ORIGIN, angle=38.0))
    centre = on_board((2.0, 0.0), 38.0)
    reach = turn((1.0, 0.0), 38.0)
    half_x, half_y = abs(reach[0]) + 0.5, abs(reach[1]) + 0.5
    assert measured(footprint, "F.Cu") == pytest.approx(
        (centre[0] - half_x, centre[1] - half_y, centre[0] + half_x, centre[1] + half_y),
        abs=1e-5,
    )


def test_custom_pad_primitives_turn_once_about_the_shape_position() -> None:
    """Primitives turn by the board pad angle and move to ``ShapePos``, offset included."""
    footprint_angle, pad_angle = 90.0, 120.0
    pad = Pad(
        number="1",
        pad_type=PadType.smd,
        shape=PadShape.custom,
        size=(0.2, 0.2),
        layers=["F.Cu"],
        position=(2.0, 0.0),
        angle=pad_angle,
        drill=DrillInfo(offset=(0.5, 0.0)),
        primitives=[
            PrimRect(start=(0.0, 0.0), end=(2.0, 0.5)),
            PrimCircle(center=(-1.0, 0.0), end=(-0.5, 0.0), fill=True),
        ],
    )
    footprint = Footprint(
        pads=[pad], at=FootprintPlacement(position=ORIGIN, angle=footprint_angle)
    )

    anchor = on_board((2.0, 0.0), footprint_angle)
    offset = turn((0.5, 0.0), pad_angle)
    shape = (anchor[0] + offset[0], anchor[1] + offset[1])

    def at_shape(point: tuple[float, float]) -> tuple[float, float]:
        turned = turn(point, pad_angle)
        return (shape[0] + turned[0], shape[1] + turned[1])

    points = [at_shape(c) for c in corners((-0.1, -0.1), (0.1, 0.1))]
    points += [at_shape(c) for c in corners((0.0, 0.0), (2.0, 0.5))]
    circle = at_shape((-1.0, 0.0))
    points += [(circle[0] - 0.5, circle[1] - 0.5), (circle[0] + 0.5, circle[1] + 0.5)]
    assert measured(footprint, "F.Cu") == pytest.approx(bounds(points), abs=1e-5)


def test_region_search_finds_a_footprint_by_its_turned_courtyard() -> None:
    """Design inspection compares each item's box to the search region.

    At 38 degrees the true courtyard reaches Y = 97.706. Two corners alone stop at
    Y = 99.487, and the pads stop at Y = 98.064, so a region near Y = 97.8 finds
    the part only when every corner counts.
    """
    board = Board(footprints=[placed(38.0)])
    inspector = inspect_design(board)
    hit = inspector.execute(
        "geometry", {"kind": "footprint", "layer": "F.CrtYd", "bbox": [99.9, 97.75, 100.1, 97.85]}
    )
    assert hit["data"]["total"] == 1
    miss = inspector.execute(
        "geometry", {"kind": "footprint", "layer": "F.CrtYd", "bbox": [99.9, 97.5, 100.1, 97.65]}
    )
    assert miss["data"]["total"] == 0
