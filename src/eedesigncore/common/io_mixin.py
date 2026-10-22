"""Centralized file-IO mixin for all KiCad file models.

Provides ``from_file``, ``from_string``, ``to_string``, and ``to_file`` with
configurable line-ending support (preserve / native / lf / crlf).
"""

from __future__ import annotations

import os
from abc import abstractmethod
from pathlib import Path
from typing import Literal, Self

from pydantic import PrivateAttr

from eedesigncore.common.base import KicadModel
from eedesigncore.common.source_reads import _record_read


def _sniff_line_ending(raw: bytes) -> str:
    """Scan the first 4096 bytes; return ``"crlf"`` if ``\\r\\n`` found, else ``"lf"``."""
    sample = raw[:4096]
    if b"\r\n" in sample:
        return "crlf"
    return "lf"


def _resolve_line_ending(
    requested: Literal["preserve", "native", "lf", "crlf"],
    recorded: str | None,
) -> str:
    """Resolve the requested line-ending mode to ``"lf"`` or ``"crlf"``.

    Rules:
    - ``"lf"`` / ``"crlf"`` → use directly.
    - ``"native"`` → use ``os.linesep`` (``"crlf"`` on Windows, ``"lf"`` elsewhere).
    - ``"preserve"`` with a recorded EOL → use that recorded EOL.
    - ``"preserve"`` with *no* recorded EOL (in-memory object) → fixed ``"crlf"``
      regardless of host OS (operator-confirmed decision; deterministic cross-host).
    """
    if requested == "lf":
        return "lf"
    if requested == "crlf":
        return "crlf"
    if requested == "native":
        return "crlf" if os.linesep == "\r\n" else "lf"
    # "preserve"
    if recorded is not None:
        return recorded
    # preserve + no recorded EOL → fixed crlf (ADR-014)
    return "crlf"


class KicadFileMixin(KicadModel):
    """Mixin that adds file-IO methods to any KiCad domain root model.

    Subclasses must implement ``_from_text`` (classmethod) and ``_to_lf_string``
    (instance method).  The mixin stores the source line-ending detected at
    ``from_file`` time in ``_source_line_ending`` (a Pydantic ``PrivateAttr``
    excluded from serialization) so that ``to_file("preserve")`` can reproduce
    the original line endings.
    """

    _source_line_ending: str | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Abstract hooks — subclasses provide the actual parse/write logic
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def _from_text(cls, text: str) -> Self:
        """Parse *text* and return a populated instance (no EOL recorded)."""
        ...

    @abstractmethod
    def _to_lf_string(self) -> str:
        """Serialise to a canonical LF-only string."""
        ...

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path | str) -> Self:
        """Read a KiCad file from disk, sniffing its line-ending."""
        raw = Path(path).read_bytes()
        _record_read(Path(path), raw)
        ending = _sniff_line_ending(raw)
        text = raw.decode("utf-8")
        obj = cls._from_text(text)
        object.__setattr__(obj, "_source_line_ending", ending)
        return obj

    @classmethod
    def from_string(cls, text: str) -> Self:
        """Parse from a string; ``_source_line_ending`` is left as ``None``."""
        return cls._from_text(text)

    def to_string(self) -> str:
        """Return a canonical LF-only serialization."""
        return self._to_lf_string()

    def to_file(
        self,
        path: Path | str,
        line_ending: Literal["preserve", "native", "lf", "crlf"] = "preserve",
    ) -> None:
        """Write to disk in binary mode with the requested line-ending style.

        Args:
            path: Destination file path.
            line_ending: One of ``"preserve"`` (default), ``"native"``,
                ``"lf"``, or ``"crlf"``.  ``"preserve"`` re-applies the
                ending sniffed at load time; if the object was constructed
                in-memory (no recorded EOL) it falls back to ``"crlf"``.
        """
        content_lf = self.to_string()
        target = _resolve_line_ending(line_ending, self._source_line_ending)
        if target == "crlf":
            out = content_lf.replace("\n", "\r\n").encode("utf-8")
        else:
            out = content_lf.encode("utf-8")
        Path(path).write_bytes(out)
