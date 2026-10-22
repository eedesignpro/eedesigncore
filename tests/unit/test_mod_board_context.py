"""Board-context footprint uuid + (at) round-trip tests (GAP-033 / T10).

These tokens have ZERO corpus coverage (library `.kicad_mod` files omit them via
CTL_OMIT_UUIDS / CTL_OMIT_AT); synthetic strings are the regression net.
"""

from __future__ import annotations

from eedesigncore.mod.models import Footprint, FootprintPlacement


def _wrap(inner: str) -> str:
    return (
        '(footprint "TEST"\n'
        "\t(version 20260206)\n"
        '\t(generator "test")\n'
        '\t(layer "F.Cu")\n'
        f"{inner}"
        "\t(duplicate_pad_numbers_are_jumpers no)\n"
        "\t(embedded_fonts no)\n"
        ")\n"
    )


BOARD_WITH_ROT = _wrap('\t(uuid "1234abcd-0000-1111-2222-333344445555")\n\t(at 10 20 90)\n')
BOARD_NO_ROT = _wrap('\t(uuid "1234abcd-0000-1111-2222-333344445555")\n\t(at 1.5 -3.25)\n')
LIB_CONTEXT = _wrap("")


class TestBoardContextLoad:
    def test_uuid_stored(self) -> None:
        fp = Footprint.from_string(BOARD_WITH_ROT)
        assert fp.uuid == "1234abcd-0000-1111-2222-333344445555"

    def test_at_stored_with_rotation(self) -> None:
        fp = Footprint.from_string(BOARD_WITH_ROT)
        assert fp.at is not None
        assert fp.at.position == (10.0, 20.0)
        assert fp.at.angle == 90.0

    def test_at_stored_without_rotation(self) -> None:
        fp = Footprint.from_string(BOARD_NO_ROT)
        assert fp.at is not None
        assert fp.at.position == (1.5, -3.25)
        assert fp.at.angle == 0.0

    def test_lib_context_has_no_uuid_or_at(self) -> None:
        fp = Footprint.from_string(LIB_CONTEXT)
        assert fp.uuid is None
        assert fp.at is None


class TestBoardContextRoundTrip:
    def test_round_trip_with_rotation_byte_identical(self) -> None:
        fp = Footprint.from_string(BOARD_WITH_ROT)
        assert fp.to_string() == BOARD_WITH_ROT

    def test_round_trip_without_rotation_byte_identical(self) -> None:
        fp = Footprint.from_string(BOARD_NO_ROT)
        assert fp.to_string() == BOARD_NO_ROT

    def test_lib_context_unchanged_byte_identical(self) -> None:
        fp = Footprint.from_string(LIB_CONTEXT)
        assert fp.to_string() == LIB_CONTEXT


class TestBoardContextEmit:
    def test_zero_angle_suppressed(self) -> None:
        fp = Footprint(
            fp_id="TEST",
            generator="test",
            uuid="uu",
            at=FootprintPlacement(position=(1.0, 2.0), angle=0.0),
        )
        out = fp.to_string()
        assert "(at 1 2)" in out
        assert "(at 1 2 0)" not in out

    def test_nonzero_angle_emitted(self) -> None:
        fp = Footprint(
            fp_id="TEST",
            generator="test",
            at=FootprintPlacement(position=(1.0, 2.0), angle=45.0),
        )
        assert "(at 1 2 45)" in fp.to_string()

    def test_no_uuid_no_at_when_none(self) -> None:
        fp = Footprint(fp_id="TEST", generator="test")
        out = fp.to_string()
        assert "(uuid" not in out
        assert "(at " not in out
