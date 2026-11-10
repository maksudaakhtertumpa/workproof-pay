"""App settings, loaded from environment variables (or a local .env file)."""

import os

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
# SANDBOX for the hackathon. Never switch to LIVE for the demo.
PAYPAL_ENVIRONMENT = os.getenv("PAYPAL_ENVIRONMENT", "SANDBOX").strip().upper()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()

# Webhook ID from PayPal Developer Dashboard -> your app -> Webhooks (for live 'paid' updates).
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()

# Optional: raises the GitHub API limit from 60 to 5,000 requests per hour.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

# Your business name, shown to the client on the PayPal invoice (instead of the account default).
SELLER_NAME = os.getenv("SELLER_NAME", "").strip()

# Defaults shown in the form (handy for the demo video).
DEFAULT_CLIENT_NAME = os.getenv("DEFAULT_CLIENT_NAME", "Acme Ltd")
DEFAULT_CLIENT_EMAIL = os.getenv("DEFAULT_CLIENT_EMAIL", "")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD").upper()
DEFAULT_HOURLY_RATE = _float("DEFAULT_HOURLY_RATE", 40)

# The "Try the demo" button: a sample client project whose commits stay fixed in time,
# so the demo works the same during judging as on the day it was built.
DEMO_CLIENT_NAME = "Kopi Labs"
DEMO_REPO = os.getenv("DEMO_REPO", "maksudaakhtertumpa/kopi-labs-website").strip()
DEMO_SINCE = os.getenv("DEMO_SINCE", "2026-10-31").strip()
DEMO_UNTIL = os.getenv("DEMO_UNTIL", "2026-11-07").strip()
DEMO_HOURLY_RATE = DEFAULT_HOURLY_RATE
DEMO_MILESTONES = (
    "Phase 1: Landing page, menu and branding - USD 400\n"
    "Phase 2: Online booking with email notifications - USD 350"
)

# Guardrails
MAX_INVOICE_TOTAL = _float("MAX_INVOICE_TOTAL", 5000)
AI_REQUESTS_PER_HOUR = int(_float("AI_REQUESTS_PER_HOUR", 60))

# Who the public demo may send invoices to. In Sandbox the default only allows PayPal sandbox
# test accounts, so strangers can't use the live demo to send invoices to real people.
_default_domains = "example.com" if PAYPAL_ENVIRONMENT == "SANDBOX" else ""
ALLOWED_RECIPIENT_DOMAINS = [
    d.strip().lower().lstrip("@") for d in os.getenv("ALLOWED_RECIPIENT_DOMAINS", _default_domains).split(",") if d.strip()
]
PAYPAL_REQUESTS_PER_HOUR = int(_float("PAYPAL_REQUESTS_PER_HOUR", 30))

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD", "SGD", "MYR", "JPY"}
