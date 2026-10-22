"""Unit tests for the canonical symbol save order (R1, R2).

Every expected order below comes from a source-verified KiCad rule, cited in
``context/audit/symbol-save-ordering-2026-08-13.md`` and reproduced in
``common/sch_family/ordering.py``.
"""

import time

from eedesigncore.common.models import Fill, FillType, Property, Stroke, StrokeType
from eedesigncore.common.ordering import str_num_cmp
from eedesigncore.common.sch_family.ordering import (
    arc_center_iu,
    field_ordinal,
    inheritance_depth,
    int32,
    internal_position,
    sort_draw_items,
    sort_library_symbols,
    sort_symbol_fields,
    sort_units,
    y_iu,
)
from eedesigncore.common.sch_family.pins import Pin, PinAlternate
from eedesigncore.common.sch_family.shapes import (
    SchArc,
    SchBezier,
    SchCircle,
    SchPolyline,
    SchRect,
)
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.common.sch_family.text import SymText, SymTextBox
from eedesigncore.sym.enums import PinElectricalType, PinShape
from eedesigncore.sym.models import SymbolLib
from eedesigncore.sym.writer import write_sym

# ---------------------------------------------------------------------------
# StrNumCmp
# ---------------------------------------------------------------------------


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def test_str_num_cmp_orders_digit_runs_numerically() -> None:
    assert _sign(str_num_cmp("9", "10")) == -1
    assert _sign(str_num_cmp("10", "9")) == 1
    assert _sign(str_num_cmp("2", "10")) == -1


def test_str_num_cmp_ignores_leading_zeros_in_a_digit_run() -> None:
    assert str_num_cmp("01", "1") == 0
    assert str_num_cmp("007", "7") == 0
    assert _sign(str_num_cmp("08", "9")) == -1


def test_str_num_cmp_compares_mixed_alphanumeric_by_segment() -> None:
    assert _sign(str_num_cmp("a1", "a2")) == -1
    assert _sign(str_num_cmp("a9", "a10")) == -1
    assert _sign(str_num_cmp("a5", "b1")) == -1
    assert _sign(str_num_cmp("A10", "A9")) == 1


def test_str_num_cmp_is_case_sensitive_by_default() -> None:
    # Upper case sorts first because it compares by code point.
    assert _sign(str_num_cmp("A1", "a1")) == -1
    assert _sign(str_num_cmp("a1", "A1")) == 1


def test_str_num_cmp_case_insensitive_branch_folds_case() -> None:
    assert str_num_cmp("A1", "a1", ignore_case=True) == 0


def test_str_num_cmp_shorter_prefix_sorts_first() -> None:
    assert _sign(str_num_cmp("A", "AB")) == -1
    assert _sign(str_num_cmp("AB", "A")) == 1
    assert str_num_cmp("AB", "AB") == 0


def test_str_num_cmp_handles_empty_strings() -> None:
    assert str_num_cmp("", "") == 0
    assert _sign(str_num_cmp("", "1")) == -1


# ---------------------------------------------------------------------------
# StrNumCmp, 32-bit wrap of the digit accumulator (ADR-071)
#
# KiCad accumulates a digit run into a C++ ``int``
# (``common/string_utils.cpp:842-857``), so a run wider than ten digits wraps.
# The oracle confirms the binary wraps:
# ``test_pathological_pin_numbers_match_the_oracle``.
# ---------------------------------------------------------------------------


def test_str_num_cmp_wraps_a_run_that_overflows_int32() -> None:
    """``2147483648`` wraps to INT_MIN, so it sorts BELOW every positive run."""
    assert _sign(str_num_cmp("2147483648", "1")) == -1
    assert _sign(str_num_cmp("2147483648", "0")) == -1
    assert _sign(str_num_cmp("2147483647", "2147483648")) == 1


def test_str_num_cmp_wraps_a_full_turn_of_the_accumulator_to_zero() -> None:
    """``4294967296`` is 2**32, which wraps to zero and sorts below ``1``."""
    assert _sign(str_num_cmp("4294967296", "1")) == -1
    assert str_num_cmp("4294967296", "0") == 0


def test_str_num_cmp_treats_two_runs_with_the_same_wrapped_value_as_equal() -> None:
    """``4294967297`` wraps to 1, so the run compares equal to ``1``."""
    assert str_num_cmp("4294967297", "1") == 0


def test_str_num_cmp_consumes_a_long_run_whole_rather_than_stopping() -> None:
    """The C++ loop never breaks early, so the trailing digits still move the value."""
    assert str_num_cmp("12345678901234567890", "12345678901234567890") == 0
    assert _sign(str_num_cmp("12345678901234567890", "12345678901234567891")) == -1
    assert _sign(str_num_cmp("99999999999999999999", "10000000000")) == 1


def test_str_num_cmp_ignores_leading_zeros_on_a_wide_run() -> None:
    assert str_num_cmp("00000000000000000009", "9") == 0


def test_str_num_cmp_compares_a_non_ascii_digit_as_a_plain_character() -> None:
    """``wxIsdigit`` accepts ASCII only, so U+0660 takes the character branch."""
    assert str_num_cmp("٠", "٠") == 0
    assert _sign(str_num_cmp("9", "٠")) == -1


def test_str_num_cmp_compares_a_multi_thousand_digit_run_in_linear_time() -> None:
    """A wide run must not build an arbitrary-precision integer (MEDIUM-4).

    The quadratic form took seconds on this input. The linear form takes
    milliseconds, so a generous bound still separates the two.
    """
    first = "1" + "0" * 200_000
    second = "1" + "0" * 199_999 + "1"

    started = time.perf_counter()
    for _ in range(10):
        assert _sign(str_num_cmp(first, second)) == -1
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"ten wide comparisons took {elapsed:.1f}s, expected linear cost"
    assert _sign(str_num_cmp("1", "")) == 1


# ---------------------------------------------------------------------------
# Conn_02x05_Odd_Even: the discriminating stock case (audit section 9)
# ---------------------------------------------------------------------------

# Pin number, file x, file y, read from Connector_Generic.kicad_sym lines
# 64456-64635 (audit section 9.1). The x alternates between the two columns, so
# this order is NOT geometric, and "10" follows "9", so it is NOT lexicographic.
ODD_EVEN_PINS: list[tuple[str, float, float]] = [
    ("1", -5.08, 5.08),
    ("2", 7.62, 5.08),
    ("3", -5.08, 2.54),
    ("4", 7.62, 2.54),
    ("5", -5.08, 0.0),
    ("6", 7.62, 0.0),
    ("7", -5.08, -2.54),
    ("8", 7.62, -2.54),
    ("9", -5.08, -5.08),
    ("10", 7.62, -5.08),
]

# Rectangle start points of the same unit block, in emitted order, from lines
# 64335-64455 (audit section 9.3). X ascends and file Y DESCENDS within each x
# group, because ascending internal Y is descending file Y.
ODD_EVEN_RECT_STARTS: list[tuple[float, float]] = [
    (-1.27, 6.35),
    (-1.27, 5.207),
    (-1.27, 2.667),
    (-1.27, 0.127),
    (-1.27, -2.413),
    (-1.27, -4.953),
    (3.81, 5.207),
    (3.81, 2.667),
    (3.81, 0.127),
    (3.81, -2.413),
    (3.81, -4.953),
]


def _odd_even_pin(number: str, x: float, y: float) -> Pin:
    return Pin(
        electrical_type=PinElectricalType.passive,
        shape=PinShape.line,
        position=(x, y),
        angle=0.0 if x < 0 else 180.0,
        length=2.54,
        name="Pin_" + number,
        number=number,
    )


def test_pin_order_reproduces_conn_02x05_odd_even() -> None:
    """Pins sort by natural number, not by geometry (audit C-2, section 9.1)."""
    pins = [_odd_even_pin(*row) for row in ODD_EVEN_PINS]
    shuffled = [pins[i] for i in (9, 4, 0, 7, 2, 8, 5, 1, 6, 3)]

    ordered = sort_draw_items(shuffled)

    assert [pin.number for pin in ordered] == [row[0] for row in ODD_EVEN_PINS]
    # The x column alternates, which a position-first sort could not produce.
    assert [pin.position[0] for pin in ordered[:4]] == [-5.08, 7.62, -5.08, 7.62]


def test_pin_order_is_not_lexicographic() -> None:
    pins = [_odd_even_pin(*row) for row in ODD_EVEN_PINS]
    ordered = sort_draw_items(list(reversed(pins)))
    numbers = [pin.number for pin in ordered]
    assert numbers.index("9") < numbers.index("10")


def _odd_even_rect(start: tuple[float, float]) -> SchRect:
    return SchRect(
        start=start,
        end=(start[0] + 2.54, start[1] - 1.143),
        stroke=Stroke(width=0.254, type=StrokeType.default),
        fill=Fill(type=FillType.background),
    )


def test_rect_order_reproduces_conn_02x05_odd_even() -> None:
    """Shapes sort on INTERNAL Y, which is descending file Y (audit section 9.3)."""
    rects = [_odd_even_rect(start) for start in ODD_EVEN_RECT_STARTS]
    shuffled = list(reversed(rects))

    ordered = sort_draw_items(shuffled)

    assert [rect.start for rect in ordered] == ODD_EVEN_RECT_STARTS


def test_rect_order_without_y_inversion_would_reverse_each_x_group() -> None:
    """Guard the Y inversion: ascending FILE y would emit each group backwards."""
    rects = [_odd_even_rect(start) for start in ODD_EVEN_RECT_STARTS]
    ordered = sort_draw_items(list(reversed(rects)))
    naive = sorted(rects, key=lambda rect: (rect.start[0], rect.start[1]))
    assert [rect.start for rect in ordered] != [rect.start for rect in naive]


def test_shapes_emit_before_pins() -> None:
    pins = [_odd_even_pin(*row) for row in ODD_EVEN_PINS]
    rects = [_odd_even_rect(start) for start in ODD_EVEN_RECT_STARTS]

    ordered = sort_draw_items([*pins, *rects])

    assert all(isinstance(item, SchRect) for item in ordered[: len(rects)])
    assert all(isinstance(item, Pin) for item in ordered[len(rects) :])


# ---------------------------------------------------------------------------
# Type rank, private flag, tie stability
# ---------------------------------------------------------------------------


def test_type_rank_orders_shape_text_textbox_pin() -> None:
    pin = _odd_even_pin("1", 0.0, 0.0)
    text = SymText(content="t", position=(0.0, 0.0))
    text_box = SymTextBox(content="b", position=(0.0, 0.0), size=(1.0, 1.0))
    circle = SchCircle(center=(0.0, 0.0), radius=1.0)

    ordered = sort_draw_items([pin, text_box, text, circle])

    assert [type(item) for item in ordered] == [SchCircle, SymText, SymTextBox, Pin]


def test_private_items_sort_after_public_ones() -> None:
    public = SchCircle(center=(10.0, 0.0), radius=1.0)
    private = SchCircle(private=True, center=(-10.0, 0.0), radius=1.0)

    ordered = sort_draw_items([private, public])

    assert ordered == [public, private]


def test_tied_text_items_keep_encounter_order() -> None:
    """Audit C-3: KiCad breaks this tie on a UUID the file never stores."""
    first = SymText(content="alpha", position=(1.0, 2.0))
    second = SymText(content="beta", position=(1.0, 2.0))

    assert sort_draw_items([second, first]) == [second, first]
    assert sort_draw_items([first, second]) == [first, second]


def test_polyline_sorts_on_its_first_vertex() -> None:
    left = SchPolyline(pts=[(-2.0, 0.0), (5.0, 0.0)])
    right = SchPolyline(pts=[(3.0, 0.0), (4.0, 0.0)])

    assert sort_draw_items([right, left]) == [left, right]


def test_bezier_sorts_on_its_first_control_point() -> None:
    left = SchBezier(pts=[(-1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (2.0, 0.0)])
    right = SchBezier(pts=[(5.0, 0.0), (6.0, 1.0), (7.0, 1.0), (8.0, 0.0)])

    assert sort_draw_items([right, left]) == [left, right]


def test_unknown_item_types_keep_encounter_order() -> None:
    class Foreign:
        pass

    first, second = Foreign(), Foreign()
    assert sort_draw_items([first, second]) == [first, second]


def test_arc_sorts_on_its_centre_not_its_start() -> None:
    """``EDA_SHAPE::getPosition`` returns the arc centre (``eda_shape.cpp:432-440``)."""
    arc = SchArc(start=(9.9411, -1.726), mid=(3.251, 1.6949), end=(9.9725, -1.6637))
    circle = SchCircle(center=(6.604, 0.0), radius=3.759)

    centre_x = arc_center_iu(arc)[0]
    assert centre_x < internal_position(circle)[0]
    assert arc.start[0] > circle.center[0]
    assert sort_draw_items([circle, arc]) == [arc, circle]


def test_degenerate_arc_centre_wraps_the_int32_difference() -> None:
    """A collinear arc clamps to the int rail, and the difference wraps (HCPL-0611-500E)."""
    arc = SchArc(start=(21.336, -10.1586), mid=(21.336, -10.1593), end=(21.336, -10.16))
    polyline = SchPolyline(pts=[(22.86, -15.24), (13.462, -15.24)])

    assert arc_center_iu(arc)[0] == -(2**31) + 100
    assert sort_draw_items([arc, polyline]) == [polyline, arc]


def test_int32_wraps_at_the_signed_boundary() -> None:
    assert int32(0) == 0
    assert int32(2**31 - 1) == 2**31 - 1
    assert int32(2**31) == -(2**31)
    assert int32(-(2**31) - 1) == 2**31 - 1


def test_y_iu_negates_and_scales() -> None:
    assert y_iu(5.08) == -50800
    assert y_iu(-5.08) == 50800
    assert y_iu(0.0) == 0


# ---------------------------------------------------------------------------
# Pin tie-break keys
# ---------------------------------------------------------------------------


def test_pins_with_equal_numbers_break_the_tie_on_position() -> None:
    left = _odd_even_pin("1", -5.08, 0.0)
    right = _odd_even_pin("1", 7.62, 0.0)

    assert sort_draw_items([right, left]) == [left, right]


def test_pins_with_equal_number_and_position_break_the_tie_on_length() -> None:
    short = _odd_even_pin("1", 0.0, 0.0)
    short.length = 1.27
    long_pin = _odd_even_pin("1", 0.0, 0.0)

    assert sort_draw_items([long_pin, short]) == [short, long_pin]


def test_pins_break_the_tie_on_electrical_type_rank() -> None:
    input_pin = _odd_even_pin("1", 0.0, 0.0)
    input_pin.electrical_type = PinElectricalType.input
    output_pin = _odd_even_pin("1", 0.0, 0.0)
    output_pin.electrical_type = PinElectricalType.output

    assert sort_draw_items([output_pin, input_pin]) == [input_pin, output_pin]


def test_pins_break_the_tie_on_alternate_names() -> None:
    first = _odd_even_pin("1", 0.0, 0.0)
    first.alternates = [
        PinAlternate(name="A", electrical_type=PinElectricalType.passive, shape=PinShape.line)
    ]
    second = _odd_even_pin("1", 0.0, 0.0)
    second.alternates = [
        PinAlternate(name="B", electrical_type=PinElectricalType.passive, shape=PinShape.line)
    ]

    assert sort_draw_items([second, first]) == [first, second]


def test_leading_zero_pin_numbers_compare_equal_and_keep_encounter_order() -> None:
    """``SCH_PIN::compare`` returns the StrNumCmp result even when it is zero."""
    padded = _odd_even_pin("01", 7.62, 0.0)
    plain = _odd_even_pin("1", -5.08, 0.0)

    assert sort_draw_items([padded, plain]) == [padded, plain]


# ---------------------------------------------------------------------------
# Shape geometry tie-breaks
# ---------------------------------------------------------------------------


def test_shapes_at_one_position_break_the_tie_on_end_point() -> None:
    small = SchRect(start=(0.0, 0.0), end=(1.0, -1.0))
    large = SchRect(start=(0.0, 0.0), end=(5.0, -1.0))

    assert sort_draw_items([large, small]) == [small, large]


def test_shapes_at_one_position_break_the_tie_on_shape_kind() -> None:
    rect = SchRect(start=(0.0, 0.0), end=(1.0, 0.0))
    circle = SchCircle(center=(0.0, 0.0), radius=1.0)

    # RECTANGLE is 1 and CIRCLE is 3 in SHAPE_T, and both report m_end (1, 0).
    assert sort_draw_items([circle, rect]) == [rect, circle]


def test_shapes_break_the_tie_on_fill_type() -> None:
    none_fill = SchRect(start=(0.0, 0.0), end=(1.0, 0.0), fill=Fill(type=FillType.none))
    outline_fill = SchRect(start=(0.0, 0.0), end=(1.0, 0.0), fill=Fill(type=FillType.outline))

    assert sort_draw_items([outline_fill, none_fill]) == [none_fill, outline_fill]


def test_shape_position_ties_inside_the_epsilon_fall_through() -> None:
    """``EDA_SHAPE::Compare`` allows a two internal unit tolerance."""
    first = SchRect(start=(0.0, 0.0), end=(1.0, 0.0), fill=Fill(type=FillType.outline))
    second = SchRect(start=(0.0, 0.0), end=(1.0001, 0.0), fill=Fill(type=FillType.none))

    # The end points differ by 1 internal unit, which is inside EPSILON, so the
    # fill type decides and the wider rectangle sorts first.
    assert sort_draw_items([first, second]) == [second, first]


def test_text_boxes_sort_on_position() -> None:
    left = SymTextBox(content="a", position=(-2.0, 0.0), size=(1.0, 1.0))
    right = SymTextBox(content="b", position=(3.0, 0.0), size=(1.0, 1.0))

    assert sort_draw_items([right, left]) == [left, right]


def test_text_boxes_at_one_position_break_the_tie_on_size() -> None:
    small = SymTextBox(content="a", position=(0.0, 0.0), size=(1.0, 1.0))
    wide = SymTextBox(content="b", position=(0.0, 0.0), size=(5.0, 1.0))

    assert sort_draw_items([wide, small]) == [small, wide]


# ---------------------------------------------------------------------------
# Units, fields, library symbols
# ---------------------------------------------------------------------------


def test_units_sort_by_unit_index_then_body_style() -> None:
    units = [
        SymbolUnit.for_symbol("U", unit_index=2, body_style_index=1),
        SymbolUnit.for_symbol("U", unit_index=1, body_style_index=2),
        SymbolUnit.for_symbol("U", unit_index=1, body_style_index=1),
        SymbolUnit.for_symbol("U", unit_index=0, body_style_index=1),
    ]

    ordered = sort_units(units)

    assert [(u.unit_index, u.body_style_index) for u in ordered] == [
        (0, 1),
        (1, 1),
        (1, 2),
        (2, 1),
    ]


def test_mandatory_fields_outrank_user_fields_wherever_they_sit() -> None:
    fields = [
        Property(name="MPN", value="X1"),
        Property(name="Reference", value="U"),
        Property(name="Manufacturer", value="Acme"),
        Property(name="Value", value="V"),
    ]

    ordered = sort_symbol_fields(fields)

    assert [field.name for field in ordered] == [
        "Reference",
        "Value",
        "MPN",
        "Manufacturer",
    ]


def test_field_ordinal_matches_the_kicad_ids() -> None:
    assert field_ordinal("Reference", 0) == 1
    assert field_ordinal("Value", 0) == 2
    assert field_ordinal("Footprint", 0) == 3
    assert field_ordinal("Datasheet", 0) == 4
    assert field_ordinal("Description", 0) == 5
    assert field_ordinal("MPN", 0) == 42
    assert field_ordinal("MPN", 3) == 45


def test_field_ordinal_matches_mandatory_names_case_insensitively() -> None:
    assert field_ordinal("reference", 0) == 1
    assert field_ordinal("VALUE", 0) == 2


def test_user_fields_keep_their_relative_file_order() -> None:
    fields = [
        Property(name="Zeta", value="1"),
        Property(name="Alpha", value="2"),
    ]

    assert [field.name for field in sort_symbol_fields(fields)] == ["Zeta", "Alpha"]


def test_library_symbols_sort_by_inheritance_depth_then_name() -> None:
    symbols = [
        Symbol(name="Derived_A", extends="Root_B"),
        Symbol(name="Root_B"),
        Symbol(name="Grandchild", extends="Derived_A"),
        Symbol(name="Root_A"),
    ]

    ordered = sort_library_symbols(symbols)

    assert [symbol.name for symbol in ordered] == [
        "Root_A",
        "Root_B",
        "Derived_A",
        "Grandchild",
    ]


def test_library_symbol_sort_survives_an_inheritance_cycle() -> None:
    first = Symbol(name="A", extends="B")
    second = Symbol(name="B", extends="A")

    ordered = sort_library_symbols([second, first])

    assert [symbol.name for symbol in ordered] == ["A", "B"]


def test_library_symbol_with_an_unresolved_parent_counts_as_a_root() -> None:
    orphan = Symbol(name="Orphan", extends="Missing")
    root = Symbol(name="AAA")

    ordered = sort_library_symbols([orphan, root])

    assert [symbol.name for symbol in ordered] == ["AAA", "Orphan"]


# ---------------------------------------------------------------------------
# One parent-resolution rule, shared by the depth sort and the writer (ADR-078)
# ---------------------------------------------------------------------------


def test_the_extends_value_is_matched_verbatim() -> None:
    """D1: KiCad never parses the ``extends`` value as a ``LIB_ID``."""
    parent = Symbol(name="R")
    child = Symbol(name="R_Small", extends="Device:R")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 0


def test_the_key_side_reduces_to_the_bare_name() -> None:
    """D2: ``m_name`` is stored nickname-stripped (``..._parser.cpp:364``)."""
    parent = Symbol(name="Foo:Bar")
    child = Symbol(name="Foo:Bar_Small", extends="Bar")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 1


def test_an_ambiguous_reduced_key_resolves_to_nothing() -> None:
    """D3: this library declines to guess where KiCad takes the last definition."""
    first = Symbol(name="A:Bar")
    second = Symbol(name="B:Bar")
    child = Symbol(name="Child", extends="Bar")
    by_name = {symbol.name: symbol for symbol in (first, second, child)}

    assert inheritance_depth(child, by_name) == 0


def test_a_slash_escape_is_reversed_on_both_sides() -> None:
    """D4: ``..._parser.cpp:343`` for the name, ``:477`` for the ``extends`` value."""
    parent = Symbol(name="A/B")
    child = Symbol(name="A/B_Small", extends="A{slash}B")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 1

    escaped_parent = Symbol(name="A{slash}B")
    escaped_map = {escaped_parent.name: escaped_parent, "A/B_Small": child}
    child.extends = "A/B"

    assert inheritance_depth(child, escaped_map) == 1


def test_the_match_is_case_sensitive() -> None:
    """``LibSymbolMapSort`` is a plain codepoint compare (``symbol_library_common.h:42``)."""
    parent = Symbol(name="Bar")
    child = Symbol(name="Child", extends="bar")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 0


def test_the_depth_sort_and_the_writer_agree_on_a_reduced_key_parent() -> None:
    """The two rules were separate before ADR-078; on D2 input they disagreed."""
    from eedesigncore.sym.writer import _dump_symbol

    parent = Symbol(name="Foo:Bar", keywords="parent kw")
    child = Symbol(name="Foo:Bar_Small", extends="Bar")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 1
    assert '"ki_keywords" "parent kw"' in _dump_symbol(child, by_name=by_name)


def test_a_shared_index_gives_the_same_depth_as_a_per_call_one() -> None:
    """``sort_library_symbols`` builds the index once; the answer must not move."""
    from eedesigncore.common.sch_family.ordering import build_bare_name_index

    root = Symbol(name="Foo:Bar")
    child = Symbol(name="Foo:Bar_Small", extends="Bar")
    grandchild = Symbol(name="Foo:Bar_Tiny", extends="Bar_Small")
    by_name = {symbol.name: symbol for symbol in (root, child, grandchild)}
    shared = build_bare_name_index(by_name)

    for symbol, expected in ((root, 0), (child, 1), (grandchild, 2)):
        assert inheritance_depth(symbol, by_name) == expected
        assert inheritance_depth(symbol, by_name, shared) == expected


def test_the_depth_sort_and_the_writer_agree_on_a_qualified_extends() -> None:
    from eedesigncore.sym.writer import _dump_symbol

    parent = Symbol(name="Bar", keywords="parent kw")
    child = Symbol(name="Bar_Small", extends="Foo:Bar")
    by_name = {symbol.name: symbol for symbol in (parent, child)}

    assert inheritance_depth(child, by_name) == 0
    assert "ki_keywords" not in _dump_symbol(child, by_name=by_name)


# ---------------------------------------------------------------------------
# Writer wiring
# ---------------------------------------------------------------------------


def _pin_line_numbers(text: str) -> list[str]:
    return [
        line.split('"')[1] for line in text.splitlines() if line.lstrip().startswith("(number ")
    ]


def test_writer_sorts_draw_items_of_a_mutated_unit() -> None:
    pins = [_odd_even_pin(*row) for row in ODD_EVEN_PINS]
    unit = SymbolUnit.for_symbol("Conn", items=list(reversed(pins)))
    lib = SymbolLib(symbols=[Symbol(name="Conn", units=[unit])])

    assert _pin_line_numbers(write_sym(lib)) == [row[0] for row in ODD_EVEN_PINS]


def test_writer_sorts_units_and_library_symbols() -> None:
    symbol = Symbol(
        name="Root",
        units=[
            SymbolUnit.for_symbol("Root", unit_index=2),
            SymbolUnit.for_symbol("Root", unit_index=1),
        ],
    )
    lib = SymbolLib(symbols=[Symbol(name="Child", extends="Root"), symbol])

    text = write_sym(lib)

    assert text.index('(symbol "Root"') < text.index('(symbol "Child"')
    assert text.index('(symbol "Root_1_1"') < text.index('(symbol "Root_2_1"')


def test_writer_sorts_properties_of_a_derived_symbol() -> None:
    lib = SymbolLib(
        symbols=[
            Symbol(
                name="Child",
                extends="Root",
                fields=[
                    Property(name="MPN", value="X"),
                    Property(name="Reference", value="U"),
                ],
            )
        ]
    )

    text = write_sym(lib)

    assert text.index('"Reference"') < text.index('"MPN"')
