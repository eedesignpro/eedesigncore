"""Unit tests for the T3b lower-frequency schematic items.

Assertions trace to the A2 sch-root-grammar report Part 2 (2.6 rule_area, 2.8
table_cell, 2.9 table, 2.10 group, 2.11 image) and the cited
``sch_io_kicad_sexpr.cpp`` lines. Groups have NO 20260306 corpus fixture
(fixture gap), so ``group`` is covered by source-cited unit tests only.
"""

from eedesigncore.common.models import Fill, FillType, Stroke, StrokeType, TextAttributes
from eedesigncore.common.sch_family.shapes import SchPolyline
from eedesigncore.sch.loader import (
    _load_group,
    _load_image,
    _load_rule_area,
    _load_table,
    _load_table_cell,
)
from eedesigncore.sch.models import (
    Group,
    Image,
    RuleArea,
    SchTable,
    SchTableCell,
)
from eedesigncore.sch.writer import (
    _dump_group,
    _dump_image,
    _dump_rule_area,
    _dump_table,
    _dump_table_cell,
)
from eedesigncore.sexpr.parser import parse


def _node(text: str):
    return parse(text)


# --- rule_area (A2 2.6, :1351-1360) -----------------------------------------


def test_rule_area_bool_order_then_polyline() -> None:
    ra = RuleArea(
        exclude_from_sim=False,
        in_bom=True,
        on_board=True,
        dnp=False,
        shape=SchPolyline(
            pts=[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)],
            stroke=Stroke(),
            fill=Fill(type=FillType.none),
            id="ra-uuid",
        ),
    )
    out = _dump_rule_area(ra)
    assert out.startswith(
        "(rule_area (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (polyline"
    )
    assert '(uuid "ra-uuid")' in out


def test_rule_area_roundtrip() -> None:
    src = (
        "(rule_area (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)"
        " (polyline (pts (xy 0 0) (xy 5 0) (xy 5 5) (xy 0 5))"
        ' (stroke (width 0) (type default)) (fill (type none)) (uuid "r")))'
    )
    ra = _load_rule_area(_node(src))
    assert ra.in_bom is True
    assert len(ra.shape.pts) == 4
    assert ra.shape.id == "r"


# --- image (A2 2.11, :1042-1065) --------------------------------------------


def test_image_scale_guard_and_token() -> None:
    """image token (not bitmap); scale only emitted when != 1.0 (:1055)."""
    img = Image(position=(10.0, 20.0), scale=None, uuid="img1", data="QUJD")
    out = _dump_image(img)
    assert out.startswith('(image (at 10 20) (uuid "img1") (data')
    assert "(scale" not in out
    # explicit scale of 1.0 is suppressed
    img_one = Image(position=(0.0, 0.0), scale=1.0, uuid="i", data="QQ")
    assert "(scale" not in _dump_image(img_one)
    # non-unity scale is emitted via {:g}
    img_s = Image(position=(0.0, 0.0), scale=2.5, uuid="i", data="QQ")
    assert "(scale 2.5)" in _dump_image(img_s)


def test_image_data_chunks_at_76_chars() -> None:
    data = "A" * 200
    out = _dump_image(Image(position=(0.0, 0.0), uuid="i", data=data))
    # FormatStreamData wraps base64 into 76-char quoted chunks (kicad_io_utils.cpp:63)
    assert '"' + "A" * 76 + '"' in out
    # 200 chars -> 76 + 76 + 48
    assert '"' + "A" * 48 + '"' in out


def test_image_roundtrip_concatenates_data() -> None:
    src = '(image (at 1 2) (scale 3) (uuid "i") (data "AAAA" "BBBB" "CC"))'
    img = _load_image(_node(src))
    assert img.position == (1.0, 2.0)
    assert img.scale == 3.0
    assert img.data == "AAAABBBBCC"
    # re-dump re-chunks (all < 76 chars here -> single chunk)
    assert '(data\n"AAAABBBBCC")' in _dump_image(img)


# --- group (A2 2.10, :1619-1642) -- NO corpus fixture (gap): source tests only


def test_group_token_order_and_sorted_members() -> None:
    g = Group(name="grp", uuid="g1", members=["uuid-zzz", "uuid-aaa", "uuid-mmm"])
    out = _dump_group(g)
    # members are sorted ascending (:1634)
    assert out == ('(group "grp" (uuid "g1") (members "uuid-aaa" "uuid-mmm" "uuid-zzz") )')


def test_group_locked_and_lib_id_guards() -> None:
    g = Group(name="g", uuid="u", locked=True, lib_id="Lib:Block", members=["m1"])
    out = _dump_group(g)
    # order: name, uuid, locked, lib_id, members (:1619-1642)
    assert out.index("(uuid") < out.index("(locked yes)") < out.index("(lib_id")
    assert '(lib_id "Lib:Block")' in out
    # no locked / lib_id when unset
    g2 = Group(name="g", uuid="u", members=["m1"])
    out2 = _dump_group(g2)
    assert "(locked" not in out2
    assert "(lib_id" not in out2


def test_group_roundtrip() -> None:
    src = '(group "grp" (uuid "g") (members "b" "a"))'
    g = _load_group(_node(src))
    assert g.name == "grp"
    assert g.uuid == "g"
    assert g.members == ["b", "a"]  # load preserves file order; writer sorts


# --- table_cell (A2 2.8, :1476-1505) ----------------------------------------


def test_table_cell_has_span_and_no_stroke() -> None:
    cell = SchTableCell(
        content="R1",
        position=(0.0, 0.0),
        angle=0.0,
        size=(10.0, 5.0),
        margins=(0.5, 0.5, 0.5, 0.5),
        span=(2, 1),
        fill=Fill(type=FillType.none),
        attributes=TextAttributes(),
        uuid="c1",
    )
    out = _dump_table_cell(cell)
    assert out.startswith('(table_cell "R1" (exclude_from_sim no) (at 0 0 0) (size 10 5)')
    assert "(span 2 1)" in out
    # cells never emit stroke (:1499)
    assert "(stroke" not in out


def test_table_cell_roundtrip() -> None:
    src = (
        '(table_cell "V" (exclude_from_sim no) (at 1 2 0) (size 3 4)'
        " (margins 1 1 1 1) (span 1 1) (fill (type none))"
        ' (effects (font (size 1.27 1.27))) (uuid "c"))'
    )
    cell = _load_table_cell(_node(src))
    assert cell.span == (1, 1)
    assert cell.uuid == "c"


# --- table (A2 2.9, :1558-1606) ---------------------------------------------


def test_table_border_separator_stroke_guards() -> None:
    """Border/separator strokes emit only when their flags are set (:1564, :1573)."""
    t = SchTable(
        column_count=2,
        border_external=True,
        border_header=False,
        border_stroke=Stroke(width=0.1, type=StrokeType.default),
        separators_rows=False,
        separators_cols=False,
        separators_stroke=Stroke(),
        column_widths=[10.0, 20.0],
        row_heights=[5.0],
        uuid="t1",
        cells=[],
    )
    out = _dump_table(t)
    assert out.startswith("(table (column_count 2)")
    # border has external true -> stroke present
    border = out[out.index("(border") : out.index("(separators")]
    assert "(stroke" in border
    # separators both false -> no stroke
    seps = out[out.index("(separators") : out.index("(column_widths")]
    assert "(stroke" not in seps
    assert "(column_widths 10 20)" in out
    assert "(row_heights 5)" in out


def test_table_roundtrip_with_cell() -> None:
    src = (
        "(table (column_count 1)"
        " (border (external yes) (header no) (stroke (width 0.1) (type default)))"
        " (separators (rows no) (cols no))"
        " (column_widths 25.4) (row_heights 5.08)"
        ' (uuid "t")'
        ' (cells (table_cell "X" (exclude_from_sim no) (at 0 0 0) (size 25.4 5.08)'
        " (margins 1 1 1 1) (span 1 1) (fill (type none))"
        ' (effects (font (size 1.27 1.27))) (uuid "cc"))))'
    )
    t = _load_table(_node(src))
    assert t.column_count == 1
    assert t.border_external is True
    assert t.separators_rows is False
    assert t.column_widths == [25.4]
    assert t.row_heights == [5.08]
    assert len(t.cells) == 1
    assert t.cells[0].content == "X"
