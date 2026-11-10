"""Save one real AI draft for the "Try the demo" project, so the live demo keeps working
when Gemini's free tier is busy or out of quota (for example while judges test it).

Run from the project folder:  python scripts/make_demo_draft.py
It writes demo_draft.json, which the app serves only for the exact demo request and labels as a saved draft.
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_agent  # noqa: E402
import config  # noqa: E402
from github_tool import fetch_commits  # noqa: E402

since, until = date.fromisoformat(config.DEMO_SINCE), date.fromisoformat(config.DEMO_UNTIL)
commits = fetch_commits(config.DEMO_REPO, since, until, config.GITHUB_TOKEN, "")
evidence_text, evidence = ai_agent.build_evidence_from_commits(commits)
result = ai_agent.analyze_work(
    config.GEMINI_API_KEY,
    config.GEMINI_MODEL,
    evidence_text,
    evidence,
    config.DEMO_HOURLY_RATE,
    "USD",
    config.DEMO_CLIENT_NAME,
    "",
    milestones=ai_agent.parse_milestones(config.DEMO_MILESTONES),
)
out = {
    "repo": config.DEMO_REPO,
    "since": config.DEMO_SINCE,
    "until": config.DEMO_UNTIL,
    "made_on": date.today().isoformat(),
    "commits": commits,
    "summary": result["summary"],
    "line_items": result["line_items"],
    "flags": result["flags"],
}
(ROOT / "demo_draft.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
hours = sum(i["hours"] for i in result["line_items"])
print(f"Saved demo_draft.json: {len(commits)} commits, {len(result['line_items'])} line items, {hours} hours")
