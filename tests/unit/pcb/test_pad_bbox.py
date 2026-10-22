"""The extent of a pad: copper on every layer, and the hole.

``PAD::BuildEffectiveShapes`` (``pad.cpp:1093-1136``) merges the bounding box of
the copper on every unique padstack layer, then merges the hole. A measurement
that reads only the top-level ``size`` gets three cases wrong, and each one has a
test below.
"""

from __future__ import annotations

import pytest

from eedesigncore import footprint_bbox, items_bbox
from eedesigncore.common.pcb_family.enums import PadShape, PadType
from eedesigncore.common.pcb_family.footprint_scoped import (
    Padstack,
    PadstackLayer,
    PadstackMode,
)
from eedesigncore.mod.models import DrillInfo, Footprint, FootprintPlacement, Pad
from eedesigncore.mod.pad_geometry import pad_hole_extent_mm, pad_unique_layers


def smd(**kwargs: object) -> Pad:
    base: dict[str, object] = {
        "number": "1",
        "pad_type": PadType.smd,
        "shape": PadShape.rect,
        "size": (1.0, 1.0),
        "layers": ["F.Cu"],
    }
    base.update(kwargs)
    return Pad(**base)  # type: ignore[arg-type]


def through(**kwargs: object) -> Pad:
    base: dict[str, object] = {
        "number": "1",
        "pad_type": PadType.thru_hole,
        "shape": PadShape.circle,
        "size": (1.0, 1.0),
        "drill": DrillInfo(width=0.5),
        "layers": ["*.Cu"],
    }
    base.update(kwargs)
    return Pad(**base)  # type: ignore[arg-type]


def span(pad: Pad) -> tuple[float, float, float, float]:
    box = items_bbox([pad])
    assert box is not None, "a pad must be measurable"
    return (box.min_x, box.min_y, box.max_x, box.max_y)


# ---------------------------------------------------------------------------
# A pad is measurable at all
# ---------------------------------------------------------------------------


def test_items_bbox_accepts_a_bare_pad() -> None:
    """It raised ``TypeError`` before, so no caller could measure a pad."""
    assert items_bbox([smd()]) is not None


def test_a_pad_alone_is_measured_in_the_frame_it_arrives_in() -> None:
    """A pad stores a FOOTPRINT-RELATIVE position.

    Measured alone it gives the footprint frame. Measured through its footprint
    it gives the board frame. Both are right, and the difference is worth
    stating because the same object gives two answers.
    """
    pad = smd(position=(2.0, 0.0))
    footprint = Footprint(
        layer="F.Cu",
        at=FootprintPlacement(position=(100.0, 50.0), angle=0.0),
        pads=[pad],
    )
    alone = items_bbox([pad])
    placed = footprint_bbox(footprint)
    assert alone is not None and placed is not None
    assert alone.min_x == pytest.approx(1.5)
    assert placed.min_x == pytest.approx(101.5)


# ---------------------------------------------------------------------------
# 1. The hole can be wider than the copper
# ---------------------------------------------------------------------------


def test_a_drill_wider_than_the_copper_grows_the_extent() -> None:
    """``pad.cpp:1136`` merges the hole shape into the pad's bounding box.

    The old measurement recorded the opposite in its own docstring: "a drill
    wider than the pad ... do not change the envelope".
    """
    assert span(through(size=(1.0, 1.0), drill=DrillInfo(width=3.0))) == pytest.approx(
        (-1.5, -1.5, 1.5, 1.5)
    )


def test_a_drill_narrower_than_the_copper_changes_nothing() -> None:
    assert span(through(size=(4.0, 4.0), drill=DrillInfo(width=0.5))) == pytest.approx(
        (-2.0, -2.0, 2.0, 2.0)
    )


def test_a_slot_grows_the_extent_along_its_length_only() -> None:
    """A slot is a thick segment, so its two axes do not grow together."""
    pad = through(size=(1.0, 1.0), drill=DrillInfo(oval=True, width=4.0, height=1.0))
    assert span(pad) == pytest.approx((-2.0, -0.5, 2.0, 0.5))


def test_a_turned_slot_turns_its_extent() -> None:
    """``RotatePoint( half_len, GetOrientation() )``, ``pad.cpp:1131``."""
    pad = through(
        size=(1.0, 1.0),
        angle=90.0,
        drill=DrillInfo(oval=True, width=4.0, height=1.0),
    )
    assert span(pad) == pytest.approx((-0.5, -2.0, 0.5, 2.0))


def test_a_pad_with_no_drill_is_measured_on_copper_alone() -> None:
    assert pad_hole_extent_mm(smd(size=(2.0, 3.0))) is None
    assert span(smd(size=(2.0, 3.0))) == pytest.approx((-1.0, -1.5, 1.0, 1.5))


def test_a_zero_width_drill_adds_nothing() -> None:
    pad = through(size=(2.0, 2.0), drill=DrillInfo(width=0.0))
    assert pad_hole_extent_mm(pad) is None
    assert span(pad) == pytest.approx((-1.0, -1.0, 1.0, 1.0))


# ---------------------------------------------------------------------------
# 2. An inner layer can be the widest
# ---------------------------------------------------------------------------


def padstack_pad(front: float, inner: float, back: float) -> Pad:
    return through(
        shape=PadShape.rect,
        size=(front, front),
        padstack=Padstack(
            mode=PadstackMode.front_inner_back,
            layers=[
                PadstackLayer(layer_name="Inner", size=(inner, inner)),
                PadstackLayer(layer_name="B.Cu", size=(back, back)),
            ],
        ),
    )


def test_an_inner_layer_wider_than_the_front_grows_the_extent() -> None:
    """``ForEachUniqueLayer``, ``pad.cpp:1107-1112``.

    Nothing says the front is the largest. Reading ``pad.size`` alone measures
    the front and misses every other layer.
    """
    assert span(padstack_pad(front=1.0, inner=6.0, back=2.0)) == pytest.approx(
        (-3.0, -3.0, 3.0, 3.0)
    )


def test_a_back_layer_wider_than_the_front_grows_the_extent() -> None:
    assert span(padstack_pad(front=1.0, inner=1.0, back=5.0)) == pytest.approx(
        (-2.5, -2.5, 2.5, 2.5)
    )


def test_a_front_wider_than_the_rest_still_wins() -> None:
    assert span(padstack_pad(front=8.0, inner=1.0, back=2.0)) == pytest.approx(
        (-4.0, -4.0, 4.0, 4.0)
    )


def test_a_custom_padstack_covers_every_named_layer() -> None:
    pad = through(
        shape=PadShape.rect,
        size=(1.0, 1.0),
        padstack=Padstack(
            mode=PadstackMode.custom,
            layers=[
                PadstackLayer(layer_name="In2.Cu", size=(7.0, 1.0)),
                PadstackLayer(layer_name="B.Cu", size=(1.0, 3.0)),
            ],
        ),
    )
    assert span(pad) == pytest.approx((-3.5, -1.5, 3.5, 1.5))


def test_unique_layers_names_one_per_distinct_geometry() -> None:
    assert pad_unique_layers(smd()) == ("F.Cu",)
    assert pad_unique_layers(padstack_pad(1.0, 2.0, 3.0)) == ("F.Cu", "In1.Cu", "B.Cu")


# ---------------------------------------------------------------------------
# 3. A shape offset moves the copper, not the hole
# ---------------------------------------------------------------------------


def test_a_shape_offset_moves_the_copper_away_from_the_hole() -> None:
    """``PAD::ShapePos``, ``pad.cpp:1558-1570``.

    The copper is centred on the offset position, and the hole stays at the pad
    position. The extent is the union of two shapes about DIFFERENT centres.
    """
    pad = through(
        shape=PadShape.rect,
        size=(2.0, 2.0),
        drill=DrillInfo(width=0.5, offset=(3.0, 0.0)),
    )
    # Copper spans 2.0 to 4.0. The hole spans -0.25 to 0.25 about the origin.
    assert span(pad) == pytest.approx((-0.25, -1.0, 4.0, 1.0))


def test_a_turned_pad_turns_its_offset() -> None:
    pad = through(
        shape=PadShape.rect,
        size=(2.0, 2.0),
        angle=90.0,
        drill=DrillInfo(width=0.5, offset=(3.0, 0.0)),
    )
    assert span(pad) == pytest.approx((-1.0, -4.0, 1.0, 0.25))


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_a_turned_rectangular_pad_keeps_its_turned_envelope() -> None:
    """A 45 degree turn reaches further than the axis-aligned size."""
    pad = smd(shape=PadShape.rect, size=(2.0, 2.0), angle=45.0)
    reach = 2.0**0.5
    assert span(pad) == pytest.approx((-reach, -reach, reach, reach))


def test_a_layer_filter_still_excludes_a_pad() -> None:
    assert items_bbox([smd(layers=["F.Cu"])], layers=["B.Cu"]) is None


def test_a_wildcard_pad_passes_a_copper_layer_filter() -> None:
    """The layer expansion and the pad extent meet here."""
    assert items_bbox([through()], layers=["In3.Cu"]) is not None
