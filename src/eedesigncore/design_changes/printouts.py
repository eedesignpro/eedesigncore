"""Two text views of one comparison: a short report and a git-style diff.

Both print the same records. Neither reads a file or compares anything itself.
"""

from __future__ import annotations

import re

from eedesigncore.design_changes.records import CHANGE_CATEGORIES, DesignChange, DesignComparison

_ARROW = " -> "
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _safe(text: str) -> str:
    """Show each control character as ``\\xNN``.

    A name read from a file then cannot start a false diff line or send a
    command to the terminal.
    """
    return _CONTROL.sub(lambda match: f"\\x{ord(match.group()):02x}", text)


def _clip(text: str | None, limit: int) -> str:
    if text is None:
        return "(none)"
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _line(change: DesignChange, limit: int) -> str:
    item, field = _safe(change.item), _safe(change.field)
    if change.kind == "added" and not field:
        return f"added {item}"
    if change.kind == "removed" and not field:
        return f"removed {item}"
    if change.kind == "added":
        return f"{item}: {field} added {_clip(change.after, limit)}"
    if change.kind == "removed":
        return f"{item}: {field} removed {_clip(change.before, limit)}"
    before, after = _clip(change.before, limit), _clip(change.after, limit)
    return f"{item}: {field or '(whole item)'} {before}{_ARROW}{after}"


def format_change_report(comparison: DesignComparison, *, value_limit: int = 80) -> str:
    """Return a short report: the count per category, then one line per change.

    *value_limit* clips each printed value to that many characters. Zero prints
    values whole.
    """
    if comparison.equal:
        return "No changes.\n"
    counts = comparison.counts()
    total = sum(counts.values())
    summary = ", ".join(f"{count} {category}" for category, count in counts.items())
    lines = [f"{total} change{'s' if total != 1 else ''}: {summary}"]
    for category in CHANGE_CATEGORIES:
        if category not in counts:
            continue
        lines.append("")
        lines.append(f"{category}:")
        for change in comparison.changes:
            if change.category == category:
                lines.append(f"  {_line(change, value_limit)}")
    return "\n".join(lines) + "\n"


def format_change_diff(
    comparison: DesignComparison,
    *,
    before_label: str = "a",
    after_label: str = "b",
    value_limit: int = 200,
) -> str:
    """Return a git-style text diff: one hunk per item, one line pair per field.

    A whole added or removed item prints as one line, not as its full content.
    *value_limit* clips each printed value. Zero prints values whole.
    """
    lines = [f"--- {_safe(before_label)}", f"+++ {_safe(after_label)}"]
    if comparison.equal:
        return "\n".join(lines) + "\n"
    current: tuple[str, str, str] | None = None
    for change in comparison.changes:
        key = (change.item_type, change.item, change.identity)
        if key != current:
            current = key
            identity = f" {_safe(change.identity)}" if change.identity else ""
            lines.append(f"@@ {_safe(change.item)} ({change.item_type}{identity}) @@")
        tag = f"  [{change.category}]"
        item, field = _safe(change.item), _safe(change.field)
        if change.kind == "added" and not field:
            lines.append(f"+ {item}{tag}")
        elif change.kind == "removed" and not field:
            lines.append(f"- {item}{tag}")
        else:
            if change.before is not None:
                lines.append(f"- {field}: {_clip(change.before, value_limit)}{tag}")
            if change.after is not None:
                lines.append(f"+ {field}: {_clip(change.after, value_limit)}{tag}")
    return "\n".join(lines) + "\n"


__all__ = ["format_change_diff", "format_change_report"]
