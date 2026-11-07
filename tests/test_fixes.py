"""Regression tests for the QA audit fixes (Nov 7, 2026). IDs refer to the QA report."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

import ai_agent
import config
import github_tool
import main
from paypal_client import PayPalClient, PayPalError, money
from test_app import FakePayPal, api, fake_paypal  # noqa: F401  (fixtures)

INVOICE = {"client_name": "Kopi Labs", "client_email": "buyer@personal.example.com", "hourly_rate": 40,
           "line_items": [{"name": "Work", "hours": 2}]}


# ---------- H1: the public demo only sends to sandbox test accounts

def test_h1_stranger_email_rejected(api, fake_paypal):
    r = api.post("/api/invoices", json={**INVOICE, "client_email": "stranger@gmail.com"})
    assert r.status_code == 400 and "sandbox test accounts" in r.json()["detail"]
    assert not fake_paypal.invoices


def test_h1_sandbox_email_allowed(api, fake_paypal):
    assert api.post("/api/invoices", json=INVOICE).status_code == 200


def test_h1_allow_list_can_be_disabled(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_RECIPIENT_DOMAINS", [])
    assert api.post("/api/invoices", json={**INVOICE, "client_email": "client@company.com"}).status_code == 200


# ---------- H2: no second invoice after a failed read-back or a retry

def test_h2_readback_failure_still_reports_success(api, fake_paypal, monkeypatch):
    def broken_get(invoice_id):
        raise PayPalError(503, "Service unavailable")
    monkeypatch.setattr(main._paypal, "get_invoice", broken_get)
    r = api.post("/api/invoices", json={**INVOICE, "request_id": "draft-1"})
    assert r.status_code == 200 and r.json()["invoice"]["status"] == "SENT"
    assert r.json()["invoice"]["payer_url"]


def test_h2_same_request_id_sends_once(api, fake_paypal):
    first = api.post("/api/invoices", json={**INVOICE, "request_id": "draft-2"}).json()
    second = api.post("/api/invoices", json={**INVOICE, "request_id": "draft-2"}).json()
    assert first == second and len(fake_paypal.invoices) == 1


# ---------- H3 / L1: fewer agent tool rounds, no duplicate drafts after a fallback

def test_h3_agent_tool_rounds_capped(api, fake_paypal, monkeypatch):
    seen = {}
    def fake_generate(model, contents, config):
        seen["max"] = config.automatic_function_calling.maximum_remote_calls
        return SimpleNamespace(text="ok")
    real = ai_agent.run_agent
    monkeypatch.setattr(main.ai_agent, "run_agent", lambda *a, **k: real(*a, **k, generate=fake_generate))
    api.post("/api/agent", json={"messages": [{"role": "user", "text": "hi"}]})
    assert seen["max"] == 4


def test_l1_fallback_does_not_duplicate_drafts(api, fake_paypal, monkeypatch):
    pp = main._paypal
    inv = pp.create_invoice(client_name="A", client_email="a@x.example.com", currency="USD",
                            items=[{"name": "w", "description": "", "hours": 1, "rate": 40}], note="n", due_in_days=3)
    pp.send_invoice(inv["id"])
    calls = {"n": 0}

    class Models:
        def generate_content(self, model, contents, config):
            calls["n"] += 1
            find, _details, propose = config.tools
            propose(invoice_id=find(status="open")[0]["id"], subject="s", message="m")
            if calls["n"] == 1:
                raise errors.APIError(503, {"error": {"message": "busy"}})
            return SimpleNamespace(text="ok")

    client = ai_agent._client("x")
    monkeypatch.setattr(type(client), "models", property(lambda self: Models()))
    monkeypatch.setattr(ai_agent.time, "sleep", lambda s: None)
    r = api.post("/api/agent", json={"messages": [{"role": "user", "text": "remind"}]})
    assert r.status_code == 200 and len(r.json()["proposals"]) == 1


# ---------- M1: rate limit can't be bypassed with a fake X-Forwarded-For

def test_m1_spoofed_forwarded_for_is_ignored(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "AI_REQUESTS_PER_HOUR", 2)
    main._hits.clear()
    monkeypatch.setattr(main.ai_agent, "analyze_work", lambda *a, **k: {"line_items": [{"name": "x"}]})
    body = {"source": "text", "text": "built the checkout page for the client today", "hourly_rate": 40, "client_name": "A"}
    codes = [api.post("/api/analyze", json=body, headers={"x-forwarded-for": f"9.9.9.{i}, 1.1.1.1"}).status_code
             for i in range(4)]
    assert codes == [200, 200, 429, 429]
    main._hits.clear()


def test_m1_rate_limit_runs_before_github(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "AI_REQUESTS_PER_HOUR", 0)
    main._hits.clear()
    called = []
    monkeypatch.setattr(main, "fetch_commits", lambda *a, **k: called.append(1) or [])
    r = api.post("/api/analyze", json={"source": "github", "repo": "o/r", "hourly_rate": 40, "client_name": "A"})
    assert r.status_code == 429 and not called
    main._hits.clear()


# ---------- M2 / M5: GitHub errors and URL forms

@pytest.mark.parametrize("status,phrase", [(401, "rejected the GITHUB_TOKEN"), (500, "returned an error (500)"),
                                           (429, "rate limit")])
def test_m2_github_status_errors_are_readable(status, phrase):
    transport = httpx.MockTransport(lambda req: httpx.Response(status, json={"message": "x"}))
    with pytest.raises(github_tool.GitHubError, match=phrase.replace("(", r"\(").replace(")", r"\)")):
        github_tool.fetch_commits("o/r", __import__("datetime").date(2026, 11, 1), __import__("datetime").date(2026, 11, 7),
                                  transport=transport)


def test_m2_github_network_error_is_readable(api, fake_paypal, monkeypatch):
    def boom(req):
        raise httpx.ConnectError("dns")
    real = github_tool.fetch_commits
    monkeypatch.setattr(main, "fetch_commits", lambda repo, s, u, t, a: real(repo, s, u, t, a, transport=httpx.MockTransport(boom)))
    r = api.post("/api/analyze", json={"source": "github", "repo": "o/r", "hourly_rate": 40, "client_name": "A"})
    assert r.status_code == 400 and "Couldn't reach GitHub" in r.json()["detail"]


@pytest.mark.parametrize("text", [
    "https://github.com/maksudaakhtertumpa/kopi-labs-website/tree/main",
    "https://github.com/maksudaakhtertumpa/kopi-labs-website/commits/main",
    "github.com/maksudaakhtertumpa/kopi-labs-website/pull/3",
    "git@github.com:maksudaakhtertumpa/kopi-labs-website.git",
    "maksudaakhtertumpa/kopi-labs-website",
])
def test_m5_repo_urls(text):
    assert github_tool.parse_repo(text) == ("maksudaakhtertumpa", "kopi-labs-website")


# ---------- M3: unusable Gemini answers give a friendly error, not a 500

@pytest.mark.parametrize("text", [None, "", '{"summary": "x", "line_items": [{"name": "a"'])
def test_m3_bad_gemini_answer_is_friendly(api, fake_paypal, monkeypatch, text):
    real = ai_agent.analyze_work
    monkeypatch.setattr(main.ai_agent, "analyze_work",
                        lambda *a, **k: real(*a, **k, generate=lambda **kw: SimpleNamespace(parsed=None, text=text)))
    r = api.post("/api/analyze", json={"source": "text", "text": "built the checkout page for the client today",
                                       "hourly_rate": 40, "client_name": "A"})
    assert r.status_code == 502 and "couldn't draft" in r.json()["detail"]


def test_m3_gemini_network_error_is_friendly(monkeypatch):
    class Models:
        def generate_content(self, **kw):
            raise httpx.ConnectError("offline")
    client = ai_agent._client("y")
    monkeypatch.setattr(type(client), "models", property(lambda self: Models()))
    with pytest.raises(ai_agent.AIResponseError, match="Couldn't reach the AI"):
        ai_agent._generate_with_retry("y")(model="m", contents="x")


# ---------- M7: a rejected cached PayPal token is refreshed once

def test_m7_paypal_token_refreshed_on_401():
    tokens = iter(["old", "new"])
    seen = []

    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 32000})
        seen.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer old":
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE", "message": "expired"})
        return httpx.Response(200, json={"items": []})

    pp = PayPalClient("i", "s", "SANDBOX", transport=httpx.MockTransport(handler))
    assert pp.list_invoices() == [] and seen == ["Bearer old", "Bearer new"]


# ---------- M8: zero-decimal currencies

def test_m8_jpy_amounts_have_no_decimals(fake_paypal):
    assert money(1500, "JPY") == "1500" and money(40, "USD") == "40.00"
    inv = main._paypal.create_invoice(client_name="A", client_email="a@x.example.com", currency="JPY",
                                      items=[{"name": "w", "description": "", "hours": 2, "rate": 6000}], note="n", due_in_days=3)
    assert inv["items"][0]["unit_amount"]["value"] == "6000"


# ---------- L6 / L7: no public API docs, invoice IDs validated

def test_l6_api_docs_hidden(api):
    assert api.get("/docs").status_code == 404 and api.get("/openapi.json").status_code == 404


def test_l7_invalid_invoice_id_rejected_before_paypal(api, fake_paypal):
    before = len(fake_paypal.calls)
    assert api.get("/api/invoices/not-an-id").status_code == 400
    assert api.post("/api/invoices/abc/remind", json={"subject": "s", "message": "m"}).status_code == 400
    assert len(fake_paypal.calls) == before
