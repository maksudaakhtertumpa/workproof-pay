"""Check that your keys work before running the app.

Usage:  python check_setup.py
"""

from datetime import date, timedelta

import config

OK, FAIL, SKIP = "✅", "❌", "⏭️ "


def check_paypal() -> bool:
    from paypal_client import PayPalClient, PayPalError

    if not (config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET):
        print(f"{FAIL} PayPal: PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing in .env")
        return False
    try:
        pp = PayPalClient(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET, config.PAYPAL_ENVIRONMENT)
        pp.check_credentials()
        count = len(pp.list_invoices(page_size=5))
        print(f"{OK} PayPal ({config.PAYPAL_ENVIRONMENT}): logged in, invoicing API works ({count} recent invoices)")
        return True
    except PayPalError as e:
        print(f"{FAIL} PayPal: {e.message}")
        return False


def check_gemini() -> bool:
    if not config.GEMINI_API_KEY:
        print(f"{FAIL} Gemini: GEMINI_API_KEY missing in .env")
        return False
    from google.genai import errors

    import ai_agent

    try:
        generate = ai_agent._generate_with_retry(config.GEMINI_API_KEY)
        r = generate(model=config.GEMINI_MODEL, contents="Reply with just: ready")
        print(f"{OK} Gemini: {r.text.strip()[:40]} (main model {config.GEMINI_MODEL}, with fallbacks {', '.join(ai_agent.FALLBACK_MODELS)})")
        return True
    except errors.APIError as e:
        print(f"{FAIL} Gemini {e.code}: {(e.message or '')[:200]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} Gemini: {str(e)[:200]}")
        return False


def check_github() -> bool:
    from github_tool import GitHubError, fetch_commits

    try:
        commits = fetch_commits(
            "paypal/agent-toolkit", date.today() - timedelta(days=365), date.today(), config.GITHUB_TOKEN
        )
        token = "with token" if config.GITHUB_TOKEN else "no token (60 requests/hour)"
        print(f"{OK} GitHub: API reachable, {token}, sample repo has {len(commits)} commits this year")
        return True
    except (GitHubError, Exception) as e:  # noqa: BLE001
        print(f"{FAIL} GitHub: {e}")
        return False


def main() -> None:
    print("Checking WorkProof Pay setup…\n")
    results = [check_paypal(), check_gemini(), check_github()]
    if not config.DEFAULT_CLIENT_EMAIL:
        print(f"{SKIP} Tip: set DEFAULT_CLIENT_EMAIL in .env to your US sandbox buyer email")
    print("\nAll good! Run:  uvicorn main:app --reload" if all(results) else "\nFix the ❌ items above, then run this again.")


if __name__ == "__main__":
    main()
