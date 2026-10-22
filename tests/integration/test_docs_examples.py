"""Every fenced Python example in the documentation must run (T10, R11).

The consumer feedback that drove T10 named stale documentation as the problem,
so the examples are executed rather than reviewed. Each block runs in its own
temporary directory, pre-populated with the files the documentation names.

Two machine inputs are needed, and the test skips when either is absent:

- the stock KiCad 10 library share, for ``Device.kicad_sym`` and one footprint,
- a private corpus project, for a board with footprints and a multi-sheet
  hierarchy. ``tests/private_inputs.py`` names it.

The test also skips without the placed-symbol schematic fixture, which the
public copy does not carry. The internal agent start file is checked only
where it is present.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from tests.private_inputs import private_path, private_text

REPO = Path(__file__).parent.parent.parent
FIXTURES = REPO / "tests" / "fixtures"
KICAD_SHARE = Path("C:/Program Files/KiCad/10.0/share/kicad")
CORPUS_PROJECT = private_path("docs_corpus_project")
CORPUS_STEM = private_text("docs_corpus_stem") or "docs-corpus-stem-absent"
TEMPLATE_PROJECT = private_path("template_project", ".kicad_pro")
SCHEMATIC_FIXTURE = FIXTURES / "sch" / "placed_symbols.kicad_sch"
# None in the public copy, which does not carry the agent start file.
AGENT_START_FILE = private_text("agent_start_file")

DEVICE_LIB = KICAD_SHARE / "symbols" / "Device.kicad_sym"
STOCK_FOOTPRINT = (
    KICAD_SHARE / "footprints" / "Capacitor_SMD.pretty" / "C_0402_1005Metric.kicad_mod"
)
CORPUS_BOARD = CORPUS_PROJECT / f"{CORPUS_STEM}.kicad_pcb"

PROSE_DOCUMENTS = ["README.md", "docs/operations.md"]
AGENT_DOCUMENTS = [
    *([] if AGENT_START_FILE is None else [AGENT_START_FILE]),
    "docs/agent/README.md",
    "docs/agent/common.md",
    "docs/agent/symbols.md",
    "docs/agent/footprints.md",
    "docs/agent/boards.md",
    "docs/agent/schematics.md",
    "docs/agent/projects.md",
]
DOCUMENTS = [*PROSE_DOCUMENTS, *AGENT_DOCUMENTS]
BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

requires_inputs = pytest.mark.skipif(
    not DEVICE_LIB.is_file()
    or not STOCK_FOOTPRINT.is_file()
    or not CORPUS_BOARD.is_file()
    or not SCHEMATIC_FIXTURE.is_file(),
    reason="the stock KiCad library share, the private corpus project, "
    "or the placed-symbol schematic fixture is absent",
)


def _examples() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for name in DOCUMENTS:
        text = (REPO / name).read_text(encoding="utf-8")
        for index, match in enumerate(BLOCK.finditer(text), 1):
            found.append((name, index, match.group(1)))
    return found


EXAMPLES = _examples()


def _populate(work: Path) -> None:
    """Create every file the documentation examples open by name."""
    shutil.copy(DEVICE_LIB, work / "Device.kicad_sym")
    shutil.copy(STOCK_FOOTPRINT, work / "C_0402_1005Metric.kicad_mod")
    shutil.copy(CORPUS_BOARD, work / "board.kicad_pcb")
    shutil.copy(CORPUS_BOARD, work / "target.kicad_pcb")
    shutil.copy(TEMPLATE_PROJECT, work / "board.kicad_pro")
    shutil.copy(SCHEMATIC_FIXTURE, work / "schematic.kicad_sch")

    shutil.copy(CORPUS_PROJECT / f"{CORPUS_STEM}.kicad_pro", work / "project.kicad_pro")
    shutil.copy(CORPUS_PROJECT / f"{CORPUS_STEM}.kicad_sch", work / "project.kicad_sch")
    for child in CORPUS_PROJECT.glob("*.kicad_sch"):
        if child.stem != CORPUS_STEM:
            shutil.copy(child, work / child.name)
    (work / "shared_sheets").mkdir()


@requires_inputs
@pytest.mark.parametrize(
    ("document", "index", "source"), EXAMPLES, ids=[f"{d}#{i}" for d, i, _ in EXAMPLES]
)
def test_a_documentation_example_runs(
    document: str, index: int, source: str, tmp_path: Path
) -> None:
    _populate(tmp_path)
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        exec(compile(source, f"{document}#{index}", "exec"), {"__name__": "__main__"})
    finally:
        os.chdir(previous)


def test_both_prose_documents_carry_examples() -> None:
    counted = {document for document, _index, _source in EXAMPLES}
    assert set(PROSE_DOCUMENTS) <= counted
    assert counted <= set(DOCUMENTS)
    assert len(EXAMPLES) >= 20


def test_the_agent_documents_carry_examples() -> None:
    """The agent set keeps fences sparse, so only some documents hold one."""
    counted = {document for document, _index, _source in EXAMPLES}
    assert counted & set(AGENT_DOCUMENTS)
