"""Shared page-setup models: ``(paper …)`` and ``(title_block …)``.

Both emitters are cross-application KiCad common code reused by pcb / sch / wks
(``common/page_info.cpp:238`` and ``common/title_block.cpp:31``), so the models
live in ``common/`` per the board-root-grammar report §7. Loaders/writers are
added alongside the board header work (T2/T3).
"""

from __future__ import annotations

from eedesigncore.common.base import KicadModel


class Paper(KicadModel):
    """A ``(paper …)`` block (``PAGE_INFO::Format`` page_info.cpp:238-256).

    ``paper_type`` is the ``magic_enum`` name of ``PAGE_SIZE_TYPE`` (e.g. ``A4``,
    ``USLetter``, ``User``). For ``User``, ``width``/``height`` carry the custom
    size in millimetres (emitted via ``FormatDouble2Str``). ``portrait`` is
    emitted only for non-custom portrait pages.
    """

    paper_type: str = "A4"
    width: float | None = None  # User type only, mm
    height: float | None = None  # User type only, mm
    portrait: bool = False


class TitleBlock(KicadModel):
    """A ``(title_block …)`` block (``TITLE_BLOCK::Format`` title_block.cpp:31-72).

    Emitted only when at least one field is non-empty. ``comments`` maps the
    1-based comment index (1..9) to its text; only present indices are emitted.
    """

    title: str = ""
    date: str = ""
    rev: str = ""
    company: str = ""
    comments: dict[int, str] = {}
