"""Board-context (SaveContext.BOARD) footprint round-trip (T7).

The fixture is a real ``(footprint ...)`` node extracted from a corpus
``.kicad_pcb`` (a private product board) and dedented to top level. It exercises the
CTL_FOR_BOARD delta: full ``lib:name`` FPID, no version/generator block,
footprint-level uuid + (at), and pad ``(net)``/``(pintype)`` tokens.
"""

from __future__ import annotations

from pathlib import Path

from eedesigncore.common import SaveContext
from eedesigncore.mod.models import DrillInfo, Footprint, Pad
from eedesigncore.mod.writer import _dump_pad, dump_mod, write_mod

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "pcb" / "footprint_board_context_C_0402.kicad_mod"
)


def _orig() -> str:
    return FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_board_footprint_round_trip_byte_identical() -> None:
    fp = Footprint.from_string(_orig())
    assert write_mod(fp, context=SaveContext.BOARD) == _orig()


def test_full_lib_name_fpid_preserved() -> None:
    fp = Footprint.from_string(_orig())
    assert fp.fp_id == "Capacitor_SMD:C_0402_1005Metric"
    assert dump_mod(fp, context=SaveContext.BOARD).startswith(
        '(footprint "Capacitor_SMD:C_0402_1005Metric"'
    )


def test_board_context_omits_version_emits_uuid_at() -> None:
    fp = Footprint.from_string(_orig())
    out = dump_mod(fp, context=SaveContext.BOARD)
    assert "(version " not in out
    assert fp.uuid is not None
    assert fp.at is not None
    assert f"(uuid {fp.uuid!r}".replace("'", '"') in out


def test_pad_net_and_pintype_stored_and_emitted() -> None:
    fp = Footprint.from_string(_orig())
    net_pads = [p for p in fp.pads if p.net_name is not None]
    assert net_pads, "fixture should carry at least one padded net"
    out = dump_mod(fp, context=SaveContext.BOARD)
    for pad in net_pads:
        assert f"(net {pad.net_name!r}".replace("'", '"') in out
    assert "(pintype " in out


def test_library_context_strips_board_only_tokens() -> None:
    # Same model emitted in LIBRARY context: no pad net/pintype tokens, but the
    # version block returns. (This is the write-side of the extract transform;
    # the geometry/uuids remain.)
    fp = Footprint.from_string(_orig())
    out = dump_mod(fp, context=SaveContext.LIBRARY)
    assert "(version " in out
    assert "(net " not in out
    assert "(pintype " not in out


def _pth_pad(zlc: list[str] | None) -> Pad:
    return Pad(
        number="1",
        pad_type="thru_hole",
        shape="circle",
        position=(0.0, 0.0),
        size=(1.0, 1.0),
        drill=DrillInfo(width=0.5),
        layers=["*.Cu", "*.Mask"],
        remove_unused_layers=True,
        keep_end_layers=False,
        zone_layer_connections=zlc,
    )


def test_zone_layer_connections_emitted_only_in_board_context() -> None:
    # ADR-031: board-context PTH pad with remove_unused_layers set emits
    # (zone_layer_connections …) after keep_end_layers; LIBRARY never does.
    pad = _pth_pad([])
    board_out = _dump_pad(pad, context=SaveContext.BOARD)
    lib_out = _dump_pad(pad, context=SaveContext.LIBRARY)
    assert "(zone_layer_connections)" in board_out
    assert board_out.index("(keep_end_layers") < board_out.index("(zone_layer_connections")
    assert "zone_layer_connections" not in lib_out


def test_zone_layer_connections_layers_round_trip() -> None:
    pad = _pth_pad(["F.Cu", "B.Cu"])
    out = _dump_pad(pad, context=SaveContext.BOARD)
    assert '(zone_layer_connections "F.Cu" "B.Cu")' in out


def test_zone_layer_connections_absent_without_remove_unused() -> None:
    pad = _pth_pad([])
    pad.remove_unused_layers = None
    out = _dump_pad(pad, context=SaveContext.BOARD)
    assert "zone_layer_connections" not in out
