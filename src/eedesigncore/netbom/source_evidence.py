"""Versioned evidence for bytes consumed by NetBOM extraction."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, field_validator, model_validator

from eedesigncore._version import __version__
from eedesigncore.common.base import KicadModel
from eedesigncore.common.source_reads import _ReadCapture

if TYPE_CHECKING:
    from eedesigncore.netbom.models import NetbomRecord
from eedesigncore.project.hierarchy import SheetHierarchy


class NetbomSourceFile(KicadModel):
    """One read file or an explicitly absent expected file."""

    path: str = Field(min_length=1, max_length=2048)
    role: Literal["board", "project", "root_schematic", "schematic_dependency"]
    status: Literal["read", "absent"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    read_count: int = Field(default=0, ge=0)
    source_ids: list[str] = Field(default_factory=list, max_length=4096)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Source paths must use project-relative POSIX names.")
        path = PurePosixPath(value)
        if (
            not path.parts
            or path.is_absolute()
            or value != path.as_posix()
            or any(p in (".", "..") for p in path.parts)
        ):
            raise ValueError("Source paths must remain inside the project.")
        return value

    @model_validator(mode="after")
    def valid_read(self) -> Self:
        if self.status == "read":
            if self.sha256 is None or self.size_bytes is None or self.read_count < 1:
                raise ValueError("Read evidence requires a byte hash, size, and read count.")
        elif self.sha256 is not None or self.size_bytes is not None or self.read_count:
            raise ValueError("Absent files cannot carry read evidence.")
        return self


class NetbomSourceManifest(KicadModel):
    """Extraction-local file evidence. It does not describe Git or authorship."""

    schema_version: Literal["source-evidence-1"] = "source-evidence-1"
    extraction_version: Literal["netbom-extraction-1"] = "netbom-extraction-1"
    tool: Literal["eedesigncore"] = "eedesigncore"
    tool_version: str = __version__
    mode: Literal["files", "in_memory"] = "in_memory"
    hash_algorithm: Literal["sha256"] = "sha256"
    hash_basis: Literal["exact_bytes_read"] = "exact_bytes_read"
    files: list[NetbomSourceFile] = Field(default_factory=list, max_length=4099)
    schematic_dependencies: Literal["complete", "partial", "not_inspected"] = "not_inspected"
    not_inspected: list[str] = Field(
        default_factory=lambda: [
            "custom_rules",
            "symbol_libraries",
            "footprint_libraries",
            "drawing_sheets",
            "3d_models",
        ]
    )
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Files describe this extraction only. "
            "They do not establish Git state, authorship, or source freshness.",
            "Embedded symbol and footprint data do not prove external library files were read.",
            "In-memory extraction does not inspect files or establish file absence.",
        ]
    )

    @model_validator(mode="after")
    def unique_files(self) -> Self:
        if len({entry.path for entry in self.files}) != len(self.files):
            raise ValueError("Source evidence contains duplicate file identities.")
        if self.mode == "in_memory" and self.files:
            raise ValueError("In-memory extraction cannot claim file reads.")
        return self


def _manifest(
    capture: _ReadCapture | None,
    source: Path | None,
    hierarchy: SheetHierarchy | None,
    records: dict[str, NetbomRecord],
) -> NetbomSourceManifest:
    if capture is None or source is None:
        return NetbomSourceManifest()
    root = source.parent
    roots = capture.root_sheets
    sources: dict[str, list[str]] = {}
    for key, record in records.items():
        if record.kind == "source":
            locator = str(record.data.get("locator", "")).replace("\\", "/")
            sources.setdefault(locator, []).append(key)
    entries = []
    for path in sorted(set(capture.reads) | capture.absent):
        relative = path.relative_to(root).as_posix()
        role: Literal["board", "project", "root_schematic", "schematic_dependency"] = (
            "board"
            if path.suffix == ".kicad_pcb"
            else "project"
            if path.suffix == ".kicad_pro"
            else "root_schematic"
            if path in roots or path == source.with_suffix(".kicad_sch")
            else "schematic_dependency"
        )
        locator = "board" if role == "board" else "project" if role == "project" else relative
        ids = sorted(sources.get(locator, []))
        measured = capture.reads.get(path)
        entries.append(
            NetbomSourceFile(
                path=relative,
                role=role,
                status="read" if measured else "absent",
                sha256=measured[0] if measured else None,
                size_bytes=measured[1] if measured else None,
                read_count=measured[2] if measured else 0,
                source_ids=ids,
            )
        )
    incomplete = bool(hierarchy and hierarchy.errors or capture.absent & roots)
    result = NetbomSourceManifest(
        mode="files",
        files=entries,
        schematic_dependencies="partial"
        if incomplete
        else "complete"
        if hierarchy
        else "not_inspected",
    )
    result.limitations = [line for line in result.limitations if not line.startswith("In-memory")]
    if incomplete:
        result.limitations.append(
            "Some schematic dependencies were unavailable or rejected. "
            "Unread dependency paths and hashes are not inferred."
        )
    return result
