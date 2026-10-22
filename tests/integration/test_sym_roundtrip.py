"""Round-trip byte-identity integration tests for .kicad_sym files.

Line endings are normalised to LF before comparison because KiCad on Windows
writes CRLF (text-mode fwrite), while our writer produces LF.  The content
must otherwise be byte-for-byte identical.

Every fixture asserts full byte identity. Four of them, ``74xx``, ``Connector``,
``Device`` and ``MCU_Microchip_PIC16``, were REGENERATED on 2026-08-13 with
``kicad-cli sym upgrade --force``, because their stored library symbol order
predated the KiCad 10 canonical save order
(``sch_io_kicad_sexpr_lib_cache.cpp:249-262``). They are now genuine KiCad 10.0.3
output, so the exemption of ADR-055 is gone (operator ruling B, ADR-070).
"""

from pathlib import Path

import pytest

from eedesigncore.sym.models import SymbolLib

FIXTURES = list(Path("tests/fixtures/sym").glob("*.kicad_sym"))


def _normalise(data: bytes) -> str:
    """Decode as UTF-8 and normalise CRLF → LF."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_roundtrip(path: Path) -> None:
    original = _normalise(path.read_bytes())
    lib = SymbolLib.from_file(path)
    result = lib.to_string()

    if original != result:
        orig_lines = original.splitlines(keepends=True)
        res_lines = result.splitlines(keepends=True)

        for i, (a, b) in enumerate(zip(orig_lines, res_lines, strict=False), 1):
            if a != b:
                pytest.fail(f"First diff at line {i}:\n  original: {a!r}\n  result:   {b!r}")

        pytest.fail(
            f"Files differ in length: original={len(orig_lines)} result={len(res_lines)} lines"
        )
