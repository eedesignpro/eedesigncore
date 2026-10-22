"""The shared base class for every model in this package.

`extra="forbid"` makes construction reject an unknown keyword. Pydantic already
rejects an unknown name on attribute assignment, whatever `extra` holds, so the
default `extra="ignore"` made the two paths disagree. A caller who misspelled a
field name got a silently discarded keyword and a wrong file (UF-010, ADR-086).

This module imports only `pydantic`. Every format sub-package imports it, so an
internal import here could create a cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class KicadModel(BaseModel):
    """A Pydantic model that rejects an unknown constructor keyword."""

    model_config = ConfigDict(extra="forbid")
