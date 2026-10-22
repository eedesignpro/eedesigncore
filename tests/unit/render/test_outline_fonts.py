"""Selected outline fonts emit filled paths for schematic and symbol SVG."""

import xml.etree.ElementTree as ET

import pytest

from eedesigncore import (
    AssemblyWorkspace,
    NetbomAssembly,
    OutlineFont,
    Paper,
    PlacedSymbol,
    Schematic,
    SchText,
    SchTextBox,
    Symbol,
    SymbolUnit,
    SymText,
    TextAttributes,
    ViewBox,
    load_font,
    prepare_schematic_view,
    render_schematic_svg,
    render_symbol_svg,
)
from eedesigncore.common.font import default_font
from eedesigncore.render.fonts import _named_font
from eedesigncore.render.text_boxes import line_positions, wrapped_lines


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    paths = []
    for family, advance, bold in [
        ("Fixture Narrow", 500, False),
        ("Fixture Wide", 900, False),
        ("Fixture Wide", 900, True),
    ]:
        builder = FontBuilder(1000, isTTF=True)
        builder.setupGlyphOrder([".notdef", "space", "A", "bullet", "O"])
        builder.setupCharacterMap({32: "space", 65: "A", 79: "O", 8226: "bullet"})
        glyphs = {}
        for name in [".notdef", "space", "A", "bullet", "O"]:
            pen = TTGlyphPen(None)
            if name != "space":
                pen.moveTo((50, 0))
                pen.lineTo((advance - 50, 0))
                if name == "O":
                    pen.lineTo((advance - 50, 700))
                else:
                    pen.qCurveTo((advance, 350), (advance - 50, 700))
                pen.lineTo((50, 700))
                pen.closePath()
                if name == "O":
                    pen.moveTo((150, 200))
                    pen.lineTo((150, 500))
                    pen.lineTo((advance - 150, 500))
                    pen.lineTo((advance - 150, 200))
                    pen.closePath()
            glyphs[name] = pen.glyph()
        builder.setupGlyf(glyphs)
        builder.setupHorizontalMetrics({name: (advance, 0) for name in glyphs})
        builder.setupHorizontalHeader(ascent=800, descent=-200)
        builder.setupNameTable(
            {
                "familyName": family,
                "styleName": "Bold" if bold else "Regular",
                "uniqueFontIdentifier": family + str(bold),
                "fullName": family + (" Bold" if bold else ""),
                "psName": family.replace(" ", "") + str(bold),
            }
        )
        builder.setupOS2(
            sTypoAscender=800,
            sTypoDescender=-200,
            usWinAscent=800,
            usWinDescent=200,
            fsSelection=32 if bold else 64,
        )
        builder.setupPost()
        builder.setupMaxp()
        builder.font["head"].macStyle = 1 if bold else 0
        path = tmp_path / (family + str(bold) + ".ttf")
        builder.save(path)
        paths.append(path)
    _named_font.cache_clear()
    monkeypatch.setattr("eedesigncore.render.fonts._font_paths", lambda: paths)
    yield paths
    _named_font.cache_clear()


def test_font_file_and_installed_name(fonts):
    direct = load_font(fonts[0])
    named = load_font("Fixture Narrow")
    assert isinstance(direct, OutlineFont)
    assert direct.advance("AA", (1000000, 1000000)) == named.advance("AA", (1000000, 1000000))
    assert load_font("Fixture Wide").advance("AA", (1000000, 1000000)) > named.advance(
        "AA", (1000000, 1000000)
    )
    assert load_font("Fixture Wide").for_style(True).path == fonts[2]
    with pytest.raises(ValueError, match="not found"):
        load_font("No such installed font")
    assert load_font("KiCad Font") is default_font()


@pytest.mark.parametrize("multiline", [False, True])
def test_public_board_named_font_uses_filled_paths_and_shared_bounds(fonts, multiline):
    from eedesigncore import Board, PcbText
    from eedesigncore.render import LayerStyle, render_board_svg
    from eedesigncore.render.board_text import pcb_text_shapes
    from eedesigncore.render.bounds import PlotBoundsPolicy, PlotTextContext, plot_content_bbox
    from eedesigncore.render.traversal import identity_map

    face = load_font(fonts[0])
    attrs = TextAttributes(font_face="Fixture Narrow", font_size=(2, 2), mirror=True)
    item = PcbText(
        content="A\nA" if multiline else "A",
        position=(10, 12),
        angle=37,
        layer="F.Cu",
        attributes=attrs,
    )
    board = Board(drawings=[item])
    context = PlotTextContext(fonts={"Fixture Narrow": face})
    pages = render_board_svg(
        board,
        ["F.Cu"],
        ViewBox(0, 0, 30, 30),
        style=LayerStyle(colors={"F.Cu": "#000000", "F.Fab": "#000000"}),
        plot_text_context=context,
    )
    paths = list(ET.fromstring(pages["F.Cu"]).iter("{http://www.w3.org/2000/svg}path"))
    assert len(paths) == (2 if multiline else 1)
    assert all(path.attrib.get("fill-rule") == "nonzero" for path in paths)
    assert all(path.attrib.get("stroke") == "none" for path in paths)
    expected = pcb_text_shapes(item, identity_map, 4, face)
    assert [p.attrib["d"] for p in paths] == [dict(p.attrs)["d"] for p in expected]
    assert plot_content_bbox(board, ["F.Cu"], policy=PlotBoundsPolicy(), text_context=context)


def test_outline_font_affine_mapping_preserves_filled_contours(fonts):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.svgLib.path import parse_path

    from eedesigncore.render.geometry import text_shapes

    face = load_font(fonts[0])
    attrs = TextAttributes(font_face="Fixture Narrow")
    first = face.svg_shape("A", (0, 0), 0, attrs, 6)
    second = text_shapes("A", (0, 0), 0, attrs, lambda p: (10 - p[1], 20 + p[0]), 6, face)[0]
    pens = [BoundsPen(None), BoundsPen(None)]
    for shape, pen in zip([first, second], pens, strict=True):
        parse_path(dict(shape.attrs)["d"], pen)
    x0, y0, x1, y1 = pens[0].bounds
    assert pens[1].bounds == pytest.approx((10 - y1, 20 + x0, 10 - y0, 20 + x1), abs=0.000002)
    assert second.filled


@pytest.mark.parametrize("angle", [0, 37, 90])
@pytest.mark.parametrize("mirror", [False, True])
def test_outline_knockout_preserves_glyph_hole_and_transform(fonts, angle, mirror):
    import math

    import pyclipper

    from eedesigncore import PcbText
    from eedesigncore.render.board_text import pcb_text_shapes
    from eedesigncore.render.filled_geometry import filled_polygons
    from eedesigncore.render.traversal import identity_map

    item = PcbText(
        content="O",
        knockout=True,
        angle=angle,
        position=(10, 20),
        attributes=TextAttributes(font_face="Fixture Narrow", font_size=(1, 1), mirror=mirror),
    )
    face = load_font(fonts[0])
    output = pcb_text_shapes(item, identity_map, 6, face)
    polygons = filled_polygons(output)
    assert all(shape.filled and dict(shape.attrs)["fill-rule"] == "nonzero" for shape in output)
    glyph = filled_polygons([face.svg_shape("O", (10, 20), 0, item.attributes, 6)])
    xs = [p[0] / 1e6 for poly in glyph for p in poly]
    ys = [p[1] / 1e6 for poly in glyph for p in poly]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    expected = (width + 2 / 9) * (height + 2 / 9) - 0.22 * 1.4**2
    assert sum(pyclipper.Area(poly) for poly in polygons) / 1e12 == pytest.approx(
        expected, abs=0.00002
    )

    def is_material(x, y):
        r = math.radians(angle)
        p = (
            round((10 + (x - 10) * math.cos(r) + (y - 20) * math.sin(r)) * 1e6),
            round((20 - (x - 10) * math.sin(r) + (y - 20) * math.cos(r)) * 1e6),
        )
        return (
            sum(
                (1 if pyclipper.Orientation(poly) else -1)
                for poly in polygons
                if pyclipper.PointInPolygon(p, poly)
            )
            != 0
        )

    assert is_material((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    assert not is_material(min(xs) + 0.03, (min(ys) + max(ys)) / 2)


@pytest.mark.parametrize("knockout", [False, True])
def test_public_named_text_box_uses_context_face(fonts, knockout):
    import pyclipper

    from eedesigncore import Board, PcbTextBox
    from eedesigncore.render import LayerStyle, render_board_svg
    from eedesigncore.render.bounds import PlotTextContext
    from eedesigncore.render.filled_geometry import filled_polygons
    from eedesigncore.render.geometry import SvgShape

    box = PcbTextBox(
        content="O",
        start=(0, 0),
        end=(10, 5),
        layer="F.Cu",
        knockout=knockout,
        border_enabled=False,
        attributes=TextAttributes(font_face="Fixture Narrow", font_size=(1, 1)),
    )
    context = PlotTextContext(fonts={"Fixture Narrow": load_font(fonts[0])})
    page = render_board_svg(
        Board(drawings=[box]),
        ["F.Cu"],
        ViewBox(0, 0, 10, 5),
        style=LayerStyle({}, default_color="#000000"),
        plot_text_context=context,
    )["F.Cu"]
    paths = list(ET.fromstring(page).iter("{http://www.w3.org/2000/svg}path"))
    polygons = filled_polygons(
        [SvgShape("path", tuple(p.attrib.items()), filled=True) for p in paths]
    )
    area = sum(pyclipper.Area(p) for p in polygons) / 1e12
    assert abs(area) == pytest.approx(50 - 0.4312 if knockout else 0.4312, abs=0.0001)


def test_public_table_uses_each_named_face(fonts):
    from eedesigncore import Board, Table, TableCell
    from eedesigncore.render import LayerStyle, render_board_svg
    from eedesigncore.render.bounds import (
        PlotBoundsPolicy,
        PlotTextContext,
        plot_font_requirements,
    )

    table = Table(
        layer="F.Cu",
        column_count=2,
        column_widths=[5, 5],
        row_heights=[5],
        cells=[
            TableCell(
                content="A",
                start=(i * 5, 0),
                end=(i * 5 + 5, 5),
                attributes=TextAttributes(font_face=family),
            )
            for i, family in enumerate(["Fixture Narrow", "Fixture Wide"])
        ],
    )
    board = Board(drawings=[table])
    context = PlotTextContext(
        fonts={"Fixture Narrow": load_font(fonts[0]), "Fixture Wide": load_font(fonts[1])}
    )
    assert len(plot_font_requirements(board, ["F.Cu"], policy=PlotBoundsPolicy())) == 2
    page = render_board_svg(
        board,
        ["F.Cu"],
        ViewBox(0, 0, 10, 5),
        style=LayerStyle({}, default_color="#000000"),
        plot_text_context=context,
    )["F.Cu"]
    paths = list(ET.fromstring(page).iter("{http://www.w3.org/2000/svg}path"))
    assert sum(path.attrib.get("fill-rule") == "nonzero" for path in paths) == 2


def test_filled_glyph_curve_subtraction_uses_bounded_approximation():
    import pyclipper

    from eedesigncore.render.filled_geometry import filled_polygons
    from eedesigncore.render.geometry import SvgShape

    shape = SvgShape("path", (("d", "M0,0 Q0.5,1 1,0 L0,0 Z"),), filled=True)
    polygons = filled_polygons([shape])
    assert abs(pyclipper.Area(polygons[0])) / 1e12 == pytest.approx(1 / 3, abs=0.001)
    assert 3 < len(polygons[0]) < 100
    with pytest.raises(ValueError, match="closed contours"):
        filled_polygons([SvgShape("path", (("d", "M0,0 L1,0"),), filled=True)])
    with pytest.raises(ValueError, match="finite and positive"):
        filled_polygons([shape], tolerance_mm=0)


@pytest.mark.parametrize("kind", ["schematic", "symbol"])
def test_every_export_uses_filled_selected_glyphs(fonts, kind):
    attrs = TextAttributes(font_size=(2, 2))
    box = ViewBox(0, 0, 30, 30)
    if kind == "schematic":
        document = Schematic(
            texts=[SchText(content="A \u2022", position=(10, 10), attributes=attrs)]
        )

        def render(font):
            return render_schematic_svg(document, view_box=box, font=font)
    else:
        document = Symbol(
            name="Example",
            units=[
                SymbolUnit(
                    name="Example_1_1",
                    items=[SymText(content="A \u2022", position=(10, -10), attributes=attrs)],
                )
            ],
        )

        def render(font):
            return render_symbol_svg(document, view_box=box, font=font)

    before = document.model_dump()
    first = render(load_font(fonts[0]))
    second = render(load_font(fonts[1]))
    assert first != second
    xml = ET.fromstring(first)
    paths = list(xml.iter("{http://www.w3.org/2000/svg}path"))
    assert paths and all(p.attrib["fill"] != "none" for p in paths)
    assert "Q" in paths[0].attrib["d"]
    assert not list(xml.iter("{http://www.w3.org/2000/svg}text"))
    assert document.model_dump() == before


def test_wrapping_and_markup_use_selected_font(fonts):
    attrs = TextAttributes(font_size=(1, 1))
    narrow = load_font(fonts[0])
    wide = load_font(fonts[1])
    assert len(wrapped_lines("A A A A", 3, attrs, 0, narrow)) < len(
        wrapped_lines("A A A A", 3, attrs, 0, wide)
    )
    assert wide.advance("A^{A}", (1000000, 1000000)) < wide.advance("AA", (1000000, 1000000))
    xml = ET.fromstring(
        render_schematic_svg(
            Schematic(texts=[SchText(content="~{A}", attributes=attrs)]),
            view_box=ViewBox(0, 0, 5, 5),
            font=wide,
        )
    )
    assert list(xml.iter("{http://www.w3.org/2000/svg}path"))
    assert wide.text_strokes("A", (1000000, 1000000), (0, 0), 10000)


def test_default_unicode_glyphs_and_lazy_reuse():
    font = default_font()
    for text in ("\u2022", "\u03a9", "\u00b5", "\u4e2d"):
        glyph = font.glyph(text)
        assert glyph != font.glyph("?")
        assert font.glyph(text) is glyph


def test_invalid_font_files(tmp_path):
    with pytest.raises(ValueError, match="local"):
        load_font("https://example.invalid/font.ttf")
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(ValueError, match="supported"):
        load_font(bad)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("workspace", [False, True])
def test_preparation_uses_selected_font_for_drawing_and_hitboxes(fonts, angle, workspace):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.svgLib.path import parse_path

    source = Schematic(
        uuid="sheet",
        paper=Paper(),
        lib_symbols=[
            Symbol(
                name="Example",
                units=[
                    SymbolUnit.for_symbol(
                        "Example", items=[SymText(content="AA", position=(0, 0))]
                    )
                ],
            )
        ],
        symbols=[PlacedSymbol(uuid="symbol", lib_id="Example", position=(20, 20), angle=angle)],
        embedded_fonts=True,
    )
    original = source.model_dump()

    def prepare(font):
        if workspace:
            owner = AssemblyWorkspace(NetbomAssembly(assembly_id="fonts"))
            owner.add_design("board", source)
            view = owner.schematic_view(font=font)
        else:
            view = prepare_schematic_view(source, document_id="fonts", font=font)
        return next(page for page in view.pages.values() if page.kind == "native_sheet")

    narrow, wide = (prepare(load_font(path)) for path in fonts[:2])
    assert narrow.svg != wide.svg
    for page in (narrow, wide):
        assert not any(issue.code == "drawing_unavailable" for issue in page.coverage.issues)
        item = next(item for item in page.items.values() if item.kind == "symbol")
        assert item.drawn and item.bounds is not None
        group = next(
            node
            for node in ET.fromstring(page.svg).iter()
            if node.attrib.get("data-item-id") == item.id
        )
        pen = BoundsPen(None)
        for node in group.iter("{http://www.w3.org/2000/svg}path"):
            parse_path(node.attrib["d"], pen)
        assert pen.bounds is not None
        left, top, right, bottom = pen.bounds
        assert item.bounds.x <= left <= right <= item.bounds.x + item.bounds.width
        assert item.bounds.y <= top <= bottom <= item.bounds.y + item.bounds.height
        message = next(
            issue.message
            for issue in page.coverage.issues
            if issue.code == "embedded_font_substitution"
        )
        assert "selected export font" in message
    first = next(item.bounds for item in narrow.items.values() if item.kind == "symbol")
    second = next(item.bounds for item in wide.items.values() if item.kind == "symbol")
    assert (second.width > first.width) if angle % 180 == 0 else (second.height > first.height)
    assert source.model_dump() == original


@pytest.mark.parametrize("ratio", [1.16, 1.8, 2.1])
def test_outline_interline_matches_native_integer_face_ratio(fonts, ratio):
    from fontTools.ttLib import TTFont

    with TTFont(fonts[0]) as face:
        face["hhea"].ascent = round(ratio * 1000) - 200
        face.save(fonts[0])
    font = load_font(fonts[0])
    assert font.line_pitch(1.27) == pytest.approx(1.27 * int(ratio) * 1.68)


def test_text_box_spacing_uses_selected_bold_face(fonts):
    from fontTools.ttLib import TTFont

    with TTFont(fonts[2]) as face:
        face["hhea"].ascent = 1900
        face.save(fonts[2])
    font = load_font("Fixture Wide")
    attributes = TextAttributes(font_size=(1.27, 1.27), bold=True)
    positions = line_positions(["A", "A"], (0, 0), 0, attributes, font)
    assert positions[1][1] - positions[0][1] == pytest.approx(1.27 * 2 * 1.68)


@pytest.mark.parametrize("vertical", ["top", "center", "bottom"])
def test_outline_text_box_ignores_only_final_empty_split(fonts, vertical):
    sheet = Schematic(
        text_boxes=[
            SchTextBox(
                content="A\n\nA",
                position=(5, 5),
                size=(20, 20),
                attributes=TextAttributes(justify_v=vertical),
            )
        ]
    )
    font = load_font(fonts[0])
    first = render_schematic_svg(sheet, view_box=ViewBox(0, 0, 30, 30), font=font)
    sheet.text_boxes[0].content += "\n"
    assert render_schematic_svg(sheet, view_box=ViewBox(0, 0, 30, 30), font=font) == first


def test_fitted_table_uses_explicit_outline_face(fonts):
    from eedesigncore import create_schematic_table, fit_schematic_table

    font = load_font("Fixture Wide")
    result = create_schematic_table(
        [["AAA", "A"], ["A", "AAA\nAAA"]],
        at=(10, 20),
        font=font,
        font_face="Fixture Wide",
        with_bounds=True,
    )
    assert result.measurement.complete
    assert all(cell.attributes.font_face == "Fixture Wide" for cell in result.item.cells)
    assert result.item.row_heights[1] > result.item.row_heights[0]
    for part in result.measurement.parts:
        if ":text" in part.name:
            cell = result.item.cells[int(part.name.split(":")[1])]
            assert part.bounds.min_x >= cell.position[0] + 0.29
            assert part.bounds.max_x <= cell.position[0] + cell.size[0] - 0.29
            assert part.bounds.min_y >= cell.position[1] + 0.29
            assert part.bounds.max_y <= cell.position[1] + cell.size[1] - 0.29
    assert fit_schematic_table(result.item, font=font) == result.item
