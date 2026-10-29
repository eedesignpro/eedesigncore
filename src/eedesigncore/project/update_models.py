"""Controls and previews for schematic and board updates."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.mod.models import FootprintPlacement
from eedesigncore.pcb.models import Board
from eedesigncore.sch.models import Schematic


class PcbUpdateOptions(KicadModel):
    match_by_reference: bool = False
    replace_footprints: bool = True
    delete_extra_footprints: bool = False
    override_locks: bool = False
    update_fields: bool = True
    remove_extra_fields: bool = False
    transfer_symbol_groups: bool = False
    update_pad_nets: bool = True
    update_attributes: bool = True
    update_references: bool = True
    update_values: bool = True
    refresh_footprints: bool = False
    preserve_routed_contacts: bool = True


class SchematicUpdateOptions(KicadModel):
    match_by_reference: bool = False
    update_references: bool = True
    update_footprints: bool = True
    update_values: bool = True
    update_net_names: bool = True
    update_attributes: bool = True
    update_other_fields: bool = True
    prefer_unit_swaps: bool = True
    prefer_pin_swaps: bool = True
    allow_unconstrained_pin_swaps: bool = False


class DesignUpdateChange(KicadModel):
    kind: str
    reference: str
    identity: str
    before: str | None = None
    after: str | None = None


class DesignUpdateIssue(KicadModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    identity: str = ""


class PcbUpdatePlan(KicadModel):
    source_signature: str
    board_signature: str
    library_signatures: dict[str, str]
    options: PcbUpdateOptions
    placements: dict[str, FootprintPlacement]
    uuid_seed: str
    changes: list[DesignUpdateChange]
    issues: list[DesignUpdateIssue]
    result_signature: str

    @property
    def blockers(self) -> list[DesignUpdateIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


class PcbUpdateResult(KicadModel):
    board: Board
    changes: list[DesignUpdateChange] = Field(default_factory=list)
    issues: list[DesignUpdateIssue] = Field(default_factory=list)


class SchematicUpdatePlan(KicadModel):
    source_signature: str
    board_signature: str
    options: SchematicUpdateOptions
    uuid_seed: str
    changes: list[DesignUpdateChange]
    issues: list[DesignUpdateIssue]
    result_signatures: dict[str, str]
    result_board_signature: str

    @property
    def blockers(self) -> list[DesignUpdateIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


class SchematicUpdateResult(KicadModel):
    board: Board
    documents: dict[str, Schematic]
    changes: list[DesignUpdateChange] = Field(default_factory=list)
    issues: list[DesignUpdateIssue] = Field(default_factory=list)

    @property
    def schematic(self) -> Schematic:
        if len(self.documents) != 1:
            raise ValueError("Select a document from the multi-sheet result.")
        return next(iter(self.documents.values()))
