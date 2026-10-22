"""In-memory assembly exploration with optional native design snapshots."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import cast

from eedesigncore.assembly.models import NetbomAssembly
from eedesigncore.inspection.assembly import AssemblyInspector
from eedesigncore.inspection.native import inspect_design
from eedesigncore.inspection.portable import DesignInspector, Object, _hash
from eedesigncore.netbom.builder import _load_inputs
from eedesigncore.netbom.models import NetbomDocument
from eedesigncore.pcb.models import Board
from eedesigncore.pro.models import ProjectFile
from eedesigncore.project.hierarchy import SheetHierarchy
from eedesigncore.render.symbols import SymbolFont
from eedesigncore.sch.models import Schematic
from eedesigncore.schematic_view.assembly import AssemblyStack, prepare_assembly_view
from eedesigncore.schematic_view.models import SchematicView, ViewBounds
from eedesigncore.schematic_view.native import prepare_schematic_view


class NativeAssemblyInspector(AssemblyInspector):
    """System inspection with board-qualified native queries where attached."""

    def __init__(self, assembly: NetbomAssembly, native: dict[str, DesignInspector]) -> None:
        self._native: dict[str, DesignInspector] = {}
        super().__init__(cast(Object, assembly.model_dump(mode="json")))
        self._native = native
        self._revision = _hash(
            {
                "assembly": self._revision,
                "native": {key: value._revision for key, value in native.items()},
            }
        )
        self._pager._revision = self._revision

    def _board(self, instance: str) -> DesignInspector:
        original = super()._board(instance)
        return self._native.get(instance, original)


class AssemblyWorkspace:
    """Own assembly inputs and native snapshots without saving source designs."""

    def __init__(self, assembly: NetbomAssembly) -> None:
        assembly.inspect()
        self.assembly = assembly.model_copy(deep=True)
        self._native: dict[str, DesignInspector] = {}
        self._schematics: dict[str, Schematic | SheetHierarchy] = {}
        self._design_keys: dict[str, str] = {}

    def add_design(
        self,
        instance: str,
        source: NetbomDocument | Board | Schematic | SheetHierarchy,
        *,
        schematic: Schematic | SheetHierarchy | None = None,
        project: ProjectFile | None = None,
        design_name: str = "design",
    ) -> None:
        """Add a design or attach matching native data to an existing occurrence."""
        source = source.model_copy(deep=True)
        schematic = schematic.model_copy(deep=True) if schematic is not None else None
        project = project.model_copy(deep=True) if project is not None else None
        inspector = inspect_design(
            source, schematic=schematic, project=project, design_name=design_name
        )
        document = NetbomDocument.model_validate(inspector._document)
        key = "design:" + _hash(inspector._document)
        if instance in self.assembly.instances:
            if self.assembly.instances[instance] != key:
                raise ValueError("Native design does not match the assembly snapshot.")
        else:
            self.assembly.add_board(instance, document)
        if isinstance(source, NetbomDocument):
            return
        self._design_keys[instance] = key
        inspector._revision = _hash(
            {
                "source": source.model_dump(mode="json", exclude={"project"}),
                "source_project": source.project.to_string()
                if isinstance(source, SheetHierarchy) and source.project
                else None,
                "schematic": schematic.model_dump(mode="json", exclude={"project"})
                if schematic
                else None,
                "schematic_project": schematic.project.to_string()
                if isinstance(schematic, SheetHierarchy) and schematic.project
                else None,
                "project": project.to_string() if project else None,
            }
        )
        self._native[instance] = inspector
        native_schematic = source if isinstance(source, Schematic | SheetHierarchy) else schematic
        if native_schematic is not None:
            self._schematics[instance] = native_schematic

    def open_design(self, instance: str, path: str | Path) -> None:
        """Load a confined native project snapshot and attach it to this assembly."""
        source, board, hierarchy, project = _load_inputs(path)
        if board is not None:
            self.add_design(
                instance, board, schematic=hierarchy, project=project, design_name=source.stem
            )
        elif hierarchy is not None:
            self.add_design(instance, hierarchy, project=project, design_name=source.stem)
        else:
            raise ValueError("No native board or schematic exists at the selected path.")

    def _check_sources(self) -> None:
        for instance, key in self._design_keys.items():
            if self.assembly.instances.get(instance) != key:
                raise ValueError(
                    "An attached assembly occurrence changed. Create a new workspace."
                )

    def inspect(self) -> NativeAssemblyInspector:
        """Create an independent system snapshot with native board-query access."""
        self._check_sources()
        return NativeAssemblyInspector(self.assembly, copy.deepcopy(self._native))

    def schematic_view(
        self,
        *,
        stacks: list[AssemblyStack] | None = None,
        font: SymbolFont | None = None,
        sheet_bounds: dict[str, dict[str, ViewBounds]] | None = None,
    ) -> SchematicView:
        """Generate the root and attached source sheets without reading files again."""
        self._check_sources()
        views = {
            instance: prepare_schematic_view(
                source,
                document_id=self.assembly.assembly_id,
                instance=instance,
                font=font,
                sheet_bounds=(sheet_bounds or {}).get(instance),
            )
            for instance, source in self._schematics.items()
        }
        return prepare_assembly_view(self.assembly, native_views=views, stacks=stacks)
