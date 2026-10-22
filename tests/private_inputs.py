"""Names of test inputs that the public copy does not carry.

Some tests read inputs that cannot be published: copies of an in-house KiCad
project template under ``tests/fixtures/``, and design projects and source
clones on the maintainer's machine. Their names live in ``tests/fixtures/private_inputs.json``,
which the public export leaves out.

Without that file, :func:`private_path` returns a path that does not exist and
:func:`private_text` returns ``None``. A test that needs the input then skips or
fails the same way it does when the input itself is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRIVATE_INPUTS = REPO / "tests" / "fixtures" / "private_inputs.json"
ABSENT = REPO / "tests" / "fixtures" / "private-input-absent"


def _entries() -> dict[str, object]:
    try:
        data = json.loads(PRIVATE_INPUTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def private_text(key: str) -> str | None:
    """The text stored under *key*, or ``None`` when the entry is absent."""
    value = _entries().get(key)
    return value if isinstance(value, str) else None


def private_path(key: str, suffix: str = "") -> Path:
    """The path stored under *key*, or a path that does not exist.

    A relative entry is relative to the project root, so the result does not
    depend on the working directory. The path that does not exist ends in
    *suffix*, so a test that reads the file type from the name still sees it.
    """
    value = private_text(key)
    if value is None:
        return ABSENT / f"{key}{suffix}"
    path = Path(value)
    return path if path.is_absolute() else REPO / path
