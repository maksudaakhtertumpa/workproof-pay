# WorkProof Pay

**An AI agent that turns a freelancer's GitHub commits, time logs or chat threads into PayPal invoices, then follows up until they're paid.**

Built for the [PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/) · Python + FastAPI · Google Gemini · PayPal Invoicing API (sandbox)

> 🎥 Demo video: _add YouTube link_ · 🌐 Live demo: _add Render URL_

---

## The problem

Freelancers, especially in Southeast Asia billing overseas clients, lose hours turning scattered work into invoices, and vague invoices get questioned or ignored. In PayPal's own Global Freelancer Survey (2017), **58% of freelancers in four Southeast Asian markets said they had not been paid for work at least once**, and **76% of payments from international clients came through PayPal**.

## What it does

1. **Proof of work in.** Pick a GitHub repo and a date range, or paste a chat thread / time log.
2. **AI drafts the invoice.** Gemini groups the raw activity into client-friendly line items, estimates hours, and writes a work summary. **Every line item cites its evidence**. Items with no real evidence are removed automatically, so the AI can't invent billable work.
3. **You stay in control.** Review and edit the line items in an editable table, then confirm.
4. **PayPal sends it.** The app creates an itemized PayPal invoice (hours × rate), with links to the commits as proof in the invoice note, and sends it through PayPal.
5. **The agent follows up.** Ask the billing agent "Who hasn't paid me?" It looks up your PayPal invoices with function calling, finds overdue ones, and drafts specific, polite reminders. You click **Send via PayPal**, and PayPal sends the reminder.
6. **Paid.** The client pays on PayPal's invoice page; the dashboard updates to **PAID**.

## How PayPal is used

All invoicing runs on the **PayPal Invoicing API v2** (sandbox), via [`paypal_client.py`](paypal_client.py):

| Action | Endpoint |
| --- | --- |
| Authenticate | `POST /v1/oauth2/token` |
| Next invoice number | `POST /v2/invoicing/generate-next-invoice-number` |
| Create itemized invoice (hours × rate, due date, proof-of-work note) | `POST /v2/invoicing/invoices` |
| Send to client | `POST /v2/invoicing/invoices/{id}/send` |
| Track status (SENT → PAID) | `GET /v2/invoicing/invoices`, `GET /v2/invoicing/invoices/{id}` |
| Payment reminder | `POST /v2/invoicing/invoices/{id}/remind` |

The client pays on PayPal's hosted invoice page, so no card data touches the app.

## How AI is used

**Model:** Google Gemini (`gemini-3.8-flash` by default) through the official `google-genai` Python SDK. See [`ai_agent.py`](ai_agent.py).

- **Structured extraction.** Gemini returns a typed JSON schema (Pydantic model): summary, line items, hours, evidence IDs and flags. The server then validates evidence IDs against the real commits/lines, rounds hours, and computes all money in Python with `Decimal`. **The AI never does the arithmetic.**
- **Agent with function calling.** The billing agent has three tools: `find_invoices`, `invoice_details` (both read live PayPal data) and `propose_reminder`. It chains them on its own (e.g. find overdue → read the work note → draft a reminder that names the delivered work).
- **Human-in-the-loop guardrails.** `propose_reminder` only creates a draft; nothing goes to a client until the freelancer clicks send. Invoices also need an explicit confirmation, are capped by `MAX_INVOICE_TOTAL`, and AI endpoints are rate-limited per IP.

AI coding tools: this project was built with help from Claude (Anthropic) as a coding assistant.

## Architecture

```
Browser (HTML + JS + AG Grid)
   │  /api/analyze ──► github_tool.py ──► GitHub REST API (commits)
   │                └─► ai_agent.analyze_work ──► Gemini (structured JSON)
   │  /api/invoices ─► paypal_client.py ──► PayPal Invoicing API v2 (create, send, list)
   │  /api/agent ────► ai_agent.run_agent ──► Gemini function calling
   │                        └─ tools: find_invoices, invoice_details, propose_reminder ──► PayPal
   │  /api/invoices/{id}/remind ──► PayPal remind (only after the user clicks Send)
FastAPI (main.py)
```

PayPal is the source of truth: there is no database. Invoices, statuses and notes are read back from PayPal.

## Run it locally

**Requirements:** Python 3.10+, a free [PayPal Developer](https://developer.paypal.com/dashboard/) account, a free [Google AI Studio](https://aistudio.google.com/) API key.

```bash
git clone https://github.com/<your-username>/workproof-pay.git
cd workproof-pay
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then open .env and add your keys
python check_setup.py              # checks PayPal, Gemini and GitHub
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

### Getting the keys

1. **PayPal:** Developer Dashboard → **Sandbox** → **Apps & Credentials** → **Create App** (Merchant) → copy **Client ID** and **Secret** into `.env`.
2. **Sandbox client (buyer):** **Testing Tools → Sandbox Accounts** → **Create account** → Personal, **United States**. Put its email in `DEFAULT_CLIENT_EMAIL`.
3. **Gemini:** [aistudio.google.com](https://aistudio.google.com/) → **Get API key** → copy into `GEMINI_API_KEY`.
4. **GitHub (optional):** a personal access token with no scopes raises the rate limit and lets the AI see lines changed per commit.

### Try the full flow

1. Enter a public repo (e.g. your own) and a date range with commits, then click **Analyze work with AI**.
2. Edit the line items if needed, tick the confirmation, and click **Create & send PayPal invoice**.
3. Click **Open client payment page** in a private window and log in with the **sandbox buyer** account to pay.
4. Click **Refresh**: the invoice shows **PAID**. For an unpaid one, ask the agent **"Who hasn't paid me?"**

## Testing for judges

- **Live demo:** _add Render URL_. It runs in PayPal **sandbox** mode, so no real money moves.
- **Sandbox buyer login to pay an invoice:** _add email_ / _add password_
- **Automated tests** (no keys needed; PayPal and Gemini are faked):

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Deploy to Render

The repo includes [`render.yaml`](render.yaml). On [Render](https://render.com): **New → Blueprint**, pick this repo, and fill in the secret env vars (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `GEMINI_API_KEY`, `DEFAULT_CLIENT_EMAIL`).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `PayPal login failed` | Use the **Sandbox** Client ID/Secret, not Live. |
| Payment stays pending / not PAID | Log in to the sandbox **business** account at sandbox.paypal.com and accept the payment, or use a US business sandbox account for the app. |
| `Gemini free-tier limit reached` | Wait a minute; free-tier limits are per minute and per day. |
| `GitHub rate limit reached` | Add `GITHUB_TOKEN` to `.env`. |

## Built with

PayPal Invoicing API v2 · Google Gemini (`google-genai`) · FastAPI · httpx · Pydantic · AG Grid Community · Render

## License

[MIT](LICENSE)
