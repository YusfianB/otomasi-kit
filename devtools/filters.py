"""Shared include/exclude glob matching and directory walking for backup and sync."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
)


def _matches(rel_posix: str, patterns: Iterable[str]) -> bool:
    """True if a relative POSIX path, or any of its components, matches a glob pattern.

    - A pattern without ``/`` is matched against every path component, so ``node_modules``
      or ``*.log`` match at any depth.
    - A pattern containing ``/`` is matched against the whole relative path
      (``fnmatch``'s ``*`` also crosses ``/``, so ``data/*.csv`` matches ``data/a/b.csv``).
      A trailing ``/**`` matches the directory itself as well.
    """
    parts = rel_posix.split("/")
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        pat = pat.rstrip("/") if pat != "/" else pat
        if "/" in pat:
            if pat.startswith("./"):
                pat = pat[2:]
            if fnmatch.fnmatchcase(rel_posix, pat):
                return True
            if pat.endswith("/**") and fnmatch.fnmatchcase(rel_posix, pat[:-3]):
                return True
            # Pattern names a directory prefix: "build/cache" excludes everything below it.
            if rel_posix.startswith(pat + "/"):
                return True
        else:
            if any(fnmatch.fnmatchcase(p, pat) for p in parts):
                return True
    return False


@dataclass
class PathFilter:
    """Decides which files of a source tree are taken."""

    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    use_default_excludes: bool = True
    # Absolute paths that are always skipped (e.g. the backup destination inside a source).
    skip_paths: list[Path] = field(default_factory=list)

    @property
    def all_excludes(self) -> list[str]:
        base = list(DEFAULT_EXCLUDES) if self.use_default_excludes else []
        return base + list(self.exclude)

    def is_excluded(self, rel_posix: str) -> bool:
        return _matches(rel_posix, self.all_excludes)

    def is_included(self, rel_posix: str) -> bool:
        """Include patterns apply to files only; empty list means 'everything'."""
        if not self.include:
            return True
        return _matches(rel_posix, self.include)

    def _skipped(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        for skip in self.skip_paths:
            if resolved == skip or skip in resolved.parents:
                return True
        return False

    def walk(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield ``(absolute_path, relative_posix_path)`` for every selected file under root.

        Symlinked directories are not followed; symlinked files are read as regular files.
        """
        root = Path(root)
        self.skip_paths = [Path(p).resolve() for p in self.skip_paths]
        for dirpath, dirnames, filenames in os.walk(root):
            dpath = Path(dirpath)
            rel_dir = dpath.relative_to(root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            kept = []
            for d in sorted(dirnames):
                rel = f"{rel_dir}/{d}" if rel_dir else d
                full = dpath / d
                if self.is_excluded(rel) or full.is_symlink() or self._skipped(full):
                    continue
                kept.append(d)
            dirnames[:] = kept
            for f in sorted(filenames):
                rel = f"{rel_dir}/{f}" if rel_dir else f
                full = dpath / f
                if self.is_excluded(rel) or not self.is_included(rel):
                    continue
                if self._skipped(full) or not full.is_file():
                    continue
                yield full, rel


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
