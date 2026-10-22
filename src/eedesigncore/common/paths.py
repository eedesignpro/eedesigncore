"""Text-only guards for a path that arrives from a file.

Every check here runs on the RAW string, before anything resolves or opens the
path. :meth:`pathlib.Path.resolve` is not a pure string operation: on Windows
``ntpath.realpath`` opens the target to read its final name, so resolving
``//attacker.example/share/x`` starts an SMB session to that host and can leak
an NTLM credential. A guard that runs after the resolve is too late.

Security review iteration 11, MEDIUM-1 and LOW-5. The library-table resolver and
the sheet-hierarchy walk both use these guards, so they live here rather than in
either format package.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Final

SCHEME_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
"""A URL scheme at the start of the text, such as ``http://`` or ``smb://``."""


def is_unc_path(text: str) -> bool:
    """Report whether *text* names a UNC host, as ``//host/share`` or ``\\\\host\\share``.

    Both separators count, because Windows accepts both.
    """
    return text.replace("\\", "/").startswith("//")


def has_uri_scheme(text: str) -> bool:
    """Report whether *text* starts with a URL scheme."""
    return SCHEME_RE.match(text) is not None


def is_remote_uri(text: str) -> bool:
    """Report whether *text* is a UNC path or carries a URL scheme such as ``http://``."""
    return is_unc_path(text) or has_uri_scheme(text)


def unc_covers(root: Path, path: Path) -> bool:
    """Compare a UNC root and a UNC path as text, with no filesystem access."""
    normalised_root = _normalise_unc(root)
    normalised_path = _normalise_unc(path)
    return normalised_path == normalised_root or normalised_path.is_relative_to(normalised_root)


def _normalise_unc(path: Path) -> PurePosixPath:
    """Render a UNC path for comparison. SMB host and share names ignore case."""
    return PurePosixPath(str(path).replace("\\", "/").casefold())
