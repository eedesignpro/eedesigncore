"""Standalone board-first electrical design descriptions."""

from eedesigncore.netbom.builder import build_netbom, load_netbom
from eedesigncore.netbom.models import (
    NetbomCoverage,
    NetbomDocument,
    NetbomEvidence,
    NetbomRecord,
)
from eedesigncore.netbom.source_evidence import NetbomSourceFile, NetbomSourceManifest

__all__ = [
    "NetbomSourceFile",
    "NetbomSourceManifest",
    "build_netbom",
    "load_netbom",
    "NetbomCoverage",
    "NetbomDocument",
    "NetbomEvidence",
    "NetbomRecord",
]
