"""The MUTABLE bus-connection state, and bus member matching.

Source: ``SCH_CONNECTION`` (``eeschema/sch_connection.cpp``) and
``CONNECTION_GRAPH::matchBusMember`` (``eeschema/connection_graph.cpp:3276-3361``)
at tag 10.0.3.

``bus.py`` classifies a label into an IMMUTABLE tree. That is the right model for
the grammar and the wrong model for propagation, because propagation CLONES one
subgraph's connection onto another and the clone deliberately keeps some fields
while replacing others (``sch_connection.cpp:242-343``):

- ``m_name``, ``m_prefix``, ``m_suffix``, ``m_sheet``, and the member list are
  REPLACED.
- ``m_local_name`` and ``m_local_prefix`` are kept when already set, so a member
  still remembers the label text of the sheet it came from.
- ``m_vector_index`` is not cloned by ``Clone`` itself; the clone helper copies it
  separately (``:286``).

**That split is the entire mechanism behind bus member identity.** A member is
matched to its counterpart by LOCAL name, or by vector INDEX, and then renamed to
the counterpart's name. Model a member as immutable and the match has nothing
stable to key on, so each sheet keeps its own member net instead of joining the
one net that crosses the hierarchy.

**A member connection is sheet-qualified until something clones a driver onto
it.** ``recacheName`` prepends the path unless the connection has no parent item,
has type ``NONE``, or has a driver whose kind opts out
(``sch_connection.cpp:405-441``). A member built by ``ConfigureFromLabel`` has a
parent item, has type ``NET`` or ``BUS``, and has NO driver, so it qualifies. But
``Clone`` copies ``m_driver`` too (``:246``), so a member cloned from a global
label stops qualifying. ``prepend`` carries that state, and getting it wrong
sheet-qualifies a global net.

The path a member carries is its OWN ``m_sheet``, which ``Clone`` copies from the
source connection. So a member cloned from a winner on another sheet carries the
WINNER's path, and that is how one bus member net spans two sheets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eedesigncore.connectivity.bus import (
    AliasResolver,
    BusConnection,
    ConnectionType,
    configure_from_label,
)


@dataclass
class MemberConn:
    """One bus connection or one of its members, with the fields ``Clone`` moves.

    Mirrors the subset of ``SCH_CONNECTION`` that ``propagate_bus_neighbors`` and
    ``matchBusMember`` read or mutate. Deliberately mutable: source mutates these
    objects in place rather than replacing them, and a caller holds a reference to
    one across a rename.
    """

    type: ConnectionType
    # m_local_name, m_local_prefix. KEPT by Clone once set.
    local_name: str
    local_prefix: str = ""
    # m_name, m_prefix, m_suffix. REPLACED by Clone.
    name: str = ""
    prefix: str = ""
    suffix: str = ""
    # m_sheet, REPLACED by Clone (`sch_connection.cpp:247`), with its escaped
    # net-name rendering alongside so a name can be built without a sheet table.
    sheet: tuple[str, ...] = ()
    sheet_prefix: str = "/"
    # m_vector_index. Copied by the clone helper, not by Clone itself.
    vector_index: int = 0
    members: list[MemberConn] = field(default_factory=list)
    # Whether recacheName prepends the path, which depends on m_driver and so
    # travels with a Clone. True for any member ConfigureFromLabel builds,
    # because such a member has no driver.
    prepend: bool = True

    @property
    def cached_name(self) -> str:
        """``Name(true)``, the unqualified form (`sch_connection.cpp:394-396`)."""
        return f"{self.prefix}{self.name}{self.suffix}"

    @property
    def qualified_name(self) -> str:
        """``Name(false)``, the form a netlist reads (`sch_connection.cpp:441-452`)."""
        return f"{self.sheet_prefix}{self.cached_name}" if self.prepend else self.cached_name

    @property
    def full_local_name(self) -> str:
        """``FullLocalName()`` (`sch_connection.h:147-150`)."""
        return f"{self.local_prefix}{self.local_name}{self.suffix}"

    @property
    def is_bus(self) -> bool:
        return self.type in (ConnectionType.BUS, ConnectionType.BUS_GROUP)


def _from_classified(node: BusConnection, sheet: tuple[str, ...], sheet_prefix: str) -> MemberConn:
    """Build a mutable connection from one classified node.

    At construction the name and the local name agree, because
    ``ConfigureFromLabel`` sets both from the same text (`:143-144`, `:166-169`).
    They diverge only once ``Clone`` runs.
    """
    return MemberConn(
        type=node.type,
        local_name=node.local_name,
        local_prefix=node.local_prefix,
        name=node.local_name,
        prefix=node.local_prefix,
        sheet=sheet,
        sheet_prefix=sheet_prefix,
        vector_index=node.vector_index,
        members=[_from_classified(m, sheet, sheet_prefix) for m in node.members],
    )


def configure_connection(
    label_text: str,
    *,
    sheet: tuple[str, ...] = (),
    sheet_prefix: str = "/",
    prepend: bool = True,
    resolve_alias: AliasResolver | None = None,
) -> tuple[ConnectionType, list[MemberConn]]:
    """Port ``ConfigureFromLabel``'s effect on a connection's type and members.

    Returns the connection type and its direct member list. The caller keeps its
    own name fields, because a subgraph already carries both name forms.

    *prepend* is the driver's own prepend decision, applied to every member the
    way ``SetDriver`` applies it. The caller passes what the prepend rule says
    about its winning driver.
    """
    classified = configure_from_label(label_text, resolve_alias=resolve_alias)
    members = [_from_classified(m, sheet, sheet_prefix) for m in classified.members]
    set_driver(members, prepend)
    return classified.type, members


def connection_from_name(
    escaped_name: str,
    raw_name: str | None = None,
    *,
    sheet: tuple[str, ...] = (),
    sheet_prefix: str = "/",
    prepend: bool = True,
    resolve_alias: AliasResolver | None = None,
) -> MemberConn:
    """Build a connection for one driver, porting ``getDefaultConnection`` (`:3243-3274`).

    Source builds a fresh connection from the driver's own name whenever it needs
    to test a driver that the subgraph did NOT choose, because the item's real
    connection was already overwritten by the winner.

    *escaped_name* is the ``GetNameForDriver`` cache and is what a name comparison
    uses. *raw_name* is the unescaped shown text, which is what the bus grammar
    must classify (`connection_graph.cpp:1747`). They differ only for a name
    holding a character the escape rule touches.
    """
    conn_type, members = configure_connection(
        raw_name if raw_name is not None else escaped_name,
        sheet=sheet,
        sheet_prefix=sheet_prefix,
        prepend=prepend,
        resolve_alias=resolve_alias,
    )
    return MemberConn(
        type=conn_type,
        local_name=escaped_name,
        name=escaped_name,
        sheet=sheet,
        sheet_prefix=sheet_prefix,
        prepend=prepend,
        members=members,
    )


def set_driver(members: list[MemberConn], prepend: bool) -> None:
    """Port ``SCH_CONNECTION::SetDriver`` (`sch_connection.cpp:111-119`).

    Source assigns the driver to the connection AND recursively to every member.
    ``ResolveDrivers`` calls ``ConfigureFromLabel`` and then ``SetDriver``
    (`connection_graph.cpp:277-278`), in that order, so every member ends up
    holding its subgraph's WINNING driver rather than none.

    That is the whole reason a GLOBAL bus label's members are not
    sheet-qualified. The prepend rule opts a global label out of the path, and
    ``SetDriver`` hands that opt-out down to each member. A port that leaves a
    member driverless qualifies the member instead, and the qualified name then
    fails to match the same member on another sheet, so one net becomes two.

    Verified against KiCad on constructed geometry: a global bus label on a root
    sheet and the same label in a child, with no wire between them, produce ONE
    net per member named without any path.
    """
    for member in members:
        member.prepend = prepend
        set_driver(member.members, prepend)


def copy_conn(src: MemberConn) -> MemberConn:
    """A deep, independent copy, porting the copy constructor at `:287-291`.

    Phase 8 keys each bus neighbour link on a member of a COPY of the driver
    connection (`connection_graph.cpp:2089`), never on the live one. That copy is
    what freezes the key while the live connection keeps being renamed.
    """
    return MemberConn(
        type=src.type,
        local_name=src.local_name,
        local_prefix=src.local_prefix,
        name=src.name,
        prefix=src.prefix,
        suffix=src.suffix,
        sheet=src.sheet,
        sheet_prefix=src.sheet_prefix,
        vector_index=src.vector_index,
        prepend=src.prepend,
        members=[copy_conn(m) for m in src.members],
    )


def clone_into(dst: MemberConn, src: MemberConn) -> None:
    """Port ``SCH_CONNECTION::Clone`` (`sch_connection.cpp:242-343`).

    The local name is taken only when *dst* has none yet, and the local PREFIX
    then comes from the source's ``Prefix()`` rather than its own local prefix
    (`:255-259`). Reproduced as written, because the two agree everywhere
    ``ConfigureFromLabel`` builds a member.
    """
    dst.name = src.name
    if not dst.local_name:
        dst.local_name = src.local_name
        dst.local_prefix = src.prefix
    dst.prefix = src.prefix
    dst.suffix = src.suffix
    dst.sheet = src.sheet
    dst.sheet_prefix = src.sheet_prefix
    # m_driver travels with the clone (`:246`), and the prepend rule reads it.
    dst.prepend = src.prepend
    dst.members = clone_member_list(dst.type, dst.members, src)
    # LAST, because the member-list branch above reads the ORIGINAL type (`:341`).
    dst.type = src.type


def _clone_new(src: MemberConn) -> MemberConn:
    """Port the ``cloneMember`` lambda (`:279-288`).

    A fresh connection starts with an empty local name, so the clone adopts the
    source's. The vector index is copied explicitly, which is the only place it
    ever crosses a clone.
    """
    copy = MemberConn(type=ConnectionType.NET, local_name="")
    clone_into(copy, src)
    copy.vector_index = src.vector_index
    return copy


def clone_member_list(
    dst_type: ConnectionType, dst_members: list[MemberConn], src: MemberConn
) -> list[MemberConn]:
    """Port the member-list half of ``Clone`` (`:275-341`).

    Three branches, and the difference between them is the point.

    A vector onto a vector matches BY INDEX, because a vector member's name may
    legitimately differ between sheets. A group onto a group matches BY LOCAL
    NAME, because a group's naming is expected to be consistent. Either way the
    existing member objects are mutated, which is what PRESERVES the local name.
    Any other pairing rebuilds the list and the local names come from the source.
    """
    src_members = src.members

    if dst_type is ConnectionType.BUS and src.type is ConnectionType.BUS:
        if not dst_members:
            return [_clone_new(m) for m in src_members]
        for index in range(min(len(dst_members), len(src_members))):
            clone_into(dst_members[index], src_members[index])
        return dst_members

    if dst_type is ConnectionType.BUS_GROUP and src.type is ConnectionType.BUS_GROUP:
        if not dst_members:
            return [_clone_new(m) for m in src_members]
        for member in dst_members:
            match = next((m for m in src_members if m.local_name == member.local_name), None)
            if match is not None:
                clone_into(member, match)
        return dst_members

    if src.is_bus:
        return [_clone_new(m) for m in src_members]

    # Source leaves the member list untouched here, even though the type is about
    # to become NET. Reproduced rather than tidied: matchBusMember checks the type
    # first, so a stale list is unreachable.
    return dst_members


def match_bus_member(
    bus_type: ConnectionType, bus_members: list[MemberConn], search: MemberConn
) -> MemberConn | None:
    """Port ``CONNECTION_GRAPH::matchBusMember`` (`:3276-3361`).

    *search* is the member connection frozen when the bus neighbour link was
    made. *bus_members* is the bus's CURRENT member list, after however many
    renames the hierarchy has applied. This function maps one onto the other.

    Returns ``None`` where source returns ``nullptr``. Source treats that as
    probably an ERC error and skips the neighbour rather than guessing.
    """
    if bus_type not in (ConnectionType.BUS, ConnectionType.BUS_GROUP):
        return None

    if bus_type is ConnectionType.BUS:
        # Vector bus: by INDEX, because the name is allowed to differ (`:3286-3296`).
        for member in bus_members:
            if member.vector_index == search.vector_index:
                return member
        return None

    match: MemberConn | None = None
    for member in bus_members:
        if member.type is ConnectionType.BUS:
            # A vector nested in a group: by local name, one level down
            # (`:3303-3312`). Source does NOT break the OUTER loop on a hit here,
            # only the inner one, so a later nested hit overwrites this one.
            # Reproduced, because it decides the winner when two nested vectors
            # share a member name.
            for nested in member.members:
                if nested.local_name == search.local_name:
                    match = nested
                    break
        elif member.local_name == search.local_name:
            return member

    if match is not None:
        return match

    # The flat-index fallback (`:3321-3358`). Reached when no local name matched.
    # The `>= 0` guard always passes, because the index defaults to 0.
    if search.vector_index >= 0:
        flat = 0
        for member in bus_members:
            if member.type is ConnectionType.BUS:
                for nested in member.members:
                    if flat == search.vector_index:
                        return nested
                    flat += 1
            else:
                if flat == search.vector_index:
                    return member
                flat += 1

    return None
