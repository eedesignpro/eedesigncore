"""Read-side walk of a KiCad project sheet tree (ADR-040 follow-on, ADR-051).

A ``.kicad_sch`` file holds exactly one sheet screen. A hierarchical design
spreads across many files, and a parent sheet reaches a child through
``(property "Sheetfile" "child.kicad_sch")``. :func:`load_hierarchy` follows
those references and returns one typed tree.

The helper never mutates a model and never writes a file. Byte identity stays
per file, exactly as ADR-040 requires.

Verified KiCad 10.0.3 behaviour behind each rule:

- A relative ``Sheetfile`` resolves against the directory of the parent
  schematic, not against the project directory
  (``eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:197-207``).
- A file that already appears as a DIRECT ANCESTOR is a cycle and is refused
  (``:210-230``). The same file used twice as two sibling instances is normal,
  and KiCad shares one screen between the two (``:236-255``).
- A sub-sheet that fails to load queues an error and the walk continues. A root
  that fails to load has no recovery (``:262-277``).
- A project file with no ``schematic.top_level_sheets`` entry gets one default
  entry named after the project (``common/project/project_file.cpp:693-709``).
- A ``top_level_sheets`` entry pointing at a missing file falls back to
  ``<project name>.kicad_sch`` when that file exists (``:712-745``).

Guards KiCad does not have, all recorded in ADR-051: a depth cap, a file
budget, a per-file read cap, and confinement of every resolved path to the
project root. A ``Sheetfile`` value that names a UNC host or carries a URL
scheme is refused on the raw text, before anything resolves or opens it, unless
an allowed UNC root covers it. Security review iteration 11, LOW-5.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.paths import is_remote_uri, is_unc_path, unc_covers
from eedesigncore.common.source_reads import (
    _record_absent,
    _record_root_fallback,
    _record_root_sheet,
)
from eedesigncore.exceptions import SheetHierarchyError
from eedesigncore.pro.models import ProjectFile
from eedesigncore.sch.models import Schematic, Sheet

SHEET_FILE_PROPERTY: Final = "Sheetfile"
"""The sheet field naming the child file (``sch_io_kicad_sexpr.cpp:1140``)."""

SHEET_NAME_PROPERTY: Final = "Sheetname"
"""The sheet field naming the child sheet."""

SCHEMATIC_SUFFIX: Final = ".kicad_sch"
PROJECT_SUFFIX: Final = ".kicad_pro"

MAX_HIERARCHY_DEPTH: Final = 32
"""Depth cap on the sheet tree. Real designs stay far below it."""

MAX_HIERARCHY_FILES: Final = 512
"""Cap on distinct schematic files loaded per walk."""

MAX_SCHEMATIC_FILE_BYTES: Final = 64 * 1024 * 1024
"""Read cap per schematic file."""


class SheetNode(KicadModel):
    """One sheet instance in the tree.

    A file used twice produces two nodes. Both nodes carry the same
    :attr:`schematic` object, because KiCad shares one screen between two
    instances of one file (``sch_io_kicad_sexpr.cpp:236-255``). Treat the
    schematic as read-only: an edit through one node is visible through the
    other.
    """

    path: Path
    """The resolved absolute path of the ``.kicad_sch`` file."""

    schematic: Schematic
    """The loaded file. Shared between every instance of the same file."""

    name: str
    """The ``Sheetname`` field, or the project sheet name for a root."""

    kiid_path: str
    """This instance's full KIID path, such as ``/<root uuid>/<sheet uuid>``.

    The root form is ``/<root uuid>`` (``eeschema/sch_sheet_path.cpp:438-458``).
    """

    page: str | None = None
    """The page number of this instance, or None when the file records none."""

    sheet: Sheet | None = None
    """The ``(sheet …)`` node in the parent file. None for a root."""

    children: list[SheetNode] = Field(default_factory=list)
    """Child instances, in the order the parent file lists them."""

    @property
    def is_root(self) -> bool:
        """True when no parent sheet references this node."""
        return self.sheet is None

    def walk(self) -> Iterator[SheetNode]:
        """Yield this node, then every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


class SheetHierarchy(KicadModel):
    """The sheet tree of one project.

    ``roots`` holds one node per top-level sheet. KiCad 10 allows more than one
    (``common/project/project_file.cpp:57-59``), so the list is not always of
    length one.
    """

    project_root: Path
    """The directory every resolved sheet file must stay inside."""

    project_path: Path | None = None
    """The ``.kicad_pro`` file, or None when the walk started at a schematic."""

    project: ProjectFile | None = None
    """The loaded project file, when one was found."""

    roots: list[SheetNode] = Field(default_factory=list)
    """One node per top-level sheet."""

    errors: list[str] = Field(default_factory=list)
    """Problems collected under ``strict=False``. Always empty when strict."""

    @property
    def root(self) -> SheetNode:
        """The single root node.

        Raises:
            SheetHierarchyError: The project has no root, or more than one.
        """
        if len(self.roots) != 1:
            raise SheetHierarchyError(
                f"Expected exactly one top-level sheet, found {len(self.roots)}"
            )
        return self.roots[0]

    def walk(self) -> Iterator[SheetNode]:
        """Yield every node of every root, depth first."""
        for root in self.roots:
            yield from root.walk()

    def files(self) -> list[Path]:
        """Every distinct schematic file, in first-sighting order."""
        seen: list[Path] = []
        known: set[str] = set()
        for node in self.walk():
            key = _path_key(node.path)
            if key not in known:
                known.add(key)
                seen.append(node.path)
        return seen


def load_hierarchy(
    path: Path | str,
    *,
    allowed_roots: Sequence[Path | str] | None = None,
    strict: bool = True,
    max_depth: int = MAX_HIERARCHY_DEPTH,
    max_files: int = MAX_HIERARCHY_FILES,
    max_occurrences: int | None = None,
    max_total_bytes: int | None = None,
) -> SheetHierarchy:
    """Walk the sheet tree of a project and return it.

    Args:
        path: A ``.kicad_pro`` file or a root ``.kicad_sch`` file. A project
            file supplies the top-level sheet list. A schematic file becomes
            the single root, and a ``.kicad_pro`` beside it still loads.
        allowed_roots: Extra directories a sheet reference may point into. The
            project root is always allowed. Every other location is refused,
            which stops an absolute or ``..`` reference from escaping.
        strict: True, the default, raises on the first missing child file. False
            collects the message in :attr:`SheetHierarchy.errors` and keeps
            walking, which mirrors KiCad
            (``sch_io_kicad_sexpr.cpp:262-277``). A missing ROOT always raises,
            under either setting, for the same reason KiCad has no recovery
            there.
        max_depth: Depth cap on the sheet tree. Exceeding it always raises.
        max_files: Cap on distinct schematic files. Exceeding it always raises.
        max_occurrences: Optional cap on sheet instances, including repeated files.
        max_total_bytes: Optional cap on bytes across distinct schematic files.

    Returns:
        The tree, with one node per sheet instance.

    Raises:
        SheetHierarchyError: The start path has the wrong suffix, a root file
            is missing, a sheet reference escapes the allowed roots, a file
            appears as its own ancestor, or a cap is exceeded.
        KicadParseError: A schematic or the project file is malformed.

    Example::

        from eedesigncore import load_hierarchy

        tree = load_hierarchy("board.kicad_pro")
        for node in tree.walk():
            print(node.page, node.name, node.path.name)
    """
    if max_occurrences is not None and max_occurrences < 1:
        raise ValueError("The sheet occurrence limit must be positive.")
    if max_total_bytes is not None and max_total_bytes < 1:
        raise ValueError("The total schematic byte limit must be positive.")
    start = _resolve(Path(path))
    context = _WalkContext(
        project_root=start.parent,
        allowed=[start.parent, *(_resolve(Path(root)) for root in (allowed_roots or ()))],
        strict=strict,
        max_depth=max_depth,
        max_files=max_files,
        max_occurrences=max_occurrences,
        max_total_bytes=max_total_bytes,
    )

    project: ProjectFile | None
    if start.suffix == PROJECT_SUFFIX:
        project = _load_project(start)
        entries = _top_level_sheets(project, start)
    elif start.suffix == SCHEMATIC_SUFFIX:
        project_path = start.with_suffix(PROJECT_SUFFIX)
        project = _load_project(project_path) if project_path.is_file() else None
        entries = [_TopLevelSheet(filename=start.name, name=start.stem)]
    else:
        raise SheetHierarchyError(
            f"Expected a {PROJECT_SUFFIX} or {SCHEMATIC_SUFFIX} file, got: {start}"
        )

    roots: list[SheetNode] = []
    for entry in entries:
        context.reject_remote(entry.filename, start)
        file_path = context.check_allowed(_resolve(context.project_root / entry.filename))
        _record_root_sheet(file_path)
        if not file_path.is_file():
            _record_absent(file_path)
            raise SheetHierarchyError(f"Top-level sheet file not found: {file_path}")
        roots.append(context.walk_root(file_path, name=entry.name))

    return SheetHierarchy(
        project_root=context.project_root,
        project_path=start if start.suffix == PROJECT_SUFFIX else None,
        project=project,
        roots=roots,
        errors=context.errors,
    )


class _TopLevelSheet(KicadModel):
    """One ``schematic.top_level_sheets`` entry (``project_file.h:49-58``)."""

    filename: str
    name: str = ""


class _WalkContext(KicadModel):
    """Per-call state: the caps, the confinement set, and the screen cache."""

    project_root: Path
    allowed: list[Path]
    strict: bool
    max_depth: int
    max_files: int
    max_occurrences: int | None = None
    max_total_bytes: int | None = None
    occurrences: int = 0
    total_bytes: int = 0
    errors: list[str] = Field(default_factory=list)
    cache: dict[str, Schematic] = Field(default_factory=dict)

    def count_occurrence(self) -> None:
        self.occurrences += 1
        if self.max_occurrences is not None and self.occurrences > self.max_occurrences:
            raise SheetHierarchyError("Sheet hierarchy exceeds the occurrence limit.")

    def walk_root(self, file_path: Path, *, name: str) -> SheetNode:
        self.count_occurrence()
        schematic = self.load(file_path)
        kiid_path = "/" + (schematic.uuid or "")
        return SheetNode(
            path=file_path,
            schematic=schematic,
            name=name or file_path.stem,
            kiid_path=kiid_path,
            page=_root_page(schematic),
            sheet=None,
            children=self.walk_children(
                schematic,
                parent_path=file_path,
                parent_kiid=kiid_path,
                ancestors=(file_path,),
                depth=1,
            ),
        )

    def walk_children(
        self,
        schematic: Schematic,
        *,
        parent_path: Path,
        parent_kiid: str,
        ancestors: tuple[Path, ...],
        depth: int,
    ) -> list[SheetNode]:
        if depth > self.max_depth:
            raise SheetHierarchyError(
                f"Sheet tree exceeds the depth cap of {self.max_depth}: {parent_path}"
            )

        nodes: list[SheetNode] = []
        for sheet in schematic.sheets:
            node = self.walk_sheet(
                sheet,
                parent_path=parent_path,
                parent_kiid=parent_kiid,
                ancestors=ancestors,
                depth=depth,
            )
            if node is not None:
                nodes.append(node)
        return nodes

    def walk_sheet(
        self,
        sheet: Sheet,
        *,
        parent_path: Path,
        parent_kiid: str,
        ancestors: tuple[Path, ...],
        depth: int,
    ) -> SheetNode | None:
        self.count_occurrence()
        reference = _field_value(sheet, SHEET_FILE_PROPERTY)
        if not reference:
            self.problem(f"Sheet without a {SHEET_FILE_PROPERTY} field in {parent_path}")
            return None

        self.reject_remote(reference, parent_path)
        file_path = self.check_allowed(_resolve(parent_path.parent / reference))

        ancestor_keys = {_path_key(item) for item in ancestors}
        if _path_key(file_path) in ancestor_keys:
            raise SheetHierarchyError(
                f"Could not load sheet {file_path} because it already appears as a direct "
                f"ancestor in the schematic hierarchy"
            )

        if not file_path.is_file():
            _record_absent(file_path)
            self.problem(f"Sub-sheet file not found: {file_path} (from {parent_path})")
            return None

        schematic = self.load(file_path)
        kiid_path = parent_kiid.rstrip("/") + "/" + (sheet.uuid or "")
        return SheetNode(
            path=file_path,
            schematic=schematic,
            name=_field_value(sheet, SHEET_NAME_PROPERTY) or file_path.stem,
            kiid_path=kiid_path,
            page=_instance_page(sheet, parent_kiid),
            sheet=sheet,
            children=self.walk_children(
                schematic,
                parent_path=file_path,
                parent_kiid=kiid_path,
                ancestors=(*ancestors, file_path),
                depth=depth + 1,
            ),
        )

    def load(self, file_path: Path) -> Schematic:
        """Load one file, or return the cached screen for a repeated file."""
        key = _path_key(file_path)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        if len(self.cache) >= self.max_files:
            raise SheetHierarchyError(
                f"Sheet tree exceeds the {self.max_files} file budget at {file_path}"
            )

        size = file_path.stat().st_size
        if size > MAX_SCHEMATIC_FILE_BYTES:
            raise SheetHierarchyError(
                f"Schematic is {size} bytes, over the {MAX_SCHEMATIC_FILE_BYTES} byte read cap: "
                f"{file_path}"
            )

        self.total_bytes += size
        if self.max_total_bytes is not None and self.total_bytes > self.max_total_bytes:
            raise SheetHierarchyError("Sheet hierarchy exceeds the total byte limit.")
        schematic = Schematic.from_file(file_path)
        self.cache[key] = schematic
        return schematic

    def reject_remote(self, reference: str, source: Path) -> None:
        """Refuse a UNC or URL reference before anything resolves or opens it.

        :meth:`pathlib.Path.resolve` opens a UNC target on Windows to read its
        final name, so a hostile ``Sheetfile`` value reaches the network before
        the confinement check can refuse it. This guard runs on the raw text
        instead, and passes only a UNC reference that an allowed UNC root
        covers. Security review iteration 11, LOW-5.
        """
        if not is_remote_uri(reference):
            return
        if is_unc_path(reference) and self.unc_allowed(Path(reference)):
            return
        raise SheetHierarchyError(
            f"Sheet reference names a remote location, refused before any file access: "
            f"{reference} (from {source})"
        )

    def unc_allowed(self, file_path: Path) -> bool:
        """Report whether an allowed UNC root covers *file_path*, compared as text."""
        return any(unc_covers(root, file_path) for root in self.allowed if is_unc_path(str(root)))

    def check_allowed(self, file_path: Path) -> Path:
        """Refuse a path outside the project root and the extra allowed roots."""
        if is_unc_path(str(file_path)):
            if self.unc_allowed(file_path):
                return file_path
        else:
            for root in self.allowed:
                if file_path == root or file_path.is_relative_to(root):
                    return file_path
        raise SheetHierarchyError(
            f"Sheet reference lies outside the project root {self.project_root}: {file_path}"
        )

    def problem(self, message: str) -> None:
        """Apply the missing-file policy: raise when strict, else collect."""
        if self.strict:
            raise SheetHierarchyError(message)
        self.errors.append(message)
        return None


def _load_project(path: Path) -> ProjectFile:
    size = path.stat().st_size
    if size > MAX_SCHEMATIC_FILE_BYTES:
        raise SheetHierarchyError(
            f"Project file is {size} bytes, over the {MAX_SCHEMATIC_FILE_BYTES} byte read cap: "
            f"{path}"
        )
    return ProjectFile.from_file(path)


def _top_level_sheets(project: ProjectFile, project_path: Path) -> list[_TopLevelSheet]:
    """The top-level sheet list, with both KiCad migration rules applied."""
    project_name = project_path.stem
    entries = [
        _TopLevelSheet(
            filename=entry.get_str("filename") or "",
            name=entry.get_str("name") or "",
        )
        for entry in project.top_level_sheets
    ]
    entries = [entry for entry in entries if entry.filename]

    if not entries:
        return [_TopLevelSheet(filename=project_name + SCHEMATIC_SUFFIX, name=project_name)]

    fallback = project_path.with_suffix(SCHEMATIC_SUFFIX)
    fixed: list[_TopLevelSheet] = []
    for entry in entries:
        if is_remote_uri(entry.filename):
            fixed.append(entry)
            continue
        named = project_path.parent / entry.filename
        if not named.is_file() and fallback.is_file():
            _record_root_fallback(named, project_path.parent)
            fixed.append(_TopLevelSheet(filename=fallback.name, name=project_name))
        else:
            fixed.append(entry)
    return fixed


def _field_value(sheet: Sheet, name: str) -> str:
    for field in sheet.fields:
        if field.name == name:
            return field.value
    return ""


def _root_page(schematic: Schematic) -> str | None:
    """The root page number from the flat ``(sheet_instances)`` block."""
    block = schematic.sheet_instances
    if block is None or not block.paths:
        return None
    for entry in block.paths:
        if entry.path == "/":
            return entry.page
    return block.paths[0].page


def _instance_page(sheet: Sheet, parent_kiid: str) -> str | None:
    """The page number this sheet carries for the parent's KIID path.

    A sheet instance records the path of its PARENT, not its own
    (``eeschema/sch_sheet_path.cpp:1604-1622``).
    """
    for project in sheet.instances:
        for entry in project.paths:
            if entry.path == parent_kiid:
                return entry.page
    return None


def _resolve(path: Path) -> Path:
    """Normalise a path and follow symlinks, without requiring it to exist.

    A UNC path comes back untouched. Resolving one opens an SMB session to the
    named host (LOW-5), and the confinement check compares it as text anyway.
    """
    if is_unc_path(str(path)):
        return path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _path_key(path: Path) -> str:
    """A comparison key that ignores case on a case-insensitive filesystem."""
    return os.path.normcase(str(path))
