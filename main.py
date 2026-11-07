"""WorkProof Pay: turn proof of work into PayPal invoices with an AI agent.

Run locally:  uvicorn main:app --reload
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field, field_validator

import ai_agent
import config
from github_tool import GitHubError, fetch_commits
from paypal_client import PayPalClient, PayPalError, money, summarize_invoice

app = FastAPI(title="WorkProof Pay")
log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_paypal: PayPalClient | None = None


def paypal() -> PayPalClient:
    global _paypal
    if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
        raise HTTPException(503, "PayPal is not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to .env")
    if _paypal is None:
        _paypal = PayPalClient(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET, config.PAYPAL_ENVIRONMENT)
    return _paypal


def require_gemini() -> None:
    if not config.GEMINI_API_KEY:
        raise HTTPException(503, "Gemini is not configured. Add GEMINI_API_KEY to .env")


# ---- simple per-IP limit so a public demo URL can't burn the AI quota
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request) -> None:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= config.AI_REQUESTS_PER_HOUR:
        raise HTTPException(429, "Too many AI requests from this address. Please try again later.")
    q.append(now)


@app.exception_handler(PayPalError)
async def paypal_error_handler(_: Request, exc: PayPalError):
    from fastapi.responses import JSONResponse

    detail = f"PayPal: {exc.message}" + (f" (debug id {exc.debug_id})" if exc.debug_id else "")
    return JSONResponse(status_code=502 if exc.status_code >= 500 else 400, content={"detail": detail})


@app.exception_handler(ai_agent.GeminiTimeout)
async def timeout_handler(_: Request, exc: ai_agent.GeminiTimeout):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=504, content={"detail": "The AI took too long to answer (Gemini is busy). Please try again."})


@app.exception_handler(genai_errors.APIError)
async def gemini_error_handler(_: Request, exc: genai_errors.APIError):
    from fastapi.responses import JSONResponse

    if exc.code == 429:
        msg = "Gemini free-tier quota is used up for all configured models. Try again later or add more models to GEMINI_FALLBACK_MODELS."
    elif exc.code in (503, 504):
        msg = "All Gemini models are busy right now. Please try again in a minute."
    elif exc.code in (401, 403):
        msg = "Gemini rejected the API key. Check GEMINI_API_KEY in .env."
    else:
        msg = f"Gemini error {exc.code}: {exc.message}"
    return JSONResponse(status_code=502, content={"detail": msg})


# ------------------------------------------------------------------ pages


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "paypal_ready": bool(config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET),
        "gemini_ready": bool(config.GEMINI_API_KEY),
        "github_token": bool(config.GITHUB_TOKEN),
        "environment": config.PAYPAL_ENVIRONMENT,
        "model": config.GEMINI_MODEL,
        "defaults": {
            "client_name": config.DEFAULT_CLIENT_NAME,
            "client_email": config.DEFAULT_CLIENT_EMAIL,
            "currency": config.DEFAULT_CURRENCY,
            "hourly_rate": config.DEFAULT_HOURLY_RATE,
            "since": (date.today() - timedelta(days=7)).isoformat(),
            "until": date.today().isoformat(),
        },
        "currencies": sorted(config.SUPPORTED_CURRENCIES),
        "max_invoice_total": config.MAX_INVOICE_TOTAL,
        "webhooks_ready": bool(config.PAYPAL_WEBHOOK_ID),
    }


# ------------------------------------------------------------------ analyze


class AnalyzeRequest(BaseModel):
    source: Literal["github", "text"]
    repo: str = ""
    since: date | None = None
    until: date | None = None
    author: str = ""
    text: str = Field("", max_length=30000)
    hourly_rate: float = Field(gt=0, le=1000)
    currency: str = "USD"
    client_name: str = Field(min_length=1, max_length=200)
    context: str = Field("", max_length=1000)


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest, request: Request):
    require_gemini()
    if req.source == "github":
        if not req.repo:
            raise HTTPException(400, "Enter a GitHub repo, e.g. owner/repo")
        until = req.until or date.today()
        since = req.since or (until - timedelta(days=7))
        if since > until:
            raise HTTPException(400, "The start date is after the end date.")
        try:
            commits = fetch_commits(req.repo, since, until, config.GITHUB_TOKEN, req.author)
        except GitHubError as e:
            raise HTTPException(400, str(e)) from e
        if not commits:
            raise HTTPException(400, f"No commits found in {req.repo} between {since} and {until}.")
        evidence_text, evidence = ai_agent.build_evidence_from_commits(commits)
    else:
        if len(req.text.strip()) < 20:
            raise HTTPException(400, "Paste a chat thread or time log (at least a few lines).")
        evidence_text, evidence = ai_agent.build_evidence_from_text(req.text)

    rate_limit(request)
    log.info("Analyzing %d evidence entries for %s", len(evidence), req.client_name)
    result = ai_agent.analyze_work(
        config.GEMINI_API_KEY,
        config.GEMINI_MODEL,
        evidence_text,
        evidence,
        req.hourly_rate,
        req.currency,
        req.client_name,
        req.context,
    )
    if not result["line_items"]:
        raise HTTPException(422, "The AI couldn't find billable work in that evidence. Try a wider date range.")
    return result


# ------------------------------------------------------------------ invoices


class InvoiceItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    hours: float = Field(gt=0, le=200)
    evidence: list[str] = []


class EvidenceRef(BaseModel):
    label: str = ""
    url: str = ""


class CreateInvoiceRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=200)
    client_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    currency: str = "USD"
    hourly_rate: float = Field(gt=0, le=1000)
    due_in_days: int = Field(14, ge=0, le=90)
    summary: str = Field("", max_length=2000)
    line_items: list[InvoiceItem] = Field(min_length=1, max_length=25)
    evidence: dict[str, EvidenceRef] = {}

    @field_validator("currency")
    @classmethod
    def check_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in config.SUPPORTED_CURRENCIES:
            raise ValueError(f"Currency must be one of {sorted(config.SUPPORTED_CURRENCIES)}")
        return v


def build_note(req: CreateInvoiceRequest) -> str:
    """Invoice note = AI work summary + links to the proof of work."""
    lines = [req.summary.strip(), "", "Proof of work:"]
    for item in req.line_items:
        refs = []
        for ev in item.evidence[:6]:
            ref = req.evidence.get(ev)
            if ref and ref.url.startswith("https://github.com/"):
                refs.append(ref.url)
            elif ref:
                refs.append(f"“{ref.label[:80]}”")
        if refs:
            lines.append(f"• {item.name}: " + ", ".join(refs))
    lines.append("")
    lines.append("Generated with WorkProof Pay.")
    note = "\n".join(lines).strip()
    return note[:3990]


@app.post("/api/invoices")
def create_and_send_invoice(req: CreateInvoiceRequest):
    pp = paypal()
    total = sum(Decimal(str(i.hours)) * Decimal(str(req.hourly_rate)) for i in req.line_items)
    if total > Decimal(str(config.MAX_INVOICE_TOTAL)):
        raise HTTPException(
            400, f"Total {req.currency} {money(total)} is above the safety limit of {config.MAX_INVOICE_TOTAL}."
        )
    items = [
        {"name": i.name, "description": i.description, "hours": i.hours, "rate": req.hourly_rate}
        for i in req.line_items
    ]
    invoice = pp.create_invoice(
        client_name=req.client_name,
        client_email=req.client_email,
        currency=req.currency,
        items=items,
        note=build_note(req),
        due_in_days=req.due_in_days,
        memo="Created by WorkProof Pay from verified proof of work.",
        seller_name=config.SELLER_NAME,
    )
    send_result = pp.send_invoice(invoice["id"])
    sent = summarize_invoice(pp.get_invoice(invoice["id"]))
    if not sent["payer_url"] and send_result.get("href"):
        sent["payer_url"] = send_result["href"]
    return {"invoice": sent, "total": money(total)}


@app.get("/api/invoices")
def list_invoices():
    rows = [summarize_invoice(i) for i in paypal().list_invoices()]
    open_rows = [r for r in rows if r["is_open"]]
    stats = {
        "count": len(rows),
        "open": len(open_rows),
        "overdue": sum(1 for r in open_rows if r["days_overdue"] > 0),
        "paid": sum(1 for r in rows if r["is_paid"]),
    }
    return {"invoices": rows, "stats": stats}


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    return summarize_invoice(paypal().get_invoice(invoice_id))


class ReminderRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)


@app.post("/api/invoices/{invoice_id}/remind")
def send_reminder(invoice_id: str, req: ReminderRequest):
    pp = paypal()
    inv = summarize_invoice(pp.get_invoice(invoice_id))
    if not inv["is_open"]:
        raise HTTPException(400, f"Invoice {inv['number']} is {inv['status']}; no reminder needed.")
    pp.send_reminder(invoice_id, req.subject, req.message)
    return {"ok": True, "invoice_number": inv["number"]}


# ------------------------------------------------------------------ PayPal webhooks
# PayPal calls this URL the moment an invoice is paid, so the dashboard updates live.

WEBHOOK_EVENTS: deque = deque(maxlen=50)
_seen_event_ids: set[str] = set()
INVOICE_EVENT_TEXT = {
    "INVOICING.INVOICE.PAID": "paid",
    "INVOICING.INVOICE.REFUNDED": "was refunded",
    "INVOICING.INVOICE.CANCELLED": "was cancelled",
    "INVOICING.INVOICE.UPDATED": "was updated",
    "INVOICING.INVOICE.CREATED": "was created",
}


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not config.PAYPAL_WEBHOOK_ID:
        raise HTTPException(503, "PAYPAL_WEBHOOK_ID is not configured")
    if not paypal().verify_webhook(dict(request.headers), event, config.PAYPAL_WEBHOOK_ID):
        log.warning("Rejected a webhook with an invalid PayPal signature")
        raise HTTPException(400, "Signature verification failed")

    event_id = event.get("id", "")
    if event_id in _seen_event_ids:  # PayPal may deliver the same event more than once
        return {"ok": True, "duplicate": True}
    _seen_event_ids.add(event_id)

    event_type = event.get("event_type", "")
    resource = event.get("resource") or {}
    invoice = resource.get("invoice") or resource
    detail = invoice.get("detail") or {}
    recipients = invoice.get("primary_recipients") or [{}]
    billing = (recipients[0] or {}).get("billing_info", {}) if recipients else {}
    item = {
        "id": event_id,
        "type": event_type,
        "invoice_id": invoice.get("id"),
        "invoice_number": detail.get("invoice_number"),
        "client_name": billing.get("business_name") or billing.get("email_address") or "Your client",
        "amount": (invoice.get("amount") or {}).get("value"),
        "currency": (invoice.get("amount") or {}).get("currency_code"),
        "text": INVOICE_EVENT_TEXT.get(event_type, event_type.lower()),
        "received_at": time.time(),
    }
    WEBHOOK_EVENTS.append(item)
    log.info("PayPal webhook: %s for invoice %s", event_type, item["invoice_number"])
    return {"ok": True}


@app.get("/api/events")
def recent_events(since: float = 0):
    return {"events": [e for e in WEBHOOK_EVENTS if e["received_at"] > since], "now": time.time()}


# ------------------------------------------------------------------ agent


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class AgentRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)


@app.post("/api/agent")
def agent(req: AgentRequest, request: Request):
    require_gemini()
    pp = paypal()
    rate_limit(request)
    return ai_agent.run_agent(
        config.GEMINI_API_KEY,
        config.GEMINI_MODEL,
        [m.model_dump() for m in req.messages],
        list_invoices=lambda: [summarize_invoice(i) for i in pp.list_invoices()],
        get_invoice=lambda invoice_id: summarize_invoice(pp.get_invoice(invoice_id)),
    )
