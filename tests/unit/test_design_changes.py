"""Labelled change records between two designs, their text views, and the command.

The flip pair is a real KiCad 10 save: the same board before and after KiCad
flipped connector J1. Every other case edits a loaded model in a known way and
checks that exactly that edit comes back, with the expected category.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    DesignUpdateChange,
    Footprint,
    Schematic,
    SchematicElectricalChange,
    SchematicElectricalComparison,
    SymbolLib,
    changes_from_schematic_electrical,
    changes_from_update,
    compare_content,
    compare_designs,
    format_change_diff,
    format_change_report,
    load_design,
)
from eedesigncore.design_changes import CHANGE_FORMAT, DesignChange
from eedesigncore.design_changes.__main__ import main
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.models import TrackSegment

FIXTURES = Path(__file__).parent.parent / "fixtures"
FLIP_BASE = FIXTURES / "pcb" / "flip_oracle_adapter_base.kicad_pcb"
FLIP_J1 = FIXTURES / "pcb" / "flip_oracle_adapter_j1_flipped.kicad_pcb"
RESISTOR = FIXTURES / "mod" / "R_1206_3216Metric.kicad_mod"
WIRES = FIXTURES / "sch" / "t3a_wire_bus_globallabel.kicad_sch"
SYMBOLS = FIXTURES / "sch" / "placed_symbols.kicad_sch"
POWER = FIXTURES / "sym" / "power.kicad_sym"


def _field(footprint: Footprint, name: str) -> object:
    return next(prop for prop in footprint.properties if prop.name == name)


def small_board() -> Board:
    """R12 with pad 1 on GND and pad 2 on VCC, and three 5 mm GND tracks."""
    footprint = Footprint.from_file(RESISTOR)
    footprint.at = FootprintPlacement(position=(10.0, 10.0), angle=0.0)
    footprint.uuid = str(uuid.UUID(int=1))
    _field(footprint, "Reference").value = "R12"  # type: ignore[attr-defined]
    for pad in footprint.pads:
        pad.net_name = "GND" if pad.number == "1" else "VCC"
    tracks = [
        TrackSegment(
            start=(0.0, float(row)),
            end=(5.0, float(row)),
            layer="F.Cu",
            net="GND",
            width=0.2,
            uuid=str(uuid.UUID(int=100 + row)),
        )
        for row in range(3)
    ]
    return Board(footprints=[footprint], tracks=list(tracks))


def edited(board: Board) -> Board:
    """Remove one track, move R12, put pad 2 on VBUS, and change the Value."""
    after = board.model_copy(deep=True)
    after.tracks.pop(0)
    footprint = after.footprints[0]
    assert footprint.at is not None
    footprint.at.position = (10.0, 12.0)
    footprint.pads[1].net_name = "VBUS"
    _field(footprint, "Value").value = "10k"  # type: ignore[attr-defined]
    return after


def by_field(changes: tuple[DesignChange, ...]) -> dict[tuple[str, str], DesignChange]:
    return {(change.item, change.field): change for change in changes}


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [FLIP_BASE, RESISTOR, WIRES, SYMBOLS])
def test_a_file_read_twice_is_equal(path: Path) -> None:
    result = compare_designs(path, path)
    assert result.equal
    assert result.changes == ()
    assert result.before_signature == result.after_signature


def test_a_copy_of_a_model_is_equal() -> None:
    board = small_board()
    assert compare_designs(board, board.model_copy(deep=True)).equal


# ---------------------------------------------------------------------------
# One record per edit, with its category
# ---------------------------------------------------------------------------


def test_each_board_edit_is_one_labelled_record() -> None:
    result = compare_designs(small_board(), edited(small_board()))
    found = by_field(result.changes)

    moved = found[("R12", "at.position")]
    assert (moved.category, moved.kind, moved.identity) == (
        "geometry",
        "changed",
        str(uuid.UUID(int=1)),
    )
    assert json.loads(moved.after or "") == [10, 12]

    pad = found[("R12 pad 2", "net_name")]
    assert (pad.category, pad.before, pad.after) == ("electrical", '"VCC"', '"VBUS"')

    value = found[("R12 field Value", "value")]
    assert (value.category, value.after) == ("part", '"10k"')

    removed = found[("track on GND", "")]
    assert (removed.category, removed.kind) == ("electrical", "removed")
    assert removed.identity == str(uuid.UUID(int=100))

    rename = found[("net VCC", "name")]
    assert (rename.item_type, rename.before, rename.after) == ("Net", '"VCC"', '"VBUS"')

    routing = found[("net GND", "routing")]
    assert json.loads(routing.before or "")["segments"] == 3
    assert json.loads(routing.after or "")["segments"] == 2
    assert json.loads(routing.after or "")["track_length_mm"] == pytest.approx(10.0)

    assert len(result.changes) == 6
    assert result.counts() == {"electrical": 4, "part": 1, "geometry": 1}


def test_the_net_summary_can_be_left_out() -> None:
    result = compare_designs(small_board(), edited(small_board()), nets=False)
    assert all(change.item_type != "Net" for change in result.changes)
    assert len(result.changes) == 4


def test_a_pad_net_moved_to_another_existing_net_changes_both_pad_lists() -> None:
    after = small_board()
    after.footprints[0].pads[1].net_name = "GND"
    found = by_field(compare_designs(small_board(), after).changes)
    assert json.loads(found[("net GND", "pads")].after or "") == ["R12.1", "R12.2"]
    assert found[("net VCC", "")].kind == "removed"


def test_tolerance_hides_a_sub_micron_move_and_exact_shows_it() -> None:
    before = small_board()
    after = before.model_copy(deep=True)
    after.tracks[0].end = (5.0000004, 0.0)
    assert not compare_designs(before, after).equal
    hidden = compare_designs(before, after, tolerance=1e-6)
    assert hidden.equal
    assert hidden.before_signature != hidden.after_signature


def test_a_real_flip_names_only_the_flipped_part() -> None:
    result = compare_designs(FLIP_BASE, FLIP_J1)
    assert result.changes
    assert {change.item.split(" ")[0] for change in result.changes} == {"J1"}
    assert "other" not in result.counts()
    pad_layers = [c for c in result.changes if c.item_type == "Pad" and c.field == "layers"]
    assert len(pad_layers) == 10
    assert all(c.category == "electrical" for c in pad_layers)
    assert '"B.Cu"' in (pad_layers[0].after or "")
    assert not any(change.item_type == "Net" for change in result.changes)


def test_schematic_edits_take_their_categories() -> None:
    before = Schematic.from_file(WIRES)
    after = before.model_copy(deep=True)
    wire = next(line for line in after.lines if line.line_type.value == "wire")
    wire.end = (wire.end[0] + 2.54, wire.end[1])
    after.labels[0].content = "RENAMED"
    found = {(c.item_type, c.field): c for c in compare_designs(before, after).changes}
    assert found[("Line", "end")].category == "electrical"
    assert found[("Line", "end")].item.startswith("wire ")
    assert found[("Label", "content")].category == "electrical"
    assert found[("Label", "content")].after == '"RENAMED"'


def test_a_symbol_value_is_a_part_change() -> None:
    before = Schematic.from_file(SYMBOLS)
    after = before.model_copy(deep=True)
    value = next(prop for prop in after.symbols[0].fields if prop.name == "Value")
    value.value = "48P"
    (change,) = compare_designs(before, after).changes
    assert (change.category, change.item, change.field) == ("part", "P1 field Value", "value")


def test_library_symbol_order_is_ignored() -> None:
    before = SymbolLib.from_file(POWER)
    after = before.model_copy(deep=True)
    after.symbols.reverse()
    assert compare_designs(before, after).equal


def test_a_new_order_of_an_ordered_list_is_one_cosmetic_change() -> None:
    """Ids that are not UUIDs make the board lists ordered, as in ``compare_content``."""
    first, second = small_board().footprints[0], small_board().footprints[0]
    first.uuid, second.uuid = "part-a", "part-b"
    _field(second, "Reference").value = "R13"  # type: ignore[attr-defined]
    before = Board(footprints=[first, second])
    after = Board(footprints=[second.model_copy(deep=True), first.model_copy(deep=True)])
    (change,) = compare_designs(before, after, nets=False).changes
    assert (change.category, change.field) == ("cosmetic", "footprints.order")
    assert json.loads(change.after or "") == ["part-b", "part-a"]


def test_a_symbol_added_to_a_library_is_one_record() -> None:
    before = SymbolLib.from_file(POWER)
    after = before.model_copy(deep=True)
    extra = after.symbols[0].model_copy(deep=True)
    extra.name = "EXTRA_POWER"
    after.symbols.append(extra)
    (change,) = compare_designs(before, after).changes
    assert (change.kind, change.item, change.category) == ("added", "symbol EXTRA_POWER", "part")


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_two_types_are_refused() -> None:
    with pytest.raises(TypeError):
        compare_designs(small_board(), Schematic())


@pytest.mark.parametrize("tolerance", [-1.0, float("nan"), float("inf")])
def test_a_bad_tolerance_is_refused(tolerance: float) -> None:
    with pytest.raises(ValueError):
        compare_designs(small_board(), small_board(), tolerance=tolerance)


def test_an_unknown_suffix_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_design(tmp_path / "notes.txt")


def test_compare_content_is_unchanged_by_the_new_view() -> None:
    """The older view still reports pad 2, which has no stored id, as removed and added."""
    before, after = small_board(), edited(small_board())
    kinds = sorted(change.kind for change in compare_content(before, after).changes)
    assert kinds == ["added", "changed", "changed", "removed", "removed"]


# ---------------------------------------------------------------------------
# Filters and text views
# ---------------------------------------------------------------------------


def test_only_keeps_the_named_categories() -> None:
    result = compare_designs(small_board(), edited(small_board())).only("part")
    assert [change.category for change in result.changes] == ["part"]
    with pytest.raises(ValueError):
        result.only("noise")  # type: ignore[arg-type]


def test_json_carries_the_format_and_parsed_values() -> None:
    data = json.loads(compare_designs(small_board(), edited(small_board())).to_json())
    assert data["format"] == CHANGE_FORMAT
    assert data["equal"] is False
    pad = next(c for c in data["changes"] if c["item"] == "R12 pad 2")
    assert (pad["before"], pad["after"]) == ("VCC", "VBUS")


def test_report_and_diff_print_the_same_records() -> None:
    result = compare_designs(small_board(), edited(small_board()))
    report = format_change_report(result)
    assert report.splitlines()[0] == "6 changes: 4 electrical, 1 part, 1 geometry"
    assert '  R12 pad 2: net_name "VCC" -> "VBUS"' in report
    assert "  removed track on GND" in report

    diff = format_change_diff(result, before_label="a/x.kicad_pcb", after_label="b/x.kicad_pcb")
    lines = diff.splitlines()
    assert lines[:2] == ["--- a/x.kicad_pcb", "+++ b/x.kicad_pcb"]
    assert '- net_name: "VCC"  [electrical]' in lines
    assert '+ net_name: "VBUS"  [electrical]' in lines
    assert "- track on GND  [electrical]" in lines


def test_an_equal_result_prints_no_changes() -> None:
    result = compare_designs(small_board(), small_board())
    assert format_change_report(result) == "No changes.\n"
    assert format_change_diff(result).splitlines() == ["--- a", "+++ b"]


def test_long_values_are_clipped() -> None:
    result = compare_designs(small_board(), edited(small_board()))
    assert all(
        len(line) < 140 for line in format_change_report(result, value_limit=20).splitlines()
    )


# ---------------------------------------------------------------------------
# Command line and git diff form
# ---------------------------------------------------------------------------


def test_command_prints_a_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FLIP_BASE), str(FLIP_J1), "--report", "--category", "electrical"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("10 changes: 10 electrical")


def test_command_exit_code_marks_a_change(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FLIP_BASE), str(FLIP_J1), "--exit-code", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["format"] == CHANGE_FORMAT
    assert main([str(FLIP_BASE), str(FLIP_BASE), "--exit-code"]) == 0


def test_git_form_reads_files_without_a_suffix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old_blob", tmp_path / "new_blob"
    old.write_bytes(FLIP_BASE.read_bytes())
    new.write_bytes(FLIP_J1.read_bytes())
    args = ["board.kicad_pcb", str(old), "0" * 40, "100644", str(new), "1" * 40, "100644"]
    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["--- a/board.kicad_pcb", "+++ b/board.kicad_pcb"]
    assert any(line.startswith("@@ J1 pad") for line in lines)


def test_git_form_reports_an_added_file(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["board.kicad_pcb", "/dev/null", ".", ".", str(FLIP_BASE), "1" * 40, "100644"]
    assert main(args) == 0
    assert capsys.readouterr().out == "board.kicad_pcb: file added\n"


def test_command_refuses_a_wrong_argument_count() -> None:
    with pytest.raises(SystemExit):
        main(["one", "two", "three"])


# ---------------------------------------------------------------------------
# Adapters for the older change records
# ---------------------------------------------------------------------------


def test_update_changes_translate() -> None:
    changes = [
        DesignUpdateChange(
            kind="delete_footprint", reference="R7", identity="u-7", before="Lib:R", after=None
        ),
        DesignUpdateChange(
            kind="rename_copper_net", reference="", identity="A", before="A", after="B"
        ),
        DesignUpdateChange(kind="new_kind", reference="", identity="x", before='{"a": 1}'),
    ]
    first, second, third = changes_from_update(changes)
    assert (first.category, first.kind, first.item, first.before) == (
        "part",
        "removed",
        "R7",
        '"Lib:R"',
    )
    assert (second.category, second.after) == ("electrical", '"B"')
    assert (third.category, third.before) == ("other", json.dumps('{"a": 1}'))


def test_schematic_electrical_changes_translate() -> None:
    comparison = SchematicElectricalComparison.model_construct(
        changes=[
            SchematicElectricalChange(
                category="net_names", identity="pins", before_json='["A"]', after_json='["B"]'
            ),
            SchematicElectricalChange(category="parts", identity="R1", before_json=None),
        ]
    )
    nets, parts = changes_from_schematic_electrical(comparison)
    assert (nets.category, nets.field, nets.after) == ("electrical", "net_names", '["B"]')
    assert (parts.category, parts.before) == ("part", None)


# ---------------------------------------------------------------------------
# Review findings, one test each
# ---------------------------------------------------------------------------


def test_a_library_footprint_line_is_its_own_item() -> None:
    before = Footprint.from_file(RESISTOR)
    after = before.model_copy(deep=True)
    after.drawings[0].stroke.width = 0.2  # type: ignore[union-attr]
    (change,) = compare_designs(before, after).changes
    assert change.item == f"R_1206_3216Metric {type(after.drawings[0]).__name__}"
    assert (change.kind, change.field, change.category) == ("changed", "stroke.width", "geometry")


def test_a_net_added_to_the_board_net_list_is_electrical() -> None:
    (change,) = compare_designs(Board(nets=["A"]), Board(nets=["A", "B"])).changes
    assert (change.category, change.kind, change.field, change.after) == (
        "electrical",
        "added",
        "nets",
        '"B"',
    )


def test_a_removed_symbol_drawing_names_its_own_type() -> None:
    before = SymbolLib.from_file(POWER)
    after = before.model_copy(deep=True)
    unit = next(
        unit
        for symbol in after.symbols
        for unit in symbol.units
        if any(type(item).__name__ != "Pin" for item in unit.items)
    )
    drawing = next(item for item in unit.items if type(item).__name__ != "Pin")
    unit.items.remove(drawing)
    (change,) = compare_designs(before, after).changes
    assert change.kind == "removed"
    assert change.item_type == type(drawing).__name__
    assert change.category == "geometry"


def test_many_moved_tracks_compare_in_linear_time() -> None:
    import time

    count = 3000
    before = Board(
        tracks=[
            TrackSegment(
                start=(0.0, row * 0.5),
                end=(5.0, row * 0.5),
                layer="F.Cu",
                net="N",
                width=0.2,
                uuid=str(uuid.UUID(int=row + 1)),
            )
            for row in range(count)
        ]
    )
    after = before.model_copy(deep=True)
    for track in after.tracks:
        track.end = (6.0, track.end[1])  # type: ignore[union-attr]
    started = time.perf_counter()
    result = compare_designs(before, after, nets=False)
    elapsed = time.perf_counter() - started
    assert len(result.changes) == count
    assert elapsed < 10.0, f"{count} moved tracks took {elapsed:.1f} s"


def test_a_new_symbol_field_is_one_added_part_record() -> None:
    before = Schematic.from_file(SYMBOLS)
    after = before.model_copy(deep=True)
    fields = after.symbols[0].fields
    extra = fields[1].model_copy(deep=True)
    extra.name, extra.value = "Supplier", "Acme"
    fields.append(extra)
    next(prop for prop in fields if prop.name == "Value").value = "48P"
    found = by_field(compare_designs(before, after).changes)
    assert found[("P1 field Supplier", "")].kind == "added"
    assert found[("P1 field Supplier", "")].category == "part"
    assert found[("P1 field Value", "value")].after == '"48P"'
    assert len(found) == 2


def test_a_pad_with_a_new_identifier_is_removed_and_added() -> None:
    before = Board.from_file(FLIP_BASE)
    after = before.model_copy(deep=True)
    after.footprints[0].pads[0].id = str(uuid.UUID(int=7))
    kinds = sorted(c.kind for c in compare_designs(before, after, nets=False).changes)
    assert kinds == ["added", "removed"]


def test_a_new_reference_alone_changes_no_net() -> None:
    before = Board.from_file(FLIP_BASE)
    after = before.model_copy(deep=True)
    _field(after.footprints[0], "Reference").value = "J99"  # type: ignore[attr-defined]
    changes = compare_designs(before, after).changes
    assert [c.category for c in changes] == ["part"]


def test_update_text_values_stay_text() -> None:
    change = DesignUpdateChange(
        kind="rename_copper_net", reference="", identity="n", before="12", after="NaN"
    )
    (record,) = changes_from_update([change])
    assert (record.before, record.after) == ('"12"', '"NaN"')
    data = json.loads(
        compare_designs(small_board(), small_board()).to_json()
    )  # strict JSON still parses
    assert data["equal"] is True


def test_a_schematic_electrical_addition_is_added() -> None:
    comparison = SchematicElectricalComparison.model_construct(
        changes=[
            SchematicElectricalChange(
                category="parts", identity="R9", before_json="null", after_json='"R9"'
            )
        ]
    )
    (record,) = changes_from_schematic_electrical(comparison)
    assert record.kind == "added"


def test_a_tolerance_of_one_or_more_is_refused() -> None:
    with pytest.raises(ValueError):
        compare_designs(small_board(), small_board(), tolerance=1.0)


def test_the_report_shows_the_value_of_an_added_field() -> None:
    before = small_board()
    after = before.model_copy(deep=True)
    after.footprints[0].pads[0].solder_mask_margin = 0.05
    report = format_change_report(compare_designs(before, after))
    assert "R12 pad 1: solder_mask_margin " in report
    assert "0.05" in report


def test_control_characters_in_names_are_escaped() -> None:
    """A net name read from a file must not start a false diff line or reach the terminal."""
    hostile = "N" + chr(10) + "+ fake" + chr(27) + "[31m"
    before = small_board()
    for track in before.tracks:
        track.net = hostile
    after = before.model_copy(deep=True)
    after.tracks.pop()
    result = compare_designs(before, after, nets=False)
    escaped = "N" + chr(92) + "x0a+ fake" + chr(92) + "x1b[31m"
    for text in (format_change_diff(result), format_change_report(result)):
        assert chr(27) not in text
        assert chr(10) + "+ fake" not in text
        assert escaped in text


def test_git_rename_form_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "old.kicad_pcb",
        str(FLIP_BASE),
        "0" * 40,
        "100644",
        str(FLIP_J1),
        "1" * 40,
        "100644",
        "new.kicad_pcb",
        "similarity index 90%",
    ]
    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["--- a/old.kicad_pcb", "+++ b/new.kicad_pcb"]


def test_git_unmerged_form_is_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["board.kicad_pcb"]) == 0
    assert capsys.readouterr().out == "board.kicad_pcb: unmerged\n"


def test_an_unreadable_file_exits_2_outside_git(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.kicad_pcb"
    broken.write_text("(kicad_pcb (version", encoding="utf-8")
    assert main([str(broken), str(FLIP_BASE), "--exit-code"]) == 2
    assert "cannot compare" in capsys.readouterr().err


def test_an_unreadable_file_lets_git_continue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "blob"
    broken.write_text("<<<<<<< HEAD\n", encoding="utf-8")
    args = ["board.kicad_pcb", str(broken), "0" * 40, "100644", str(FLIP_BASE), "1" * 40, "100644"]
    assert main(args) == 0
    assert capsys.readouterr().out.startswith("board.kicad_pcb: cannot compare:")


@pytest.mark.parametrize("value", ["-1", "1", "nan"])
def test_a_bad_command_tolerance_is_an_argument_error(value: str) -> None:
    with pytest.raises(SystemExit):
        main([str(FLIP_BASE), str(FLIP_BASE), "--tolerance", value])
