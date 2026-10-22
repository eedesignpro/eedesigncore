"""The agent documentation set is enforced, not reviewed (R4, iteration 11.7).

`docs/agent/` is written for AI agents and for people, so three failure modes
matter more than prose quality: a pointer that no longer resolves, a public
name the hub never mentions, and a file that grew past the context budget an
agent can afford to spend on it.

The internal checkout also holds an agent start file that names the hub and
every spoke. The public copy does not carry it. ``tests/private_inputs.py``
names it, and the checks on it skip where it is absent.

Every pointer in these documents has the form ``path`` or ``path § Heading``.
No line numbers, because they go stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import eedesigncore
from tests.private_inputs import private_text

REPO = Path(__file__).parent.parent.parent
# None in the public copy, which does not carry the agent start file.
AGENTS = private_text("agent_start_file")
HUB = "docs/agent/README.md"
SPOKES = [
    "docs/agent/common.md",
    "docs/agent/symbols.md",
    "docs/agent/footprints.md",
    "docs/agent/boards.md",
    "docs/agent/schematics.md",
    "docs/agent/projects.md",
]
AGENT_DOCUMENTS = [HUB, *SPOKES] if AGENTS is None else [AGENTS, HUB, *SPOKES]

# Byte budgets stand in for token budgets. English prose runs about 4 bytes per
# token, so 4096 bytes is roughly 1k tokens, 8192 roughly 2k, and 6144 roughly
# 1.5k. Bytes are exact and need no tokenizer. The size is measured over
# line-feed text, so a CRLF checkout on Windows cannot change the verdict.
# Operator ruling 2026-08-19: the hub budget is a guideline for density, not a
# ceiling. 12288 bytes is about 3k tokens, and it pays for the public-name
# census the closure of the root __all__ made large (ADR-085).
# Operator ruling 2026-09-10: raised from 12288 hub and 6144 spoke. Both were
# full to within a handful of bytes, so the next public export could not be
# documented without evicting something a reader needs. The budgets still
# bind: they stop a spoke's detail migrating into the hub.
BUDGET = {HUB: 16384, **{spoke: 8192 for spoke in SPOKES}}
if AGENTS is not None:
    BUDGET[AGENTS] = 4096

# Operator ruling 2026-08-19: the density guideline still binds the part a
# reader REASONS from. The census is reference data an agent scans on demand,
# so the two budgets split. This one stops spoke content migrating into the hub.
HUB_PROSE_BUDGET = 10240

CENSUS_HEADING = "## Every public name"

# A row of the Not implemented table starts its status with one of these.
STATUS_WORDS = ("Not implemented", "Partial", "Not planned")

# A backticked token that names a file in this repository. Example filenames the
# prose opens by name (Device.kicad_sym) carry an extension outside this set.
REFERENCE = re.compile(r"`([^`\n]+?\.(?:md|py|toml|txt|json|cfg|ini))(?: § ([^`\n]+))?`")

CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
HEADING = re.compile(r"^#+\s+(.*?)\s*$", re.MULTILINE)


def _text(document: str) -> str:
    return (REPO / document).read_text(encoding="utf-8")


def _prose(document: str) -> str:
    """The document with every fenced block removed."""
    return CODE_FENCE.sub("", _text(document))


def _references() -> list[tuple[str, str, str]]:
    """Every (document, path, heading) pointer. The heading may be empty."""
    found: list[tuple[str, str, str]] = []
    for document in AGENT_DOCUMENTS:
        for match in REFERENCE.finditer(_prose(document)):
            found.append((document, match.group(1), match.group(2) or ""))
    return found


REFERENCES = _references()


@pytest.mark.parametrize("document", AGENT_DOCUMENTS)
def test_an_agent_document_exists(document: str) -> None:
    assert (REPO / document).is_file()


def test_the_hub_lists_every_spoke() -> None:
    hub = _text(HUB)
    for spoke in SPOKES:
        assert spoke in hub


@pytest.mark.skipif(AGENTS is None, reason="the internal agent start file is absent")
def test_agents_md_names_the_hub_and_every_spoke() -> None:
    assert AGENTS is not None
    bootstrap = _text(AGENTS)
    for document in [HUB, *SPOKES]:
        assert document in bootstrap


@pytest.mark.parametrize(
    ("document", "target", "heading"),
    REFERENCES,
    ids=[f"{d}->{t}{'#' + h if h else ''}" for d, t, h in REFERENCES],
)
def test_a_referenced_path_exists(document: str, target: str, heading: str) -> None:
    referenced = REPO / target
    assert referenced.is_file(), f"{document} points at a missing file: {target}"

    if not heading:
        return

    # A pointer names a heading by its leading text, so a heading that carries a
    # dash and a subtitle still resolves from its stable prefix. Markdown accents
    # are stripped, so `extract_footprint` resolves from extract_footprint.
    headings = [
        found.replace("`", "").replace("*", "")
        for found in HEADING.findall(referenced.read_text(encoding="utf-8"))
    ]
    assert any(found.casefold().startswith(heading.casefold()) for found in headings), (
        f"{document} points at a missing heading: {target} § {heading}"
    )


def test_every_reference_carries_no_line_number() -> None:
    for document in AGENT_DOCUMENTS:
        assert not re.search(r"\.md:\d+", _prose(document)), document


@pytest.mark.parametrize("name", sorted(eedesigncore.__all__))
def test_the_hub_names_every_public_export(name: str) -> None:
    assert re.search(rf"\b{re.escape(name)}\b", _text(HUB)), (
        f"{name} is exported from the package root but absent from {HUB}"
    )


@pytest.mark.parametrize("document", AGENT_DOCUMENTS)
def test_an_agent_document_stays_inside_its_budget(document: str) -> None:
    size = len(_text(document).replace("\r\n", "\n").encode("utf-8"))
    assert size <= BUDGET[document], f"{document} is {size} bytes, budget {BUDGET[document]}"


def test_the_hub_prose_stays_inside_the_density_budget() -> None:
    """The whole hub may hold the census. The REASONING half may not grow."""
    text = _text(HUB).replace("\r\n", "\n")
    assert CENSUS_HEADING in text, f"{HUB} lost the census heading"
    kept: list[str] = []
    in_census = False
    for line in text.split("\n"):
        if line.startswith("## "):
            in_census = line.startswith(CENSUS_HEADING)
        if in_census and line.startswith("|"):
            continue
        kept.append(line)
    size = len("\n".join(kept).encode("utf-8"))
    assert size <= HUB_PROSE_BUDGET, (
        f"{HUB} minus the census table is {size} bytes, budget {HUB_PROSE_BUDGET}. "
        "Reference data belongs in the table and detail belongs in a spoke."
    )


def test_the_census_is_a_table_and_not_a_fenced_block() -> None:
    """`test_docs_examples.py` executes fenced python. A fenced census would run."""
    text = _text(HUB).replace("\r\n", "\n")
    census = text.split(CENSUS_HEADING, 1)[1].split("\n## ", 1)[0]
    assert "```" not in census
    assert census.count("\n|") >= 10


@pytest.mark.parametrize("document", SPOKES)
def test_a_spoke_opens_with_a_condition_and_closes_with_its_gaps(document: str) -> None:
    text = _text(document)
    assert "## Read this when" in text
    if document != "docs/agent/common.md":
        assert text.rstrip().rfind("## Gaps in this scope") > text.find("## Model tree")
    assert "## Gaps in this scope" in text


def test_the_hub_carries_the_support_boundaries() -> None:
    hub = _text(HUB)
    assert "## Not implemented" in hub
    assert "## Do not" in hub
    assert "`py.typed`" in hub
    assert "__all__" in hub


def test_every_not_implemented_row_names_a_status() -> None:
    """A row states the status in words: not implemented, partial, or not planned."""
    hub = _text(HUB)
    table = hub.split("## Not implemented")[1].split("## Do not")[0]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) >= 10
    for row in rows[1:]:
        status = row.rsplit("|", 2)[1]
        assert any(word in status for word in STATUS_WORDS), row
