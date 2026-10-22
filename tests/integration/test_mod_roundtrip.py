"""Integration tests: byte-identical round-trip for all 5 .kicad_mod fixtures.

Per ADR-004: write(parse(file)) must produce byte-identical output (post LF
normalisation per ADR-006).

ADR-008: kicad-footprint-generator pts formatting
-------------------------------------------------
Files written by kicad-footprint-generator (generator != "pcbnew") format
``(pts (xy x y) ...)`` blocks with one xy per line, using their own Python
S-expression formatter rather than KiCad's PRETTIFIED_FILE_OUTPUTFORMATTER.
KiCad's prettify algorithm (and our port) consolidates multiple ``(xy ...)``
tokens onto one line when within the column limit (99 chars).

When our library parses a generator-script file and re-emits it through
our prettify port, the pts formatting normalises to the pcbnew/prettify style.
This is the same transformation pcbnew itself would apply on first save.
Per ADR-004, byte-identity is required only for "any file KiCad 10.0.x itself
wrote". Generator-script files do not qualify; only fixture 5 (pcbnew-saved)
strictly requires byte-identity.

For generator-script fixtures, the test normalises pts block formatting
by collapsing whitespace within pts blocks before comparison.

One pcbnew-saved fixture is exempt from byte identity. KiCad 10 sorts a
footprint's drawings by type and then by LAYER ID on every save
(``pcbnew/footprint.cpp:4316-4322``), and this fixture stores its ``F.CrtYd``
lines after its ``F.Fab`` lines, which inverts the layer ordinals 31 and 35. Run
``kicad-cli fp upgrade --force`` on it and KiCad 10.0.3 moves the block itself,
to exactly the position our writer emits. That resave is reproduced in
``tests/integration/test_mutation_fidelity.py``. Here the fixture asserts the
fixed-point property instead: writing our output a second time changes nothing.
See ADR-058.
"""

import re
from pathlib import Path

import pytest

from eedesigncore.mod.models import Footprint

FIXTURES = list(Path("tests/fixtures/mod").glob("*.kicad_mod"))

# Fixtures whose stored drawing order predates the KiCad 10 canonical save order.
PRE_CANONICAL = {"CAP_DISK_5.9MM_TH_Cornell_Dubilier.kicad_mod"}


def _normalise_pts(text: str) -> str:
    """Normalise (pts (xy ...) ...) blocks to single-line format.

    Collapses whitespace-separated (xy ...) tokens inside (pts ...) blocks
    so that both one-per-line and same-line formats compare equal.
    """
    # Match (pts ...) blocks and normalise internal whitespace
    # This regex finds (pts followed by content up to the matching closing paren
    result = []
    i = 0
    while i < len(text):
        # Look for (pts
        if text[i : i + 4] == "(pts":
            # Find the closing paren of this pts block (simple depth tracking)
            depth = 0
            start = i
            j = i
            while j < len(text):
                if text[j] == "(" and (j == 0 or text[j - 1] != "\\"):
                    depth += 1
                elif text[j] == ")" and (j == 0 or text[j - 1] != "\\"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            pts_block = text[start : j + 1]
            # Normalise: collapse whitespace between (xy ...) tokens
            normalised = re.sub(r"\s+", " ", pts_block.strip())
            result.append(normalised)
            i = j + 1
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def _is_pcbnew_file(path: Path) -> bool:
    """Return True if this file was written by pcbnew (not a generator script)."""
    content = path.read_text(encoding="utf-8", errors="replace")
    return '(generator "pcbnew")' in content


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_roundtrip(path: Path) -> None:
    """Parse and re-emit each fixture; compare byte-for-byte after LF normalisation.

    For pcbnew-saved fixtures (generator "pcbnew"): strict byte-identical check.
    For generator-script fixtures: pts blocks are normalised before comparison
    (see ADR-008 in module docstring).
    """
    original = path.read_bytes()
    fp = Footprint.from_file(path)
    result = fp.to_string().encode("utf-8")

    # Normalise line endings (ADR-006)
    orig_norm = original.replace(b"\r\n", b"\n").decode("utf-8", errors="replace")
    res_norm = result.replace(b"\r\n", b"\n").decode("utf-8", errors="replace")

    if path.name in PRE_CANONICAL:
        assert Footprint.from_string(res_norm).to_string() == res_norm
        assert sorted(res_norm.splitlines()) == sorted(orig_norm.splitlines())
        return

    pcbnew_file = _is_pcbnew_file(path)

    if not pcbnew_file:
        # Generator-script files: normalise pts block formatting (ADR-008)
        orig_cmp = _normalise_pts(orig_norm)
        res_cmp = _normalise_pts(res_norm)
    else:
        orig_cmp = orig_norm
        res_cmp = res_norm

    if orig_cmp != res_cmp:
        orig_lines = orig_cmp.splitlines(keepends=True)
        res_lines = res_cmp.splitlines(keepends=True)
        for i, (a, b) in enumerate(zip(orig_lines, res_lines, strict=False), 1):
            if a != b:
                pytest.fail(f"First diff at line {i}:\n  original: {a!r}\n  result:   {b!r}")
        pytest.fail(f"Length differs: original={len(orig_lines)} result={len(res_lines)} lines")
