import math

import pytest

from eedesigncore import (
    JustifyH,
    JustifyV,
    Line,
    Schematic,
    TextAttributes,
    create_schematic_table,
    find_schematic_overlaps,
    fit_schematic_table,
    measure_schematic_item,
)
from eedesigncore.render.text_boxes import text_pen, wrapped_lines


def assert_text_fits(table):
    measured = measure_schematic_item(table)
    assert measured.complete
    for part in measured.parts:
        if ":text" not in part.name:
            continue
        cell = table.cells[int(part.name.split(":")[1])]
        x, y = cell.position
        w, h = cell.size
        assert part.bounds.min_x >= x + 0.29
        assert part.bounds.max_x <= x + w - 0.29
        assert part.bounds.min_y >= y + 0.29
        assert part.bounds.max_y <= y + h - 0.29
    assert not find_schematic_overlaps([measured])


def test_content_drives_independent_rows_columns_and_roundtrip():
    table = create_schematic_table(
        [
            ["A", "Meaning"],
            ["PWR", "A long power supply description"],
            ["MULTI", "First line\nSecond line"],
            ["", ""],
        ],
        at=(25.4, 38.1),
    )
    assert table.column_widths[1] > table.column_widths[0]
    assert table.row_heights[2] > table.row_heights[1]
    assert table.cells[0].attributes.bold
    assert not table.cells[2].attributes.bold
    assert len({table.uuid, *(cell.uuid for cell in table.cells)}) == 9
    assert_text_fits(table)
    for cell in table.cells:
        lines = wrapped_lines(
            cell.content,
            cell.size[0] - cell.margins[0] - cell.margins[2],
            cell.attributes,
            text_pen(cell.attributes, 0.1524),
            None,
        )
        assert len(lines) == (len(cell.content.split("\n")) if cell.content else 0)
    restored = Schematic.from_string(Schematic(tables=[table]).to_string()).tables[0]
    assert restored == table


def test_refit_copy_grows_and_shrinks_preserving_ids_and_styles():
    table = create_schematic_table([["Heading"], ["tiny"]], at=(10, 20))
    table.cells[1].content = "A substantially longer phrase"
    table.cells[1].attributes.italic = True
    before = table.model_dump()
    fitted = fit_schematic_table(table)
    assert table.model_dump() == before
    assert fitted.column_widths[0] > table.column_widths[0]
    assert fitted.uuid == table.uuid
    assert [cell.uuid for cell in fitted.cells] == [cell.uuid for cell in table.cells]
    assert fitted.cells[1].attributes == table.cells[1].attributes
    assert fit_schematic_table(fitted) == fitted
    fitted.cells[1].content = "x"
    smaller = fit_schematic_table(fitted)
    assert smaller.column_widths[0] < fitted.column_widths[0]
    assert smaller.cells[0].position == (10.16, 20.32)
    assert_text_fits(smaller)


@pytest.mark.parametrize("horizontal", list(JustifyH))
@pytest.mark.parametrize("vertical", list(JustifyV))
def test_alignment_and_multiline_overhang(horizontal, vertical):
    table = create_schematic_table([["Wgjpq\nitalic second line"]], at=(0, 0))
    table.cells[0].attributes = TextAttributes(
        justify_h=horizontal, justify_v=vertical, italic=True, bold=True
    )
    assert_text_fits(fit_schematic_table(table))


def test_return_bounds_and_wire_collision():
    created = create_schematic_table(
        [["Name", "Description"], ["PWR", "Power"]], at=(20, 20), with_bounds=True
    )
    assert created.measurement.complete
    assert created.measurement.bounds.width >= sum(created.item.column_widths)
    wire = Line(start=(19, 21), end=(50, 21), uuid="wire")
    assert find_schematic_overlaps([created.measurement, measure_schematic_item(wire)])
    far = Line(start=(0, 0), end=(10, 0), uuid="far")
    assert not find_schematic_overlaps([created.measurement, measure_schematic_item(far)])


@pytest.mark.parametrize("rows", [[], [[]], [["a"], ["b", "c"]], "text", [[1]]])
def test_invalid_rows(rows):
    with pytest.raises(ValueError):
        create_schematic_table(rows, at=(0, 0))


@pytest.mark.parametrize("option", ["span", "angle", "font", "variable"])
def test_rejected_fit_preserves_source(option):
    table = create_schematic_table([["Name"]], at=(0, 0))
    if option == "span":
        table.cells[0].span = (0, 0)
    elif option == "angle":
        table.cells[0].angle = 90
    elif option == "font":
        table.cells[0].attributes.font_face = "Missing font"
    else:
        table.cells[0].content = "${UNRESOLVED}"
    before = table.model_dump()
    with pytest.raises(ValueError):
        fit_schematic_table(table)
    assert table.model_dump() == before


@pytest.mark.parametrize(
    "kwargs",
    [{"padding_mm": -1}, {"text_size_mm": 0}, {"at": (math.nan, 0)}, {"padding_mm": math.inf}],
)
def test_invalid_dimensions(kwargs):
    with pytest.raises(ValueError):
        create_schematic_table([["x"]], **({"at": (0, 0)} | kwargs))


def test_resolved_variables_fit_display_text_without_replacing_source():
    from eedesigncore import TextResolutionContext

    context = TextResolutionContext(variables={"DESCRIPTION": "Long resolved description"})
    fitted = create_schematic_table(
        [["${DESCRIPTION}"]], at=(0, 0), text_context=context, with_bounds=True
    )
    literal = create_schematic_table([["Long resolved description"]], at=(0, 0))
    assert fitted.item.column_widths == literal.column_widths
    assert fitted.item.cells[0].content == "${DESCRIPTION}"
    assert fitted.measurement.complete


def test_explicit_font_and_nonfinite_text_size():
    from eedesigncore.common.font import default_font

    table = create_schematic_table([["Named font"]], at=(0, 0), font=default_font())
    assert fit_schematic_table(table, font=default_font(), with_bounds=True).measurement.complete
    table.cells[0].attributes.font_size = (math.inf, 1.27)
    with pytest.raises(ValueError):
        fit_schematic_table(table, font=default_font())


def test_grid_alignment_includes_all_cell_corners():
    table = create_schematic_table(
        [["Name", "Longer column"], ["PWR", "Description"]], at=(-10.03, 20.12)
    )
    for cell in table.cells:
        for value in (
            *cell.position,
            *cell.size,
            cell.position[0] + cell.size[0],
            cell.position[1] + cell.size[1],
        ):
            assert value / 1.27 == pytest.approx(round(value / 1.27), abs=1e-9)
    assert_text_fits(table)


@pytest.mark.parametrize("horizontal", list(JustifyH))
@pytest.mark.parametrize("vertical", list(JustifyV))
def test_optional_word_wrap_grows_rows_and_preserves_text(horizontal, vertical):
    text = "Several words describing a power supply and its operating requirements"
    original = create_schematic_table([["Meaning"], [text]], at=(0, 0))
    original.cells[1].attributes.justify_h = horizontal
    original.cells[1].attributes.justify_v = vertical
    original.cells[1].attributes.italic = True
    fitted = fit_schematic_table(original, word_wrap=True, max_column_width_mm=25.4)
    assert fitted.column_widths[0] < original.column_widths[0]
    assert fitted.row_heights[1] > original.row_heights[1]
    assert fitted.cells[1].content == text
    assert fitted.cells[1].uuid == original.cells[1].uuid
    assert fit_schematic_table(fitted, word_wrap=True, max_column_width_mm=25.4) == fitted
    assert_text_fits(fitted)
    unwrapped = fit_schematic_table(fitted, word_wrap=False)
    assert unwrapped.column_widths[0] > fitted.column_widths[0]
    assert unwrapped.row_heights[1] < fitted.row_heights[1]


def test_long_unbroken_word_expands_column_instead_of_clipping():
    text = "LONG_UNBROKEN_IDENTIFIER_WITHOUT_SPACES"
    table = create_schematic_table([[text]], at=(0, 0), word_wrap=True, max_column_width_mm=10)
    assert table.column_widths[0] > 10
    assert_text_fits(table)


def test_wrapping_parameters_reach_creation_and_reject_invalid_limit():
    rows = [["Power supply with several words that need wrapping"]]
    table = create_schematic_table(rows, at=(0, 0), word_wrap=True, max_column_width_mm=15.24)
    assert table.column_widths[0] <= 15.24
    assert table.row_heights[0] > 5.08
    assert_text_fits(table)
    with pytest.raises(ValueError):
        create_schematic_table(rows, at=(0, 0), word_wrap=True, max_column_width_mm=0)


def test_user_saved_table_refit_preserves_fields_and_rounds_all_corners():
    from pathlib import Path

    source = (
        Path(__file__).parents[1] / "fixtures/schematic_tables/user_saved_grid_reference.kicad_sch"
    )
    original = Schematic.from_file(source).tables[0]
    before = original.model_dump()
    # Use a supplied stroke font after removing only the fixture's machine font name.
    from eedesigncore.common.font import default_font

    sample = original.model_copy(deep=True)
    for cell in sample.cells:
        cell.attributes.font_face = None
    result = fit_schematic_table(sample, font=default_font(), word_wrap=True)
    assert original.model_dump() == before
    assert [cell.content for cell in result.cells] == [cell.content for cell in original.cells]
    assert [cell.uuid for cell in result.cells] == [cell.uuid for cell in original.cells]
    assert result.uuid == original.uuid
    for cell in result.cells:
        for value in (*cell.position, *cell.size):
            assert value / 1.27 == pytest.approx(round(value / 1.27))
    assert_text_fits(result)
