"""Update PCB from Schematic: the KiCad 10 dialog behaviours, as opt-in options.

Each test names the KiCad 10.0.3 source line it follows. The guarded defaults
stay as they were; ``tests/unit/test_design_updates.py`` covers them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore import (
    Footprint,
    LibTable,
    LibTableRow,
    PcbUpdateOptions,
    load_update_footprints,
    plan_update_pcb_from_schematic,
    resolve_lib_table,
    update_pcb_from_schematic,
)
from eedesigncore.common.pcb_family import PcbGroup
from eedesigncore.pcb.models import PcbGenerator, TrackSegment
from tests.unit.test_design_updates import design, replacement, uid


def _assign(sch: object, footprint: str) -> None:
    field = next(f for f in sch.symbols[0].fields if f.name == "Footprint")  # type: ignore[attr-defined]
    field.value = footprint


def _codes(issues: list) -> set[str]:
    return {issue.code for issue in issues}


# ---------------------------------------------------------------------------
# The preset
# ---------------------------------------------------------------------------


def test_the_dialog_preset_keeps_the_dialog_defaults() -> None:
    """``dialog_update_pcb_base.cpp``: replace and update-fields on, the rest off."""
    preset = PcbUpdateOptions.kicad_dialog()
    assert preset.replace_footprints and preset.update_fields
    assert not preset.delete_extra_footprints and not preset.override_locks
    assert not preset.match_by_reference and not preset.remove_extra_fields
    assert not preset.transfer_symbol_groups
    assert not preset.preserve_routed_contacts
    assert preset.delete_grouped_footprints and preset.skip_blocked_parts


def test_the_dialog_preset_takes_check_box_changes() -> None:
    preset = PcbUpdateOptions.kicad_dialog(delete_extra_footprints=True)
    assert preset.delete_extra_footprints and preset.skip_blocked_parts


def test_the_dialog_preset_lets_a_caller_turn_one_behaviour_off() -> None:
    preset = PcbUpdateOptions.kicad_dialog(skip_blocked_parts=False)
    assert not preset.skip_blocked_parts and preset.delete_grouped_footprints


def test_the_guarded_defaults_do_not_change() -> None:
    defaults = PcbUpdateOptions()
    assert defaults.preserve_routed_contacts
    assert not defaults.delete_grouped_footprints
    assert not defaults.skip_blocked_parts


# ---------------------------------------------------------------------------
# Skip a blocked part and apply the rest (board_netlist_updater.cpp:2156-2180)
# ---------------------------------------------------------------------------


def test_a_locked_replacement_keeps_the_old_footprint_and_still_updates_it() -> None:
    board, sch = design()
    board.footprints[0].locked = True
    _assign(sch, "Test:New")
    libraries = {"Test:New": replacement()}

    blocked = plan_update_pcb_from_schematic(board, sch, library_footprints=libraries)
    assert "locked_replacement" in _codes(blocked.blockers)

    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints=libraries,
        options=PcbUpdateOptions(skip_blocked_parts=True),
    )
    kept = result.board.footprints[0]
    assert kept.fp_id == "Test:Package"
    assert next(f.value for f in kept.properties if f.name == "Value") == "Schematic value"
    assert "locked_replacement" in _codes(result.issues)
    assert all(issue.severity == "warning" for issue in result.issues)


def test_a_missing_definition_keeps_the_old_footprint() -> None:
    board, sch = design()
    _assign(sch, "Test:New")
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(skip_blocked_parts=True)
    )
    assert result.board.footprints[0].fp_id == "Test:Package"
    assert "missing_definition" in _codes(result.issues)
    assert not any(change.kind == "replace_footprint" for change in result.changes)


def test_a_missing_footprint_field_keeps_the_old_footprint() -> None:
    board, sch = design()
    _assign(sch, "")
    blocked = plan_update_pcb_from_schematic(board, sch)
    assert "missing_footprint" in _codes(blocked.blockers)
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(skip_blocked_parts=True)
    )
    assert result.board.footprints[0].fp_id == "Test:Package"
    assert "missing_footprint" in _codes(result.issues)


def test_a_kept_footprint_still_gets_its_pad_nets() -> None:
    board, sch = design()
    board.footprints[0].locked = True
    _assign(sch, "Test:New")
    sch.labels[1].content = "NET_C"
    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints={"Test:New": replacement()},
        options=PcbUpdateOptions(skip_blocked_parts=True),
    )
    assert [pad.net_name for pad in result.board.footprints[0].pads] == ["NET_A", "NET_C"]


def test_a_definition_with_another_identifier_keeps_the_old_footprint() -> None:
    board, sch = design()
    _assign(sch, "Test:New")
    wrong = replacement()
    wrong.fp_id = "Other"
    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints={"Test:New": wrong},
        options=PcbUpdateOptions(skip_blocked_parts=True),
    )
    assert result.board.footprints[0].fp_id == "Test:Package"
    assert "definition_identity" in _codes(result.issues)


def test_a_new_part_without_a_placement_is_skipped() -> None:
    board, sch = design()
    board.footprints.clear()
    package = replacement()
    package.fp_id = "Package"
    result = update_pcb_from_schematic(
        board,
        sch,
        library_footprints={"Test:Package": package},
        options=PcbUpdateOptions(skip_blocked_parts=True),
    )
    assert result.board.footprints == []
    assert "missing_placement" in _codes(result.issues)


def test_a_pin_without_a_pad_is_a_warning_with_skip_on() -> None:
    """KiCad reports it as an error and still applies (board_netlist_updater.cpp:1939-1947)."""
    board, sch = design()
    board.footprints[0].pads.pop()
    blocked = plan_update_pcb_from_schematic(board, sch)
    assert "missing_pad" in _codes(blocked.blockers)
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(skip_blocked_parts=True)
    )
    assert "missing_pad" in _codes(result.issues)
    assert [pad.net_name for pad in result.board.footprints[0].pads] == ["NET_A"]


def test_a_new_part_without_a_definition_is_skipped() -> None:
    board, sch = design()
    board.footprints.clear()
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(skip_blocked_parts=True)
    )
    assert result.board.footprints == []
    assert "missing_definition" in _codes(result.issues)


def test_a_board_wide_problem_still_blocks_with_skip_on() -> None:
    """Skipping is for one part. Duplicate footprint UUIDs still block the board."""
    board, sch = design()
    board.footprints.append(board.footprints[0].model_copy(deep=True))
    plan = plan_update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(skip_blocked_parts=True)
    )
    assert "duplicate_footprint_uuid" in _codes(plan.blockers)


# ---------------------------------------------------------------------------
# Delete a grouped footprint (board_commit.cpp:352-373)
# ---------------------------------------------------------------------------


def _extra(board: object, name: str) -> Footprint:
    extra = Footprint(uuid=uid(name), fp_id="Mechanical:Extra")
    board.footprints.append(extra)  # type: ignore[attr-defined]
    return extra


def test_a_grouped_extra_footprint_still_blocks_by_default() -> None:
    board, sch = design()
    extra = _extra(board, "grouped-extra")
    board.groups.append(PcbGroup(id=uid("group"), members=[extra.uuid or ""]))
    plan = plan_update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions(delete_extra_footprints=True)
    )
    assert "group_deletion" in _codes(plan.blockers)


def test_a_grouped_extra_footprint_leaves_its_group_when_asked() -> None:
    board, sch = design()
    extra = _extra(board, "grouped-extra")
    keeper = _extra(board, "kept-member")
    keeper.attributes.board_only = True
    board.groups.append(
        PcbGroup(id=uid("group"), name="G", members=[extra.uuid or "", keeper.uuid or ""])
    )
    result = update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, delete_grouped_footprints=True),
    )
    assert extra.uuid not in {fp.uuid for fp in result.board.footprints}
    assert result.board.groups[0].members == [keeper.uuid]
    assert "grouped_deletion" in _codes(result.issues)
    assert {"delete_footprint", "ungroup_footprint"} <= {c.kind for c in result.changes}


def test_an_emptied_group_is_removed_and_leaves_its_parent() -> None:
    """KiCad writes no group without members (``pcb_io_kicad_sexpr.cpp:2482``)."""
    board, sch = design()
    extra = _extra(board, "only-member")
    keeper = _extra(board, "outer-member")
    keeper.attributes.board_only = True
    inner = PcbGroup(id=uid("inner"), name="inner", members=[extra.uuid or ""])
    outer = PcbGroup(id=uid("outer"), name="outer", members=[inner.id or "", keeper.uuid or ""])
    board.groups.extend([inner, outer])
    result = update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, delete_grouped_footprints=True),
    )
    assert [group.id for group in result.board.groups] == [outer.id]
    assert result.board.groups[0].members == [keeper.uuid]
    assert "remove_group" in {change.kind for change in result.changes}


def test_a_group_without_an_identifier_is_removed_when_emptied() -> None:
    board, sch = design()
    extra = _extra(board, "nameless-member")
    board.groups.append(PcbGroup(name="nameless", members=[extra.uuid or ""]))
    result = update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, delete_grouped_footprints=True),
    )
    assert result.board.groups == []


def test_a_generator_that_names_an_emptied_group_blocks() -> None:
    board, sch = design()
    extra = _extra(board, "in-generator-group")
    group = PcbGroup(id=uid("inner"), members=[extra.uuid or ""])
    board.groups.append(group)
    board.generators.append(PcbGenerator(uuid=uid("generator"), members=[group.id or ""]))
    plan = plan_update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, delete_grouped_footprints=True),
    )
    assert "group_deletion" in _codes(plan.blockers)


def test_a_generator_member_still_blocks() -> None:
    board, sch = design()
    extra = _extra(board, "generator-member")
    board.generators.append(PcbGenerator(uuid=uid("generator"), members=[extra.uuid or ""]))
    plan = plan_update_pcb_from_schematic(
        board,
        sch,
        options=PcbUpdateOptions(delete_extra_footprints=True, delete_grouped_footprints=True),
    )
    assert "group_deletion" in _codes(plan.blockers)


def test_the_dialog_preset_deletes_a_routed_grouped_footprint() -> None:
    """The KiCad dialog deletes it and leaves the copper (board_netlist_updater.cpp:2295)."""
    board, sch = design()
    extra = _extra(board, "routed-grouped")
    from eedesigncore import Pad

    extra.pads = [Pad(number="1", id=uid("routed-pad"), layers=["F.Cu"], net_name="NET_A")]
    board.tracks.append(TrackSegment(start=(0, 0), end=(1, 0), net="NET_A"))
    board.groups.append(PcbGroup(id=uid("group"), members=[extra.uuid or ""]))
    result = update_pcb_from_schematic(
        board, sch, options=PcbUpdateOptions.kicad_dialog(delete_extra_footprints=True)
    )
    assert extra.uuid not in {fp.uuid for fp in result.board.footprints}
    assert len(result.board.tracks) == 1
    assert {"routed_deletion", "grouped_deletion"} <= _codes(result.issues)


# ---------------------------------------------------------------------------
# Load definitions through the library table (board_netlist_updater.cpp:326)
# ---------------------------------------------------------------------------


def _project_with_library(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    library = project / "Test.pretty"
    library.mkdir(parents=True)
    footprint = replacement()
    footprint.fp_id = "New"
    footprint.to_file(library / "New.kicad_mod")
    LibTable(
        kind="fp",
        version=7,
        rows=[LibTableRow(name="Test", type="KiCad", uri="${KIPRJMOD}/Test.pretty")],
    ).to_file(project / "fp-lib-table")
    (tmp_path / "settings").mkdir()
    return project


def _table(tmp_path: Path, project: Path):  # noqa: ANN202
    return resolve_lib_table(
        "fp",
        project_dir=project,
        settings_dir=tmp_path / "settings",
        kicad_root=tmp_path / "install",
        env={},
    )


def test_definitions_load_through_the_library_table(tmp_path: Path) -> None:
    project = _project_with_library(tmp_path)
    board, sch = design()
    _assign(sch, "Test:New")
    loaded = load_update_footprints(sch, _table(tmp_path, project))
    assert set(loaded.footprints) == {"Test:New"}
    assert loaded.missing == {}

    result = update_pcb_from_schematic(board, sch, library_footprints=loaded.footprints)
    assert result.board.footprints[0].fp_id == "Test:New"
    assert "replace_footprint" in {change.kind for change in result.changes}


def test_footprints_not_in_the_table_are_reported(tmp_path: Path) -> None:
    project = _project_with_library(tmp_path)
    _, sch = design()
    table = _table(tmp_path, project)
    for lib_id, reason in (
        ("Test:Package", "no footprint 'Package'"),
        ("Test:new", "no footprint 'new'"),
        ("Other:Part", "no library 'Other'"),
        ("Absent", "No library holds"),
        ("Test:../escape", "not a plain file name"),
        ("Test:Host:ads", "not a plain file name"),
        (":New", "empty library name"),
    ):
        _assign(sch, lib_id)
        loaded = load_update_footprints(sch, table)
        assert loaded.footprints == {}
        assert reason in loaded.missing[lib_id], lib_id


def _with_row(table, **update):  # noqa: ANN001, ANN202
    row = table.libraries[0].model_copy(update=update)
    return table.model_copy(update={"libraries": [row]})


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"disabled": True}, "disabled"),
        ({"type": "Legacy"}, "not KiCad"),
        ({"read_roots": [Path("Z:/nowhere")]}, "outside the allowed folders"),
    ],
)
def test_a_refused_library_row_is_reported(tmp_path: Path, update: dict, reason: str) -> None:
    project = _project_with_library(tmp_path)
    _, sch = design()
    _assign(sch, "Test:New")
    loaded = load_update_footprints(sch, _with_row(_table(tmp_path, project), **update))
    assert reason in loaded.missing["Test:New"]


def test_an_unreadable_or_large_file_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_library(tmp_path)
    _, sch = design()
    _assign(sch, "Test:New")
    table = _table(tmp_path, project)
    monkeypatch.setattr("eedesigncore.pcb.update_library.MAX_LIBRARY_SCAN_BYTES", 10)
    assert "larger than" in load_update_footprints(sch, table).missing["Test:New"]
    monkeypatch.undo()
    (project / "Test.pretty" / "New.kicad_mod").write_text("(footprint", encoding="utf-8")
    assert "could not be read" in load_update_footprints(sch, table).missing["Test:New"]


def test_a_name_without_a_library_is_searched_for_as_kicad_does(tmp_path: Path) -> None:
    """``footprint_library_adapter.cpp:334-353``: every library, alphabetical order."""
    project = _project_with_library(tmp_path)
    second = project / "Aaa.pretty"
    second.mkdir()
    early = replacement()
    early.fp_id = "New"
    early.properties[1].value = "From Aaa"
    early.to_file(second / "New.kicad_mod")
    LibTable(
        kind="fp",
        version=7,
        rows=[
            LibTableRow(name="Test", type="KiCad", uri="${KIPRJMOD}/Test.pretty"),
            LibTableRow(name="Aaa", type="KiCad", uri="${KIPRJMOD}/Aaa.pretty"),
        ],
    ).to_file(project / "fp-lib-table")
    _, sch = design()
    _assign(sch, "New")
    loaded = load_update_footprints(sch, _table(tmp_path, project))
    value = next(p.value for p in loaded.footprints["New"].properties if p.name == "Value")
    assert value == "From Aaa"
    assert "'Aaa'" in loaded.notes["New"]


def test_a_board_footprint_matches_a_name_without_a_library() -> None:
    """``board_netlist_updater.cpp:2105-2112``: compare the item name only."""
    board, sch = design()
    _assign(sch, "Package")
    result = update_pcb_from_schematic(board, sch)
    assert result.board.footprints[0].fp_id == "Test:Package"
    assert not any(change.kind == "replace_footprint" for change in result.changes)
    assert "footprint_without_library" in _codes(result.issues)


def test_a_symbol_table_is_refused(tmp_path: Path) -> None:
    project = _project_with_library(tmp_path)
    _, sch = design()
    table = _table(tmp_path, project).model_copy(update={"kind": "sym"})
    with pytest.raises(ValueError, match="'fp' library table"):
        load_update_footprints(sch, table)
