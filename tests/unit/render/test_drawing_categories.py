"""Remaining drawing categories, checked through public in-memory exports."""

from __future__ import annotations

import base64
import binascii
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    Schematic,
    SchematicStyle,
    SchTextBox,
    SymbolStyle,
    TextAttributes,
    ViewBox,
)
from eedesigncore.common.models import Fill, FillType, Stroke
from eedesigncore.common.pcb_family.board_items import Barcode, BarcodeKind, ReferenceImage
from eedesigncore.common.pcb_family.shapes import PcbCircle, PcbPoly, PcbRect, PcbText
from eedesigncore.common.sch_family.shapes import SchPolyline
from eedesigncore.render import LayerStyle, render_board_svg, render_schematic_svg
from eedesigncore.render.compare import compare_svg, read_primitives
from eedesigncore.render.images import image_dimensions
from eedesigncore.sch.models import Image, RuleArea

FIXTURES = Path(__file__).parents[2] / "fixtures/svg_drawing_categories"
WINDOW = ViewBox(0, 0, 297, 210)
STYLE = LayerStyle({}, default_color="#000000")


def elements(svg: str) -> list[ET.Element]:
    return list(ET.fromstring(svg).iter())[2:]


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
        + chunk(b"pHYs", struct.pack(">IIB", 11811, 11811, 1))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\xff\x00"))
        + chunk(b"IEND", b"")
    )


def test_saved_schematic_matches_kicad_geometry_without_mutation():
    schematic = Schematic.from_file(FIXTURES / "schematic.kicad_sch")
    before = schematic.model_dump()
    ours = render_schematic_svg(schematic, view_box=WINDOW)
    comparison = compare_svg(ours, (FIXTURES / "schematic.svg").read_text(), tolerance=0.001)
    assert comparison.agrees, comparison.summary()
    assert comparison.matched == 716
    assert ours == render_schematic_svg(schematic, view_box=WINDOW)
    assert schematic.model_dump() == before


def test_do_not_populate_mark_geometry_matches_kicad():
    schematic = Schematic.from_file(FIXTURES / "do-not-populate.kicad_sch")
    ours = render_schematic_svg(
        schematic, view_box=WINDOW, style=SchematicStyle(symbol=SymbolStyle(clock_style="plot"))
    )
    comparison = compare_svg(ours, (FIXTURES / "do-not-populate.svg").read_text(), tolerance=0.001)
    assert not comparison.only_in_left
    assert len(comparison.only_in_right) == 2
    primitives = read_primitives(ours, tolerance=0.001)
    assert all(tuple(item) in primitives for item in comparison.only_in_right)
    assert any(
        e.get("stroke-width") == ".4572" or e.get("stroke-width") == "0.4572"
        for e in elements(ours)
    )


@pytest.mark.parametrize("kind", list(BarcodeKind))
def test_each_barcode_draws_deterministically_and_without_mutation(kind):
    item = Barcode(
        text="ABC123", barcode_type=kind, size=(20, 10), position=(20, 20), show_text=False
    )
    board = Board(drawings=[item])
    before = board.model_dump()
    first = render_board_svg(board, ["F.Cu"], WINDOW, style=STYLE)["F.Cu"]
    assert first == render_board_svg(board, ["F.Cu"], WINDOW, style=STYLE)["F.Cu"]
    assert any(e.get("fill") == "#000000" for e in elements(first))
    assert board.model_dump() == before


def test_board_fixture_all_items_draw_and_xml_is_valid():
    board = Board.from_file(FIXTURES / "board.kicad_pcb")
    before = board.model_dump()
    skipped = []
    svg = render_board_svg(board, ["F.SilkS"], WINDOW, style=STYLE, on_skip=skipped.append)[
        "F.SilkS"
    ]
    assert elements(svg) and not skipped
    comparison = compare_svg(svg, (FIXTURES / "board.svg").read_text(), tolerance=0.002)
    assert not [p for p in comparison.only_in_left if len(p) > 2 and p[2] < 50]
    assert not [p for p in comparison.only_in_right if len(p) > 2 and p[2] < 50]
    assert board.model_dump() == before


def test_image_embeds_valid_data_and_physical_size():
    data = base64.b64encode(png()).decode()
    sheet = Schematic(images=[Image(data=data, position=(10, 20), scale=2)])
    image = next(
        e
        for e in elements(render_schematic_svg(sheet, view_box=WINDOW))
        if e.tag.endswith("image")
    )
    assert image.get("href") == "data:image/png;base64," + data
    assert float(image.get("width")) == pytest.approx(2 * 2 * 25.4 / 300, abs=0.0001)
    assert float(image.get("height")) == pytest.approx(2 * 25.4 / 300, abs=0.0001)
    board = Board(drawings=[ReferenceImage(data=data, position=(10, 20))])
    assert any(
        e.tag.endswith("image")
        for e in elements(render_board_svg(board, ["F.Cu"], WINDOW, style=STYLE)["F.Cu"])
    )


@pytest.mark.parametrize(
    "data",
    [b"", b'<svg onload="x"/>', b"\xff\xd8bad", png()[:-5], png()[:40] + b"bad" + png()[43:]],
)
def test_invalid_images_fail_explicitly(data):
    with pytest.raises(ValueError):
        image_dimensions(data)


@pytest.mark.parametrize("fill", [FillType.hatch, FillType.reverse_hatch, FillType.cross_hatch])
@pytest.mark.parametrize(
    "shape",
    [
        PcbRect(start=(1, 1), end=(5, 5)),
        PcbCircle(center=(3, 3), end=(5, 3)),
        PcbPoly(pts=[(1, 1), (5, 1), (3, 5), (1, 1)]),
    ],
)
def test_hatched_shapes_have_filled_polygons(fill, shape):
    item = shape.model_copy(update={"fill": Fill(type=fill), "stroke": Stroke(width=0.15)})
    svg = render_board_svg(Board(drawings=[item]), ["F.SilkS"], WINDOW, style=STYLE)["F.SilkS"]
    assert any(e.get("fill") == "#000000" for e in elements(svg))
    assert any(e.get("fill") == "none" for e in elements(svg))


def test_rule_area_and_multiline_text_box_draw():
    sheet = Schematic(
        rule_areas=[
            RuleArea(shape=SchPolyline(pts=[(1, 1), (8, 1), (8, 8)], stroke=Stroke(width=0.1)))
        ],
        text_boxes=[SchTextBox(content="A\nB", position=(10, 10), size=(8, 8))],
    )
    assert len(elements(render_schematic_svg(sheet, view_box=WINDOW))) > 4


def test_knockout_text_has_holes_and_no_stroke():
    board = Board(drawings=[PcbText(content="ABC", position=(10, 10), knockout=True)])
    svg = render_board_svg(board, ["F.SilkS"], WINDOW, style=STYLE)["F.SilkS"]
    shape = elements(svg)[0]
    assert shape.get("fill") == "#000000" and shape.get("stroke") == "none"
    assert shape.get("d").count("M") > 1


def test_hidden_pcb_text_has_no_paths():
    board = Board(drawings=[PcbText(content="SECRET", attributes=TextAttributes(visible=False))])
    assert not elements(render_board_svg(board, ["F.SilkS"], WINDOW, style=STYLE)["F.SilkS"])


def test_multiline_fields_library_text_and_stacked_numbers_match_kicad():
    schematic = Schematic.from_file(FIXTURES / "multiline.kicad_sch")
    comparison = compare_svg(
        render_schematic_svg(
            schematic,
            view_box=WINDOW,
            style=SchematicStyle(symbol=SymbolStyle(clock_style="plot")),
        ),
        (FIXTURES / "multiline.svg").read_text(),
        tolerance=0.001,
    )
    assert comparison.agrees, comparison.summary()
    assert comparison.matched == 2191


def test_multiline_labels_match_kicad():
    schematic = Schematic.from_file(FIXTURES / "labels.kicad_sch")
    comparison = compare_svg(
        render_schematic_svg(schematic, view_box=WINDOW),
        (FIXTURES / "labels.svg").read_text(),
        tolerance=0.001,
    )
    assert comparison.agrees, comparison.summary()
    assert comparison.matched == 704


def test_exports_do_not_read_files_or_start_processes(monkeypatch):
    import builtins
    import subprocess

    board = Board.from_file(FIXTURES / "board.kicad_pcb")
    schematic = Schematic.from_file(FIXTURES / "schematic.kicad_sch")

    def blocked(*args, **kwargs):
        raise AssertionError("Export accessed a file or started a process.")

    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    assert render_board_svg(board, ["F.SilkS"], WINDOW, style=STYLE)
    assert render_schematic_svg(schematic, view_box=WINDOW)


def test_markup_nesting_limit_is_explicit():
    from eedesigncore.common.font import default_font

    with pytest.raises(ValueError, match="nesting"):
        default_font().advance("~{" * 258 + "A" + "}" * 258, (1000000, 1000000))


def test_all_barcode_areas_match_saved_kicad_export():
    import re

    import pyclipper

    def filled_paths(svg):
        result = []

        def walk(node, style):
            style = (
                style
                | dict(
                    pair.split(":", 1)
                    for pair in node.attrib.get("style", "").split(";")
                    if ":" in pair
                )
                | node.attrib
            )
            if node.tag.endswith("path") and style.get("fill", "none") not in (
                "none",
                "transparent",
            ):
                for segment in re.split("[Mm]", node.get("d", ""))[1:]:
                    nums = [
                        float(v)
                        for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", segment)
                    ]
                    points = [
                        (round(nums[i] * 1e6), round(nums[i + 1] * 1e6))
                        for i in range(0, len(nums), 2)
                    ]
                    if len(points) > 2:
                        result.append(points)
            for child in node:
                walk(child, style)

        walk(ET.fromstring(svg), {})
        return result

    board = Board.from_file(FIXTURES / "board.kicad_pcb")
    actual = filled_paths(render_board_svg(board, ["F.SilkS"], WINDOW, style=STYLE)["F.SilkS"])
    expected = filled_paths((FIXTURES / "board.svg").read_text())
    for index in range(5):
        left, right = 18 + index * 35, 42 + index * 35

        def select(paths, left=left, right=right):
            return [
                p
                for p in paths
                if left <= sum(x for x, y in p) / len(p) / 1e6 <= right
                and 55 <= sum(y for x, y in p) / len(p) / 1e6 <= 75
            ]

        a, b = select(actual), select(expected)
        assert a and b
        engine = pyclipper.Pyclipper()
        engine.AddPaths(a, pyclipper.PT_SUBJECT, True)
        engine.AddPaths(b, pyclipper.PT_CLIP, True)
        difference = engine.Execute(pyclipper.CT_XOR, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        assert abs(sum(pyclipper.Area(p) for p in difference)) == 0


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_footprint_text_angle_is_already_board_absolute(angle):
    from eedesigncore import Footprint, FootprintPlacement
    from eedesigncore.render.traversal import footprint_point_map

    text = PcbText(
        content="ABC", position=(1, 2), angle=angle, attributes=TextAttributes(font_size=(1, 1))
    )
    footprint = Footprint(
        fp_id="Test", at=FootprintPlacement(position=(100, 70), angle=90), drawings=[text]
    )
    absolute = text.model_copy(update={"position": footprint_point_map(footprint)(text.position)})
    nested = render_board_svg(Board(footprints=[footprint]), ["F.SilkS"], WINDOW, style=STYLE)[
        "F.SilkS"
    ]
    flat = render_board_svg(Board(drawings=[absolute]), ["F.SilkS"], WINDOW, style=STYLE)[
        "F.SilkS"
    ]
    assert nested == flat


def test_board_coordinate_children_are_not_transformed_twice():
    from eedesigncore import Footprint, FootprintPlacement

    image = ReferenceImage(data=base64.b64encode(png()).decode(), position=(20, 30))
    barcode = Barcode(text="ABC123", size=(10, 10), position=(20, 30), show_text=False)
    footprint = Footprint(
        fp_id="Test",
        at=FootprintPlacement(position=(100, 70), angle=90),
        drawings=[image, barcode],
    )
    nested = render_board_svg(Board(footprints=[footprint]), ["F.Cu"], WINDOW, style=STYLE)["F.Cu"]
    flat = render_board_svg(Board(drawings=[image, barcode]), ["F.Cu"], WINDOW, style=STYLE)[
        "F.Cu"
    ]
    assert nested == flat
