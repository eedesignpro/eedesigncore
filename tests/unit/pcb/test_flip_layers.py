"""The opposite-layer table a flip needs (iteration 19, P1).

``BOARD::FlipLayer`` reads a table KiCad rebuilds from the board, so the answer
depends on the copper count and on the user-layer names THAT board declares. One
test per rule in ``context/audit/pcb-flip-source-2026-09-11.md`` section 3, so a
change to any rule fails with the rule named.
"""

from __future__ import annotations

import pytest

from eedesigncore import Board
from eedesigncore.common.layers import flip_layer_name
from eedesigncore.pcb.layers import (
    board_copper_layer_count,
    board_layer_opposites,
    flip_board_layer,
)
from eedesigncore.pcb.models import BoardLayer


def _board(rows: list[tuple[int, str, str, str | None]]) -> Board:
    return Board(
        layers=[
            BoardLayer(
                ordinal=ordinal,
                canonical_name=name,
                layer_type=layer_type,  # type: ignore[arg-type]
                user_name=user_name,
            )
            for ordinal, name, layer_type, user_name in rows
        ]
    )


_TWO_LAYER_ROWS: list[tuple[int, str, str, str | None]] = [
    (0, "F.Cu", "signal", None),
    (2, "B.Cu", "signal", None),
    (1, "F.Mask", "user", None),
    (3, "B.Mask", "user", None),
    (5, "F.SilkS", "user", None),
    (7, "B.SilkS", "user", None),
    (25, "Edge.Cuts", "user", None),
]


@pytest.mark.parametrize(
    ("front", "back"),
    [
        ("F.Cu", "B.Cu"),
        ("F.SilkS", "B.SilkS"),
        ("F.Adhes", "B.Adhes"),
        ("F.Mask", "B.Mask"),
        ("F.Paste", "B.Paste"),
        ("F.CrtYd", "B.CrtYd"),
        ("F.Fab", "B.Fab"),
    ],
)
def test_seven_technical_pairs_always_swap(front: str, back: str) -> None:
    """Pass 1, the fixed cases of ``FlipLayer`` (``common/layer_id.cpp:175-196``)."""
    assert flip_layer_name(front) == back
    assert flip_layer_name(back) == front


def test_unsided_layer_maps_to_itself() -> None:
    """The default arm returns the layer unchanged (``layer_id.cpp:214``)."""
    for name in ("Edge.Cuts", "Margin", "Dwgs.User", "Cmts.User", "Rescue"):
        assert flip_layer_name(name, 6) == name


def test_inner_copper_does_not_swap_below_four_layers() -> None:
    """``aCopperLayersCount >= 4`` gates the inner arm (``layer_id.cpp:199``)."""
    assert flip_layer_name("In1.Cu", 2) == "In1.Cu"
    assert flip_layer_name("In1.Cu", 0) == "In1.Cu"


def test_inner_copper_swaps_by_index_on_four_layers() -> None:
    """``flippedIndex = count - 3 - innerIndex`` (``layer_id.cpp:201-202``)."""
    assert flip_layer_name("In1.Cu", 4) == "In2.Cu"
    assert flip_layer_name("In2.Cu", 4) == "In1.Cu"


def test_inner_copper_swaps_by_index_on_six_layers() -> None:
    """Six layers give four inner layers and two swapped pairs."""
    assert flip_layer_name("In1.Cu", 6) == "In4.Cu"
    assert flip_layer_name("In4.Cu", 6) == "In1.Cu"
    assert flip_layer_name("In2.Cu", 6) == "In3.Cu"
    assert flip_layer_name("In3.Cu", 6) == "In2.Cu"


def test_inner_index_clamps_at_both_ends() -> None:
    """An inner layer past the count clamps to ``maxIndex`` (``layer_id.cpp:204-211``).

    ``In9.Cu`` on a 4-layer board gives ``4 - 3 - 8 = -7``, which clamps to 0 and
    returns ``In1.Cu``. This is KiCad behavior on an inconsistent board, not a
    case a healthy file reaches.
    """
    assert flip_layer_name("In9.Cu", 4) == "In1.Cu"


def test_copper_count_comes_from_the_layers_table() -> None:
    """``copperLayerCount = cu.size()`` (``..._sexpr_parser.cpp:2297``)."""
    board = _board(_TWO_LAYER_ROWS)
    assert board_copper_layer_count(board) == 2

    four = _board([*_TWO_LAYER_ROWS, (4, "In1.Cu", "signal", None), (6, "In2.Cu", "power", None)])
    assert board_copper_layer_count(four) == 4


def test_board_table_uses_its_own_copper_count() -> None:
    """The same layer flips differently on a 2-layer and a 4-layer board."""
    two = _board(_TWO_LAYER_ROWS)
    four = _board([*_TWO_LAYER_ROWS, (4, "In1.Cu", "signal", None), (6, "In2.Cu", "signal", None)])

    assert flip_board_layer(two, "In1.Cu") == "In1.Cu"
    assert flip_board_layer(four, "In1.Cu") == "In2.Cu"


def test_user_layers_pair_by_name_after_the_first_dot() -> None:
    """Pass 2 (``board.cpp:861-892``). Non-consecutive, matched on the suffix."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "front", "F.Adjust"),
            (41, "User.2", "user", "Notes"),
            (43, "User.3", "back", "B.Adjust"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.3"
    assert table["User.3"] == "User.1"
    assert table["User.2"] == "User.2"


def test_user_pairing_rejects_an_empty_suffix() -> None:
    """``!candidate.IsEmpty()`` (``board.cpp:886``). A dotless name pairs nothing."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "front", "Adjust"),
            (41, "User.2", "back", "Adjust"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.2"


def test_user_pairing_needs_opposite_types() -> None:
    """``m_layers[layer].m_type == m_layers[ii].m_type`` skips (``board.cpp:883``)."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "front", "F.Adjust"),
            (41, "User.2", "front", "B.Adjust"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.1"
    assert table["User.2"] == "User.2"


def test_consecutive_front_back_user_layers_pair_when_both_are_named() -> None:
    """Pass 3 (``board.cpp:895-915``). Order matters: front first, then back."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "front", "Glue"),
            (41, "User.2", "back", "Paste"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.2"
    assert table["User.2"] == "User.1"


def test_pass_three_skips_an_unnamed_pair() -> None:
    """Both rows must carry a user name (``board.cpp:908``)."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "front", None),
            (41, "User.2", "back", "Paste"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.1"
    assert table["User.2"] == "User.2"


def test_pass_three_needs_front_then_back() -> None:
    """A back layer followed by a front layer is not a pass-3 pair."""
    board = _board(
        [
            *_TWO_LAYER_ROWS,
            (39, "User.1", "back", "Glue"),
            (41, "User.2", "front", "Glue"),
        ]
    )
    table = board_layer_opposites(board)
    assert table["User.1"] == "User.1"


def test_undeclared_layer_falls_back_to_the_free_function() -> None:
    """``recalcOpposites`` seeds every id, declared in the file or not."""
    board = _board(_TWO_LAYER_ROWS)
    assert "F.Fab" not in board_layer_opposites(board)
    assert flip_board_layer(board, "F.Fab") == "B.Fab"


def test_a_prebuilt_table_is_reused() -> None:
    """The table is built once per flip, not once per item."""
    board = _board(_TWO_LAYER_ROWS)
    table = board_layer_opposites(board)
    assert flip_board_layer(board, "F.Cu", opposites=table) == "B.Cu"


def test_legacy_numbered_layer_table_pairs_by_name() -> None:
    """The ordinal column is not an id, so the name decides (``board.cpp:856``).

    ``RCTF_TT_BTX_REVA`` in the corpus carries legacy numbering: ``(1 "In1.Cu")``,
    ``(31 "B.Cu")``, ``(39 "F.Mask")``. Reading column 1 as a modern
    ``PCB_LAYER_ID`` made ordinal 39 look like ``User.1`` and paired ``F.Mask``
    with it. Every earlier test used modern numbering and passed anyway.
    """
    board = _board(
        [
            (0, "F.Cu", "mixed", None),
            (1, "In1.Cu", "signal", None),
            (2, "In2.Cu", "signal", None),
            (31, "B.Cu", "mixed", None),
            (36, "B.SilkS", "user", "B.Silkscreen"),
            (37, "F.SilkS", "user", "F.Silkscreen"),
            (38, "B.Mask", "user", None),
            (39, "F.Mask", "user", None),
            (44, "Edge.Cuts", "user", None),
        ]
    )
    assert board_copper_layer_count(board) == 4
    table = board_layer_opposites(board)
    assert table["F.Mask"] == "B.Mask"
    assert table["B.Mask"] == "F.Mask"
    assert table["F.SilkS"] == "B.SilkS"
    assert table["In1.Cu"] == "In2.Cu"
    assert table["In2.Cu"] == "In1.Cu"
    assert table["Edge.Cuts"] == "Edge.Cuts"
    assert not any(name.startswith("User.") for name in table)
