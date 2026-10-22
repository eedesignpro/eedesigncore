"""Exact-source guards for existing schematic, project, and board files."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eedesigncore.common.paths import is_remote_uri
from eedesigncore.pcb.models import Board
from eedesigncore.pro.models import ProjectFile
from eedesigncore.sch.models import Schematic


def _local(path: str | Path) -> Path:
    if is_remote_uri(str(path)) or str(path).startswith(("//", "\\\\")):
        raise ValueError("Guarded files require local paths.")
    result = Path(path).absolute()
    if any(part.is_symlink() for part in (result, *result.parents)):
        raise ValueError("Guarded files cannot use symbolic links.")
    return result.resolve()


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable bytes captured before reading or editing a local source."""

    path: Path
    content: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _local(self.path))
        if not isinstance(self.content, bytes):
            raise TypeError("Source content must be immutable bytes.")

    @classmethod
    def capture(cls, path: str | Path) -> SourceSnapshot:
        target = _local(path)
        return cls(target, target.read_bytes())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def verify(self) -> None:
        if _local(self.path) != self.path or self.path.read_bytes() != self.content:
            raise ValueError(f"Source changed since capture: {self.path}")


@dataclass(frozen=True)
class GuardedDocument:
    """An editable document paired with immutable original source bytes."""

    source: SourceSnapshot
    document: Schematic | ProjectFile | Board

    def __post_init__(self) -> None:
        if not isinstance(self.document, Schematic | ProjectFile | Board):
            raise TypeError("Guarded documents require a schematic, project, or board model.")
        suffix = (
            ".kicad_sch"
            if isinstance(self.document, Schematic)
            else ".kicad_pcb"
            if isinstance(self.document, Board)
            else ".kicad_pro"
        )
        if self.source.path.suffix.lower() != suffix:
            raise ValueError("The guarded document type does not match its file extension.")


@dataclass(frozen=True)
class GuardedFileResult:
    path: Path
    before_sha256: str
    after_sha256: str
    status: Literal["unchanged", "saved", "pending"]


@dataclass(frozen=True)
class GuardedSaveResult:
    """Per-file outcome. Multiple replacements are never one atomic transaction."""

    files: tuple[GuardedFileResult, ...]
    status: Literal["complete", "rejected", "partial"]


class GuardedSaveError(ValueError):
    """A rejected or partially completed save, with per-file recovery evidence."""

    def __init__(self, message: str, result: GuardedSaveResult) -> None:
        super().__init__(message)
        self.result = result


def load_guarded_document(path: str | Path) -> GuardedDocument:
    source = SourceSnapshot.capture(path)
    kind = source.path.suffix.lower()
    if kind == ".kicad_sch":
        document: Schematic | ProjectFile | Board = Schematic.from_string(
            source.content.decode("utf-8")
        )
    elif kind == ".kicad_pro":
        document = ProjectFile.from_string(source.content.decode("utf-8"))
    elif kind == ".kicad_pcb":
        document = Board.from_string(source.content.decode("utf-8"))
    else:
        raise ValueError("Guarded documents support schematic, project, and board files.")
    return GuardedDocument(source, document)


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


@contextmanager
def _staged_bytes(target: Path, data: bytes) -> Iterator[Path]:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            os.chmod(temporary, target.stat().st_mode)
        yield temporary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_guarded_documents(
    documents: Sequence[GuardedDocument],
    *,
    dependencies: Sequence[SourceSnapshot] = (),
) -> GuardedSaveResult:
    """Validate and stage all outputs, then replace files with exact-byte guards.

    Unchanged models retain original bytes and timestamps. Dependencies can guard
    unchanged child sheets and other inputs. Locks coordinate cooperating writers.
    An editor that ignores locks can still race the final check. Unsaved GUI edits
    cannot be detected. After partial failure, inspect result and retained snapshots.
    """
    sources = [entry.source for entry in documents] + list(dependencies)
    paths = [source.path for source in sources]
    if len(set(paths)) != len(paths):
        raise ValueError("Guarded sources must have distinct paths.")
    results: list[GuardedFileResult] = []
    try:
        with ExitStack() as stack:
            for path in sorted(paths):
                stack.enter_context(_lock(_local(path)))
            for source in sources:
                source.verify()
            pending: list[tuple[GuardedDocument, Path, int]] = []
            for entry in documents:
                original = type(entry.document).from_string(entry.source.content.decode("utf-8"))
                text = entry.document.to_string()
                type(entry.document).from_string(text)
                if text == original.to_string():
                    data = entry.source.content
                else:
                    data = (
                        text.replace("\n", "\r\n").encode("utf-8")
                        if b"\r\n" in entry.source.content[:4096]
                        else text.encode("utf-8")
                    )
                changed = data != entry.source.content
                results.append(
                    GuardedFileResult(
                        entry.source.path,
                        entry.source.sha256,
                        hashlib.sha256(data).hexdigest(),
                        "pending" if changed else "unchanged",
                    )
                )
                if changed:
                    staged = stack.enter_context(_staged_bytes(entry.source.path, data))
                    pending.append((entry, staged, len(results) - 1))
            expected = {source.path: source for source in sources}
            for entry, staged, index in pending:
                for source in expected.values():
                    source.verify()
                next_source = SourceSnapshot(entry.source.path, staged.read_bytes())
                os.replace(staged, entry.source.path)
                expected[entry.source.path] = next_source
                previous = results[index]
                results[index] = GuardedFileResult(
                    previous.path, previous.before_sha256, previous.after_sha256, "saved"
                )
            for source in expected.values():
                source.verify()
        return GuardedSaveResult(tuple(results), "complete")
    except (OSError, ValueError) as error:
        status: Literal["rejected", "partial"] = (
            "partial" if any(result.status == "saved" for result in results) else "rejected"
        )
        raise GuardedSaveError(str(error), GuardedSaveResult(tuple(results), status)) from error
