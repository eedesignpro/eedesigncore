"""Unit tests for the canonical board save order (R4).

Boards have no `kicad-cli` resave command, so every rule here is asserted
directly against the source-verified comparator, per plan requirement R7. The
citations are in ``pcb/ordering.py`` and in
``context/audit/save-canonicalization-sweep-2026-08-13.md`` section 2.2.
"""

from __future__ import annotations

import re

from eedesigncore.common.models import Stroke, TextAttributes
from eedesigncore.common.pcb_family import (
    PcbGroup,
    PcbLine,
    PcbPoint,
    PcbPoly,
    PcbRect,
    PcbText,
    Zone,
)
from eedesigncore.mod.models import Footprint, FootprintPlacement
from eedesigncore.pcb.models import (
    Board,
    BoardLayer,
    PcbGenerator,
    PcbTarget,
    TrackArc,
    TrackSegment,
    Via,
)
from eedesigncore.pcb.ordering import (
    board_member_layer_sets,
    compare_board_drawings,
    copper_stack,
    derive_net_codes,
    sort_board_drawings,
    sort_board_footprints,
    sort_board_generators,
    sort_board_groups,
    sort_board_targets,
    sort_board_tracks,
    sort_board_zones,
    via_layer_seq,
)
from eedesigncore.pcb.writer import dump_board

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_FOUR_LAYER = [
    BoardLayer(ordinal=0, canonical_name="F.Cu"),
    BoardLayer(ordinal=4, canonical_name="In1.Cu"),
    BoardLayer(ordinal=6, canonical_name="In2.Cu"),
    BoardLayer(ordinal=2, canonical_name="B.Cu"),
    BoardLayer(ordinal=31, canonical_name="F.CrtYd", layer_type="user"),
]


def _segment(net: str, layer: str, uuid: str) -> TrackSegment:
    return TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), layer=layer, net=net, uuid=uuid)


def _footprint(uuid: str, layer: str = "F.Cu") -> Footprint:
    return Footprint(
        fp_id="lib:FP", uuid=uuid, layer=layer, at=FootprintPlacement(position=(0.0, 0.0))
    )


def _line(start: tuple[float, float], layer: str, uuid: str) -> PcbLine:
    line = PcbLine(start=start, end=(9.0, 9.0), layer=layer, stroke=Stroke(width=0.1))
    line.id = uuid
    return line


def _board(**kwargs: object) -> Board:
    return Board(layers=list(_FOUR_LAYER), **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Footprints: BOARD_ITEM::ptr_cmp (board_item.cpp:314-326)
# ---------------------------------------------------------------------------


def test_board_footprints_sort_by_uuid_within_one_layer() -> None:
    """Sweep discriminator 2: twelve F.Cu footprints ordered by UUID alone."""
    later = _footprint("0c1ff839-0000-0000-0000-000000000000")
    earlier = _footprint("009ce14c-0000-0000-0000-000000000000")

    assert [fp.uuid for fp in sort_board_footprints([later, earlier])] == [
        earlier.uuid,
        later.uuid,
    ]


def test_board_footprints_sort_by_layer_before_uuid() -> None:
    """``F.Cu`` is 0 and ``B.Cu`` is 2, so a front footprint always leads."""
    back = _footprint("00000000-0000-0000-0000-000000000000", layer="B.Cu")
    front = _footprint("ffffffff-0000-0000-0000-000000000000", layer="F.Cu")

    assert [fp.layer for fp in sort_board_footprints([back, front])] == ["F.Cu", "B.Cu"]


def test_board_footprints_emit_sorted() -> None:
    board = _board(
        footprints=[
            _footprint("bbbbbbbb-0000-0000-0000-000000000000"),
            _footprint("aaaaaaaa-0000-0000-0000-000000000000"),
        ]
    )
    emitted = dump_board(board)

    assert emitted.index('"aaaaaaaa') < emitted.index('"bbbbbbbb')


# ---------------------------------------------------------------------------
# Drawings: BOARD::cmp_drawings (board.cpp:3426-3474)
# ---------------------------------------------------------------------------


def test_board_drawings_sort_by_type_then_layer() -> None:
    text = PcbText(content="A", position=(0.0, 0.0), layer="F.Cu", attributes=TextAttributes())
    text.id = "t1"
    crtyd = _line((0.0, 0.0), "F.CrtYd", "l2")
    front = _line((0.0, 0.0), "F.Cu", "l1")

    assert sort_board_drawings([text, crtyd, front]) == [front, crtyd, text]


def test_board_shape_keys_put_start_before_the_shape_kind() -> None:
    """``EDA_SHAPE::Compare`` compares start and end FIRST (``eda_shape.cpp:2457-2460``).

    That is the opposite of ``FOOTPRINT::cmp_drawings``, which compares the shape
    kind first. A rectangle whose start leads therefore precedes a segment.
    """
    segment = _line((5.0, 0.0), "F.Cu", "b")
    rect = PcbRect(start=(1.0, 0.0), end=(9.0, 9.0), layer="F.Cu")
    rect.id = "a"

    assert sort_board_drawings([segment, rect]) == [rect, segment]


def test_board_polygons_with_an_arc_tie_rather_than_guess_a_vertex_count() -> None:
    """ADR-061: a ``SHAPE_LINE_CHAIN`` interpolates an arc, so the count is unknowable."""
    from eedesigncore.common.models import PtsArc

    curved = PcbPoly(
        pts=[(0.0, 0.0), PtsArc(start=(1.0, 0.0), mid=(2.0, 1.0), end=(3.0, 0.0))], layer="F.Cu"
    )
    curved.id = "z"
    plain = PcbPoly(pts=[(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], layer="F.Cu")
    plain.id = "a"

    assert compare_board_drawings(curved, plain) == 0
    assert sort_board_drawings([curved, plain]) == [curved, plain]


# ---------------------------------------------------------------------------
# Targets (sweep OQ-A)
# ---------------------------------------------------------------------------


def test_targets_emit_after_every_other_drawing() -> None:
    """``PCB_TARGET_T`` is 24, above every other drawing type (``typeinfo.h:107``)."""
    board = _board(
        drawings=[_line((0.0, 0.0), "F.Cu", "l1")],
        targets=[PcbTarget(position=(0.0, 0.0), layer="F.Cu", uuid="tg1")],
    )
    emitted = dump_board(board)

    assert emitted.index("(gr_line") < emitted.index("(target")


def test_targets_sort_by_layer_then_uuid() -> None:
    back = PcbTarget(layer="B.Cu", uuid="aaaa")
    front = PcbTarget(layer="F.Cu", uuid="zzzz")

    assert sort_board_targets([back, front]) == [front, back]


# ---------------------------------------------------------------------------
# Tracks: PCB_TRACK::cmp_tracks (pcb_track.cpp:2718-2733)
# ---------------------------------------------------------------------------


def test_net_codes_come_from_the_track_stream_order() -> None:
    board = _board(
        tracks=[
            _segment("GNDA", "F.Cu", "a"),
            _segment("VCC", "F.Cu", "b"),
            _segment("VEE", "F.Cu", "c"),
        ]
    )

    assert derive_net_codes(board) == {"": 0, "GNDA": 1, "VCC": 2, "VEE": 3}


def test_a_track_added_to_an_existing_net_lands_beside_that_net() -> None:
    board = _board(
        tracks=[
            _segment("GNDA", "F.Cu", "a"),
            _segment("VCC", "F.Cu", "b"),
            _segment("VEE", "F.Cu", "c"),
        ]
    )
    board.tracks.append(_segment("GNDA", "F.Cu", "z"))

    ordered = sort_board_tracks(board.tracks, derive_net_codes(board))

    assert [track.net for track in ordered] == ["GNDA", "GNDA", "VCC", "VEE"]


def test_tracks_sort_by_layer_within_a_net() -> None:
    codes = {"": 0, "GND": 1}
    back = _segment("GND", "B.Cu", "a")
    front = _segment("GND", "F.Cu", "z")

    assert sort_board_tracks([back, front], codes) == [front, back]


def test_a_via_sorts_before_an_arc_on_the_same_net_and_layer() -> None:
    """``PCB_VIA_T`` is 14 and ``PCB_ARC_T`` is 15 (``typeinfo.h:97-98``)."""
    codes = {"": 0, "GND": 1}
    arc = TrackArc(start=(0.0, 0.0), mid=(1.0, 1.0), end=(2.0, 0.0), layer="F.Cu", net="GND")
    arc.uuid = "a"
    via = Via(at=(0.0, 0.0), layers=("F.Cu", "B.Cu"), net="GND")
    via.uuid = "z"

    assert sort_board_tracks([arc, via], codes) == [via, arc]


def test_an_unnamed_track_leads_the_stream() -> None:
    codes = {"": 0, "GND": 1}
    named = _segment("GND", "F.Cu", "a")
    unnamed = TrackSegment(start=(0.0, 0.0), end=(1.0, 0.0), layer="F.Cu", uuid="z")

    assert sort_board_tracks([named, unnamed], codes) == [unnamed, named]


# ---------------------------------------------------------------------------
# Via layer sets (pcb_track.cpp:1568-1594)
# ---------------------------------------------------------------------------


def test_a_through_via_covers_every_board_copper_layer() -> None:
    """``LSET::AllCuMask( BoardCopperLayerCount() )``, not just the two named layers."""
    board = _board()
    stack = copper_stack(board)
    via = Via(via_type="through", layers=("F.Cu", "B.Cu"))

    assert stack == (0, 4, 6, 2)
    assert via_layer_seq(via, stack) == (0, 2, 4, 6)


def test_a_buried_via_covers_only_its_stack_range() -> None:
    stack = copper_stack(_board())
    via = Via(via_type="buried", layers=("In1.Cu", "In2.Cu"))

    assert via_layer_seq(via, stack) == (4, 6)


# ---------------------------------------------------------------------------
# Zones, groups and generators
# ---------------------------------------------------------------------------


def test_board_zones_sort_by_layer_set_then_uuid_not_by_priority() -> None:
    """A board zone goes through ``ptr_cmp``, so priority is NOT a key here."""
    high_priority_front = Zone(priority=99, layer="F.Cu", id="zzzz")
    low_priority_front = Zone(priority=0, layer="F.Cu", id="aaaa")
    back = Zone(priority=0, layer="B.Cu", id="0000")

    ordered = sort_board_zones([back, high_priority_front, low_priority_front])

    assert [zone.id for zone in ordered] == ["aaaa", "zzzz", "0000"]


def test_a_zone_without_a_persisted_uuid_never_moves() -> None:
    """KiCad writes no ``(uuid …)`` for a teardrop area (``:2887-2888``).

    It still sorts on the runtime UUID, which the file does not carry, so the
    comparator ties and the stable sort keeps the KiCad-written neighbours.
    """
    teardrop_a = Zone(layer="F.Cu", id=None)
    named = Zone(layer="F.Cu", id="13b2c061")
    teardrop_b = Zone(layer="F.Cu", id=None)
    stream = [teardrop_a, named, teardrop_b]

    assert sort_board_zones(stream) == stream


def test_groups_sort_by_their_member_layer_union() -> None:
    front_line = _line((0.0, 0.0), "F.Cu", "l-front")
    crtyd_line = _line((0.0, 0.0), "F.CrtYd", "l-crtyd")
    on_crtyd = PcbGroup(name="crtyd", id="aaaa", members=["l-crtyd"])
    on_front = PcbGroup(name="front", id="zzzz", members=["l-front"])
    board = _board(drawings=[front_line, crtyd_line], groups=[on_crtyd, on_front])

    layer_sets = board_member_layer_sets(board)

    assert sort_board_groups([on_crtyd, on_front], layer_sets) == [on_front, on_crtyd]


def test_a_group_holding_a_through_via_inherits_every_copper_layer() -> None:
    via = Via(via_type="through", layers=("F.Cu", "B.Cu"))
    via.uuid = "v1"
    board = _board(tracks=[via], groups=[PcbGroup(name="g", id="g1", members=["v1"])])

    assert board_member_layer_sets(board)["v1"] == (0, 2, 4, 6)


def test_a_generator_layer_set_unions_its_own_layer_with_its_members() -> None:
    """``PCB_GENERATOR::GetLayerSet`` (``pcbnew/pcb_generator.cpp:134-137``)."""
    front_line = _line((0.0, 0.0), "F.Cu", "l-front")
    generator = PcbGenerator(uuid="gen1", layer="B.Cu", members=["l-front"])
    board = _board(drawings=[front_line], generators=[generator])

    layer_sets = board_member_layer_sets(board)

    assert layer_sets["gen1"] == (0, 2)
    assert sort_board_generators([generator], layer_sets) == [generator]


def test_board_points_emit_sorted() -> None:
    board = _board(
        points=[
            PcbPoint(position=(5.0, 0.0), layer="F.Cu", id="a"),
            PcbPoint(position=(1.0, 0.0), layer="F.Cu", id="b"),
        ]
    )
    emitted = dump_board(board)

    assert emitted.index("(at 1 0)") < emitted.index("(at 5 0)")


# ---------------------------------------------------------------------------
# The whole board: a reversed model writes the same bytes
# ---------------------------------------------------------------------------


def test_reversing_every_board_stream_writes_the_same_bytes() -> None:
    board = _board(
        footprints=[_footprint("aaaa"), _footprint("bbbb")],
        drawings=[_line((0.0, 0.0), "F.Cu", "l1"), _line((1.0, 0.0), "F.Cu", "l2")],
        points=[PcbPoint(position=(0.0, 0.0), id="p1"), PcbPoint(position=(1.0, 0.0), id="p2")],
        tracks=[_segment("GND", "F.Cu", "t1"), _segment("VCC", "F.Cu", "t2")],
        zones=[Zone(layer="F.Cu", id="z1"), Zone(layer="B.Cu", id="z2")],
        targets=[PcbTarget(layer="F.Cu", uuid="g1"), PcbTarget(layer="B.Cu", uuid="g2")],
    )
    canonical = dump_board(board)

    for stream in (
        board.footprints,
        board.drawings,
        board.points,
        board.zones,
        board.targets,
    ):
        stream.reverse()

    assert dump_board(board) == canonical


def test_a_reversed_track_stream_keeps_its_net_grouping() -> None:
    """Reversing the tracks reverses the derived net rank, which is by design.

    A net code is runtime state that only the saved stream reveals
    (:func:`derive_net_codes`). Reversing the stream therefore reverses the net
    order, and the tracks of each net still emit together.
    """
    board = _board(tracks=[_segment("GND", "F.Cu", "t1"), _segment("VCC", "F.Cu", "t2")])
    board.tracks.reverse()

    nets = re.findall(r'\(net "([^"]+)"\)', dump_board(board))

    assert nets == ["VCC", "GND"]
