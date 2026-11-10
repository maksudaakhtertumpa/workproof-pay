"""'Try the demo' keeps working when Gemini or GitHub can't answer (e.g. while judges test it)."""

import json

import pytest
from google.genai import errors

import config
import main
from test_app import FakePayPal, api, fake_paypal  # noqa: F401  (fixtures)

DRAFT = json.loads(main.DEMO_DRAFT_FILE.read_text())
DEMO = {"source": "github", "repo": config.DEMO_REPO, "since": config.DEMO_SINCE, "until": config.DEMO_UNTIL,
        "hourly_rate": 40, "client_name": "Kopi Labs", "milestones": config.DEMO_MILESTONES}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    main._hits.clear()
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "fetch_commits", lambda *a, **k: DRAFT["commits"])
    yield
    main._hits.clear()


def quota_used_up(*a, **k):
    raise errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})


def test_config_exposes_demo(api):
    assert api.get("/api/config").json()["demo"]["repo"] == config.DEMO_REPO


def test_demo_uses_saved_draft_when_gemini_quota_is_used_up(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(main.ai_agent, "analyze_work", quota_used_up)
    r = api.post("/api/analyze", json=DEMO)
    assert r.status_code == 200
    body = r.json()
    assert "saved AI draft" in body["sample_note"]
    assert len(body["line_items"]) == len(DRAFT["line_items"])
    assert all(e in body["evidence"] for i in body["line_items"] for e in i["evidence"])
    assert {m["id"] for m in body["milestones"]} == {"M1", "M2"}


def test_demo_uses_saved_commits_when_github_fails(api, fake_paypal, monkeypatch):
    def github_down(*a, **k):
        raise main.GitHubError("rate limit")
    monkeypatch.setattr(main, "fetch_commits", github_down)
    monkeypatch.setattr(main.ai_agent, "analyze_work", quota_used_up)
    r = api.post("/api/analyze", json=DEMO)
    assert r.status_code == 200 and r.json()["stats"]["evidence_count"] == len(DRAFT["commits"])


def test_demo_prefers_live_ai_when_it_works(api, fake_paypal, monkeypatch):
    live = {"summary": "live", "line_items": [{"name": "x", "hours": 1, "evidence": []}]}
    monkeypatch.setattr(main.ai_agent, "analyze_work", lambda *a, **k: live)
    assert api.post("/api/analyze", json=DEMO).json()["summary"] == "live"


def test_demo_without_milestones_drops_milestone_tags(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(main.ai_agent, "analyze_work", quota_used_up)
    body = api.post("/api/analyze", json={**DEMO, "milestones": ""}).json()
    assert body["milestones"] == [] and all(i["milestone"] == "" for i in body["line_items"])


@pytest.mark.parametrize("change", [{"since": "2026-11-01"}, {"repo": "someone/else"}, {"author": "bob"},
                                    {"milestones": "Something else - USD 10"}])
def test_other_requests_never_get_the_saved_draft(api, fake_paypal, monkeypatch, change):
    monkeypatch.setattr(main.ai_agent, "analyze_work", quota_used_up)
    r = api.post("/api/analyze", json={**DEMO, **change})
    assert r.status_code == 502 and "quota" in r.json()["detail"]
