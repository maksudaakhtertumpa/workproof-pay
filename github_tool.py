"""Fetch commits from a GitHub repo to use as proof of work."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any

import httpx

API = "https://api.github.com"
MAX_COMMITS = 100
MAX_COMMITS_WITH_STATS = 40


class GitHubError(Exception):
    pass


def parse_repo(text: str) -> tuple[str, str]:
    """Accept 'owner/repo' or a github.com URL; return (owner, repo)."""
    text = text.strip().removesuffix(".git").rstrip("/")
    match = re.search(r"(?:github\.com[/:])?([\w.-]+)/([\w.-]+)$", text)
    if not match:
        raise GitHubError("Use the form owner/repo or https://github.com/owner/repo")
    return match.group(1), match.group(2)


def _iso(d: date, end_of_day: bool = False) -> str:
    t = time(23, 59, 59) if end_of_day else time(0, 0, 0)
    return datetime.combine(d, t, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_commits(
    repo: str,
    since: date,
    until: date,
    token: str = "",
    author: str = "",
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Return commits (newest first) with message, date, URL and, with a token, line stats."""
    owner, name = parse_repo(repo)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, Any] = {"since": _iso(since), "until": _iso(until, True), "per_page": MAX_COMMITS}
    if author:
        params["author"] = author

    with httpx.Client(base_url=API, headers=headers, timeout=20, transport=transport) as http:
        resp = http.get(f"/repos/{owner}/{name}/commits", params=params)
        if resp.status_code == 404:
            raise GitHubError(f"Repo {owner}/{name} not found (private repos need a GITHUB_TOKEN).")
        if resp.status_code == 403:
            raise GitHubError("GitHub rate limit reached. Add a GITHUB_TOKEN to .env or wait an hour.")
        if resp.status_code == 409:
            raise GitHubError("That repo is empty.")
        resp.raise_for_status()

        commits = []
        for c in resp.json():
            message = c["commit"]["message"].strip()
            if message.lower().startswith("merge "):
                continue  # merge commits are not billable work
            commits.append(
                {
                    "id": c["sha"][:7],
                    "sha": c["sha"],
                    "message": message[:500],
                    "date": c["commit"]["author"]["date"],
                    "author": c["commit"]["author"]["name"],
                    # short SHA keeps the proof links readable on the invoice; GitHub resolves them
                    "url": f"https://github.com/{owner}/{name}/commit/{c['sha'][:7]}",
                }
            )

        # Line counts help the AI estimate effort. Each one costs an API call, so only with a token.
        if token:
            for c in commits[:MAX_COMMITS_WITH_STATS]:
                detail = http.get(f"/repos/{owner}/{name}/commits/{c['sha']}")
                if detail.status_code == 200:
                    body = detail.json()
                    c["additions"] = body.get("stats", {}).get("additions")
                    c["deletions"] = body.get("stats", {}).get("deletions")
                    c["files"] = [f["filename"] for f in body.get("files", [])][:8]
        return commits
