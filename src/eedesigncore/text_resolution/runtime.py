"""Opt-in runtime inputs for text. Repository access always has an explicit root."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from eedesigncore.common.paths import is_remote_uri
from eedesigncore.text_resolution.expressions import _date, number, shown
from eedesigncore.text_resolution.models import ExpressionValue, TextResolutionContext


class GitTextResolver:
    """Read Git text values from one explicit local repository.

    HEAD is captured at construction. Results cache per call and argument list.
    Create a new resolver to refresh. Commands are read-only, bounded by timeout,
    and never run through a command shell. File arguments must stay in the root.
    """

    def __init__(self, repository: str | Path) -> None:
        if is_remote_uri(str(repository)) or "\0" in str(repository):
            raise ValueError("Git text context requires a local repository directory.")
        self.root = Path(repository).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Git text context requires a directory.")
        executable = shutil.which("git")
        if executable is None:
            raise ValueError("Git is unavailable for the requested text context.")
        self.executable = executable
        top = self._git("rev-parse", "--show-toplevel")
        if not top:
            raise ValueError("The selected directory is not a readable Git repository.")
        self.repository_root = Path(top).resolve(strict=True)
        self.commit = self._git("rev-parse", "--verify", "HEAD", allow_missing=True)
        self.cache: dict[tuple[str, tuple[ExpressionValue, ...]], ExpressionValue] = {}

    def _git(self, *arguments: str, allow_missing: bool = False) -> str:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        try:
            result = subprocess.run(
                [
                    self.executable,
                    "--no-pager",
                    "--literal-pathspecs",
                    "-c",
                    "core.quotepath=false",
                    *arguments,
                ],
                cwd=getattr(self, "repository_root", self.root),
                env=environment,
                capture_output=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("The Git text query failed or exceeded its time limit.") from error
        if len(result.stdout) > 1_000_000:
            raise ValueError("The Git text result exceeds the supported length.")
        if result.returncode:
            if allow_missing:
                return ""
            raise ValueError(f"The Git text query failed with exit status {result.returncode}.")
        return result.stdout.rstrip("\r\n")

    def _file(self, argument: ExpressionValue) -> str:
        text = shown(argument)
        if is_remote_uri(text) or "\0" in text:
            raise ValueError("Git text file arguments must be local paths.")
        path = (self.root / text).resolve()
        if not path.is_relative_to(self.repository_root):
            raise ValueError("Git text file arguments must stay inside the selected repository.")
        return path.relative_to(self.repository_root).as_posix()

    def _log(self, format_string: str, file: str | None = None) -> str:
        if not self.commit:
            return ""
        commit = self.commit
        if file is not None and file not in (".", ""):
            history = self._git("rev-list", "--timestamp", self.commit).splitlines()
            if len(history) > 4096:
                raise ValueError("The Git file query exceeds the supported history limit.")
            previous = None
            commit = ""
            for row in sorted(history, key=lambda item: int(item.split()[0]), reverse=True):
                current = row.split()[1]
                entry = self._git("ls-tree", current, "--", file)
                blob = entry.split("\t", 1)[0].split()[-1] if entry else None
                if previous is not None and blob != previous:
                    break
                if blob is not None:
                    previous, commit = blob, current
            if not commit:
                return ""
        return self._git("log", "-1", "--format=" + format_string, commit)

    def __call__(
        self, name: str, arguments: tuple[ExpressionValue, ...]
    ) -> ExpressionValue | None:
        key = name, arguments
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= 1024:
            raise ValueError("Git text queries exceed the supported call count.")
        value = self._resolve(name, arguments)
        if value is not None:
            self.cache[key] = value
        return value

    def _resolve(self, name: str, args: tuple[ExpressionValue, ...]) -> ExpressionValue | None:
        file = self._file(args[0]) if name.startswith("vcsfile") and args else None
        base = name.replace("vcsfile", "vcs", 1) if file is not None else name
        values = args[1:] if file is not None else args
        formats = {
            "vcsidentifier": "%H",
            "vcsauthor": "%an",
            "vcsauthoremail": "%ae",
            "vcscommitter": "%cn",
            "vcscommitteremail": "%ce",
            "vcscommitdate": "%ct",
        }
        if base in formats:
            value = self._log(formats[base], file)
            if not value:
                return "<unknown>"
            if base == "vcsidentifier":
                try:
                    length = int(number(values[0])) if values else 40
                except ValueError:
                    length = 40
                return value[: max(4, min(40, length))]
            if base == "vcscommitdate":
                return _date(int(int(value) / 86400), shown(values[0]) if values else "ISO")
            return value
        if name == "vcsbranch":
            return (
                self._git("symbolic-ref", "--quiet", "--short", "HEAD", allow_missing=True)
                or "<unknown>"
            )
        if name in ("vcsdirty", "vcsdirtysuffix"):
            index = 0 if name == "vcsdirty" else 1
            include = len(args) > index and bool(number(args[index]))
            dirty = bool(
                self._git(
                    "status",
                    "--porcelain",
                    "--untracked-files=" + ("all" if include else "no"),
                    "--ignore-submodules=" + ("none" if include else "all"),
                )
            )
            if name == "vcsdirty":
                return "1" if dirty else "0"
            return (shown(args[0]) if args else "-dirty") if dirty else ""
        if name in ("vcsnearestlabel", "vcslabeldistance"):
            match = shown(args[0]) if args else "*"
            any_tags = len(args) > 1 and bool(number(args[1]))
            tagged: dict[str, str] = {}
            for row in self._git(
                "for-each-ref",
                "--format=%(objectname)%09%(objecttype)%09%(*objectname)%09%(refname:strip=2)",
                "refs/tags",
            ).splitlines():
                oid, kind, peeled, label = row.split("\t", 3)
                if fnmatch.fnmatchcase(label, match or "*") and (kind == "tag" or any_tags):
                    tagged[peeled if kind == "tag" else oid] = label
            if self.commit:
                commits = self._git("rev-list", "--topo-order", self.commit).splitlines()
                for distance, commit in enumerate(commits):
                    if commit in tagged:
                        return tagged[commit] if name == "vcsnearestlabel" else str(distance)
            return "<unknown>" if name == "vcsnearestlabel" else "0"
        return None

    def variable(self, item_path: str, token: str) -> str | None:
        """Resolve the project Git hash variables from this same repository."""
        if token == "VCSHASH":
            return self.commit or "no hash"
        if token == "VCSSHORTHASH":
            return self.commit[:8] if self.commit else "no hash"
        return None


def capture_text_context(*, repository: str | Path | None = None) -> TextResolutionContext:
    """Capture the local clock. Enable Git reads only for an explicit repository."""
    import random

    generator = random.Random()
    git = GitTextResolver(repository) if repository is not None else None
    return TextResolutionContext(
        now=datetime.now().astimezone(),
        random=generator.random,
        function_provider=git,
        variable_provider=git.variable if git else None,
    )
