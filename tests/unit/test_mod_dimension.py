"""Unit tests for dimension round-trips in mod loader/writer (iter-6 coverage)."""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.mod.models import Footprint

_HEAD = """\
(footprint "TEST"
\t(version 20260206)
\t(generator "test")
\t(layer "F.Cu")
\t(duplicate_pad_numbers_are_jumpers no)
\t(embedded_fonts no)
"""


def _make(extra: str) -> str:
    return _HEAD + extra + "\n)"


def _round_trip(sexpr: str) -> Footprint:
    fp = Footprint.from_string(sexpr)
    out = fp.to_string()
    return Footprint.from_string(out)


# ---------------------------------------------------------------------------
# Aligned dimension
# ---------------------------------------------------------------------------

_ALIGNED_DIM = _make(
    """\
\t(dimension (type aligned)
\t\t(layer "Dwgs.User")
\t\t(uuid "dim-uuid-1")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 10 0)
\t\t)
\t\t(height 1.0)
\t\t(format
\t\t\t(prefix "")
\t\t\t(suffix "mm")
\t\t\t(units 3)
\t\t\t(units_format 1)
\t\t\t(precision 4)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(arrow_direction inward)
\t\t\t(extension_height 0.5)
\t\t\t(extension_offset 0)
\t\t\t(keep_text_aligned yes)
\t\t)
\t\t(gr_text ""
\t\t\t(at 5 -1 0)
\t\t\t(layer "Dwgs.User")
\t\t)
\t)
"""
)


class TestAlignedDimension:
    def test_parses_type(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        from eedesigncore.common.pcb_family.board_items import DimensionType

        assert len(fp.dimensions) == 1
        assert fp.dimensions[0].dim_type == DimensionType.aligned

    def test_layer(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        assert fp.dimensions[0].layer == "Dwgs.User"

    def test_id(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        assert fp.dimensions[0].id == "dim-uuid-1"

    def test_pts(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        pts = fp.dimensions[0].pts
        assert len(pts) == 2
        assert pts[0] == (0.0, 0.0)
        assert pts[1] == (10.0, 0.0)

    def test_height(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        assert fp.dimensions[0].height == pytest.approx(1.0)

    def test_format_suffix(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        fmt = fp.dimensions[0].dim_format
        assert fmt is not None
        assert fmt.suffix == "mm"

    def test_style_arrow_direction(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        from eedesigncore.common.pcb_family.board_items import ArrowDirection

        st = fp.dimensions[0].style
        assert st.arrow_direction == ArrowDirection.inward
        assert st.extension_height == pytest.approx(0.5)
        assert st.keep_text_aligned is True

    def test_gr_text_parsed(self) -> None:
        fp = Footprint.from_string(_ALIGNED_DIM)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.position == (5.0, -1.0)

    def test_round_trip_stable(self) -> None:
        fp1 = Footprint.from_string(_ALIGNED_DIM)
        fp2 = _round_trip(_ALIGNED_DIM)
        assert fp1.dimensions[0] == fp2.dimensions[0]


# ---------------------------------------------------------------------------
# Orthogonal dimension
# ---------------------------------------------------------------------------

_ORTHOGONAL_DIM = _make(
    """\
\t(dimension (type orthogonal)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 0 5)
\t\t)
\t\t(height 2.0)
\t\t(orientation 1)
\t\t(format
\t\t\t(prefix "H=")
\t\t\t(suffix "")
\t\t\t(units 3)
\t\t\t(units_format 1)
\t\t\t(precision 4)
\t\t\t(suppress_zeroes yes)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(arrow_direction outward)
\t\t\t(extension_offset 0.5)
\t\t)
\t\t(gr_text "H=5mm"
\t\t\t(at 2 2.5 0)
\t\t\t(layer "Dwgs.User")
\t\t)
\t)
"""
)


class TestOrthogonalDimension:
    def test_orthogonal_type(self) -> None:
        fp = Footprint.from_string(_ORTHOGONAL_DIM)
        from eedesigncore.common.pcb_family.board_items import DimensionType

        assert fp.dimensions[0].dim_type == DimensionType.orthogonal

    def test_orientation(self) -> None:
        fp = Footprint.from_string(_ORTHOGONAL_DIM)
        assert fp.dimensions[0].orientation == 1

    def test_suppress_zeroes(self) -> None:
        fp = Footprint.from_string(_ORTHOGONAL_DIM)
        fmt = fp.dimensions[0].dim_format
        assert fmt is not None
        assert fmt.suppress_zeroes is True

    def test_outward_arrow_direction(self) -> None:
        fp = Footprint.from_string(_ORTHOGONAL_DIM)
        from eedesigncore.common.pcb_family.board_items import ArrowDirection

        assert fp.dimensions[0].style.arrow_direction == ArrowDirection.outward

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_ORTHOGONAL_DIM)
        fp2 = _round_trip(_ORTHOGONAL_DIM)
        assert fp1.dimensions[0] == fp2.dimensions[0]


# ---------------------------------------------------------------------------
# Leader dimension
# ---------------------------------------------------------------------------

_LEADER_DIM = _make(
    """\
\t(dimension (type leader)
\t\t(layer "F.Fab")
\t\t(pts
\t\t\t(xy 1 1)
\t\t\t(xy 3 3)
\t\t)
\t\t(leader_length 1.5)
\t\t(format
\t\t\t(prefix "")
\t\t\t(suffix "")
\t\t\t(units 3)
\t\t\t(units_format 1)
\t\t\t(precision 4)
\t\t\t(override_value "REF")
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(text_frame 1)
\t\t\t(extension_offset 0)
\t\t)
\t\t(gr_text "REF"
\t\t\t(at 3 3 0)
\t\t\t(layer "F.Fab")
\t\t)
\t)
"""
)


class TestLeaderDimension:
    def test_leader_type(self) -> None:
        fp = Footprint.from_string(_LEADER_DIM)
        from eedesigncore.common.pcb_family.board_items import DimensionType

        assert fp.dimensions[0].dim_type == DimensionType.leader

    def test_leader_length(self) -> None:
        fp = Footprint.from_string(_LEADER_DIM)
        assert fp.dimensions[0].leader_length == pytest.approx(1.5)

    def test_override_value(self) -> None:
        fp = Footprint.from_string(_LEADER_DIM)
        fmt = fp.dimensions[0].dim_format
        assert fmt is not None
        assert fmt.override_value == "REF"

    def test_text_frame(self) -> None:
        fp = Footprint.from_string(_LEADER_DIM)
        assert fp.dimensions[0].style.text_frame == 1

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_LEADER_DIM)
        fp2 = _round_trip(_LEADER_DIM)
        assert fp1.dimensions[0] == fp2.dimensions[0]


# ---------------------------------------------------------------------------
# Center dimension (no format, no gr_text)
# ---------------------------------------------------------------------------

_CENTER_DIM = _make(
    """\
\t(dimension (type center)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 5 5)
\t\t\t(xy 7 5)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 0.2)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t)
"""
)


class TestCenterDimension:
    def test_center_type(self) -> None:
        fp = Footprint.from_string(_CENTER_DIM)
        from eedesigncore.common.pcb_family.board_items import DimensionType

        assert fp.dimensions[0].dim_type == DimensionType.center

    def test_no_format(self) -> None:
        fp = Footprint.from_string(_CENTER_DIM)
        assert fp.dimensions[0].dim_format is None

    def test_no_text(self) -> None:
        fp = Footprint.from_string(_CENTER_DIM)
        assert fp.dimensions[0].text is None

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_CENTER_DIM)
        fp2 = _round_trip(_CENTER_DIM)
        assert fp1.dimensions[0] == fp2.dimensions[0]


# ---------------------------------------------------------------------------
# Radial dimension
# ---------------------------------------------------------------------------

_RADIAL_DIM = _make(
    """\
\t(dimension (type radial)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 5 5)
\t\t\t(xy 8 5)
\t\t)
\t\t(leader_length 0.5)
\t\t(format
\t\t\t(prefix "R=")
\t\t\t(suffix "")
\t\t\t(units 3)
\t\t\t(units_format 1)
\t\t\t(precision 4)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t\t(gr_text "R=3mm"
\t\t\t(at 8 4 0)
\t\t\t(layer "Dwgs.User")
\t\t)
\t)
"""
)


class TestRadialDimension:
    def test_radial_type(self) -> None:
        fp = Footprint.from_string(_RADIAL_DIM)
        from eedesigncore.common.pcb_family.board_items import DimensionType

        assert fp.dimensions[0].dim_type == DimensionType.radial

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_RADIAL_DIM)
        fp2 = _round_trip(_RADIAL_DIM)
        assert fp1.dimensions[0] == fp2.dimensions[0]


# ---------------------------------------------------------------------------
# Dimension text locked/unlocked/knockout flags
# ---------------------------------------------------------------------------

_DIM_TEXT_FLAGS = _make(
    """\
\t(dimension (type aligned)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(height 1.0)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t\t(gr_text "test"
\t\t\t(at 2.5 -1 0)
\t\t\t(locked yes)
\t\t\t(unlocked yes)
\t\t\t(layer "Dwgs.User" knockout)
\t\t\t(hide yes)
\t\t\t(uuid "txt-uuid-1")
\t\t)
\t)
"""
)


class TestDimensionTextFlags:
    def test_text_locked(self) -> None:
        fp = Footprint.from_string(_DIM_TEXT_FLAGS)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.locked is True

    def test_text_unlocked(self) -> None:
        fp = Footprint.from_string(_DIM_TEXT_FLAGS)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.unlocked is True

    def test_text_knockout(self) -> None:
        fp = Footprint.from_string(_DIM_TEXT_FLAGS)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.knockout is True

    def test_text_hidden(self) -> None:
        fp = Footprint.from_string(_DIM_TEXT_FLAGS)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.visible is False

    def test_text_uuid(self) -> None:
        fp = Footprint.from_string(_DIM_TEXT_FLAGS)
        txt = fp.dimensions[0].text
        assert txt is not None
        assert txt.id == "txt-uuid-1"

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_DIM_TEXT_FLAGS)
        fp2 = _round_trip(_DIM_TEXT_FLAGS)
        assert fp1.dimensions[0].text == fp2.dimensions[0].text


# ---------------------------------------------------------------------------
# Dimension locked via bool token
# ---------------------------------------------------------------------------

_LOCKED_DIM = _make(
    """\
\t(dimension (type aligned)
\t\t(locked yes)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(height 1.0)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t)
"""
)


class TestLockedDimension:
    def test_locked_flag(self) -> None:
        fp = Footprint.from_string(_LOCKED_DIM)
        assert fp.dimensions[0].locked is True

    def test_round_trip(self) -> None:
        fp1 = Footprint.from_string(_LOCKED_DIM)
        fp2 = _round_trip(_LOCKED_DIM)
        assert fp1.dimensions[0].locked == fp2.dimensions[0].locked


# ---------------------------------------------------------------------------
# Hard-error tests
# ---------------------------------------------------------------------------


class TestDimensionErrors:
    def test_unknown_dim_style_token_raises(self) -> None:
        bad = _make(
            """\
\t(dimension (type aligned)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t\t(bogus_style_token 99)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in dimension style"):
            Footprint.from_string(bad)

    def test_unknown_dim_format_token_raises(self) -> None:
        bad = _make(
            """\
\t(dimension (type aligned)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(format
\t\t\t(prefix "")
\t\t\t(suffix "")
\t\t\t(units 3)
\t\t\t(units_format 1)
\t\t\t(precision 4)
\t\t\t(bogus_fmt_token 1)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in dimension format"):
            Footprint.from_string(bad)

    def test_unknown_dim_token_raises(self) -> None:
        bad = _make(
            """\
\t(dimension (type aligned)
\t\t(layer "Dwgs.User")
\t\t(pts
\t\t\t(xy 0 0)
\t\t\t(xy 5 0)
\t\t)
\t\t(style
\t\t\t(thickness 0.1)
\t\t\t(arrow_length 1.27)
\t\t\t(text_position_mode 0)
\t\t\t(extension_offset 0)
\t\t)
\t\t(bogus_dim_token 99)
\t)
"""
        )
        with pytest.raises(KicadParseError, match="Unknown token in dimension"):
            Footprint.from_string(bad)
