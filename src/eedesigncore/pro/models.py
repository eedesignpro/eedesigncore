"""Pydantic models for ``.kicad_pro`` and ``.kicad_prl``.

Both file types are JSON, so these models wrap a preserved
:class:`~eedesigncore.pro.tree.JsonTree` instead of rebuilding the document
from typed fields. The audit ``context/audit/json-save-path-2026-07-28.md``
section 9.2 requires that design: KiCad keeps every key it does not understand,
and a writer that dropped unknown keys would emit a smaller file than KiCad.

Preserve-only policy (ADR-049). KiCad's own load-then-write **grows** a project
file, because every registered parameter stores its default even when the key
was absent (audit section 6.4 and contradiction C5). This library deliberately
diverges. It emits exactly what it read, plus whatever the caller changed. That
divergence is what makes byte identity possible.
"""

from __future__ import annotations

from typing import ClassVar, Self

from pydantic import ConfigDict, Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.io_mixin import KicadFileMixin
from eedesigncore.exceptions import KicadParseError
from eedesigncore.pro.tree import JsonTree, JsonValue

# The structural marker that identifies a KiCad 10 save of a `.kicad_pro`.
# `meta.version` cannot do that job: KiCad 9.0.6 and KiCad 10.0.3 both use
# project schema version 3 and project-local schema version 5 (audit
# contradiction C3). `schematic.top_level_sheets` is a KiCad 10 parameter
# (`common/project/project_file.cpp:58`), and KiCad writes every registered
# parameter on save, so any KiCad 10 save carries it.
V10_MARKER_PATH = "schematic.top_level_sheets"

PRO_SCHEMA_VERSION = 3
PRL_SCHEMA_VERSION = 5


class MetaBlock(KicadModel):
    """A read-only view of the ``meta`` block of a project file.

    Both fields are None when the file omits them. ``meta`` is an ordinary
    sorted sibling of the other top-level keys, not a header, so it lands in the
    middle of the file (audit section 6.1).
    """

    model_config = ConfigDict(frozen=True)

    filename: str | None = None
    version: int | None = None


class ProjectJsonFile(KicadFileMixin):
    """Shared behaviour of the two JSON project files.

    The model holds one preserved tree. Typed accessors read through it, and
    :meth:`section` hands back a write-through view of a top-level section, so
    an edit reaches the tree that the writer serialises.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: JsonTree = Field(default_factory=JsonTree)

    schema_version: ClassVar[int]
    file_extension: ClassVar[str]

    @property
    def meta(self) -> MetaBlock:
        """The ``meta`` block. Absent keys read as None."""
        block = self.root.get_object("meta")
        if block is None:
            return MetaBlock()
        return MetaBlock(filename=block.get_str("filename"), version=block.get_int("version"))

    def section_names(self) -> list[str]:
        """Every top-level key, in the order the writer will emit them."""
        return sorted(self.root.keys())

    def section(self, name: str) -> JsonTree | None:
        """A write-through view of top-level section *name*, or None."""
        return self.root.get_object(name)

    def value_at(self, dotted_path: str) -> JsonValue:
        """The value at a dotted path such as ``pcbnew.last_paths.plot``."""
        return self.root.path(dotted_path)

    @classmethod
    def _from_text(cls, text: str) -> Self:
        from eedesigncore.pro.loader import load_project_json

        return load_project_json(cls, text)

    def _to_lf_string(self) -> str:
        from eedesigncore.pro.writer import dump_json

        return dump_json(self.root.entries)


class ProjectFile(ProjectJsonFile):
    """A parsed ``.kicad_pro`` project file.

    Schema version 3 (``common/project/project_file.cpp:39``).
    """

    schema_version: ClassVar[int] = PRO_SCHEMA_VERSION
    file_extension: ClassVar[str] = "kicad_pro"

    @property
    def has_v10_marker(self) -> bool:
        """True when the file carries the KiCad 10 structural marker.

        See :data:`V10_MARKER_PATH` for why ``meta.version`` cannot answer this.
        """
        return self.root.has_path(V10_MARKER_PATH)

    @property
    def text_variables(self) -> dict[str, str]:
        """The project text variables, empty when the file omits the block.

        This is the one subtree KiCad wipes and rewrites on save (audit section
        5.3), so it is also the one subtree a caller may safely replace whole.
        """
        block = self.root.get_object("text_variables")
        if block is None:
            return {}
        return {key: _require_str(block.get(key), f"text_variables.{key}") for key in block}

    @property
    def saved_netclass_assignments(self) -> dict[str, tuple[str, ...]] | None:
        """Read saved schematic assignments without applying project patterns.

        Return None when the section or assignment field is absent. A saved
        null or empty object means no saved schematic assignments. Class names
        are unique and sorted. The returned mapping is detached from the file.
        This read does not establish whether the files are synchronized.
        """
        settings = self.root.get_object("net_settings")
        if settings is None or "netclass_assignments" not in settings:
            return None
        assignments = settings.get_object("netclass_assignments")
        if assignments is None:
            return {}
        result: dict[str, tuple[str, ...]] = {}
        for net_name in assignments:
            path = f"net_settings.netclass_assignments.{net_name}"
            names = assignments.get(net_name)
            if not isinstance(names, list):
                raise KicadParseError(f"Expected a JSON array at {path!r}")
            result[net_name] = tuple(sorted({_require_str(name, path) for name in names}))
        return result

    @property
    def bus_aliases(self) -> dict[str, list[str]]:
        """Bus alias name to member list, empty when the file omits the block.

        KiCad 10 keeps bus aliases in the project file, not in the schematic
        (``common/project/project_file.cpp:117-166``).
        """
        block = self.root.get_object("schematic")
        aliases = None if block is None else block.get_object("bus_aliases")
        if aliases is None:
            return {}
        out: dict[str, list[str]] = {}
        for name in aliases:
            members = aliases.get_list(name) or []
            out[name] = [
                _require_str(member, f"schematic.bus_aliases.{name}") for member in members
            ]
        return out

    @property
    def top_level_sheets(self) -> list[JsonTree]:
        """The KiCad 10 top-level sheet entries, empty when absent."""
        entries = self.root.path(V10_MARKER_PATH)
        if not isinstance(entries, list):
            return []
        return [JsonTree(entry) for entry in entries if isinstance(entry, dict)]


class ProjectLocalSettings(ProjectJsonFile):
    """A parsed ``.kicad_prl`` project-local settings file.

    Schema version 5 (``common/project/project_local_settings.cpp:29``). The
    file registers no nested settings and no wiped subtree, so every key in it
    round-trips untouched.
    """

    schema_version: ClassVar[int] = PRL_SCHEMA_VERSION
    file_extension: ClassVar[str] = "kicad_prl"


def _require_str(value: JsonValue, path: str) -> str:
    if not isinstance(value, str):
        raise KicadParseError(f"Expected a JSON string at {path!r}")
    return value
