"""Group-aware UUID lifecycle transforms (ADR-019 points 3 and 5).

`strip_uuids`, `generate_uuids` and `derive_uuids` are pure, caller-invoked
model transforms.
Neither is ever invoked implicitly by load or save (ADR-019 point 1); plain
`to_file()`/`to_string()` round-trip is untouched (ADR-019 point 4).

All three walk a model tree and match on field NAME, so they are
format-agnostic: a `Footprint`, a `Board` and a `Schematic` all work, and every
uuid-bearing child goes with the parent. For symbol-family objects
(`SymbolLib`/`Symbol`) they are a documented **no-op**: `.kicad_sym` is uuid-free
by construction (`sch_io_kicad_sexpr_lib_cache.cpp:389`), so there is nothing to
strip, generate or derive.

Group safety (ADR-019 point 3, mandatory)
-----------------------------------------
A `(group ...)` node references its members by uuid plus carries its own uuid.
The KiCad parser silently drops any member whose uuid resolves to nothing and
then drops the emptied group entirely on the next save
(`pcb_io_kicad_sexpr.cpp:2473,2482-2483,2497-2502`; parser `:1616-1629`).
Therefore `strip_uuids(..., group_aware=True)` (the default) collects the
protected set - every member uuid and every owner's own uuid - and never removes
those. `generate_uuids(..., only_missing=False)` and `derive_uuids` rewrite every
uuid AND remap the member references together, so no reference is left dangling.

Three models own a `members` list and all three mean uuids: `PcbGroup`, the
schematic `Group`, and `PcbGenerator`, which names the tracks a generated
pattern owns. Both the protection and the remap match on the FIELD, so all three
are covered. Matching on one class instead left two of them broken until
iteration 14.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel

# Field names that hold a KiCad KIID (uuid). `Footprint` uses ``uuid``; every
# child model uses ``id``. No non-uuid ``id``/``uuid`` string fields exist in the
# model tree (verified), so targeting these two names is exact.
_UUID_FIELDS = ("uuid", "id")

# The field that holds a LIST of uuid references. Three models carry it and all
# three mean the same thing: ``PcbGroup`` (a board or footprint group), the
# schematic ``Group``, and ``PcbGenerator`` (the items a generated pattern owns,
# pcb/models.py). Matching on the field name keeps this module free of any
# format sub-package import, which ADR-015 requires of ``common``.
_MEMBER_FIELD = "members"

# The default namespace for :func:`derive_uuids`. Fixed, because a derived uuid
# must be the same value on every run and on every machine. It is
# ``uuid5(NAMESPACE_URL, "urn:eedesigncore:derived-uuid")``,
# written out so it can never drift.
DERIVED_UUID_NAMESPACE = UUID("81b98e61-75e7-5093-85f7-3c8de29948cc")


def _is_symbol_family(obj: object) -> bool:
    """True for `.kicad_sym` models (uuid-free; strip/generate are no-ops)."""
    module = type(obj).__module__
    return "sch_family" in module or module.startswith("eedesigncore.sym")


def _iter_models(root: BaseModel) -> Iterator[BaseModel]:
    """Yield *root* and every nested Pydantic model (depth-first, no cycles)."""
    stack: list[BaseModel] = [root]
    while stack:
        model = stack.pop()
        yield model
        for name in type(model).model_fields:
            value = getattr(model, name)
            if isinstance(value, BaseModel):
                stack.append(value)
            elif isinstance(value, list | tuple):
                stack.extend(v for v in value if isinstance(v, BaseModel))


def _member_lists(models: list[BaseModel]) -> Iterator[BaseModel]:
    """Yield every model that references other items by a list of uuids."""
    for model in models:
        if _MEMBER_FIELD in type(model).model_fields:
            yield model


def _collect_protected(models: list[BaseModel]) -> set[str]:
    """Every referenced member uuid, plus the uuid of each referencing owner.

    ADR-019 point 3. This matches on the ``members`` FIELD, the same way
    :func:`_remap_members` does, so it covers all three owners: ``PcbGroup``, the
    schematic ``Group``, and ``PcbGenerator``. It used to test
    ``isinstance(model, PcbGroup)``, which protected a board or footprint group
    and left a schematic group unprotected. Stripping a member uuid that a group
    still names makes the KiCad parser drop the member, and the next save drops
    the emptied group.
    """
    protected: set[str] = set()
    for model in _member_lists(models):
        fields = type(model).model_fields
        for field in _UUID_FIELDS:
            if field in fields:
                own = getattr(model, field)
                if own is not None:
                    protected.add(own)
        members: list[str] = getattr(model, _MEMBER_FIELD)
        protected.update(m for m in members if m)
    return protected


def _remap_members(models: list[BaseModel], remap: dict[str, str]) -> None:
    """Point every uuid reference list at the new uuids."""
    for model in _member_lists(models):
        members: list[str] = getattr(model, _MEMBER_FIELD)
        setattr(model, _MEMBER_FIELD, [remap.get(m, m) for m in members])


def strip_uuids(obj: BaseModel, *, group_aware: bool = True) -> None:
    """Remove `(uuid ...)` values from *obj* in place (ADR-019 points 3/5).

    With ``group_aware=True`` (default) every uuid referenced by any group
    (member uuids + the group's own uuid) is preserved, so grouped footprints
    survive stripping without group loss.

    .. warning::
       ``group_aware=False`` disables that protection and will **silently
       destroy groups**: stripping a member's uuid makes the KiCad parser drop
       the member, and an emptied group is deleted on the next save
       (`pcb_io_kicad_sexpr.cpp:2482-2483`). Only pass ``group_aware=False`` for
       a footprint you know carries no groups.

    For symbol-family objects this is a no-op (`.kicad_sym` is uuid-free).
    """
    if _is_symbol_family(obj):
        return
    models = list(_iter_models(obj))
    protected = _collect_protected(models) if group_aware else set()
    for model in models:
        fields = type(model).model_fields
        for field in _UUID_FIELDS:
            if field in fields:
                value = getattr(model, field)
                if value is not None and value not in protected:
                    setattr(model, field, None)


def generate_uuids(obj: BaseModel, *, only_missing: bool = True) -> None:
    """Inject fresh uuid4 values in place (ADR-019 point 5).

    With ``only_missing=True`` (default) a fresh uuid is minted only where one is
    absent; existing uuids - including group-referenced ones - are untouched, so
    group integrity is preserved trivially.

    With ``only_missing=False`` every uuid is regenerated; group member
    references are remapped together (old->new) so no reference is left dangling.

    For symbol-family objects this is a no-op (`.kicad_sym` is uuid-free).
    """
    if _is_symbol_family(obj):
        return
    models = list(_iter_models(obj))
    if only_missing:
        for model in models:
            fields = type(model).model_fields
            for field in _UUID_FIELDS:
                if field in fields and getattr(model, field) is None:
                    setattr(model, field, str(uuid4()))
        return

    remap: dict[str, str] = {}
    for model in models:
        fields = type(model).model_fields
        for field in _UUID_FIELDS:
            if field in fields:
                old = getattr(model, field)
                new = str(uuid4())
                if old is not None:
                    remap[old] = new
                setattr(model, field, new)
    _remap_members(models, remap)


def derive_uuids(
    obj: BaseModel,
    *,
    key: str,
    namespace: UUID = DERIVED_UUID_NAMESPACE,
) -> None:
    """Replace every uuid in *obj* with one derived from it and *key*, in place.

    ``new = uuid5(namespace, f"{old}|{key}")``. The result is a function of its
    inputs alone, so the same source and the same key give the same uuids on
    every run and on every machine. That is the difference from
    :func:`generate_uuids`, which mints a random uuid4 each time.

    *key* is a caller string that names the copy. The library never invents one:
    what makes two copies different is the caller's business, not this library's.

    An item with NO uuid keeps none, because there is nothing to derive from.
    Call ``generate_uuids(obj)`` afterwards to fill those in.

    Every uuid reference list is remapped with the rest, so a group still names
    its members and a generator still names the items it owns.

    KiCad accepts a version-5 uuid: its reader takes any well-formed value and
    never inspects the version nibble (``common/kiid.cpp``).

    For symbol-family objects this is a no-op (``.kicad_sym`` is uuid-free).
    """
    if _is_symbol_family(obj):
        return
    models = list(_iter_models(obj))
    remap: dict[str, str] = {}
    for model in models:
        fields = type(model).model_fields
        for field in _UUID_FIELDS:
            if field in fields:
                old = getattr(model, field)
                if old is None:
                    continue
                new = remap.get(old) or str(uuid5(namespace, f"{old}|{key}"))
                remap[old] = new
                setattr(model, field, new)
    _remap_members(models, remap)
