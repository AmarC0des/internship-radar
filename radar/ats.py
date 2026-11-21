"""Applicant-tracking-system (ATS) detection and per-ATS job fetchers.

Every fetcher returns a list of Job objects with whatever location text the ATS
provides; filtering (internship? right field? right region?) happens later.

Verified against live boards in Nov 2026: greenhouse, lever, ashby, workday,
phenom, jibe, ukg, adp, paylocity, smartrecruiters.  The others follow each
vendor's documented/public format and degrade to the generic page scan if they
stop working.
"""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse, quote

from bs4 import BeautifulSoup

from .http import extract_js_object, get_json, get_text, post_json
from .models import Job

INTERN_SEARCH = ["intern", "co-op"]
DETECT_VERSION = 2  # bump when detection logic changes so cached boards get re-checked
INTERN_RE = re.compile(r"\b(intern|interns|internship|internships|co-?op|coop)\b", re.I)

# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

SIGNATURES = [
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([\w-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([\w.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)")),
    ("ukg", re.compile(r"(recruiting2?\.ultipro\.(?:com|ca))/([A-Z0-9]+)/JobBoard/(" + UUID + ")", re.I)),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com/recruiting/jobs/(?:All|List)/(" + UUID + ")", re.I)),
    ("adp", re.compile(r"(?:workforcenow\.adp\.com/[^\"' ]*?[?&]cid=|recruitment-current-openings[^>]*?cid=\")(" + UUID + ")", re.I)),
    ("icims", re.compile(r"https?://((?:careers-)?[\w-]+\.icims\.com)", re.I)),
    ("bamboohr", re.compile(r"https?://([\w-]+)\.bamboohr\.com/(?:careers|jobs)", re.I)),
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com", re.I)),
    ("breezy", re.compile(r"https?://([\w-]+)\.breezy\.hr", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("pinpoint", re.compile(r"https?://([\w-]+)\.pinpointhq\.com", re.I)),
    ("oracle", re.compile(r"https?://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)", re.I)),
    ("successfactors", re.compile(r"https?://((?:career\d*|jobs)\.[\w.-]*successfactors\.(?:com|eu))[^\"' ]*company=([\w-]+)", re.I)),
    # Recognised, but no structured feed: fall back to scanning the board page.
    ("paycom", re.compile(r"https?://www\.paycomonline\.net/v4/ats/web\.php/jobs[^\"' <]*", re.I)),
    ("hirebridge", re.compile(r"https?://recruit\.hirebridge\.com/v3/[^\"' <]*cid=\d+[^\"' <]*", re.I)),
    ("dayforce", re.compile(r"https?://jobs\.dayforcehcm\.com/[^\"' <]+", re.I)),
    ("jobvite", re.compile(r"https?://jobs\.jobvite\.com/[\w-]+[^\"' <]*", re.I)),
    ("jazzhr", re.compile(r"https?://[\w-]+\.applytojob\.com/apply[^\"' <]*", re.I)),
    ("taleo", re.compile(r"https?://[\w-]+\.taleo\.net/careersection/[^\"' <]+", re.I)),
    ("applicantstack", re.compile(r"https?://[\w-]+\.applicantstack\.com/x/openings", re.I)),
    ("silkroad", re.compile(r"https?://jobs\.silkroad\.com/[\w-]+/[\w-]+", re.I)),
]
PAGE_SCAN_ATS = {"paycom", "hirebridge", "dayforce", "jobvite", "jazzhr", "taleo", "applicantstack", "silkroad"}
# Paths on ATS domains that are not company boards.
RESERVED_SLUGS = {"embed", "js", "v1", "my-applications", "web-sso", "widget", "sr-jobs", "jobs", "careers",
                  "api", "static", "assets", "en", "privacy", "cookie-policy"}
BAD_ICIMS_HOSTS = re.compile(r"^(www|static|cookie-policy-scripts|assets|cdn|developer|community|help|login)\.", re.I)

CAREER_LINK_RE = re.compile(
    r"career|jobs?\b|employment|join[-_ ]?(our[-_ ])?team|work[-_ ](with|for)[-_ ]us|opportunit|openings|hiring|internship",
    re.I,
)
SKIP_LINK_RE = re.compile(r"\.(pdf|jpg|png|docx?)$|mailto:|tel:|linkedin\.com|facebook\.com|indeed\.com|glassdoor|twitter|x\.com/|youtube|instagram", re.I)


def match_ats(url: str, html: str) -> dict | None:
    blob = url + "\n" + html
    for name, rx in SIGNATURES:
        m = rx.search(blob)
        if not m:
            continue
        g = m.groups()
        if name == "workday":
            return {"ats": "workday", "host": f"{g[0]}.{g[1]}.myworkdayjobs.com", "tenant": g[0], "site": g[2]}
        if name in ("greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr", "recruitee", "breezy",
                    "workable", "pinpoint"):
            if g[0].lower() in RESERVED_SLUGS:
                # e.g. jobs.smartrecruiters.com/my-applications/<Company> — look for the real slug
                m2 = re.search(r"smartrecruiters\.com/(?:my-applications|web-sso/saml)/([\w-]+)", blob) if name == "smartrecruiters" else None
                if m2:
                    return {"ats": "smartrecruiters", "slug": m2.group(1)}
                continue
            return {"ats": name, "slug": g[0]}
        if name == "ukg":
            return {"ats": "ukg", "board": f"https://{g[0]}/{g[1]}/JobBoard/{g[2]}"}
        if name == "paylocity":
            return {"ats": "paylocity", "guid": g[0]}
        if name == "adp":
            return {"ats": "adp", "cid": g[0]}
        if name == "icims":
            host = g[0].lower()
            if host == "icims.com" or BAD_ICIMS_HOSTS.match(host):
                continue
            return {"ats": "icims", "host": host}
        if name == "oracle":
            return {"ats": "oracle", "host": g[0], "site": g[1]}
        if name == "successfactors":
            return {"ats": "successfactors", "host": g[0], "company_id": g[1]}
        if name in PAGE_SCAN_ATS:
            return {"ats": "page", "vendor": name, "url": unescape(m.group(0))}
    # Hosted career sites that sit on the company's own domain.
    if "phApp.ddo" in html or "phenompeople" in html:
        p = urlparse(url)
        loc = re.match(r"(.*?/[a-z]{2,6}/[a-z]{2})(?:/|$)", p.path)
        prefix = loc.group(1) if loc else ""
        return {"ats": "phenom", "base": f"{p.scheme}://{p.netloc}{prefix}"}
    oc = re.search(r"https?://([\w-]+\.fa(?:\.[\w-]+)?\.oraclecloud\.com)", html)
    site = re.search(r"/sites/([\w-]+)", url)
    if oc and site:  # Oracle Recruiting on a custom domain, e.g. careers.honeywell.com/en/sites/Honeywell
        return {"ats": "oracle", "host": oc.group(1), "site": site.group(1)}
    if "/careers-home" in blob and ("jibe" in html.lower() or "careers-home" in url):
        p = urlparse(url)
        return {"ats": "jibe", "origin": f"{p.scheme}://{p.netloc}"}
    if (re.search(r'class="[^"]*jobTitle-link', html) and "/search/" in html) or re.search(r"/platform/js/j2w/|jobs2web\.com", html):
        p = urlparse(url)
        return {"ats": "sf_rmk", "origin": f"{p.scheme}://{p.netloc}"}
    return None


GOOD_TEXT_RE = re.compile(r"search|openings|open positions|all jobs|current|view jobs|find jobs|apply|opportunities", re.I)
WEAK_LINK_RE = re.compile(r"benefit|why|culture|life-at|values|story|stories|blog|news|faq|event|diversity|veteran|leadership", re.I)


def _career_links(base_url: str, html: str, limit: int = 5) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = urljoin(base_url, base_tag["href"])
    seen, scored = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if SKIP_LINK_RE.search(href) or href.startswith("#") or href.lower().startswith("javascript"):
            continue
        text = a.get_text(" ", strip=True)[:80]
        if not (CAREER_LINK_RE.search(href) or CAREER_LINK_RE.search(text)):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if full in seen or full.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(full)
        score = 0
        host = urlparse(full).netloc.lower()
        if re.search(r"career|jobs|openings|opportunit", href + " " + text, re.I):
            score += 2
        if re.match(r"(careers?|jobs|recruiting|talent)\.", host):
            score += 3
        if GOOD_TEXT_RE.search(text):
            score += 2
        if re.search(r"intern|student|early.career|campus", href + " " + text, re.I):
            score += 1
        if WEAK_LINK_RE.search(href):
            score -= 2
        if any(rx.search(full) for _, rx in SIGNATURES):
            score += 6
        scored.append((score, full))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:limit]]


def detect(session, company: dict, max_fetches: int = 10) -> dict:
    """Find where a company posts jobs. Returns a dict with an 'ats' key."""
    start = company.get("careers") or company.get("website")
    if not start:
        return {"ats": "none", "error": "no website or careers url"}
    if not start.startswith("http"):
        start = "https://" + start
    queue, visited, fetches = [(start, 0)], set(), 0
    host = urlparse(start).netloc
    if not company.get("careers") and not host.startswith("www."):
        queue.append((start.replace("://" + host, "://www." + host, 1), 0))  # bare domains sometimes don't resolve
    best_page = None
    last_err = ""
    while queue and fetches < max_fetches:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        # A link straight to an ATS can be matched without fetching it.
        hit = match_ats(url, "")
        if hit and hit["ats"] not in ("phenom", "jibe", "sf_rmk"):
            hit["careers_url"] = url
            return hit
        try:
            final, html = get_text(session, url)
            fetches += 1
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            fetches += 1
            continue
        if depth == 0 and len(queue) and queue[0][1] == 0:
            queue.pop(0)  # bare domain worked; skip the www. variant
        hit = match_ats(final, html)
        if hit:
            hit["careers_url"] = final
            return hit
        if depth > 0 or company.get("careers"):
            best_page = best_page or final
        if depth < 2:
            for link in _career_links(final, html):
                if link not in visited:
                    queue.append((link, depth + 1))
    if best_page:
        return {"ats": "page", "vendor": "own-site", "url": best_page, "careers_url": best_page}
    return {"ats": "none", "error": last_err or "no careers page found"}


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

def _date(s) -> str:
    if not s:
        return ""
    if isinstance(s, (int, float)):
        import datetime as dt

        ts = s / 1000 if s > 1e11 else s
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else ""


def fetch_greenhouse(s, name, p):
    d = get_json(s, f"https://boards-api.greenhouse.io/v1/boards/{p['slug']}/jobs")
    return [
        Job(name, j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"], "greenhouse",
            _date(j.get("first_published") or j.get("updated_at")))
        for j in d.get("jobs", [])
    ]


def fetch_lever(s, name, p):
    d = get_json(s, f"https://api.lever.co/v0/postings/{p['slug']}?mode=json")
    out = []
    for j in d:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        out.append(Job(name, j["text"], "; ".join(x for x in locs if x), j["hostedUrl"], "lever",
                       _date(j.get("createdAt")), remote=(j.get("workplaceType") == "remote")))
    return out


def fetch_ashby(s, name, p):
    d = get_json(s, f"https://api.ashbyhq.com/posting-api/job-board/{p['slug']}")
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        out.append(Job(name, j["title"], "; ".join(x for x in locs if x), j["jobUrl"], "ashby",
                       _date(j.get("publishedAt")), remote=bool(j.get("isRemote"))))
    return out


def fetch_smartrecruiters(s, name, p):
    out = []
    for q in INTERN_SEARCH:
        d = get_json(s, f"https://api.smartrecruiters.com/v1/companies/{p['slug']}/postings?q={quote(q)}&limit=100")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            txt = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            out.append(Job(name, j["name"], txt, f"https://jobs.smartrecruiters.com/{p['slug']}/{j['id']}",
                           "smartrecruiters", _date(j.get("releasedDate")), remote=bool(loc.get("remote"))))
    return out


def fetch_workday(s, name, p):
    host, tenant, site = p["host"], p["tenant"], p["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    out, seen = [], set()
    for q in INTERN_SEARCH:
        offset = 0
        while offset < 200:  # results are relevance-sorted; interns come first
            d = post_json(s, f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
            posts = d.get("jobPostings") or []
            hits = 0
            for j in posts:
                path = j.get("externalPath", "")
                if path in seen or not INTERN_RE.search(j.get("title", "")):
                    continue
                seen.add(path)
                hits += 1
                loc = j.get("locationsText", "")
                if re.match(r"^\d+ Locations?$", loc):
                    try:
                        det = get_json(s, api + path).get("jobPostingInfo", {})
                        loc = "; ".join([det.get("location", "")] + (det.get("additionalLocations") or []))
                    except Exception:  # noqa: BLE001
                        pass
                out.append(Job(name, j["title"], loc, f"https://{host}/{site}{path}", "workday",
                               remote=bool(re.search(r"remote|virtual", loc, re.I))))
            if len(posts) < 20 or hits == 0:
                break
            offset += 20
    return out


def fetch_phenom(s, name, p):
    """Phenom career sites. The page HTML ignores ?keywords= without a browser session,
    so use the site's own search API (POST /widgets, ddoKey=refineSearch)."""
    u = urlparse(p["base"])
    origin = f"{u.scheme}://{u.netloc}"
    loc = re.search(r"/([a-z]{2,6})/([a-z]{2})$", u.path)
    country, lang = (loc.group(1), loc.group(2)) if loc else ("us", "en")
    api = origin + (u.path[: loc.start()] if loc else u.path.rstrip("/")) + "/widgets"
    out, seen = [], set()
    for q in INTERN_SEARCH:
        frm, dry = 0, 0
        while frm < 400:
            body = {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country,
                    "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
                    "from": frm, "jobs": True, "counts": True, "all_fields": ["category", "country", "state", "city", "type"],
                    "size": 50, "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                    "pageId": "page20", "siteType": "external", "keywords": q, "global": True,
                    "selected_fields": {}, "locationData": {}}
            d = post_json(s, api, body, headers={"Referer": p["base"] + "/search-results", "Origin": origin})
            r = d.get("refineSearch") or {}
            jobs = (r.get("data") or {}).get("jobs") or []
            hit = False
            for j in jobs:
                if INTERN_RE.search(j.get("title", "")):
                    hit = True
                jid = str(j.get("jobId") or j.get("reqId"))
                if jid in seen:
                    continue
                seen.add(jid)
                locs = j.get("multi_location") or [j.get("cityStateCountry") or j.get("location", "")]
                out.append(Job(name, j.get("title", ""), "; ".join(locs), f"{p['base']}/job/{jid}", "phenom",
                               _date(j.get("postedDate")), remote=bool(re.search(r"remote", json.dumps(locs), re.I))))
            frm += len(jobs)
            dry = 0 if hit else dry + 1
            if len(jobs) < 50 or frm >= (r.get("totalHits") or 0) or dry >= 2:
                break
    return out


def fetch_jibe(s, name, p):
    out, seen = [], set()
    for q in INTERN_SEARCH:
        page = 1
        while page <= 10:
            d = get_json(s, f"{p['origin']}/api/jobs?keywords={quote(q)}&page={page}&limit=50")
            jobs = d.get("jobs") or []
            for w in jobs:
                j = w.get("data", w)
                jid = str(j.get("req_id") or j.get("slug"))
                if jid in seen:
                    continue
                seen.add(jid)
                loc = j.get("full_location") or ", ".join(x for x in [j.get("city"), j.get("state")] if x)
                out.append(Job(name, j.get("title", ""), loc, f"{p['origin']}/jobs/{j.get('slug', jid)}", "jibe",
                               _date(j.get("posted_date")), remote=bool(re.search(r"remote", loc or "", re.I))))
            if len(jobs) < 50:
                break
            page += 1
    return out


def fetch_ukg(s, name, p):
    board = p["board"].rstrip("/")
    try:
        s.get(board + "/", timeout=20)  # sets the session cookies the board expects
    except Exception:  # noqa: BLE001
        pass
    out, skip = [], 0
    while skip < 1000:
        body = {
            "opportunitySearch": {
                "Top": 100, "Skip": skip, "QueryString": "",
                "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                "Filters": [{"t": "TermsSearchFilterDto", "fieldName": n, "extra": None, "values": []} for n in (4, 5, 6)],
            },
            "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                              "hasNoLicenses": False, "SkippedSkills": []},
        }
        d = post_json(s, f"{board}/JobBoardView/LoadSearchResults", body)
        opps = d.get("opportunities") or []
        for o in opps:
            locs = []
            for L in o.get("Locations") or []:
                a = L.get("Address") or {}
                st = (a.get("State") or {}).get("Code") or ""
                locs.append(", ".join(x for x in [a.get("City"), st] if x) or L.get("LocalizedName") or "")
            out.append(Job(name, o.get("Title", ""), "; ".join(locs),
                           f"{board}/OpportunityDetail?opportunityId={o['Id']}", "ukg", _date(o.get("PostedDate")),
                           remote=str(o.get("JobLocationType")) in ("2", "Remote")))
        skip += len(opps)
        if not opps or skip >= (d.get("totalCount") or 0):
            break
    return out


def fetch_adp(s, name, p):
    cid = p["cid"]
    base = ("https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/"
            f"job-requisitions?cid={cid}&lang=en_US&locale=en_US")
    out, skip = [], 0
    while skip < 1000:
        d = get_json(s, f"{base}&$top=100&$skip={skip}")
        reqs = d.get("jobRequisitions") or []
        for j in reqs:
            locs = []
            for L in j.get("requisitionLocations") or []:
                a = L.get("address") or {}
                st = (a.get("countrySubdivisionLevel1") or {}).get("codeValue", "")
                locs.append(", ".join(x for x in [a.get("cityName"), st] if x) or (L.get("nameCode") or {}).get("shortName", ""))
            url = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
                   f"?cid={cid}&jobId={j.get('itemID')}&lang=en_US")
            out.append(Job(name, j.get("requisitionTitle", ""), "; ".join(locs), url, "adp", _date(j.get("postDate"))))
        skip += len(reqs)
        total = (d.get("meta") or {}).get("totalNumber") or 0
        if not reqs or skip >= total:
            break
    return out


def fetch_paylocity(s, name, p):
    _, html = get_text(s, f"https://recruiting.paylocity.com/recruiting/jobs/All/{p['guid']}")
    d = extract_js_object(html, "window.pageData") or {}
    out = []
    for j in d.get("Jobs") or []:
        L = j.get("JobLocation") or {}
        loc = j.get("LocationName") or ", ".join(x for x in [L.get("City"), L.get("State")] if x) or L.get("Metro") or ""
        out.append(Job(name, j.get("JobTitle", ""), loc,
                       f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{j['JobId']}", "paylocity",
                       _date(j.get("PublishedDate")), remote=bool(j.get("IsRemote"))))
    return out


def fetch_icims(s, name, p):
    host = p["host"]
    out, seen = [], set()
    for q in INTERN_SEARCH:
        for pr in range(0, 5):
            final, html = get_text(s, f"https://{host}/jobs/search?ss=1&searchKeyword={quote(q)}&in_iframe=1&pr={pr}")
            if "icims.com" not in urlparse(final).netloc:
                # The iCIMS portal redirects to a branded site (often Jibe) — use that instead.
                hit = match_ats(final, html)
                if hit and hit["ats"] == "jibe":
                    return fetch_jibe(s, name, hit)
                break
            soup = BeautifulSoup(html, "html.parser")
            found = 0
            for a in soup.select("a[href*='/jobs/']"):
                m = re.search(r"/jobs/(\d+)/[^/]+/job", a["href"])
                if not m or m.group(1) in seen:
                    continue
                title = a.get("title") or a.get_text(" ", strip=True)
                title = re.sub(r"^\d+\s*-\s*", "", title).strip()
                if not title:
                    continue
                seen.add(m.group(1))
                found += 1
                row = a.find_parent(class_=re.compile("row|iCIMS_JobsTable", re.I))
                loc = ""
                if row:
                    lt = row.find(string=re.compile(r"Location", re.I))
                    if lt and lt.find_next():
                        loc = lt.find_next().get_text(" ", strip=True)[:120]
                    if not loc:
                        mm = re.search(r"\b(US|USA)-([A-Z]{2})-([A-Za-z .'-]+)", row.get_text(" ", strip=True))
                        if mm:
                            loc = f"{mm.group(3).strip()}, {mm.group(2)}"
                out.append(Job(name, title, loc, urljoin(final, a["href"].split("?")[0]), "icims"))
            if found == 0:
                break
    return out


def fetch_bamboohr(s, name, p):
    d = get_json(s, f"https://{p['slug']}.bamboohr.com/careers/list")
    out = []
    for j in d.get("result", []):
        L = j.get("location") or {}
        loc = ", ".join(x for x in [L.get("city"), L.get("state")] if x)
        out.append(Job(name, j.get("jobOpeningName", ""), loc, f"https://{p['slug']}.bamboohr.com/careers/{j['id']}",
                       "bamboohr", remote=bool(j.get("isRemote"))))
    return out


def fetch_recruitee(s, name, p):
    d = get_json(s, f"https://{p['slug']}.recruitee.com/api/offers/")
    return [Job(name, j.get("title", ""), j.get("location", ""), j.get("careers_url", ""), "recruitee",
                _date(j.get("published_at")), remote=bool(j.get("remote"))) for j in d.get("offers", [])]


def fetch_breezy(s, name, p):
    d = get_json(s, f"https://{p['slug']}.breezy.hr/json")
    out = []
    for j in d:
        L = j.get("location") or {}
        loc = L.get("name") or ", ".join(x for x in [L.get("city"), (L.get("state") or {}).get("id")] if x)
        out.append(Job(name, j.get("name", ""), loc, j.get("url", ""), "breezy", _date(j.get("published_date")),
                       remote=bool(L.get("is_remote"))))
    return out


def fetch_workable(s, name, p):
    out, token = [], None
    for _ in range(10):
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        d = post_json(s, f"https://apply.workable.com/api/v3/accounts/{p['slug']}/jobs", body)
        for j in d.get("results", []):
            L = j.get("location") or {}
            loc = ", ".join(x for x in [L.get("city"), L.get("region")] if x)
            out.append(Job(name, j.get("title", ""), loc, f"https://apply.workable.com/{p['slug']}/j/{j['shortcode']}/",
                           "workable", _date(j.get("published")), remote=bool(j.get("remote"))))
        token = d.get("nextPage")
        if not token:
            break
    return out


def fetch_pinpoint(s, name, p):
    slug = p["slug"]
    d = get_json(s, f"https://{slug}.pinpointhq.com/postings.json")
    out = []
    for j in d.get("data", []):
        L = j.get("location") or {}
        loc = L.get("name") or ", ".join(x for x in [L.get("city"), L.get("province")] if x)
        out.append(Job(name, j.get("title", ""), loc, j.get("url") or f"https://{slug}.pinpointhq.com/en/postings/{j.get('id')}",
                       "pinpoint", remote=str(j.get("workplace_type", "")).lower() == "remote"))
    return out


def fetch_oracle(s, name, p):
    host, site = p["host"], p["site"]
    out = []
    seen = set()
    for q in INTERN_SEARCH:
        for offset in range(0, 400, 100):
            url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList&finder=findReqs;siteNumber={site},keyword={quote(q)},limit=100,offset={offset}")
            d = get_json(s, url)
            reqs = [j for item in d.get("items", []) for j in item.get("requisitionList") or []]
            for j in reqs:
                if j["Id"] in seen:
                    continue
                seen.add(j["Id"])
                out.append(Job(name, j.get("Title", ""), j.get("PrimaryLocation", ""),
                               f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}", "oracle",
                               _date(j.get("PostedDate")), remote=bool(re.search("remote", j.get("WorkplaceType") or "", re.I))))
            if len(reqs) < 100 or not any(INTERN_RE.search(j.get("Title", "")) for j in reqs):
                break
    return out


def _rmk_parse(s, name, origin):
    out, seen = [], set()
    for q in INTERN_SEARCH:
        _, html = get_text(s, f"{origin}/search/?q={quote(q)}&sortColumn=referencedate&sortDirection=desc")
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a.jobTitle-link"):
            href = urljoin(origin, a["href"])
            if href in seen:
                continue
            seen.add(href)
            row = a.find_parent("tr") or a.find_parent("li") or a.parent
            loc_el = row.select_one(".jobLocation") if row else None
            loc = loc_el.get_text(" ", strip=True) if loc_el else ""
            if not loc:
                # Some layouts omit it; the job URL slug carries it: /job/Elkhart-Controller-IN-46514/123/
                m = re.search(r"/job/([^/]+)/\d+", href)
                if m:
                    loc = re.sub(r"[-_]+", " ", unescape(m.group(1)))
            out.append(Job(name, a.get_text(" ", strip=True), loc, href, "successfactors"))
    return out


def fetch_sf_rmk(s, name, p):
    return _rmk_parse(s, name, p["origin"])


def fetch_successfactors(s, name, p):
    # Plain SuccessFactors career sites are JS-heavy; scan the page instead.
    return fetch_page(s, name, {"url": f"https://{p['host']}/career?company={p['company_id']}"})


def _looks_like_article(t: str) -> bool:
    """Blog posts and stories that mention interns, e.g. "Career Takeoff: Kaylee's Journey From Intern To Director"."""
    return bool(re.search(r":\s|\b(journey|story|stories|spotlight|meet|how|why|what|tips|blog|news|"
                          r"testimonial|alumni|from intern|to director|day in the life)\b", t, re.I)) \
        or len(t.split()) > 10


def fetch_page(s, name, p):
    """Generic fallback: read a careers page and pull anything that looks like an internship.

    Order: schema.org JobPosting JSON-LD, then links whose text mentions intern/co-op,
    then plain text lines (linked to the careers page itself).
    """
    final, html = get_text(s, p["url"])
    out: list[Job] = []
    soup = BeautifulSoup(html, "html.parser")
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                loc = it.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                ltxt = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x)
                out.append(Job(name, unescape(it.get("title", "")), ltxt, it.get("url") or final, "page",
                               _date(it.get("datePosted")), remote=it.get("jobLocationType") == "TELECOMMUTE"))
    seen_titles = {j.title.lower() for j in out}
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        if 4 <= len(t) <= 120 and INTERN_RE.search(t) and t.lower() not in seen_titles:
            if re.search(r"^(view|see|learn|apply|explore)\b|programs?$|^internships?$", t, re.I) or _looks_like_article(t):
                continue
            seen_titles.add(t.lower())
            out.append(Job(name, t, "", urljoin(final, a["href"]), "page"))
    if not out:
        for line in soup.get_text("\n", strip=True).split("\n"):
            line = line.strip()
            if 6 <= len(line) <= 90 and INTERN_RE.search(line) and line.lower() not in seen_titles:
                if re.search(r"[.!?]$|^(our|we|the|learn|explore|view|apply)\b", line, re.I) or _looks_like_article(line):
                    continue
                seen_titles.add(line.lower())
                out.append(Job(name, line, "", final, "page"))
            if len(out) >= 15:
                break
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "phenom": fetch_phenom,
    "jibe": fetch_jibe,
    "ukg": fetch_ukg,
    "adp": fetch_adp,
    "paylocity": fetch_paylocity,
    "icims": fetch_icims,
    "bamboohr": fetch_bamboohr,
    "recruitee": fetch_recruitee,
    "breezy": fetch_breezy,
    "workable": fetch_workable,
    "oracle": fetch_oracle,
    "pinpoint": fetch_pinpoint,
    "sf_rmk": fetch_sf_rmk,
    "successfactors": fetch_successfactors,
    "page": fetch_page,
}


def fetch(session, company_name: str, params: dict) -> list[Job]:
    fn = FETCHERS.get(params.get("ats"))
    if not fn:
        return []
    return fn(session, company_name, params)
