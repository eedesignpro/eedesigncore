"""The board's opposite-layer table, which a flip needs and a file never stores.

``BOARD::FlipLayer`` (``pcbnew/board.cpp:916-920``) is a lookup into a table
KiCad rebuilds from the board, not a fixed mapping. ``BOARD::recalcOpposites``
(``:856-915``) fills it in three passes, and only the first is fixed. The other
two pair USER layers by the names that one board declares, so two boards with
the same items can flip the same layer to different places.

The evidence and the trap list are in
``context/audit/pcb-flip-source-2026-09-11.md`` section 3.
"""

from __future__ import annotations

from eedesigncore.common.layers import (
    CANONICAL_LAYER_NAMES,
    LAYER_NAME_TO_ORDINAL,
    flip_layer_name,
    is_copper_layer_name,
)
from eedesigncore.pcb.models import Board, BoardLayer

# User_1, the first user-definable ordinal. Pass 2 and pass 3 walk from here in
# steps of two, which is how the C++ loops are written.
_USER_1_ORDINAL = LAYER_NAME_TO_ORDINAL["User.1"]

_SIDED_TYPES = ("front", "back")


def board_copper_layer_count(board: Board) -> int:
    """Return the copper row count of the ``(layers …)`` table.

    KiCad's parser takes the count from that table and nowhere else:
    ``copperLayerCount = cu.size()``
    (``pcb_io_kicad_sexpr_parser.cpp:2297``, applied at ``:2357``).
    """
    return sum(1 for row in board.layers if is_copper_layer_name(row.canonical_name))


def _layer_id(row: BoardLayer) -> int | None:
    """The modern ``PCB_LAYER_ID`` of *row*, derived from its NAME.

    ``BoardLayer.ordinal`` is the raw first column and it is NOT reliable as an
    id. A board written with legacy numbering carries ``(1 "In1.Cu")`` and
    ``(31 "B.Cu")``, and KiCad's parser recomputes the id from the name and the
    position rather than trusting the column (``..._sexpr_parser.cpp:2270-2277``,
    ``BoardLayerFromLegacyId`` at ``common/layer_id.cpp:220``). Reading the
    column as an id paired ``F.Mask`` with ``User.1`` on a real corpus board.
    """
    return LAYER_NAME_TO_ORDINAL.get(row.canonical_name)


def _user_rows_by_ordinal(board: Board) -> dict[int, BoardLayer]:
    rows: dict[int, BoardLayer] = {}
    for row in board.layers:
        ordinal = _layer_id(row)
        if ordinal is not None and ordinal >= _USER_1_ORDINAL and ordinal % 2 == 1:
            rows[ordinal] = row
    return rows


def _principal_name(row: BoardLayer) -> str:
    """The user name after its first dot, which is what pass 2 compares.

    ``m_userName.AfterFirst( '.' )`` returns an empty string when the name holds
    no dot, and pass 2 rejects an empty candidate.
    """
    name = row.user_name
    if name is None or "." not in name:
        return ""
    return name.split(".", 1)[1]


def board_layer_opposites(board: Board) -> dict[str, str]:
    """Return the canonical-name to canonical-name opposite map for *board*.

    Port of ``BOARD::recalcOpposites`` (``pcbnew/board.cpp:856-915``). Three
    passes, in order:

    1. Seed every declared layer from :func:`flip_layer_name` with this board's
       copper count.
    2. Pair an unpaired user layer typed ``front`` or ``back`` with the first
       unpaired user layer of the OPPOSITE type whose user name after the first
       dot is equal and not empty.
    3. Pair the remaining CONSECUTIVE user layers when the lower one is typed
       ``front``, the next is typed ``back``, and BOTH carry a user name.

    A layer that pairs with nothing maps to itself, exactly as the C++ does.
    """
    count = board_copper_layer_count(board)
    opposite: dict[int, int] = {}
    for row in board.layers:
        ordinal = _layer_id(row)
        if ordinal is None:
            continue
        flipped = flip_layer_name(row.canonical_name, count)
        opposite[ordinal] = LAYER_NAME_TO_ORDINAL.get(flipped, ordinal)

    user_rows = _user_rows_by_ordinal(board)
    ordinals = sorted(user_rows)

    for ordinal in ordinals:
        if opposite.get(ordinal, ordinal) != ordinal:
            continue
        row = user_rows[ordinal]
        if row.layer_type not in _SIDED_TYPES:
            continue
        principal = _principal_name(row)
        for candidate_ordinal in ordinals:
            if candidate_ordinal <= ordinal:
                continue
            if opposite.get(candidate_ordinal, candidate_ordinal) != candidate_ordinal:
                continue
            candidate = user_rows[candidate_ordinal]
            if candidate.layer_type not in _SIDED_TYPES:
                continue
            if candidate.layer_type == row.layer_type:
                continue
            candidate_principal = _principal_name(candidate)
            if candidate_principal and candidate_principal == principal:
                opposite[ordinal] = candidate_ordinal
                opposite[candidate_ordinal] = ordinal
                break

    for ordinal in ordinals:
        nxt = ordinal + 2
        if nxt not in user_rows:
            continue
        if opposite.get(ordinal, ordinal) != ordinal:
            continue
        if opposite.get(nxt, nxt) != nxt:
            continue
        row, next_row = user_rows[ordinal], user_rows[nxt]
        if row.layer_type != "front" or next_row.layer_type != "back":
            continue
        if row.user_name is not None and next_row.user_name is not None:
            opposite[ordinal] = nxt
            opposite[nxt] = ordinal

    return {
        CANONICAL_LAYER_NAMES[source]: CANONICAL_LAYER_NAMES[target]
        for source, target in opposite.items()
        if source in CANONICAL_LAYER_NAMES and target in CANONICAL_LAYER_NAMES
    }


def flip_board_layer(board: Board, name: str, opposites: dict[str, str] | None = None) -> str:
    """Return the opposite of *name* on *board*, or *name* when it has none.

    A layer the board does not declare falls back to :func:`flip_layer_name`,
    which is what KiCad does: ``recalcOpposites`` seeds EVERY layer id, whether
    or not the file listed it.

    Pass *opposites* when flipping many items, so the three-pass table is built
    once instead of once per item.
    """
    table = board_layer_opposites(board) if opposites is None else opposites
    flipped = table.get(name)
    if flipped is not None:
        return flipped
    return flip_layer_name(name, board_copper_layer_count(board))


__all__ = [
    "board_copper_layer_count",
    "board_layer_opposites",
    "flip_board_layer",
]
