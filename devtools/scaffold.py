"""Create new projects from the templates in ``devtools/templates``.

Template layout::

    devtools/templates/<name>/
        template.yaml      # metadata: judul, deskripsi, deskripsi_default, langkah (next steps)
        files/             # the project tree
            ...

Rules applied while copying ``files/``:

- a file ending in ``.tmpl`` is rendered with Jinja2 and the suffix is stripped
  (so ``main.py.tmpl`` -> ``main.py``; this also keeps ruff/pytest of *this* repo away
  from template code); other files are copied byte-for-byte
- path segment ``__paket__`` -> python package name, ``__nama__`` -> project slug
- path segment starting with ``dot-`` -> ``.`` (``dot-gitignore`` -> ``.gitignore``,
  ``dot-github`` -> ``.github``) so dotfiles are not hidden/ignored inside this repo
"""

from __future__ import annotations

import getpass
import keyword
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import jinja2
import yaml

from devtools.backup import BackupError

log = logging.getLogger("devtools.scaffold")

TEMPLATES_DIR = Path(__file__).parent / "templates"
TMPL_SUFFIX = ".tmpl"


class ScaffoldError(BackupError):
    """Scaffold error with a user-facing message."""


@dataclass
class TemplateInfo:
    name: str
    title: str
    description: str
    path: Path
    meta: dict[str, Any]


def list_templates(templates_dir: Path = TEMPLATES_DIR) -> list[TemplateInfo]:
    out = []
    for d in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        meta_file = d / "template.yaml"
        if not meta_file.is_file() or not (d / "files").is_dir():
            continue
        meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
        out.append(
            TemplateInfo(
                name=d.name,
                title=str(meta.get("judul", d.name)),
                description=str(meta.get("deskripsi", "")),
                path=d,
                meta=meta,
            )
        )
    return out


def get_template(name: str, templates_dir: Path = TEMPLATES_DIR) -> TemplateInfo:
    for t in list_templates(templates_dir):
        if t.name == name:
            return t
    names = ", ".join(t.name for t in list_templates(templates_dir))
    raise ScaffoldError(f"Template '{name}' tidak ada. Template yang tersedia: {names}")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ScaffoldError(f"Nama proyek '{name}' tidak valid: butuh minimal satu huruf/angka.")
    return slug


def package_name(slug: str) -> str:
    pkg = slug.replace("-", "_")
    if pkg[0].isdigit() or keyword.iskeyword(pkg):
        pkg = f"proyek_{pkg}"
    return pkg


def build_context(
    name: str, description: str | None, author: str | None, template: TemplateInfo
) -> dict[str, Any]:
    slug = slugify(name)
    judul = " ".join(w.capitalize() for w in slug.split("-"))
    if not author:
        try:
            author = getpass.getuser()
        except Exception:  # noqa: BLE001 - getuser can raise many things on odd systems
            author = "Penulis"
    default_desc = str(template.meta.get("deskripsi_default", "Proyek {judul}"))
    return {
        "nama": name.strip(),
        "slug": slug,
        "paket": package_name(slug),
        "judul": judul,
        "deskripsi": description or default_desc.format(judul=judul, slug=slug),
        "penulis": author,
        "tahun": date.today().year,
        "tanggal": date.today().isoformat(),
        "template": template.name,
    }


def _render_rel_path(rel: Path, ctx: dict[str, Any]) -> Path:
    parts = []
    for part in rel.parts:
        part = part.replace("__paket__", ctx["paket"]).replace("__nama__", ctx["slug"])
        if part.startswith("dot-"):
            part = "." + part[4:]
        parts.append(part)
    out = Path(*parts)
    if out.name.endswith(TMPL_SUFFIX):
        out = out.with_name(out.name[: -len(TMPL_SUFFIX)])
    return out


def _is_effectively_empty(folder: Path) -> bool:
    return not folder.exists() or not any(folder.iterdir())


def create_project(
    template_name: str,
    name: str,
    folder: Path | None = None,
    description: str | None = None,
    author: str | None = None,
    force: bool = False,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[Path, list[Path], dict[str, Any]]:
    """Render a template into ``folder`` (default ``./<slug>``).

    Returns ``(project_dir, created_files, context)``.
    """
    template = get_template(template_name, templates_dir)
    ctx = build_context(name, description, author, template)
    target = Path(folder) if folder else Path.cwd() / ctx["slug"]
    if target.exists() and not target.is_dir():
        raise ScaffoldError(f"{target} sudah ada dan bukan folder.")
    if not _is_effectively_empty(target) and not force:
        raise ScaffoldError(
            f"Folder {target} sudah ada dan tidak kosong. Gunakan --paksa untuk menimpa "
            "(file lain di folder itu tidak dihapus)."
        )

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template.path / "files")),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    files_root = template.path / "files"
    created: list[Path] = []
    for src in sorted(files_root.rglob("*")):
        if src.is_dir() or src.name in {".DS_Store"} or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(files_root)
        out = target / _render_rel_path(rel, ctx)
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.name.endswith(TMPL_SUFFIX):
            try:
                text = env.get_template(rel.as_posix()).render(**ctx)
            except jinja2.TemplateError as exc:
                raise ScaffoldError(f"Gagal merender {rel}: {exc}") from exc
            out.write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, out)
        if src.stat().st_mode & 0o111:
            out.chmod(out.stat().st_mode | 0o755)
        created.append(out)
        log.debug("dibuat: %s", out)
    log.info("Proyek '%s' dibuat dari template %s di %s (%d file)",
             ctx["judul"], template.name, target, len(created))
    return target, created, ctx


def next_steps(template_name: str, project_dir: Path, ctx: dict[str, Any]) -> list[str]:
    template = get_template(template_name)
    steps = template.meta.get("langkah", []) or []
    folder = str(project_dir)
    return [str(s).format(folder=folder, **ctx) for s in steps]
