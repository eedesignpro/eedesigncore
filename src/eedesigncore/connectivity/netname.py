"""Net-name escaping, the ``CTX_NETNAME`` context only.

KiCad escapes a net-name component before it joins a path. The rule is two
substitutions and nothing else (`common/string_utils.cpp:196-204`, audit
``escape-string-module-2026-08-18.md`` line 586).

1. A forward slash becomes the literal seven-character ``{slash}``.
2. A carriage return and a line feed drop.

**Every other character passes through unchanged.** A parenthesis, a space, a
hyphen, a brace, and a hash are NOT escaped in this context. Escaping the wrong
set produces net names that disagree with KiCad on any sheet whose name carries
punctuation, which is most of them.

The transform inspects the slash only, so running it twice is the same as
running it once.
"""

from __future__ import annotations

# The literal replacement. Seven characters, and not a format placeholder.
SLASH_TOKEN = "{slash}"

# Dropped outright rather than replaced.
_DROPPED = ("\n", "\r")


def escape_netname(component: str) -> str:
    """Escape one net-name component for the ``CTX_NETNAME`` context.

    Applies the two substitutions above and no others.
    """
    out = component.replace("/", SLASH_TOKEN)
    for char in _DROPPED:
        out = out.replace(char, "")
    return out


def join_sheet_path(names: list[str]) -> str:
    """Join sheet names into a net-name path prefix.

    Each component is escaped, then the components join on an unescaped slash.
    The separator is a real slash. Only a slash INSIDE a component escapes, which
    is what keeps a path parseable.
    """
    return "/" + "/".join(escape_netname(n) for n in names) if names else "/"
