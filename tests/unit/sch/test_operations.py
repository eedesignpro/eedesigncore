"""Unit tests for extract_symbol / inject_symbol (T8, spec §2.3-2.4 / §5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.ordering import sort_symbol_fields
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.operations import extract_symbol, inject_symbol

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "sch" / "placed_symbols.kicad_sch"
FIXTURE_LIB_ID = "provisional:132-00514_117156"


def _corpus_schematic() -> Schematic:
    return Schematic.from_file(FIXTURE)


def _simple_symbol(name: str = "R", value: str = "R") -> Symbol:
    """A minimal library symbol with a Reference field and two pins."""
    return Symbol(
        name=name,
        fields=[
            Property(name="Reference", value=value),
            Property(name="Value", value=value),
        ],
        units=[
            SymbolUnit(
                name=f"{name}_1_1",
                items=[
                    Pin(
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                        number="1",
                    ),
                    Pin(
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                        number="2",
                    ),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# extract_symbol
# ---------------------------------------------------------------------------


def test_extract_de_qualifies_name() -> None:
    sch = _corpus_schematic()
    sym = extract_symbol(sch, lib_id=FIXTURE_LIB_ID)
    # step 1: (symbol "Lib:Name") -> "Name"
    assert sym.name == "132-00514_117156"


def test_extract_unknown_lib_id_raises() -> None:
    sch = _corpus_schematic()
    with pytest.raises(KeyError):
        extract_symbol(sch, lib_id="nope:missing")


def test_extract_does_not_mutate_source() -> None:
    sch = _corpus_schematic()
    extract_symbol(sch, lib_id=FIXTURE_LIB_ID)
    assert sch.lib_symbols[0].name == FIXTURE_LIB_ID


def test_extract_preserves_units_and_fields() -> None:
    sch = _corpus_schematic()
    src = sch.lib_symbols[0]
    sym = extract_symbol(sch, lib_id=FIXTURE_LIB_ID)
    # steps 2/3: per-unit child names + fields carried verbatim
    assert [u.name for u in sym.units] == [u.name for u in src.units]
    assert [f.name for f in sym.fields] == [f.name for f in src.fields]


def test_extract_bare_name_entry_unchanged() -> None:
    sch = Schematic(lib_symbols=[_simple_symbol(name="R")])
    sym = extract_symbol(sch, lib_id="R")
    assert sym.name == "R"


# ---------------------------------------------------------------------------
# inject_symbol — registration + synthesis
# ---------------------------------------------------------------------------


def test_inject_registers_def_and_appends_placed() -> None:
    sch = Schematic()
    lib_sym = _simple_symbol()
    placed = inject_symbol(sch, lib_sym, at=(10.0, 20.0), lib_nickname="Device")
    # step 5: def registered under full lib_id
    assert [s.name for s in sch.lib_symbols] == ["Device:R"]
    # step 3: placed instance appended + returned
    assert placed in sch.symbols
    assert placed.lib_id == "Device:R"
    assert placed.position == (10.0, 20.0)


def test_inject_lib_nickname_qualifies_bare_name() -> None:
    sch = Schematic()
    placed = inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    assert placed.lib_id == "Device:R"
    assert sch.lib_symbols[0].name == "Device:R"


def test_inject_keeps_prequalified_name() -> None:
    sch = Schematic()
    placed = inject_symbol(sch, _simple_symbol(name="Lib:R"), at=(0.0, 0.0), lib_nickname="Other")
    # already-qualified name is left as-is (":" present)
    assert placed.lib_id == "Lib:R"


def test_inject_bare_name_no_nickname_uses_placeholder() -> None:
    sch = Schematic()
    # bare name + no lib_nickname -> placeholder "eedesigncore" (ADR-041 amd 2)
    placed = inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0))
    assert placed.lib_id == "eedesigncore:R"
    assert sch.lib_symbols[0].name == "eedesigncore:R"


def test_inject_prequalified_name_no_nickname_unchanged() -> None:
    sch = Schematic()
    # pre-qualified name is left as-is even with no lib_nickname
    placed = inject_symbol(sch, _simple_symbol(name="Lib:R"), at=(0.0, 0.0))
    assert placed.lib_id == "Lib:R"


def test_inject_collision_rename_with_placeholder_default() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(value="R"), at=(0.0, 0.0))
    differing = _simple_symbol(value="R")
    differing.fields.append(Property(name="Extra", value="x"))
    placed2 = inject_symbol(sch, differing, at=(5.0, 5.0))
    # first def registered under "eedesigncore:R"; the differing def collides and
    # renames to the bare "<ItemName>_1" (§5.2, empty nickname), NOT "Lib:R_1"
    assert sch.lib_symbols[0].name == "eedesigncore:R"
    assert placed2.lib_id == "R_1"


def test_inject_synthesizes_pins_with_uuids() -> None:
    sch = Schematic()
    placed = inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    assert [p.number for p in placed.pins] == ["1", "2"]
    assert all(p.uuid for p in placed.pins)
    # instance uuid minted (schematic-side KIID)
    assert placed.uuid is not None


def test_inject_reference_override() -> None:
    sch = Schematic()
    placed = inject_symbol(
        sch, _simple_symbol(), at=(0.0, 0.0), reference="R42", lib_nickname="Device"
    )
    ref_field = next(f.value for f in placed.fields if f.name == "Reference")
    assert ref_field == "R42"
    assert placed.instances[0].paths[0].reference == "R42"


def test_inject_auto_reference_from_lib_field_prefix() -> None:
    """R2: an omitted reference auto-annotates <Prefix><n> (was the bare prefix)."""
    sch = Schematic()
    placed = inject_symbol(sch, _simple_symbol(value="R"), at=(0.0, 0.0), lib_nickname="Device")
    assert placed.instances[0].paths[0].reference == "R1"
    assert next(f.value for f in placed.fields if f.name == "Reference") == "R1"


def test_inject_instance_defaults() -> None:
    sch = Schematic()
    placed = inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device", unit=2)
    inst = placed.instances[0]
    assert inst.paths[0].path == "/"
    assert inst.paths[0].unit == 2
    assert placed.unit == 2


def test_inject_does_not_mutate_incoming_symbol() -> None:
    sch = Schematic()
    lib_sym = _simple_symbol()
    inject_symbol(sch, lib_sym, at=(0.0, 0.0), lib_nickname="Device")
    # incoming name stays bare; registered copy is qualified
    assert lib_sym.name == "R"


# ---------------------------------------------------------------------------
# inject_symbol — collision-rename (§5.2)
# ---------------------------------------------------------------------------


def test_inject_no_collision_distinct_keys() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(name="R"), at=(0.0, 0.0), lib_nickname="Device")
    inject_symbol(sch, _simple_symbol(name="C"), at=(0.0, 0.0), lib_nickname="Device")
    assert sorted(s.name for s in sch.lib_symbols) == ["Device:C", "Device:R"]


def test_inject_identical_def_reused() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    placed2 = inject_symbol(sch, _simple_symbol(), at=(5.0, 5.0), lib_nickname="Device")
    # identical def under the same key: reuse, no new lib entry
    assert len(sch.lib_symbols) == 1
    assert placed2.lib_id == "Device:R"
    # both placed instances present
    assert len(sch.symbols) == 2


def test_inject_differing_def_renamed() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(value="R"), at=(0.0, 0.0), lib_nickname="Device")
    differing = _simple_symbol(value="R")
    differing.in_bom = False  # body differs from the cached def
    placed2 = inject_symbol(sch, differing, at=(5.0, 5.0), lib_nickname="Device")
    # collision-rename to bare "<ItemName>_1" (empty nickname), §5.2
    assert placed2.lib_id == "R_1"
    assert {s.name for s in sch.lib_symbols} == {"Device:R", "R_1"}


def test_inject_differing_def_twice_reuses_renamed_entry() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    diff = _simple_symbol()
    diff.in_bom = False
    inject_symbol(sch, diff.model_copy(deep=True), at=(1.0, 1.0), lib_nickname="Device")
    placed3 = inject_symbol(sch, diff.model_copy(deep=True), at=(2.0, 2.0), lib_nickname="Device")
    # second differing inject matches the already-renamed R_1: reuse, no R_2
    assert placed3.lib_id == "R_1"
    assert {s.name for s in sch.lib_symbols} == {"Device:R", "R_1"}


def test_inject_two_distinct_differing_defs_increment_suffix() -> None:
    sch = Schematic()
    inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    d1 = _simple_symbol()
    d1.in_bom = False
    inject_symbol(sch, d1, at=(1.0, 1.0), lib_nickname="Device")
    d2 = _simple_symbol()
    d2.on_board = False  # a third, distinct body
    placed3 = inject_symbol(sch, d2, at=(2.0, 2.0), lib_nickname="Device")
    assert placed3.lib_id == "R_2"
    assert {s.name for s in sch.lib_symbols} == {"Device:R", "R_1", "R_2"}


# ---------------------------------------------------------------------------
# round-trip: inject into a real corpus schematic
# ---------------------------------------------------------------------------


def test_inject_into_corpus_reparses_and_extracts_back() -> None:
    sch = _corpus_schematic()
    # Extract a real corpus symbol, then inject it under a fresh library nickname
    # (avoids the identical-def reuse path) and place it.
    sym_a = extract_symbol(sch, lib_id=FIXTURE_LIB_ID)
    inject_symbol(sch, sym_a, at=(50.0, 50.0), lib_nickname="mylib", reference="X1")

    # modified schematic must still write valid output that re-parses
    text = sch.to_string()
    reloaded = Schematic.from_string(text)
    assert any(s.name == "mylib:132-00514_117156" for s in reloaded.lib_symbols)

    # The injected def extracts back equivalently (same loader/writer pipeline).
    # Fields come back in canonical ordinal order, because the writer applies the
    # KiCad property order on the way out (ADR-055). The corpus symbol stores its
    # mandatory fields after its user fields, so the source model is canonicalised
    # here before the comparison.
    sym_a.fields = sort_symbol_fields(sym_a.fields)
    extracted = extract_symbol(reloaded, lib_id="mylib:132-00514_117156")
    assert extracted.name == "132-00514_117156"
    assert extracted == sym_a


def test_inject_preserves_untouched_corpus_round_trip() -> None:
    """Injecting appends without disturbing existing lib_symbols entries."""
    sch = _corpus_schematic()
    original_names = [s.name for s in sch.lib_symbols]
    inject_symbol(sch, _simple_symbol(), at=(0.0, 0.0), lib_nickname="Device")
    assert [s.name for s in sch.lib_symbols][: len(original_names)] == original_names
