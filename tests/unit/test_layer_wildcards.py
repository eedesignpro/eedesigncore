"""Layer wildcard expansion, and the selection functions that depend on it.

KiCad collapses a layer pair to one wildcard token on write, and its parser
expands the token again on read. This library keeps the token VERBATIM so the
writer reproduces KiCad's bytes, which leaves every reader to expand it.

That expansion was missing until iteration 19. `on_layers` compared the raw
string, so `on_layers(pad, {"F.Cu"})` was FALSE for a pad whose list is
`["*.Cu"]`. Since `*.Cu` is how KiCad writes every through-hole pad, any caller
selecting pads by copper layer received none, with no error.

These tests hold the expansion at the level every selection function shares.
"""

from __future__ import annotations

import pytest

from eedesigncore import items_bbox, items_on_layers
from eedesigncore.common.layers import (
    LAYER_WILDCARD_ORDINALS,
    LAYER_WILDCARDS,
    expand_layer_names,
    is_layer_wildcard,
    on_layers,
)
from eedesigncore.common.models import Stroke
from eedesigncore.common.pcb_family.enums import PadShape, PadType
from eedesigncore.common.pcb_family.ordering import layer_set_seq
from eedesigncore.common.pcb_family.shapes import PcbLine
from eedesigncore.mod.models import DrillInfo, Pad


def pad(layers: list[str]) -> Pad:
    return Pad(
        number="1",
        pad_type=PadType.thru_hole,
        shape=PadShape.circle,
        size=(2.0, 2.0),
        drill=DrillInfo(width=1.0),
        layers=layers,
    )


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_every_wildcard_kicad_writes_is_in_the_table() -> None:
    """``formatLayers``, ``pcb_io_kicad_sexpr.cpp:1540-1612``."""
    written = {"*.Cu", "F&B.Cu", "*.Adhes", "*.Paste", "*.SilkS", "*.Mask", "*.CrtYd", "*.Fab"}
    assert written <= set(LAYER_WILDCARDS)


def test_the_table_also_holds_a_token_kicad_only_reads() -> None:
    """``m_layerMasks["*In.Cu"]``, ``pcb_io_kicad_sexpr_parser.cpp:121``.

    KiCad's writer never emits this one, so no KiCad-saved file carries it. A
    hand-written or converted file can, and the parser accepts it, so a reader
    that omits it drops every layer of that pad.
    """
    assert "*In.Cu" in LAYER_WILDCARDS
    inner = LAYER_WILDCARDS["*In.Cu"]
    assert "In1.Cu" in inner
    assert "F.Cu" not in inner, "*In.Cu is the INTERNAL copper mask, not all copper"
    assert "B.Cu" not in inner


def test_the_name_table_and_the_ordinal_table_agree() -> None:
    """One table, so a reader that selects by name and one that sorts by ordinal
    can never disagree about what a token means."""
    assert set(LAYER_WILDCARDS) == set(LAYER_WILDCARD_ORDINALS)
    for token in LAYER_WILDCARDS:
        assert len(LAYER_WILDCARDS[token]) == len(LAYER_WILDCARD_ORDINALS[token])


def test_the_ordering_module_reads_the_same_table() -> None:
    """``layer_set_seq`` sorts by ordinal and must expand identically."""
    assert layer_set_seq(["*.Mask"]) == tuple(sorted(LAYER_WILDCARD_ORDINALS["*.Mask"]))
    assert layer_set_seq(["*In.Cu"]) == tuple(sorted(LAYER_WILDCARD_ORDINALS["*In.Cu"]))


def test_is_layer_wildcard_separates_a_token_from_a_layer() -> None:
    assert is_layer_wildcard("*.Cu")
    assert not is_layer_wildcard("F.Cu")


# ---------------------------------------------------------------------------
# expand_layer_names
# ---------------------------------------------------------------------------


def test_expansion_keeps_a_plain_name() -> None:
    assert expand_layer_names(["F.Cu", "B.SilkS"]) == ["F.Cu", "B.SilkS"]


def test_expansion_keeps_a_name_this_library_does_not_know() -> None:
    """Dropping an unknown name would silently lose an item from a newer format."""
    assert expand_layer_names(["Some.Future.Layer"]) == ["Some.Future.Layer"]


def test_expansion_does_not_repeat_a_layer() -> None:
    """``*.Mask`` and ``F.Mask`` together name F.Mask once."""
    assert expand_layer_names(["F.Mask", "*.Mask"]) == ["F.Mask", "B.Mask"]


def test_expansion_holds_the_input_order() -> None:
    assert expand_layer_names(["Edge.Cuts", "*.Mask"]) == ["Edge.Cuts", "F.Mask", "B.Mask"]


# ---------------------------------------------------------------------------
# on_layers, the function every selection shares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layer", ["F.Cu", "B.Cu", "In1.Cu", "In30.Cu"])
def test_a_copper_wildcard_matches_every_copper_layer(layer: str) -> None:
    """The regression this whole file exists for."""
    assert on_layers(pad(["*.Cu"]), {layer})


def test_a_copper_wildcard_does_not_match_another_family() -> None:
    assert not on_layers(pad(["*.Cu"]), {"F.SilkS"})
    assert not on_layers(pad(["*.Cu"]), {"Edge.Cuts"})


def test_a_mask_wildcard_matches_both_sides_only() -> None:
    item = pad(["F.Cu", "*.Mask"])
    assert on_layers(item, {"F.Mask"})
    assert on_layers(item, {"B.Mask"})
    assert on_layers(item, {"F.Cu"})
    assert not on_layers(item, {"B.Cu"})


def test_the_front_and_back_token_is_not_all_copper() -> None:
    """``F&B.Cu`` names two layers. Reading it as ``*.Cu`` would add every inner."""
    item = pad(["F&B.Cu"])
    assert on_layers(item, {"F.Cu"})
    assert on_layers(item, {"B.Cu"})
    assert not on_layers(item, {"In1.Cu"})


def test_no_filter_still_passes_everything() -> None:
    assert on_layers(pad(["*.Cu"]), None)


def test_an_item_naming_no_layer_never_passes_a_filter() -> None:
    assert not on_layers(object(), {"F.Cu"})


# ---------------------------------------------------------------------------
# The public selection functions
# ---------------------------------------------------------------------------


def test_items_on_layers_returns_a_wildcard_pad() -> None:
    """The operator's requirement, stated plainly: this must return the pad."""
    pads = [pad(["*.Cu"]), pad(["F.SilkS"])]
    assert items_on_layers(pads, ["F.Cu"]) == [pads[0]]
    assert items_on_layers(pads, ["In2.Cu"]) == [pads[0]]
    assert items_on_layers(pads, ["F.SilkS"]) == [pads[1]]


def multilayer_line(layers: list[str]) -> PcbLine:
    """A shape on more than one layer, which is what makes KiCad write a wildcard.

    ``format( const PCB_SHAPE* )`` calls ``formatLayers`` only when the layer set
    holds more than one member (``pcb_io_kicad_sexpr.cpp:1094-1095``).
    """
    return PcbLine(
        start=(10.0, 10.0),
        end=(20.0, 10.0),
        stroke=Stroke(width=0.0),
        layers=layers,
    )


def test_items_bbox_measures_a_wildcard_shape() -> None:
    """``items_bbox`` filters through the same function.

    Before the expansion it returned ``None`` for a copper layer holding only
    wildcard items, which reads as an empty board rather than as a selection
    failure.
    """
    box = items_bbox([multilayer_line(["*.Cu"])], layers=["In1.Cu"])
    assert box is not None, "a wildcard shape must be measurable on a copper layer"
    assert box.min_x == pytest.approx(10.0)
    assert box.max_x == pytest.approx(20.0)


def test_items_bbox_still_excludes_a_layer_the_shape_does_not_name() -> None:
    """The widening must not make everything match everything."""
    assert items_bbox([multilayer_line(["*.Cu"])], layers=["F.SilkS"]) is None
