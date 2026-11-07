"""Tests with fake PayPal and fake Gemini, so they run without any keys.

Run:  pytest -q
"""

from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import ai_agent
import config
import main
from paypal_client import PayPalClient

# ------------------------------------------------------------------ fake PayPal


class FakePayPal:
    """In-memory stand-in for the PayPal Invoicing API v2."""

    def __init__(self):
        self.invoices: dict[str, dict] = {}
        self.calls: list[str] = []
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path == "/v2/invoicing/generate-next-invoice-number":
            self.n += 1
            return httpx.Response(200, json={"invoice_number": f"{self.n:04d}"})
        if path == "/v2/invoicing/invoices" and method == "POST":
            body = httpx.Response(200, content=request.content).json()
            inv_id = f"INV2-TEST-{self.n:04d}"
            total = sum(float(i["quantity"]) * float(i["unit_amount"]["value"]) for i in body["items"])
            body.update(id=inv_id, status="DRAFT", amount={"currency_code": body["detail"]["currency_code"], "value": f"{total:.2f}"})
            body["detail"]["metadata"] = {"recipient_view_url": f"https://www.sandbox.paypal.com/invoice/p/#{inv_id}"}
            self.invoices[inv_id] = body
            return httpx.Response(201, json=body)
        if path == "/v2/invoicing/invoices" and method == "GET":
            return httpx.Response(200, json={"items": list(self.invoices.values())})
        parts = path.split("/")
        if len(parts) >= 5 and parts[3] == "invoices":
            inv = self.invoices.get(parts[4])
            if inv is None:
                return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "Invoice not found"})
            if method == "GET":
                return httpx.Response(200, json=inv)
            if path.endswith("/send"):
                inv["status"] = "SENT"
                return httpx.Response(200, json={"rel": "payer-view", "href": inv["detail"]["metadata"]["recipient_view_url"]})
            if path.endswith("/remind"):
                return httpx.Response(204)
        if path == "/v1/notifications/verify-webhook-signature":
            body = httpx.Response(200, content=request.content).json()
            ok = body["transmission_sig"] == "good-sig" and body["webhook_id"] == "WH-1"
            return httpx.Response(200, json={"verification_status": "SUCCESS" if ok else "FAILURE"})
        return httpx.Response(400, json={"name": "BAD", "message": f"unexpected {method} {path}"})


@pytest.fixture
def fake_paypal(monkeypatch):
    fake = FakePayPal()
    client = PayPalClient("id", "secret", "SANDBOX", transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(main, "_paypal", client)
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake")
    return fake


@pytest.fixture
def api():
    return TestClient(main.app)


# ------------------------------------------------------------------ tests


def test_config_and_index(api):
    assert api.get("/").status_code == 200
    cfg = api.get("/api/config").json()
    assert "defaults" in cfg and "USD" in cfg["currencies"]


def test_analyze_text_drops_invented_evidence(api, fake_paypal, monkeypatch):
    def fake_generate(model, contents, config):
        assert "[L1]" in contents and "[L2]" in contents
        parsed = ai_agent.WorkAnalysis(
            summary="Built the checkout page and fixed a mobile bug.",
            line_items=[
                ai_agent.LineItem(name="Checkout page", description="New checkout.", hours=5.1, evidence=["L1"]),
                ai_agent.LineItem(name="Mobile fix", description="Fixed layout.", hours=1.9, evidence=["L2", "L99"]),
                ai_agent.LineItem(name="Invented work", description="Not real.", hours=3, evidence=["L42"]),
            ],
            flags=[],
        )
        return SimpleNamespace(parsed=parsed, text="")

    real = ai_agent.analyze_work
    monkeypatch.setattr(main.ai_agent, "analyze_work", lambda *a, **k: real(*a, **k, generate=fake_generate))
    r = api.post("/api/analyze", json={
        "source": "text", "text": "Tue - built checkout page (5h)\nWed - fixed mobile layout bug",
        "hourly_rate": 40, "currency": "USD", "client_name": "Acme Ltd",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    names = [i["name"] for i in data["line_items"]]
    assert names == ["Checkout page", "Mobile fix"]           # invented item removed
    assert data["line_items"][0]["hours"] == 5.0              # rounded to 0.25
    assert data["line_items"][1]["evidence"] == ["L2"]        # unknown evidence id dropped
    assert any("Invented work" in f for f in data["flags"])


def test_create_send_list_and_remind(api, fake_paypal):
    r = api.post("/api/invoices", json={
        "client_name": "Acme Ltd", "client_email": "sb-buyer@personal.example.com", "currency": "USD",
        "hourly_rate": 40, "due_in_days": 14, "summary": "Checkout work.",
        "line_items": [{"name": "Checkout page", "description": "x", "hours": 6, "evidence": ["abc1234"]},
                       {"name": "Bug fix", "description": "y", "hours": 2, "evidence": []}],
        "evidence": {"abc1234": {"label": "feat: checkout", "url": "https://github.com/o/r/commit/abc1234"}},
    })
    assert r.status_code == 200, r.text
    inv = r.json()["invoice"]
    assert r.json()["total"] == "320.00" and inv["status"] == "SENT" and inv["payer_url"]
    sent = fake_paypal.invoices[inv["id"]]
    assert "https://github.com/o/r/commit/abc1234" in sent["detail"]["note"]
    assert sent["items"][0]["unit_of_measure"] == "HOURS"

    listed = api.get("/api/invoices").json()
    assert listed["stats"]["open"] == 1

    rem = api.post(f"/api/invoices/{inv['id']}/remind", json={"subject": "Reminder", "message": "Hi!"})
    assert rem.status_code == 200
    assert f"POST /v2/invoicing/invoices/{inv['id']}/remind" in fake_paypal.calls


def test_safety_limit(api, fake_paypal):
    r = api.post("/api/invoices", json={
        "client_name": "Acme", "client_email": "buyer@personal.example.com", "currency": "USD", "hourly_rate": 1000,
        "line_items": [{"name": "Huge", "hours": 100}],
    })
    assert r.status_code == 400 and "safety limit" in r.json()["detail"]


def test_agent_tools_propose_but_do_not_send(api, fake_paypal):
    pp = main._paypal
    inv = pp.create_invoice(client_name="Acme", client_email="a@b.co", currency="USD",
                            items=[{"name": "Work", "description": "", "hours": 2, "rate": 40}],
                            note="Built checkout", due_in_days=0)
    pp.send_invoice(inv["id"])
    from paypal_client import seller_today
    fake_paypal.invoices[inv["id"]]["detail"]["payment_term"]["due_date"] = (seller_today() - timedelta(days=5)).isoformat()

    def fake_generate(model, contents, config):
        find, details, propose = config.tools
        overdue = find(status="overdue")
        assert overdue and overdue[0]["days_overdue"] == 5
        details(invoice_id=overdue[0]["id"])
        propose(invoice_id=overdue[0]["id"], subject="Friendly reminder", message="Hi Acme, a quick reminder…")
        return SimpleNamespace(text="I drafted 1 reminder for you to review.")

    real = ai_agent.run_agent
    import main as m
    orig = m.ai_agent.run_agent
    m.ai_agent.run_agent = lambda *a, **k: real(*a, **k, generate=fake_generate)
    try:
        r = api.post("/api/agent", json={"messages": [{"role": "user", "text": "Who hasn't paid me?"}]})
    finally:
        m.ai_agent.run_agent = orig
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["proposals"]) == 1 and body["proposals"][0]["subject"] == "Friendly reminder"
    assert not any(c.endswith("/remind") for c in fake_paypal.calls)   # nothing sent without approval


def test_paypal_error_is_readable(api, fake_paypal):
    r = api.get("/api/invoices/INV2-NOPE")
    assert r.status_code == 400 and "Invoice not found" in r.json()["detail"]


def test_parse_repo():
    from github_tool import parse_repo
    assert parse_repo("https://github.com/paypal/agent-toolkit.git") == ("paypal", "agent-toolkit")
    assert parse_repo("octocat/hello-world") == ("octocat", "hello-world")


WEBHOOK_HEADERS = {
    "paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
    "paypal-transmission-id": "t-1", "paypal-transmission-time": "2026-11-07T08:00:00Z",
}
PAID_EVENT = {
    "id": "WH-EVT-1", "event_type": "INVOICING.INVOICE.PAID",
    "resource": {"invoice": {"id": "INV2-X", "detail": {"invoice_number": "0007"},
                             "amount": {"currency_code": "USD", "value": "770.00"},
                             "primary_recipients": [{"billing_info": {"business_name": "Kopi Labs"}}]}},
}


def test_webhook_verified_and_deduplicated(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_WEBHOOK_ID", "WH-1")
    main.WEBHOOK_EVENTS.clear(); main._seen_event_ids.clear()
    r = api.post("/api/paypal/webhook", json=PAID_EVENT, headers={**WEBHOOK_HEADERS, "paypal-transmission-sig": "good-sig"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    again = api.post("/api/paypal/webhook", json=PAID_EVENT, headers={**WEBHOOK_HEADERS, "paypal-transmission-sig": "good-sig"})
    assert again.json()["duplicate"] is True
    events = api.get("/api/events?since=0").json()["events"]
    assert len(events) == 1 and events[0]["client_name"] == "Kopi Labs" and events[0]["text"] == "paid"


def test_webhook_rejects_bad_signature(api, fake_paypal, monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_WEBHOOK_ID", "WH-1")
    main.WEBHOOK_EVENTS.clear(); main._seen_event_ids.clear()
    r = api.post("/api/paypal/webhook", json=PAID_EVENT, headers={**WEBHOOK_HEADERS, "paypal-transmission-sig": "forged"})
    assert r.status_code == 400 and not main.WEBHOOK_EVENTS


def test_milestones_tagging_and_scope(api, fake_paypal, monkeypatch):
    def fake_generate(model, contents, config):
        assert "AGREED MILESTONES" in contents and "[M1] Phase 1" in contents
        return SimpleNamespace(parsed=ai_agent.WorkAnalysis(summary="Work.", flags=[], line_items=[
            ai_agent.LineItem(name="Landing page", description="", hours=6, evidence=["L1"], milestone="M1"),
            ai_agent.LineItem(name="Booking form", description="", hours=5, evidence=["L2"], milestone="m2"),
            ai_agent.LineItem(name="SEO extras", description="", hours=2, evidence=["L3"], milestone="OUT"),
            ai_agent.LineItem(name="Odd tag", description="", hours=1, evidence=["L3"], milestone="M9"),
        ]), text="")

    real = ai_agent.analyze_work
    monkeypatch.setattr(main.ai_agent, "analyze_work", lambda *a, **k: real(*a, **k, generate=fake_generate))
    r = api.post("/api/analyze", json={
        "source": "text", "text": "built landing page\nbuilt booking form\nadded SEO robots.txt",
        "hourly_rate": 40, "currency": "USD", "client_name": "Kopi Labs",
        "milestones": "Phase 1: Landing page - USD 400\nPhase 2: Booking - $350",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert [i["milestone"] for i in d["line_items"]] == ["M1", "M2", "OUT", "OUT"]   # unknown id -> OUT
    assert d["scope"]["milestones"][0]["value"] == 240 and d["scope"]["milestones"][0]["budget"] == 400
    assert d["scope"]["out_of_scope"]["hours"] == 3

    inv = api.post("/api/invoices", json={
        "client_name": "Kopi Labs", "client_email": "buyer@personal.example.com", "currency": "USD", "hourly_rate": 40,
        "line_items": [{"name": "Landing page", "hours": 6, "evidence": ["L1"], "milestone": "M1"},
                       {"name": "SEO extras", "hours": 2, "evidence": ["L3"], "milestone": "OUT"}],
        "evidence": {"L1": {"label": "built landing page", "url": ""}, "L3": {"label": "added SEO", "url": ""}},
        "milestones": d["milestones"],
    }).json()["invoice"]
    note = fake_paypal.invoices[inv["id"]]["detail"]["note"]
    assert "[Phase 1: Landing page] Landing page" in note and "[Additional work, outside agreed milestones] SEO extras" in note
