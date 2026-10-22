"""Identify aperture rules that need evaluation outside the file-only geometry path."""

from __future__ import annotations

from dataclasses import dataclass

from eedesigncore.dru import DrcConstraint, DrcConstraintType, DrcFile


@dataclass(frozen=True)
class ApertureRuleDependency:
    """A potentially relevant rule, without claiming its condition matched an item."""

    rule_index: int
    rule_name: str
    constraint: DrcConstraintType


def aperture_rule_dependencies(
    document: DrcFile, layer: str
) -> tuple[ApertureRuleDependency, ...]:
    """List relevant constraints without evaluating conditions or changing the rules.

    This is not permission to ignore other geometry limitations or mask web width.
    Conditions and severity remain unevaluated, so the result is conservative.
    """
    if layer in ("F.Mask", "B.Mask"):
        relevant = {
            DrcConstraintType.SOLDER_MASK_EXPANSION,
            DrcConstraintType.SOLDER_MASK_SLIVER,
            DrcConstraintType.BRIDGED_MASK,
        }
    elif layer in ("F.Paste", "B.Paste"):
        relevant = {
            DrcConstraintType.SOLDER_PASTE_ABS_MARGIN,
            DrcConstraintType.SOLDER_PASTE_REL_MARGIN,
        }
    else:
        raise ValueError("Aperture rule dependencies require a mask or paste layer.")
    return tuple(
        ApertureRuleDependency(index, rule.name, clause.type)
        for index, rule in enumerate(document.rules)
        for clause in rule.clauses
        if isinstance(clause, DrcConstraint) and clause.type in relevant
    )
