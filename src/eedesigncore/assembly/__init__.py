"""Netbom assembly construction and connector-defined system inspection."""

from eedesigncore.assembly.models import (
    AssemblyEvidence,
    ConnectorEndpoint,
    ConnectorMate,
    ConnectorMateDefinition,
    ConnectorPinPair,
    NetbomAssembly,
)
from eedesigncore.inspection.assembly import AssemblyInspector

__all__ = [
    "AssemblyEvidence",
    "AssemblyInspector",
    "ConnectorEndpoint",
    "ConnectorMate",
    "ConnectorMateDefinition",
    "ConnectorPinPair",
    "NetbomAssembly",
]
