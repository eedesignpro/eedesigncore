"""Drawing-sheet edits preserve templates and resolve separate page graphics."""

import base64
import binascii
import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest

from eedesigncore import (
    DrawingSheet,
    DrawingSheetCoordinate,
    DrawingSheetItem,
    Paper,
    TextResolutionContext,
    TitleBlock,
    drawing_sheet_graphics,
    render_drawing_sheet_svg,
)
from eedesigncore.exceptions import KicadParseError


def point(x=0, y=0, anchor="ltcorner"):
    return DrawingSheetCoordinate(x=x, y=y, anchor=anchor)


def image_data():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def test_all_items_edit_save_reload(tmp_path):
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(
                kind="line", position=point(1, 2), end=point(10, 20), repeat=2, increment=(3, 4)
            ),
            DrawingSheetItem(
                kind="rect", position=point(), end=point(20, 20), page_option="notonpage1"
            ),
            DrawingSheetItem(
                kind="tbtext",
                text="${TITLE} @{2+3}",
                bold=True,
                italic=True,
                text_size=(2, 3),
                line_width=0.2,
                font_face="KiCad Font",
                justify_h="center",
                justify_v="bottom",
                max_length=20,
                max_height=5,
                repeat=2,
                increment_label=2,
            ),
            DrawingSheetItem(
                kind="polygon",
                position=point(5, 6),
                rotation=30,
                polygons=[[(0, 0), (3, 0), (0, 3)]],
            ),
            DrawingSheetItem(
                kind="bitmap", position=point(40, 40), image_data=image_data(), image_scale=2
            ),
        ]
    )
    text = sheet.to_string()
    loaded = DrawingSheet.from_string(text)
    assert loaded.model_dump() == sheet.model_dump()
    loaded.items[2].text = "changed ${TITLE}"
    path = tmp_path / "drawing.kicad_wks"
    loaded.to_file(path)
    assert DrawingSheet.from_file(path).items[2].text == "changed ${TITLE}"
    assert "${TITLE}" in path.read_text(encoding="utf-8")


def test_unchanged_crlf_and_comments(tmp_path):
    raw = (
        b"# retained comment\r\n"
        b'(page_layout (setup (textsize 1.5 1.5)) (tbtext "%T" (pos 1 2)))\r\n'
    )
    path = tmp_path / "source.kicad_wks"
    path.write_bytes(raw)
    sheet = DrawingSheet.from_file(path)
    target = tmp_path / "copy.kicad_wks"
    sheet.to_file(target)
    assert target.read_bytes() == raw


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("ltcorner", (12, 13)),
        ("rtcorner", (285.0022, 13)),
        ("lbcorner", (12, 197.0072)),
        ("rbcorner", (285.0022, 197.0072)),
    ],
)
def test_corner_anchors(anchor, expected):
    sheet = DrawingSheet(items=[DrawingSheetItem(kind="line", position=point(2, 3, anchor))])
    assert drawing_sheet_graphics(sheet).schematic.graphic_shapes[0].pts[0] == pytest.approx(
        expected
    )


def test_repetition_page_filter_and_source_preservation():
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(
                kind="tbtext", text="A", position=point(1, 1), repeat=100, increment=(50, 0)
            ),
            DrawingSheetItem(kind="tbtext", text="other", page_option="notonpage1"),
        ]
    )
    before = sheet.model_dump()
    first = drawing_sheet_graphics(sheet)
    assert [text.content for text in first.schematic.texts] == list("ABCDEF")
    later = drawing_sheet_graphics(sheet, first_page=False)
    assert later.schematic.texts[-1].content == "other"
    assert sheet.model_dump() == before


def test_context_raw_results_and_legacy_variables():
    sheet = DrawingSheet(
        items=[DrawingSheetItem(kind="tbtext", text="%T ${PAPER} ${#}/${##} @{2+3} ${MISSING}")]
    )
    result = drawing_sheet_graphics(
        sheet,
        title_block=TitleBlock(title="Power"),
        text_context=TextResolutionContext(page="3", page_count=4),
    )
    text = next(iter(result.texts.values()))
    assert text.raw == sheet.items[0].text
    assert text.resolved == "Power A4 3/4 5 ${MISSING}"
    assert not result.complete


def test_prepared_graphics_svg_and_embedded_image():
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(kind="bitmap", image_data=image_data()),
            DrawingSheetItem(kind="tbtext", text="<safe & text>"),
        ]
    )
    graphics = drawing_sheet_graphics(sheet, paper=Paper(paper_type="User", width=100, height=80))
    before = sheet.model_dump()
    svg = render_drawing_sheet_svg(graphics)
    xml = ET.fromstring(svg)
    assert xml.attrib["viewBox"] == "0.0000 0.0000 100.0000 80.0000"
    assert len(list(xml.iter("{http://www.w3.org/2000/svg}image"))) == 1
    assert sheet.model_dump() == before
    assert render_drawing_sheet_svg(graphics) == svg


@pytest.mark.parametrize(
    "bad",
    [
        "(kicad_wks (version 99999999))",
        "(kicad_wks (version 20231118) (unknown 1))",
        "(kicad_wks (version 20231118) (line (start 1)))",
        '(kicad_wks (version 20231118) (tbtext "x" (font rubbish)))',
        "(kicad_wks (version 20231118) (rect (end 1 2 badcorner)))",
    ],
)
def test_invalid_input_is_not_silently_discarded(bad):
    with pytest.raises(KicadParseError):
        DrawingSheet.from_string(bad)


def test_limits_and_invalid_page():
    sheet = DrawingSheet(items=[DrawingSheetItem(kind="line", repeat=100)])
    with pytest.raises(ValueError, match="limit"):
        drawing_sheet_graphics(sheet, max_items=10)
    with pytest.raises(ValueError, match="margins"):
        drawing_sheet_graphics(sheet, paper=Paper(paper_type="User", width=1, height=1))
    with pytest.raises(ValueError, match="local"):
        DrawingSheet.from_file("https://example.invalid/x.kicad_wks")


def test_constraints_and_font_control():
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(
                kind="tbtext",
                text="Long drawing title",
                position=point(20, 20),
                max_length=5,
                text_size=(3, 3),
            )
        ]
    )
    prepared = drawing_sheet_graphics(sheet)
    assert prepared.schematic.texts[0].attributes.font_size[0] < 3

    class Font:
        def __init__(self):
            self.calls = []

        def text_strokes(self, text, *args, **kwargs):
            self.calls.append(text)
            return []

    font = Font()
    render_drawing_sheet_svg(prepared, font=font)
    assert font.calls == ["Long drawing title"]
    with pytest.raises(ValueError, match="text_box"):
        drawing_sheet_graphics(sheet, font=font)


def test_native_geometry_and_text_fixture():
    root = Path(__file__).parents[1] / "fixtures" / "drawing_sheet"
    expected = json.loads((root / "native-results.json").read_text())
    sheet = DrawingSheet.from_file(root / "geometry.kicad_wks")
    xml = ET.fromstring(render_drawing_sheet_svg(sheet))
    vertices = []
    for element in xml.iter():
        kind = element.tag.rsplit("}", 1)[-1]
        if kind == "path":
            values = list(map(float, re.findall(r"[-+]?\d+(?:\.\d+)?", element.attrib["d"])))
            points = list(zip(values[::2], values[1::2], strict=True))
        elif kind == "rect":
            x, y, width, height = (
                float(element.attrib[key]) for key in ("x", "y", "width", "height")
            )
            points = [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]
        else:
            continue
        vertices.append(sorted(set((round(x, 4), round(y, 4)) for x, y in points)))
    assert json.loads(json.dumps(sorted(vertices))) == expected["geometry_vertices"]
    sheet = DrawingSheet.from_file(root / "text.kicad_wks")
    prepared = drawing_sheet_graphics(sheet, title_block=TitleBlock(title="Probe"))
    assert [text.content for text in prepared.schematic.texts] == expected["texts"]


def test_legacy_overbar_and_empty_filename():
    sheet = DrawingSheet.from_string('(page_layout (tbtext "~RESET~ %F" (pos 1 1)))')
    prepared = drawing_sheet_graphics(sheet)
    assert prepared.schematic.texts[0].content == "~{RESET} "
    assert prepared.complete
    sheet.items[0].name = "edited"
    assert DrawingSheet.from_string(sheet.to_string()).items[0].text == "~{RESET} ${FILENAME}"


def test_failed_save_preserves_destination_and_bom(tmp_path):
    path = tmp_path / "sheet.kicad_wks"
    original = b"\xef\xbb\xbf(page_layout)\r\n"
    path.write_bytes(original)
    sheet = DrawingSheet.from_file(path)
    sheet.to_file(path)
    assert path.read_bytes() == original
    sheet.setup.line_width = -1
    with pytest.raises(ValueError):
        sheet.to_file(path)
    assert path.read_bytes() == original


def test_literal_backslash_and_repeat_resolution_diagnostic():
    sheet = DrawingSheet(items=[DrawingSheetItem(kind="tbtext", text=r"a\\nb\nc")])
    assert drawing_sheet_graphics(sheet).schematic.texts[0].content == "a\\nb\nc"
    sheet.items[0].text = "${TITLE}"
    sheet.items[0].repeat = 2
    prepared = drawing_sheet_graphics(sheet, title_block=TitleBlock(title="Probe"))
    assert prepared.schematic.texts[0].content == "Probe"
    assert prepared.schematic.texts[1].content == "${TITLE~"
    assert not prepared.complete


def test_contour_limit_and_invalid_image():
    sheet = DrawingSheet(
        items=[DrawingSheetItem(kind="polygon", polygons=[[(0, 0), (1, 0), (0, 1)]] * 5)]
    )
    with pytest.raises(ValueError, match="limit"):
        drawing_sheet_graphics(sheet, max_items=4)
    with pytest.raises(KicadParseError):
        DrawingSheet.from_string('(page_layout (bitmap (data "bad!")))')


def test_template_paint_order_and_minimum_text_width():
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(
                kind="tbtext", text="I", position=point(20, 20), text_size=(0.1, 0.1)
            ),
            DrawingSheetItem(
                kind="polygon", position=point(20, 20), polygons=[[(0, 0), (1, 0), (0, 1)]]
            ),
        ]
    )
    xml = ET.fromstring(render_drawing_sheet_svg(sheet))
    paths = list(xml.iter("{http://www.w3.org/2000/svg}path"))
    assert paths[-1].attrib["fill"] == "#000000"
    assert all(float(path.attrib["stroke-width"]) >= 0.1524 for path in paths)


def test_polygon_repeat_uses_native_integer_bounds():
    sheet = DrawingSheet(
        items=[
            DrawingSheetItem(
                kind="polygon",
                position=point(276, 0),
                repeat=2,
                increment=(0.5, 0),
                polygons=[[(0, 0), (0.9, 0), (0, 0.9)]],
            )
        ]
    )
    assert len(drawing_sheet_graphics(sheet).schematic.graphic_shapes) == 2


def test_documented_drawing_sheet_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    DrawingSheet(items=[DrawingSheetItem(kind="tbtext", name="title", text="Old")]).to_file(
        "company.kicad_wks"
    )
    document = Path(__file__).resolve().parents[2] / "docs/drawing-sheets.md"
    namespace = {}
    for code in re.findall(r"```python\n(.*?)```", document.read_text(encoding="utf-8"), re.S):
        exec(compile(code, str(document), "exec"), namespace)
    ET.fromstring(namespace["svg"])
