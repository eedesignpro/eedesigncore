"""Unit tests for mod/enums.py."""

import pytest

from eedesigncore.mod.enums import PadProperty, PadShape, PadType


class TestPadType:
    def test_all_values_instantiate(self) -> None:
        assert PadType("thru_hole") == PadType.thru_hole
        assert PadType("smd") == PadType.smd
        assert PadType("connect") == PadType.connect
        assert PadType("np_thru_hole") == PadType.np_thru_hole

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            PadType("unknown_type")

    def test_value_is_string(self) -> None:
        assert PadType.smd.value == "smd"
        assert PadType.thru_hole.value == "thru_hole"


class TestPadShape:
    def test_all_values_instantiate(self) -> None:
        assert PadShape("circle") == PadShape.circle
        assert PadShape("rect") == PadShape.rect
        assert PadShape("oval") == PadShape.oval
        assert PadShape("trapezoid") == PadShape.trapezoid
        assert PadShape("roundrect") == PadShape.roundrect
        assert PadShape("custom") == PadShape.custom

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            PadShape("hexagon")

    def test_value_is_string(self) -> None:
        assert PadShape.roundrect.value == "roundrect"


class TestPadProperty:
    def test_all_values_instantiate(self) -> None:
        assert PadProperty("pad_prop_bga") == PadProperty.bga
        assert PadProperty("pad_prop_fiducial_glob") == PadProperty.fiducial_glob
        assert PadProperty("pad_prop_fiducial_loc") == PadProperty.fiducial_loc
        assert PadProperty("pad_prop_testpoint") == PadProperty.testpoint
        assert PadProperty("pad_prop_heatsink") == PadProperty.heatsink
        assert PadProperty("pad_prop_castellated") == PadProperty.castellated
        assert PadProperty("pad_prop_mechanical") == PadProperty.mechanical
        assert PadProperty("pad_prop_pressfit") == PadProperty.pressfit

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            PadProperty("pad_prop_unknown")

    def test_bga_value(self) -> None:
        assert PadProperty.bga.value == "pad_prop_bga"
