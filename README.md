# WorkProof Pay

**An AI agent that turns a freelancer's GitHub commits, time logs or chat threads into PayPal invoices, then follows up until they're paid.**

Built for the [PayPal AI Hackathon 2026](https://paypalaihackathon.devpost.com/) · Python + FastAPI · Google Gemini · PayPal Invoicing API v2 + Webhooks (sandbox)

- 🌐 **Live demo:** https://workproof-pay.onrender.com (free server: allow up to a minute to wake up)
- 🎥 **Demo video:** _add YouTube link_
- 🧪 **Demo proof-of-work repo:** https://github.com/maksudaakhtertumpa/kopi-labs-website

---

## The problem

Freelancers, especially in Southeast Asia billing overseas clients, lose hours turning scattered work into invoices, and vague invoices get questioned or ignored. In PayPal's own Global Freelancer Survey (2017), **58% of freelancers in four Southeast Asian markets said they had not been paid for work at least once**, and **76% of payments from international clients came through PayPal**.

## What it does

1. **Proof of work in.** Pick a GitHub repo and a date range, or paste a chat thread or time log.
2. **AI drafts the invoice.** Gemini groups the raw activity into client-friendly line items, estimates hours and writes a work summary. **Every line cites its evidence**; lines with no real evidence are removed, so the AI can't invent billable work.
3. **Checked against the agreement.** Paste the agreed milestones and prices. The AI tags each line with its milestone and flags **work outside every milestone** (scope creep). A summary shows the value delivered per milestone against its agreed price.
4. **You stay in control.** Review and edit the line items in an editable table, then confirm.
5. **PayPal sends it.** The app creates an itemized PayPal invoice (hours × rate) whose note lists each milestone with links to the commits as proof, and sends it through PayPal.
6. **Paid, live.** When the client pays on PayPal, a **verified PayPal webhook** turns the invoice to Paid on the dashboard within seconds.
7. **The agent follows up.** Ask the billing agent "Who hasn't paid me?". It reads your PayPal invoices with function calling, finds overdue ones and drafts a reminder that names the delivered work. You click **Send via PayPal**.

## How PayPal is used

All invoicing runs on the **PayPal Invoicing API v2** and **PayPal Webhooks** (sandbox), in [`paypal_client.py`](paypal_client.py) and [`main.py`](main.py):

| Action | Endpoint |
| --- | --- |
| Authenticate | `POST /v1/oauth2/token` |
| Next invoice number | `POST /v2/invoicing/generate-next-invoice-number` |
| Create itemized invoice (hours × rate, due date, seller name, proof-of-work note) | `POST /v2/invoicing/invoices` |
| Send to client | `POST /v2/invoicing/invoices/{id}/send` |
| Track status | `GET /v2/invoicing/invoices`, `GET /v2/invoicing/invoices/{id}` |
| Payment reminder | `POST /v2/invoicing/invoices/{id}/remind` |
| Receive invoice events (paid, cancelled, refunded, updated) | Webhook → `POST /api/paypal/webhook` |
| Check each webhook really came from PayPal | `POST /v1/notifications/verify-webhook-signature` |

The client pays on PayPal's hosted invoice page, so no card data touches the app. PayPal is the source of truth: there is no database.

## How AI is used

**Model:** Google Gemini (`gemini-3.8-flash`, with automatic fallback to other free Gemini models when one is busy or out of quota) through the official `google-genai` Python SDK. See [`ai_agent.py`](ai_agent.py).

- **Structured extraction.** Gemini returns a typed JSON schema: summary, line items, hours, evidence IDs, milestone and flags. The server validates evidence IDs against the real commits, rounds hours and computes all money in Python with `Decimal`. **The AI never does the arithmetic.**
- **Milestone matching.** Each line is tagged with an agreed milestone or marked as outside scope; unknown milestone IDs are treated as outside scope.
- **Agent with function calling.** The billing agent has three tools: `find_invoices`, `invoice_details` (both read live PayPal data) and `propose_reminder`. It chains them on its own (find overdue → read the work note → draft a reminder).
- **Human in the loop.** `propose_reminder` only creates a draft; nothing reaches a client until the freelancer clicks send. Invoices need an explicit confirmation and are capped by `MAX_INVOICE_TOTAL`, and AI endpoints are rate-limited per IP.

AI coding tools: this project was built with help from Claude (Anthropic) as a coding assistant.

## Architecture

```
Browser (HTML + JS + AG Grid)
   │  /api/analyze ──► github_tool.py ──► GitHub REST API (commits)
   │                └─► ai_agent.analyze_work ──► Gemini (structured JSON, milestone tags)
   │  /api/invoices ─► paypal_client.py ──► PayPal Invoicing API v2 (create, send, list)
   │  /api/agent ────► ai_agent.run_agent ──► Gemini function calling
   │                        └─ tools: find_invoices, invoice_details, propose_reminder ──► PayPal
   │  /api/invoices/{id}/remind ──► PayPal remind (only after the user clicks Send)
   │  /api/events ◄── live "paid" notices
PayPal ──webhook──► /api/paypal/webhook ──► verify-webhook-signature ──► event feed
FastAPI (main.py), hosted on Render
```

## Testing for judges

- **Live demo:** https://workproof-pay.onrender.com, running in PayPal **Sandbox** (no real money moves).
- **Try it:** enter repo `maksudaakhtertumpa/kopi-labs-website`, keep the default dates, and paste these milestones:
  ```
  Phase 1: Landing page, menu and branding - USD 400
  Phase 2: Online booking with email notifications - USD 350
  ```
  Then click **Draft invoice from my work**.
- **Pay an invoice:** click **View** next to an invoice, open it in a private window, log in with the sandbox buyer `sb-0tp6v53708007@personal.example.com`, password `7W2<Uu.%`, and pay with the **Visa** card. The dashboard turns it Paid within seconds.
- **Automated tests** (no keys needed; PayPal and Gemini are faked):
  ```bash
  pip install -r requirements-dev.txt
  pytest -q
  ```

## Run it locally

**Requirements:** Python 3.10+, a free [PayPal Developer](https://developer.paypal.com/dashboard/) account, a free [Google AI Studio](https://aistudio.google.com/) API key.

```bash
git clone https://github.com/maksudaakhtertumpa/workproof-pay.git
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

1. **PayPal:** Developer Dashboard → **Sandbox** → create a **US Business** sandbox account → **Apps & Credentials** → **Create App** (Merchant, linked to that account) → tick **Invoicing** → copy **Client ID** and **Secret** into `.env`.
2. **Sandbox client (buyer):** **Testing Tools → Sandbox Accounts** → **Create account** → Personal, **United States**. Put its email in `DEFAULT_CLIENT_EMAIL`.
3. **Gemini:** [aistudio.google.com](https://aistudio.google.com/) → **Get API key** → `GEMINI_API_KEY`.
4. **Webhooks (optional, needs a public URL):** in your app → **Webhooks** → add `https://<your-host>/api/paypal/webhook` with the invoice paid, cancelled, refunded and updated events → copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID`.
5. **Demo repo (optional):** `bash scripts/make_demo_repo.sh` creates a sample client project with 15 commits over the last week.

## Deploy to Render

The repo includes [`render.yaml`](render.yaml). On [Render](https://render.com): **New → Blueprint**, pick this repo, and fill in the secret env vars (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `GEMINI_API_KEY`, `PAYPAL_WEBHOOK_ID`).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Authorization failed due to insufficient permissions` | Tick **Invoicing** in the app's features. If it still fails, create a new app: PayPal caches old tokens for hours. |
| Invoice shows **Scheduled** instead of Sent | Set `SELLER_TIMEZONE` to your seller account's time zone. |
| Payment stays pending | Use a **US** sandbox seller and pay with the sandbox **Visa** card, not the bank account. |
| `Gemini free-tier quota is used up` | Free limits are per model per day; add more models to `GEMINI_FALLBACK_MODELS` or try tomorrow. |
| `GitHub rate limit reached` | Add `GITHUB_TOKEN` to `.env`. |

## Built with

PayPal Invoicing API v2 · PayPal Webhooks · Google Gemini (`google-genai`) · FastAPI · httpx · Pydantic · AG Grid Community · Render · GitHub REST API

## License

[MIT](LICENSE)
