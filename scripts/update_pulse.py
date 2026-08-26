#!/usr/bin/env python3
"""Refresh the repository-native GitHub pulse block in README.md."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


LOGIN = "gkmraju"
README = Path(__file__).resolve().parents[1] / "README.md"
START_MARKER = "<!-- GITHUB-PULSE:START -->"
END_MARKER = "<!-- GITHUB-PULSE:END -->"
GRAPHQL_URL = "https://api.github.com/graphql"
EVENTS_URL = f"https://api.github.com/users/{LOGIN}/events/public?per_page=15"


QUERY = """
query ProfilePulse($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    followers { totalCount }
    repositories(first: 1, privacy: PUBLIC, ownerAffiliations: OWNER) { totalCount }
    pullRequests(first: 1, states: MERGED) { totalCount }
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      repositoriesContributedTo(first: 1) { totalCount }
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""


def api_request(url: str, token: str, payload: dict | None = None) -> object:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "gkmraju-profile-pulse",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def streaks(days: list[dict], today: date) -> tuple[int, int, int]:
    counts = {date.fromisoformat(day["date"]): day["contributionCount"] for day in days}
    active_days = sum(1 for count in counts.values() if count > 0)

    cursor = today
    if counts.get(cursor, 0) == 0:
        cursor -= timedelta(days=1)

    current = 0
    while counts.get(cursor, 0) > 0:
        current += 1
        cursor -= timedelta(days=1)

    longest = 0
    run = 0
    for day in sorted(counts):
        if counts[day] > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    return current, longest, active_days


def event_summary(event: dict) -> str | None:
    kind = event.get("type", "")
    payload = event.get("payload") or {}
    if kind == "PushEvent":
        count = len(payload.get("commits") or [])
        return f"Pushed {count} commit{'s' if count != 1 else ''}"
    if kind == "PullRequestEvent":
        return f"Pull request {payload.get('action', 'updated')}"
    if kind == "IssuesEvent":
        return f"Issue {payload.get('action', 'updated')}"
    if kind == "IssueCommentEvent":
        return "Commented on an issue or pull request"
    if kind == "PullRequestReviewEvent":
        return "Reviewed a pull request"
    if kind == "PullRequestReviewCommentEvent":
        return "Commented on a pull request review"
    if kind == "CreateEvent":
        ref_type = payload.get("ref_type", "repository")
        return f"Created {ref_type}"
    if kind == "ForkEvent":
        return "Forked a repository"
    if kind == "WatchEvent":
        return "Starred a repository"
    if kind == "ReleaseEvent":
        return f"Release {payload.get('action', 'published')}"
    return None


def recent_activity(events: list[dict], limit: int = 5) -> list[str]:
    rows: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        summary = event_summary(event)
        repository = (event.get("repo") or {}).get("name")
        created_at = event.get("created_at")
        if not summary or not repository or not created_at:
            continue
        day = created_at[:10]
        key = (day, summary, repository)
        if key in seen:
            continue
        seen.add(key)
        url = f"https://github.com/{repository}"
        rows.append(f"| {day} | {summary} | [{repository}]({url}) |")
        if len(rows) == limit:
            break
    return rows


def render(user: dict, events: list[dict], now: datetime) -> str:
    collection = user["contributionsCollection"]
    calendar = collection["contributionCalendar"]
    days = [
        day
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ]
    current, longest, active_days = streaks(days, now.date())
    activity_rows = recent_activity(events)
    if not activity_rows:
        activity_rows = ["| — | No recent public event returned | — |"]

    return "\n".join(
        [
            START_MARKER,
            "| 🔥 Current streak | 🏆 Longest streak | ⚡ Contributions · 365d | 📅 Active days · 365d |",
            "|:--:|:--:|:--:|:--:|",
            f"| **{current} days** | **{longest} days** | **{calendar['totalContributions']:,}** | **{active_days}** |",
            "",
            "| 📦 Public repositories | ✅ Merged PRs | 🔀 PRs · 365d | 👀 Reviews · 365d |",
            "|:--:|:--:|:--:|:--:|",
            f"| **{user['repositories']['totalCount']}** | **{user['pullRequests']['totalCount']}** | **{collection['totalPullRequestContributions']}** | **{collection['totalPullRequestReviewContributions']}** |",
            "",
            "### Recent public activity",
            "",
            "| Date · UTC | Activity | Repository |",
            "|:--|:--|:--|",
            *activity_rows,
            "",
            f"<sub>Repository-native snapshot · Updated {now:%Y-%m-%d %H:%M UTC} from GitHub's API. The last successful snapshot remains visible if a refresh is interrupted.</sub>",
            END_MARKER,
        ]
    )


def replace_block(readme: str, block: str) -> str:
    if START_MARKER not in readme or END_MARKER not in readme:
        raise ValueError("GitHub pulse markers are missing from README.md")
    before, remainder = readme.split(START_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    return before + block + after


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=364)
    payload = {
        "query": QUERY,
        "variables": {
            "login": LOGIN,
            "from": start.isoformat(timespec="seconds"),
            "to": now.isoformat(timespec="seconds"),
        },
    }

    try:
        response = api_request(GRAPHQL_URL, token, payload)
        if not isinstance(response, dict) or response.get("errors"):
            raise RuntimeError(f"GitHub GraphQL returned errors: {response}")
        user = response["data"]["user"]
        events = api_request(EVENTS_URL, token)
        if not isinstance(events, list):
            raise RuntimeError("GitHub events response was not a list")

        original = README.read_text(encoding="utf-8")
        updated = replace_block(original, render(user, events, now))
        README.write_text(updated, encoding="utf-8")
    except (KeyError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Pulse refresh failed; preserving the last good snapshot: {exc}", file=sys.stderr)
        return 1

    print("README GitHub pulse refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
