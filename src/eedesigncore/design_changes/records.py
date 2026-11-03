"""The change record and the comparison result that holds a list of them."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Literal

ChangeCategory = Literal[
    "electrical", "geometry", "part", "cosmetic", "derived", "settings", "other"
]
ChangeKind = Literal["added", "removed", "changed"]

#: Every category, in the order a report lists them.
CHANGE_CATEGORIES: tuple[ChangeCategory, ...] = (
    "electrical",
    "part",
    "geometry",
    "settings",
    "cosmetic",
    "derived",
    "other",
)

#: The name and version of the record format. A change to a record field or to
#: the meaning of a category takes a new version.
CHANGE_FORMAT = "eedesign-changes-v1"


@dataclass(frozen=True)
class DesignChange:
    """One difference between two designs.

    ``item`` is a name a person reads (``R12 pad 1``, ``net GND``). ``identity``
    is the stored identifier the match used, usually a UUID, or ``""`` when the
    item has none. ``field`` is the dotted field path inside the item, or ``""``
    when the whole item was added or removed. Values are JSON text, and ``None``
    means absent. ``path`` is the full path from the document root.
    """

    category: ChangeCategory
    kind: ChangeKind
    item_type: str
    item: str
    identity: str
    field: str
    before: str | None
    after: str | None
    path: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return the record as plain JSON-ready data, values parsed."""
        return {
            "category": self.category,
            "kind": self.kind,
            "item_type": self.item_type,
            "item": self.item,
            "identity": self.identity,
            "field": self.field,
            "before": None if self.before is None else json.loads(self.before),
            "after": None if self.after is None else json.loads(self.after),
            "path": list(self.path),
        }


@dataclass(frozen=True)
class DesignComparison:
    """The changes between two designs, under one tolerance.

    ``equal`` is true when no change survives the tolerance. The two signatures
    are the exact :func:`~eedesigncore.content_signature` of each side, so they
    can differ while ``equal`` is true when a tolerance hid a difference.
    """

    before_signature: str
    after_signature: str
    tolerance: float
    changes: tuple[DesignChange, ...]

    @property
    def equal(self) -> bool:
        """True when no change was found."""
        return not self.changes

    def counts(self) -> dict[ChangeCategory, int]:
        """Return the number of changes in each category that has any."""
        found = Counter(change.category for change in self.changes)
        return {category: found[category] for category in CHANGE_CATEGORIES if found[category]}

    def only(self, *categories: ChangeCategory) -> DesignComparison:
        """Return a copy that keeps the changes in *categories* only."""
        unknown = set(categories) - set(CHANGE_CATEGORIES)
        if unknown:
            raise ValueError(f"Unknown change categories: {', '.join(sorted(unknown))}.")
        kept = tuple(change for change in self.changes if change.category in categories)
        return DesignComparison(self.before_signature, self.after_signature, self.tolerance, kept)

    def to_json(self) -> str:
        """Return the comparison as JSON text in the :data:`CHANGE_FORMAT` layout."""
        return json.dumps(
            {
                "format": CHANGE_FORMAT,
                "before_signature": self.before_signature,
                "after_signature": self.after_signature,
                "tolerance": self.tolerance,
                "equal": self.equal,
                "changes": [change.as_dict() for change in self.changes],
            },
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )


__all__ = [
    "CHANGE_CATEGORIES",
    "CHANGE_FORMAT",
    "ChangeCategory",
    "ChangeKind",
    "DesignChange",
    "DesignComparison",
]
