"""Text variable resolution, shared across formats.

KiCad substitutes a ``${NAME}`` reference before it shows, matches, or compares
any text. The mechanism is ``GetShownText``, and it applies to far more than
connectivity: a sheet name, a symbol field, a title block entry, a text item, and
a label all pass through it.

This module therefore lives in ``common`` and not in ``connectivity``.
Connectivity is one CONSUMER of it, and the first one to need it, because a sheet
name that carries a variable produces a different net-name path per instance.

**Scope layering, nearest first.** A sheet's own fields override an ancestor
sheet's, which override the project's variables. The caller builds that layering
and passes the merged scope, because only the caller knows where it sits in a
tree. This module does no lookup of its own.

Source: variable substitution runs before name matching and before bus parsing
(audit ``hierarchy-and-bus-2026-07-28.md`` A6 item 5). The project-level table is
``PROJECT_FILE::m_TextVars``, exposed here as ``ProjectFile.text_variables``. A
sheet-level variable is an ordinary field on the sheet item, which is how one
sheet file appears twice under two different names.
"""

from __future__ import annotations

import re

# A text variable reference, as ``${NAME}``.
_VARIABLE = re.compile(r"\$\{([^}]*)\}")


def resolve(text: str, scope: dict[str, str]) -> str:
    """Substitute every ``${NAME}`` reference in *text* from *scope*.

    An unknown name is left as written, which matches KiCad showing the raw
    reference rather than emptying it. That behavior matters: an emptied sheet
    name would silently merge two instances of one sheet file.
    """
    from eedesigncore.text_resolution.engine import resolve_text
    from eedesigncore.text_resolution.models import TextResolutionContext

    return resolve_text(text, TextResolutionContext(variables=scope)).resolved


def has_reference(text: str) -> bool:
    """True when *text* carries at least one unresolved reference."""
    return bool(_VARIABLE.search(text)) or "@{" in text
