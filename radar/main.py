"""Daily run: detect boards, pull postings, filter, diff against yesterday, publish, email.

    python -m radar.main                # full run
    python -m radar.main --no-email     # don't send mail
    python -m radar.main --only "Lippert,Zimmer Biomet"   # debug a few companies
    python -m radar.main --redetect     # ignore the cached board locations
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import aggregators, ats, emailer, report
from .filters import Filter
from .http import make_session
from .models import Job

ROOT = Path(__file__).resolve().parent.parent
CFG, DATA, DOCS = ROOT / "config", ROOT / "data", ROOT / "docs"
REDETECT_OK_DAYS, REDETECT_FAIL_DAYS, FORGET_DAYS = 21, 4, 45
SOURCE_RANK = {"simplify": 5, "adzuna": 6, "page": 4}  # lower wins when the same posting shows up twice


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def load_companies() -> list[dict]:
    raw = load_yaml(CFG / "companies.yaml")
    out, seen = [], set()
    for group, items in raw.items():
        local = group != "chicago_and_national"
        for c in items or []:
            if c["name"].lower() in seen:
                continue
            seen.add(c["name"].lower())
            out.append({**c, "group": group, "local": local})
    return out


def board_for(company: dict, cache: dict, session, today: dt.date, force: bool) -> dict:
    if company.get("ats"):  # pinned in companies.yaml
        b = {k: v for k, v in company.items() if k not in ("name", "website", "group", "local", "careers", "note")}
        b.setdefault("careers_url", company.get("careers") or company.get("website", ""))
        return {**b, "pinned": True}
    hit = cache.get(company["name"])
    if hit and hit.get("v") != ats.DETECT_VERSION:
        hit = None  # detected by an older version of the detector
    if hit and not force:
        age = (today - dt.date.fromisoformat(hit.get("detected", "2000-01-01"))).days
        ok = hit.get("ats") not in ("none", None)
        if (ok and age < REDETECT_OK_DAYS) or (not ok and age < REDETECT_FAIL_DAYS):
            return hit
    b = ats.detect(session, company)
    b["detected"] = today.isoformat()
    b["v"] = ats.DETECT_VERSION
    return b


def run_company(company, cache, today, force):
    session = make_session()
    board = board_for(company, cache, session, today, force)
    status = {"company": company["name"], "group": company["group"], "ats": board.get("ats"),
              "careers_url": board.get("careers_url", ""), "jobs": 0, "error": board.get("error", "")}
    jobs: list[Job] = []
    if board.get("ats") not in ("none", None):
        try:
            jobs = ats.fetch(session, company["name"], board)
        except Exception as e:  # noqa: BLE001
            status["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            # A broken structured feed gets one more chance via the plain page scan.
            if board.get("ats") != "page" and board.get("careers_url"):
                try:
                    jobs = ats.fetch_page(session, company["name"], {"url": board["careers_url"]})
                    status["error"] += " (fell back to page scan)"
                except Exception:  # noqa: BLE001
                    pass
            if not board.get("pinned"):
                board["detected"] = "2000-01-01"  # re-detect next run
    for j in jobs:
        j.local_company = company["local"]
        if not j.company:
            j.company = company["name"]
    status["jobs"] = len(jobs)
    return company["name"], board, jobs, status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--redetect", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-aggregators", action="store_true")
    args = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    filters_cfg = load_yaml(CFG / "filters.yaml")
    flt = Filter(filters_cfg)
    companies = load_companies()
    if args.only:
        want = {x.strip().lower() for x in args.only.split(",")}
        companies = [c for c in companies if c["name"].lower() in want]

    cache = load_json(DATA / "boards.json", {})
    seen = load_json(DATA / "seen.json", {})

    raw: list[Job] = []
    statuses = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_company, c, cache, today, args.redetect) for c in companies]
        for f in as_completed(futs):
            try:
                name, board, jobs, status = f.result()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                continue
            if not board.get("pinned"):
                cache[name] = board
            raw.extend(jobs)
            statuses.append(status)
            mark = "ok " if status["jobs"] else ("ERR" if status["error"] else " - ")
            print(f"[{mark}] {name:<38} {str(status['ats']):<15} {status['jobs']:>4} postings  {status['error'][:70]}")

    agg_status = {}
    if not args.skip_aggregators and not args.only:
        s = make_session()
        for label, fn in [("Simplify (Pitt CSC) list", lambda: aggregators.fetch_simplify(s)),
                          ("Adzuna", lambda: aggregators.fetch_adzuna(s, filters_cfg.get("adzuna_searches") or [
                              {"where": "South Bend, Indiana"}, {"where": "Fort Wayne, Indiana"},
                              {"where": "Warsaw, Indiana"}, {"where": "Merrillville, Indiana"},
                              {"where": "Kalamazoo, Michigan"}, {"where": "Grand Rapids, Michigan"},
                              {"where": "Chicago, Illinois", "km": 40}]))]:
            try:
                got = fn()
                raw.extend(got)
                agg_status[label] = f"{len(got)} postings"
            except Exception as e:  # noqa: BLE001
                agg_status[label] = f"error: {type(e).__name__}: {str(e)[:120]}"
            print(f"[agg] {label}: {agg_status[label]}")

    # Filter + merge duplicates (same company + title from two sources → keep the direct link).
    local_names = {c["name"].lower() for c in companies if c["local"]}
    merged: dict[str, Job] = {}
    for j in raw:
        if j.company.lower() in local_names:
            j.local_company = True
        kept = flt.apply(j)
        if not kept:
            continue
        k = kept.loose_key
        cur = merged.get(k)
        if cur is None or SOURCE_RANK.get(kept.source, 0) < SOURCE_RANK.get(cur.source, 0):
            merged[k] = kept

    matches = []
    new_count = 0
    for k, j in merged.items():
        rec = seen.get(k)
        is_new = rec is None
        if is_new:
            rec = {"first_seen": today.isoformat()}
            new_count += 1
        rec["last_seen"] = today.isoformat()
        seen[k] = rec
        matches.append({"key": k, "company": j.company, "title": j.title, "location": j.location, "url": j.url,
                        "source": j.source, "posted": j.posted, "region": j.region, "categories": j.categories,
                        "local": j.local_company, "first_seen": rec["first_seen"], "new": is_new})
    # Forget postings we haven't seen for a while so a re-post counts as new again.
    seen = {k: v for k, v in seen.items()
            if (today - dt.date.fromisoformat(v["last_seen"])).days <= FORGET_DAYS}

    # Companies the community lists/Adzuna found in our regions that we don't track directly.
    tracked = {c["name"].lower() for c in load_companies()}
    discovered = load_json(DATA / "discovered_companies.json", {})
    for m in matches:
        if m["source"] in ("simplify", "adzuna") and m["region"] in ("Northern Indiana", "Southern Michigan") \
                and m["company"].lower() not in tracked:
            d = discovered.setdefault(m["company"], {"first_seen": today.isoformat(), "example": m["url"]})
            d["last_seen"] = today.isoformat()

    if not args.only:
        save_json(DATA / "boards.json", cache)
        save_json(DATA / "seen.json", seen)
        save_json(DATA / "discovered_companies.json", discovered)
        DOCS.mkdir(exist_ok=True)
        stamp = now.astimezone(ZoneInfo("America/Indiana/Indianapolis")).strftime("%b %d, %Y %I:%M %p ET")
        report.write_page(DOCS / "index.html", matches, stamp, len(companies), list(filters_cfg["categories"]))
        report.write_csv(DOCS / "matches.csv", matches)
        write_sources_report(DOCS / "sources.md", statuses, agg_status, stamp)

    new = [m for m in matches if m["new"]]
    print(f"\n{len(raw)} postings scanned → {len(matches)} matches, {new_count} new.")
    by_region = {}
    for m in matches:
        by_region[m["region"]] = by_region.get(m["region"], 0) + 1
    print("  " + ", ".join(f"{r}: {n}" for r, n in sorted(by_region.items())))

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Internship Radar\n{len(matches)} matches, **{new_count} new**.\n\n")
            for m in sorted(new, key=lambda m: (m["region"], m["company"]))[:60]:
                f.write(f"- **{m['company']}**: [{m['title']}]({m['url']}) ({m['location'] or m['region']})\n")

    if new and not args.no_email and not args.only:
        if emailer.smtp_configured():
            page_url = os.getenv("PAGE_URL", "")
            body = report.email_html(new, len(matches), page_url)
            text = "\n".join(f"- {m['company']}: {m['title']} ({m['location'] or m['region']}) {m['url']}"
                             for m in new[:200])
            subj = f"{len(new)} new internship{'s' if len(new) != 1 else ''} near you"
            try:
                emailer.send(subj, body, text)
                print("Email sent.")
            except Exception as e:  # noqa: BLE001
                print(f"Email failed: {type(e).__name__}: {e}", file=sys.stderr)
                return 2
        else:
            print("SMTP not configured; skipping email.")
    return 0


def write_sources_report(path: Path, statuses: list[dict], agg: dict, stamp: str) -> None:
    statuses = sorted(statuses, key=lambda s: (s["group"], s["company"].lower()))
    ok = sum(1 for s in statuses if s["jobs"])
    lines = [f"# Source health — {stamp}", "",
             f"{ok} of {len(statuses)} company boards returned postings this run.", ""]
    for k, v in agg.items():
        lines.append(f"- **{k}**: {v}")
    lines += ["", "Companies with **0 postings and an error** need a look: open their careers page, copy the job-board",
              "URL into `careers:` in `config/companies.yaml` (or pin `ats:` + its params), and the next run will use it.", "",
              "| Group | Company | Board type | Postings | Note |", "|---|---|---|---:|---|"]
    for s in statuses:
        url = s["careers_url"]
        board = f"[{s['ats']}]({url})" if url else str(s["ats"])
        note = re.sub(r"[|\n]", " ", s["error"])[:140]
        lines.append(f"| {s['group']} | {s['company']} | {board} | {s['jobs']} | {note} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
