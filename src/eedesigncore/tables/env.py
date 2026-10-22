"""Environment sources and URI expansion for KiCad library tables.

Authority: ``context/audit/lib-table-discovery-2026-07-28.md`` (the A2 report).
That report cites KiCad 10.0.3 for every rule this module implements:

- The user settings directory formula, ``common/paths.cpp:645-668``.
- The expander and its four syntax forms, ``common/common.cpp:360-585``.
- The predefined variable names, ``common/env_vars.cpp:37-46``.
- The compiled-in ``KICAD10_*`` defaults, which KiCad never writes to disk,
  ``common/settings/common_settings.cpp:833-865`` and ``:151-158``.
- ``KIPRJMOD``, set to the project directory with no trailing separator,
  ``common/settings/settings_manager.cpp:1020-1021``.

Three deviations from KiCad are deliberate. All appear in ADR-047.

1. The ``%NAME%`` form works on every platform. KiCad guards it with
   ``__WINDOWS__``. One behaviour on every host keeps the tests deterministic.
2. Project text variables are an input, not a discovery. They outrank
   environment variables (``common/common.cpp:459-468``) but live in the
   ``.kicad_pro`` file, which this library does not read yet.
3. Only KiCad-owned process variables expand. KiCad reads the whole environment
   (``common/common.cpp:464-468``). This library reads the ``KICAD*`` names plus
   ``KIPRJMOD``, ``KISYS3DMOD``, and ``KISYSMOD``, so a table row cannot expand
   a secret such as ``CI_JOB_TOKEN`` into a path. An unmatched reference keeps
   its text verbatim, brackets included, which is what KiCad does with a name it
   cannot resolve (``common/common.cpp:493-527``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Self

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.paths import has_uri_scheme, is_unc_path
from eedesigncore.exceptions import LibTableResolutionError

KICAD_SETTINGS_VERSION: Final = "10.0"
"""The major.minor settings directory name for KiCad 10 (``build_version.cpp:121-125``)."""

KICAD_MAJOR_VERSION: Final = 10
"""The major version that names the ``KICAD<major>_*`` variables (``env_vars.cpp:77-83``)."""

PROJECT_VAR_NAME: Final = "KIPRJMOD"
"""The project-directory variable (``include/project.h:41``)."""

COMMON_SETTINGS_FILE_NAME: Final = "kicad_common.json"
"""The user settings file that carries ``environment.vars``."""

MAX_SETTINGS_FILE_BYTES: Final = 16 * 1024 * 1024
"""Read cap for ``kicad_common.json``. A larger file raises instead of loading."""

MAX_EXPANSION_PASSES: Final = 8
"""Cap on expansion passes. KiCad re-passes under a loop guard (``common.cpp:577-582``)."""

KICAD_ENV_PREFIX: Final = "KICAD"
"""Prefix of every KiCad-owned process variable (``env_vars.cpp:36-45``, ``:143-146``)."""

KICAD_ENV_NAMES: Final = frozenset({PROJECT_VAR_NAME, "KISYS3DMOD", "KISYSMOD"})
"""The KiCad-owned names that do not carry the prefix (``env_vars.cpp:37``, ``:144-145``)."""

_NAME_CHARS: Final = "A-Za-z0-9_:"

_SCAN_RE: Final = re.compile(
    rf"""
      \\(?P<escaped>[%$])
    | \$\{{(?P<curly>[{_NAME_CHARS}]+)\}}
    | \$\((?P<normal>[{_NAME_CHARS}]+)\)
    | %(?P<windows>[{_NAME_CHARS}]+)%
    | \$(?P<bare>[{_NAME_CHARS}]+)
    """,
    re.VERBOSE,
)

_VERSIONED_RE: Final = re.compile(
    r"^KICAD(?P<version>\d*)_(?P<base>SYMBOL_DIR|FOOTPRINT_DIR|3DMODEL_DIR|3RD_PARTY)$"
)

_OPENING_BRACKETS: Final = "{(%"
_CLOSING_BRACKETS: Final = "})%"


def _as_posix(path: Path) -> str:
    """Render a path with forward slashes, the separator library-table URIs use."""
    return path.as_posix()


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def is_kicad_env_var(name: str) -> bool:
    """Report whether *name* is a KiCad-owned environment variable.

    KiCad names every variable it owns with the ``KICAD`` prefix, either
    versioned as ``KICAD10_SYMBOL_DIR`` or plain as ``KICAD_USER_TEMPLATE_DIR``
    (``common/env_vars.cpp:36-45``, ``:127-146``). Three owned names lack the
    prefix: ``KIPRJMOD`` (``:37``) and the deprecated aliases ``KISYS3DMOD`` and
    ``KISYSMOD`` (``:144-145``).
    """
    return name.startswith(KICAD_ENV_PREFIX) or name in KICAD_ENV_NAMES


def kicad_env_vars(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the KiCad-owned subset of the process environment.

    URI expansion reads this subset, never the whole environment. A library
    table is data, and a project directory is often a repository the caller does
    not own, so a row of the form ``${CI_JOB_TOKEN}`` must not reach a secret.
    Security review iteration 11, MEDIUM-1. ADR-047 amendment 1 records the
    ruling.
    """
    return {name: value for name, value in _environ(env).items() if is_kicad_env_var(name)}


def default_user_settings_dir(
    *,
    version: str = KICAD_SETTINGS_VERSION,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the KiCad user settings directory (``common/paths.cpp:645-668``).

    ``KICAD_CONFIG_HOME`` replaces the base when it is set and not empty. An
    empty value does not count as set. The version subdirectory is appended in
    either case, so KiCad 10.0.0 and 10.0.3 share one directory.

    On Windows the base is ``%APPDATA%/kicad``. On another platform this
    function uses ``XDG_CONFIG_HOME`` or ``~/.config`` plus ``kicad``, which
    approximates ``wxStandardPaths::GetUserConfigDir``. The A2 report audited
    Windows only (its OQ-4).
    """
    source = _environ(env)

    config_home = source.get("KICAD_CONFIG_HOME", "")
    if config_home:
        return Path(config_home) / version

    appdata = source.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "kicad" / version

    xdg = source.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "kicad" / version


def default_kicad_install_root(
    *,
    version: str = KICAD_SETTINGS_VERSION,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the KiCad install root, or ``None`` when no candidate exists.

    KiCad reads the root from the running executable
    (``common/paths.cpp:623-629``). No KiCad process runs here, so this
    function checks ``<ProgramFiles>/KiCad/<version>`` and the default
    ``C:/Program Files/KiCad/<version>``. Pass an explicit root when the
    install lives elsewhere.
    """
    source = _environ(env)

    candidates: list[Path] = []
    program_files = source.get("ProgramFiles", "")
    if program_files:
        candidates.append(Path(program_files) / "KiCad" / version)
    candidates.append(Path("C:/Program Files/KiCad") / version)

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def stock_data_dir(
    kicad_root: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the stock data directory, ``<root>/share/kicad``.

    ``KICAD_STOCK_DATA_HOME`` replaces it verbatim
    (``common/paths.cpp:251-254``).
    """
    override = _environ(env).get("KICAD_STOCK_DATA_HOME", "")
    if override:
        return Path(override)
    return kicad_root / "share" / "kicad"


def kicad_builtin_vars(
    kicad_root: Path | None,
    *,
    major: int = KICAD_MAJOR_VERSION,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the compiled-in ``KICAD<major>_*`` defaults for an install root.

    KiCad seeds these at startup and never writes them to disk
    (``common_settings.cpp:833-865``, ``:151-158``), so a Python resolver must
    carry its own copy. An empty result means the caller supplied no root.

    Every value drops the trailing separator KiCad's own getters carry. The
    separator normalises away in a path join, and dropping it keeps an expanded
    URI free of a doubled slash.

    ``KICAD_USER_TEMPLATE_DIR`` and ``KICAD<major>_3RD_PARTY`` are absent. Both
    derive from the user documents directory, which the A2 report left
    unverified (its OQ-5), and neither expands a symbol or footprint URI.
    """
    if kicad_root is None:
        return {}

    stock = stock_data_dir(kicad_root, env=env)
    return {
        f"KICAD{major}_SYMBOL_DIR": _as_posix(stock / "symbols"),
        f"KICAD{major}_FOOTPRINT_DIR": _as_posix(stock / "footprints"),
        f"KICAD{major}_3DMODEL_DIR": _as_posix(stock / "3dmodels"),
        f"KICAD{major}_TEMPLATE_DIR": _as_posix(stock / "template"),
        f"KICAD{major}_DESIGN_BLOCK_DIR": _as_posix(stock / "blocks"),
    }


def load_kicad_common_vars(path: Path, *, strict: bool = False) -> dict[str, str]:
    """Return the ``environment.vars`` object from a ``kicad_common.json`` file.

    KiCad pushes each stored variable into the process environment at startup
    (``common/pgm_base.cpp:511-535``), which is how a user-defined name such as
    ``MY_LIBRARY_ROOT`` reaches URI expansion. This library reads the file
    instead, because no KiCad process runs.

    A missing or malformed file yields an empty mapping. With ``strict`` set it
    raises :class:`LibTableResolutionError` instead. A file over
    :data:`MAX_SETTINGS_FILE_BYTES` always raises. Non-string values are
    dropped.
    """
    try:
        text = read_text_capped(path, MAX_SETTINGS_FILE_BYTES)
    except FileNotFoundError as exc:
        if strict:
            raise LibTableResolutionError(f"Settings file not found: {path}") from exc
        return {}
    except OSError as exc:
        if strict:
            raise LibTableResolutionError(f"Cannot read settings file: {path}") from exc
        return {}

    try:
        data = json.loads(text)
    except ValueError as exc:
        if strict:
            raise LibTableResolutionError(f"Malformed settings file: {path}") from exc
        return {}

    if not isinstance(data, dict):
        if strict:
            raise LibTableResolutionError(f"Settings file is not a JSON object: {path}")
        return {}

    environment = data.get("environment")
    if not isinstance(environment, dict):
        return {}
    variables = environment.get("vars")
    if not isinstance(variables, dict):
        return {}

    return {key: value for key, value in variables.items() if isinstance(value, str)}


def read_text_capped(path: Path, max_bytes: int) -> str:
    """Read a UTF-8 text file, refusing anything over ``max_bytes``.

    The cap guards the discovery paths that read outside the repository. It
    raises :class:`LibTableResolutionError` rather than allocating.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    size = path.stat().st_size
    if size > max_bytes:
        raise LibTableResolutionError(
            f"File is {size} bytes, over the {max_bytes} byte read cap: {path}"
        )
    return path.read_bytes().decode("utf-8")


class LibTableEnvironment(KicadModel):
    """The variable sources a library-table URI expands against.

    ``variables`` holds one merged mapping, lowest precedence first at build
    time: the compiled-in ``KICAD<major>_*`` defaults, then the
    ``kicad_common.json`` ``environment.vars`` object, then the KiCad-owned
    subset of the process environment, explicit caller path variables,
    then ``KIPRJMOD``. :meth:`discover`
    performs that merge.

    The process-environment source is filtered by :func:`is_kicad_env_var`. A
    variable such as ``CI_JOB_TOKEN`` therefore never expands, and a URI naming
    it stays verbatim. That deviates from KiCad, which reads the whole
    environment (``common/common.cpp:464-468``), and it closes MEDIUM-1 of the
    iteration-11 security review. A caller who needs another name supplies it
    through ``path_variables``, ``kicad_common.json``, or ``project_text_variables``.

    ``project_text_variables`` outranks everything in ``variables``, matching
    ``common/common.cpp:459-468``. This library cannot read the ``.kicad_pro``
    file that holds them yet, so a caller supplies them. Left empty, a URI that
    names a project text variable stays unexpanded. ADR-047 records the
    limitation.
    """

    variables: dict[str, str] = Field(default_factory=dict)
    project_text_variables: dict[str, str] = Field(default_factory=dict)
    major: int = KICAD_MAJOR_VERSION
    max_passes: int = MAX_EXPANSION_PASSES

    @classmethod
    def discover(
        cls,
        *,
        settings_dir: Path | None = None,
        project_dir: Path | None = None,
        kicad_root: Path | None = None,
        env: Mapping[str, str] | None = None,
        path_variables: Mapping[str, str] | None = None,
        project_text_variables: Mapping[str, str] | None = None,
        major: int = KICAD_MAJOR_VERSION,
        strict: bool = False,
    ) -> Self:
        """Build the merged environment for one project.

        Args:
            settings_dir: The KiCad user settings directory. Its
                ``kicad_common.json`` supplies the user-defined variables.
            project_dir: The directory holding the ``.kicad_pro`` file. It
                becomes ``KIPRJMOD``, with no trailing separator.
            kicad_root: The KiCad install root that seeds the
                ``KICAD<major>_*`` defaults.
            env: The process environment. Defaults to ``os.environ``. Only its
                KiCad-owned names reach the expansion map. Tests pass an
                explicit mapping.
            project_text_variables: Values read from a ``.kicad_pro``
                ``text_variables`` object, when the caller has them.
            path_variables: Caller-approved path variables. These override
                saved and process values. The project directory still sets
                KIPRJMOD. Project text variables retain their precedence.
            major: The version that names the versioned variables.
            strict: Raise on an unreadable or malformed ``kicad_common.json``
                instead of ignoring it. An absent file never raises, because a
                KiCad install with no user variables has none.
        """
        explicit: dict[str, str] = {}
        if path_variables is not None:
            if not isinstance(path_variables, Mapping):
                raise TypeError("path_variables must be a mapping of strings.")
            for name, value in path_variables.items():
                if not isinstance(name, str) or not isinstance(value, str):
                    raise TypeError("Path variable names and values must be strings.")
                if re.fullmatch(rf"[{_NAME_CHARS}]+", name) is None or "\x00" in value:
                    raise ValueError("Invalid path variable name or value.")
                explicit[name] = value
        merged: dict[str, str] = dict(kicad_builtin_vars(kicad_root, major=major, env=env))

        if settings_dir is not None:
            common_settings = settings_dir / COMMON_SETTINGS_FILE_NAME
            if common_settings.is_file():
                merged.update(load_kicad_common_vars(common_settings, strict=strict))

        merged.update(kicad_env_vars(env))
        merged.update(explicit)

        if project_dir is not None:
            merged[PROJECT_VAR_NAME] = _as_posix(project_dir)

        return cls(
            variables=merged,
            project_text_variables=dict(project_text_variables or {}),
            major=major,
        )

    def directory_roots(self) -> list[Path]:
        """Return every variable value that names an existing local directory.

        These are the directories the user's own KiCad configuration points its
        library tables at, so they are the directories a table is entitled to
        reach. :meth:`LibTableResolver.default_roots` adds them to the read
        confinement of the GLOBAL tier, which is what makes a library under
        ``${PARTS_PATH}`` readable with no ``allowed_roots`` argument (ADR-047
        amendment 3).

        **``project_text_variables`` is deliberately NOT read here.** Those
        values come from a ``.kicad_pro`` file, which is often a repository the
        caller did not write, and a project that can name a read root can name
        any directory on the disk — a drive root included — and then reach it
        with an ordinary table row. That is MEDIUM-2 of the iteration-11
        security review, and the iteration-20 security review found it reopened
        here. Only ``variables`` is read: the compiled-in ``KICAD<major>_*``
        defaults, ``kicad_common.json``, and the KiCad-owned subset of the
        process environment, plus explicit caller-approved path variables.
        A project that genuinely needs a wider root passes
        ``allowed_roots``, which is explicit and caller-reviewed.

        Four kinds of value are refused as well, so the widening cannot leave
        the local filesystem:

        - a value carrying an unexpanded ``$`` or ``%`` reference,
        - a URI with a scheme, such as ``https://``,
        - a UNC path, which still needs an explicit ``allowed_roots`` entry
          because trusting one by default would undo MEDIUM-1 of the
          iteration-11 security review, and
        - anything that is not an existing directory, which drops a file value
          such as a ``.kicad_dbl`` path and every stale entry.

        Duplicates are dropped on the resolved text.
        """
        roots: list[Path] = []
        seen: set[str] = set()

        for value in self.variables.values():
            if not value or "$" in value or "%" in value:
                continue
            if has_uri_scheme(value) or is_unc_path(value):
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                continue
            try:
                if not candidate.is_dir():
                    continue
                key = str(candidate.resolve(strict=False)).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            roots.append(candidate)

        return roots

    def lookup(self, name: str) -> str | None:
        """Return the value of one variable, or ``None`` when nothing matches.

        The order copies ``KIwxExpandEnvVars`` (``common/common.cpp:456-503``):
        project text variables, then the merged environment, then the
        versioned-wildcard fallback that lets a ``${KICAD9_SYMBOL_DIR}`` URI
        resolve under KiCad 10.
        """
        value = self.project_text_variables.get(name)
        if value is not None:
            return value

        value = self.variables.get(name)
        if value is not None:
            return value

        return self._versioned_fallback(name)

    def expand(self, text: str) -> str:
        """Expand every variable reference in ``text``.

        Four forms resolve: ``${NAME}``, ``$(NAME)``, ``%NAME%``, and ``$NAME``
        (``common/common.cpp:399-432``). A backslash before ``$`` or ``%``
        suppresses the special meaning and the backslash itself is dropped
        (``:561-569``). An unresolved reference stays in the text verbatim
        (``:493-527``). Expansion re-runs while brackets remain, under a loop
        guard and a pass cap (``:577-582``).

        One KiCad quirk carries over. An escaped bracket form such as
        ``\\${LIB}`` loses its backslash on the first pass, and the brackets
        left behind trigger a second pass that expands it after all. Only the
        bare form, ``\\$LIB``, stays literal.
        """
        current = text
        seen: set[str] = set()

        for _ in range(self.max_passes):
            if current in seen:
                break
            seen.add(current)

            expanded = _SCAN_RE.sub(self._substitute, current)
            if expanded == current:
                break
            current = expanded

            if not _needs_another_pass(current):
                break

        return current

    def _substitute(self, match: re.Match[str]) -> str:
        escaped = match.group("escaped")
        if escaped is not None:
            return escaped

        name = (
            match.group("curly")
            or match.group("normal")
            or match.group("windows")
            or match.group("bare")
        )
        value = self.lookup(name)
        if value is None:
            return match.group(0)
        return value

    def _versioned_fallback(self, name: str) -> str | None:
        base: str | None = None
        if "KISYS3DMOD" in name:
            base = "3DMODEL_DIR"
        else:
            match = _VERSIONED_RE.match(name)
            if match is not None:
                base = match.group("base")

        if base is None:
            return None

        current = self.variables.get(f"KICAD{self.major}_{base}")
        if current:
            return current

        pattern = re.compile(rf"^KICAD\d*_{re.escape(base)}$")
        for key, value in self.variables.items():
            if value and pattern.match(key):
                return value
        return None


def _needs_another_pass(text: str) -> bool:
    """Mirror the re-pass guard at ``common/common.cpp:578-581``."""
    first = min((text.find(char) for char in _OPENING_BRACKETS if char in text), default=-1)
    last = max((text.rfind(char) for char in _CLOSING_BRACKETS if char in text), default=-1)
    return first != -1 and last != -1 and first != last
