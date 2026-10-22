"""Injected content lands in canonical order, not appended (R8, T7).

``inject_symbol`` and ``inject_footprint`` both APPEND to a model list. That is
correct, because the writers canonicalise on the way out (T1, T3, T4). These
tests prove the end-to-end result rather than the model state, so a regression in
either the operation or the writer fails here.

Neither schematic nor board has an inert resave oracle, so every assertion below
is the source-verified order (plan R7).
"""

from __future__ import annotations

import re
from pathlib import Path

from eedesigncore.common.models import Property
from eedesigncore.common.pcb_family.ordering import layer_set_seq
from eedesigncore.common.sch_family.enums import PinElectricalType, PinShape
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.mod.models import Footprint
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.operations import inject_footprint
from eedesigncore.pcb.writer import write_board
from eedesigncore.sch.models import Schematic
from eedesigncore.sch.operations import inject_symbol
from eedesigncore.sch.writer import write_sch

FIXTURES = Path(__file__).parent.parent / "fixtures"
MOD_FIXTURE = FIXTURES / "pcb" / "footprint_board_context_C_0402.kicad_mod"


# ---------------------------------------------------------------------------
# inject_symbol
# ---------------------------------------------------------------------------


def _symbol(name: str, pin_numbers: tuple[str, ...]) -> Symbol:
    return Symbol(
        name=name,
        fields=[Property(name="Reference", value="R"), Property(name="Value", value=name)],
        units=[
            SymbolUnit(
                name=f"{name}_1_1",
                items=[
                    Pin(
                        electrical_type=PinElectricalType.passive,
                        shape=PinShape.line,
                        position=(0.0, -2.54 * index),
                        number=number,
                    )
                    for index, number in enumerate(pin_numbers)
                ],
            )
        ],
    )


def _emitted_lib_ids(sch: Schematic) -> list[str]:
    return [
        line.strip().removeprefix("(symbol ").strip('"')
        for line in write_sch(sch).splitlines()
        if line.startswith("\t\t(symbol ")
    ]


def _emitted_pin_numbers(sch: Schematic) -> list[str]:
    return [
        line.split('"')[1]
        for line in write_sch(sch).splitlines()
        if line.lstrip().startswith("(number ")
    ]


def test_injected_symbol_lands_in_its_lib_id_slot() -> None:
    """The lib id sorts below both existing entries, so it emits FIRST."""
    sch = Schematic()
    inject_symbol(sch, _symbol("R", ("1",)), at=(0.0, 0.0), lib_nickname="Zulu")
    inject_symbol(sch, _symbol("C", ("1",)), at=(0.0, 0.0), lib_nickname="Mike")
    inject_symbol(sch, _symbol("L", ("1",)), at=(0.0, 0.0), lib_nickname="Alpha")

    assert [item.name for item in sch.lib_symbols] == ["Zulu:R", "Mike:C", "Alpha:L"]
    assert _emitted_lib_ids(sch) == ["Alpha:L", "Mike:C", "Zulu:R"]


def test_injected_symbol_pins_emit_in_canonical_order() -> None:
    """A definition stored with scrambled pins registers, then emits sorted."""
    sch = Schematic()
    inject_symbol(sch, _symbol("R", ("10", "9", "1")), at=(0.0, 0.0), lib_nickname="Lib")

    assert [item.number for item in sch.lib_symbols[0].units[0].items] == ["10", "9", "1"]
    assert _emitted_pin_numbers(sch) == ["1", "9", "10"]


def test_injected_placed_symbols_emit_in_uuid_order() -> None:
    """The root item stream is keyed on ``(Type(), m_Uuid)`` (``:436-443``).

    ``inject_symbol`` mints a fresh uuid, so the expected order comes from the
    minted values rather than from a literal.
    """
    sch = Schematic()
    for index in range(4):
        inject_symbol(sch, _symbol(f"R{index}", ("1",)), at=(0.0, 0.0), lib_nickname="Lib")

    emitted = re.findall(r'\(uuid "([^"]+)"\)', write_sch(sch))
    placed = sorted(symbol.uuid or "" for symbol in sch.symbols)
    assert [value for value in emitted if value in placed] == placed


def test_injected_symbol_fields_emit_in_ordinal_order() -> None:
    """A definition whose fields lead with a user field still emits mandatory first."""
    definition = _symbol("R", ("1",))
    definition.fields.insert(0, Property(name="MPN", value="ACME-1"))
    sch = Schematic()
    placed = inject_symbol(sch, definition, at=(0.0, 0.0), lib_nickname="Lib")

    assert placed.fields[0].name == "MPN"
    emitted = re.findall(r'\(property "([^"]+)"', write_sch(sch))
    assert emitted[:2] == ["Reference", "Value"]
    assert emitted.index("MPN") > emitted.index("Value")


# ---------------------------------------------------------------------------
# inject_footprint
# ---------------------------------------------------------------------------


def _board_footprint() -> Footprint:
    return Footprint.from_file(MOD_FIXTURE)


def _emitted_footprint_uuids(board: Board) -> list[str]:
    text = write_board(board)
    return re.findall(r'^\t\t\(uuid "([^"]+)"\)$', text, flags=re.MULTILINE)


def test_injected_footprint_lands_in_uuid_order_not_appended() -> None:
    """Board footprints sort by type, layer set, then uuid (``board_item.cpp:314-326``).

    Every footprint here sits on the same layer, so the uuid key decides. The
    injected uuid is minted, so the expectation is derived, not a literal.
    """
    board = Board()
    for _ in range(4):
        inject_footprint(board, _board_footprint(), at=(10.0, 10.0))

    stored = [footprint.uuid or "" for footprint in board.footprints]
    assert {layer_set_seq(footprint.layer) for footprint in board.footprints} == {
        layer_set_seq(board.footprints[0].layer)
    }
    assert _emitted_footprint_uuids(board) == sorted(stored)


def test_a_footprint_whose_uuid_sorts_first_emits_first() -> None:
    """The deterministic twin of the test above, with a controlled uuid."""
    board = Board()
    for _ in range(3):
        inject_footprint(board, _board_footprint(), at=(10.0, 10.0))
    injected = inject_footprint(board, _board_footprint(), at=(20.0, 20.0))
    injected.uuid = "00000000-0000-0000-0000-000000000000"

    assert board.footprints[-1] is injected
    assert _emitted_footprint_uuids(board)[0] == injected.uuid


def test_injected_footprint_pads_emit_in_canonical_order() -> None:
    """A library footprint stored with scrambled pads emits sorted."""
    footprint = _board_footprint()
    footprint.pads.reverse()
    board = Board()
    injected = inject_footprint(board, footprint, at=(0.0, 0.0))

    stored = [pad.number for pad in injected.pads]
    emitted = re.findall(r'\(pad "([^"]*)"', write_board(board))
    assert stored == list(reversed(emitted))
    assert emitted == sorted(emitted)


def test_injected_footprint_properties_emit_mandatory_first() -> None:
    """The four mandatory footprint fields lead (``footprint.cpp:105-117``)."""
    footprint = _board_footprint()
    footprint.properties.insert(0, Property(name="MPN", value="ACME-1", layer="F.Fab"))
    board = Board()
    inject_footprint(board, footprint, at=(0.0, 0.0))

    emitted = re.findall(r'\(property "([^"]+)"', write_board(board))
    assert emitted[:5] == ["Reference", "Value", "Datasheet", "Description", "MPN"]
