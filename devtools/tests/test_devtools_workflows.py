"""Schema-ish sanity checks for the GitHub Actions workflows (actionlint is not available
everywhere, so the most common mistakes are checked here)."""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GITHUB = REPO / ".github"
TEMPLATES = REPO / "devtools" / "templates"

ALLOWED_ACTIONS = {
    "actions/checkout": "v4",
    "actions/setup-python": "v5",
    "actions/upload-artifact": "v4",
    "actions/cache": "v4",
    "actions/configure-pages": "v5",
    "actions/upload-pages-artifact": "v3",
    "actions/deploy-pages": "v4",
}

REPO_WORKFLOWS = sorted((GITHUB / "workflows").glob("*.yml")) if GITHUB.is_dir() else []
TEMPLATE_WORKFLOWS = sorted(TEMPLATES.glob("*/files/dot-github/workflows/*.yml"))

pytestmark = pytest.mark.skipif(not REPO_WORKFLOWS, reason=".github/workflows tidak ada")


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    # PyYAML (YAML 1.1) parses the key `on` as boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def test_expected_workflows_exist():
    names = {p.name for p in REPO_WORKFLOWS}
    assert {"ci.yml", "jadwal-konten.yml", "jadwal-scraper.yml", "backup.yml",
            "kualitas-kode.yml"} <= names
    assert (GITHUB / "dependabot.yml").is_file()


@pytest.mark.parametrize("path", REPO_WORKFLOWS + TEMPLATE_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_structure(path):
    wf = load(path)
    assert wf.get("name"), "workflow butuh name"
    assert wf.get("on"), "workflow butuh trigger 'on'"
    triggers = wf["on"] if isinstance(wf["on"], dict) else {t: None for t in wf["on"]}
    for item in (triggers.get("schedule") or []):
        assert len(item["cron"].split()) == 5, item
    dispatch = triggers.get("workflow_dispatch") or {}
    for name, spec in (dispatch.get("inputs") or {}).items():
        assert spec.get("description"), f"input {name} butuh description"
        if spec.get("type") == "choice":
            assert spec.get("default") in spec["options"]

    jobs = wf.get("jobs")
    assert jobs, "workflow butuh jobs"
    for job_id, job in jobs.items():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", job_id)
        assert "runs-on" in job, job_id
        # The `runner` context is not available in job-level env.
        assert "runner." not in yaml.safe_dump(job.get("env", {})), job_id
        steps = job.get("steps")
        assert steps, job_id
        for step in steps:
            assert ("uses" in step) != ("run" in step), f"{job_id}: step butuh uses ATAU run"
            if "uses" in step:
                action, _, ref = step["uses"].partition("@")
                assert action in ALLOWED_ACTIONS, f"action tak dikenal: {action}"
                assert ref == ALLOWED_ACTIONS[action], f"{action} harus @{ALLOWED_ACTIONS[action]}"


def test_expressions_are_balanced():
    for path in REPO_WORKFLOWS + TEMPLATE_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert text.count("${{") == text.count("}}"), path


def test_ci_matrix_and_commands():
    wf = load(GITHUB / "workflows" / "ci.yml")
    job = wf["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.13"]
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "pip install -r requirements-dev.txt" in runs
    assert "ruff check ." in runs and "pytest" in runs
    assert "secrets." not in (GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python"))
    assert setup["with"]["cache"] == "pip"


def test_scheduled_workflows():
    konten = (GITHUB / "workflows" / "jadwal-konten.yml").read_text(encoding="utf-8")
    assert "secrets.ANTHROPIC_API_KEY" in konten and "python -m konten sosmed" in konten
    scraper = (GITHUB / "workflows" / "jadwal-scraper.yml").read_text(encoding="utf-8")
    assert "python -m scraper monitor" in scraper
    for name in ["jadwal-konten.yml", "jadwal-scraper.yml", "backup.yml", "kualitas-kode.yml"]:
        wf = load(GITHUB / "workflows" / name)
        assert "schedule" in wf["on"] and "workflow_dispatch" in wf["on"], name
    backup = load(GITHUB / "workflows" / "backup.yml")
    upload = next(s for s in backup["jobs"]["backup"]["steps"]
                  if s.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["with"]["retention-days"] > 0


def test_dependabot():
    data = yaml.safe_load((GITHUB / "dependabot.yml").read_text(encoding="utf-8"))
    assert data["version"] == 2
    ecosystems = {u["package-ecosystem"]: u for u in data["updates"]}
    assert set(ecosystems) == {"pip", "github-actions"}
    assert all(u["schedule"]["interval"] == "weekly" for u in data["updates"])
