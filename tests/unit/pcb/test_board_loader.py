"""T5 — full board loader dispatch, nets-by-name, and legacy tolerance."""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pcb.loader import load_pcb
from eedesigncore.pcb.models import Board, TrackSegment, Via
from eedesigncore.sexpr.parser import parse


def _load(text: str) -> Board:
    return load_pcb(parse(text))


class TestLegacyNetTolerance:
    def test_root_net_table_accepted_and_dropped(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (net 0 "") (net 1 "GND")'
            ' (segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu")'
            ' (net "GND") (uuid "s1")))'
        )
        board = _load(text)
        # Legacy root net table is dropped; nets come from item refs only.
        assert board.net_names == {"GND"}
        assert "(net 0" not in board.to_string()
        assert "(net 1" not in board.to_string()

    def test_net_class_accepted_and_dropped(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (net_class "Default" "desc" (clearance 0.2) (add_net "GND")))'
        )
        board = _load(text)
        assert "net_class" not in board.to_string()

    def test_host_accepted_and_dropped(self) -> None:
        text = '(kicad_pcb (version 20260206) (generator "pcbnew") (host "pcbnew" "1.0"))'
        board = _load(text)
        assert "(host" not in board.to_string()

    def test_item_legacy_code_plus_name(self) -> None:
        # Legacy pad/item form `(net <code> "<name>")` -> name only.
        node = parse('(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 3 "SIG"))')
        from eedesigncore.pcb.loader import _load_track_segment

        seg = _load_track_segment(node)
        assert seg.net == "SIG"

    def test_item_legacy_code_only_drops_name(self) -> None:
        node = parse('(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 3))')
        from eedesigncore.pcb.loader import _load_track_segment

        seg = _load_track_segment(node)
        assert seg.net is None


class TestNetsByName:
    def test_synthesized_set_order_independent(self) -> None:
        board = Board(
            tracks=[
                TrackSegment(start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu", net="GND"),
                TrackSegment(start=(0, 0), end=(2, 0), width=0.2, layer="F.Cu", net="VCC"),
                Via(at=(1, 1), size=0.4, drill=0.2, net="GND"),
                Via(at=(2, 2), size=0.4, drill=0.2, net=""),
            ]
        )
        assert board.net_names == {"GND", "VCC", ""}

    def test_writer_never_emits_net_table(self) -> None:
        board = Board(
            tracks=[
                TrackSegment(
                    start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu", net="GND", uuid="s"
                ),
            ]
        )
        flat = board.to_string()
        # No root net table and no numeric-code net refs anywhere.
        assert "\t(net 0" not in flat
        assert "(net_class" not in flat
        assert '(net "GND")' in flat


class TestBoardItemDispatch:
    def test_footprint_loaded_into_board(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (footprint "Lib:R_0402" (layer "F.Cu") (uuid "fp1") (at 10 10)))'
        )
        board = _load(text)
        assert len(board.footprints) == 1
        assert board.footprints[0].fp_id == "Lib:R_0402"

    def test_module_alias_loaded(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (module "Lib:R_0402" (layer "F.Cu") (uuid "fp1") (at 10 10)))'
        )
        board = _load(text)
        assert len(board.footprints) == 1

    def test_zone_net_by_name(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (zone (net "V_SUPERCAP") (layer "F.Cu") (uuid "z1")'
            " (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))))"
        )
        board = _load(text)
        assert len(board.zones) == 1
        assert board.zones[0].net_name == "V_SUPERCAP"
        assert board.net_names == {"V_SUPERCAP"}

    def test_generator_loads(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (generated (uuid "g") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (members "m1")))'
        )
        board = _load(text)
        assert len(board.generators) == 1
        assert board.generators[0].generator_type == "tuning_pattern"
        assert board.generators[0].members == ["m1"]

    def test_target_loads(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (target plus (at 0 0) (size 5) (layer "Edge.Cuts") (uuid "tg")))'
        )
        board = _load(text)
        assert len(board.targets) == 1
        assert board.targets[0].is_x is False

    def test_gr_text_loads_without_type_field(self) -> None:
        # Board gr_text has NO leading type field (unlike fp_text) -- T6.
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            ' (gr_text "hi" (at 0 0) (layer "F.SilkS") (uuid "t1")))'
        )
        board = _load(text)
        assert len(board.drawings) == 1
        item = board.drawings[0]
        assert item.content == "hi"
        assert item.text_type == ""

    def test_unknown_root_token_hard_errors(self) -> None:
        text = '(kicad_pcb (version 20260206) (generator "pcbnew") (bogus_token 1))'
        with pytest.raises(KicadParseError, match="Unknown token in kicad_pcb"):
            _load(text)

    def test_embedded_fonts_and_files(self) -> None:
        text = (
            '(kicad_pcb (version 20260206) (generator "pcbnew")'
            " (embedded_fonts no)"
            ' (embedded_files (file (name "a.step") (type model) (data) (checksum "x"))))'
        )
        board = _load(text)
        assert board.embedded_fonts is False
        assert len(board.embedded_files) == 1
        assert board.embedded_files[0].name == "a.step"
