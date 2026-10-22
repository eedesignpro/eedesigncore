"""The consumer-facing surface is enforced, not reviewed (iteration 11.9).

`pyproject.toml` declares ``readme = "README.md"``, so the build bakes that text
into the wheel METADATA as the long description. It is the registry landing
page, and a consumer copies the install command straight out of it. A fix after
the build costs a version bump, because METADATA is frozen at build time.

v0.10.0 shipped a BREAKING change with a README that still pinned 0.9.0 and
carried no link to `CHANGELOG.md`. `docs/agent/` stayed current through the same
iteration, because `tests/integration/test_agent_docs.py` guards it. This module
gives the consumer surface the same kind of guard.

Five things are checked mechanically: every pinned install command names the
release in `VERSION`, `[project.urls]` is declared and its repository targets
resolve, `README.md` reaches `CHANGELOG.md`, `CHANGELOG.md` opens with a section
for the current `VERSION`, and the built wheel carries the intended manifest.

Nothing here hard-codes a version number. The tests read `VERSION`, so they keep
working next release.
"""

from __future__ import annotations

import re
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).parent.parent.parent

# Consumer-facing documents that carry a copy-paste install command.
PINNED_DOCUMENTS = ["README.md"]

# The registry page is useless without these three.
REQUIRED_URLS = ["Homepage", "Changelog", "Documentation"]

# The distribution name in a pip command, with the version it pins.
PIN = re.compile(r"eedesigncore\s*==\s*([0-9][^\"'\s]*)")

# A GitHub blob or tree link into this repository. The capture is the path
# relative to the repository root.
REPO_PATH_URL = re.compile(r"/(?:blob|tree)/[^/]+/(.+)$")

CHANGELOG_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

# Where the support set lands inside the wheel. The layout under the package
# mirrors the repository root, so a relative pointer inside a document still
# resolves for a consumer who has only the installed package (ADR-079).
WHEEL_PACKAGE = "eedesigncore"
SHIPPED_ROOT_FILES = ["README.md", "CHANGELOG.md"]

# hatchling below 1.32 emits this. twine 6.1.0 rejects 2.5, which is what broke
# the v0.9.1 publish. The bound that keeps it here is guarded in
# tests/unit/test_package_metadata.py, section TestBuildRequirement.
METADATA_VERSION = "2.4"

try:  # pragma: no cover - import guard, not a branch under test
    from hatchling.builders.wheel import WheelBuilder
except ImportError:  # pragma: no cover - only when the dev extra is not installed
    WheelBuilder = None  # type: ignore[assignment,misc]

requires_hatchling = pytest.mark.skipif(
    WheelBuilder is None,
    reason='hatchling is missing; run pip install -e ".[dev]" -c constraints.txt',
)


def _text(document: str) -> str:
    return (REPO / document).read_text(encoding="utf-8")


def _version() -> str:
    return (REPO / "VERSION").read_text(encoding="utf-8").strip()


def _pyproject() -> dict[str, Any]:
    with (REPO / "pyproject.toml").open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
    return data


def _project_urls() -> dict[str, str]:
    urls: dict[str, str] = _pyproject()["project"].get("urls", {})
    return urls


def _pins() -> list[tuple[str, str]]:
    """Every (document, pinned version) install command in the consumer set."""
    found: list[tuple[str, str]] = []
    for document in PINNED_DOCUMENTS:
        for match in PIN.finditer(_text(document)):
            found.append((document, match.group(1)))
    return found


def _repo_path_urls() -> list[tuple[str, str]]:
    """Every (url key, repository-relative path) a declared URL names."""
    found: list[tuple[str, str]] = []
    for key, url in sorted(_project_urls().items()):
        match = REPO_PATH_URL.search(url)
        if match is not None:
            found.append((key, match.group(1)))
    return found


def _shipped_docs() -> list[str]:
    """Every markdown file under `docs/`, as a repository-relative posix path."""
    return sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in REPO.glob("docs/**/*.md"))


PINS = _pins()
REPO_PATH_URLS = _repo_path_urls()
SHIPPED_DOCS = _shipped_docs()


class TestPinnedInstallCommands:
    def test_every_consumer_document_carries_a_pinned_install_command(self) -> None:
        """A document that drops its pin would otherwise pass the next test vacuously."""
        assert {document for document, _ in PINS} == set(PINNED_DOCUMENTS)

    @pytest.mark.parametrize(("document", "pinned"), PINS, ids=[f"{d}=={v}" for d, v in PINS])
    def test_a_pinned_install_command_names_this_release(self, document: str, pinned: str) -> None:
        assert pinned == _version(), (
            f"{document} pins {pinned}, but VERSION reads {_version()}. "
            "README.md ships as the package long description."
        )


class TestProjectUrls:
    def test_project_urls_is_declared(self) -> None:
        assert _project_urls(), "[project.urls] carries the registry page's only links out"

    @pytest.mark.parametrize("key", REQUIRED_URLS)
    def test_a_required_url_is_declared_and_absolute(self, key: str) -> None:
        urls = _project_urls()
        assert key in urls, f"[project.urls] is missing {key}"
        assert urls[key].startswith("https://"), f"{key} must be an absolute https URL"

    def test_at_least_one_url_points_into_this_repository(self) -> None:
        assert len(REPO_PATH_URLS) >= 2, (
            "Changelog and Documentation both name a path in this repository"
        )

    @pytest.mark.parametrize(
        ("key", "target"), REPO_PATH_URLS, ids=[key for key, _ in REPO_PATH_URLS]
    )
    def test_a_url_that_names_a_repository_path_resolves(self, key: str, target: str) -> None:
        assert (REPO / target).exists(), f"[project.urls] {key} points at a missing path: {target}"

    def test_the_changelog_url_names_the_changelog(self) -> None:
        assert _project_urls()["Changelog"].endswith("/CHANGELOG.md")


class TestChangelogReachability:
    def test_the_readme_links_to_the_changelog(self) -> None:
        assert "CHANGELOG.md" in _text("README.md"), (
            "a consumer landing on the registry has no other path to the release notes"
        )

    def test_the_readme_carries_an_upgrade_heading(self) -> None:
        assert re.search(r"^#{2,3} Upgrading\s*$", _text("README.md"), re.MULTILINE)

    def test_the_changelog_has_a_section_for_the_current_version(self) -> None:
        assert _version() in CHANGELOG_HEADING.findall(_text("CHANGELOG.md"))

    def test_the_newest_changelog_section_is_the_current_version(self) -> None:
        """A release that forgets its entry leaves the previous version on top."""
        assert CHANGELOG_HEADING.findall(_text("CHANGELOG.md"))[0] == _version()


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A freshly built wheel, kept for the whole session.

    The build runs through the hatchling backend in this interpreter, so it
    needs no network, no isolated environment, and no subprocess. It costs
    roughly a quarter second, which is why the normal suite can afford it
    (ADR-080).
    """
    assert WheelBuilder is not None
    directory = tmp_path_factory.mktemp("wheel")
    artifacts = list(WheelBuilder(str(REPO)).build(directory=str(directory)))
    assert len(artifacts) == 1, artifacts
    return Path(artifacts[0])


@pytest.fixture(scope="session")
def wheel_manifest(built_wheel: Path) -> list[str]:
    """Names inside the built wheel."""
    with zipfile.ZipFile(built_wheel) as archive:
        return archive.namelist()


@pytest.fixture(scope="session")
def wheel_metadata(built_wheel: Path) -> str:
    """The `METADATA` text of the built wheel, which the registry reads."""
    with zipfile.ZipFile(built_wheel) as archive:
        return archive.read(f"{WHEEL_PACKAGE}-{_version()}.dist-info/METADATA").decode("utf-8")


@requires_hatchling
class TestWheelManifest:
    def test_the_wheel_has_exactly_the_intended_top_level_entries(
        self, wheel_manifest: list[str]
    ) -> None:
        """The support set ships inside the package, so site-packages gains nothing new."""
        top_level = {name.split("/")[0] for name in wheel_manifest}
        assert top_level == {WHEEL_PACKAGE, f"{WHEEL_PACKAGE}-{_version()}.dist-info"}

    def test_the_wheel_still_carries_the_library(self, wheel_manifest: list[str]) -> None:
        for name in [f"{WHEEL_PACKAGE}/__init__.py", f"{WHEEL_PACKAGE}/py.typed"]:
            assert name in wheel_manifest

    @pytest.mark.parametrize("document", SHIPPED_DOCS)
    def test_every_repository_document_ships_in_the_wheel(
        self, document: str, wheel_manifest: list[str]
    ) -> None:
        """Derived from the tree, so a new spoke is covered the moment it is added."""
        assert f"{WHEEL_PACKAGE}/{document}" in wheel_manifest

    @pytest.mark.parametrize("document", SHIPPED_ROOT_FILES)
    def test_every_shipped_root_document_is_in_the_wheel(
        self, document: str, wheel_manifest: list[str]
    ) -> None:
        assert f"{WHEEL_PACKAGE}/{document}" in wheel_manifest

    def test_the_wheel_carries_child_sheet_seed(self, wheel_manifest: list[str]) -> None:
        assert f"{WHEEL_PACKAGE}/sch/templates/child_sheet.kicad_sch" in wheel_manifest

    def test_the_wheel_carries_no_repository_state(self, wheel_manifest: list[str]) -> None:
        """Tests, fixtures, SDLC state, and the venv stay out of the distribution."""
        for name in wheel_manifest:
            assert not name.startswith(f"{WHEEL_PACKAGE}/tests/"), name
            assert not name.startswith(f"{WHEEL_PACKAGE}/context/"), name
            assert "/.venv/" not in name, name

    def test_the_wheel_version_matches_the_version_file(self, wheel_manifest: list[str]) -> None:
        metadata = f"{WHEEL_PACKAGE}-{_version()}.dist-info/METADATA"
        assert metadata in wheel_manifest

    def test_the_metadata_version_is_the_one_twine_accepts(self, wheel_metadata: str) -> None:
        """hatchling 1.32.0 emits 2.5, which twine 6.1.0 rejects. That broke the
        v0.9.1 publish, and the manifest test alone never opened the file
        (MEDIUM-7)."""
        assert wheel_metadata.splitlines()[0] == f"Metadata-Version: {METADATA_VERSION}"

    def test_the_metadata_carries_the_readme_as_the_long_description(
        self, wheel_metadata: str
    ) -> None:
        """`readme = "README.md"` bakes the landing page in at build time."""
        assert "Description-Content-Type: text/markdown" in wheel_metadata
        assert _text("README.md").splitlines()[0] in wheel_metadata

