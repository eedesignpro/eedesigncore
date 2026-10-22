"""Discovery, recursive load, merge, and lookup for KiCad library tables.

Authority: ``context/audit/lib-table-discovery-2026-07-28.md`` (the A2 report),
which cites KiCad 10.0.3 for every rule below.

- The two tiers. Global tables sit directly in the user settings directory
  (``common/libraries/library_manager.cpp:448``, ``:169-178``). Project tables
  are siblings of the ``.kicad_pro`` file (``:521-524``,
  ``common/project.cpp:193-196``).
- Nested tables. A row whose ``type`` is the literal ``Table`` points at
  another table file, which KiCad loads recursively into the same flat
  nickname namespace (``library_manager.cpp:118-166``, ``:671-681``,
  ``common/libraries/library_table.cpp:34``). A disabled nested row
  contributes nothing (``:677-678``). A hidden nested row hides every
  descendant (``:667-669``, ``:680``).
- The merge. One nickname-keyed map takes the global tier first and the
  project tier second, so the project row wins and the last duplicate inside
  one file wins (``:647-648``, ``:687``). Display order follows the first
  sighting (``:684-685``).
- Missing tiers. KiCad skips an unreadable table and a table whose root token
  is the wrong kind (``:93-114``). No error reaches the user.

Deviations from KiCad, all recorded in ADR-047:

1. A relative nested-table URI resolves against the parent table directory,
   which the KiCad comment at ``library_manager.cpp:137`` states as the
   intent. KiCad itself resolves it against the process working directory
   first, which no library caller can predict.
2. Recursion carries a depth cap and a table-file budget. KiCad has neither.
   Both guard a discovery path that reads outside the repository.
3. ``find_library`` searches for an item by name, which KiCad never does. KiCad
   resolves a nickname the user already chose. ADR-047 states the search order.
4. A skip is RECORDED. Every table file and nested row the resolve did not
   read lands on ``ResolvedLibTable.skipped`` with a reason. KiCad skips
   silently and shows the user an empty library list; a library has no user to
   notice that.
5. Every file the resolver opens is confined to a root set, and the set has a
   default. The global tier reads under the KiCad install root and the user
   settings directory. The project tier reads under the project directory.
   ``allowed_roots`` only widens the set. A UNC path is refused before any
   resolution, unless an allowed root covers it. ADR-047 amendment 2 records
   the ruling, which closes MEDIUM-1 and MEDIUM-2 of the iteration-11 security
   review.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, PrivateAttr

from eedesigncore.common.base import KicadModel
from eedesigncore.common.paths import (
    has_uri_scheme,
    is_remote_uri,
    is_unc_path,
    unc_covers,
)
from eedesigncore.exceptions import KicadParseError, LibTableResolutionError
from eedesigncore.tables.env import (
    KICAD_SETTINGS_VERSION,
    LibTableEnvironment,
    default_kicad_install_root,
    default_user_settings_dir,
    read_text_capped,
)
from eedesigncore.tables.models import LibTable, LibTableKind

LibTableScope = Literal["global", "project"]

TABLE_TYPE_NAME: Final = "Table"
"""The row type that marks a nested table (``library_table.cpp:34``)."""

KICAD_ROW_TYPE: Final = "KiCad"
"""The row type of a plain on-disk library."""

PLACEHOLDER_NICKNAME: Final = "eedesigncore"
"""The nickname both injects fall back to when no table row matches (ADR-041)."""

TABLE_FILE_NAMES: Final[dict[LibTableKind, str]] = {
    "fp": "fp-lib-table",
    "sym": "sym-lib-table",
}
"""Table file names (``common/wildcards_and_files_ext.cpp:219-220``)."""

MAX_NESTED_TABLE_DEPTH: Final = 8
"""Depth cap on the nested-table chain. The stock chain is one level deep."""

MAX_TABLE_FILES: Final = 128
"""Cap on table files read per resolve call, across both tiers."""

MAX_TABLE_FILE_BYTES: Final = 16 * 1024 * 1024
"""Read cap per table file."""

MAX_LIBRARY_SCAN_BYTES: Final = 64 * 1024 * 1024
"""Read cap per symbol library scanned during an item search."""

MAX_LIBRARY_FOLDER_FILES: Final = 4096
"""Cap on part files read from one folder-form symbol library.

KiCad has no cap: it walks the whole directory. The largest folder-form library
on the measuring machine holds 30 files, so the cap is three orders of
magnitude above real use and exists only to bound a hostile directory.
"""

_FOOTPRINT_SUFFIX: Final = ".kicad_mod"

_SYMBOL_SUFFIX: Final = ".kicad_sym"


def path_within_roots(path: Path, roots: Sequence[Path]) -> bool:
    """Report whether *path* sits inside one of *roots*.

    A UNC path is compared as text against the UNC roots alone, because
    resolving it would touch the network. Every other path is normalised with
    :func:`_resolve_path` first, and so is each root.
    """
    text = str(path)
    if is_unc_path(text):
        return any(unc_covers(root, path) for root in roots if is_unc_path(str(root)))
    if has_uri_scheme(text):
        return False

    resolved = _resolve_path(path)
    for root in roots:
        if is_unc_path(str(root)):
            continue
        resolved_root = _resolve_path(root)
        if resolved == resolved_root or resolved.is_relative_to(resolved_root):
            return True
    return False


class ResolvedLibrary(KicadModel):
    """One library row after discovery, merge, and URI expansion.

    ``uri`` is expanded. ``raw_uri`` is the text the table file carries, which
    an unresolved variable leaves untouched. ``scope`` names the tier the row
    came from, and nested rows inherit the tier of their root table
    (``library_manager.cpp:151``).
    """

    nickname: str
    type: str
    uri: str
    raw_uri: str
    options: str = ""
    descr: str = ""
    disabled: bool = False
    hidden: bool = False
    scope: LibTableScope
    source_table: Path
    read_roots: list[Path] | None = None

    @property
    def path(self) -> Path:
        """The expanded URI as a path."""
        return Path(self.uri)

    @property
    def read_allowed(self) -> bool:
        """Report whether this row's file may be opened.

        The confinement travels WITH the row, not with the call site.
        :meth:`LibTableResolver.resolve` fills ``read_roots`` with the roots
        of the tier the row came from, so any reader can ask without being
        handed the table as well. ``None`` means no confinement, which is
        what a row built by hand gets, because its URI came from the caller
        and not from a file.

        The iteration-20 security review found the previous shape: the check
        lived in :meth:`ResolvedLibTable.scan_allowed`, so
        ``library_item_names(table.get(nickname))`` — a natural one-liner —
        read a file the resolver had already refused. An opt-in guard is the
        same shape that hid the amendment-3 defect.
        """
        if self.read_roots is None:
            return True
        return path_within_roots(self.path, self.read_roots)


class SkippedLibTable(KicadModel):
    """One table file or nested row the resolver did NOT read, and why.

    The skip policy has always been "skip, do not raise", which copies KiCad
    (``library_manager.cpp:93-114``). What it lacked was a record, so a library
    that vanished from the namespace looked exactly like a library that was
    never configured. That cost real time: the whole provisional chain, 22
    symbol libraries and 21 footprint libraries, was dropped by the read
    confinement with no signal, and the only way to see it was to re-run with
    ``strict=True``.

    ``reason`` is written for a person and names the fix where there is one.

    **It carries a local filesystem path**, which on a normal machine spells
    out the operating user's name and the directory layout. Fine for a local
    diagnostic; treat it as environment-sensitive before showing it to anyone
    else, the way a log line or a support ticket would be. Use ``path.name``
    when only the file matters.
    """

    path: Path
    scope: LibTableScope
    reason: str


class ResolvedLibTable(KicadModel):
    """The merged nickname namespace for one table kind.

    ``libraries`` holds one entry per nickname, ordered by first sighting, with
    the winning row in each slot. That copies ``LIBRARY_MANAGER::Rows``
    (``library_manager.cpp:700-703``).

    ``skipped`` names every table file and nested row the resolve did not read,
    with the reason. It is empty on a clean resolve. **Check it whenever a
    nickname you expected is missing**: a confinement miss, an absent file and
    a malformed file all subtract from the namespace without raising.

    ``global_roots`` and ``project_roots`` confine the library files an item
    search reads. :meth:`LibTableResolver.resolve` fills both with the roots it
    used, so a scan cannot reach a file the table load itself could not reach
    (MEDIUM-2). ``None`` means no confinement, which is what a table built by
    hand gets, because its rows come from the caller and not from a file.
    """

    kind: LibTableKind
    libraries: list[ResolvedLibrary] = Field(default_factory=list)
    global_roots: list[Path] | None = None
    project_roots: list[Path] | None = None
    skipped: list[SkippedLibTable] = Field(default_factory=list)

    _contains_cache: dict[tuple[str, str], bool] = PrivateAttr(default_factory=dict)

    def __len__(self) -> int:
        return len(self.libraries)

    def __iter__(self) -> Iterator[ResolvedLibrary]:  # type: ignore[override]
        return iter(self.libraries)

    def __contains__(self, nickname: str) -> bool:
        return self.get(nickname) is not None

    @property
    def nicknames(self) -> list[str]:
        """Every nickname, in display order."""
        return [library.nickname for library in self.libraries]

    def get(self, nickname: str) -> ResolvedLibrary | None:
        """Return the winning row for ``nickname``, or ``None``."""
        for library in self.libraries:
            if library.nickname == nickname:
                return library
        return None

    def search_order(self, *, include_disabled: bool = False) -> list[ResolvedLibrary]:
        """Return the rows an item search walks: project tier first, then global.

        The tier order copies the plugin load path
        (``library_manager.cpp:1543-1551``), which tries the project scope
        before the global scope. Order inside a tier is the display order.
        """
        project = [lib for lib in self.libraries if lib.scope == "project"]
        globals_ = [lib for lib in self.libraries if lib.scope == "global"]
        ordered = project + globals_
        if include_disabled:
            return ordered
        return [lib for lib in ordered if not lib.disabled]

    def find_library(
        self,
        item_name: str,
        *,
        include_disabled: bool = False,
    ) -> ResolvedLibrary | None:
        """Return the first library that holds ``item_name``, or ``None``.

        Only ``type "KiCad"`` rows are searched. A footprint library is a
        directory holding ``<name>.kicad_mod``. A symbol library is one
        ``.kicad_sym`` file that defines ``(symbol "<name>" ...)``. Other row
        types, such as ``Database``, need a query engine this library does not
        ship, so they never match (A2 report, OQ-3).

        KiCad never searches for a bare item name. The search order is library
        policy, stated in ADR-047.
        """
        if not item_name or ":" in item_name:
            return None

        for library in self.search_order(include_disabled=include_disabled):
            if library.type != KICAD_ROW_TYPE:
                continue
            if self._library_holds(library, item_name):
                return library
        return None

    def nickname_for(
        self,
        item_name: str,
        *,
        include_disabled: bool = False,
    ) -> str | None:
        """Return the nickname of the first library holding ``item_name``."""
        library = self.find_library(item_name, include_disabled=include_disabled)
        return None if library is None else library.nickname

    def scan_allowed(self, library: ResolvedLibrary) -> bool:
        """Report whether an item search may open the file behind *library*.

        The tier that supplied the row supplies the root set. A row outside its
        roots reads nothing and holds nothing, which keeps a confinement miss
        indistinguishable from an empty library (MEDIUM-2).
        """
        if library.read_roots is not None:
            return library.read_allowed
        roots = self.global_roots if library.scope == "global" else self.project_roots
        if roots is None:
            return True
        return path_within_roots(library.path, roots)

    def _library_holds(self, library: ResolvedLibrary, item_name: str) -> bool:
        key = (library.uri, item_name)
        cached = self._contains_cache.get(key)
        if cached is not None:
            return cached

        if not self.scan_allowed(library):
            self._contains_cache[key] = False
            return False

        if self.kind == "fp":
            found = _footprint_library_holds(library.path, item_name)
        else:
            found = _symbol_library_holds(library.path, item_name)

        self._contains_cache[key] = found
        return found


def _footprint_library_holds(directory: Path, item_name: str) -> bool:
    """Report whether a ``.pretty`` directory holds ``<item_name>.kicad_mod``."""
    if _has_path_separator(item_name):
        return False
    try:
        return (directory / f"{item_name}{_FOOTPRINT_SUFFIX}").is_file()
    except OSError:
        return False


def symbol_library_files(path: Path) -> list[Path]:
    """Return the ``.kicad_sym`` files behind a symbol library, in read order.

    KiCad 10 accepts two shapes for a ``type "KiCad"`` symbol library
    (``eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_lib_cache.cpp:60-63``,
    ``:75-108``, ``:110-200``):

    - one ``.kicad_sym`` file holding every definition, and
    - a DIRECTORY holding one ``.kicad_sym`` per definition, which KiCad globs
      with ``*.kicad_sym`` and parses into a single shared name map.

    KiCad decides by asking the filesystem whether the path is a directory, not
    by the spelling of the URI (``:60-63``). This does the same.

    The folder glob is NOT recursive, which copies ``wxDir::GetFirst`` with a
    file spec. Every in-house symbol library measured here uses the folder shape.

    Two deviations from KiCad, both recorded in ADR-099:

    1. The file list is sorted. KiCad takes the order the filesystem hands it,
       which is neither specified nor stable across platforms. The order is
       only visible when two files define the same name, where the later file
       wins in KiCad; sorting makes that outcome reproducible.
    2. The count is capped at :data:`MAX_LIBRARY_FOLDER_FILES`. KiCad reads the
       whole directory.

    A path that is neither a readable directory nor an existing file returns an
    empty list, which keeps a missing library indistinguishable from an empty
    one on the read side.
    """
    try:
        if path.is_dir():
            files = sorted(
                entry
                for entry in path.iterdir()
                if entry.suffix == _SYMBOL_SUFFIX and entry.is_file()
            )
            return files[:MAX_LIBRARY_FOLDER_FILES]
        if path.is_file():
            return [path]
    except OSError:
        return []
    return []


def _text_holds_symbol(path: Path, pattern: re.Pattern[str]) -> bool:
    try:
        text = read_text_capped(path, MAX_LIBRARY_SCAN_BYTES)
    except (OSError, UnicodeDecodeError):
        return False
    return pattern.search(text) is not None


def _symbol_library_holds(path: Path, item_name: str) -> bool:
    """Report whether a symbol library defines ``(symbol "<item_name>")``.

    Both library shapes are searched, per :func:`symbol_library_files`.

    The scan is a text match, not a parse, because a nickname search may touch
    every library in the merged table. A unit inside a definition is named
    ``<name>_<unit>_<style>``, so an exact quoted-name match reaches the
    top-level definition only.

    A folder-form library gets one extra read first: the file named after the
    item. That is where a one-part-per-file library puts it, so the common case
    costs one read instead of one per part. The fallback still scans every
    file, because KiCad takes the name from inside the file and never from the
    file name.
    """
    pattern = re.compile(rf'\(\s*symbol\s+"{re.escape(item_name)}"')
    files = symbol_library_files(path)
    if len(files) > 1 and not _has_path_separator(item_name):
        candidate = path / f"{item_name}{_SYMBOL_SUFFIX}"
        if candidate in files and _text_holds_symbol(candidate, pattern):
            return True
    return any(_text_holds_symbol(file, pattern) for file in files)


def _has_path_separator(name: str) -> bool:
    return "/" in name or "\\" in name or name in {".", ".."}


class LibTableResolver(KicadModel):
    """Discovers, loads, and merges the global and project library tables.

    Build one with :meth:`discover`, then call :meth:`resolve`. The resolver
    reads outside the repository, under the user settings directory and
    wherever a nested-table row points, so it carries the guards ADR-047
    documents: a depth cap, a table-file budget, a per-file read cap, and a
    root confinement that is on by default.

    Confinement covers every file the resolver opens, table files and scanned
    library files alike. :meth:`default_roots` states the default per tier, and
    ``allowed_roots`` widens it. A file outside the roots follows the skip
    policy, so an ordinary resolve keeps going and ``strict=True`` raises.
    """

    settings_dir: Path | None = None
    project_dir: Path | None = None
    kicad_root: Path | None = None
    environment: LibTableEnvironment = Field(default_factory=LibTableEnvironment)
    strict: bool = False
    max_depth: int = MAX_NESTED_TABLE_DEPTH
    max_tables: int = MAX_TABLE_FILES
    allowed_roots: list[Path] | None = None
    trust_path_variables: bool = True
    trust_table_chain: bool = True

    _chain_roots: list[Path] = PrivateAttr(default_factory=list)
    _skipped: list[SkippedLibTable] = PrivateAttr(default_factory=list)

    @classmethod
    def discover(
        cls,
        *,
        project_dir: Path | str | None = None,
        settings_dir: Path | str | None = None,
        kicad_root: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        path_variables: Mapping[str, str] | None = None,
        project_text_variables: Mapping[str, str] | None = None,
        version: str = KICAD_SETTINGS_VERSION,
        strict: bool = False,
        allowed_roots: Sequence[Path | str] | None = None,
        trust_path_variables: bool = True,
        trust_table_chain: bool = True,
    ) -> Self:
        """Build a resolver for one project directory.

        Args:
            project_dir: The directory holding the ``.kicad_pro`` file. Its
                sibling tables form the project tier, and it becomes
                ``KIPRJMOD``. ``None`` resolves the global tier alone.
            settings_dir: The user settings directory. Defaults to the KiCad
                formula, ``KICAD_CONFIG_HOME`` or ``%APPDATA%/kicad``, plus the
                version subdirectory. Pass ``False``-like ``None`` to keep the
                default; tests pass a temporary directory.
            kicad_root: The KiCad install root that seeds the ``KICAD10_*``
                defaults. Detected from the install location when omitted.
            env: The process environment. Defaults to ``os.environ``.
            path_variables: Explicit caller-approved path values, applied
                after saved and process values. The project directory still
                sets KIPRJMOD. Project text variables retain precedence.
                Existing local directories can become global read roots when
                trust_path_variables is true. UNC and URI restrictions remain.
            project_text_variables: ``.kicad_pro`` text variables, when the
                caller has them. They outrank environment variables.
            version: The settings and install version, ``10.0`` by default.
            strict: Raise instead of skipping an unreadable, malformed, or
                cyclic table.
            allowed_roots: Extra directories the resolver may read. They widen
                the per-tier default that :meth:`default_roots` states. They
                never narrow it. Name a UNC root here to allow a UNC URI.
            trust_path_variables: Add every KiCad path variable that names an
                existing local directory to the GLOBAL tier's read
                confinement, which is the default. Set it false for the
                pre-0.19.0 behaviour, where only the KiCad install root, the
                settings directory and the project directory were readable.
                ADR-047 amendment 3 records the change and why the default is
                on. ``project_text_variables`` is never read as a root, per
                amendment 5.
            trust_table_chain: Follow a nested table row of the GLOBAL tier to
                a local directory outside the roots, and read that directory,
                which is the default. The global tables live in the user's own
                settings directory, so a row in them is user configuration, the
                same standing a ``kicad_common.json`` variable has. The project
                tier is NOT widened, and a UNC path or a remote URI is still
                refused on both tiers. ADR-047 amendment 4.
        """
        resolved_settings = (
            default_user_settings_dir(version=version, env=env)
            if settings_dir is None
            else Path(settings_dir)
        )
        resolved_root = (
            default_kicad_install_root(version=version, env=env)
            if kicad_root is None
            else Path(kicad_root)
        )
        resolved_project = None if project_dir is None else Path(project_dir)

        environment = LibTableEnvironment.discover(
            settings_dir=resolved_settings,
            project_dir=resolved_project,
            kicad_root=resolved_root,
            env=env,
            path_variables=path_variables,
            project_text_variables=project_text_variables,
            strict=strict,
        )

        return cls(
            settings_dir=resolved_settings,
            project_dir=resolved_project,
            kicad_root=resolved_root,
            environment=environment,
            strict=strict,
            allowed_roots=(
                None if allowed_roots is None else [Path(root) for root in allowed_roots]
            ),
            trust_path_variables=trust_path_variables,
            trust_table_chain=trust_table_chain,
        )

    def resolve(self, kind: LibTableKind) -> ResolvedLibTable:
        """Return the merged namespace for one table kind.

        The global tier loads first and the project tier second, so a project
        row of the same nickname wins (``library_manager.cpp:647-648``,
        ``:687``). A missing tier contributes nothing.
        """
        file_name = TABLE_FILE_NAMES[kind]
        budget = _Budget(remaining=self.max_tables)
        self._chain_roots.clear()
        self._skipped.clear()

        merged: dict[str, ResolvedLibrary] = {}
        order: list[str] = []

        for scope, directory in self._tiers():
            libraries = self._load_table(
                path=directory / file_name,
                kind=kind,
                scope=scope,
                depth=0,
                seen=set(),
                budget=budget,
            )
            for library in libraries:
                if library.nickname not in merged:
                    order.append(library.nickname)
                merged[library.nickname] = library

        global_roots = self.roots("global")
        project_roots = self.roots("project")
        confined = [
            merged[name].model_copy(
                update={
                    "read_roots": (
                        global_roots if merged[name].scope == "global" else project_roots
                    )
                }
            )
            for name in order
        ]

        return ResolvedLibTable(
            kind=kind,
            libraries=confined,
            global_roots=global_roots,
            project_roots=project_roots,
            skipped=list(self._skipped),
        )

    def resolve_footprints(self) -> ResolvedLibTable:
        """Return the merged ``fp-lib-table`` namespace."""
        return self.resolve("fp")

    def resolve_symbols(self) -> ResolvedLibTable:
        """Return the merged ``sym-lib-table`` namespace."""
        return self.resolve("sym")

    def default_roots(self, scope: LibTableScope) -> list[Path]:
        """Return the confinement a tier gets before ``allowed_roots`` widens it.

        The global tier reads the KiCad install root and the user settings
        directory, which is where the stock chain lives. The project tier reads
        the project directory.

        The GLOBAL tier also reads every KiCad path variable that names an
        existing local directory, unless ``trust_path_variables`` is false.
        Those variables come from the user's own ``kicad_common.json`` and the
        compiled-in defaults, so the confinement grants exactly what the user's
        KiCad configuration grants and nothing more. Without them a table row
        pointing at ``${PARTS_PATH}`` resolved and then read nothing, silently:
        223 libraries instead of 245 on the measuring machine. ADR-047
        amendment 3.

        **The PROJECT tier gets the project directory and nothing else.** A
        project is often a repository the caller did not write. Widening it
        from a variable would let the project name its own read root, which is
        MEDIUM-2 of the iteration-11 security review; the iteration-20 review
        found that exact hole here and this is the fix. A project that needs a
        wider root passes ``allowed_roots``.

        A UNC root and a remote URI are still refused, so a chain that leaves
        the local filesystem needs an explicit ``allowed_roots`` entry.
        """
        if scope != "global":
            return [] if self.project_dir is None else [self.project_dir]

        roots = [root for root in (self.kicad_root, self.settings_dir) if root is not None]
        if self.trust_path_variables:
            roots.extend(self.environment.directory_roots())
        return roots

    def roots(self, scope: LibTableScope) -> list[Path]:
        """Return the full root set for a tier.

        The per-tier default, then ``allowed_roots``, then, for the GLOBAL
        tier only, any directory a trusted global table chain has led to
        during this resolve (see ``trust_table_chain``). The last group is
        empty until a resolve runs.

        A chain root is deliberately invisible to the PROJECT tier. Only the
        global tier can create one, and letting the project tier SEE one
        would hand a project directory a root it did not earn. The
        iteration-20 QA review found that leak; amendment 4 had always said
        the project tier is not widened.
        """
        chain = self._chain_roots if scope == "global" else ()
        return [
            *self.default_roots(scope),
            *(self.allowed_roots or ()),
            *chain,
        ]

    def _tiers(self) -> list[tuple[LibTableScope, Path]]:
        tiers: list[tuple[LibTableScope, Path]] = []
        if self.settings_dir is not None:
            tiers.append(("global", self.settings_dir))
        if self.project_dir is not None:
            tiers.append(("project", self.project_dir))
        return tiers

    def _load_table(
        self,
        *,
        path: Path,
        kind: LibTableKind,
        scope: LibTableScope,
        depth: int,
        seen: set[Path],
        budget: _Budget,
    ) -> list[ResolvedLibrary]:
        """Load one table file and splice its nested tables into a flat list."""
        if depth > self.max_depth:
            raise LibTableResolutionError(
                f"Nested library tables exceed the depth cap of {self.max_depth}: {path}"
            )

        if not self._table_allowed(path, scope):
            return []

        resolved = _resolve_path(path)
        seen.add(resolved)

        table = self._read_table(resolved, scope)
        if table is None:
            return []
        if table.kind != kind:
            return self._skip(
                f"Library table is a {table.kind} table, expected {kind}: {resolved}",
                path=resolved,
                scope=scope,
            )

        budget.take(resolved)

        libraries: list[ResolvedLibrary] = []

        for row in table.rows:
            if row.type == TABLE_TYPE_NAME:
                if row.disabled:
                    continue
                candidate = self._nested_path(row.uri, parent=resolved)
                if not self._table_allowed(candidate, scope, chain_trusted=True):
                    continue
                child_path = _resolve_path(candidate)
                if child_path in seen:
                    self._skip(
                        f"Nested library table already loaded, cycle broken: {child_path}",
                        path=child_path,
                        scope=scope,
                    )
                    continue
                self._trust_chain_directory(child_path, scope)
                children = self._load_table(
                    path=child_path,
                    kind=kind,
                    scope=scope,
                    depth=depth + 1,
                    seen=seen,
                    budget=budget,
                )
                if row.hidden:
                    children = [child.model_copy(update={"hidden": True}) for child in children]
                libraries.extend(children)
            else:
                libraries.append(
                    ResolvedLibrary(
                        nickname=row.name,
                        type=row.type,
                        uri=self.environment.expand(row.uri),
                        raw_uri=row.uri,
                        options=row.options,
                        descr=row.descr,
                        disabled=row.disabled,
                        hidden=row.hidden,
                        scope=scope,
                        source_table=resolved,
                    )
                )

        return libraries

    def _trust_chain_directory(self, table_path: Path, scope: LibTableScope) -> None:
        """Make the directory holding a trusted nested table a read root.

        A nested table names its libraries relative to itself: the operator's
        ``provisional_sym_table`` sits in
        a shared library folder and names 22 libraries under
        ``.../Symbols/``. Reading the table and then refusing every library it
        names would leave 22 empty nicknames, which is worse than dropping the
        table. So the table's own directory joins the roots for the rest of
        this resolve, and it lands on the resolved table's root list, which is
        what an item search reads under (MEDIUM-2).

        Global tier only, and never for a UNC path or a remote URI.
        """
        if not self.trust_table_chain or scope != "global":
            return
        text = str(table_path)
        if is_unc_path(text) or has_uri_scheme(text):
            return
        directory = _resolve_path(table_path.parent)
        if directory not in self._chain_roots:
            self._chain_roots.append(directory)

    def _nested_path(self, raw_uri: str, *, parent: Path) -> Path:
        """Expand a nested-table URI and make it absolute against the parent table.

        The result is not resolved. The confinement check runs first, because
        resolving a UNC path opens a network session on Windows (LOW-5).
        """
        expanded = self.environment.expand(raw_uri)
        candidate = Path(expanded)
        if is_remote_uri(expanded) or candidate.is_absolute():
            return candidate
        return parent.parent / candidate

    def _read_table(self, path: Path, scope: LibTableScope = "global") -> LibTable | None:
        """Read one table file, or return ``None`` when the policy skips it."""
        try:
            text = read_text_capped(path, MAX_TABLE_FILE_BYTES)
        except FileNotFoundError:
            self._skip(f"No library table at {path}", path=path, scope=scope)
            return None
        except (OSError, UnicodeDecodeError) as exc:
            self._skipped.append(
                SkippedLibTable(path=path, scope=scope, reason=f"Cannot read library table: {exc}")
            )
            if self.strict:
                raise LibTableResolutionError(f"Cannot read library table: {path}") from exc
            return None

        try:
            return LibTable.from_string(text)
        except KicadParseError as exc:
            self._skipped.append(
                SkippedLibTable(path=path, scope=scope, reason=f"Malformed library table: {exc}")
            )
            if self.strict:
                raise LibTableResolutionError(f"Malformed library table: {path}") from exc
            return None

    def _skip(
        self,
        message: str,
        *,
        path: Path | None = None,
        scope: LibTableScope = "global",
    ) -> list[ResolvedLibrary]:
        """Apply the skip policy: record it, and raise under ``strict``.

        Recording is not optional. A skip subtracts from the namespace, and a
        silent subtraction is the fault class this argument exists to end. The
        record lands on :attr:`ResolvedLibTable.skipped`.
        """
        self._skipped.append(
            SkippedLibTable(path=Path("") if path is None else path, scope=scope, reason=message)
        )
        if self.strict:
            raise LibTableResolutionError(message)
        return []

    def _table_allowed(
        self,
        path: Path,
        scope: LibTableScope,
        *,
        chain_trusted: bool = False,
    ) -> bool:
        """Apply the confinement to one table file, under the skip policy.

        ``chain_trusted`` marks a nested row reached from a table that was
        itself allowed. On the GLOBAL tier that is enough to read it, because
        the global tables are the user's own configuration: see
        ``trust_table_chain``. A UNC path and a remote URI are refused
        regardless, because trusting one by default would undo MEDIUM-1 of the
        iteration-11 security review.
        """
        if path_within_roots(path, self.roots(scope)):
            return True

        if chain_trusted and self.trust_table_chain and scope == "global":
            text = str(path)
            if not is_unc_path(text) and not has_uri_scheme(text):
                return True

        self._skip(
            f"Library table lies outside the allowed roots, so it and every library it "
            f"names were dropped: {path}. Widen the read confinement with "
            f"allowed_roots=[...] naming the directory that holds it.",
            path=path,
            scope=scope,
        )
        return False


class _Budget(KicadModel):
    """Counts the table files one resolve call reads."""

    remaining: int

    def take(self, path: Path) -> None:
        if self.remaining <= 0:
            raise LibTableResolutionError(
                f"Library-table chain exceeds the {MAX_TABLE_FILES} file budget at {path}"
            )
        self.remaining -= 1


def _resolve_path(path: Path) -> Path:
    """Normalise a path and follow symlinks, without requiring it to exist."""
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def resolve_lib_table(
    kind: LibTableKind,
    *,
    project_dir: Path | str | None = None,
    settings_dir: Path | str | None = None,
    kicad_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    path_variables: Mapping[str, str] | None = None,
    project_text_variables: Mapping[str, str] | None = None,
    version: str = KICAD_SETTINGS_VERSION,
    strict: bool = False,
    allowed_roots: Sequence[Path | str] | None = None,
    trust_path_variables: bool = True,
    trust_table_chain: bool = True,
) -> ResolvedLibTable:
    """Discover, merge, and return one library-table namespace.

    This is the one-call form of :meth:`LibTableResolver.discover` followed by
    :meth:`LibTableResolver.resolve`. See :meth:`LibTableResolver.discover` for
    every argument.
    """
    resolver = LibTableResolver.discover(
        project_dir=project_dir,
        settings_dir=settings_dir,
        kicad_root=kicad_root,
        env=env,
        path_variables=path_variables,
        project_text_variables=project_text_variables,
        version=version,
        strict=strict,
        allowed_roots=allowed_roots,
        trust_path_variables=trust_path_variables,
        trust_table_chain=trust_table_chain,
    )
    return resolver.resolve(kind)


def qualify_item_name(
    item_name: str,
    *,
    lib_nickname: str | None = None,
    lib_table: ResolvedLibTable | None = None,
    placeholder: str = PLACEHOLDER_NICKNAME,
) -> str:
    """Return ``item_name`` qualified with a library nickname.

    Precedence, per R4 and ADR-047:

    1. A pre-qualified ``lib:name`` passes through untouched.
    2. An explicit ``lib_nickname`` wins over everything discovered.
    3. A ``lib_table`` match supplies the KiCad nickname.
    4. The ``eedesigncore`` placeholder covers the no-match case (ADR-041
       amendment 1 supersedes amendment 2 as the default path).
    """
    if ":" in item_name:
        return item_name
    if lib_nickname:
        return f"{lib_nickname}:{item_name}"
    if lib_table is not None:
        nickname = lib_table.nickname_for(item_name)
        if nickname:
            return f"{nickname}:{item_name}"
    return f"{placeholder}:{item_name}"
