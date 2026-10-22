"""Unit tests for reference auto-annotation in inject_symbol (R2, ADR-046).

The prefix rule ports ``UTIL::GetRefDesPrefix`` and the number rule ports
``UTIL::GetRefDesNumber`` (``common/refdes_utils.cpp:35-70``). The lowest-free
selection is library policy, not a copy of the KiCad annotator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import InstancePath, InstanceProject, Schematic
from eedesigncore.sch.operations import (
    _next_free_reference,
    _refdes_number,
    _refdes_prefix,
    inject_symbol,
)

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "sch" / "placed_symbols.kicad_sch"


def _symbol(name: str = "R", reference: str = "R") -> Symbol:
    """A minimal library symbol carrying a Reference field and one pin."""
    return Symbol(
        name=name,
        fields=[
            Property(name="Reference", value=reference),
            Property(name="Value", value=name),
        ],
        units=[
            SymbolUnit(
                name=f"{name}_1_1",
                items=[
                    Pin(
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                        number="1",
                    )
                ],
            )
        ],
    )


def _place(sch: Schematic, reference: str) -> None:
    """Add a placed symbol carrying only an instance reference, no field copy."""
    inject_symbol(sch, _symbol(), at=(0.0, 0.0), lib_nickname="Device", reference=reference)


class TestRefDesPrefix:
    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("R12", "R"),
            ("R?", "R"),
            ("R", "R"),
            ("C0402", "C"),
            ("IC1A", "IC1A"),
            ("12", ""),
            ("", ""),
            ("#PWR01", "#PWR"),
            ("U1?", "U"),
        ],
    )
    def test_prefix_matches_the_kicad_rule(self, reference: str, expected: str) -> None:
        assert _refdes_prefix(reference) == expected


class TestRefDesNumber:
    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("R12", 12),
            ("R1", 1),
            ("R", -1),
            ("R?", -1),
            ("R1A", -1),
            ("C0402", 402),
            ("", -1),
        ],
    )
    def test_number_matches_the_kicad_rule(self, reference: str, expected: int) -> None:
        assert _refdes_number(reference) == expected

    def test_underscore_digits_do_not_parse(self) -> None:
        """wxString::ToLong rejects "1_0"; Python int() would accept it."""
        assert _refdes_number("R1_0") == -1


class TestNextFreeReference:
    def test_empty_schematic_starts_at_one(self) -> None:
        assert _next_free_reference(Schematic(), "R") == "R1"

    def test_fills_the_lowest_gap(self) -> None:
        sch = Schematic()
        for reference in ("R1", "R2", "R4"):
            _place(sch, reference)
        assert _next_free_reference(sch, "R") == "R3"

    def test_ignores_other_prefixes(self) -> None:
        sch = Schematic()
        for reference in ("C1", "C2", "U1"):
            _place(sch, reference)
        assert _next_free_reference(sch, "R") == "R1"

    def test_ignores_unannotated_references(self) -> None:
        sch = Schematic()
        _place(sch, "R?")
        assert _next_free_reference(sch, "R") == "R1"

    def test_reads_an_instance_only_reference(self) -> None:
        """A reference held only in the instances node still counts as used."""
        sch = Schematic()
        _place(sch, "R1")
        sch.symbols[0].fields = []
        assert _next_free_reference(sch, "R") == "R2"

    def test_reads_a_field_only_reference(self) -> None:
        sch = Schematic()
        _place(sch, "R1")
        sch.symbols[0].instances = [InstanceProject(name="p", paths=[])]
        assert _next_free_reference(sch, "R") == "R2"


class TestInjectAutoAnnotation:
    def test_empty_schematic_gets_number_one(self) -> None:
        sch = Schematic()
        placed = inject_symbol(sch, _symbol(), at=(0.0, 0.0), lib_nickname="Device")
        assert placed.instances[0].paths[0].reference == "R1"

    def test_gapped_sequence_fills_the_gap(self) -> None:
        sch = Schematic()
        for reference in ("R1", "R2", "R4"):
            _place(sch, reference)
        placed = inject_symbol(sch, _symbol(), at=(0.0, 0.0), lib_nickname="Device")
        assert placed.instances[0].paths[0].reference == "R3"

    def test_prefix_with_no_existing_member_gets_number_one(self) -> None:
        sch = Schematic()
        for reference in ("R1", "R2", "R3"):
            _place(sch, reference)
        placed = inject_symbol(
            sch, _symbol(name="C", reference="C"), at=(0.0, 0.0), lib_nickname="Device"
        )
        assert placed.instances[0].paths[0].reference == "C1"

    def test_three_bare_injects_do_not_collide(self) -> None:
        sch = Schematic()
        placed = [
            inject_symbol(sch, _symbol(), at=(float(i), 0.0), lib_nickname="Device")
            for i in range(3)
        ]
        references = [p.instances[0].paths[0].reference for p in placed]
        assert references == ["R1", "R2", "R3"]
        assert len(set(references)) == 3

    def test_field_and_instance_carry_the_same_reference(self) -> None:
        sch = Schematic()
        placed = inject_symbol(sch, _symbol(), at=(0.0, 0.0), lib_nickname="Device")
        field = next(f.value for f in placed.fields if f.name == "Reference")
        assert field == placed.instances[0].paths[0].reference == "R1"

    def test_unannotated_lib_prefix_is_stripped(self) -> None:
        sch = Schematic()
        placed = inject_symbol(sch, _symbol(reference="R?"), at=(0.0, 0.0), lib_nickname="Device")
        assert placed.instances[0].paths[0].reference == "R1"

    def test_missing_reference_field_falls_back_to_u(self) -> None:
        symbol = _symbol()
        symbol.fields = [Property(name="Value", value="R")]
        placed = inject_symbol(Schematic(), symbol, at=(0.0, 0.0), lib_nickname="Device")
        assert placed.instances[0].paths[0].reference == "U1"

    def test_explicit_reference_still_wins(self) -> None:
        sch = Schematic()
        placed = inject_symbol(
            sch, _symbol(), at=(0.0, 0.0), lib_nickname="Device", reference="R99"
        )
        assert placed.instances[0].paths[0].reference == "R99"

    def test_auto_annotation_against_a_corpus_schematic(self) -> None:
        sch = Schematic.from_file(FIXTURE)
        used = {
            path.reference
            for placed in sch.symbols
            for project in placed.instances
            for path in project.paths
        }
        placed = [
            inject_symbol(sch, _symbol(), at=(float(i), 0.0), lib_nickname="Device")
            for i in range(3)
        ]
        new = [p.instances[0].paths[0].reference for p in placed]
        assert len(set(new)) == 3
        assert not (set(new) & used)

    def test_injected_schematic_still_round_trips(self) -> None:
        sch = Schematic.from_file(FIXTURE)
        inject_symbol(sch, _symbol(), at=(10.0, 10.0), lib_nickname="Device")
        reloaded = Schematic.from_string(sch.to_string())
        references = [
            path.reference
            for placed in reloaded.symbols
            for project in placed.instances
            for path in project.paths
        ]
        assert len(references) == len(set(references))


class TestInstancePathModel:
    def test_place_helper_builds_a_readable_instance(self) -> None:
        sch = Schematic()
        _place(sch, "R7")
        path = sch.symbols[0].instances[0].paths[0]
        assert isinstance(path, InstancePath)
        assert path.reference == "R7"
