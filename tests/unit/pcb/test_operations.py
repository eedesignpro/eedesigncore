"""Unit tests for extract_footprint / inject_footprint (T10, spec §2.1-2.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.context import SaveContext
from eedesigncore.common.uuid_ops import strip_uuids
from eedesigncore.mod.models import Footprint
from eedesigncore.mod.writer import dump_mod
from eedesigncore.pcb.models import BOARD_FILE_VERSION, Board
from eedesigncore.pcb.operations import extract_footprint, inject_footprint

FIXTURE = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "pcb"
    / "footprint_board_context_C_0402.kicad_mod"
)


def _board_with_footprint() -> Board:
    """A minimal board carrying one board-context footprint from the fixture."""
    fp = Footprint.from_file(FIXTURE)
    return Board(footprints=[fp])


# ---------------------------------------------------------------------------
# extract_footprint
# ---------------------------------------------------------------------------


def test_extract_by_reference() -> None:
    board = _board_with_footprint()
    fp = extract_footprint(board, reference="C68")
    assert fp.fp_id == "C_0402_1005Metric"  # step 1: bare item name


def test_extract_by_uuid() -> None:
    board = _board_with_footprint()
    fp = extract_footprint(board, uuid="014910f1-7124-4332-892e-56a71d3b84d0")
    assert fp.fp_id == "C_0402_1005Metric"


def test_extract_requires_exactly_one_selector() -> None:
    board = _board_with_footprint()
    with pytest.raises(ValueError, match="exactly one"):
        extract_footprint(board)
    with pytest.raises(ValueError, match="exactly one"):
        extract_footprint(board, reference="C68", uuid="x")


def test_extract_unknown_reference_raises() -> None:
    board = _board_with_footprint()
    with pytest.raises(KeyError):
        extract_footprint(board, reference="NOPE")


def test_extract_strips_board_context_tokens() -> None:
    board = _board_with_footprint()
    fp = extract_footprint(board, reference="C68")
    # steps 3/4/5/8: fp uuid, at, path, sheetname/sheetfile all cleared
    assert fp.uuid is None
    assert fp.at is None
    assert fp.path is None
    assert fp.sheetname is None
    assert fp.sheetfile is None
    # step 2: version pinned to board file version, generator block present
    assert fp.version == BOARD_FILE_VERSION
    # step 6: pad net/pinfunction/pintype stripped
    for pad in fp.pads:
        assert pad.net_code is None
        assert pad.net_name is None
        assert pad.pinfunction is None
        assert pad.pintype is None


def test_extract_strips_child_uuids_group_aware() -> None:
    board = _board_with_footprint()
    fp = extract_footprint(board, reference="C68")
    # ADR-019: EXTRACT strips uuids by default (overrides Part-3 KEEP step 10).
    for pad in fp.pads:
        assert pad.id is None
    for prop in fp.properties:
        assert prop.id is None


def test_extract_does_not_mutate_source_board() -> None:
    board = _board_with_footprint()
    extract_footprint(board, reference="C68")
    src = board.footprints[0]
    # deep copy: source retains its board-context identity
    assert src.uuid == "014910f1-7124-4332-892e-56a71d3b84d0"
    assert src.at is not None
    assert src.pads[0].net_name == "Net-(D3-K)"


def test_extract_is_valid_kicad_mod_round_trip() -> None:
    board = _board_with_footprint()
    fp = extract_footprint(board, reference="C68")
    text = fp.to_string()
    # LIBRARY-context shape: version/generator block emitted, no fp uuid/at.
    assert "(version" in text
    assert "(generator" in text
    reloaded = Footprint.from_string(text)
    assert reloaded.fp_id == "C_0402_1005Metric"
    assert reloaded.uuid is None
    assert reloaded.to_string() == text  # byte-stable


# ---------------------------------------------------------------------------
# inject_footprint
# ---------------------------------------------------------------------------


def test_inject_appends_and_mints_uuid() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(10.0, 20.0))
    assert placed in target.footprints
    assert len(target.footprints) == 1
    # step 3: fresh footprint-level uuid minted
    assert placed.uuid is not None
    # step 4: placement set
    assert placed.at is not None
    assert placed.at.position == (10.0, 20.0)


def test_inject_regenerates_child_uuids() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")  # child uuids stripped
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0), regenerate_uuids=True)
    for pad in placed.pads:
        assert pad.id is not None


def test_inject_lib_nickname_qualifies_bare_name() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")  # bare fp_id now
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0), lib_nickname="Capacitor_SMD")
    assert placed.fp_id == "Capacitor_SMD:C_0402_1005Metric"


def test_inject_bare_fpid_no_nickname_uses_placeholder() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")  # bare fp_id now
    target = Board()
    # bare FPID + no lib_nickname -> placeholder "eedesigncore" (ADR-041 amd 2)
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0))
    assert placed.fp_id == "eedesigncore:C_0402_1005Metric"


def test_inject_prequalified_fpid_no_nickname_unchanged() -> None:
    target = Board()
    lib_fp = Footprint(fp_id="Capacitor_SMD:C_0402_1005Metric")
    # pre-qualified FPID is left as-is even with no lib_nickname
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0))
    assert placed.fp_id == "Capacitor_SMD:C_0402_1005Metric"


def test_inject_net_map_by_pad_number() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0), net_map={"1": "GND", "2": "VCC"})
    nets = {pad.number: pad.net_name for pad in placed.pads}
    assert nets == {"1": "GND", "2": "VCC"}


def test_inject_reference_override() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0), reference="C99")
    ref = next(p.value for p in placed.properties if p.name == "Reference")
    assert ref == "C99"


def test_inject_board_writes_cleanly() -> None:
    board = _board_with_footprint()
    lib_fp = extract_footprint(board, reference="C68")
    target = Board()
    inject_footprint(target, lib_fp, at=(1.0, 2.0), lib_nickname="Capacitor_SMD")
    text = target.to_string()
    assert "Capacitor_SMD:C_0402_1005Metric" in text
    # a board footprint carries no version block (BOARD context)
    fp_block = text[text.index("(footprint") :]
    assert "(version" not in fp_block


def test_extract_then_inject_semantically_equivalent() -> None:
    """Extract then inject back yields a semantically equivalent footprint node.

    UUIDs differ by design (extract strips, inject mints); comparison is on the
    uuid-stripped BOARD-context emission with nets reapplied via net_map.
    """
    board = _board_with_footprint()
    original = board.footprints[0]
    net_map = {p.number: p.net_name for p in original.pads if p.net_name}

    lib_fp = extract_footprint(board, reference="C68")
    target = Board()
    placed = inject_footprint(
        target,
        lib_fp,
        at=tuple(original.at.position),  # type: ignore[union-attr]
        rotation=original.at.angle,  # type: ignore[union-attr]
        path=original.path,
        lib_nickname="Capacitor_SMD",
        net_map=net_map,
    )

    a = original.model_copy(deep=True)
    b = placed.model_copy(deep=True)
    # sheetname/sheetfile and pad pinfunction/pintype are board netlist/sheet
    # metadata that extract strips and this inject (net_map only) does not
    # restore; normalize them out of the semantic comparison.
    a.sheetname = None
    a.sheetfile = None
    for pad in a.pads:
        pad.pinfunction = None
        pad.pintype = None
    strip_uuids(a, group_aware=False)
    strip_uuids(b, group_aware=False)
    assert dump_mod(a, context=SaveContext.BOARD) == dump_mod(b, context=SaveContext.BOARD)


def test_inject_moves_embedded_data_to_board_root() -> None:
    from eedesigncore.common.models import EmbeddedFile

    lib_fp = Footprint(
        fp_id="C_0402_1005Metric",
        embedded_files=[
            EmbeddedFile(name="m.step", file_type="model", data="QUJD", checksum="ck")
        ],
    )
    target = Board()
    placed = inject_footprint(target, lib_fp, at=(0.0, 0.0))
    # step 8: bytes moved to board root, footprint stub carries no data
    assert placed.embedded_files[0].data == ""
    assert placed.embedded_files[0].checksum == "ck"
    assert len(target.embedded_files) == 1
    assert target.embedded_files[0].data == "QUJD"
