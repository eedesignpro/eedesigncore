"""T8 — generators (§5 / A3 report) + targets (§6).

Exercises the exhaustive 5-branch property-bag dispatch (number/bool/point/pts/
string), sorted-by-key emission, sorted-uuid members, the ghost-generator guard,
and the strict-value-form / tolerant-key parser contract (A3 §6). Byte-identity
uses the canonical (prettified) form as the fidelity reference (ADR-004).
"""

from __future__ import annotations

import pytest

from eedesigncore.exceptions import KicadParseError
from eedesigncore.pcb.models import (
    Board,
    GenBool,
    GenNumber,
    GenPoint,
    GenPts,
    GenString,
)

_HEADER = '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")'
_TAIL = " (embedded_fonts no))"


def _canonical(body: str) -> str:
    return Board.from_string(_HEADER + body + _TAIL).to_string()


class TestGeneratorRoundTrip:
    def test_all_five_value_types_round_trip(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "tp") (layer "F.Cu")'
            " (locked yes)"
            " (base_line (pts (xy 0 0) (xy 5 0) (xy 5 5)))"  # branch 4: pts
            " (corner_radius_percent 30)"  # branch 1: number (int-valued)
            " (end (xy 10 -5))"  # branch 3: point
            ' (initial_side "left")'  # branch 5: string
            " (is_time_domain no)"  # branch 2: bool
            " (max_amplitude 1.27)"  # branch 1: number (float)
            ' (members "zzz" "aaa"))'
        )
        canonical = _canonical(body)
        assert Board.from_string(canonical).to_string() == canonical
        board = Board.from_string(canonical)
        gen = board.generators[0]
        assert isinstance(gen.properties["base_line"], GenPts)
        assert isinstance(gen.properties["corner_radius_percent"], GenNumber)
        assert isinstance(gen.properties["end"], GenPoint)
        assert isinstance(gen.properties["initial_side"], GenString)
        assert isinstance(gen.properties["is_time_domain"], GenBool)
        assert isinstance(gen.properties["max_amplitude"], GenNumber)

    def test_properties_emitted_sorted_by_key(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (zeta 1) (alpha 2) (mu 3) (members "m1"))'
        )
        canonical = _canonical(body)
        assert canonical.index("(alpha") < canonical.index("(mu") < canonical.index("(zeta")

    def test_members_emitted_sorted(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (members "ccc" "aaa" "bbb"))'
        )
        canonical = _canonical(body)
        assert '(members "aaa" "bbb" "ccc")' in canonical

    def test_number_uses_10g_shortest_form(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (corner_radius_percent 30) (members "m1"))'
        )
        canonical = _canonical(body)
        # {:.10g} strips the decimal point for integer-valued numbers.
        assert "(corner_radius_percent 30)" in canonical

    def test_type_is_bare_symbol(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (members "m1"))'
        )
        canonical = _canonical(body)
        assert "(type tuning_pattern)" in canonical  # unquoted


class TestGhostGuard:
    def test_tuning_pattern_without_members_emits_nothing(self) -> None:
        body = ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu"))'
        canonical = _canonical(body)
        assert "(generated" not in canonical

    def test_ghost_generator_still_parses(self) -> None:
        body = ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu"))'
        board = Board.from_string(_HEADER + body + _TAIL)
        assert len(board.generators) == 1
        assert board.generators[0].members == []


class TestParserContract:
    def test_unknown_key_tolerated(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (some_future_key 42) (members "m1"))'
        )
        board = Board.from_string(_HEADER + body + _TAIL)
        val = board.generators[0].properties["some_future_key"]
        assert isinstance(val, GenNumber)
        assert val.value == 42

    def test_unknown_value_form_bare_symbol_hard_errors(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (bad_key some_symbol) (members "m1"))'
        )
        with pytest.raises(KicadParseError, match="unexpected symbol value"):
            Board.from_string(_HEADER + body + _TAIL)

    def test_unknown_inner_list_form_hard_errors(self) -> None:
        body = (
            ' (generated (uuid "g1") (type tuning_pattern) (name "t") (layer "F.Cu")'
            ' (bad_key (bogus 1 2)) (members "m1"))'
        )
        with pytest.raises(KicadParseError, match="expected .xy"):
            Board.from_string(_HEADER + body + _TAIL)


class TestTarget:
    def test_target_x_with_width(self) -> None:
        body = ' (target x (at 10 10) (size 5) (width 0.15) (layer "Edge.Cuts") (uuid "t1"))'
        canonical = _canonical(body)
        assert Board.from_string(canonical).to_string() == canonical
        board = Board.from_string(canonical)
        assert board.targets[0].is_x is True
        assert board.targets[0].width == pytest.approx(0.15)

    def test_target_plus_omits_zero_width(self) -> None:
        body = ' (target plus (at 0 0) (size 5) (layer "Edge.Cuts") (uuid "t2"))'
        canonical = _canonical(body)
        assert "(width" not in canonical
        assert "(target plus" in canonical
        board = Board.from_string(canonical)
        assert board.targets[0].is_x is False

    def test_invalid_shape_hard_errors(self) -> None:
        body = ' (target star (at 0 0) (size 5) (layer "Edge.Cuts"))'
        with pytest.raises(KicadParseError, match="expected shape"):
            Board.from_string(_HEADER + body + _TAIL)
