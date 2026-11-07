"""The AI parts of WorkProof Pay, powered by Google Gemini.

1. analyze_work(): turns raw proof of work (commits or pasted text) into billable line items.
   Every line item must cite evidence; items with no real evidence are flagged, never invented.
2. run_agent(): a billing assistant that reads PayPal invoice data through tools
   (function calling) and *proposes* payment reminders. Nothing is sent until the
   freelancer clicks "Send" in the UI.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn.error")
FAST_THINKING = types.ThinkingConfig(thinking_level="LOW")

# ---------------------------------------------------------------- analysis


class LineItem(BaseModel):
    name: str = Field(description="Short client-friendly title, e.g. 'Checkout page'. Max 80 characters.")
    description: str = Field(description="One or two sentences on what was delivered, in plain English.")
    hours: float = Field(description="Estimated billable hours for this item, in steps of 0.25.")
    evidence: list[str] = Field(description="IDs of the evidence entries this item is based on, e.g. ['a1b2c3d'] or ['L4', 'L5'].")


class WorkAnalysis(BaseModel):
    summary: str = Field(description="A 2-4 sentence summary of the work for the client, friendly and specific.")
    line_items: list[LineItem]
    flags: list[str] = Field(description="Anything the freelancer should double-check before billing. Can be empty.")


ANALYSIS_INSTRUCTIONS = """You are WorkProof Pay, a billing assistant for freelancers.
You receive a list of evidence entries (git commits or lines of a chat/time log), each with an ID.
Turn them into an itemized invoice:
- Group related entries into 2-8 billable line items a client understands (features, fixes, meetings), not one item per commit.
- Estimate realistic hours for each item. Use explicit hours if the evidence states them; otherwise estimate from the
  scope of the work (commit messages, files changed, lines changed). Round to 0.25 hours.
- Every line item MUST list the IDs of the evidence it is based on. Only use IDs that appear in the evidence.
- Never invent work that is not in the evidence. Skip trivial entries (typo fixes, merges) or fold them into a related item.
- Write for the client: no internal jargon, no commit hashes in names or descriptions.
- Add a flag for anything uncertain (e.g. very large estimates, unclear entries, work that may be out of scope).
"""


REQUEST_TIMEOUT_MS = 60_000  # give up on a single Gemini call after 60 seconds
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]


class GeminiTimeout(Exception):
    """Gemini did not answer in time."""


@lru_cache(maxsize=4)
def _client(api_key: str) -> genai.Client:
    # Cached so the client stays alive: a Client that is garbage-collected closes its HTTP connection.
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))


def _generate_with_retry(api_key: str) -> Callable[..., Any]:
    """generate_content that survives a busy Gemini.

    Tries the configured model, then each fallback model in GEMINI_FALLBACK_MODELS.
    Busy (503/504), out of quota (429) or unavailable (404) models are skipped at once,
    because free-tier quotas are per model. Timeouts move on to the next model too.
    """
    models = _client(api_key).models

    def generate(**kwargs: Any) -> Any:
        primary = kwargs.pop("model")
        candidates = [primary] + [m for m in FALLBACK_MODELS if m != primary]
        last_error: Exception | None = None
        for model in candidates:
            for attempt in range(2):
                started = time.time()
                log.info("Calling Gemini (%s), attempt %d…", model, attempt + 1)
                try:
                    response = models.generate_content(model=model, **kwargs)
                    log.info("Gemini (%s) answered in %.1fs", model, time.time() - started)
                    return response
                except Exception as e:  # noqa: BLE001
                    if "Timeout" in type(e).__name__:
                        log.warning("Gemini (%s) timed out after %.1fs", model, time.time() - started)
                        last_error = GeminiTimeout()
                        break  # try the next model
                    if not isinstance(e, genai_errors.APIError):
                        raise
                    config = kwargs.get("config")
                    if e.code == 400 and config is not None and config.thinking_config is not None:
                        log.warning("%s rejected thinking_config, retrying without it", model)
                        kwargs["config"] = config.model_copy(update={"thinking_config": None})
                        continue
                    log.warning("Gemini (%s) error %s after %.1fs: %s", model, e.code, time.time() - started, e.message)
                    last_error = e
                    if e.code in (404, 429, 503, 504):
                        # Unavailable, quota used up, or busy: move straight to the next model
                        # (free-tier quotas are counted per model, so retrying here only wastes quota).
                        break
                    if e.code != 500:
                        raise
                    if attempt == 0:
                        time.sleep(2)
        if isinstance(last_error, GeminiTimeout):
            raise last_error
        raise last_error or RuntimeError("No Gemini model available")

    return generate


def build_evidence_from_commits(commits: list[dict[str, Any]]) -> tuple[str, dict[str, dict[str, str]]]:
    lines, index = [], {}
    for c in commits:
        stats = ""
        if c.get("additions") is not None:
            stats = f" | +{c['additions']}/-{c['deletions']} lines"
            if c.get("files"):
                stats += f" | files: {', '.join(c['files'])}"
        lines.append(f"[{c['id']}] {c['date'][:10]} {c['message'].splitlines()[0]}{stats}")
        index[c["id"]] = {"label": c["message"].splitlines()[0][:120], "url": c["url"]}
    return "\n".join(lines), index


def build_evidence_from_text(text: str) -> tuple[str, dict[str, dict[str, str]]]:
    lines, index = [], {}
    n = 0
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        n += 1
        ref = f"L{n}"
        lines.append(f"[{ref}] {raw[:400]}")
        index[ref] = {"label": raw[:120], "url": ""}
        if n >= 300:
            break
    return "\n".join(lines), index


def _round_hours(h: float) -> float:
    h = max(0.25, min(float(h), 40.0))
    return float(Decimal(str(round(h * 4) / 4)))


def analyze_work(
    api_key: str,
    model: str,
    evidence_text: str,
    evidence_index: dict[str, dict[str, str]],
    hourly_rate: float,
    currency: str,
    client_name: str,
    extra_context: str = "",
    generate: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Ask Gemini for line items, then validate them against the real evidence."""
    prompt = (
        f"Client: {client_name}\nHourly rate: {hourly_rate} {currency}\n"
        f"Extra context from the freelancer: {extra_context or 'none'}\n\n"
        f"EVIDENCE ({len(evidence_index)} entries):\n{evidence_text}"
    )
    config = types.GenerateContentConfig(
        system_instruction=ANALYSIS_INSTRUCTIONS,
        response_mime_type="application/json",
        response_schema=WorkAnalysis,
        temperature=0.2,
        thinking_config=FAST_THINKING,
    )
    if generate is None:
        generate = _generate_with_retry(api_key)
    response = generate(model=model, contents=prompt, config=config)
    analysis: WorkAnalysis | None = response.parsed
    if analysis is None:
        analysis = WorkAnalysis.model_validate_json(response.text)

    # Guardrail: keep only evidence IDs that really exist; flag items with none.
    items, flags = [], list(analysis.flags)
    for item in analysis.line_items:
        valid = [e for e in item.evidence if e in evidence_index]
        if not valid:
            flags.append(f"“{item.name}” had no matching evidence, so it was removed.")
            continue
        items.append(
            {
                "name": item.name.strip()[:200],
                "description": item.description.strip()[:900],
                "hours": _round_hours(item.hours),
                "evidence": valid,
            }
        )
    used = {e for i in items for e in i["evidence"]}
    unused = len(evidence_index) - len(used)
    return {
        "summary": analysis.summary.strip(),
        "line_items": items,
        "flags": flags,
        "evidence": evidence_index,
        "stats": {"evidence_count": len(evidence_index), "evidence_used": len(used), "evidence_unused": unused},
    }


# ---------------------------------------------------------------- agent

AGENT_INSTRUCTIONS = """You are WorkProof Pay, an AI billing assistant for a freelancer. Today is {today}.
You can look up the freelancer's PayPal invoices with your tools. Rules:
- Always use the tools for facts about invoices; never guess amounts, dates or statuses.
- To chase an unpaid invoice, call propose_reminder with a short, polite, specific message that names the
  delivered work (from the invoice note) and the amount and due date. Firmer tone only if more than 14 days overdue.
- propose_reminder does NOT send anything. Tell the user the drafts are ready for review and that they can
  edit and click "Send via PayPal".
- Keep answers short. Use plain text and simple lists. Money as e.g. "USD 480.00".
"""


def run_agent(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    list_invoices: Callable[[], list[dict[str, Any]]],
    get_invoice: Callable[[str], dict[str, Any]],
    generate: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run one agent turn. Returns the reply text, proposed reminders and the tools it used."""
    proposals: list[dict[str, str]] = []
    tool_log: list[str] = []
    cache: dict[str, dict[str, Any]] = {}

    def _all() -> list[dict[str, Any]]:
        if "all" not in cache:
            cache["all"] = {"items": list_invoices()}
        return cache["all"]["items"]

    def find_invoices(status: str = "open") -> list[dict[str, Any]]:
        """List the freelancer's PayPal invoices.

        Args:
            status: Which invoices to return: "open" (sent but not fully paid), "paid", "overdue", or "all".
        """
        tool_log.append(f"find_invoices(status={status})")
        rows = _all()
        if status == "open":
            rows = [r for r in rows if r["is_open"]]
        elif status == "paid":
            rows = [r for r in rows if r["is_paid"]]
        elif status == "overdue":
            rows = [r for r in rows if r["is_open"] and r["days_overdue"] > 0]
        return [
            {k: r[k] for k in ("id", "number", "status", "client_name", "amount", "currency",
                               "invoice_date", "due_date", "days_overdue")}
            for r in rows
        ]

    def invoice_details(invoice_id: str) -> dict[str, Any]:
        """Get one invoice including the note describing the delivered work.

        Args:
            invoice_id: The PayPal invoice ID, e.g. "INV2-ABCD-1234-EFGH-5678".
        """
        tool_log.append(f"invoice_details({invoice_id})")
        return get_invoice(invoice_id)

    def propose_reminder(invoice_id: str, subject: str, message: str) -> str:
        """Draft a payment reminder for an open invoice. It is NOT sent until the user approves it.

        Args:
            invoice_id: The PayPal invoice ID to remind about.
            subject: Email subject line, under 80 characters.
            message: The reminder message to the client, 40-120 words, polite and specific.
        """
        tool_log.append(f"propose_reminder({invoice_id})")
        match = next((r for r in _all() if r["id"] == invoice_id), None)
        if match is None:
            return "Error: no invoice with that ID. Call find_invoices first."
        if not match["is_open"]:
            return f"Error: invoice {match['number']} is {match['status']}, so no reminder is needed."
        proposals.append(
            {
                "invoice_id": invoice_id,
                "invoice_number": match["number"],
                "client_name": match["client_name"],
                "amount": f"{match['currency']} {match['amount']}",
                "subject": subject[:200],
                "message": message[:4000],
            }
        )
        return "Draft saved. The user will review it and decide whether to send it."

    contents = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part.from_text(text=m["text"])])
        for m in messages[-12:]
        if m.get("text")
    ]
    config = types.GenerateContentConfig(
        system_instruction=AGENT_INSTRUCTIONS.format(today=date.today().isoformat()),
        tools=[find_invoices, invoice_details, propose_reminder],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=8),
        temperature=0.3,
        thinking_config=FAST_THINKING,
    )
    if generate is None:
        generate = _generate_with_retry(api_key)
    response = generate(model=model, contents=contents, config=config)
    reply = (response.text or "").strip() or "Done."
    return {"reply": reply, "proposals": proposals, "tools_used": tool_log}
