"""Content comparison invariants and conservative matching boundaries."""

import json
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

import pytest

from eedesigncore import (
    Footprint,
    Pad,
    PcbGroup,
    PcbPoint,
    Pin,
    PinElectricalType,
    PinShape,
    Property,
    SchPolyline,
    Stackup,
    StackupLayer,
    Symbol,
    SymbolLib,
    SymbolUnit,
    Zone,
    compare_content,
    content_signature,
)


def _symbol() -> Symbol:
    return Symbol(
        name="Example",
        units=[
            SymbolUnit.for_symbol(
                "Example",
                items=[
                    Pin(
                        number="1",
                        name="A",
                        electrical_type=PinElectricalType.input,
                        shape=PinShape.line,
                    ),
                    Pin(
                        number="2",
                        name="B",
                        electrical_type=PinElectricalType.output,
                        shape=PinShape.line,
                    ),
                ],
            )
        ],
    )


def _assert_changed(
    before: Footprint | Symbol | SymbolLib, after: Footprint | Symbol | SymbolLib
) -> None:
    result = compare_content(before, after)
    assert not result.equal
    assert result.changes
    assert result.before_signature != result.after_signature


def test_pad_order_and_duplicate_numbers_do_not_hide_content() -> None:
    first = Footprint(
        pads=[
            Pad(layers=["F.Cu"], number="1", position=(1, 2)),
            Pad(layers=["F.Cu"], number="1", position=(3, 4)),
        ]
    )
    second = first.model_copy(deep=True)
    second.pads.reverse()
    original = first.model_dump()
    assert compare_content(first, second).equal
    assert content_signature(first) == content_signature(second)
    assert first.model_dump() == original
    second.pads.pop()
    _assert_changed(first, second)


def test_identical_duplicate_removal_is_reported_once() -> None:
    first = Footprint(pads=[Pad(layers=["F.Cu"], number="1"), Pad(layers=["F.Cu"], number="1")])
    second = Footprint(pads=[Pad(layers=["F.Cu"], number="1")])
    result = compare_content(first, second)
    assert [change.kind for change in result.changes] == ["removed"]
    assert json.loads(result.changes[0].before)["number"] == "1"


def test_unique_pad_identifier_reports_position_and_net_changes() -> None:
    first = Footprint(pads=[Pad(layers=["F.Cu"], id="pad-identity", number="1", net_name="GND")])
    second = first.model_copy(deep=True)
    second.pads[0].position = (1.5, 2.5)
    second.pads[0].net_name = "VCC"
    result = compare_content(first, second)
    assert {change.path[-1] for change in result.changes} == {"position", "net_name"}
    assert all(change.kind == "changed" for change in result.changes)
    position = next(change for change in result.changes if change.path[-1] == "position")
    assert json.loads(position.before) == [0, 0]
    assert json.loads(position.after) == [1.5, 2.5]


@pytest.mark.parametrize("identifier", [None, "duplicate"])
def test_ambiguous_pad_changes_are_additions_and_removals(identifier: str | None) -> None:
    first = Footprint(
        pads=[
            Pad(layers=["F.Cu"], id=identifier),
            Pad(layers=["F.Cu"], id=identifier, position=(1, 0)),
        ]
    )
    second = first.model_copy(deep=True)
    second.pads[1].position = (2, 0)
    result = compare_content(first, second)
    assert [change.kind for change in result.changes] == ["removed", "added"]


def test_symbol_pin_order_and_electrical_type() -> None:
    first = _symbol()
    second = first.model_copy(deep=True)
    second.units[0].items.reverse()
    assert compare_content(first, second).equal
    second.units[0].items[0].electrical_type = PinElectricalType.passive
    _assert_changed(first, second)


def test_symbol_and_unit_order() -> None:
    symbol = _symbol()
    symbol.units.append(SymbolUnit.for_symbol("Example", unit_index=2))
    first = SymbolLib(symbols=[symbol, Symbol(name="Other")])
    second = first.model_copy(deep=True)
    second.symbols[0].units.reverse()
    second.symbols.reverse()
    assert compare_content(first, second).equal


@pytest.mark.parametrize("names", [("X", "X"), ("X", "Lib:X"), ("A/B", "A{slash}B")])
def test_colliding_symbol_names_keep_order(names: tuple[str, str]) -> None:
    first = SymbolLib(symbols=[Symbol(name=names[0]), Symbol(name=names[1], in_bom=False)])
    second = first.model_copy(deep=True)
    second.symbols.reverse()
    _assert_changed(first, second)


@pytest.mark.parametrize("problem", ["duplicate", "display", "encoded"])
def test_ambiguous_units_keep_order(problem: str) -> None:
    first = _symbol()
    first.units.append(SymbolUnit.for_symbol("Example", body_style_index=2))
    if problem == "duplicate":
        first.units[1].body_style_index = 1
        first.units[1].name = first.units[0].name
    elif problem == "display":
        first.units[0].unit_display_name = "First"
        first.units[1].unit_display_name = "Second"
    else:
        first.units[1].name = "Wrong_1_1"
    second = first.model_copy(deep=True)
    second.units.reverse()
    _assert_changed(first, second)


def test_property_and_body_style_order_are_significant() -> None:
    first = Footprint(properties=[Property(name="A", value="1"), Property(name="B", value="2")])
    second = first.model_copy(deep=True)
    second.properties.reverse()
    _assert_changed(first, second)
    symbol = Symbol(name="X", body_styles=["Normal", "Alternate"])
    other = symbol.model_copy(deep=True)
    other.body_styles.reverse()
    _assert_changed(symbol, other)


def test_polygon_points_remain_ordered_inside_independent_items() -> None:
    first = Symbol(
        name="X",
        units=[
            SymbolUnit.for_symbol(
                "X",
                items=[
                    SchPolyline(id="shape", pts=[(0, 0), (1, 0), (1, 1)]),
                ],
            )
        ],
    )
    second = first.model_copy(deep=True)
    second.units[0].items[0].pts.reverse()
    _assert_changed(first, second)


def test_tiny_geometry_changes_are_not_rounded_away() -> None:
    first = Footprint(pads=[Pad(layers=["F.Cu"], id="one")])
    second = first.model_copy(deep=True)
    second.pads[0].position = (0.0000000001, 0)
    _assert_changed(first, second)


def test_header_identity_and_source_ending_contract() -> None:
    first = Footprint.from_string('(footprint "X" (version 20260206) (generator "test"))')
    second = Footprint.from_string('(footprint  "X"\r\n(version 20260206)\r\n(generator "test"))')
    assert compare_content(first, second).equal
    second.generator = "another"
    _assert_changed(first, second)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numbers_fail(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        content_signature(Footprint(pads=[Pad(layers=["F.Cu"], angle=value)]))


@pytest.mark.parametrize("value", [object(), {"unknown": "value"}, "unknown", 4])
def test_untyped_symbol_items_fail(value: object) -> None:
    symbol = Symbol(name="X", units=[SymbolUnit.for_symbol("X", items=[value])])
    with pytest.raises(TypeError):
        content_signature(symbol)


def test_input_types_cannot_collide() -> None:
    _assert_changed(Symbol(name="X"), Footprint(fp_id="X"))
    with pytest.raises(TypeError, match="Footprint"):
        content_signature("file.kicad_mod")  # type: ignore[arg-type]


def test_signature_is_stable_across_python_hash_seeds() -> None:
    code = (
        "from eedesigncore import Footprint,Pad,content_signature; "
        "print(content_signature(Footprint(pads=[Pad(layers=['F.Cu'],number='1')])))"
    )
    expected = content_signature(Footprint(pads=[Pad(layers=["F.Cu"], number="1")]))
    for seed in ("1", "98765"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert result.stdout.strip() == expected


@pytest.mark.parametrize("path", sorted(Path("tests/fixtures/mod").glob("*.kicad_mod")))
def test_real_footprints_preserve_content_under_independent_permutations(path: Path) -> None:
    first = Footprint.from_file(path)
    second = first.model_copy(deep=True)
    for field in ("pads", "drawings", "points", "zones", "groups"):
        getattr(second, field).reverse()
    assert compare_content(first, second).equal


def test_real_symbols_preserve_content_under_item_permutations() -> None:
    first = SymbolLib.from_file("tests/fixtures/sym/474811-E.kicad_sym")
    second = first.model_copy(deep=True)
    for symbol in second.symbols:
        for unit in symbol.units:
            unit.items.reverse()
    assert compare_content(first, second).equal


def test_point_zone_and_group_permutations_preserve_duplicates() -> None:
    first = Footprint(
        points=[PcbPoint(size=1), PcbPoint(size=2)],
        zones=[Zone(priority=1), Zone(priority=2)],
        groups=[PcbGroup(name="first"), PcbGroup(name="second")],
    )
    second = first.model_copy(deep=True)
    for field in ("points", "zones", "groups"):
        getattr(second, field).reverse()
    assert compare_content(first, second).equal
    second.zones[0].priority = 3
    _assert_changed(first, second)


def test_stackup_order_is_significant() -> None:
    first = Footprint(
        stackup=Stackup(layers=[StackupLayer(name="F.Cu"), StackupLayer(name="B.Cu")])
    )
    second = first.model_copy(deep=True)
    second.stackup.layers.reverse()
    _assert_changed(first, second)


def test_size_budget_rejects_shared_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    import eedesigncore.comparison as comparison

    original = comparison._Budget
    monkeypatch.setattr(comparison, "_Budget", lambda: original(values=100, encoding=100_000))
    value: object = [1]
    for _ in range(10):
        value = [value, value]
    footprint = Footprint()
    footprint.pads = value
    with pytest.raises(ValueError, match="size limit"):
        content_signature(footprint)


def test_oversized_text_is_rejected_before_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    import eedesigncore.comparison as comparison

    original = comparison._Budget
    monkeypatch.setattr(comparison, "_Budget", lambda: original(encoding=10_000))
    with pytest.raises(ValueError, match="size limit"):
        content_signature(Footprint(description="x" * 10_000))


def test_cycles_and_custom_enums_are_rejected() -> None:
    footprint = Footprint()
    footprint.pads.append(footprint)
    with pytest.raises(ValueError, match="cycle"):
        content_signature(footprint)

    class Unexpected(Enum):
        item = []

    Unexpected.item.value.append(Unexpected.item)
    footprint = Footprint()
    footprint.description = Unexpected.item
    with pytest.raises(TypeError, match="enumeration"):
        content_signature(footprint)
