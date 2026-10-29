"""Typed controls and reports for symbol library updates."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from eedesigncore.common.base import KicadModel
from eedesigncore.common.models import Property
from eedesigncore.sch.models import PlacedSymbolPin, Schematic


class SymbolFieldUpdatePolicy(KicadModel):
    reset_text: bool = True
    reset_empty: bool = False
    reset_visibility: bool = False
    reset_effects: bool = False
    reset_positions: bool = False
    remove_extra: bool = False
    add_missing: bool = True
    update_private: bool = True


class SymbolFieldUpdateOptions(SymbolFieldUpdatePolicy):
    selected_fields: list[str] | None = None
    excluded_fields: list[str] = Field(default_factory=lambda: ["Reference", "Value"])
    overrides: dict[str, SymbolFieldUpdatePolicy] = Field(default_factory=dict)


class SymbolAttributeUpdateOptions(KicadModel):
    exclude_from_sim: bool = False
    in_bom: bool = False
    on_board: bool = False
    in_pos_files: bool = False


class SymbolLibraryUpdateOptions(KicadModel):
    fields: SymbolFieldUpdateOptions = Field(default_factory=SymbolFieldUpdateOptions)
    attributes: SymbolAttributeUpdateOptions = Field(default_factory=SymbolAttributeUpdateOptions)
    reset_pin_name_visibility: bool = True
    reset_pin_number_visibility: bool = True
    reset_alternate_pins: bool = False
    reset_custom_power_value: bool = False
    reset_reference_prefix: bool = False
    field_placement: Literal["library", "isolated"] = "isolated"

    @classmethod
    def gui_defaults(
        cls, mode: Literal["update", "change"] = "update"
    ) -> SymbolLibraryUpdateOptions:
        """Return fresh dialog settings, with preserved reference text."""
        if mode not in ("update", "change"):
            raise ValueError("The dialog mode must be update or change.")
        changing = mode == "change"
        return cls(
            fields=SymbolFieldUpdateOptions(
                reset_visibility=changing,
                reset_effects=changing,
                reset_positions=changing,
            ),
            attributes=SymbolAttributeUpdateOptions(
                exclude_from_sim=changing,
                in_bom=changing,
                on_board=changing,
                in_pos_files=changing,
            ),
            reset_alternate_pins=changing,
        )

    @classmethod
    def preserve_custom_fields(cls) -> SymbolLibraryUpdateOptions:
        """Replace the definition while retaining placed field content and formatting."""
        return cls(
            fields=SymbolFieldUpdateOptions(
                selected_fields=[],
                excluded_fields=[],
                reset_text=False,
                add_missing=False,
                update_private=False,
            )
        )

    @classmethod
    def atomic_part(cls) -> SymbolLibraryUpdateOptions:
        """Reset library fields and attributes while preserving the reference and DNP flag."""
        return cls(
            fields=SymbolFieldUpdateOptions(
                excluded_fields=["Reference"],
                reset_empty=True,
                reset_visibility=True,
                reset_effects=True,
                reset_positions=True,
                remove_extra=True,
            ),
            attributes=SymbolAttributeUpdateOptions(
                exclude_from_sim=True,
                in_bom=True,
                on_board=True,
                in_pos_files=True,
            ),
            reset_alternate_pins=True,
            reset_custom_power_value=True,
        )


class SymbolLibraryUpdateTarget(KicadModel):
    document_id: str
    symbol_uuid: str
    new_lib_id: str | None = None
    instance_paths: list[str] | None = None
    display_instance_path: str | None = None


class SymbolLibraryUpdateIssue(KicadModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    document_id: str
    symbol_uuid: str


class SymbolLibraryFieldChange(KicadModel):
    name: str
    before: Property | None = None
    after: Property | None = None


class SymbolLibraryPinChange(KicadModel):
    number: str
    unit: int
    before_mm: tuple[float, float] | None = None
    after_mm: tuple[float, float] | None = None
    kind: Literal["moved", "added", "removed"]


class SymbolLibraryChange(KicadModel):
    document_id: str
    symbol_uuid: str
    before_lib_id: str
    after_lib_id: str
    before_cache_key: str
    after_cache_key: str
    before_unit_count: int
    after_unit_count: int
    fields: list[SymbolLibraryFieldChange] = Field(default_factory=list)
    pins: list[SymbolLibraryPinChange] = Field(default_factory=list)
    before_pins: list[PlacedSymbolPin] = Field(default_factory=list)
    after_pins: list[PlacedSymbolPin] = Field(default_factory=list)
    affected_instance_paths: list[str] = Field(default_factory=list)
    changed_definition_properties: list[str] = Field(default_factory=list)
    changed_attributes: list[str] = Field(default_factory=list)
    changed_instance_references: dict[str, str] = Field(default_factory=dict)
    removed_cache_keys: list[str] = Field(default_factory=list)
    added_cache_keys: list[str] = Field(default_factory=list)


class SymbolLibraryUpdatePlan(KicadModel):
    source_signatures: dict[str, str]
    library_signatures: dict[str, str]
    targets: list[SymbolLibraryUpdateTarget]
    options: SymbolLibraryUpdateOptions
    uuid_seed: str
    changes: list[SymbolLibraryChange] = Field(default_factory=list)
    issues: list[SymbolLibraryUpdateIssue] = Field(default_factory=list)


class SymbolLibraryUpdateResult(KicadModel):
    documents: dict[str, Schematic]
    changes: list[SymbolLibraryChange]
    issues: list[SymbolLibraryUpdateIssue]

    @property
    def schematic(self) -> Schematic:
        """Return the sole schematic from a single-document operation."""
        if len(self.documents) != 1:
            raise ValueError("Select a document from a multi-document result.")
        return next(iter(self.documents.values()))
