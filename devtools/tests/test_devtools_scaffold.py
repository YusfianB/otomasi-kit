import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None

from devtools.scaffold import (
    TEMPLATES_DIR,
    ScaffoldError,
    create_project,
    list_templates,
    package_name,
    slugify,
)

EXPECTED = {
    "api-fastapi": [
        "app/main.py", "app/db.py", "app/schemas.py", "app/routers/produk.py",
        "tests/test_produk.py", "Dockerfile", ".env.example", "README.md",
        ".github/workflows/ci.yml", "requirements.txt", ".gitignore",
    ],
    "web-statis": [
        "index.html", "css/style.css", "js/main.js", "404.html", ".nojekyll",
        ".github/workflows/pages.yml", "README.md",
    ],
    "bot-telegram": [
        "bot/__main__.py", "bot/handlers.py", "bot/telegram.py", "bot/pengingat.py",
        "faq.yaml", ".env.example", "tests/test_handlers.py", "README.md",
    ],
    "cli-python": [
        "pyproject.toml", "src/toko_saya/cli.py", "src/toko_saya/__main__.py",
        "tests/test_cli.py", "README.md", ".github/workflows/ci.yml",
    ],
}


def generate(tmp_path: Path, template: str, **kw) -> Path:
    folder = tmp_path / template
    project, files, _ = create_project(
        template, "Toko Saya", folder=folder, description='Deskripsi "uji" & <tes>',
        author="Budi Santoso", **kw,
    )
    assert project == folder and files
    return folder


def test_list_templates():
    names = {t.name for t in list_templates()}
    assert names == set(EXPECTED)
    assert all(t.description for t in list_templates())


def test_slug_and_package_name():
    assert slugify("  Toko Saya! ") == "toko-saya"
    assert package_name("123-app") == "proyek_123_app"
    assert package_name("class") == "proyek_class"
    with pytest.raises(ScaffoldError):
        slugify("!!!")


@pytest.mark.parametrize("template", sorted(EXPECTED))
def test_scaffold_creates_rendered_project(tmp_path, template):
    folder = generate(tmp_path, template)
    for rel in EXPECTED[template]:
        assert (folder / rel).is_file(), rel
    for path in folder.rglob("*"):
        assert not path.name.endswith(".tmpl"), path
        assert "__paket__" not in str(path) and not path.name.startswith("dot-"), path
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "{{" not in text.replace("${{", "") and "{%" not in text, path
    readme = (folder / "README.md").read_text(encoding="utf-8")
    assert "Toko Saya" in readme and "Budi Santoso" in readme
    # Every generated Python file compiles; every YAML/JSON/TOML file parses.
    for py in folder.rglob("*.py"):
        py_compile.compile(str(py), doraise=True, cfile=str(tmp_path / "x.pyc"))
    for yml in list(folder.rglob("*.yml")) + list(folder.rglob("*.yaml")):
        assert yaml.safe_load(yml.read_text(encoding="utf-8")) is not None, yml
    for js in folder.rglob("*.json"):
        json.loads(js.read_text(encoding="utf-8"))
    for toml in folder.rglob("*.toml") if tomllib else []:
        tomllib.loads(toml.read_text(encoding="utf-8"))


def test_html_escapes_description(tmp_path):
    folder = generate(tmp_path, "web-statis")
    html = (folder / "index.html").read_text(encoding="utf-8")
    assert "Deskripsi &#34;uji&#34; &amp; &lt;tes&gt;" in html
    assert "<title>Toko Saya</title>" in html


@pytest.mark.skipif(tomllib is None, reason="butuh Python 3.11+")
def test_pyproject_metadata(tmp_path):
    folder = generate(tmp_path, "cli-python")
    data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "toko-saya"
    assert data["project"]["description"] == 'Deskripsi "uji" & <tes>'
    assert data["project"]["scripts"]["toko-saya"] == "toko_saya.cli:main"


def test_refuses_non_empty_folder(tmp_path):
    folder = tmp_path / "ada"
    folder.mkdir()
    (folder / "penting.txt").write_text("jangan ditimpa", encoding="utf-8")
    with pytest.raises(ScaffoldError, match="--paksa"):
        create_project("cli-python", "ada", folder=folder)
    create_project("cli-python", "ada", folder=folder, force=True)
    assert (folder / "penting.txt").read_text(encoding="utf-8") == "jangan ditimpa"
    assert (folder / "pyproject.toml").is_file()


def test_unknown_template(tmp_path):
    with pytest.raises(ScaffoldError, match="tidak ada"):
        create_project("tidak-ada", "x", folder=tmp_path / "x")


def test_default_folder_is_slug_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project, _, _ = create_project("web-statis", "Kedai Kopi")
    assert project == tmp_path / "kedai-kopi"
    assert (project / "index.html").is_file()


def test_template_sources_are_not_python_files():
    """Template code must use .tmpl so this repo's ruff/pytest never pick it up."""
    offenders = [p for p in TEMPLATES_DIR.rglob("*.py")]
    assert offenders == []


def _run_pytest(folder: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=folder, capture_output=True, text=True, timeout=120, check=False,
    )


@pytest.mark.skipif(sys.version_info < (3, 11), reason="template cli-python butuh 3.11+")
def test_generated_cli_python_tests_pass(tmp_path):
    folder = generate(tmp_path, "cli-python")
    proc = _run_pytest(folder)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_generated_bot_telegram_tests_pass(tmp_path):
    pytest.importorskip("requests")
    folder = generate(tmp_path, "bot-telegram")
    proc = _run_pytest(folder)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_generated_api_fastapi_repository_tests_pass(tmp_path):
    # FastAPI is not a dependency of this repo; the repository layer is stdlib-only.
    folder = generate(tmp_path, "api-fastapi")
    proc = _run_pytest(folder, "tests/test_db.py")
    assert proc.returncode == 0, proc.stdout + proc.stderr
