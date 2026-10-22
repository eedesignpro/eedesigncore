"""Unit tests for the project sheet-tree helper (T9, R10, ADR-051).

Every synthetic schematic here is written through the real writer, so the
loader under test reads the same grammar KiCad writes. One test reuses the
corpus fixture ``t4_multisheet_parent.kicad_sch``, which instantiates one child
file twice and carries real page numbers.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from eedesigncore import SheetHierarchyError, load_hierarchy
from eedesigncore.common.models import Property
from eedesigncore.project.hierarchy import MAX_SCHEMATIC_FILE_BYTES, _WalkContext
from eedesigncore.sch.models import (
    RootInstancePath,
    Schematic,
    Sheet,
    SheetInstancePath,
    SheetInstanceProject,
    SheetInstances,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sch"

PROJECT_NAME = "demo"
ROOT_UUID = "00000000-0000-4000-8000-000000000001"


def _uuid(tag: int) -> str:
    return f"00000000-0000-4000-8000-{tag:012d}"


def _sheet(
    *,
    uuid: str,
    file_name: str,
    name: str = "",
    pages: Sequence[tuple[str, str]] = (),
) -> Sheet:
    """A sheet node pointing at *file_name*, with (parent path, page) pairs."""
    fields = [Property(name="Sheetfile", value=file_name)]
    if name:
        fields.insert(0, Property(name="Sheetname", value=name))
    instances = (
        [
            SheetInstanceProject(
                name=PROJECT_NAME,
                paths=[SheetInstancePath(path=path, page=page) for path, page in pages],
            )
        ]
        if pages
        else []
    )
    return Sheet(uuid=uuid, fields=fields, instances=instances)


def _write_sch(
    path: Path,
    *,
    uuid: str,
    sheets: Sequence[Sheet] = (),
    root_page: str | None = None,
    root_instance_path: str = "/",
) -> Path:
    schematic = Schematic(
        uuid=uuid,
        sheets=list(sheets),
        sheet_instances=(
            SheetInstances(paths=[RootInstancePath(path=root_instance_path, page=root_page)])
            if root_page is not None
            else None
        ),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    schematic.to_file(path, line_ending="lf")
    return path


def _write_pro(path: Path, sheets: Sequence[tuple[str, str, str]] | None = None) -> Path:
    """A project file. Each sheets entry is (filename, name, uuid)."""
    document: dict[str, object] = {"meta": {"filename": path.name, "version": 3}}
    if sheets is not None:
        document["schematic"] = {
            "top_level_sheets": [
                {"filename": filename, "name": name, "uuid": uuid}
                for filename, name, uuid in sheets
            ]
        }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _simple_project(root: Path) -> Path:
    """Root with one child, both carrying page numbers."""
    _write_sch(
        root / "child.kicad_sch",
        uuid=_uuid(20),
        root_page=None,
    )
    _write_sch(
        root / f"{PROJECT_NAME}.kicad_sch",
        uuid=ROOT_UUID,
        sheets=[
            _sheet(
                uuid=_uuid(2),
                file_name="child.kicad_sch",
                name="CHILD",
                pages=[(f"/{ROOT_UUID}", "2")],
            )
        ],
        root_page="1",
    )
    return _write_pro(
        root / f"{PROJECT_NAME}.kicad_pro",
        [(f"{PROJECT_NAME}.kicad_sch", PROJECT_NAME, ROOT_UUID)],
    )


class TestProjectRootDiscovery:
    def test_a_project_file_supplies_the_root_sheet(self, tmp_path: Path) -> None:
        project = _simple_project(tmp_path)
        tree = load_hierarchy(project)
        assert tree.root.path == (tmp_path / f"{PROJECT_NAME}.kicad_sch").resolve()
        assert tree.project_path == project.resolve()
        assert tree.project is not None
        assert tree.project.meta.version == 3

    def test_a_schematic_start_needs_no_project_file(self, tmp_path: Path) -> None:
        root = _write_sch(tmp_path / "solo.kicad_sch", uuid=ROOT_UUID, root_page="1")
        tree = load_hierarchy(root)
        assert tree.project_path is None
        assert tree.project is None
        assert tree.root.name == "solo"

    def test_a_schematic_start_still_loads_a_sibling_project_file(self, tmp_path: Path) -> None:
        _simple_project(tmp_path)
        tree = load_hierarchy(tmp_path / f"{PROJECT_NAME}.kicad_sch")
        assert tree.project is not None
        assert tree.project_path is None

    def test_an_empty_top_level_sheet_list_falls_back_to_the_project_name(
        self, tmp_path: Path
    ) -> None:
        _write_sch(tmp_path / f"{PROJECT_NAME}.kicad_sch", uuid=ROOT_UUID, root_page="1")
        project = _write_pro(tmp_path / f"{PROJECT_NAME}.kicad_pro", [])
        assert load_hierarchy(project).root.name == PROJECT_NAME

    def test_a_project_file_without_a_schematic_block_uses_the_same_fallback(
        self, tmp_path: Path
    ) -> None:
        _write_sch(tmp_path / f"{PROJECT_NAME}.kicad_sch", uuid=ROOT_UUID, root_page="1")
        project = _write_pro(tmp_path / f"{PROJECT_NAME}.kicad_pro", None)
        assert load_hierarchy(project).root.path.name == f"{PROJECT_NAME}.kicad_sch"

    def test_a_stale_template_reference_falls_back_to_the_project_name(
        self, tmp_path: Path
    ) -> None:
        _write_sch(tmp_path / f"{PROJECT_NAME}.kicad_sch", uuid=ROOT_UUID, root_page="1")
        project = _write_pro(
            tmp_path / f"{PROJECT_NAME}.kicad_pro",
            [("template_original.kicad_sch", "template_original", ROOT_UUID)],
        )
        assert load_hierarchy(project).root.path.name == f"{PROJECT_NAME}.kicad_sch"

    def test_two_top_level_sheets_produce_two_roots(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "first.kicad_sch", uuid=_uuid(11), root_page="1")
        _write_sch(tmp_path / "second.kicad_sch", uuid=_uuid(12), root_page="1")
        project = _write_pro(
            tmp_path / f"{PROJECT_NAME}.kicad_pro",
            [
                ("first.kicad_sch", "FIRST", _uuid(11)),
                ("second.kicad_sch", "SECOND", _uuid(12)),
            ],
        )
        tree = load_hierarchy(project)
        assert [node.name for node in tree.roots] == ["FIRST", "SECOND"]
        with pytest.raises(SheetHierarchyError, match="exactly one top-level sheet"):
            _ = tree.root

    def test_a_missing_root_always_raises(self, tmp_path: Path) -> None:
        project = _write_pro(
            tmp_path / f"{PROJECT_NAME}.kicad_pro",
            [("absent.kicad_sch", "ABSENT", ROOT_UUID)],
        )
        with pytest.raises(SheetHierarchyError, match="Top-level sheet file not found"):
            load_hierarchy(project, strict=False)

    def test_an_unknown_suffix_raises(self, tmp_path: Path) -> None:
        board = tmp_path / "demo.kicad_pcb"
        board.write_text("(kicad_pcb)", encoding="utf-8")
        with pytest.raises(SheetHierarchyError, match="Expected a .kicad_pro"):
            load_hierarchy(board)


class TestTraversal:
    def test_the_tree_carries_paths_names_and_pages(self, tmp_path: Path) -> None:
        project = _simple_project(tmp_path)
        tree = load_hierarchy(project)
        root = tree.root
        child = root.children[0]
        assert root.is_root
        assert root.page == "1"
        assert root.kiid_path == f"/{ROOT_UUID}"
        assert not child.is_root
        assert child.name == "CHILD"
        assert child.page == "2"
        assert child.kiid_path == f"/{ROOT_UUID}/{_uuid(2)}"
        assert child.sheet is not None
        assert child.path == (tmp_path / "child.kicad_sch").resolve()

    def test_walk_is_depth_first_and_includes_every_node(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "leaf.kicad_sch", uuid=_uuid(30))
        _write_sch(
            tmp_path / "mid.kicad_sch",
            uuid=_uuid(20),
            sheets=[_sheet(uuid=_uuid(3), file_name="leaf.kicad_sch", name="LEAF")],
        )
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[
                _sheet(uuid=_uuid(2), file_name="mid.kicad_sch", name="MID"),
                _sheet(uuid=_uuid(4), file_name="leaf.kicad_sch", name="SECOND LEAF"),
            ],
            root_page="1",
        )
        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        assert [node.name for node in tree.walk()] == ["root", "MID", "LEAF", "SECOND LEAF"]

    def test_a_page_without_a_matching_instance_path_reads_none(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[
                _sheet(
                    uuid=_uuid(2),
                    file_name="child.kicad_sch",
                    pages=[("/some-other-root", "9")],
                )
            ],
        )
        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        assert tree.root.page is None
        assert tree.root.children[0].page is None

    def test_a_root_block_without_the_slash_entry_uses_its_first_entry(
        self, tmp_path: Path
    ) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            root_page="7",
            root_instance_path=f"/{ROOT_UUID}",
        )
        assert load_hierarchy(tmp_path / "root.kicad_sch").root.page == "7"

    def test_a_sheet_name_defaults_to_the_file_stem(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="child.kicad_sch")],
        )
        assert load_hierarchy(tmp_path / "root.kicad_sch").root.children[0].name == "child"

    def test_a_child_in_a_subdirectory_resolves_against_its_parent(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "sub" / "leaf.kicad_sch", uuid=_uuid(30))
        _write_sch(
            tmp_path / "sub" / "mid.kicad_sch",
            uuid=_uuid(20),
            sheets=[_sheet(uuid=_uuid(3), file_name="leaf.kicad_sch", name="LEAF")],
        )
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="sub/mid.kicad_sch", name="MID")],
        )
        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        leaf = tree.root.children[0].children[0]
        assert leaf.path == (tmp_path / "sub" / "leaf.kicad_sch").resolve()

    def test_the_helper_never_mutates_the_files_it_reads(self, tmp_path: Path) -> None:
        _simple_project(tmp_path)
        before = {path: path.read_bytes() for path in tmp_path.glob("*.kicad_sch")}
        load_hierarchy(tmp_path / f"{PROJECT_NAME}.kicad_pro")
        assert {path: path.read_bytes() for path in tmp_path.glob("*.kicad_sch")} == before

    def test_every_loaded_file_still_round_trips_byte_identically(self, tmp_path: Path) -> None:
        _simple_project(tmp_path)
        tree = load_hierarchy(tmp_path / f"{PROJECT_NAME}.kicad_pro")
        for node in tree.walk():
            assert node.schematic.to_string() == node.path.read_text(encoding="utf-8")


class TestSameFileTwice:
    def test_two_instances_of_one_file_are_two_nodes(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[
                _sheet(
                    uuid=_uuid(2),
                    file_name="child.kicad_sch",
                    name="COPY A",
                    pages=[(f"/{ROOT_UUID}", "2")],
                ),
                _sheet(
                    uuid=_uuid(3),
                    file_name="child.kicad_sch",
                    name="COPY B",
                    pages=[(f"/{ROOT_UUID}", "3")],
                ),
            ],
            root_page="1",
        )
        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        first, second = tree.root.children
        assert [first.name, second.name] == ["COPY A", "COPY B"]
        assert [first.page, second.page] == ["2", "3"]
        assert first.kiid_path != second.kiid_path
        assert len(tree.files()) == 2

    def test_one_file_loads_once_and_both_nodes_share_it(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[
                _sheet(uuid=_uuid(2), file_name="child.kicad_sch"),
                _sheet(uuid=_uuid(3), file_name="./child.kicad_sch"),
            ],
        )
        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        first, second = tree.root.children
        assert first.schematic is second.schematic

    def test_the_corpus_multisheet_parent_instantiates_one_child_twice(
        self, tmp_path: Path
    ) -> None:
        shutil.copy(FIXTURES / "t4_multisheet_parent.kicad_sch", tmp_path / "root.kicad_sch")
        _write_sch(tmp_path / "array.kicad_sch", uuid=_uuid(40))
        _write_sch(tmp_path / "connector.kicad_sch", uuid=_uuid(41))

        tree = load_hierarchy(tmp_path / "root.kicad_sch")
        assert tree.root.page == "1"
        pages = [node.page for node in tree.root.children]
        files = [node.path.name for node in tree.root.children]
        assert files == ["array.kicad_sch", "array.kicad_sch", "connector.kicad_sch"]
        assert pages == ["3", "20", "2"]
        assert len(tree.files()) == 3


class TestMissingFiles:
    def test_a_missing_child_raises_under_strict(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="absent.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="Sub-sheet file not found"):
            load_hierarchy(tmp_path / "root.kicad_sch")

    def test_a_missing_child_is_collected_when_not_strict(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[
                _sheet(uuid=_uuid(2), file_name="absent.kicad_sch"),
                _sheet(uuid=_uuid(3), file_name="present.kicad_sch", name="PRESENT"),
            ],
        )
        _write_sch(tmp_path / "present.kicad_sch", uuid=_uuid(20))
        tree = load_hierarchy(tmp_path / "root.kicad_sch", strict=False)
        assert [node.name for node in tree.root.children] == ["PRESENT"]
        assert len(tree.errors) == 1
        assert "absent.kicad_sch" in tree.errors[0]

    def test_a_sheet_without_a_sheetfile_field_is_a_problem(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[Sheet(uuid=_uuid(2), fields=[Property(name="Sheetname", value="NAMELESS")])],
        )
        with pytest.raises(SheetHierarchyError, match="without a Sheetfile field"):
            load_hierarchy(tmp_path / "root.kicad_sch")
        tree = load_hierarchy(tmp_path / "root.kicad_sch", strict=False)
        assert tree.root.children == []
        assert len(tree.errors) == 1


class TestCycles:
    def test_a_self_referencing_file_raises(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="root.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="direct ancestor"):
            load_hierarchy(tmp_path / "root.kicad_sch")

    def test_a_two_file_cycle_raises(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="child.kicad_sch")],
        )
        _write_sch(
            tmp_path / "child.kicad_sch",
            uuid=_uuid(20),
            sheets=[_sheet(uuid=_uuid(3), file_name="root.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="direct ancestor"):
            load_hierarchy(tmp_path / "root.kicad_sch")

    def test_a_cycle_raises_even_when_not_strict(self, tmp_path: Path) -> None:
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="root.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="direct ancestor"):
            load_hierarchy(tmp_path / "root.kicad_sch", strict=False)


class TestPathSafety:
    def test_a_relative_escape_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        inside = tmp_path / "project"
        _write_sch(outside / "secret.kicad_sch", uuid=_uuid(20))
        _write_sch(
            inside / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="../outside/secret.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="outside the project root"):
            load_hierarchy(inside / "root.kicad_sch")

    def test_an_absolute_escape_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        inside = tmp_path / "project"
        target = _write_sch(outside / "secret.kicad_sch", uuid=_uuid(20))
        _write_sch(
            inside / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name=str(target))],
        )
        with pytest.raises(SheetHierarchyError, match="outside the project root"):
            load_hierarchy(inside / "root.kicad_sch")

    def test_the_caller_opts_in_with_allowed_roots(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        inside = tmp_path / "project"
        _write_sch(outside / "shared.kicad_sch", uuid=_uuid(20))
        _write_sch(
            inside / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="../outside/shared.kicad_sch", name="SHARED")],
        )
        tree = load_hierarchy(inside / "root.kicad_sch", allowed_roots=[outside])
        assert tree.root.children[0].path == (outside / "shared.kicad_sch").resolve()

    def test_the_project_root_stays_allowed_alongside_an_extra_root(self, tmp_path: Path) -> None:
        inside = tmp_path / "project"
        _write_sch(inside / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            inside / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="child.kicad_sch", name="CHILD")],
        )
        tree = load_hierarchy(inside / "root.kicad_sch", allowed_roots=[tmp_path / "elsewhere"])
        assert tree.root.children[0].name == "CHILD"


class TestRemoteReferenceRejection:
    """LOW-5: a remote sheet reference must not reach the network, not even a resolve."""

    UNC_FORMS = (
        "//attacker.example/share/x.kicad_sch",
        "\\\\attacker.example\\share\\x.kicad_sch",
    )

    def _project_with_reference(self, root: Path, reference: str) -> Path:
        return _write_sch(
            root / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name=reference)],
        )

    @pytest.mark.parametrize("reference", UNC_FORMS)
    def test_a_unc_sheetfile_is_refused(self, tmp_path: Path, reference: str) -> None:
        start = self._project_with_reference(tmp_path, reference)
        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start)

    @pytest.mark.parametrize("reference", UNC_FORMS)
    def test_a_unc_sheetfile_never_reaches_path_resolution(
        self, tmp_path: Path, reference: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        start = self._project_with_reference(tmp_path, reference)
        seen: list[str] = []
        real = Path.resolve

        def spy(self: Path, strict: bool = False) -> Path:
            seen.append(str(self))
            return real(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", spy)

        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start)

        assert seen
        assert not any(candidate.replace("\\", "/").startswith("//") for candidate in seen)

    def test_a_url_scheme_sheetfile_is_refused(self, tmp_path: Path) -> None:
        start = self._project_with_reference(tmp_path, "http://attacker.example/x.kicad_sch")
        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start)

    def test_the_refusal_survives_a_non_strict_walk(self, tmp_path: Path) -> None:
        """A remote reference is a refusal, not a collected missing-file problem."""
        start = self._project_with_reference(tmp_path, self.UNC_FORMS[0])
        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start, strict=False)

    def test_a_unc_top_level_sheet_entry_is_refused(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / f"{PROJECT_NAME}.kicad_sch", uuid=ROOT_UUID)
        start = _write_pro(
            tmp_path / f"{PROJECT_NAME}.kicad_pro",
            [(self.UNC_FORMS[0], PROJECT_NAME, ROOT_UUID)],
        )
        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start)

    def test_an_allowed_unc_root_covers_a_unc_reference(self) -> None:
        """An explicit UNC allowed root opts in, and the guard steps aside.

        The guard runs alone here. Walking the tree would open the share, which
        is exactly the network access these tests must never perform.
        """
        context = _WalkContext(
            project_root=Path("C:/proj"),
            allowed=[Path("C:/proj"), Path("//host/share")],
            strict=True,
            max_depth=1,
            max_files=1,
        )

        context.reject_remote("//HOST/Share/lib/x.kicad_sch", Path("C:/proj/root.kicad_sch"))

        assert context.check_allowed(Path("//host/share/lib/x.kicad_sch")) == Path(
            "//host/share/lib/x.kicad_sch"
        )

    def test_a_local_root_never_covers_a_unc_reference(self) -> None:
        context = _WalkContext(
            project_root=Path("C:/proj"),
            allowed=[Path("C:/proj")],
            strict=True,
            max_depth=1,
            max_files=1,
        )

        with pytest.raises(SheetHierarchyError, match="outside the project root"):
            context.check_allowed(Path("//host/share/x.kicad_sch"))

    def test_another_unc_host_stays_refused(self, tmp_path: Path) -> None:
        start = self._project_with_reference(tmp_path, "//other/share/x.kicad_sch")
        with pytest.raises(SheetHierarchyError, match="remote location"):
            load_hierarchy(start, allowed_roots=[Path("//host/share")])


class TestCaps:
    def test_the_depth_cap_raises(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "leaf.kicad_sch", uuid=_uuid(30))
        _write_sch(
            tmp_path / "mid.kicad_sch",
            uuid=_uuid(20),
            sheets=[_sheet(uuid=_uuid(3), file_name="leaf.kicad_sch")],
        )
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="mid.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="depth cap"):
            load_hierarchy(tmp_path / "root.kicad_sch", max_depth=1)

    def test_the_file_budget_raises(self, tmp_path: Path) -> None:
        _write_sch(tmp_path / "child.kicad_sch", uuid=_uuid(20))
        _write_sch(
            tmp_path / "root.kicad_sch",
            uuid=ROOT_UUID,
            sheets=[_sheet(uuid=_uuid(2), file_name="child.kicad_sch")],
        )
        with pytest.raises(SheetHierarchyError, match="file budget"):
            load_hierarchy(tmp_path / "root.kicad_sch", max_files=1)

    def test_the_read_cap_refuses_an_oversize_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_sch(tmp_path / "root.kicad_sch", uuid=ROOT_UUID)
        monkeypatch.setattr(
            "eedesigncore.project.hierarchy.MAX_SCHEMATIC_FILE_BYTES",
            8,
        )
        with pytest.raises(SheetHierarchyError, match="read cap"):
            load_hierarchy(tmp_path / "root.kicad_sch")

    def test_the_read_cap_refuses_an_oversize_project_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_sch(tmp_path / f"{PROJECT_NAME}.kicad_sch", uuid=ROOT_UUID)
        project = _write_pro(tmp_path / f"{PROJECT_NAME}.kicad_pro", [])
        monkeypatch.setattr("eedesigncore.project.hierarchy.MAX_SCHEMATIC_FILE_BYTES", 8)
        with pytest.raises(SheetHierarchyError, match="Project file is"):
            load_hierarchy(project)

    def test_the_default_read_cap_is_sixty_four_megabytes(self) -> None:
        assert MAX_SCHEMATIC_FILE_BYTES == 64 * 1024 * 1024
