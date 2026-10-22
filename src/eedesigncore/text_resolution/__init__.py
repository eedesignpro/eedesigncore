"""Raw and resolved KiCad text, without input mutation or implicit file access."""

from eedesigncore.text_resolution.documents import (
    ResolvedDocumentText,
    context_for_sheet,
    resolve_document_text,
)
from eedesigncore.text_resolution.engine import resolve_text
from eedesigncore.text_resolution.models import (
    ResolvedText,
    TextDiagnostic,
    TextResolutionContext,
)
from eedesigncore.text_resolution.runtime import GitTextResolver, capture_text_context

__all__ = [
    "GitTextResolver",
    "capture_text_context",
    "ResolvedDocumentText",
    "ResolvedText",
    "TextDiagnostic",
    "TextResolutionContext",
    "context_for_sheet",
    "resolve_document_text",
    "resolve_text",
]
