"""Shared native and assembly schematic pages for viewer consumers."""

from eedesigncore.schematic_view.assembly import AssemblyStack, prepare_assembly_view
from eedesigncore.schematic_view.models import (
    SchematicBus,
    SchematicCoverage,
    SchematicIssue,
    SchematicNet,
    SchematicPage,
    SchematicView,
    ViewBounds,
    ViewContact,
    ViewItem,
    ViewPinSelection,
    ViewSource,
    view_identity,
)
from eedesigncore.schematic_view.native import prepare_schematic_view
from eedesigncore.schematic_view.workspace import AssemblyWorkspace, NativeAssemblyInspector

__all__ = [
    "AssemblyStack",
    "AssemblyWorkspace",
    "NativeAssemblyInspector",
    "SchematicBus",
    "SchematicCoverage",
    "SchematicIssue",
    "SchematicNet",
    "SchematicPage",
    "SchematicView",
    "ViewBounds",
    "ViewContact",
    "ViewItem",
    "ViewPinSelection",
    "ViewSource",
    "prepare_assembly_view",
    "prepare_schematic_view",
    "view_identity",
]
