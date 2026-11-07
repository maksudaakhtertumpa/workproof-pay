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
    """Accept 'owner/repo' or any github.com URL inside a repo (branch, commits, PR pages); return (owner, repo)."""
    text = text.strip().split("?")[0].split("#")[0]
    text = re.sub(r"^(?:https?://|git@)?(?:www\.)?github\.com[/:]", "", text)
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2 or not all(re.fullmatch(r"[\w.-]+", p) for p in parts[:2]):
        raise GitHubError("Use the form owner/repo or https://github.com/owner/repo")
    return parts[0], parts[1].removesuffix(".git")


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
        try:
            resp = http.get(f"/repos/{owner}/{name}/commits", params=params)
        except httpx.HTTPError as e:
            raise GitHubError("Couldn't reach GitHub. Check your connection and try again.") from e
        if resp.status_code == 404:
            raise GitHubError(f"Repo {owner}/{name} not found (private repos need a GITHUB_TOKEN).")
        if resp.status_code == 401:
            raise GitHubError("GitHub rejected the GITHUB_TOKEN. Check or replace it.")
        if resp.status_code in (403, 429):
            raise GitHubError("GitHub rate limit reached. Add a GITHUB_TOKEN or wait an hour.")
        if resp.status_code == 409:
            raise GitHubError("That repo is empty.")
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub returned an error ({resp.status_code}). Please try again in a minute.")

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
                try:
                    detail = http.get(f"/repos/{owner}/{name}/commits/{c['sha']}")
                except httpx.HTTPError:
                    break  # line stats are optional
                if detail.status_code == 200:
                    body = detail.json()
                    c["additions"] = body.get("stats", {}).get("additions")
                    c["deletions"] = body.get("stats", {}).get("deletions")
                    c["files"] = [f["filename"] for f in body.get("files", [])][:8]
        return commits
