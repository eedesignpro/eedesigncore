"""Context-local byte evidence for a bounded extraction operation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class _ReadCapture:
    source: Path | None = None
    reads: dict[Path, tuple[str, int, int]] = field(default_factory=dict)
    root_sheets: set[Path] = field(default_factory=set)
    absent: set[Path] = field(default_factory=set)


_active: ContextVar[_ReadCapture | None] = ContextVar("netbom_source_reads", default=None)


@contextmanager
def _capture_reads() -> Iterator[_ReadCapture]:
    capture = _ReadCapture()
    token = _active.set(capture)
    try:
        yield capture
    finally:
        _active.reset(token)


def _record_read(path: Path, raw: bytes) -> None:
    capture = _active.get()
    if capture is None:
        return
    path = path.resolve()
    digest = hashlib.sha256(raw).hexdigest()
    previous = capture.reads.get(path)
    if path in capture.absent or previous and previous[0] != digest:
        raise ValueError("A source file changed during extraction.")
    capture.reads[path] = (digest, len(raw), previous[2] + 1 if previous else 1)


def _record_absent(path: Path) -> None:
    capture = _active.get()
    if capture is not None:
        path = path.resolve()
        if path in capture.reads:
            raise ValueError("A source file disappeared during extraction.")
        capture.absent.add(path)


def _record_root_sheet(path: Path) -> None:
    capture = _active.get()
    if capture is not None:
        capture.root_sheets.add(path.resolve())


def _record_root_fallback(path: Path, project_root: Path) -> None:
    if _active.get() is None:
        return
    resolved = path.resolve()
    if resolved.is_relative_to(project_root.resolve()):
        _record_root_sheet(resolved)
        _record_absent(resolved)
