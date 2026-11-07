"""Small client for the PayPal Invoicing API v2 (sandbox).

Docs: https://developer.paypal.com/docs/api/invoicing/v2/
"""

from __future__ import annotations

import time
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

BASE_URLS = {
    "SANDBOX": "https://api-m.sandbox.paypal.com",
    "LIVE": "https://api-m.paypal.com",
}

# PayPal treats an invoice_date "in the future" (in the seller's time zone) as SCHEDULED, not SENT.
# Asia is often a day ahead of a US seller, so dates are computed in the seller's time zone.
SELLER_TIMEZONE = os.getenv("SELLER_TIMEZONE", "America/Los_Angeles")


def seller_today() -> date:
    return datetime.now(ZoneInfo(SELLER_TIMEZONE)).date()


# Invoices that still need money from the client.
OPEN_STATUSES = {"SENT", "UNPAID", "PARTIALLY_PAID"}
PAID_STATUSES = {"PAID", "MARKED_AS_PAID", "PAYMENT_PENDING"}


class PayPalError(Exception):
    """A readable error built from PayPal's error response."""

    def __init__(self, status_code: int, message: str, debug_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.debug_id = debug_id


def money(value: Decimal | float | str) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        environment: str = "SANDBOX",
        transport: httpx.BaseTransport | None = None,
    ):
        if environment not in BASE_URLS:
            raise ValueError("PAYPAL_ENVIRONMENT must be SANDBOX or LIVE")
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        self.http = httpx.Client(base_url=BASE_URLS[environment], timeout=30, transport=transport)
        self._token: str | None = None
        self._token_expires_at = 0.0

    # ---------- auth & requests ----------

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        resp = self.http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        if resp.status_code != 200:
            raise PayPalError(
                resp.status_code,
                "PayPal login failed. Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
                "(and that they are Sandbox credentials).",
            )
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 3000))
        return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        headers.setdefault("Content-Type", "application/json")
        resp = self.http.request(method, path, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise self._error_from(resp)
        return resp

    @staticmethod
    def _error_from(resp: httpx.Response) -> PayPalError:
        try:
            body = resp.json()
        except ValueError:
            return PayPalError(resp.status_code, f"PayPal error {resp.status_code}: {resp.text[:300]}")
        message = body.get("message") or body.get("error_description") or body.get("name") or "PayPal error"
        details = body.get("details") or []
        if details:
            parts = []
            for d in details[:3]:
                field = d.get("field", "")
                issue = d.get("description") or d.get("issue", "")
                parts.append(f"{field} {issue}".strip())
            message = f"{message} ({'; '.join(parts)})"
        return PayPalError(resp.status_code, message, body.get("debug_id"))

    def check_credentials(self) -> bool:
        self._token = None
        self._access_token()
        return True

    # ---------- invoicing ----------

    def next_invoice_number(self) -> str:
        resp = self._request("POST", "/v2/invoicing/generate-next-invoice-number")
        return resp.json()["invoice_number"]

    def create_invoice(
        self,
        *,
        client_name: str,
        client_email: str,
        currency: str,
        items: list[dict[str, Any]],
        note: str,
        due_in_days: int,
        memo: str = "",
    ) -> dict[str, Any]:
        """Create a DRAFT invoice. Each item: name, description, hours, rate."""
        today = seller_today()
        body = {
            "detail": {
                "invoice_number": self.next_invoice_number(),
                "currency_code": currency,
                "invoice_date": today.isoformat(),
                "note": note[:4000],
                "memo": memo[:500],
                "payment_term": {
                    "term_type": "DUE_ON_DATE_SPECIFIED",
                    "due_date": (today + timedelta(days=due_in_days)).isoformat(),
                },
            },
            "primary_recipients": [
                {
                    "billing_info": {
                        "business_name": client_name[:300],
                        "email_address": client_email,
                    }
                }
            ],
            "items": [
                {
                    "name": item["name"][:200],
                    "description": (item.get("description") or "")[:1000],
                    "quantity": str(item["hours"]),
                    "unit_amount": {"currency_code": currency, "value": money(item["rate"])},
                    "unit_of_measure": "HOURS",
                }
                for item in items
            ],
            "configuration": {"allow_tip": False, "tax_inclusive": False},
        }
        resp = self._request(
            "POST",
            "/v2/invoicing/invoices",
            json=body,
            headers={"Prefer": "return=representation"},
        )
        data = resp.json()
        # Some responses only return a link to the new invoice; fetch it if so.
        if "id" not in data and data.get("href"):
            data = self.get_invoice(data["href"].rstrip("/").split("/")[-1])
        return data

    def send_invoice(self, invoice_id: str, subject: str = "", note: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"send_to_invoicer": True, "send_to_recipient": True}
        if subject:
            body["subject"] = subject[:4000]
        if note:
            body["note"] = note[:4000]
        resp = self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body)
        return resp.json() if resp.content else {}

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/invoicing/invoices/{invoice_id}").json()

    def list_invoices(self, page_size: int = 50) -> list[dict[str, Any]]:
        resp = self._request(
            "GET",
            "/v2/invoicing/invoices",
            params={"page": 1, "page_size": page_size, "total_required": "false"},
        )
        return resp.json().get("items", [])

    def send_reminder(self, invoice_id: str, subject: str, note: str) -> None:
        self._request(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/remind",
            json={"subject": subject[:4000], "note": note[:4000], "send_to_invoicer": True},
        )


def summarize_invoice(inv: dict[str, Any]) -> dict[str, Any]:
    """Flatten a PayPal invoice into the fields the UI and the AI agent need."""
    detail = inv.get("detail", {})
    recipients = inv.get("primary_recipients") or [{}]
    billing = recipients[0].get("billing_info", {}) if recipients else {}
    name = billing.get("business_name") or " ".join(
        filter(None, [billing.get("name", {}).get("given_name"), billing.get("name", {}).get("surname")])
    )
    amount = inv.get("amount", {})
    due_date = (detail.get("payment_term") or {}).get("due_date")
    status = inv.get("status", "UNKNOWN")
    days_overdue = 0
    if due_date and status in OPEN_STATUSES:
        days_overdue = max(0, (seller_today() - date.fromisoformat(due_date)).days)
    metadata = detail.get("metadata") or {}
    return {
        "id": inv.get("id"),
        "number": detail.get("invoice_number"),
        "status": status,
        "client_name": name or billing.get("email_address", ""),
        "client_email": billing.get("email_address", ""),
        "amount": amount.get("value"),
        "currency": amount.get("currency_code") or detail.get("currency_code"),
        "invoice_date": detail.get("invoice_date"),
        "due_date": due_date,
        "days_overdue": days_overdue,
        "is_open": status in OPEN_STATUSES,
        "is_paid": status in PAID_STATUSES,
        "payer_url": metadata.get("recipient_view_url"),
        "note": (detail.get("note") or "")[:600],
    }
