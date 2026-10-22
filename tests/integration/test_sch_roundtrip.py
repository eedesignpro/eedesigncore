"""Round-trip byte-identity integration tests for .kicad_sch files.

Line endings are normalised to LF before comparison (KiCad on Windows writes
CRLF; our writer produces LF). Content must otherwise be byte-for-byte identical.

The iteration-10 T1/T2 corpus fixtures cover the root skeleton plus placed
symbols / lib_symbols / root sheet_instances; T3a adds wire/bus/junction/
no_connect/bus_entry/label/text/text_box + free graphic-shape exemplars; T3b
adds rule_area / image / table exemplars (no 20260306 group fixture exists in
the corpus). T4 adds real multi-sheet parent + sub-sheet child schematics
(t4_*.kicad_sch) exercising sheet nodes, per-sheet (instances), and the root
(sheet_instances) block -- each round-tripping byte-identically as its own file.

Every fixture asserts full byte identity. ``placed_symbols.kicad_sch`` was
REGENERATED on 2026-08-13 with ``kicad-cli sch upgrade --force``, because its
stored ``lib_symbols`` property order predated the KiCad 10 canonical save order
(``eeschema/sch_field.h:129-132``, ``eeschema/lib_symbol.cpp:1510-1530``). It is
now genuine KiCad 10.0.3 output, so the exemption of ADR-055 is gone (operator
ruling B, ADR-070).
"""

from pathlib import Path

import pytest

from eedesigncore.sch.models import Schematic

FIXTURES = list(Path("tests/fixtures/sch").glob("*.kicad_sch"))


def _normalise(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_roundtrip(path: Path) -> None:
    original = _normalise(path.read_bytes())
    sch = Schematic.from_file(path)
    result = sch.to_string()

    if original != result:
        orig_lines = original.splitlines(keepends=True)
        res_lines = result.splitlines(keepends=True)
        for i, (a, b) in enumerate(zip(orig_lines, res_lines, strict=False), 1):
            if a != b:
                pytest.fail(f"First diff at line {i}:\n  original: {a!r}\n  result:   {b!r}")
        pytest.fail(
            f"Files differ in length: original={len(orig_lines)} result={len(res_lines)} lines"
        )
