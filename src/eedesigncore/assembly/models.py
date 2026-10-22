"""Editable, standalone netbom assemblies with reusable connector mate definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from pydantic import ConfigDict, Field

from eedesigncore.common.base import KicadModel
from eedesigncore.inspection.assembly import AssemblyInspector
from eedesigncore.inspection.portable import Json, Object, _dump, _hash, _json, _object, _read
from eedesigncore.netbom.models import NetbomDocument


class AssemblyEvidence(KicadModel):
    """Caller-supplied source location for a mate or pin-mapping definition."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1)
    locator: str
    revision: str | None = None
    derivation: Literal["specified", "inferred"] = "specified"
    method: str | None = None


class ConnectorEndpoint(KicadModel):
    """A board instance and its board-local connector component identifier."""

    model_config = ConfigDict(extra="forbid", strict=True)
    instance: str = Field(min_length=1, max_length=256)
    component_id: str


class ConnectorPinPair(KicadModel):
    """Pin numbers on the left and right connectors of a mate definition."""

    model_config = ConfigDict(extra="forbid", strict=True)
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class ConnectorMateDefinition(KicadModel):
    """An oriented, one-to-one connector pin map with its source evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)
    pin_pairs: list[ConnectorPinPair] = Field(min_length=1, max_length=4096)
    evidence: AssemblyEvidence


class ConnectorMate(KicadModel):
    """An identified pair of mated connectors using a shared definition."""

    model_config = ConfigDict(extra="forbid", strict=True)
    definition: str
    left: ConnectorEndpoint
    right: ConnectorEndpoint
    evidence: AssemblyEvidence


class NetbomAssembly(KicadModel):
    """Assembly inputs. Resolved system nets are recalculated from these saved facts."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["netbom-assembly-1"] = "netbom-assembly-1"
    assembly_id: str = Field(min_length=1, max_length=256)
    name: str = "assembly"
    designs: dict[str, NetbomDocument] = Field(default_factory=dict)
    instances: dict[str, str] = Field(default_factory=dict)
    mate_definitions: dict[str, ConnectorMateDefinition] = Field(default_factory=dict)
    mates: dict[str, ConnectorMate] = Field(default_factory=dict)
    expected_connectors: list[ConnectorEndpoint] = Field(default_factory=list)

    def inspect(self) -> AssemblyInspector:
        """Validate inputs and resolve a read-only system snapshot."""
        return AssemblyInspector(cast(Object, self.model_dump(mode="json")))

    def add_board(self, instance: str, netbom: NetbomDocument) -> str:
        """Add a distinct board occurrence and retain one copy of identical design data."""
        if instance in self.instances:
            raise ValueError("Board instance already exists. Use replace_board explicitly.")
        return self._store_board(instance, netbom)

    def replace_board(self, instance: str, netbom: NetbomDocument) -> str:
        """Replace one occurrence. Stale connector identifiers become inspection issues."""
        if instance not in self.instances:
            raise ValueError("Unknown board instance.")
        return self._store_board(instance, netbom)

    def _store_board(self, instance: str, netbom: NetbomDocument) -> str:
        if not isinstance(instance, str) or not 1 <= len(instance) <= 256:
            raise ValueError("Board instance identifiers require 1 through 256 characters.")
        netbom.validate_links()
        data = cast(Object, netbom.model_dump(mode="json"))
        from eedesigncore.inspection.portable import DesignInspector

        DesignInspector(data)
        key = "design:" + _hash(data)
        if len(self.instances) >= 1024 and instance not in self.instances:
            raise ValueError("Assembly exceeds the board instance limit.")
        retained = set(self.instances.values())
        if (
            instance in self.instances
            and list(self.instances.values()).count(self.instances[instance]) == 1
        ):
            retained.discard(self.instances[instance])
        if len(retained | {key}) > 256:
            raise ValueError("Assembly exceeds the distinct design limit.")
        if key not in self.designs:
            self.designs[key] = netbom.model_copy(deep=True)
        self.instances[instance] = key
        self._prune_designs()
        return key

    def remove_board(self, instance: str) -> None:
        """Remove an unused instance. Remove its mates and expectations first."""
        if instance not in self.instances:
            raise ValueError("Unknown board instance.")
        if any(
            instance in (mate.left.instance, mate.right.instance) for mate in self.mates.values()
        ) or any(endpoint.instance == instance for endpoint in self.expected_connectors):
            raise ValueError("Remove this board's mates and expected connectors first.")
        del self.instances[instance]
        self._prune_designs()

    def _prune_designs(self) -> None:
        used = set(self.instances.values())
        self.designs = {key: value for key, value in self.designs.items() if key in used}

    def connector(self, instance: str, reference: str) -> ConnectorEndpoint:
        """Resolve an exact connector reference without guessing among duplicates."""
        if instance not in self.instances:
            raise ValueError("Unknown board instance.")
        design = self.designs[self.instances[instance]]
        matches = [
            key
            for key, record in design.records.items()
            if record.kind == "component" and record.data.get("reference") == reference
        ]
        if len(matches) != 1:
            raise ValueError(
                "Connector reference is missing or ambiguous. Use a component identifier."
            )
        return ConnectorEndpoint(instance=instance, component_id=matches[0])

    def add_mate_definition(self, identifier: str, definition: ConnectorMateDefinition) -> None:
        """Add a reusable mapping. Existing definitions require explicit replacement."""
        if identifier in self.mate_definitions:
            raise ValueError("Mate definition already exists.")
        candidate = self.model_copy(deep=False)
        candidate.mate_definitions = {
            **self.mate_definitions,
            identifier: definition.model_copy(deep=True),
        }
        candidate.inspect()
        self.mate_definitions = candidate.mate_definitions

    def replace_mate_definition(
        self, identifier: str, definition: ConnectorMateDefinition
    ) -> None:
        """Replace a shared mapping and recalculate its mate diagnostics."""
        if identifier not in self.mate_definitions:
            raise ValueError("Unknown mate definition.")
        candidate = self.model_copy(deep=False)
        candidate.mate_definitions = {
            **self.mate_definitions,
            identifier: definition.model_copy(deep=True),
        }
        candidate.inspect()
        self.mate_definitions = candidate.mate_definitions

    def mate_connectors(self, identifier: str, mate: ConnectorMate) -> None:
        """Add an identified mate. Unresolved wiring remains visible through issues."""
        if identifier in self.mates:
            raise ValueError("Mate identifier already exists.")
        candidate = self.model_copy(deep=False)
        candidate.mates = {**self.mates, identifier: mate.model_copy(deep=True)}
        candidate.inspect()
        self.mates = candidate.mates

    def remove_mate(self, identifier: str) -> None:
        """Remove a declared mate. The next inspection recalculates system nets."""
        if identifier not in self.mates:
            raise ValueError("Unknown connector mate.")
        del self.mates[identifier]

    def to_json(self) -> str:
        """Serialize validated assembly inputs deterministically without external files."""
        self.inspect()
        return _dump(cast(Json, self.model_dump(mode="json"))) + "\n"

    @classmethod
    def from_json(cls, text: str) -> NetbomAssembly:
        """Read bounded JSON and validate its embedded designs and assembly structure."""
        data = _object(_json(text))
        AssemblyInspector(data)
        result = cls.model_validate(data)
        result.inspect()
        return result

    def save(self, path: str | Path) -> None:
        """Prepare the complete output before writing a caller-selected local file."""
        raw = str(path)
        if raw.replace("\\", "/").startswith("//") or "://" in raw:
            raise ValueError("Assembly output requires a local file path.")
        text = self.to_json()
        Path(path).write_text(text, encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> NetbomAssembly:
        """Read a standalone assembly without resolving external source paths."""
        return cls.from_json(_read(path))
