"""Phase 7: ``generateBusAliasMembers``.

Source: ``eeschema/connection_graph.cpp:1733-1794`` at tag 10.0.3. Mapped by
audit ``connection-graph-algorithm-2026-09-02.md``, section "Phase 7", and by
audit ``hierarchy-and-bus-2026-07-28.md``, section B5's
"``generateBusAliasMembers``" subsection.

Despite the name, this phase is not alias-specific. For every bus label on
every driver subgraph, it classifies the label's text
(``SCH_CONNECTION::ConfigureFromLabel``, ported in ``bus.py``) and creates one
placeholder subgraph per direct ``NET`` member. A nested bus member, a vector
inside a group, is skipped (``!conn->IsNet()``, ``:1755-1756``): a placeholder
never forms for it. These placeholders carry no items and no real driver.
Their sole purpose is bus-to-bus neighbor matching in a later phase
(``processSubGraphs``, connection_graph.h:237-241, out of this task's scope).

Two things this port does not, and cannot yet, do.

1. Assign a net code. ``assignNewNetCode`` (``:1771``) keys into
   ``m_net_name_to_code_map``, a ``CONNECTION_GRAPH``-level map this task's
   scope has no container for. The ``Subgraph`` model already omits net codes
   everywhere else in this engine, by design (``subgraph.py`` module
   docstring): "a code is not reproducible and must never be compared."
2. Register the placeholder in a net-name-to-subgraphs map
   (``m_net_code_to_subgraphs_map``, ``m_net_name_to_subgraphs_map``,
   ``:1783-1785``). No such graph-level container exists in this task's
   scope. A later phase that needs one owns adding it.

One further gap, flagged rather than guessed at: source classifies the RAW
label text, ``label->GetShownText(&subgraph->m_sheet, false)``
(``:1747``), not the escaped ``driverName`` cache
(``GetNameForDriver``, ``connection_graph.cpp:414``). The existing
``Driver`` model (``subgraph.py``) carries only one text field, ``name``,
documented there as the ``GetNameForDriver`` cache. This port reads
``driver.raw_name``, which is the RAW shown text when it differs from
form is exposed on ``Driver``. The audit itself flags the same gap as
"UNVERIFIED as to consequence" (B5, "``generateBusAliasMembers``", point 3).
See ``context/decisions.md`` for the ADR this gap was logged under.
"""

from __future__ import annotations

from eedesigncore.connectivity.bus import AliasResolver, configure_from_label
from eedesigncore.connectivity.subgraph import Driver, ItemKind, Subgraph

# The item kinds GetAllBusLabels admits (connection_graph.cpp:341-358). A
# sheet pin, a plain pin, and a directive label are excluded outright by the
# switch's default branch.
_BUS_LABEL_KINDS = (ItemKind.LABEL, ItemKind.GLOBAL_LABEL, ItemKind.HIER_LABEL)


def _bus_label_drivers(subgraph: Subgraph) -> list[Driver]:
    """Port of ``CONNECTION_SUBGRAPH::GetAllBusLabels`` (``connection_graph.cpp:335-362``).

    Source filters ``m_drivers`` by item type, then by the driver's own,
    already-resolved connection type (``BUS`` or ``BUS_GROUP``). This model
    has no separate "declared connection type" field on ``Driver``.
    ``Driver.is_bus`` is the only available proxy, and for a label-kind
    driver it reduces to "this driver carries a non-empty ``bus_members``
    set," which is set at driver construction from the label's own text.
    """
    return [d for d in subgraph.drivers if d.kind in _BUS_LABEL_KINDS and d.is_bus]


def generate_bus_alias_members(
    driver_subgraphs: list[Subgraph],
    *,
    resolve_alias: AliasResolver | None = None,
) -> list[Subgraph]:
    """Port of ``CONNECTION_GRAPH::generateBusAliasMembers`` (``connection_graph.cpp:1733-1794``).

    Runs after ``generateGlobalPowerPinSubGraphs`` and before
    ``processSubGraphs`` in the source call order
    (``connection_graph.cpp:2286-2291``). *driver_subgraphs* is the caller's
    already-assembled driver list at that point in the pipeline, including
    any global-power placeholder subgraphs. This function does not mutate
    it.

    Returns the new placeholder subgraphs, matching what source appends to
    ``m_driver_subgraphs`` at ``:1792-1793``. The caller extends its own
    driver list with the result, the same two-step source itself takes
    (build ``new_subgraphs`` locally, then ``std::copy`` it onto
    ``m_driver_subgraphs``).

    Each placeholder sets ``is_bus_member`` and ``strong_driver`` true
    (``:1779-1780``) and ``name`` to the member's ``FullLocalName()``
    (``:1758``, ``:1767``). It leaves ``driver`` unset. Source never assigns
    ``new_sg->m_driver``, only ``m_driver_connection``
    (``:1776``), so ``GetNetName()`` on a placeholder returns an empty string
    in source (``connection_graph.cpp:317-320``, the ``!m_driver`` guard).
    Ported faithfully: a placeholder here likewise carries no ``Driver``.

    Every placeholder is independent. Nothing here reads another
    placeholder's state, so the order of *driver_subgraphs* does not change
    the result.
    """
    new_subgraphs: list[Subgraph] = []

    for subgraph in driver_subgraphs:
        for driver in _bus_label_drivers(subgraph):
            classified = configure_from_label(driver.raw_name, resolve_alias=resolve_alias)

            for member in classified.members:
                # Only create subgraphs for NET members, not nested buses
                # (connection_graph.cpp:1754-1756).
                if not member.is_net:
                    continue

                # The placeholder's connection is a bare NET carrying the
                # member's full local name, with no driver (`:1764-1767`). No
                # driver means the prepend rule applies, so its qualified form
                # carries the parent's path. Phase 8 compares the unqualified
                # form and bus propagation the qualified one, so BOTH must be
                # set or the placeholder is renamed on every pass.
                new_subgraphs.append(
                    Subgraph(
                        sheet_path=subgraph.sheet_path,
                        name=member.full_local_name,
                        full_name=f"{subgraph.conn_sheet_prefix}{member.full_local_name}",
                        conn_sheet=subgraph.sheet_path,
                        conn_sheet_prefix=subgraph.conn_sheet_prefix,
                        conn_prepend=True,
                        is_bus_member=True,
                        strong_driver=True,
                        dirty=False,
                    )
                )

    return new_subgraphs
