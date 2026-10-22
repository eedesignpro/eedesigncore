"""Backdrill depth follows supplied material elevations and selected stop policy."""

from dataclasses import replace

import pytest

from eedesigncore.common.pcb_family.footprint_scoped import BackdrillInfo
from eedesigncore.pcb.machining import CopperDepthInterval, resolve_backdrill_depth

COPPER = (
    CopperDepthInterval("F.Cu", 1.56, 1.60),
    CopperDepthInterval("In1.Cu", 1.10, 1.14),
    CopperDepthInterval("In2.Cu", 0.46, 0.50),
    CopperDepthInterval("B.Cu", 0.00, 0.04),
)


@pytest.mark.parametrize(
    ("entry", "stop", "expected", "following"),
    [("F.Cu", "In1.Cu", (0.80, 1.72), "In2.Cu"), ("B.Cu", "In2.Cu", (-0.12, 0.80), "In1.Cu")],
)
def test_midpoint_follows_direction_and_keeps_source(entry, stop, expected, following):
    drill = BackdrillInfo(size=0.8, start_layer=entry, end_layer=stop)
    before = drill.model_dump()
    result = resolve_backdrill_depth(
        drill,
        COPPER,
        policy="between-layers",
        surface_bottom_mm=-0.02,
        surface_top_mm=1.62,
        entry_extension_mm=0.1,
    )
    assert (result.bottom_mm, result.top_mm) == pytest.approx(expected)
    assert result.next_layer == following
    assert drill.model_dump() == before


def test_native_reaches_stop_copper_and_selected_margin():
    result = resolve_backdrill_depth(
        BackdrillInfo(size=0.8, start_layer="F.Cu", end_layer="In1.Cu"),
        COPPER,
        policy="native",
        surface_bottom_mm=-0.02,
        surface_top_mm=1.62,
        entry_extension_mm=0.5,
        axial_margin_mm=0.001,
    )
    assert (result.bottom_mm, result.top_mm) == pytest.approx((1.099, 2.121))
    assert result.next_layer is None


def test_datum_translation_moves_every_depth_equally():
    drill = BackdrillInfo(size=0.8, start_layer="B.Cu", end_layer="In2.Cu")
    shifted = tuple(
        replace(row, bottom_mm=row.bottom_mm - 2, top_mm=row.top_mm - 2) for row in COPPER
    )
    result = resolve_backdrill_depth(
        drill,
        shifted,
        policy="between-layers",
        surface_bottom_mm=-2.02,
        surface_top_mm=-0.38,
        entry_extension_mm=0.1,
    )
    assert (result.bottom_mm, result.top_mm) == pytest.approx((-2.12, -1.2))


@pytest.mark.parametrize(
    "bad",
    [
        COPPER[::-1],
        (COPPER[0], COPPER[0], COPPER[-1]),
        (COPPER[0], replace(COPPER[1], top_mm=1.58), COPPER[-1]),
    ],
)
def test_bad_layer_intervals_reject(bad):
    with pytest.raises(ValueError):
        resolve_backdrill_depth(
            BackdrillInfo(size=0.8, start_layer="F.Cu", end_layer="B.Cu"),
            bad,
            policy="native",
            surface_bottom_mm=-0.02,
            surface_top_mm=1.62,
            entry_extension_mm=0.1,
        )


def test_midpoint_rejects_no_following_layer():
    with pytest.raises(ValueError, match="beyond"):
        resolve_backdrill_depth(
            BackdrillInfo(size=0.8, start_layer="F.Cu", end_layer="B.Cu"),
            COPPER,
            policy="between-layers",
            surface_bottom_mm=-0.02,
            surface_top_mm=1.62,
            entry_extension_mm=0.1,
        )
