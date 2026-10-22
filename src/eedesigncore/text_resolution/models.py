"""Explicit inputs and results for KiCad shown text."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from eedesigncore.common.base import KicadModel

if TYPE_CHECKING:
    from eedesigncore.pro.models import ProjectFile
    from eedesigncore.project.hierarchy import SheetHierarchy

ExpressionValue = str | float
VariableProvider = Callable[[str, str], str | None]
FunctionProvider = Callable[[str, tuple[ExpressionValue, ...]], ExpressionValue | None]


class TextDiagnostic(KicadModel):
    """A missing input or failed expression. The original text remains available."""

    code: Literal["unresolved_variable", "expression_error", "resolution_limit", "missing_context"]
    token: str
    message: str


class ResolvedText(KicadModel):
    """Stored text and shown text, before wrapping or glyph generation."""

    raw: str
    resolved: str
    diagnostics: list[TextDiagnostic] = []

    @property
    def complete(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class TextResolutionContext:
    """Caller-owned text inputs. No implicit files, environment, or processes.

    Scopes have nearest-first precedence. Runtime providers receive the item
    path and variable name, or a function name and evaluated arguments.
    Return None for missing data. Raise ValueError for invalid supplied data.
    Capture a clock and random source once when reproducible output is needed.
    """

    variables: Mapping[str, str] = field(default_factory=dict)
    scopes: tuple[Mapping[str, str], ...] = ()
    sheet_scopes: tuple[Mapping[str, str], ...] = ()
    sheet_flags: Mapping[str, bool] = field(default_factory=dict)
    filename: str | None = None
    project_name: str | None = None
    sheet_path: str | None = None
    sheet_name: str | None = None
    page: str | None = None
    page_count: int | None = None
    instance_path: str | None = None
    hierarchy: SheetHierarchy | None = None
    project: ProjectFile | None = None
    net_classes: Mapping[str, str] = field(default_factory=dict)
    connections: Mapping[tuple[str, str, str], str] = field(default_factory=dict)
    resolve_connections: bool = True
    variant: str = ""
    variant_description: str = ""
    now: datetime | None = None
    random: Callable[[], float] | None = None
    variable_provider: VariableProvider | None = None
    function_provider: FunctionProvider | None = None
    max_depth: int = 6
    max_length: int = 1_000_000
    max_operations: int = 100_000

    def __post_init__(self) -> None:
        if not 0 <= self.max_depth <= 64:
            raise ValueError("Text resolution depth must be between zero and 64.")
        if not 1 <= self.max_length <= 10_000_000:
            raise ValueError("Text resolution length must be between one and ten million.")
        if not 1 <= self.max_operations <= 1_000_000:
            raise ValueError("Text resolution operations must be between one and one million.")
