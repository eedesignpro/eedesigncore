"""A consumer-style type-check probe over the installed package (R2, UF-001).

The unit test in ``tests/unit/test_package_metadata.py`` proves the marker file
exists. This test proves the marker does its job: it runs ``mypy --strict`` over
a small snippet that imports the installed package, with no
``ignore_missing_imports`` override. Without ``py.typed`` the same snippet
reports ``import-untyped`` on the import and ``no-any-return`` on the attribute,
which is exactly what the consumer reported.

The probe runs in a temporary directory with its own empty config file, so the
project's own ``[tool.mypy]`` section cannot influence the result.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROBE = """\
from eedesigncore.common.sch_family.symbol import SymbolUnit


def unit_number(unit: SymbolUnit) -> int:
    return unit.unit_index


def encode(name: str) -> str:
    return SymbolUnit.encode_name(name, 2, 1)
"""

EMPTY_CONFIG = "[mypy]\n"


def _run_mypy(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",
            "--config-file",
            "mypy.ini",
            "--cache-dir",
            str(work / ".mypy_cache"),
            "consumer.py",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )


def test_installed_package_type_checks_without_ignore_missing_imports(tmp_path: Path) -> None:
    (tmp_path / "consumer.py").write_text(PROBE, encoding="utf-8")
    (tmp_path / "mypy.ini").write_text(EMPTY_CONFIG, encoding="utf-8")

    try:
        result = _run_mypy(tmp_path)
    except FileNotFoundError:  # pragma: no cover - only without mypy installed
        pytest.skip("mypy is not installed in this interpreter")

    output = result.stdout + result.stderr
    if "Cannot find implementation or library stub" in output:  # pragma: no cover
        pytest.skip("eedesigncore is not importable by this interpreter")

    assert "import-untyped" not in output, output
    assert "no-any-return" not in output, output
    assert result.returncode == 0, output
