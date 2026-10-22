"""Mutation-fidelity tests: what we write after a mutation is what KiCad writes.

The round-trip suite cannot see the gap this class covers. A KiCad-written file
is already in canonical node order, so preserving encounter order reproduces it
exactly. Only a MUTATED model reveals whether the writer canonicalises.

Procedure (R7): load a golden file, mutate it through the models, write file A,
copy A to B, resave B with the KiCad oracle, then assert A and B are
byte-identical.

Every oracle test carries a skip marker, so a runner without KiCad passes. Each
mutation also has an oracle-free twin that asserts the emitted order directly,
so the rule itself stays covered everywhere.

The CONTROL test runs first in this file. It resaves UNMUTATED output and asserts
byte identity, which proves the oracle adds no unrelated normalisation. Without
it, a later failure could not distinguish an ordering defect from a resave that
rewrites something else.

Oracle safety (T9 scope). Every oracle helper builds an argument LIST, never a
command string, and never passes ``shell=True``. The executable path is a module
constant, and the only variable argument is a path pytest created under
``tmp_path``. No library input and no fixture content reaches the argument list.
The call carries a timeout and never raises through to the runner.

Every oracle call also runs with ``KICAD_CONFIG_HOME`` pointed at an empty
``tmp_path`` directory, so ``kicad-cli`` reads and writes a throwaway settings
tree instead of the operator's ``%APPDATA%/kicad``. The tests are hermetic and
leave no trace on the machine.
"""

import os
import re
import shutil
import subprocess
from functools import cmp_to_key
from pathlib import Path

import pytest

from eedesigncore.common.models import (
    Fill,
    FillType,
    Property,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.common.ordering import str_num_cmp
from eedesigncore.common.pcb_family import (
    PcbArc,
    PcbLine,
    PcbPoly,
    PcbRect,
    PcbText,
    Zone,
    ZoneFill,
    ZoneFilledPolygon,
    ZoneOutlinePolygon,
    extract_fills,
    flatten_fills,
    replace_fills,
)
from eedesigncore.common.pcb_family.enums import PadShape, PadType
from eedesigncore.common.sch_family.pins import Pin
from eedesigncore.common.sch_family.shapes import SchArc, SchRect
from eedesigncore.common.sch_family.symbol import SymbolUnit
from eedesigncore.common.uuid_ops import generate_uuids
from eedesigncore.common.writers import fmt_iu
from eedesigncore.mod.models import Footprint, FootprintPlacement, Pad
from eedesigncore.pcb.models import Board
from eedesigncore.pcb.operations import inject_board, inject_footprint, new_board_from
from eedesigncore.pcb.transform import rotate_board, translate_board
from eedesigncore.sch.models import Line, LineType, Schematic
from eedesigncore.sym.enums import PinElectricalType, PinShape
from eedesigncore.sym.models import SymbolLib
from tests.private_inputs import private_path

KICAD_CLI = Path(r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")
ORACLE_TIMEOUT_SECONDS = 300

SYM_FIXTURES = sorted(Path("tests/fixtures/sym").glob("*.kicad_sym"))
GOLDEN = Path("tests/fixtures/sym/474811-E.kicad_sym")

# The one .kicad_mod fixture the oracle leaves untouched, so the only fixture
# that can act as a footprint control. Every other stock fixture reads
# `(generator "kicad-footprint-generator")`, and KiCad rewrites its generator
# token and injects the two mandatory fields it lacks (sweep contradiction C-7).
MOD_GOLDEN = Path(
    "tests/fixtures/mod/BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD.kicad_mod"
)

requires_kicad_cli = pytest.mark.skipif(
    not KICAD_CLI.is_file(),
    reason=f"KiCad oracle not installed at {KICAD_CLI}",
)


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment whose KiCad settings tree is a throwaway directory.

    ``kicad-cli`` reads and can REWRITE ``%APPDATA%/kicad`` on startup, for
    example the global library tables. ``KICAD_CONFIG_HOME`` moves that tree, so
    the oracle never touches the operator's install.
    """
    config_home = tmp_path / "kicad-config"
    config_home.mkdir(exist_ok=True)
    return {**os.environ, "KICAD_CONFIG_HOME": str(config_home)}


def _run_oracle(arguments: list[str], tmp_path: Path) -> None:
    """Run ``kicad-cli`` with a fixed argument list and an isolated settings tree."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, temp path only
        [str(KICAD_CLI), *arguments],
        capture_output=True,
        text=True,
        timeout=ORACLE_TIMEOUT_SECONDS,
        check=False,
        env=_isolated_env(tmp_path),
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"kicad-cli failed with code {completed.returncode}: "
            f"{completed.stdout.strip()} {completed.stderr.strip()}"
        )


def _resave_with_oracle(path: Path) -> None:
    """Resave *path* in place with ``kicad-cli sym upgrade --force``."""
    _run_oracle(["sym", "upgrade", "--force", str(path)], path.parent)


def _assert_oracle_agrees(lib: SymbolLib, tmp_path: Path) -> None:
    """Write *lib*, resave a copy with KiCad, and assert the two files match."""
    file_a = tmp_path / "written.kicad_sym"
    file_b = tmp_path / "resaved.kicad_sym"
    lib.to_file(file_a, line_ending="native")
    shutil.copyfile(file_a, file_b)

    _resave_with_oracle(file_b)

    assert file_a.read_bytes() == file_b.read_bytes()


def _golden() -> SymbolLib:
    return SymbolLib.from_file(GOLDEN)


def _pin_numbers(lib: SymbolLib) -> list[str]:
    return [item.number for item in lib.symbols[0].units[0].items if isinstance(item, Pin)]


def _emitted_pin_numbers(lib: SymbolLib) -> list[str]:
    return [
        line.split('"')[1]
        for line in lib.to_string().splitlines()
        if line.lstrip().startswith("(number ")
    ]


def _new_pin(number: str, y: float) -> Pin:
    return Pin(
        electrical_type=PinElectricalType.passive,
        shape=PinShape.line,
        position=(-10.16, y),
        angle=0.0,
        length=2.54,
        name="~",
        number=number,
    )


# ---------------------------------------------------------------------------
# The control: the oracle must be inert on unmutated output
# ---------------------------------------------------------------------------


@requires_kicad_cli
@pytest.mark.parametrize("path", SYM_FIXTURES, ids=lambda p: p.name)
def test_oracle_is_inert_on_unmutated_output(path: Path, tmp_path: Path) -> None:
    """Resaving our UNMUTATED output must change nothing.

    This separates ordering failures from unrelated normalisation. Every
    mutation test below is meaningful only while this one passes.
    """
    _assert_oracle_agrees(SymbolLib.from_file(path), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 1: renumber a pin
# ---------------------------------------------------------------------------


def _renumber_a_pin() -> SymbolLib:
    lib = _golden()
    for item in lib.symbols[0].units[0].items:
        if isinstance(item, Pin) and item.number == "1":
            item.number = "9"
            break
    return lib


def test_renumbered_pin_moves_to_its_new_slot() -> None:
    lib = _renumber_a_pin()

    assert _pin_numbers(lib)[0] == "9"
    assert _emitted_pin_numbers(lib) == [
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "MT1",
        "MT2",
        "MT3",
        "MT4",
    ]


@requires_kicad_cli
def test_renumbered_pin_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_renumber_a_pin(), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 2: insert a pin
# ---------------------------------------------------------------------------


def _insert_a_pin() -> SymbolLib:
    lib = _golden()
    lib.symbols[0].units[0].items.append(_new_pin("0", 12.7))
    return lib


def test_inserted_pin_lands_in_canonical_position_not_appended() -> None:
    lib = _insert_a_pin()

    assert _pin_numbers(lib)[-1] == "0"
    assert _emitted_pin_numbers(lib)[0] == "0"


@requires_kicad_cli
def test_inserted_pin_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_insert_a_pin(), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 2b: pathological pin numbers, the 32-bit wrap of ADR-071
# ---------------------------------------------------------------------------
#
# KiCad accumulates a digit run into a C++ ``int``
# (``common/string_utils.cpp:842-857``), so a run wider than ten digits wraps
# rather than growing. The suggested MEDIUM-4 remediation, strip leading zeros
# then compare lengths, would sort every wide run ABOVE the short ones and
# diverge from the binary. This test is the fidelity proof for the wrap, and the
# expected order below is the ORACLE order, not a derived one.

# Wrapped values: 2147483648 -> INT_MIN, 4294967296 -> 0, 12345678901234567890 ->
# -350287150, 00000000000000000009 -> 9, 10000000000 -> 1410065408, and
# 99999999999999999999 -> 1661992959.
PATHOLOGICAL_PIN_NUMBERS = (
    "2147483648",
    "12345678901234567890",
    "99999999999999999999",
    "00000000000000000009",
    "4294967296",
    "10000000000",
)

EXPECTED_PATHOLOGICAL_ORDER = [
    "2147483648",
    "12345678901234567890",
    "4294967296",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "00000000000000000009",
    "10000000000",
    "99999999999999999999",
    "MT1",
    "MT2",
    "MT3",
    "MT4",
]


def _add_pathological_pin_numbers() -> SymbolLib:
    lib = _golden()
    unit = lib.symbols[0].units[0]
    for index, number in enumerate(PATHOLOGICAL_PIN_NUMBERS):
        unit.items.append(_new_pin(number, 12.7 + 2.54 * index))
    return lib


def test_pathological_pin_numbers_sort_by_the_wrapped_value() -> None:
    """A run that overflows int32 sorts by its WRAPPED value, not its magnitude."""
    emitted = _emitted_pin_numbers(_add_pathological_pin_numbers())

    assert emitted == EXPECTED_PATHOLOGICAL_ORDER
    # The two load-bearing inversions a big-integer port would get wrong.
    assert emitted.index("2147483648") < emitted.index("1")
    assert emitted.index("4294967296") < emitted.index("1")


@requires_kicad_cli
def test_pathological_pin_numbers_match_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_add_pathological_pin_numbers(), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 3: move a shape
# ---------------------------------------------------------------------------


def _add_a_second_rect() -> SymbolLib:
    lib = _golden()
    unit = lib.symbols[0].units[0]
    original = next(item for item in unit.items if isinstance(item, SchRect))
    added = original.model_copy(deep=True)
    added.start = (original.start[0] + 50.8, original.start[1])
    added.end = (original.end[0] + 50.8, original.end[1])
    unit.items.append(added)
    return lib


def _move_a_shape() -> SymbolLib:
    lib = _add_a_second_rect()
    unit = lib.symbols[0].units[0]
    rects = [item for item in unit.items if isinstance(item, SchRect)]
    moved = rects[-1]
    moved.start = (rects[0].start[0] - 50.8, moved.start[1])
    moved.end = (rects[0].end[0] - 50.8, moved.end[1])
    return lib


def _emitted_rect_start_x(lib: SymbolLib) -> list[str]:
    return [
        line.strip().split()[1]
        for line in lib.to_string().splitlines()
        if line.strip().startswith("(start ")
    ]


def test_added_shape_emits_after_the_shape_it_sits_right_of() -> None:
    lib = _add_a_second_rect()
    rects = [item for item in lib.symbols[0].units[0].items if isinstance(item, SchRect)]

    assert _emitted_rect_start_x(lib) == [fmt_iu(rect.start[0]) for rect in rects]


def test_moved_shape_lands_in_its_new_slot() -> None:
    lib = _move_a_shape()
    rects = [item for item in lib.symbols[0].units[0].items if isinstance(item, SchRect)]

    # The moved rectangle is still LAST in the model and FIRST on the way out.
    assert _emitted_rect_start_x(lib)[0] == fmt_iu(rects[-1].start[0])


@requires_kicad_cli
def test_moved_shape_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_move_a_shape(), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 4: add a property
# ---------------------------------------------------------------------------


def _add_a_property() -> SymbolLib:
    lib = _golden()
    # Inserted at the FRONT, where a naive writer would emit it. KiCad orders
    # the five mandatory fields ahead of every user field.
    #
    # No text attributes are passed. The writer emits the KiCad default effects
    # block for a field built without them (ADR-068), so this property is exactly
    # what a caller writes with no knowledge of the effects grammar.
    lib.symbols[0].fields.insert(0, Property(name="MPN", value="474811-E", visible=False))
    return lib


def test_added_property_emits_after_the_mandatory_fields() -> None:
    lib = _add_a_property()

    assert lib.symbols[0].fields[0].name == "MPN"
    emitted = [
        line.split('"')[1]
        for line in lib.to_string().splitlines()
        if line.lstrip().startswith("(property ")
    ]
    assert emitted[:5] == ["Reference", "Value", "Footprint", "Datasheet", "Description"]
    assert "MPN" in emitted[5:]


@requires_kicad_cli
def test_added_property_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_add_a_property(), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 4b/4c: add a property to a symbol that carries the DCM data
# ---------------------------------------------------------------------------
#
# The oracle run of iteration 11.8 measured this exact defect on both write
# paths: we wrote `… ki_fp_filters, MPN` and KiCad rewrote it as
# `… MPN, ki_locked, ki_keywords, ki_fp_filters` (oracle sections 4 and 5). The
# three names are synthesized after the whole ordinal-sorted block
# (`sch_io_kicad_sexpr_lib_cache.cpp:462-476`), so no user property can follow
# them. `COTO_3602_Split` is the root case with all three gates on, and `ADW11`
# is the derived case, which never gets a `ki_locked` (audit 10.1 and 10.3).

RELAY = Path("tests/fixtures/sym/Relay.kicad_sym")


def _symbol_property_names(lib: SymbolLib, name: str) -> list[str]:
    """Property names emitted inside symbol *name*, in emission order."""
    names: list[str] = []
    inside = False
    for line in lib.to_string().splitlines():
        if line.startswith("\t(symbol "):
            inside = line == f'\t(symbol "{name}"'
        elif inside and line.startswith('\t\t(property "'):
            names.append(line.split('"')[1])
    return names


def _add_a_property_to(symbol_name: str) -> SymbolLib:
    lib = SymbolLib.from_file(RELAY)
    symbol = next(item for item in lib.symbols if item.name == symbol_name)
    symbol.fields.append(Property(name="MPN", value="MUTATED", visible=False))
    return lib


def test_added_property_precedes_the_dcm_block_on_the_root_path() -> None:
    lib = _add_a_property_to("COTO_3602_Split")
    names = _symbol_property_names(lib, "COTO_3602_Split")

    assert names == [
        "Reference",
        "Value",
        "Footprint",
        "Datasheet",
        "Description",
        "MPN",
        "ki_locked",
        "ki_keywords",
        "ki_fp_filters",
    ]


def test_added_property_precedes_the_dcm_block_on_the_derived_path() -> None:
    lib = _add_a_property_to("ADW11")
    names = _symbol_property_names(lib, "ADW11")

    assert names == [
        "Reference",
        "Value",
        "Footprint",
        "Datasheet",
        "Description",
        "MPN",
        "ki_keywords",
        "ki_fp_filters",
    ]
    assert "ki_locked" not in names


@requires_kicad_cli
def test_added_property_on_a_dcm_root_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_add_a_property_to("COTO_3602_Split"), tmp_path)


@requires_kicad_cli
def test_added_property_on_a_derived_symbol_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_add_a_property_to("ADW11"), tmp_path)


# ---------------------------------------------------------------------------
# Mutation 5: add a unit
# ---------------------------------------------------------------------------


def _add_a_unit() -> SymbolLib:
    lib = _golden()
    symbol = lib.symbols[0]
    added = SymbolUnit.for_symbol(
        symbol.name,
        unit_index=2,
        body_style_index=1,
        items=[_new_pin("100", 0.0)],
    )
    # Prepended, so an unsorted writer would emit unit 2 before unit 1.
    symbol.units.insert(0, added)
    return lib


def test_added_unit_emits_after_the_lower_unit_index() -> None:
    lib = _add_a_unit()

    assert lib.symbols[0].units[0].unit_index == 2
    text = lib.to_string()
    assert text.index('(symbol "474811-E_1_0"') < text.index('(symbol "474811-E_2_1"')


@requires_kicad_cli
def test_added_unit_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(_add_a_unit(), tmp_path)


# ---------------------------------------------------------------------------
# Every fixture, mutated the same way
# ---------------------------------------------------------------------------


@requires_kicad_cli
@pytest.mark.parametrize("path", SYM_FIXTURES, ids=lambda p: p.name)
def test_reversed_draw_items_match_the_oracle(path: Path, tmp_path: Path) -> None:
    """Reverse every unit's items, which is the harshest order mutation."""
    lib = SymbolLib.from_file(path)
    for symbol in lib.symbols:
        for unit in symbol.units:
            unit.items.reverse()

    _assert_oracle_agrees(lib, tmp_path)


def test_reversed_draw_items_write_the_same_bytes_as_the_untouched_model() -> None:
    """The sort is the identity on canonical input, whatever the stored order."""
    original = _golden()
    reversed_lib = _golden()
    for unit in reversed_lib.symbols[0].units:
        unit.items.reverse()

    assert reversed_lib.to_string() == original.to_string()


# ---------------------------------------------------------------------------
# Footprints: the kicad-cli fp upgrade oracle
#
# `fp upgrade` takes a FILE OR DIRECTORY, confirmed with `fp upgrade --help`,
# and it renames the footprint to match the file stem. So each helper builds a
# temporary `.pretty` directory and names the file after `fp_id`.
# ---------------------------------------------------------------------------


def _resave_footprints_with_oracle(library: Path) -> None:
    """Resave every footprint in *library* with ``kicad-cli fp upgrade --force``."""
    _run_oracle(["fp", "upgrade", "--force", str(library)], library.parent)


def _assert_footprint_oracle_agrees(footprint: Footprint, tmp_path: Path) -> None:
    """Write *footprint*, resave it with KiCad, and assert the bytes match."""
    library = tmp_path / "mutation.pretty"
    library.mkdir(exist_ok=True)
    written = library / f"{footprint.fp_id}.kicad_mod"
    footprint.to_file(written, line_ending="native")
    before = written.read_bytes()

    _resave_footprints_with_oracle(library)

    assert written.read_bytes() == before


def _mod_golden() -> Footprint:
    return Footprint.from_file(MOD_GOLDEN)


def _emitted_pad_numbers(footprint: Footprint) -> list[str]:
    return re.findall(r'\(pad "([^"]*)"', footprint.to_string())


def _emitted_property_names(footprint: Footprint) -> list[str]:
    return re.findall(r'\(property "([^"]+)"', footprint.to_string())


# The control: the oracle must be inert on unmutated footprint output


@requires_kicad_cli
def test_footprint_oracle_is_inert_on_unmutated_output(tmp_path: Path) -> None:
    """Resaving our UNMUTATED footprint output must change nothing.

    Only this fixture qualifies. Every other stock ``.kicad_mod`` carries
    ``(generator "kicad-footprint-generator")``, which KiCad rewrites, so a
    failure there could not be attributed to ordering (sweep C-7).
    """
    _assert_footprint_oracle_agrees(_mod_golden(), tmp_path)


# Footprint mutation 1: renumber a pad


def _renumber_a_pad() -> Footprint:
    footprint = _mod_golden()
    next(pad for pad in footprint.pads if pad.number == "A1").number = "K11"
    return footprint


def test_renumbered_pad_moves_to_its_new_slot() -> None:
    footprint = _renumber_a_pad()

    assert footprint.pads[0].number == "K11"
    emitted = _emitted_pad_numbers(footprint)
    assert emitted[0] == "A2"
    assert emitted[-1] == "K11"


@requires_kicad_cli
def test_renumbered_pad_matches_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_renumber_a_pad(), tmp_path)


# Footprint mutation 2: add a pad, which must land in its canonical position


def _add_a_pad() -> Footprint:
    footprint = _mod_golden()
    added = footprint.pads[0].model_copy(deep=True)
    added.number = "A0"
    added.position = (-5.5, -5.5)
    # KiCad gives every pad a uuid and always writes it. This library mints NO
    # uuid, ever: uuids stay 100 percent caller controlled, which is the one
    # sanctioned deviation from KiCad-exact output (operator ruling 2026-08-13,
    # ADR-068, ADR-019 lineage). The oracle therefore needs an explicit uuid here.
    added.id = "11111111-2222-3333-4444-555555555555"
    footprint.pads.append(added)
    return footprint


def test_added_pad_lands_in_canonical_position_not_appended() -> None:
    footprint = _add_a_pad()

    assert footprint.pads[-1].number == "A0"
    assert _emitted_pad_numbers(footprint)[0] == "A0"


@requires_kicad_cli
def test_added_pad_matches_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_add_a_pad(), tmp_path)


# Footprint mutation 3: move a drawing


def _move_a_drawing() -> Footprint:
    footprint = _mod_golden()
    moved = next(
        item for item in footprint.drawings if isinstance(item, PcbLine) and item.layer == "F.Fab"
    )
    moved.start = (-99.0, moved.start[1])
    moved.end = (-98.0, moved.end[1])
    return footprint


def _emitted_fab_line_starts(footprint: Footprint) -> list[str]:
    """Return the ``(start …)`` line of every emitted ``F.Fab`` ``fp_line``, in order."""
    starts: list[str] = []
    pending: str | None = None
    for line in footprint.to_string().splitlines():
        stripped = line.strip()
        if stripped == "(fp_line":
            pending = None
        elif stripped.startswith("(start ") and pending is None:
            pending = stripped
        elif stripped == '(layer "F.Fab")' and pending is not None:
            starts.append(pending)
            pending = None
    return starts


def test_moved_drawing_lands_in_its_new_slot() -> None:
    footprint = _move_a_drawing()
    stored = [item for item in footprint.drawings if isinstance(item, PcbLine)]
    moved_index = next(index for index, item in enumerate(stored) if item.start[0] == -99.0)

    # The moved line keeps its stored position, which is NOT the head.
    assert moved_index > 0
    # On the way out it leads its layer group, because its new x is the smallest
    # on F.Fab and the layer key groups it with the other F.Fab lines.
    assert _emitted_fab_line_starts(footprint)[0].startswith("(start -99 ")


@requires_kicad_cli
def test_moved_drawing_matches_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_move_a_drawing(), tmp_path)


# Footprint mutation 4: add a property


def _add_a_footprint_property() -> Footprint:
    footprint = _mod_golden()
    # Inserted at the FRONT, where a naive writer would emit it. KiCad orders the
    # four mandatory footprint fields ahead of every user field.
    #
    # No text attributes are passed here either (ADR-068). The explicit uuid
    # stays: uuids are 100 percent caller controlled, this library mints none,
    # and that is the one sanctioned deviation from KiCad-exact output (operator
    # ruling 2026-08-13, ADR-068, ADR-019 lineage).
    footprint.properties.insert(
        0,
        Property(
            name="MPN",
            value="BGA-100",
            visible=False,
            layer="F.Fab",
            id="66666666-7777-8888-9999-aaaaaaaaaaaa",
        ),
    )
    return footprint


def test_added_footprint_property_emits_after_the_mandatory_fields() -> None:
    footprint = _add_a_footprint_property()

    assert footprint.properties[0].name == "MPN"
    assert _emitted_property_names(footprint) == [
        "Reference",
        "Value",
        "Datasheet",
        "Description",
        "MPN",
    ]


@requires_kicad_cli
def test_added_footprint_property_matches_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_add_a_footprint_property(), tmp_path)


# Every footprint stream reversed, which is the harshest order mutation


def _reverse_every_footprint_stream() -> Footprint:
    footprint = _mod_golden()
    footprint.pads.reverse()
    footprint.drawings.reverse()
    footprint.properties.reverse()
    return footprint


@requires_kicad_cli
def test_reversed_footprint_streams_match_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_reverse_every_footprint_stream(), tmp_path)


def test_reversed_footprint_streams_write_the_same_bytes_as_the_untouched_model() -> None:
    """The sort is the identity on canonical input, whatever the stored order."""
    assert _reverse_every_footprint_stream().to_string() == _mod_golden().to_string()


# ---------------------------------------------------------------------------
# Injection: the oracle judges what inject_footprint produced (R8)
# ---------------------------------------------------------------------------


def _inject_the_golden_footprint() -> Footprint:
    """Inject a scrambled golden footprint, then make the result library-shaped.

    ``inject_footprint`` mints fresh child uuids, and the uuid is the last key of
    every PCB-family comparator. So this case exercises the sort on values no
    fixture carries, which the stored-order tests above cannot do.

    Board context adds three things a library file does not carry: ``(at …)``,
    the footprint-level ``(uuid …)`` (``CTL_OMIT_AT`` and ``CTL_OMIT_UUIDS``,
    ``pcb_io_kicad_sexpr.cpp:1216-1226``), and the ``lib:name`` qualification of
    the FPID. All three are undone here, so the oracle judges the child order and
    nothing else.
    """
    footprint = _mod_golden()
    footprint.pads.reverse()
    footprint.drawings.reverse()
    injected = inject_footprint(Board(), footprint, at=(0.0, 0.0))
    injected.at = None
    injected.uuid = None
    injected.fp_id = _mod_golden().fp_id
    return injected


def test_injected_footprint_children_stay_sorted() -> None:
    """Pads emit in number order even though the injected model stores them reversed."""
    injected = _inject_the_golden_footprint()

    emitted = _emitted_pad_numbers(injected)
    assert [pad.number for pad in injected.pads] == list(reversed(emitted))
    assert emitted == sorted(emitted, key=cmp_to_key(str_num_cmp))


@requires_kicad_cli
def test_injected_footprint_matches_the_oracle(tmp_path: Path) -> None:
    _assert_footprint_oracle_agrees(_inject_the_golden_footprint(), tmp_path)


# ---------------------------------------------------------------------------
# Arc winding: the LOAD-time normalisation of ADR-067
# ---------------------------------------------------------------------------
#
# This case differs from every mutation above. Nothing is mutated through the
# models. The FILE carries an arc whose winding disagrees with the KiCad internal
# winding, our loader normalises it exactly as EDA_SHAPE::SetArcGeometry does
# (common/eda_shape.cpp:1180-1205), and the oracle must agree byte for byte.


def _arc_property(name: str, value: str, hidden: bool) -> str:
    """One mandatory property, in the exact shape KiCad writes for a fresh field."""
    return (
        f'\t\t(property "{name}" "{value}"\n'
        "\t\t\t(at 0 0 0)\n"
        "\t\t\t(show_name no)\n"
        "\t\t\t(do_not_autoplace no)\n"
        + ("\t\t\t(hide yes)\n" if hidden else "")
        + "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        "\t\t)\n"
    )


def _arc_library_text(mid_y: str) -> str:
    """A complete one-symbol library whose only draw item is one arc.

    All five mandatory properties are present, because KiCad injects any missing
    one on load and that would mask the arc comparison.
    """
    return (
        "(kicad_symbol_lib\n"
        "\t(version 20251024)\n"
        '\t(generator "kicad_symbol_editor")\n'
        '\t(generator_version "10.0")\n'
        '\t(symbol "ARC_TEST"\n'
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(in_pos_files yes)\n"
        "\t\t(duplicate_pin_numbers_are_jumpers no)\n"
        + _arc_property("Reference", "U", False)
        + _arc_property("Value", "ARC_TEST", False)
        + _arc_property("Footprint", "", True)
        + _arc_property("Datasheet", "", True)
        + _arc_property("Description", "", True)
        + '\t\t(symbol "ARC_TEST_1_1"\n'
        "\t\t\t(arc\n"
        "\t\t\t\t(start 0 0)\n"
        f"\t\t\t\t(mid 1 {mid_y})\n"
        "\t\t\t\t(end 2 0)\n"
        "\t\t\t\t(stroke\n\t\t\t\t\t(width 0)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n"
        "\t\t\t\t(fill\n\t\t\t\t\t(type none)\n\t\t\t\t)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)\n"
        ")\n"
    )


def _arc_points(lib: SymbolLib) -> tuple[tuple[float, float], ...]:
    arc = next(item for item in lib.symbols[0].units[0].items if isinstance(item, SchArc))
    return (arc.start, arc.mid, arc.end)


def test_a_wrong_winding_arc_swaps_its_ends_at_load() -> None:
    lib = SymbolLib.from_string(_arc_library_text("-1"))

    assert _arc_points(lib) == ((2.0, 0.0), (1.0, -1.0), (0.0, 0.0))


def test_a_right_winding_arc_is_left_alone_at_load() -> None:
    lib = SymbolLib.from_string(_arc_library_text("1"))

    assert _arc_points(lib) == ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0))


@requires_kicad_cli
def test_a_wrong_winding_arc_matches_the_oracle(tmp_path: Path) -> None:
    _assert_oracle_agrees(SymbolLib.from_string(_arc_library_text("-1")), tmp_path)


@requires_kicad_cli
def test_a_right_winding_arc_matches_the_oracle(tmp_path: Path) -> None:
    """The control for the case above: an already-canonical arc must not move."""
    _assert_oracle_agrees(SymbolLib.from_string(_arc_library_text("1")), tmp_path)


# ---------------------------------------------------------------------------
# Board and schematic oracles (ADR-069)
# ---------------------------------------------------------------------------
#
# KiCad 10.0.3 ships `kicad-cli pcb upgrade --force` and
# `kicad-cli sch upgrade --force`, so boards and schematics are not oracle-free
# after all, which corrects plan R7. Both commands carry load-context quirks,
# measured in ADR-069, so a fixture qualifies for a byte-identity test only when
# its INERTNESS CONTROL passes. A fixture whose control fails gets a targeted
# assertion that names its quirk instead.

BOARD_GOLDEN = private_path("template_board", ".kicad_pcb")
BOARD_GENERATORS = Path("tests/fixtures/pcb/battery_buck_120w_generators.kicad_pcb")
SCH_GOLDEN = Path("tests/fixtures/sch/minimal_symbols.kicad_sch")

# Rule-area and table controls use KiCad 10.0.4 saves. See fixtures/sch/README.md.
INERT_SCH_FIXTURES = [
    Path("tests/fixtures/sch/minimal_symbols.kicad_sch"),
    Path("tests/fixtures/sch/placed_symbols.kicad_sch"),
    Path("tests/fixtures/sch/t3a_noconnect_wire_dense.kicad_sch"),
    Path("tests/fixtures/sch/t3a_text_hlabel_textbox.kicad_sch"),
    Path("tests/fixtures/sch/t3a_wire_bus_globallabel.kicad_sch"),
    Path("tests/fixtures/sch/t3b_image.kicad_sch"),
    Path("tests/fixtures/sch/t3b_rule_area.kicad_sch"),
    Path("tests/fixtures/sch/t3b_table.kicad_sch"),
    Path("tests/fixtures/sch/t4_subsheet_child.kicad_sch"),
]


def _assert_board_oracle_agrees(board: Board, tmp_path: Path) -> None:
    """Write *board*, resave a copy with ``pcb upgrade --force``, and compare."""
    file_a = tmp_path / "written.kicad_pcb"
    file_b = tmp_path / "resaved.kicad_pcb"
    board.to_file(file_a, line_ending="native")
    shutil.copyfile(file_a, file_b)

    _run_oracle(["pcb", "upgrade", "--force", str(file_b)], tmp_path)

    assert file_a.read_bytes() == file_b.read_bytes()


def _resaved_schematic_text(schematic: Schematic, tmp_path: Path) -> tuple[str, str]:
    """Return our emitted text and the oracle resave of it, both as LF text."""
    file_a = tmp_path / "written.kicad_sch"
    file_b = tmp_path / "resaved.kicad_sch"
    schematic.to_file(file_a, line_ending="native")
    shutil.copyfile(file_a, file_b)

    _run_oracle(["sch", "upgrade", "--force", str(file_b)], tmp_path)

    return (
        file_a.read_bytes().decode("utf-8").replace("\r\n", "\n"),
        file_b.read_bytes().decode("utf-8").replace("\r\n", "\n"),
    )


def _assert_schematic_oracle_agrees(schematic: Schematic, tmp_path: Path) -> None:
    ours, resaved = _resaved_schematic_text(schematic, tmp_path)
    assert ours == resaved


# The control: the board oracle must be inert on unmutated output


@requires_kicad_cli
def test_board_oracle_is_inert_on_unmutated_output(tmp_path: Path) -> None:
    _assert_board_oracle_agrees(Board.from_file(BOARD_GOLDEN), tmp_path)


# Board mutation 1: a property inserted out of name order


def _add_a_board_property() -> Board:
    board = Board.from_file(BOARD_GOLDEN)
    # Appended LAST, where a naive writer would emit it. KiCad holds board
    # properties in a std::map, so the name decides (board.h:401).
    board.properties.append(Property(name="AAA_TEST", value="inserted last"))
    return board


def _emitted_board_property_names(board: Board) -> list[str]:
    return [
        line.split('"')[1]
        for line in board.to_string().splitlines()
        if line.startswith("\t(property ")
    ]


def test_added_board_property_emits_in_name_order() -> None:
    board = _add_a_board_property()

    assert board.properties[-1].name == "AAA_TEST"
    assert _emitted_board_property_names(board)[0] == "AAA_TEST"


@requires_kicad_cli
def test_added_board_property_matches_the_oracle(tmp_path: Path) -> None:
    _assert_board_oracle_agrees(_add_a_board_property(), tmp_path)


# Board mutation 2: two footprints appended in DESCENDING uuid order


def _add_two_footprints() -> Board:
    board = Board.from_file(BOARD_GOLDEN)
    for uuid, x in (
        ("ffffffff-0000-0000-0000-000000000001", 20.0),
        ("00000000-0000-0000-0000-000000000002", 60.0),
    ):
        placed = _mod_golden()
        placed.uuid = uuid
        placed.at = FootprintPlacement(position=(x, 20.0))
        placed.fp_id = "TestLib:BGA-100"
        board.footprints.append(placed)
    return board


def _emitted_footprint_uuids(board: Board) -> list[str]:
    return [
        line.split('"')[1]
        for line in board.to_string().splitlines()
        if line.startswith("\t\t(uuid ")
    ]


def test_added_footprints_emit_in_uuid_order() -> None:
    board = _add_two_footprints()

    assert [fp.uuid for fp in board.footprints] == [
        "ffffffff-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert _emitted_footprint_uuids(board) == [
        "00000000-0000-0000-0000-000000000002",
        "ffffffff-0000-0000-0000-000000000001",
    ]


@requires_kicad_cli
def test_added_footprints_match_the_oracle(tmp_path: Path) -> None:
    _assert_board_oracle_agrees(_add_two_footprints(), tmp_path)


# Board quirk: a fixture with no board header loses its generators


@requires_kicad_cli
def test_the_board_oracle_drops_generators_from_a_headerless_fixture(tmp_path: Path) -> None:
    """This fixture does NOT qualify for a byte-identity test, and here is why.

    ``battery_buck_120w_generators.kicad_pcb`` is a reduced extract: it carries
    four ``(generated …)`` tuning patterns and no ``(general …)``, ``(layers …)``
    or ``(setup …)`` block. The resave injects the default header and DROPS all
    four generators, because a generator on ``In5.Cu`` cannot survive on a board
    whose layer set does not name that layer.

    The drop is a property of the FIXTURE, not of the command. A real corpus
    board that carries a generator keeps it through the same resave (ADR-069).
    """
    resaved = tmp_path / "generators.kicad_pcb"
    shutil.copyfile(BOARD_GENERATORS, resaved)
    stored = BOARD_GENERATORS.read_text(encoding="utf-8")

    _run_oracle(["pcb", "upgrade", "--force", str(resaved)], tmp_path)
    text = resaved.read_text(encoding="utf-8")

    assert stored.count("(generated") == 4
    assert text.count("(generated") == 0
    assert "(layers" in text


# The control: the schematic oracle must be inert on unmutated output


@requires_kicad_cli
@pytest.mark.parametrize("path", INERT_SCH_FIXTURES, ids=lambda p: p.name)
def test_schematic_oracle_is_inert_on_unmutated_output(path: Path, tmp_path: Path) -> None:
    _assert_schematic_oracle_agrees(Schematic.from_file(path), tmp_path)


# Schematic mutation 1: lib_symbols added out of lib id order


def _add_lib_symbols() -> Schematic:
    schematic = Schematic.from_file(SCH_GOLDEN)
    library = SymbolLib.from_file(GOLDEN)
    for name in ("ZZZ:474811-E", "AAA:474811-E"):
        entry = library.symbols[0].model_copy(deep=True)
        entry.name = name
        schematic.lib_symbols.append(entry)
    return schematic


def _emitted_lib_symbol_names(schematic: Schematic) -> list[str]:
    return [
        line.split('"')[1]
        for line in schematic.to_string().splitlines()
        if line.startswith("\t\t(symbol ")
    ]


def test_added_lib_symbols_emit_in_lib_id_order() -> None:
    schematic = _add_lib_symbols()

    assert [entry.name for entry in schematic.lib_symbols] == ["ZZZ:474811-E", "AAA:474811-E"]
    assert _emitted_lib_symbol_names(schematic) == ["AAA:474811-E", "ZZZ:474811-E"]


@requires_kicad_cli
def test_added_lib_symbols_match_the_oracle(tmp_path: Path) -> None:
    _assert_schematic_oracle_agrees(_add_lib_symbols(), tmp_path)


# Schematic mutation 2: two wires appended in DESCENDING uuid order


def _add_two_wires() -> Schematic:
    schematic = Schematic.from_file(SCH_GOLDEN)
    for uuid, y in (
        ("ffffffff-0000-0000-0000-000000000005", 25.4),
        ("00000000-0000-0000-0000-000000000006", 50.8),
    ):
        schematic.lines.append(
            Line(
                line_type=LineType.wire,
                start=(0.0, y),
                end=(10.0, y),
                stroke=Stroke(width=0.0, type=StrokeType.default),
                uuid=uuid,
            )
        )
    return schematic


def _added_wire_uuids(schematic: Schematic) -> list[str]:
    return [
        line.split('"')[1]
        for line in schematic.to_string().splitlines()
        if line.strip().startswith("(uuid ") and "-0000-0000-0000-00000000000" in line
    ]


def test_added_wires_emit_in_uuid_order() -> None:
    schematic = _add_two_wires()

    assert schematic.lines[0].uuid == "ffffffff-0000-0000-0000-000000000005"
    assert _added_wire_uuids(schematic) == [
        "00000000-0000-0000-0000-000000000006",
        "ffffffff-0000-0000-0000-000000000005",
    ]


@requires_kicad_cli
def test_added_wires_match_the_oracle(tmp_path: Path) -> None:
    _assert_schematic_oracle_agrees(_add_two_wires(), tmp_path)


# Schematic quirks: two load-context effects that block byte identity

SCH_PROJECT_NAME = re.compile(r'\(project "[^"]*"')


def _without_the_schematic_quirks(text: str) -> str:
    """Remove the two load-context effects of ``sch upgrade`` (ADR-069).

    1. A bare resave has no project, so every placed-symbol instance block loses
       its project NAME. KiCad rebuilds that name from runtime state
       (``sch_io_kicad_sexpr.cpp:927-932``), which ADR-063 already records.
    2. The root ``(embedded_fonts …)`` node is written only for the schematic's
       first top-level sheet (``sch_io_kicad_sexpr.cpp:532-538``). A bare resave
       has no such sheet, so the node disappears.
    """
    lines = [line for line in text.splitlines() if line != "\t(embedded_fonts no)"]
    return "\n".join(SCH_PROJECT_NAME.sub('(project ""', line) for line in lines)


@requires_kicad_cli
def test_a_schematic_with_instances_matches_the_oracle_apart_from_the_two_quirks(
    tmp_path: Path,
) -> None:
    """``t6_placed_variant`` does NOT qualify for a byte-identity test.

    Its resave blanks two instance project names and drops the root
    ``(embedded_fonts no)`` node. Neither is an ordering effect, so this test
    asserts equality with exactly those two classes normalised away.
    """
    path = Path("tests/fixtures/sch/t6_placed_variant.kicad_sch")
    ours, resaved = _resaved_schematic_text(Schematic.from_file(path), tmp_path)

    assert ours != resaved
    assert _without_the_schematic_quirks(ours) == _without_the_schematic_quirks(resaved)


@requires_kicad_cli
def test_the_schematic_oracle_drops_root_embedded_files(tmp_path: Path) -> None:
    """``t5_embedded_root`` does NOT qualify either, for the same root cause.

    ``(embedded_files …)`` is written beside ``(embedded_fonts …)``, under the
    same first-top-level-sheet guard (``sch_io_kicad_sexpr.cpp:532-538``), so a
    bare resave drops the whole block.
    """
    path = Path("tests/fixtures/sch/t5_embedded_root.kicad_sch")
    ours, resaved = _resaved_schematic_text(Schematic.from_file(path), tmp_path)

    assert "(embedded_files" in ours
    assert "(embedded_files" not in resaved


# ---------------------------------------------------------------------------
# Translation oracles (iteration 14, work order P1-translate)
# ---------------------------------------------------------------------------
#
# A translation invents new coordinate values, so it tests something no
# untouched round trip can: that our internal-unit arithmetic and our number
# formatting land on the exact text KiCad writes for those values. The delta is
# deliberately awkward — 12.7 mm is half an inch and 0.0254 mm is one mil, and
# both meet a metric grid at digits a naive float add would smear.
#
# The frame rules these oracles cover are recorded in
# `context/audit/footprint-transform-frame-2026-09-02.md`.

TRANSLATE_DELTAS = [(12.7, -3.81), (0.0254, 0.0254), (-100.0, 250.0)]


def _board_with_moveable_content() -> Board:
    """The golden header, plus content whose coordinates a move must rewrite.

    The golden board is a header-only template, so a move on it is a no-op and
    proves nothing. Two placed footprints and three drawings give the move real
    coordinates to work on, and every one of them is a stock KiCad shape the
    oracle knows how to resave.
    """
    board = _add_two_footprints()
    board.drawings.extend(
        [
            PcbLine(
                start=(20.0, 20.0),
                end=(80.0, 60.0),
                stroke=Stroke(width=0.15, type=StrokeType.solid),
                layer="Edge.Cuts",
                id="aaaaaaaa-0000-0000-0000-000000000001",
            ),
            PcbArc(
                # Wound as KiCad stores it. ``SetArcGeometry`` swaps the ends of
                # an arc whose winding disagrees (ADR-067), and this fixture must
                # be canonical before a move can be judged.
                start=(20.0, 40.0),
                mid=(15.857864, 30.0),
                end=(20.0, 20.0),
                stroke=Stroke(width=0.15, type=StrokeType.solid),
                layer="Edge.Cuts",
                id="aaaaaaaa-0000-0000-0000-000000000002",
            ),
            PcbPoly(
                pts=[(30.0, 70.0), (40.0, 70.0), (40.0, 75.0)],
                stroke=Stroke(width=0.0, type=StrokeType.solid),
                fill=Fill(type=FillType.background),
                layer="F.Cu",
                net_name="GND",
                id="aaaaaaaa-0000-0000-0000-000000000003",
            ),
        ]
    )
    return board


def _translated_board(delta: tuple[float, float]) -> Board:
    board = _board_with_moveable_content()
    translate_board(board, delta)
    return board


@requires_kicad_cli
def test_the_board_oracle_is_inert_on_the_untranslated_content(tmp_path: Path) -> None:
    """The control. The added content must resave unchanged before a move can."""
    _assert_board_oracle_agrees(_board_with_moveable_content(), tmp_path)


@requires_kicad_cli
@pytest.mark.parametrize("delta", TRANSLATE_DELTAS)
def test_a_translated_board_matches_the_oracle(delta: tuple[float, float], tmp_path: Path) -> None:
    _assert_board_oracle_agrees(_translated_board(delta), tmp_path)


def test_a_translation_rewrites_the_coordinates_it_should() -> None:
    """The oracle-free twin: a runner with no KiCad still covers the rule."""
    before = _board_with_moveable_content().to_string()
    after = _translated_board((12.7, -3.81)).to_string()

    assert after != before
    assert "(start 32.7 16.19)" in after  # the Edge.Cuts line, moved
    assert "(at 32.7 16.19" in after  # the first footprint placement, moved


def test_a_translation_and_its_inverse_restore_the_bytes() -> None:
    board = _board_with_moveable_content()
    before = board.to_string()

    translate_board(board, (12.7, -3.81))
    translate_board(board, (-12.7, 3.81))

    assert board.to_string() == before


# ---------------------------------------------------------------------------
# Rotation oracles (iteration 14, work order P1-rotate)
# ---------------------------------------------------------------------------
#
# A resave oracle CANNOT judge a rotation. KiCad's parse and its write are
# inverse for every angle field on the file, so a board with stale child angles
# is a fixed point of `pcb upgrade --force` exactly like a correct one. Only a
# PLOT sees the geometry the angles mean.
#
# Two plot oracles run here, each with a control that must FAIL, so the oracle is
# shown to discriminate rather than merely to pass. The control is the naive
# rotation the work order asked about: change the footprint placement angle and
# leave every child alone.
#
# * IPC-D-356 carries one record per pad with its position, its size and its
#   ROTATION, so it judges the pad rule.
# * A silkscreen gerber carries the plotted glyph outlines, so its extent judges
#   the footprint text rule.
#
# Evidence for the rules themselves:
# `context/audit/footprint-transform-frame-2026-09-02.md`.

ROTATION_CENTER = (60.0, 60.0)

# One IPC-D-356 pad record: position, size, rotation. The leading position X
# carries a sign and the size X does not, which is what separates the two pairs.
IPC_PAD = re.compile(r"X([+-]\d+)Y(-\d+)X(\d+)Y(\d+)R(\d+)")

# A gerber DRAW or MOVE command. The anchor matters: without it the pattern
# also matches the 4.6 in the `%FSLAX46Y46*%` format header.
GERBER_COORD = re.compile(r"^X(-?\d+)Y(-?\d+)D0\d\*$", re.MULTILINE)


def _asymmetric_part() -> Footprint:
    """A footprint whose pads are asymmetric in position AND in angle.

    A symmetric part cannot fail a rotation test: its plot looks the same
    whatever the child angles do. Pad 2 sits at 30 degrees so a wrong pad rule
    shows up as a wrong rotation field, not only as a wrong position.
    """
    return Footprint(
        fp_id="TestLib:Asym",
        uuid="11111111-1111-1111-1111-111111111111",
        at=FootprintPlacement(position=ROTATION_CENTER, angle=0.0),
        layer="F.Cu",
        pads=[
            Pad(
                number="1",
                pad_type=PadType.smd,
                shape=PadShape.rect,
                position=(-3.0, 0.0),
                angle=0.0,
                size=(2.0, 1.0),
                layers=["F.Cu", "F.Paste", "F.Mask"],
                id="22222222-0000-0000-0000-000000000001",
            ),
            Pad(
                number="2",
                pad_type=PadType.smd,
                shape=PadShape.rect,
                position=(3.0, 0.0),
                angle=30.0,
                size=(2.0, 1.0),
                layers=["F.Cu", "F.Paste", "F.Mask"],
                id="22222222-0000-0000-0000-000000000002",
            ),
        ],
        drawings=[
            PcbText(
                text_type="user",
                content="WIDE",
                position=(0.0, 0.0),
                angle=0.0,
                layer="F.SilkS",
                unlocked=True,  # not keep-upright, so the turn is a plain add
                id="33333333-0000-0000-0000-000000000001",
                attributes=TextAttributes(font_size=(1.5, 1.5), font_thickness=0.2),
            )
        ],
    )


def _rotation_probe_board() -> Board:
    board = Board.from_file(BOARD_GOLDEN)
    board.footprints.append(_asymmetric_part())
    return board


def _turned_board() -> Board:
    board = _rotation_probe_board()
    rotate_board(board, 90.0, center=ROTATION_CENTER)
    return board


def _naively_turned_board() -> Board:
    """The control: only the placement angle changes, as the naive rule would."""
    board = _rotation_probe_board()
    placement = board.footprints[-1].at
    assert placement is not None
    placement.angle = 90.0
    return board


def _ipc_pads(board: Board, tmp_path: Path, name: str) -> set[tuple[int, int, int, int, int]]:
    """Return the IPC-D-356 pad records KiCad plots for *board*."""
    source = tmp_path / f"{name}.kicad_pcb"
    report = tmp_path / f"{name}.d356"
    board.to_file(source, line_ending="native")

    _run_oracle(["pcb", "export", "ipcd356", "-o", str(report), str(source)], tmp_path)

    text = report.read_text(encoding="utf-8")
    return {
        (int(x), int(y), int(size_x), int(size_y), int(rotation))
        for x, y, size_x, size_y, rotation in IPC_PAD.findall(text)
    }


def _quarter_turn_in_ipc(
    pads: set[tuple[int, int, int, int, int]],
    center: tuple[int, int],
) -> set[tuple[int, int, int, int, int]]:
    """Map IPC-D-356 pad records through the quarter turn this test applies.

    IPC-D-356 negates the KiCad y axis, so KiCad's ``(x, y) -> (y, -x)`` becomes
    a swap of the two offsets in the report, and a KiCad turn of +90 reads as
    minus 90 in the report's rotation field. Pad size is unchanged, because the
    report states the pad size in the pad's own frame.
    """
    turned = set()
    for x, y, size_x, size_y, rotation in pads:
        offset_x, offset_y = x - center[0], y - center[1]
        turned.add(
            (
                center[0] + offset_y,
                center[1] + offset_x,
                size_x,
                size_y,
                (rotation - 90) % 360,
            )
        )
    return turned


def _ipc_center(pads: set[tuple[int, int, int, int, int]]) -> tuple[int, int]:
    """The rotation centre in report units: 0.0001 inch, y negated."""
    per_inch = 10000.0 / 25.4
    return (round(ROTATION_CENTER[0] * per_inch), -round(ROTATION_CENTER[1] * per_inch))


def _silk_extent(board: Board, tmp_path: Path, name: str) -> tuple[int, int]:
    """Return the width and height of everything KiCad plots on the silkscreen."""
    source = tmp_path / f"{name}.kicad_pcb"
    output = tmp_path / f"{name}-gerber"
    board.to_file(source, line_ending="native")

    _run_oracle(
        [
            "pcb",
            "export",
            "gerbers",
            "--layers",
            "F.SilkS",
            "--no-protel-ext",
            "-o",
            str(output),
            str(source),
        ],
        tmp_path,
    )

    text = "".join(
        plotted.read_text(encoding="utf-8", errors="replace") for plotted in output.glob("*")
    )
    points = [(int(x), int(y)) for x, y in GERBER_COORD.findall(text)]
    assert points, "the silkscreen plot carried no coordinates"
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (max(xs) - min(xs), max(ys) - min(ys))


@requires_kicad_cli
def test_a_rotated_board_puts_every_pad_where_kicad_plots_it(tmp_path: Path) -> None:
    """The pad oracle: our turn must reproduce KiCad's own plotted pad records."""
    flat = _ipc_pads(_rotation_probe_board(), tmp_path, "flat")
    turned = _ipc_pads(_turned_board(), tmp_path, "turned")

    assert turned == _quarter_turn_in_ipc(flat, _ipc_center(flat))


@requires_kicad_cli
def test_the_naive_rotation_leaves_every_pad_turned_wrongly(tmp_path: Path) -> None:
    """The control. Without it, the test above could pass on an inert oracle.

    The naive rule puts each pad in the RIGHT place, because KiCad applies the
    placement angle to a footprint-local pad position on load. It leaves each
    pad's own rotation untouched, which is the defect.
    """
    flat = _ipc_pads(_rotation_probe_board(), tmp_path, "flat")
    naive = _ipc_pads(_naively_turned_board(), tmp_path, "naive")
    expected = _quarter_turn_in_ipc(flat, _ipc_center(flat))

    assert naive != expected
    positions = {(record[0], record[1]) for record in naive}
    assert positions == {(record[0], record[1]) for record in expected}
    assert {record[4] for record in naive} == {record[4] for record in flat}


@requires_kicad_cli
def test_a_rotated_board_turns_the_silkscreen_text(tmp_path: Path) -> None:
    """The text oracle: a quarter turn must swap the plotted extent."""
    flat_width, flat_height = _silk_extent(_rotation_probe_board(), tmp_path, "flat")
    turned_width, turned_height = _silk_extent(_turned_board(), tmp_path, "turned")

    assert (turned_width, turned_height) == (flat_height, flat_width)


@requires_kicad_cli
def test_the_naive_rotation_leaves_the_silkscreen_text_flat(tmp_path: Path) -> None:
    """The control for the text oracle."""
    flat = _silk_extent(_rotation_probe_board(), tmp_path, "flat")
    naive = _silk_extent(_naively_turned_board(), tmp_path, "naive")

    assert naive == flat


@requires_kicad_cli
@pytest.mark.parametrize("angle", [90.0, 180.0, 270.0])
def test_a_rotated_board_matches_the_resave_oracle(angle: float, tmp_path: Path) -> None:
    """A resave cannot judge the geometry, but it still judges the OUTPUT.

    It proves the turned coordinates are written exactly as KiCad writes them and
    that the item order stays canonical after the turn.
    """
    board = _board_with_moveable_content()
    rotate_board(board, angle, center=ROTATION_CENTER)

    _assert_board_oracle_agrees(board, tmp_path)


def test_a_turn_and_its_inverse_restore_the_bytes() -> None:
    """The oracle-free twin. A quarter turn is exact and it is reversible."""
    board = _board_with_moveable_content()
    before = board.to_string()

    rotate_board(board, 90.0, center=ROTATION_CENTER)
    assert board.to_string() != before
    rotate_board(board, -90.0, center=ROTATION_CENTER)

    assert board.to_string() == before


def test_four_quarter_turns_leave_a_pad_angle_at_360_as_kicad_does() -> None:
    """Four quarter turns are NOT the identity, and that is not a defect.

    A pad angle takes no normalisation. ``PAD::Rotate`` adds the turn to the
    padstack orientation (pad.cpp:2155-2161) and ``PADSTACK::SetOrientation``
    stores it raw (padstack.h:345), so four quarter turns in KiCad's own editor
    leave a pad that started at zero reading 360. Every coordinate does return,
    because a quarter turn is exact.
    """
    board = _rotation_probe_board()
    before_positions = [pad.position for pad in board.footprints[-1].pads]

    for _ in range(4):
        rotate_board(board, 90.0, center=ROTATION_CENTER)

    footprint = board.footprints[-1]
    assert footprint.at is not None
    assert footprint.at.angle == 0.0
    assert [pad.position for pad in footprint.pads] == before_positions
    assert [pad.angle for pad in footprint.pads] == [360.0, 390.0]


def test_a_rotation_rejects_a_board_rectangle_off_the_quarter_turns() -> None:
    """KiCad turns a non-cardinally rotated rectangle into a polygon."""
    board = _board_with_moveable_content()
    board.drawings.append(
        PcbRect(
            start=(10.0, 10.0),
            end=(20.0, 20.0),
            stroke=Stroke(width=0.1, type=StrokeType.solid),
            layer="Edge.Cuts",
            id="aaaaaaaa-0000-0000-0000-000000000004",
        )
    )

    with pytest.raises(ValueError, match="multiple of 90"):
        rotate_board(board, 45.0)


# ---------------------------------------------------------------------------
# Board-into-board injection (iteration 14, work order P3 to P7)
# ---------------------------------------------------------------------------
#
# This is the end-to-end shape the consumer runs: scaffold a board from one
# board's settings, then copy that board's content into it several times, each
# copy at its own place with its own derived uuids and its own net names.
#
# Two properties are asserted. KiCad resaves the result unchanged, which proves
# the output is canonical and well formed. And two runs of the same inputs
# produce the same bytes, which is what makes a rebuild auditable.

STEP_OFFSETS = [(0.0, 0.0), (60.0, 0.0), (0.0, 60.0), (60.0, 60.0)]


def _stepped_board() -> Board:
    """One source board copied into a scaffold four times, with derived uuids."""
    source = _board_with_moveable_content()
    stepped = new_board_from(source)
    for row, (dx, dy) in enumerate(STEP_OFFSETS):
        key = f"step-{row}"
        inject_board(
            stepped,
            source,
            offset=(dx, dy),
            instance_key=key,
            net_map={name: f"{name}_{row}" for name in sorted(source.net_names)},
        )
    return stepped


def test_a_stepped_board_is_reproducible_to_the_byte() -> None:
    """The oracle-free twin, and the property the consumer depends on most."""
    assert _stepped_board().to_string() == _stepped_board().to_string()


def test_a_stepped_board_keeps_every_instance_apart() -> None:
    source = _board_with_moveable_content()
    stepped = _stepped_board()

    assert len(stepped.footprints) == len(source.footprints) * len(STEP_OFFSETS)
    uuids = [fp.uuid for fp in stepped.footprints]
    assert len(set(uuids)) == len(uuids)
    assert stepped.net_names == {
        f"{name}_{row}" for row in range(len(STEP_OFFSETS)) for name in source.net_names
    }


@requires_kicad_cli
def test_a_stepped_board_matches_the_resave_oracle(tmp_path: Path) -> None:
    _assert_board_oracle_agrees(_stepped_board(), tmp_path)


@requires_kicad_cli
def test_a_turned_instance_matches_the_resave_oracle(tmp_path: Path) -> None:
    source = _board_with_moveable_content()
    stepped = new_board_from(source)
    inject_board(stepped, source, instance_key="flat")
    inject_board(
        stepped,
        source,
        offset=(120.0, 0.0),
        rotation=90.0,
        rotation_center=ROTATION_CENTER,
        instance_key="turned",
    )

    _assert_board_oracle_agrees(stepped, tmp_path)


# ---------------------------------------------------------------------------
# Zone fill flattening (iteration 14, work order P6)
# ---------------------------------------------------------------------------
#
# The oracle for a flattened fill is the PLOT, because the question is whether
# the copper a fabricator receives is the same copper. A gerber names three
# things per filled region: the layer it is on, its vertices, and its aperture
# FUNCTION, which is Conductor for net-carrying copper and NonConductor for
# copper with no net. All three must match the zone the fill came from.
#
# The control keeps the net on the island record, which rule four forbids. It
# must plot the island as Conductor, and it does.
#
# One difference is NOT reproducible and it is measured rather than hidden. See
# the docstring on test_a_flattened_island_can_inherit_a_stale_net_attribute.

APERTURE_FUNCTION = re.compile(r"^%TA\.AperFunction,([^*]+)\*%$")
GERBER_NET = re.compile(r"^%TO\.N,([^*]*)\*%$")
REGION_COORD = re.compile(r"^X(-?\d+)Y(-?\d+)D0[12]\*$")

FILL_NET = "GND"


def _filled_zone() -> Zone:
    """A zone with one solid fill record and one ISLAND record."""
    return Zone(
        net_name=FILL_NET,
        layer="F.Cu",
        id="20000000-0000-0000-0000-000000000001",
        fill=ZoneFill(filled=True),
        polygons=[
            ZoneOutlinePolygon(pts=[(40.0, 40.0), (80.0, 40.0), (80.0, 80.0), (40.0, 80.0)])
        ],
        filled_polygons=[
            ZoneFilledPolygon(
                layer="F.Cu", pts=[(41.0, 41.0), (79.0, 41.0), (79.0, 79.0), (41.0, 79.0)]
            ),
            ZoneFilledPolygon(
                layer="F.Cu",
                island=True,
                pts=[(90.0, 41.0), (95.0, 41.0), (95.0, 46.0), (90.0, 46.0)],
            ),
        ],
    )


def _zoned_board() -> Board:
    board = Board.from_file(BOARD_GOLDEN)
    board.zones.append(_filled_zone())
    return board


def _flattened_board(*, keep_island_net: bool = False) -> Board:
    board = Board.from_file(BOARD_GOLDEN)
    graphics = flatten_fills(_filled_zone())
    if keep_island_net:
        graphics[1].net_name = FILL_NET  # the control: rule four broken
    board.drawings.extend(graphics)
    generate_uuids(board)
    return board


def _plotted_regions(board: Board, tmp_path: Path, name: str) -> set:
    """Return one entry per filled region KiCad plots on F.Cu.

    An entry is (aperture function, net attribute, vertex set). The vertices are
    a SET because KiCad chooses the start vertex and the winding of a closed
    region itself, and neither changes the copper.
    """
    source = tmp_path / name / "board.kicad_pcb"
    source.parent.mkdir(parents=True, exist_ok=True)
    board.to_file(source, line_ending="native")
    output = tmp_path / name / "gerber"

    _run_oracle(
        [
            "pcb",
            "export",
            "gerbers",
            "--layers",
            "F.Cu",
            "--no-protel-ext",
            "-o",
            str(output),
            str(source),
        ],
        tmp_path,
    )

    found: set = set()
    function: str | None = None
    net: str | None = None
    inside = False
    points: set = set()
    for plotted in sorted(output.glob("*.gbr")):
        for line in plotted.read_text(encoding="utf-8", errors="replace").splitlines():
            if match := APERTURE_FUNCTION.match(line):
                function = match.group(1)
            elif match := GERBER_NET.match(line):
                net = match.group(1)
            elif line == "%TD*%":
                net = None
            elif line == "G36*":
                inside, points = True, set()
            elif line == "G37*":
                found.add((function, net, frozenset(points)))
                inside = False
            elif inside and (match := REGION_COORD.match(line)):
                points.add((int(match.group(1)), int(match.group(2))))
    assert found, "the copper plot carried no filled region"
    return found


def _copper(regions: set) -> set:
    """The part of a region a fabricator etches: its function and its vertices."""
    return {(function, points) for function, _net, points in regions}


@requires_kicad_cli
def test_a_flattened_fill_plots_the_same_copper_as_the_zone(tmp_path: Path) -> None:
    """Rules one to five, all at once, against KiCad's own plot.

    Same count of regions, same vertices, and the same Conductor and
    NonConductor attribution. A wrong stroke width, a missing fill, a merged
    outline or a mis-set net would all move one of those.
    """
    from_zone = _plotted_regions(_zoned_board(), tmp_path, "zoned")
    from_flatten = _plotted_regions(_flattened_board(), tmp_path, "flattened")

    assert len(from_flatten) == len(from_zone) == 2
    assert _copper(from_flatten) == _copper(from_zone)


@requires_kicad_cli
def test_an_island_that_keeps_its_net_plots_as_conductor(tmp_path: Path) -> None:
    """The control. Rule four is the one rule a reader is most likely to drop."""
    from_zone = _plotted_regions(_zoned_board(), tmp_path, "zoned")
    control = _plotted_regions(_flattened_board(keep_island_net=True), tmp_path, "island-with-net")

    assert _copper(control) != _copper(from_zone)
    assert {function for function, _points in _copper(control)} == {"Conductor"}
    assert {function for function, _points in _copper(from_zone)} == {
        "Conductor",
        "NonConductor",
    }


@requires_kicad_cli
def test_a_flattened_island_can_inherit_a_stale_net_attribute(tmp_path: Path) -> None:
    """A measured limitation, recorded here so it cannot be discovered as a bug.

    The COPPER is identical and the aperture function is identical. The X2 net
    attribute is not always. A gerber object attribute lives until something
    clears it, and only two things do: a new net attribute, or ``%TD*%``.

    The zone path emits ``%TD*%`` between the main area and the islands, because
    ``BRDITEMS_PLOTTER::PlotZone`` runs inside its own ``StartBlock`` and
    ``EndBlock`` (plot_brditems_plotter.cpp:882,908; GERBER_plotter.cpp:192-203).

    A board-level copper GRAPHIC with no net sets only the aperture attribute and
    leaves the net attribute type UNSPECIFIED
    (plot_brditems_plotter.cpp:980-985), and ``FormatNetAttribute`` returns early
    on UNSPECIFIED without clearing anything (gbr_metadata.cpp:584-585). So a
    net-less graphic plotted after a net-carrying one inherits the earlier
    ``%TO.N``.

    This is a property of plotting net-less copper graphics on ANY board, not of
    this operation. It is not reachable from a field on the graphic. The
    fabrication consequence is metadata only: an island reads as belonging to the
    zone's net in a netlist check, where the zone's own island reads as
    unattributed.
    """
    from_zone = _plotted_regions(_zoned_board(), tmp_path, "zoned")
    from_flatten = _plotted_regions(_flattened_board(), tmp_path, "flattened")

    island_from_zone = next(net for function, net, _pts in from_zone if function == "NonConductor")
    island_from_flatten = next(
        net for function, net, _pts in from_flatten if function == "NonConductor"
    )

    assert island_from_zone is None
    assert island_from_flatten == FILL_NET
    assert from_flatten != from_zone
    assert _copper(from_flatten) == _copper(from_zone)


@requires_kicad_cli
def test_a_flattened_board_matches_the_resave_oracle(tmp_path: Path) -> None:
    _assert_board_oracle_agrees(_flattened_board(), tmp_path)


def test_a_transplanted_fill_keeps_the_geometry_byte_for_byte() -> None:
    """The oracle-free twin for the transplant."""
    donor = _zoned_board()
    host = Board.from_file(BOARD_GOLDEN)
    host.zones.append(
        Zone(
            net_name=FILL_NET,
            layer="F.Cu",
            id="20000000-0000-0000-0000-000000000002",
            fill=ZoneFill(filled=True),
            polygons=donor.zones[0].polygons,
        )
    )

    replace_fills(host.zones[0], extract_fills(donor.zones[0]))

    assert host.zones[0].filled_polygons == donor.zones[0].filled_polygons
    text = host.to_string()
    assert Board.from_string(text).to_string() == text
