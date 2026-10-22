"""Net class assignment, phase 9h, and the recache helper, phase 12.

Source: the netclass block of ``buildConnectionGraph`` (`:2679-2802`),
``CONNECTION_SUBGRAPH::GetNetclassesForDriver`` (`:452-499`), and
``recacheSubgraphName`` (`:3364-3378`), tag 10.0.3.

**The canonical field name is ``Netclass``, one word.** Not two, and not with a
space (`:480`). A schematic written by an older release can carry the two-word
spelling, which a newer release rewrites. That difference is the whole of
BS-12.1-2 in this repository.

**A net class is a label, never a driver.** A directive label falls to the rank
switch default and returns no priority, so it attaches a class and never names a
net (`:613-615`).

**Assignment order is fixed by a sort**, not by iteration (`:491-496`). The
classes found for one net are sorted by name before they are applied, so the
result does not depend on the order items were met.
"""

from __future__ import annotations

from eedesigncore.connectivity.subgraph import Subgraph

# The one canonical spelling (`connection_graph.cpp:480`).
NETCLASS_FIELD = "Netclass"


def netclasses_for_names(
    directive_labels: list[tuple[str, list[tuple[str, str]]]],
) -> dict[str, list[str]]:
    """Group net class names by the net they attach to.

    *directive_labels* pairs a net name with the label's fields, as
    ``(field name, value)``. Only a field whose canonical name is ``Netclass``
    contributes, and the result is sorted by class name per net.
    """
    out: dict[str, set[str]] = {}
    for net_name, fields in directive_labels:
        for field_name, value in fields:
            if field_name == NETCLASS_FIELD and value:
                out.setdefault(net_name, set()).add(value)
    return {name: sorted(classes) for name, classes in out.items()}


def assign(
    subgraphs: list[Subgraph],
    assignments: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Apply net class assignments to the derived nets, porting ``:2787-2802``.

    Returns the applied map, keyed on the net name a caller will see, so a
    consumer can read a class without reaching into a subgraph.

    A class attaches to the ROOT net name and to every bus member of that net
    (`:2748-2769`). Bus member expansion is not reached here, because a bus
    member list only exists once the bus phases run, and this states that rather
    than guessing at one.
    """
    applied: dict[str, list[str]] = {}
    live_names = {s.full_name or s.name for s in subgraphs if not s.absorbed}
    for net_name, classes in assignments.items():
        if net_name in live_names:
            applied[net_name] = list(classes)
    return applied


def recache_name(subgraph: Subgraph, old_name: str) -> bool:
    """Phase 12. Move a subgraph between name buckets after a rename.

    Ports ``recacheSubgraphName`` (`:3364-3378`). In KiCad this maintains
    ``m_net_name_to_subgraphs_map`` so a later lookup by name finds the subgraph
    under its NEW name.

    This port keeps no such map, because every later phase reads the name from
    the subgraph directly. The function therefore reports whether a rename
    happened and does not maintain a second copy of the truth. A map that can
    disagree with the subgraph is a bug waiting to happen, and the audit records
    that KiCad rebuilds this map wholesale at `:2667-2677` anyway.
    """
    return old_name != (subgraph.full_name or subgraph.name)
