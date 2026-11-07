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

# Optional: raises the GitHub API limit from 60 to 5,000 requests per hour.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

# Defaults shown in the form (handy for the demo video).
DEFAULT_CLIENT_NAME = os.getenv("DEFAULT_CLIENT_NAME", "Acme Ltd")
DEFAULT_CLIENT_EMAIL = os.getenv("DEFAULT_CLIENT_EMAIL", "")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD").upper()
DEFAULT_HOURLY_RATE = _float("DEFAULT_HOURLY_RATE", 40)

# Guardrails
MAX_INVOICE_TOTAL = _float("MAX_INVOICE_TOTAL", 5000)
AI_REQUESTS_PER_HOUR = int(_float("AI_REQUESTS_PER_HOUR", 60))

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD", "SGD", "MYR", "JPY"}
