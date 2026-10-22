"""Unit tests for the canonical schematic save order (R3, R5).

Every expected order below comes from a source-verified KiCad rule, cited in
``context/audit/save-canonicalization-sweep-2026-08-13.md`` sections 3.2 and 3.3.
The schematic has no resave oracle, so each test asserts the source-verified
order directly (plan R7).
"""

from eedesigncore.common.models import Property
from eedesigncore.common.sch_family.ordering import (
    kiid_path_sort_key,
    sort_schematic_lib_symbols,
)
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.symbol import Symbol, SymbolUnit
from eedesigncore.sch.models import (
    InstancePath,
    InstanceProject,
    Label,
    LabelKind,
    PlacedSymbol,
    PlacedSymbolPin,
    Schematic,
    Sheet,
    SheetPin,
)
from eedesigncore.sch.writer import (
    _dump_instances,
    _dump_label,
    _dump_placed_symbol,
    _dump_sheet,
    write_sch,
)
from eedesigncore.sym.enums import PinElectricalType, PinShape


def _symbol(name: str, *, pin_numbers: tuple[str, ...] = ()) -> Symbol:
    unit = SymbolUnit.for_symbol(
        name,
        unit_index=1,
        body_style_index=1,
        items=[
            Pin(
                electrical_type=PinElectricalType.passive,
                shape=PinShape.line,
                position=(0.0, -2.54 * index),
                angle=0.0,
                length=2.54,
                name="~",
                number=number,
            )
            for index, number in enumerate(pin_numbers)
        ],
    )
    return Symbol(name=name, units=[unit])


def _emitted_lib_ids(sch: Schematic) -> list[str]:
    """Return the lib id of every emitted ``(lib_symbols)`` entry, in order."""
    return [
        line.strip().removeprefix("(symbol ").strip('"')
        for line in write_sch(sch).splitlines()
        if line.startswith("\t\t(symbol ")
    ]


# ---------------------------------------------------------------------------
# lib_symbols map order (R3, sweep 3.2)
# ---------------------------------------------------------------------------


def test_lib_symbols_sort_by_lib_id_string() -> None:
    """The map key is ``LIB_ID::Format()`` (``sch_screen.cpp:1452``)."""
    symbols = [_symbol("power:GND"), _symbol("Device:R"), _symbol("Device:C")]

    assert [item.name for item in sort_schematic_lib_symbols(symbols)] == [
        "Device:C",
        "Device:R",
        "power:GND",
    ]


def test_lib_symbols_order_is_case_sensitive_and_not_natural() -> None:
    """The two discriminators the sweep read off a KiCad-written file (3.2).

    ``C15195`` before ``C1546`` kills a natural-number comparison.
    ``Sensor_Energy`` before ``power`` kills case folding.
    """
    symbols = [
        _symbol("power:GND"),
        _symbol("0-http:JLCPCB_C1546"),
        _symbol("Sensor_Energy:PAC1954"),
        _symbol("0-http:JLCPCB_C15195"),
        _symbol("0_JLC:TPS3895"),
    ]

    assert [item.name for item in sort_schematic_lib_symbols(symbols)] == [
        "0-http:JLCPCB_C15195",
        "0-http:JLCPCB_C1546",
        "0_JLC:TPS3895",
        "Sensor_Energy:PAC1954",
        "power:GND",
    ]


def test_symbol_inserted_out_of_order_emits_in_its_sorted_slot() -> None:
    """A symbol appended to the model takes its lib id position on the way out."""
    sch = Schematic(lib_symbols=[_symbol("Device:R"), _symbol("power:GND")])
    sch.lib_symbols.append(_symbol("Device:C"))

    assert sch.lib_symbols[-1].name == "Device:C"
    assert _emitted_lib_ids(sch) == ["Device:C", "Device:R", "power:GND"]


def test_reversed_lib_symbols_write_the_same_bytes_as_the_untouched_model() -> None:
    """The sort is the identity on canonical input, whatever the stored order."""
    names = ["Device:C", "Device:R", "power:GND"]
    original = Schematic(lib_symbols=[_symbol(name) for name in names])
    reversed_sch = Schematic(lib_symbols=[_symbol(name) for name in reversed(names)])

    assert write_sch(reversed_sch) == write_sch(original)


def test_mutated_pin_number_moves_inside_a_lib_symbols_entry() -> None:
    """A pin renumbered inside a ``lib_symbols`` entry takes its new slot.

    This is the T2 mutation class applied to the schematic path. No schematic
    resave oracle exists, so the assertion is the source-verified order:
    ``SaveSymbol`` is shared, so the pin comparator of
    ``sch_io_kicad_sexpr_lib_cache.cpp:507-520`` governs here too.
    """
    sch = Schematic(lib_symbols=[_symbol("Device:R", pin_numbers=("1", "2", "10"))])
    pins = [item for item in sch.lib_symbols[0].units[0].items if isinstance(item, Pin)]
    pins[0].number = "9"

    emitted = [
        line.split('"')[1]
        for line in write_sch(sch).splitlines()
        if line.lstrip().startswith("(number ")
    ]
    assert [pin.number for pin in pins] == ["9", "2", "10"]
    assert emitted == ["2", "9", "10"]


# ---------------------------------------------------------------------------
# Placed symbol fields by ordinal (R5, sweep 3.3)
# ---------------------------------------------------------------------------


def _field(name: str) -> Property:
    return Property(name=name, value=name, position=(0.0, 0.0))


def _placed(fields: list[Property]) -> PlacedSymbol:
    return PlacedSymbol(lib_id="Device:R", position=(0.0, 0.0), fields=fields)


def _emitted_names(text: str) -> list[str]:
    return [chunk.split('"')[1] for chunk in text.split("(property ")[1:]]


def test_placed_symbol_fields_emit_in_mandatory_then_user_order() -> None:
    """``SCH_SYMBOL::GetFields`` sorts on ``GetOrdinal()`` (``sch_symbol.cpp:1321-1325``).

    The schematic mandatory set INCLUDES Footprint, unlike the footprint side
    (sweep C-6).
    """
    symbol = _placed(
        [
            _field("MPN"),
            _field("Datasheet"),
            _field("Reference"),
            _field("Description"),
            _field("Value"),
            _field("Footprint"),
        ]
    )

    assert symbol.fields[0].name == "MPN"
    assert _emitted_names(_dump_placed_symbol(symbol)) == [
        "Reference",
        "Value",
        "Footprint",
        "Datasheet",
        "Description",
        "MPN",
    ]


def test_placed_symbol_user_fields_keep_file_order() -> None:
    """User ordinals count up from 42 in file order (``sch_field.cpp:83-93``)."""
    symbol = _placed([_field("Zeta"), _field("Reference"), _field("Alpha")])

    assert _emitted_names(_dump_placed_symbol(symbol)) == ["Reference", "Zeta", "Alpha"]


def test_placed_symbol_field_name_match_is_case_insensitive() -> None:
    """The parser maps a property name to a mandatory id with ``CmpNoCase``."""
    symbol = _placed([_field("MPN"), _field("value")])

    assert _emitted_names(_dump_placed_symbol(symbol)) == ["value", "MPN"]


def test_placed_symbol_pins_keep_stored_order() -> None:
    """``GetRawPins`` hands the writer the raw vector (``sch_symbol.h:668``)."""
    symbol = PlacedSymbol(
        lib_id="Device:R",
        position=(0.0, 0.0),
        pins=[
            PlacedSymbolPin(number="2"),
            PlacedSymbolPin(number="10"),
            PlacedSymbolPin(number="1"),
        ],
    )

    text = _dump_placed_symbol(symbol)
    assert [chunk.split('"')[1] for chunk in text.split("(pin ")[1:]] == ["2", "10", "1"]


# ---------------------------------------------------------------------------
# Symbol instances (R5, sweep 3.3)
# ---------------------------------------------------------------------------

_ROOT_A = "0aaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ROOT_B = "1bbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_LEAF_A = "2ccccccc-cccc-cccc-cccc-cccccccccccc"
_LEAF_B = "3ddddddd-dddd-dddd-dddd-dddddddddddd"


def _path(path: str, reference: str) -> InstancePath:
    return InstancePath(path=path, reference=reference, unit=1)


def _emitted_values(text: str, token: str) -> list[str]:
    return [chunk.split('"')[1] for chunk in text.split(token)[1:]]


def test_kiid_path_sort_key_ranks_a_shorter_path_first() -> None:
    """``KIID_PATH::operator<`` compares SIZE first (``include/kiid.h:189-203``).

    A plain string compare of the path text puts the two-element path first here,
    which is the WRONG rule. The sweep report claimed the two agree (3.3).
    """
    short = f"/{_ROOT_B}"
    long_path = f"/{_ROOT_A}/{_LEAF_A}"

    assert kiid_path_sort_key(short) < kiid_path_sort_key(long_path)
    assert long_path < short


def test_instance_paths_sort_by_kiid_path() -> None:
    project = InstanceProject(
        name="board",
        paths=[
            _path(f"/{_ROOT_A}/{_LEAF_B}", "R2"),
            _path(f"/{_ROOT_A}", "R3"),
            _path(f"/{_ROOT_A}/{_LEAF_A}", "R1"),
        ],
    )

    assert _emitted_values(_dump_instances([project]), "(path ") == [
        f"/{_ROOT_A}",
        f"/{_ROOT_A}/{_LEAF_A}",
        f"/{_ROOT_A}/{_LEAF_B}",
    ]


def test_instance_projects_sort_by_root_sheet_uuid_not_by_name() -> None:
    """Blocks are keyed on the FIRST sheet KIID (``sch_io_kicad_sexpr.cpp:845,916``)."""
    projects = [
        InstanceProject(name="zzz", paths=[_path(f"/{_ROOT_A}/{_LEAF_A}", "R1")]),
        InstanceProject(name="aaa", paths=[_path(f"/{_ROOT_B}/{_LEAF_A}", "R2")]),
    ]

    assert _emitted_values(_dump_instances(projects), "(project ") == ["zzz", "aaa"]


def test_instance_project_reordered_by_root_uuid_when_stored_out_of_order() -> None:
    projects = [
        InstanceProject(name="second", paths=[_path(f"/{_ROOT_B}", "R2")]),
        InstanceProject(name="first", paths=[_path(f"/{_ROOT_A}", "R1")]),
    ]

    assert _emitted_values(_dump_instances(projects), "(project ") == ["first", "second"]


def test_instance_project_without_paths_keeps_its_place() -> None:
    """A block with no path has no root KIID, so it sorts first and stays stable."""
    projects = [
        InstanceProject(name="empty", paths=[]),
        InstanceProject(name="rooted", paths=[_path(f"/{_ROOT_A}", "R1")]),
    ]

    assert _emitted_values(_dump_instances(projects), "(project ") == ["empty", "rooted"]


# ---------------------------------------------------------------------------
# The verified NOT-SORTED streams: stored order is the correct behaviour
# ---------------------------------------------------------------------------


def test_sheet_fields_keep_stored_order() -> None:
    """``SCH_SHEET::GetFields`` returns the raw vector (``sch_sheet.h:88-89``).

    This is the asymmetry the sweep records (3.4): a placed SYMBOL sorts its
    fields, a SHEET does not.
    """
    sheet = Sheet(fields=[_field("Zeta"), _field("Sheetname"), _field("Alpha")])

    assert _emitted_names(_dump_sheet(sheet)) == ["Zeta", "Sheetname", "Alpha"]


def test_sheet_pins_keep_stored_order() -> None:
    """``SCH_SHEET::GetPins`` returns the raw vector (``sch_sheet.h:228-229``)."""
    sheet = Sheet(pins=[SheetPin(name="B"), SheetPin(name="A")])

    assert _emitted_values(_dump_sheet(sheet), "(pin ") == ["B", "A"]


def test_label_fields_keep_stored_order() -> None:
    """``SCH_LABEL_BASE::GetFields`` returns the raw vector (``sch_label.h:212-213``).

    The sweep did not cover label fields. Verified here against the same source.
    """
    label = Label(
        kind=LabelKind.local, content="NET", fields=[_field("Zeta"), _field("Reference")]
    )

    assert _emitted_names(_dump_label(label)) == ["Zeta", "Reference"]
