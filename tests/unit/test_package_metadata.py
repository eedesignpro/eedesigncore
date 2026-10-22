"""Tests for the installed package metadata (P3, R15).

Hatchling reads the distribution version from ``src/eedesigncore/_version.py``,
which is also what ``eedesigncore.__version__`` re-exports. One file feeds both,
so the metadata and the imported module cannot disagree (UF-012).

The root ``VERSION`` file stays as the mirror the release tooling reads. It is no
longer an authority, and :meth:`TestVersionMetadata.test_the_version_mirror_has_not_drifted`
fails if the two ever separate.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

import pytest

import eedesigncore
import eedesigncore._version

PROJECT_ROOT = Path(__file__).parent.parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
CONSTRAINTS_FILE = PROJECT_ROOT / "constraints.txt"


def _version_file_text() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


class TestVersionMetadata:
    def test_version_file_exists_and_is_a_release_number(self) -> None:
        text = _version_file_text()
        parts = text.split(".")
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)

    def test_the_package_exports_its_own_version(self) -> None:
        """UF-012: a consumer must be able to assert its pin against the CODE.

        ``importlib.metadata`` reads distribution metadata, which can name a
        different installation than the module actually imported. This attribute
        travels with the module itself.
        """
        assert eedesigncore.__version__ == _version_file_text()
        assert "__version__" in eedesigncore.__all__

    def test_the_version_mirror_has_not_drifted(self) -> None:
        """``_version.py`` is the authority; root ``VERSION`` mirrors it.

        Hatchling reads the former. A release that bumps one and not the other
        would ship metadata that disagrees with the code, which is the fault
        UF-012 reported. This test is what makes that unshippable.
        """
        assert eedesigncore._version.__version__ == _version_file_text()

    def test_installed_version_matches_the_version_file(self) -> None:
        """The metadata must report the release number, not the old 0.0.0 placeholder."""
        try:
            reported = version("eedesigncore")
        except PackageNotFoundError:  # pragma: no cover - only when not installed
            pytest.skip("eedesigncore is not installed in this interpreter")
        assert reported == _version_file_text()
        assert reported != "0.0.0"


class TestDependencyDeclaration:
    """Guard the R13 and R14 install contract, which the operator ruled on."""

    def _requirements(self) -> list[str]:
        try:
            declared = requires("eedesigncore")
        except PackageNotFoundError:  # pragma: no cover - only when not installed
            pytest.skip("eedesigncore is not installed in this interpreter")
        assert declared is not None
        return declared

    def test_pydantic_is_a_range_not_an_exact_pin(self) -> None:
        pydantic = [r for r in self._requirements() if r.startswith("pydantic")]
        assert pydantic, "pydantic must stay a runtime dependency"
        assert all("==" not in r for r in pydantic)

    def test_zstandard_carries_the_python_version_marker(self) -> None:
        """Python 3.14 and later use the standard library backend instead."""
        zstd = [r for r in self._requirements() if r.startswith("zstandard") and "extra" not in r]
        assert zstd
        assert any('python_version < "3.14"' in r.replace("'", '"') for r in zstd)

    def test_constraints_file_pins_both_runtime_dependencies(self) -> None:
        text = CONSTRAINTS_FILE.read_text(encoding="utf-8")
        assert "pydantic==" in text
        assert "zstandard==" in text


class TestBuildRequirement:
    """hatchling 1.32.0 emits Metadata-Version 2.5, which twine 6.1.0 rejects.

    An unbounded build requirement broke the v0.9.1 publish. The bound keeps an
    isolated build on a hatchling that emits Metadata-Version 2.4.

    The bound lives in three files: `[build-system].requires`, the `dev` extra,
    and the `constraints.txt` pin the project venv and CI install with. Every
    assertion here shares the two bound strings below, so one edit moves them
    together (MEDIUM-7, iteration 11.9).
    """

    LOWER = "1.27"
    UPPER = "1.32"

    def _hatchling_requirement(self) -> str:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        requires = data["build-system"]["requires"]
        matches = [r for r in requires if r.replace(" ", "").startswith("hatchling")]
        assert len(matches) == 1, requires
        return matches[0].replace(" ", "")

    def _dev_extra_requirement(self) -> str:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            data = tomllib.load(handle)
        dev = data["project"]["optional-dependencies"]["dev"]
        matches = [r for r in dev if r.replace(" ", "").startswith("hatchling")]
        assert len(matches) == 1, dev
        return matches[0].replace(" ", "")

    def _constraints_pin(self) -> tuple[int, ...]:
        text = CONSTRAINTS_FILE.read_text(encoding="utf-8")
        matches = re.findall(r"^hatchling==([0-9][^\s#]*)", text, re.MULTILINE)
        assert len(matches) == 1, text
        return tuple(int(part) for part in matches[0].split("."))

    @staticmethod
    def _bound(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))

    def test_hatchling_carries_an_upper_bound(self) -> None:
        assert f"<{self.UPPER}" in self._hatchling_requirement()

    def test_hatchling_carries_a_lower_bound(self) -> None:
        assert f">={self.LOWER}" in self._hatchling_requirement()

    def test_the_dev_extra_declares_the_same_bound_as_the_build_system(self) -> None:
        """The venv installs the dev extra, so a drifted copy is a drifted build."""
        assert self._dev_extra_requirement() == self._hatchling_requirement()

    def test_the_constraints_pin_falls_inside_the_declared_bound(self) -> None:
        """CI and the project venv install from constraints.txt, not from the range."""
        pinned = self._constraints_pin()
        assert self._bound(self.LOWER) <= pinned, (
            f"constraints.txt pins hatchling {pinned}, below the declared "
            f"lower bound {self.LOWER}."
        )
        assert pinned < self._bound(self.UPPER), (
            f"constraints.txt pins hatchling {pinned}, which is at or above "
            f"{self.UPPER}. That backend emits Metadata-Version 2.5, which "
            "twine 6.1.0 rejects."
        )


class TestTypeMarker:
    """PEP 561: without this marker a consumer's type checker reads the package
    as untyped, and every model attribute degrades to Any (UF-001)."""

    def test_marker_sits_beside_the_package_init(self) -> None:
        package_dir = Path(eedesigncore.__file__).parent
        assert (package_dir / "__init__.py").is_file()
        assert (package_dir / "py.typed").is_file()

    def test_marker_is_empty(self) -> None:
        package_dir = Path(eedesigncore.__file__).parent
        assert (package_dir / "py.typed").read_bytes() == b""

    def test_marker_is_in_the_source_tree(self) -> None:
        assert (PROJECT_ROOT / "src" / "eedesigncore" / "py.typed").is_file()
