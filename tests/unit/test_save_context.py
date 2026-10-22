"""Tests for the SaveContext enum and its writer activation (T18 / T7).

The default (LIBRARY) preserves byte-identity. BOARD context is active (T7): it
omits the version/generator block and emits board-only tokens. SCHEMATIC is not
valid for a footprint and raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eedesigncore import SaveContext as SaveContextTop
from eedesigncore.common import SaveContext
from eedesigncore.mod.models import Footprint
from eedesigncore.mod.writer import dump_mod, write_mod

FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "mod"
    / "CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod"
)


def test_savecontext_members() -> None:
    assert {c.name for c in SaveContext} == {"LIBRARY", "BOARD", "SCHEMATIC"}
    assert SaveContext.LIBRARY.value == "library"
    assert SaveContext.BOARD.value == "board"
    assert SaveContext.SCHEMATIC.value == "schematic"


def test_savecontext_exported_both_surfaces() -> None:
    # Same object from common and top-level re-export.
    assert SaveContextTop is SaveContext


def test_default_context_is_library_and_unchanged() -> None:
    fp = Footprint.from_file(FIXTURE)
    assert dump_mod(fp) == dump_mod(fp, context=SaveContext.LIBRARY)
    assert write_mod(fp) == write_mod(fp, context=SaveContext.LIBRARY)


def test_default_round_trip_preserves_every_line() -> None:
    """LIBRARY context re-emits every line of the fixture, in canonical order.

    The fixture stores its ``F.CrtYd`` lines after its ``F.Fab`` lines, which is
    not the layer order KiCad 10 sorts to (``pcbnew/footprint.cpp:4321-4322``).
    ``kicad-cli fp upgrade --force`` moves that block too, so byte identity
    against the stored order is the wrong bar here. Line content is unchanged and
    the output is a fixed point. See ADR-058.
    """
    fp = Footprint.from_file(FIXTURE)
    original = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")
    written = write_mod(fp)

    assert sorted(written.splitlines()) == sorted(original.splitlines())
    assert write_mod(Footprint.from_string(written)) == written


def test_schematic_context_raises_for_footprint() -> None:
    fp = Footprint.from_file(FIXTURE)
    with pytest.raises(NotImplementedError):
        dump_mod(fp, context=SaveContext.SCHEMATIC)
    with pytest.raises(NotImplementedError):
        write_mod(fp, context=SaveContext.SCHEMATIC)


def test_board_context_omits_version_block() -> None:
    fp = Footprint.from_file(FIXTURE)
    out = dump_mod(fp, context=SaveContext.BOARD)
    assert "(version " not in out
    assert "(generator " not in out


def test_board_context_library_file_still_bare_name() -> None:
    # A library fixture stores a bare fp_id; BOARD context emits it verbatim.
    fp = Footprint.from_file(FIXTURE)
    out = dump_mod(fp, context=SaveContext.BOARD)
    assert out.startswith(f"(footprint {fp.fp_id!r}".replace("'", '"'))
