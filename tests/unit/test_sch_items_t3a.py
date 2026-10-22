"""Unit tests for the T3a high-frequency schematic items.

Assertions trace to the A2 sch-root-grammar report Part 2 (per-``save*`` token
order/guards) and the cited ``sch_io_kicad_sexpr.cpp`` lines. Each test exercises
the flat writer output (token order, optional-token guards, quoting) and the
loader round-trip (parse -> model -> emit).
"""

from eedesigncore.common.models import Color, Fill, FillType, Stroke, StrokeType, TextAttributes
from eedesigncore.sch.loader import (
    _load_bus_entry,
    _load_junction,
    _load_label,
    _load_line,
    _load_no_connect,
    _load_polyline_item,
    _load_text,
    _load_text_box,
)
from eedesigncore.sch.models import (
    BusEntry,
    Junction,
    Label,
    LabelKind,
    Line,
    LineType,
    NoConnect,
    SchPolyline,
    SchText,
    SchTextBox,
)
from eedesigncore.sch.writer import (
    _dump_bus_entry,
    _dump_item_stream,
    _dump_junction,
    _dump_label,
    _dump_line,
    _dump_no_connect,
    _dump_text,
    _dump_text_box,
)
from eedesigncore.sexpr.parser import parse


def _node(text: str):
    return parse(text)


# --- junction (A2 2.1, :1248-1261) ------------------------------------------


def test_junction_token_order() -> None:
    j = Junction(
        position=(10.0, 20.0),
        diameter=0.0,
        color=Color(r=0, g=0, b=0, a=0.0),
        uuid="j-uuid",
    )
    out = _dump_junction(j)
    assert out == '(junction (at 10 20) (diameter 0) (color 0 0 0 0) (uuid "j-uuid") )'


def test_junction_roundtrip() -> None:
    src = '(junction (at 1.27 2.54) (diameter 0.5) (color 255 0 0 0.5) (uuid "abc"))'
    j = _load_junction(_node(src))
    assert j.position == (1.27, 2.54)
    assert j.diameter == 0.5
    assert j.color == Color(r=255, g=0, b=0, a=0.5)
    assert j.uuid == "abc"


# --- no_connect (A2 2.2, :1269-1276) ----------------------------------------


def test_no_connect_token_order() -> None:
    nc = NoConnect(position=(5.0, 6.0), uuid="nc1")
    assert _dump_no_connect(nc) == '(no_connect (at 5 6) (uuid "nc1") )'


def test_no_connect_roundtrip() -> None:
    nc = _load_no_connect(_node('(no_connect (at 3 4) (uuid "z"))'))
    assert nc.position == (3.0, 4.0)
    assert nc.uuid == "z"


# --- bus_entry (A2 2.3, :1294-1306) -----------------------------------------


def test_bus_entry_token_order() -> None:
    be = BusEntry(
        position=(0.0, 0.0),
        size=(2.54, 2.54),
        stroke=Stroke(width=0.0, type=StrokeType.default),
        uuid="be1",
    )
    out = _dump_bus_entry(be)
    assert out == (
        '(bus_entry (at 0 0) (size 2.54 2.54) (stroke (width 0) (type default)) (uuid "be1") )'
    )


def test_bus_entry_roundtrip() -> None:
    src = '(bus_entry (at 1 1) (size 2.54 -2.54) (stroke (width 0) (type default)) (uuid "b"))'
    be = _load_bus_entry(_node(src))
    assert be.size == (2.54, -2.54)
    assert be.uuid == "b"


# --- line: wire / bus / polyline (A2 2.4, :1381-1394) -----------------------


def test_wire_token_order() -> None:
    line = Line(
        line_type=LineType.wire,
        start=(0.0, 0.0),
        end=(2.54, 0.0),
        stroke=Stroke(),
        uuid="w1",
    )
    out = _dump_line(line)
    assert out == (
        '(wire (pts (xy 0 0) (xy 2.54 0)) (stroke (width 0) (type default)) (uuid "w1") )'
    )


def test_bus_token_selects_bus_keyword() -> None:
    line = Line(line_type=LineType.bus, start=(1.0, 1.0), end=(1.0, 5.0), uuid="b1")
    assert _dump_line(line).startswith("(bus (pts")


def test_line_roundtrip() -> None:
    src = '(wire (pts (xy 0 0) (xy 10 0)) (stroke (width 0) (type default)) (uuid "w"))'
    line = _load_line(_node(src), LineType.wire)
    assert line.start == (0.0, 0.0)
    assert line.end == (10.0, 0.0)
    assert line.uuid == "w"


def test_polyline_two_points_is_line() -> None:
    """A 2-point polyline parses to a Line (parser.cpp:2941-2955)."""
    src = '(polyline (pts (xy 0 0) (xy 5 5)) (stroke (width 0) (type default)) (uuid "p"))'
    item = _load_polyline_item(_node(src))
    assert isinstance(item, Line)
    assert item.line_type is LineType.polyline
    assert _dump_line(item).startswith("(polyline (pts")


def test_polyline_three_points_is_graphic_shape() -> None:
    """A >2-point polyline parses to a graphic SchPolyline (parser.cpp:2937-2940)."""
    src = (
        "(polyline (pts (xy 0 0) (xy 5 0) (xy 5 5))"
        ' (stroke (width 0) (type default)) (fill (type none)) (uuid "g"))'
    )
    item = _load_polyline_item(_node(src))
    assert isinstance(item, SchPolyline)
    assert len(item.pts) == 3


# --- text (A2 2.7 SCH_TEXT_T, :1405-1468) -----------------------------------


def test_text_token_order_and_exclude_from_sim() -> None:
    t = SchText(
        content="hello",
        exclude_from_sim=False,
        position=(1.0, 2.0),
        angle=0.0,
        attributes=TextAttributes(),
        uuid="t1",
    )
    out = _dump_text(t)
    assert out.startswith('(text "hello" (exclude_from_sim no) (at 1 2 0)')
    assert out.endswith('(uuid "t1") )')
    # plain text has no shape and no property fields
    assert "(shape" not in out
    assert "(property" not in out


def test_text_quoting_escapes() -> None:
    t = SchText(content='a "quote" and \\slash', position=(0.0, 0.0), uuid="t")
    out = _dump_text(t)
    assert r'"a \"quote\" and \\slash"' in out


def test_text_roundtrip() -> None:
    src = (
        '(text "N" (exclude_from_sim yes) (at 1 2 90)'
        ' (effects (font (size 1.27 1.27))) (uuid "t"))'
    )
    t = _load_text(_node(src))
    assert t.content == "N"
    assert t.exclude_from_sim is True
    assert t.angle == 90.0
    assert t.uuid == "t"


# --- labels (A2 2.7, common.cpp:201-212) ------------------------------------


def test_local_label_no_shape_no_length() -> None:
    lbl = Label(kind=LabelKind.local, content="NET", position=(0.0, 0.0), uuid="l1")
    out = _dump_label(lbl)
    assert out.startswith('(label "NET" (at 0 0 0)')
    assert "(shape" not in out
    assert "(length" not in out


def test_global_label_emits_shape_before_at() -> None:
    lbl = Label(
        kind=LabelKind.global_,
        content="VCC",
        shape="input",
        position=(1.0, 1.0),
        angle=180.0,
        uuid="g1",
    )
    out = _dump_label(lbl)
    assert out.startswith('(global_label "VCC" (shape input) (at 1 1 180)')


def test_hierarchical_label_token_and_shape() -> None:
    lbl = Label(kind=LabelKind.hierarchical, content="CLK", shape="output", uuid="h1")
    assert _dump_label(lbl).startswith('(hierarchical_label "CLK" (shape output)')


def test_netclass_flag_emits_length_before_shape() -> None:
    """Directive label serializes as netclass_flag with (length) then (shape) (2.7 step 3-4)."""
    lbl = Label(
        kind=LabelKind.netclass_flag,
        content="HV",
        length=2.54,
        shape="dot",
        position=(0.0, 0.0),
        uuid="d1",
    )
    out = _dump_label(lbl)
    assert out.startswith('(netclass_flag "HV" (length 2.54) (shape dot) (at 0 0 0)')


def test_label_fields_autoplaced_guard() -> None:
    lbl = Label(kind=LabelKind.local, content="X", fields_autoplaced=True, uuid="l")
    assert "(fields_autoplaced yes)" in _dump_label(lbl)
    lbl2 = Label(kind=LabelKind.local, content="X", fields_autoplaced=False, uuid="l")
    assert "(fields_autoplaced" not in _dump_label(lbl2)


def test_global_label_roundtrip_with_field() -> None:
    src = (
        '(global_label "VCC" (shape input) (at 10 20 0) (fields_autoplaced yes)'
        " (effects (font (size 1.27 1.27)) (justify left))"
        ' (uuid "g")'
        ' (property "Intersheet References" "${INTERSHEET_REFS}" (at 0 0 0)'
        " (hide yes) (show_name no) (do_not_autoplace no)))"
    )
    lbl = _load_label(_node(src), LabelKind.global_)
    assert lbl.shape == "input"
    assert lbl.fields_autoplaced is True
    assert len(lbl.fields) == 1
    assert lbl.fields[0].name == "Intersheet References"


def test_netclass_flag_directive_label_legacy_alias_maps_same() -> None:
    """Both netclass_flag and directive_label load to the netclass_flag kind (Contradiction 1)."""
    src = '(directive_label "HV" (length 2.54) (shape dot) (at 0 0 0) (uuid "d"))'
    lbl = _load_label(_node(src), LabelKind.netclass_flag)
    assert lbl.kind is LabelKind.netclass_flag
    # emits the current on-disk token, not the legacy alias
    assert _dump_label(lbl).startswith("(netclass_flag")


# --- text_box (A2 2.8, :1476-1505) ------------------------------------------


def test_text_box_token_order() -> None:
    tb = SchTextBox(
        content="note",
        exclude_from_sim=False,
        position=(0.0, 0.0),
        angle=0.0,
        size=(10.0, 5.0),
        margins=(0.9, 0.9, 0.9, 0.9),
        stroke=Stroke(),
        fill=Fill(type=FillType.none),
        attributes=TextAttributes(),
        uuid="tb1",
    )
    out = _dump_text_box(tb)
    assert out.startswith(
        '(text_box "note" (exclude_from_sim no) (at 0 0 0) (size 10 5)'
        " (margins 0.9 0.9 0.9 0.9) (stroke (width 0) (type default)) (fill (type none))"
    )
    assert out.endswith('(uuid "tb1") )')


def test_text_box_roundtrip() -> None:
    src = (
        '(text_box "n" (exclude_from_sim no) (at 1 2 0) (size 3 4)'
        " (margins 1 1 1 1) (stroke (width 0) (type default)) (fill (type none))"
        ' (effects (font (size 1.27 1.27))) (uuid "tb"))'
    )
    tb = _load_text_box(_node(src))
    assert tb.size == (3.0, 4.0)
    assert tb.margins == (1.0, 1.0, 1.0, 1.0)
    assert tb.uuid == "tb"


# --- item-stream ordering (A2 Part 3) ---------------------------------------


def test_item_stream_sorts_by_type_rank_then_uuid() -> None:
    """Items emit grouped by KICAD_T rank ascending, uuid within a rank (Part 3)."""
    from eedesigncore.sch.models import Schematic

    sch = Schematic(
        junctions=[Junction(uuid="j-zzz"), Junction(uuid="j-aaa")],
        lines=[Line(uuid="line-1")],
        texts=[SchText(content="t", uuid="text-1")],
    )
    stream = _dump_item_stream(sch)
    # text (rank 1) before junction (rank 3) before line (rank 6)
    kinds = [s.split()[0] for s in stream]
    assert kinds == ["(text", "(junction", "(junction", "(wire"]
    # within junctions, uuid ascending: j-aaa before j-zzz
    junctions = [s for s in stream if s.startswith("(junction")]
    assert junctions[0].endswith('(uuid "j-aaa") )')
    assert junctions[1].endswith('(uuid "j-zzz") )')
