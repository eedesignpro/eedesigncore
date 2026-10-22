from pathlib import Path

import pytest

from eedesigncore import (
    Board,
    BoardUpdateSelection,
    Footprint,
    GuardedDocument,
    GuardedSaveError,
    Pad,
    Property,
    apply_schematic_board_update,
    inspect_schematic_electrical,
    load_guarded_document,
    plan_schematic_board_update,
    save_guarded_documents,
)
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.models import TrackSegment

FIXTURE = Path(__file__).parents[1] / "fixtures/schematic_preparation/root.kicad_pro"


@pytest.fixture
def sample():
    view = inspect_schematic_electrical(FIXTURE, document_id="board-update-test")
    part = next(part for part in view.parts.values() if part.on_board)
    part.fields["Footprint"] = "Test:Connector"
    pins = [
        pin
        for pin in view.items.values()
        if pin.kind == "pin" and pin.parent_item == part.id and pin.netlist_member
    ]
    assert pins
    pads = [
        Pad(number=number, layers=["B.Cu"], net_name=None)
        for number in sorted({pin.pin_number for pin in pins})
    ]
    pads.append(pads[0].model_copy(deep=True))
    fp = Footprint(
        uuid="00000000-0000-0000-0000-000000000001",
        fp_id="Test:Connector",
        layer="B.Cu",
        locked=True,
        at=FootprintPlacement(position=(12.3, 45.6), angle=90),
        properties=[Property(name="Reference", value=part.reference)],
        pads=pads,
    )
    board = Board(footprints=[fp, Footprint(uuid="00000000-0000-0000-0000-000000000002")])
    selection = BoardUpdateSelection(footprint_uuid=fp.uuid, symbol_id=part.id)
    return board, view, selection, pins


def plan(sample, **kwargs):
    board, view, selection, _ = sample
    return plan_schematic_board_update(board, view, selections=[selection], **kwargs)


def test_explicit_initial_assignments_preserve_all_other_content(sample):
    board, view, selection, _ = sample
    original = board.model_dump()
    proposal = plan(sample)
    assert not proposal.blockers
    updated = apply_schematic_board_update(board, view, proposal)
    assert board.model_dump() == original
    assert updated.footprints[0].path.endswith(view.items[selection.symbol_id].source.source_uuid)
    assert all(pad.net_name for pad in updated.footprints[0].pads)
    assert updated.footprints[0].pads[0].net_name == updated.footprints[0].pads[-1].net_name
    restored = updated.model_copy(deep=True)
    restored.footprints[0].path = board.footprints[0].path
    for pad in restored.footprints[0].pads:
        pad.net_name = None
    assert restored == board
    assert (
        not plan_schematic_board_update(updated, view, selections=[selection])
        .updates[0]
        .pad_updates
    )


def test_no_selection_never_matches_by_reference(sample):
    board, view, _, _ = sample
    proposal = plan_schematic_board_update(board, view, selections=[])
    assert not proposal.updates
    assert apply_schematic_board_update(board, view, proposal) == board


def test_mismatch_requires_explicit_temporary_preservation(sample):
    board, view, selection, _ = sample
    board.footprints[0].fp_id = "Other:Footprint"
    assert any("identifier differs" in item for item in plan(sample).blockers)
    selection.footprint_policy = "preserve_existing"
    proposal = plan(sample)
    assert not proposal.blockers
    assert "Temporary" in proposal.updates[0].notes[0]
    assert (
        apply_schematic_board_update(board, view, proposal).footprints[0].fp_id
        == "Other:Footprint"
    )


@pytest.mark.parametrize("change", ["board", "inspection", "plan"])
def test_stale_or_tampered_plan_rejected(sample, change):
    board, view, _, _ = sample
    proposal = plan(sample)
    if change == "board":
        board.footprints[0].locked = False
    elif change == "inspection":
        view.drawing_signature = "changed"
    else:
        proposal.updates[0].after_path = "/wrong"
    with pytest.raises(ValueError, match="changed"):
        apply_schematic_board_update(board, view, proposal)


def test_existing_copper_blocks_net_change(sample):
    board, view, _, _ = sample
    board.footprints[0].pads[0].net_name = "OLD"
    board.tracks.append(TrackSegment(net="OLD", end=(4, 5)))
    proposal = plan(sample)
    assert any("affects copper" in item for item in proposal.blockers)
    with pytest.raises(ValueError, match="blockers"):
        apply_schematic_board_update(board, view, proposal)


def test_unrelated_copper_preserved(sample):
    board, view, _, _ = sample
    board.tracks.append(TrackSegment(net="OTHER", end=(4, 5)))
    updated = apply_schematic_board_update(board, view, plan(sample))
    assert updated.tracks == board.tracks


def test_association_only_and_explicit_reference_update(sample):
    board, view, selection, _ = sample
    board.footprints[0].properties[0].value = "BOARD_OUTLINE"
    assert plan(sample).blockers
    selection.update_reference = True
    selection.update_pad_nets = False
    proposal = plan(sample)
    assert not proposal.blockers
    updated = apply_schematic_board_update(board, view, proposal)
    assert updated.footprints[0].properties[0].value == view.parts[selection.symbol_id].reference
    assert updated.footprints[0].pads == board.footprints[0].pads


def test_no_connect_does_not_invent_net(sample):
    board, view, _, pins = sample
    pin = pins[0]
    view.no_connect_pins.append(pin.id)
    proposal = plan(sample)
    assert not proposal.blockers
    updated = apply_schematic_board_update(board, view, proposal)
    assert all(
        pad.net_name is None for pad in updated.footprints[0].pads if pad.number == pin.pin_number
    )
    next(pad for pad in board.footprints[0].pads if pad.number == pin.pin_number).net_name = "OLD"
    assert any("No-connect pad" in item for item in plan(sample).blockers)


@pytest.mark.parametrize(
    "case", ["missing_pad", "extra_pad", "missing_uuid", "partial", "duplicate"]
)
def test_invalid_selections_and_missing_evidence_block(sample, case):
    board, view, selection, _ = sample
    selections = [selection]
    if case == "missing_pad":
        board.footprints[0].pads = []
    elif case == "extra_pad":
        board.footprints[0].pads.append(Pad(number="999", layers=["B.Cu"]))
    elif case == "missing_uuid":
        selection.footprint_uuid = "absent"
    elif case == "partial":
        view.coverage.electrical = "partial"
    else:
        selections.append(selection)
    assert plan_schematic_board_update(board, view, selections=selections).blockers


def test_board_guard_noop_save_and_stale_rejection(sample, tmp_path):
    board, _, _, _ = sample
    path = tmp_path / "test.kicad_pcb"
    board.to_file(path)
    entry = load_guarded_document(path)
    before = path.read_bytes()
    assert save_guarded_documents([entry]).files[0].status == "unchanged"
    assert path.read_bytes() == before
    entry.document.footprints[0].path = "/root/symbol"
    assert save_guarded_documents([entry]).files[0].status == "saved"
    assert Board.from_file(path).footprints[0].path == "/root/symbol"
    with pytest.raises(GuardedSaveError):
        save_guarded_documents([GuardedDocument(entry.source, board)])
