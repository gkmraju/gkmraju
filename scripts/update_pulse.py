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
GRAPH_SVG = Path(__file__).resolve().parents[1] / "assets" / "contribution-graph.svg"
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


def heat_color(count: int) -> str:
    if count <= 0:
        return "#142235"
    if count == 1:
        return "#134E4A"
    if count <= 3:
        return "#0F766E"
    if count <= 7:
        return "#14B8A6"
    return "#F5B942"


def render_contribution_svg(calendar: dict, now: datetime) -> str:
    days = sorted(
        (
            day
            for week in calendar["weeks"]
            for day in week["contributionDays"]
            if date.fromisoformat(day["date"]) <= now.date()
        ),
        key=lambda day: day["date"],
    )
    current, longest, active_days = streaks(days, now.date())
    total = calendar["totalContributions"]
    peak = max((day["contributionCount"] for day in days), default=0)
    if not days:
        raise ValueError("Contribution calendar returned no days")

    start = date.fromisoformat(days[0]["date"])
    cell = 12
    gap = 4
    step = cell + gap
    grid_x = 88
    grid_y = 134
    grid_width = 53 * step
    month_labels: list[tuple[int, str]] = []
    seen_months: set[tuple[int, int]] = set()
    cells: list[str] = []

    for day in days:
        day_date = date.fromisoformat(day["date"])
        offset = (day_date - start).days
        week = offset // 7
        weekday = offset % 7
        x = grid_x + week * step
        y = grid_y + weekday * step
        count = day["contributionCount"]
        key = (day_date.year, day_date.month)
        if key not in seen_months:
            seen_months.add(key)
            month_labels.append((x, day_date.strftime("%b").upper()))
        noun = "contribution" if count == 1 else "contributions"
        cells.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" '
            f'fill="{heat_color(count)}"><title>{count} {noun} on {day_date.isoformat()}</title></rect>'
        )

    filtered_months: list[tuple[int, str]] = []
    for index, label in enumerate(month_labels):
        if index == 0 and len(month_labels) > 1 and month_labels[1][0] - label[0] < 40:
            continue
        if filtered_months and label[0] - filtered_months[-1][0] < 40:
            continue
        filtered_months.append(label)
    month_svg = "".join(
        f'<text x="{x}" y="112" class="month">{label}</text>'
        for x, label in filtered_months
    )

    legend_x = grid_x + grid_width - 152
    legend = "".join(
        f'<rect x="{legend_x + index * 18}" y="278" width="12" height="12" rx="3" fill="{color}"/>'
        for index, color in enumerate(("#142235", "#134E4A", "#0F766E", "#14B8A6", "#F5B942"))
    )

    return f'''<svg width="1200" height="320" viewBox="0 0 1200 320" fill="none" xmlns="http://www.w3.org/2000/svg">
  <title>GKM Raju contribution signal</title>
  <desc>{total} contributions over the last year, with a {current}-day current streak.</desc>
  <defs>
    <linearGradient id="graph-bg" x1="0" y1="0" x2="1200" y2="320" gradientUnits="userSpaceOnUse">
      <stop stop-color="#07111F"/>
      <stop offset="0.55" stop-color="#0B1727"/>
      <stop offset="1" stop-color="#08201E"/>
    </linearGradient>
    <linearGradient id="signal-line" x1="56" y1="0" x2="1144" y2="0" gradientUnits="userSpaceOnUse">
      <stop stop-color="#F5B942"/>
      <stop offset="0.48" stop-color="#2DD4BF"/>
      <stop offset="1" stop-color="#38BDF8"/>
    </linearGradient>
    <pattern id="graph-grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="#94A3B8" stroke-opacity="0.045"/>
    </pattern>
    <style>
      text {{ font-family: Inter, Segoe UI, Arial, sans-serif; }}
      .eyebrow {{ fill: #F5B942; font-size: 11px; font-weight: 700; letter-spacing: 2px; }}
      .title {{ fill: #F8FAFC; font-size: 27px; font-weight: 760; }}
      .sub {{ fill: #7F93A8; font-size: 13px; }}
      .metric-label {{ fill: #7F93A8; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; }}
      .metric-value {{ fill: #F8FAFC; font-size: 19px; font-weight: 750; }}
      .month {{ fill: #8DA2B7; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; }}
      .day {{ fill: #657A91; font-size: 10px; font-weight: 600; }}
    </style>
  </defs>
  <rect width="1200" height="320" rx="24" fill="url(#graph-bg)"/>
  <rect width="1200" height="320" rx="24" fill="url(#graph-grid)"/>
  <rect x="0.5" y="0.5" width="1199" height="319" rx="23.5" stroke="#2DD4BF" stroke-opacity="0.16"/>
  <rect x="56" y="95" width="1088" height="2" rx="1" fill="url(#signal-line)" opacity="0.8"/>

  <text x="56" y="34" class="eyebrow">CONTRIBUTION SIGNAL</text>
  <text x="56" y="67" class="title">{total:,} contributions in the last year</text>
  <text x="56" y="86" class="sub">Consistency, shipped work and open-source momentum</text>

  <g transform="translate(700 22)">
    <rect width="128" height="58" rx="14" fill="#102436" stroke="#2DD4BF" stroke-opacity="0.22"/>
    <text x="16" y="22" class="metric-label">CURRENT STREAK</text>
    <text x="16" y="47" class="metric-value">{current} DAYS</text>
  </g>
  <g transform="translate(842 22)">
    <rect width="128" height="58" rx="14" fill="#102436" stroke="#F5B942" stroke-opacity="0.22"/>
    <text x="16" y="22" class="metric-label">LONGEST</text>
    <text x="16" y="47" class="metric-value">{longest} DAYS</text>
  </g>
  <g transform="translate(984 22)">
    <rect width="160" height="58" rx="14" fill="#102436" stroke="#38BDF8" stroke-opacity="0.20"/>
    <text x="16" y="22" class="metric-label">ACTIVE DAYS · PEAK</text>
    <text x="16" y="47" class="metric-value">{active_days} · {peak}/DAY</text>
  </g>

  {month_svg}
  <text x="56" y="162" class="day">MON</text>
  <text x="56" y="194" class="day">WED</text>
  <text x="56" y="226" class="day">FRI</text>
  {''.join(cells)}

  <text x="88" y="288" class="sub">{days[0]['date']}  →  {days[-1]['date']}</text>
  <text x="{legend_x - 42}" y="288" class="sub">LESS</text>
  {legend}
  <text x="{legend_x + 96}" y="288" class="sub">MORE</text>
</svg>
'''


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
        GRAPH_SVG.write_text(render_contribution_svg(user["contributionsCollection"]["contributionCalendar"], now), encoding="utf-8")
    except (KeyError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Pulse refresh failed; preserving the last good snapshot: {exc}", file=sys.stderr)
        return 1

    print("README GitHub pulse and contribution graph refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
