"""T4 — tracks/arcs/vias model + loader + writer round-trip coverage."""

from __future__ import annotations

from eedesigncore.common.pcb_family import BackdrillInfo, PostMachining, PostMachiningMode
from eedesigncore.mod.models import TeardropParams
from eedesigncore.pcb.loader import (
    _load_track_arc,
    _load_track_segment,
    _load_via,
)
from eedesigncore.pcb.models import (
    Board,
    TrackArc,
    TrackSegment,
    Via,
    ViaFrontBackOpt,
    ViaPadstack,
    ViaPadstackLayer,
)
from eedesigncore.pcb.writer import (
    _dump_arc,
    _dump_segment,
    _dump_via,
    dump_board,
)
from eedesigncore.sexpr.parser import parse


def _seg_node(flat: str):
    return parse(flat)


class TestSegment:
    def test_dump_basic(self) -> None:
        seg = TrackSegment(
            start=(95.377, 96.522),
            end=(95.377, 97.624),
            width=0.254,
            layer="F.Cu",
            net="Net-(D1-K)",
            uuid="159ecb6b-518d-4894-9a5e-ad4280ed230f",
        )
        flat = _dump_segment(seg)
        assert "(segment (start 95.377 96.522) (end 95.377 97.624) (width 0.254)" in flat
        assert '(layer "F.Cu")' in flat
        assert '(net "Net-(D1-K)")' in flat
        assert '(uuid "159ecb6b-518d-4894-9a5e-ad4280ed230f")' in flat

    def test_round_trip(self) -> None:
        seg = TrackSegment(
            start=(1.0, 2.0),
            end=(3.0, 4.0),
            width=0.2,
            locked=True,
            layer="B.Cu",
            solder_mask_margin=0.05,
            net="GND",
            uuid="u1",
        )
        node = _seg_node(_dump_segment(seg))
        assert _load_track_segment(node) == seg

    def test_multi_layer_round_trip(self) -> None:
        seg = TrackSegment(
            start=(1.0, 2.0),
            end=(3.0, 4.0),
            width=0.2,
            layers=["F.Cu", "B.Cu"],
            net="",
            uuid="u2",
        )
        flat = _dump_segment(seg)
        assert '(layers "F.Cu" "B.Cu")' in flat
        assert _load_track_segment(_seg_node(flat)) == seg

    def test_empty_net_emitted(self) -> None:
        seg = TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), width=0.1, layer="F.Cu", net="")
        assert '(net "")' in _dump_segment(seg)


class TestArc:
    def test_round_trip(self) -> None:
        arc = TrackArc(
            start=(1.0, 1.0),
            mid=(2.0, 1.5),
            end=(3.0, 1.0),
            width=0.25,
            layer="F.Cu",
            net="SIG",
            uuid="a1",
        )
        flat = _dump_arc(arc)
        assert "(arc (start 1 1) (mid 2 1.5) (end 3 1) (width 0.25)" in flat
        assert _load_track_arc(_seg_node(flat)) == arc


class TestVia:
    def test_simple_round_trip(self) -> None:
        via = Via(
            at=(102.616, 45.085),
            size=0.508,
            drill=0.3,
            layers=("F.Cu", "B.Cu"),
            free=True,
            capping=False,
            covering=ViaFrontBackOpt(front=False, back=False),
            plugging=ViaFrontBackOpt(front=False, back=False),
            filling=False,
            net="Earth",
            uuid="00a63052-2ca4-41b1-bcd9-840d4037e1a4",
        )
        flat = _dump_via(via)
        assert "(via (at 102.616 45.085) (size 0.508) (drill 0.3)" in flat
        assert '(layers "F.Cu" "B.Cu")' in flat
        assert "(free yes)" in flat
        assert "(capping no)" in flat
        assert "(covering (front no) (back no))" in flat
        assert _load_via(_seg_node(flat)) == via

    def test_blind_type(self) -> None:
        via = Via(via_type="blind", at=(1.0, 1.0), size=0.4, drill=0.2)
        flat = _dump_via(via)
        assert flat.startswith("(via blind (at")
        assert _load_via(_seg_node(flat)).via_type == "blind"

    def test_through_type_omits_qualifier(self) -> None:
        via = Via(at=(1.0, 1.0), size=0.4, drill=0.2)
        assert _dump_via(via).startswith("(via (at")

    def test_unconnected_modes_round_trip(self) -> None:
        for mode, tokens in [
            ("remove_all", ["(remove_unused_layers yes)", "(keep_end_layers no)"]),
            (
                "remove_except_start_end",
                ["(remove_unused_layers yes)", "(keep_end_layers yes)"],
            ),
            ("start_end_only", ["(start_end_only yes)"]),
        ]:
            via = Via(at=(1.0, 1.0), size=0.4, drill=0.2, unconnected_layer_mode=mode)  # type: ignore[arg-type]
            flat = _dump_via(via)
            for tok in tokens:
                assert tok in flat
            assert _load_via(_seg_node(flat)).unconnected_layer_mode == mode

    def test_keep_all_emits_nothing(self) -> None:
        via = Via(at=(1.0, 1.0), size=0.4, drill=0.2)
        flat = _dump_via(via)
        assert "remove_unused_layers" not in flat
        assert "start_end_only" not in flat

    def test_zone_layer_connections(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.3,
            drill=0.2,
            unconnected_layer_mode="remove_except_start_end",
            zone_layer_connections=["In2.Cu"],
            net="N",
            uuid="v",
        )
        flat = _dump_via(via)
        assert '(zone_layer_connections "In2.Cu")' in flat
        assert _load_via(_seg_node(flat)) == via

    def test_tenting_covering_plugging(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.3,
            drill=0.2,
            tenting=ViaFrontBackOpt(front=True, back=None),
            covering=ViaFrontBackOpt(front=False, back=True),
        )
        flat = _dump_via(via)
        assert "(tenting (front yes) (back none))" in flat
        assert "(covering (front no) (back yes))" in flat
        assert _load_via(_seg_node(flat)) == via

    def test_teardrops_round_trip(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.3,
            drill=0.2,
            teardrops=TeardropParams(
                best_length_ratio=0.3,
                max_length=0.3,
                best_width_ratio=1.0,
                max_width=0.3,
                curved_edges=False,
                filter_ratio=0.9,
                enabled=True,
                allow_two_segments=False,
                prefer_zone_connections=True,
            ),
            net="X",
            uuid="v",
        )
        flat = _dump_via(via)
        assert "(teardrops" in flat
        assert "(best_length_ratio 0.3)" in flat
        assert _load_via(_seg_node(flat)) == via

    def test_padstack_front_inner_back(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.5,
            drill=0.3,
            padstack=ViaPadstack(
                mode="front_inner_back",
                layers=[
                    ViaPadstackLayer(layer_name="Inner", size=0.4),
                    ViaPadstackLayer(layer_name="B.Cu", size=0.45),
                ],
            ),
            net="P",
            uuid="v",
        )
        flat = _dump_via(via)
        assert "(padstack (mode front_inner_back)" in flat
        assert '(layer "Inner" (size 0.4))' in flat
        assert _load_via(_seg_node(flat)) == via

    def test_padstack_custom_round_trip(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.5,
            drill=0.3,
            padstack=ViaPadstack(
                mode="custom",
                layers=[ViaPadstackLayer(layer_name="In1.Cu", size=0.42)],
            ),
        )
        flat = _dump_via(via)
        assert "(mode custom)" in flat
        assert _load_via(_seg_node(flat)) == via

    def test_backdrill_and_post_machining(self) -> None:
        via = Via(
            at=(1.0, 1.0),
            size=0.6,
            drill=0.3,
            backdrill=BackdrillInfo(size=0.5, start_layer="F.Cu", end_layer="In2.Cu"),
            tertiary_drill=BackdrillInfo(size=0.4, start_layer="B.Cu", end_layer="In3.Cu"),
            front_post_machining=PostMachining(
                mode=PostMachiningMode.counterbore, size=0.7, depth=0.2, angle=9.0
            ),
            net="N",
            uuid="v",
        )
        flat = _dump_via(via)
        assert '(backdrill (size 0.5) (layers "F.Cu" "In2.Cu"))' in flat
        assert '(tertiary_drill (size 0.4) (layers "B.Cu" "In3.Cu"))' in flat
        assert "(front_post_machining counterbore" in flat
        assert _load_via(_seg_node(flat)) == via

    def test_capping_none_is_dropped(self) -> None:
        node = _seg_node('(via (at 1 1) (size 0.3) (drill 0.2) (capping none) (net "N"))')
        via = _load_via(node)
        assert via.capping is None
        assert "capping" not in _dump_via(via)

    def test_finishing_both_none_dropped(self) -> None:
        node = _seg_node(
            '(via (at 1 1) (size 0.3) (drill 0.2) (covering (front none) (back none)) (net "N"))'
        )
        via = _load_via(node)
        assert via.covering is None


class TestBoardTrackStream:
    def test_board_emits_tracks_in_order(self) -> None:
        board = Board(
            tracks=[
                TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), width=0.2, layer="F.Cu", net=""),
                Via(at=(1.0, 1.0), size=0.4, drill=0.2, net="", uuid="v"),
            ]
        )
        flat = dump_board(board)
        assert flat.index("(segment") < flat.index("(via")

    def test_board_round_trip_idempotent(self) -> None:
        board = Board(
            tracks=[
                TrackSegment(
                    start=(0.0, 0.0),
                    end=(1.0, 0.0),
                    width=0.2,
                    layer="F.Cu",
                    net="GND",
                    uuid="s1",
                ),
                TrackArc(
                    start=(0.0, 0.0),
                    mid=(0.5, 0.5),
                    end=(1.0, 0.0),
                    width=0.2,
                    layer="F.Cu",
                    net="GND",
                    uuid="a1",
                ),
                Via(
                    at=(2.0, 2.0),
                    size=0.5,
                    drill=0.3,
                    layers=("F.Cu", "B.Cu"),
                    net="GND",
                    uuid="v1",
                ),
            ]
        )
        first = board.to_string()
        second = Board.from_string(first).to_string()
        assert first == second
