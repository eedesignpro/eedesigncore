"""Native border tips retain their relation to the electrical anchor."""

from __future__ import annotations

import math

import pytest

from eedesigncore import global_label_outline


@pytest.mark.parametrize("shape", ["input", "output", "bidirectional", "tri_state", "passive"])
@pytest.mark.parametrize(
    "direction,axis",
    [
        ("right", (1, 0)),
        ("left", (-1, 0)),
        ("top", (0, -1)),
        ("bottom", (0, 1)),
    ],
)
def test_native_tip_mapping(shape, direction, axis):
    anchor = (17.0, -4.0)
    points = global_label_outline(
        text_width_mm=10,
        text_height_mm=2,
        shape=shape,
        anchor=anchor,
        body_direction=direction,
        pen_width_mm=0.2,
    )
    assert points[0] == points[-1]
    projected = [(x - anchor[0]) * axis[0] + (y - anchor[1]) * axis[1] for x, y in points]
    assert min(projected) == pytest.approx(0)
    assert projected[0] == pytest.approx(0)
    assert (projected[1] > 1) == (shape in ("input", "bidirectional", "tri_state"))
    far_tip = projected[3] - projected[2]
    assert far_tip == pytest.approx(
        1.75 if shape in ("output", "bidirectional", "tri_state") else 0
    )


def test_source_dimensions_and_closed_passive_rectangle():
    points = global_label_outline(
        text_width_mm=10,
        text_height_mm=2,
        shape="passive",
        body_direction="left",
        pen_width_mm=0.2,
    )
    expected = [
        (0, 0),
        (0, -1.9503),
        (-11.7003, -1.9503),
        (-11.7003, 0),
        (-11.7003, 1.9503),
        (0, 1.9503),
        (0, 0),
    ]
    for actual, wanted in zip(points, expected, strict=True):
        assert actual == pytest.approx(wanted)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1])
def test_bad_dimensions_fail(bad):
    with pytest.raises(ValueError):
        global_label_outline(text_width_mm=bad, text_height_mm=1, shape="passive")


def test_unknown_is_not_silently_reclassified():
    with pytest.raises(ValueError, match="shape"):
        global_label_outline(text_width_mm=3, text_height_mm=1, shape="unknown")


def test_diagram_origin_is_not_recentered():
    original = global_label_outline(text_width_mm=3, text_height_mm=1, shape="input")
    shifted = global_label_outline(
        text_width_mm=3, text_height_mm=1, shape="input", anchor=(21, -7)
    )
    for point, translated in zip(original, shifted, strict=True):
        assert translated == pytest.approx((point[0] + 21, point[1] - 7))
