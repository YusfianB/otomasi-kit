"""sosmed: planning, template engine quality checks, outputs, resume, and the AI path."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from konten import sosmed
from konten.cli import main
from konten.common import KontenError
from konten.llm import LLMClient
from konten.tests.fake_anthropic import FakeClient, prompt_text, sosmed_responder

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "brand.yaml"


def brief(**over):
    profile = sosmed.load_profile(EXAMPLE)
    return sosmed.build_brief(profile, over)


def test_schedule_frequencies():
    days = sosmed.schedule("2026-11-09", 6, "3x-seminggu")  # 9 Nov 2026 is a Monday
    assert [d.weekday() for d in days] == [0, 2, 4, 0, 2, 4]
    assert sosmed.schedule("2026-11-09", 3, "mingguan")[-1] == dt.date(2026, 11, 23)
    assert sosmed.schedule("2026-11-09", 3, "2")[1] == dt.date(2026, 11, 11)
    with pytest.raises(KontenError, match="Frekuensi"):
        sosmed.schedule("2026-11-09", 3, "kadang-kadang")


def test_brief_validation():
    with pytest.raises(KontenError, match="brand"):
        sosmed.build_brief({}, {"niche": "kopi"})
    with pytest.raises(KontenError, match="Platform tidak dikenal"):
        sosmed.build_brief({"brand": "A", "niche": "b"}, {"platforms": "myspace"})
    with pytest.raises(KontenError, match="Tone"):
        sosmed.build_brief({"brand": "A", "niche": "b", "tone": "galak"}, {})
    b = sosmed.build_brief({"brand": "A", "niche": "b"}, {"platforms": "ig, twitter"})
    assert b.platforms == ["instagram", "x"]


def test_template_posts_are_unique_and_respect_limits():
    b = brief(jumlah=10, platforms="instagram,tiktok,linkedin,x,facebook")
    posts = [sosmed.generate_template(b, s) for s in sosmed.plan(b)]
    assert len(posts) == 50
    assert len({p["caption"] for p in posts}) == 50, "captions must not repeat"
    for p in posts:
        key = next(k for k, v in sosmed.PLATFORMS.items() if v["nama"] == p["platform"])
        cfg = sosmed.PLATFORMS[key]
        tags = p["hashtag"].split()
        assert p["jumlah_karakter"] <= cfg["max_caption"]
        assert cfg["hashtag"][0] <= len(tags) <= cfg["hashtag"][1], (p["platform"], tags)
        assert all(t.startswith("#") and " " not in t for t in tags)
        assert "{" not in p["caption"] and "}" not in p["caption"], p["caption"]
        if "video" in p["format"] or "reels" in p["format"]:
            assert "dtk" in p["naskah_video"]
    per_platform = {}
    for p in posts:
        per_platform.setdefault(p["platform"], set()).add(p["hook"])
    assert all(len(h) >= 6 for h in per_platform.values()), "hooks should vary"


def test_linkedin_uses_formal_register():
    b = brief(platforms="linkedin", jumlah=7, tone="santai")
    posts = [sosmed.generate_template(b, s) for s in sosmed.plan(b)]
    text = " ".join(p["caption"] for p in posts).lower()
    assert " kamu" not in text and " yuk" not in text
    assert "Hiburan" not in {p["pilar"] for p in posts}
    user_tags = {h.lower() for h in b.hashtag}
    generated = [t for p in posts for t in p["hashtag"].split() if t.lower() not in user_tags]
    assert generated and all(t[1].isupper() for t in generated)  # CamelCase for LinkedIn


def test_client_topics_use_marked_placeholders_not_invented_facts():
    b = brief(platforms="instagram", jumlah=7)
    posts = [sosmed.generate_template(b, s) for s in sosmed.plan(b)]
    topical = [p for p in posts if p["topik"] in b.topik]
    assert topical and all("[" in p["caption"] for p in topical)


def test_english_output():
    b = brief(bahasa="en", platforms="instagram", jumlah=3)
    posts = [sosmed.generate_template(b, s) for s in sosmed.plan(b)]
    assert posts[0]["hari"] in sosmed.HARI["en"]


def test_run_writes_outputs_and_resumes(tmp_path):
    b = brief(jumlah=3)
    files = sosmed.run(b, tmp_path, llm=None, concurrency=1, timpa=False)
    names = {f.name for f in files}
    assert names == {"kalender_konten.xlsx", "kalender_konten.csv", "kalender_konten.md"}
    sheets = pd.read_excel(tmp_path / "kalender_konten.xlsx", sheet_name=None)
    assert set(sheets) == {"Kalender", "Instagram", "TikTok", "X (Twitter)"}
    assert len(sheets["Kalender"]) == 9
    md = (tmp_path / "kalender_konten.md").read_text(encoding="utf-8")
    assert "mesin **template**" in md and "Senin, 9 November 2026" in md
    cached = list((tmp_path / "_items").rglob("*.json"))
    assert len(cached) == 9
    mtimes = {p: p.stat().st_mtime_ns for p in cached}
    sosmed.run(b, tmp_path, llm=None, concurrency=1, timpa=False)
    assert all(p.stat().st_mtime_ns == m for p, m in mtimes.items())  # skipped, not rewritten


def test_ai_engine_with_mocked_client(tmp_path):
    fake = FakeClient(sosmed_responder)
    llm = LLMClient(client=fake, backoff_base=0)
    b = brief(jumlah=2, platforms="instagram,tiktok")
    sosmed.run(b, tmp_path, llm=llm, concurrency=2, timpa=False)
    df = pd.read_csv(tmp_path / "kalender_konten.csv")
    assert len(df) == 4 and df["engine"].str.startswith("ai:claude-opus-5-5").all()
    assert len(fake.calls) == 4
    tiktok = [c for c in fake.calls if "platform: TikTok" in prompt_text(c)]
    assert all("Video format" in prompt_text(c) for c in tiktok)
    assert all("Kopi Senja" in prompt_text(c) for c in fake.calls)


def test_ai_caption_over_limit_is_retried_then_truncated(tmp_path):
    def responder(params, n):
        msg = sosmed_responder(params, n)
        if "platform: X" in prompt_text(params):
            import json

            data = json.loads(msg.content[1].text)
            data["caption"] = "kata " * 200
            msg.content[1].text = json.dumps(data)
        return msg

    fake = FakeClient(responder)
    llm = LLMClient(client=fake, backoff_base=0)
    b = brief(jumlah=1, platforms="x")
    post = sosmed.generate_ai(llm, b, sosmed.plan(b)[0])
    assert len(fake.calls) == 3  # retried with feedback
    assert post["jumlah_karakter"] <= 280


def test_cli_sosmed_auto_engine_without_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    code = main(["sosmed", "--profil", str(EXAMPLE), "--jumlah", "2", "-o", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "TEMPLATE" in out
    assert (tmp_path / "kalender_konten.xlsx").exists()
