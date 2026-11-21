"""Broad sources that cover companies we don't list ourselves."""
from __future__ import annotations

import os
from urllib.parse import quote

from .http import get_json
from .models import Job

SIMPLIFY_FEEDS = [
    # Pitt CSC + Simplify community lists (summer and off-season terms).
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
]


def fetch_simplify(session) -> list[Job]:
    out, seen = [], set()
    for feed in SIMPLIFY_FEEDS:
        for j in get_json(session, feed):
            if not j.get("active") or not j.get("is_visible", True) or j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            locs = j.get("locations") or []
            ts = j.get("date_posted")
            posted = ""
            if ts:
                import datetime as dt

                posted = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
            out.append(Job(
                company=j.get("company_name", ""),
                title=j.get("title", ""),
                location="; ".join(locs),
                url=j.get("url", ""),
                source="simplify",
                posted=posted,
                remote=any("remote" in L.lower() for L in locs),
            ))
    return out


def fetch_adzuna(session, searches: list[dict]) -> list[Job]:
    """Adzuna job-search API (free key at developer.adzuna.com). Skipped when no key is set."""
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    out, seen = [], set()
    for s in searches:
        for page in range(1, 4):
            url = (f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?app_id={app_id}&app_key={app_key}"
                   f"&results_per_page=50&what_or={quote('intern internship co-op')}"
                   f"&where={quote(s['where'])}&distance={s.get('km', 60)}&max_days_old=45&content-type=application/json")
            d = get_json(session, url)
            res = d.get("results") or []
            for j in res:
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                out.append(Job(
                    company=(j.get("company") or {}).get("display_name", ""),
                    title=j.get("title", "").replace("<strong>", "").replace("</strong>", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    url=j.get("redirect_url", ""),
                    source="adzuna",
                    posted=(j.get("created") or "")[:10],
                ))
            if len(res) < 50:
                break
    return out
