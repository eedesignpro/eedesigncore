"""Canonical KiCad PCB layer-name table and helpers.

Data-driven from the A1 verify report (``verify-board-layer-tables-2026-07-14``):
``LSET::Name`` (lset.cpp:188-260) over the ``PCB_LAYER_ID`` enum (layer_ids.h:59-172).
The explicit-name cases are pinned verbatim; the inner-copper (even 4..62) and
user-definable (odd 39..127) names are computed by the documented rules. The
table is used to validate non-copper layer names on load (parser hard-errors
unknown non-copper names against a fixed hash, A1 §4b) and to map names on the
item side (canonical name only, no ordinals, A1 §5).
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterable
from typing import TypeVar

# Explicitly-cased layers (A1 §1a): ordinal -> canonical name.
_EXPLICIT_LAYER_NAMES: dict[int, str] = {
    0: "F.Cu",
    2: "B.Cu",
    1: "F.Mask",
    3: "B.Mask",
    5: "F.SilkS",
    7: "B.SilkS",
    9: "F.Adhes",
    11: "B.Adhes",
    13: "F.Paste",
    15: "B.Paste",
    17: "Dwgs.User",
    19: "Cmts.User",
    21: "Eco1.User",
    23: "Eco2.User",
    25: "Edge.Cuts",
    27: "Margin",
    29: "B.CrtYd",
    31: "F.CrtYd",
    33: "B.Fab",
    35: "F.Fab",
    37: "Rescue",
}


def _build_canonical_names() -> dict[int, str]:
    names = dict(_EXPLICIT_LAYER_NAMES)
    # Inner copper: even ordinals 4..62 -> In1..In30 (offset = (id - B_Cu) / 2).
    for oid in range(4, 63, 2):
        names.setdefault(oid, f"In{(oid - 2) // 2}.Cu")
    # User-definable: odd ordinals 39..127 -> User.1..User.45 (offset = (id - Rescue) / 2).
    for oid in range(39, 128, 2):
        names.setdefault(oid, f"User.{(oid - 37) // 2}")
    return names


# ordinal -> canonical name, for all valid PCB_LAYER_IDs (0..127).
CANONICAL_LAYER_NAMES: dict[int, str] = _build_canonical_names()

# canonical name -> ordinal (inverse map).
LAYER_NAME_TO_ORDINAL: dict[str, int] = {v: k for k, v in CANONICAL_LAYER_NAMES.items()}

# Set of all canonical layer names.
CANONICAL_LAYER_NAME_SET: frozenset[str] = frozenset(CANONICAL_LAYER_NAMES.values())

T = TypeVar("T")

_INNER_COPPER_RE = re.compile(r"^In\d+\.Cu$")


def is_copper_layer_name(name: str) -> bool:
    """Return True for ``F.Cu``, ``B.Cu`` or any ``In<N>.Cu`` name (A1 §4b)."""
    return name in ("F.Cu", "B.Cu") or bool(_INNER_COPPER_RE.match(name))


def is_valid_layer_name(name: str) -> bool:
    """Return True if *name* is a canonical layer name or matches the copper pattern.

    Mirrors KiCad's parser: non-copper names must be in the fixed hash
    (canonical table); copper names matching ``In<N>.Cu`` are tolerated even
    beyond the enumerated range.
    """
    return name in CANONICAL_LAYER_NAME_SET or is_copper_layer_name(name)


# ---------------------------------------------------------------------------
# Layer wildcards
# ---------------------------------------------------------------------------

#: Ordinals of every copper layer KiCad enumerates, front, back, then inner.
#:
#: ``*.Cu`` means "every copper layer the BOARD enables". A standalone footprint
#: file cannot know that count, so the token expands to the full enumerated
#: range (ADR-060).
_ALL_COPPER_ORDINALS = (0, 2, *range(4, 63, 2))

#: Ordinals of the INNER copper layers alone, which is ``LSET::InternalCuMask``.
_INNER_COPPER_ORDINALS = tuple(range(4, 63, 2))

#: Layer wildcards KiCad writes, and the layer ids each one stands for.
#:
#: Source: ``PCB_IO_KICAD_SEXPR::formatLayers``
#: (``pcb_io_kicad_sexpr.cpp:1540-1612``). KiCad collapses a layer pair to one
#: wildcard token on write, and the parser expands it again on read
#: (``pcb_io_kicad_sexpr_parser.cpp:120-127``).
#:
#: The loader keeps the token VERBATIM so the writer reproduces KiCad's bytes.
#: That leaves every reader to expand it, which is what :func:`on_layers` and
#: ``pcb_family.ordering.layer_set_seq`` both do from this one table.
LAYER_WILDCARD_ORDINALS: dict[str, tuple[int, ...]] = {
    "*.Cu": _ALL_COPPER_ORDINALS,
    # KiCad's PARSER accepts this one and its writer never emits it
    # (`m_layerMasks["*In.Cu"]`, pcb_io_kicad_sexpr_parser.cpp:121). A
    # hand-written or converted file can still carry it, and the loader keeps
    # every token verbatim, so a reader has to know it.
    "*In.Cu": _INNER_COPPER_ORDINALS,
    "F&B.Cu": (0, 2),
    "*.Adhes": (9, 11),
    "*.Paste": (13, 15),
    "*.SilkS": (5, 7),
    "*.Mask": (1, 3),
    "*.CrtYd": (29, 31),
    "*.Fab": (33, 35),
}

#: The same table by canonical NAME, for callers that select by name.
LAYER_WILDCARDS: dict[str, tuple[str, ...]] = {
    token: tuple(CANONICAL_LAYER_NAMES[ordinal] for ordinal in ordinals)
    for token, ordinals in LAYER_WILDCARD_ORDINALS.items()
}


def is_layer_wildcard(name: str) -> bool:
    """True when *name* is a wildcard token rather than one layer name."""
    return name in LAYER_WILDCARDS


def expand_layer_names(names: Iterable[str]) -> list[str]:
    """Return *names* with every wildcard replaced by the layers it stands for.

    A name that is not a wildcard passes through unchanged, including a name this
    library does not recognize. Order follows the input, and a layer named twice
    appears once.
    """
    out: dict[str, None] = {}
    for name in names:
        expanded = LAYER_WILDCARDS.get(name)
        if expanded is None:
            out[name] = None
        else:
            for layer in expanded:
                out[layer] = None
    return list(out)


# ---------------------------------------------------------------------------
# Flipping a layer to the other side of the board
# ---------------------------------------------------------------------------

_FLIP_LAYER_PAIRS: dict[str, str] = {}
for _front, _back in (
    ("F.Cu", "B.Cu"),
    ("F.SilkS", "B.SilkS"),
    ("F.Adhes", "B.Adhes"),
    ("F.Mask", "B.Mask"),
    ("F.Paste", "B.Paste"),
    ("F.CrtYd", "B.CrtYd"),
    ("F.Fab", "B.Fab"),
):
    _FLIP_LAYER_PAIRS[_front] = _back
    _FLIP_LAYER_PAIRS[_back] = _front

_IN1_CU_ORDINAL = LAYER_NAME_TO_ORDINAL["In1.Cu"]


def flip_layer_name(name: str, copper_layer_count: int = 0) -> str:
    """Return the layer on the opposite side of the board, or *name* unchanged.

    Port of the free function ``FlipLayer`` (``common/layer_id.cpp:172-217``).
    Seven technical pairs always swap. Inner copper swaps only when the board
    carries four or more copper layers, by index from ``In1.Cu`` and clamped at
    both ends. Every other layer maps to itself.

    This is the NO-BOARD fallback. A board computes its own table, because a
    user layer pair depends on the layer names that board declares. See
    :func:`eedesigncore.pcb.layers.flip_board_layer`.
    """
    paired = _FLIP_LAYER_PAIRS.get(name)
    if paired is not None:
        return paired
    if copper_layer_count < 4 or not _INNER_COPPER_RE.match(name):
        return name
    ordinal = LAYER_NAME_TO_ORDINAL.get(name)
    if ordinal is None:
        # An In<N>.Cu past the enumerated range: tolerated on load, no ordinal.
        return name
    max_index = copper_layer_count - 3
    inner_index = (ordinal - _IN1_CU_ORDINAL) // 2
    flipped_index = min(max(max_index - inner_index, 0), max_index)
    return CANONICAL_LAYER_NAMES[_IN1_CU_ORDINAL + flipped_index * 2]


def item_layers(item: object) -> list[str]:
    """Return the layer names *item* sits on, empty when it names none.

    The rule is one line of KiCad and easy to get wrong: a MULTI-LAYER shape
    emits ``(layers …)`` instead of ``(layer …)``
    (``pcb_io_kicad_sexpr.cpp:1094-1097``), so ``layers`` wins whenever it is
    present and ``layer`` means nothing then. A via names its start and end
    layers the same way, as a pair.

    An item that names no layer at all, such as a group, gives an empty list.
    """
    layers = getattr(item, "layers", None)
    if isinstance(layers, list | tuple) and layers:
        return [str(name) for name in layers]
    layer = getattr(item, "layer", None)
    return [layer] if isinstance(layer, str) and layer else []


def on_layers(item: object, layers: Container[str] | None) -> bool:
    """True when *item* names at least one layer in *layers*.

    ``layers=None`` means no filter, so everything passes. With a filter, an item
    that names no layer never passes, because there is nothing to match.

    A WILDCARD in the item expands before the test. A pad writes ``*.Cu`` for
    every copper layer (``formatLayers``, ``pcb_io_kicad_sexpr.cpp:1540-1612``)
    and the loader keeps that token verbatim, so a reader that compares the raw
    string finds no match on ``F.Cu``. Every caller that selects pads by copper
    layer depended on this, and every one of them got nothing back.

    The expansion runs on the ITEM side only. A caller asking for ``*.Cu`` is
    asking for one name, and this function answers about the layers an item
    occupies, not about a query language.
    """
    if layers is None:
        return True
    return any(name in layers for name in expand_layer_names(item_layers(item)))


def items_on_layers(items: Iterable[T], layers: Container[str] | None) -> list[T]:
    """Return the items of *items* that :func:`on_layers` accepts.

    This is the selection ``board_bbox``, ``translate_items`` and their
    neighbours apply to their own ``layers=`` argument, exposed so a caller does
    not have to restate the multi-layer rule.
    """
    return [item for item in items if on_layers(item, layers)]


__all__ = [
    "CANONICAL_LAYER_NAME_SET",
    "flip_layer_name",
    "is_copper_layer_name",
    "is_valid_layer_name",
    "item_layers",
    "items_on_layers",
    "on_layers",
]
