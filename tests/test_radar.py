"""Offline tests. Response bodies below are trimmed copies of what each live board
returned when the fetchers were written (Nov 2026)."""
import json
import re
from pathlib import Path

import pytest
import yaml

from radar import ats
from radar.filters import Filter
from radar.models import Job

ROOT = Path(__file__).resolve().parent.parent
FLT = Filter(yaml.safe_load((ROOT / "config/filters.yaml").read_text()))


class Resp:
    def __init__(self, body, url):
        self._b, self.url, self.status_code = body, url, 200
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._b if not isinstance(self._b, str) else json.loads(self._b)

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _hit(self, url, data=None):
        self.calls.append((url, data))
        for rx, body in self.routes:
            if re.search(rx, url):
                return Resp(body(url, data) if callable(body) else body, url)
        raise AssertionError(f"unexpected url {url}")

    def get(self, url, **kw):
        return self._hit(url)

    def post(self, url, data=None, **kw):
        return self._hit(url, data)


# ---------------------------------------------------------------- filters

@pytest.mark.parametrize("title,loc,local,expect_region,expect_tag", [
    ("Summer 2027 Software Engineering Intern", "Chicago, IL", False, "Chicago Area", "Software"),
    ("IT Intern", "South Bend, IN", True, "Northern Indiana", "IT"),
    ("Cybersecurity Intern", "US-IN-Warsaw", True, "Northern Indiana", "Cybersecurity"),
    ("IT Audit Intern", "Kalamazoo, Michigan", False, "Southern Michigan", "Audit & Finance"),
    ("Summer 2027 Intern - Industrial Design, User Experience - Michigan", "Portage, Michigan", True, None, None),
    ("Data Analytics Intern", "Remote - United States", False, "Remote", "Data & AI"),
    ("Data Analytics Intern", "Remote, Canada", False, None, None),
    ("Software Intern", "Austin, TX", False, None, None),
    ("Software Intern", "Portage, IN", False, "Northern Indiana", "Software"),
    ("Intern", "Mishawaka, Indiana", True, "Northern Indiana", "General"),
    ("Intern", "Mishawaka, Indiana", False, None, None),
    ("HR Intern", "Goshen, Indiana", True, None, None),
    ("Mechanical Engineering Intern", "Elkhart, IN", True, None, None),
    ("Software Engineering Intern - Manufacturing Systems", "Elkhart, IN", True, "Northern Indiana", "Software"),
    ("Internal Audit Manager", "Chicago, IL", False, None, None),
    ("Nursing Student Intern", "South Bend, IN", True, None, None),
    ("Business Analyst Co-op", "Fort Wayne, IN", False, "Northern Indiana", "Business"),
    ("Quality Intern", "Elkhart, IN", True, None, None),
    ("Software Intern", "Indianapolis, IN; Chicago, IL", False, "Chicago Area", "Software"),
    ("Accounting Intern", "", True, "Location not listed", "Audit & Finance"),
    ("Software Intern", "California, Virtual Address", False, "Remote", "Software"),
    ("Software Intern", "Gary", True, "Northern Indiana", "Software"),
])
def test_filter(title, loc, local, expect_region, expect_tag):
    j = FLT.apply(Job("Acme", title, loc, "u", "x", local_company=local))
    if expect_region is None:
        assert j is None
    else:
        assert j is not None, title
        assert j.region == expect_region
        assert expect_tag in j.categories


# ---------------------------------------------------------------- detection

def test_match_ats_signatures():
    m = ats.match_ats("https://stryker.wd1.myworkdayjobs.com/en-US/StrykerCareers/job/x", "")
    assert m == {"ats": "workday", "host": "stryker.wd1.myworkdayjobs.com", "tenant": "stryker", "site": "StrykerCareers"}
    assert ats.match_ats("x", 'href="https://recruiting.ultipro.com/STS1000SCO/JobBoard/bdcbc22d-c568-4b9f-bc84-421f9522fd47/?q="')["ats"] == "ukg"
    assert ats.match_ats("x", '<recruitment-current-openings cid="4c0f5e63-d8a0-4686-b48c-977660d2dabc" ccid="1">')["cid"] == "4c0f5e63-d8a0-4686-b48c-977660d2dabc"
    assert ats.match_ats("x", "https://recruiting.paylocity.com/recruiting/jobs/All/a9f9a8d7-7178-4d80-9666-05cb559c5f6f/MORryde")["guid"] == "a9f9a8d7-7178-4d80-9666-05cb559c5f6f"
    assert ats.match_ats("x", '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">')["slug"] == "acme"
    assert ats.match_ats("x", "https://jobs.lever.co/palantir/abc")["slug"] == "palantir"
    assert ats.match_ats("https://careers.zimmerbiomet.com/us/en/search-results", "phApp.ddo = {}")["base"] == "https://careers.zimmerbiomet.com/us/en"
    assert ats.match_ats("https://careers.lippert.com/careers-home", "")["ats"] == "jibe"
    assert ats.match_ats("x", "https://careers-lci1.icims.com/jobs/1/login")["host"] == "careers-lci1.icims.com"
    assert ats.match_ats("x", "nothing here") is None


def test_detect_follows_careers_link():
    home = '<a href="/about">About</a><a href="/joinourteam">Join our team</a>'
    careers = '<a href="https://recruiting.paylocity.com/recruiting/jobs/All/a9f9a8d7-7178-4d80-9666-05cb559c5f6f/X">Openings</a>'
    s = FakeSession([(r"acme\.com/joinourteam", careers), (r"acme\.com", home)])
    b = ats.detect(s, {"name": "Acme", "website": "acme.com"})
    assert b["ats"] == "paylocity"


def test_detect_falls_back_to_page():
    s = FakeSession([(r"acme\.com/careers", "<h1>Careers</h1><p>Email us</p>"),
                     (r"acme\.com", '<a href="/careers">Careers</a>')])
    b = ats.detect(s, {"name": "Acme", "website": "acme.com"})
    assert b["ats"] == "page" and b["url"].endswith("/careers")


# ---------------------------------------------------------------- fetchers

def test_workday():
    page = {"total": 3, "jobPostings": [
        {"title": "Summer 2027 Internship - IT - Michigan", "externalPath": "/job/Portage-Michigan/IT_R1", "locationsText": "Portage, Michigan"},
        {"title": "Area Sales Manager", "externalPath": "/job/x/R2", "locationsText": "Gurugram"},
        {"title": "Data Intern", "externalPath": "/job/multi/R3", "locationsText": "2 Locations"},
    ]}
    det = {"jobPostingInfo": {"location": "Michigan, Kalamazoo", "additionalLocations": ["Indiana, Warsaw"]}}
    s = FakeSession([(r"/jobs$", page), (r"/job/multi/R3$", det)])
    jobs = ats.fetch_workday(s, "Stryker", {"host": "stryker.wd1.myworkdayjobs.com", "tenant": "stryker", "site": "StrykerCareers"})
    titles = {j.title: j for j in jobs}
    assert set(titles) == {"Summer 2027 Internship - IT - Michigan", "Data Intern"}
    assert titles["Data Intern"].location == "Michigan, Kalamazoo; Indiana, Warsaw"
    assert titles["Data Intern"].url == "https://stryker.wd1.myworkdayjobs.com/StrykerCareers/job/multi/R3"


def test_phenom():
    resp = {"refineSearch": {"totalHits": 2, "data": {"jobs": [
        {"jobId": "12675", "title": "2027 Spring Intern, Connected Health", "cityStateCountry": "Grand Rapids, Michigan, United States", "postedDate": "2026-11-10T00:00:00.000+0000"},
        {"jobId": "13093", "title": "Warehouse Intern", "multi_location": ["Warsaw, Indiana, United States"]}]}}}
    s = FakeSession([(r"/widgets$", resp)])
    jobs = ats.fetch_phenom(s, "Zimmer Biomet", {"base": "https://careers.zimmerbiomet.com/us/en"})
    assert [j.url for j in jobs] == ["https://careers.zimmerbiomet.com/us/en/job/12675", "https://careers.zimmerbiomet.com/us/en/job/13093"]
    assert jobs[0].posted == "2026-11-10"
    url, data = s.calls[0]
    body = json.loads(data)
    assert url == "https://careers.zimmerbiomet.com/widgets" and body["keywords"] == "intern" and body["lang"] == "en_us"
    # sites without a locale path (careers.crowe.com) and nested ones (qualtrics.com/careers/us/en)
    s2 = FakeSession([(r"/widgets$", resp)])
    ats.fetch_phenom(s2, "Crowe", {"base": "https://careers.crowe.com"})
    assert s2.calls[0][0] == "https://careers.crowe.com/widgets"
    assert ats.match_ats("https://www.qualtrics.com/careers/us/en", "phApp.ddo={}")["base"] == "https://www.qualtrics.com/careers/us/en"
    s3 = FakeSession([(r"/widgets$", resp)])
    ats.fetch_phenom(s3, "Q", {"base": "https://www.qualtrics.com/careers/us/en"})
    assert s3.calls[0][0] == "https://www.qualtrics.com/careers/widgets"


def test_bad_detections_from_first_run():
    # Aon: an iCIMS cookie script is not a job board
    assert ats.match_ats("x", '<script src="https://cookie-policy-scripts.icims.com/x.js">') is None
    # Notre Dame: a "my applications" link must resolve to the real SmartRecruiters company
    m = ats.match_ats("x", '<a href="https://jobs.smartrecruiters.com/my-applications/UniversityOfNotreDame?x=1">')
    assert m == {"ats": "smartrecruiters", "slug": "UniversityOfNotreDame"}
    # Honeywell: Oracle Recruiting on its own domain
    m = ats.match_ats("https://careers.honeywell.com/en/sites/Honeywell",
                      '<script src="https://ibqbjb.fa.ocs.oraclecloud.com:443/hcmUI/CandExpStatic/js/x.js">')
    assert m == {"ats": "oracle", "host": "ibqbjb.fa.ocs.oraclecloud.com", "site": "Honeywell"}
    # Forest River: ApplicantStack board gets a page scan
    assert ats.match_ats("x", 'href="http://forestriverinc.applicantstack.com/x/openings"')["ats"] == "page"


def test_detect_tries_www_when_bare_domain_fails():
    class S(FakeSession):
        def get(self, url, **kw):
            if url.startswith("https://acme.com"):
                raise ConnectionError("dns")
            return super().get(url, **kw)
    s = S([(r"www\.acme\.com/careers", 'href="https://acme.wd5.myworkdayjobs.com/Acme"'),
           (r"www\.acme\.com", '<a href="/careers">Careers</a>')])
    assert ats.detect(s, {"name": "Acme", "website": "acme.com"})["ats"] == "workday"


def test_oracle_paging():
    page = lambda url, d: {"items": [{"requisitionList": [{"Id": str(i), "Title": f"IT Intern {i}", "PrimaryLocation": "Fort Wayne, IN, United States"}
                                                          for i in (range(100) if "offset=0" in url else range(100, 130))]}]}
    s = FakeSession([(r"recruitingCEJobRequisitions", page)])
    jobs = ats.fetch_oracle(s, "Parkview", {"host": "x.fa.oraclecloud.com", "site": "CX_1"})
    assert len(jobs) == 130


def test_jibe():
    d = {"totalCount": 1, "jobs": [{"data": {"title": "Intern", "slug": "17973", "req_id": "17973", "full_location": "Mishawaka, Indiana", "posted_date": "2026-10-06T18:11:00+0000"}}]}
    s = FakeSession([(r"/api/jobs", d)])
    jobs = ats.fetch_jibe(s, "Lippert", {"origin": "https://careers.lippert.com"})
    assert len(jobs) == 1 and jobs[0].url == "https://careers.lippert.com/jobs/17973" and jobs[0].location == "Mishawaka, Indiana"


def test_ukg():
    d = {"totalCount": 1, "opportunities": [{"Id": "d947", "Title": "IT Intern", "PostedDate": "2026-11-20T22:08:04Z",
          "Locations": [{"Address": {"City": "South Bend", "State": {"Code": "IN"}}}]}]}
    board = "https://recruiting.ultipro.com/STS1000SCO/JobBoard/bdcbc22d-c568-4b9f-bc84-421f9522fd47"
    s = FakeSession([(r"LoadSearchResults", d), (r"JobBoard/[^/]+/$", "<html>")])
    jobs = ats.fetch_ukg(s, "1st Source", {"board": board})
    assert jobs[0].location == "South Bend, IN" and jobs[0].url.endswith("OpportunityDetail?opportunityId=d947")


def test_adp():
    d = {"meta": {"totalNumber": 1}, "jobRequisitions": [{"itemID": "9205", "requisitionTitle": "IT Intern",
          "postDate": "2026-11-20T19:16:00", "requisitionLocations": [{"address": {"cityName": "Warsaw", "countrySubdivisionLevel1": {"codeValue": "IN"}}}]}]}
    s = FakeSession([(r"job-requisitions", d)])
    jobs = ats.fetch_adp(s, "Lake City Bank", {"cid": "abc"})
    assert jobs[0].location == "Warsaw, IN" and "jobId=9205" in jobs[0].url


def test_paylocity():
    data = {"Jobs": [{"JobId": 4682426, "JobTitle": "IT Intern", "LocationName": "Elkhart, IN", "IsRemote": False,
                      "PublishedDate": "2026-11-20T12:23:39-06:00", "JobLocation": {"Metro": "Elkhart, IN"}}]}
    html = "<script>\r\n window.pageData = " + json.dumps(data) + ";\r\n  </script>"
    s = FakeSession([(r"paylocity", html)])
    jobs = ats.fetch_paylocity(s, "MORryde", {"guid": "g"})
    assert jobs[0].url == "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4682426" and jobs[0].posted == "2026-11-20"


def test_greenhouse_lever_ashby():
    gh = {"jobs": [{"title": "SWE Intern", "location": {"name": "Chicago, IL"}, "absolute_url": "https://x/1", "first_published": "2026-09-01T00:00:00-04:00"}]}
    lv = [{"text": "Data Intern", "categories": {"location": "Chicago, IL", "allLocations": ["Chicago, IL", "Remote"]}, "hostedUrl": "https://jobs.lever.co/a/1", "createdAt": 1786469891368, "workplaceType": "hybrid"}]
    ab = {"jobs": [{"title": "IT Intern", "location": "Kalamazoo, MI", "secondaryLocations": [{"location": "Remote (US)"}], "jobUrl": "https://jobs.ashbyhq.com/a/1", "isListed": True, "isRemote": False, "publishedAt": "2026-04-07T17:12:35Z"}]}
    s = FakeSession([(r"greenhouse", gh), (r"lever", lv), (r"ashby", ab)])
    assert ats.fetch_greenhouse(s, "A", {"slug": "a"})[0].posted == "2026-09-01"
    assert ats.fetch_lever(s, "A", {"slug": "a"})[0].location == "Chicago, IL; Remote"
    assert ats.fetch_ashby(s, "A", {"slug": "a"})[0].location == "Kalamazoo, MI; Remote (US)"


def test_page_scan():
    html = """<script type="application/ld+json">{"@type":"JobPosting","title":"IT Intern","url":"https://a/1",
    "jobLocation":{"address":{"addressLocality":"Goshen","addressRegion":"IN"}}}</script>
    <a href="/jobs/2">Data Analyst Intern</a><a href="/internships">Internship Program</a><a href="/x">View internships</a>"""
    s = FakeSession([(r"acme", html)])
    jobs = ats.fetch_page(s, "Acme", {"url": "https://acme.com/careers"})
    assert [j.title for j in jobs] == ["IT Intern", "Data Analyst Intern"]
    assert jobs[0].location == "Goshen, IN" and jobs[1].url == "https://acme.com/jobs/2"


def test_companies_yaml_is_valid():
    data = yaml.safe_load((ROOT / "config/companies.yaml").read_text())
    names = [c["name"].lower() for g in data.values() for c in g]
    assert len(names) == len(set(names)), "duplicate company names"
    for g in data.values():
        for c in g:
            assert c.get("website") or c.get("careers"), c
            if c.get("ats"):
                assert c["ats"] in ats.FETCHERS, c


def test_phenom_without_locale():
    assert ats.match_ats("https://careers.crowe.com/", "phApp.ddo = {}")["base"] == "https://careers.crowe.com"


def test_link_ranking_real_crowe_homepage():
    # Anchors copied from crowe.com's homepage (Nov 2026).
    anchors = [("https://www.crowe.com/services/tax/international-tax", "International tax"),
               ("https://www.crowe.com/insights/is-your-crm-system-working-for-you", "Is Your CRM System Working for You?"),
               ("https://www.crowe.com/careers/students", "Students"),
               ("https://www.crowe.com/careers/why-crowe/benefits", "Benefits"),
               ("https://www.crowe.com/careers/experienced-professionals", "Experienced professionals"),
               ("https://www.crowe.com/careers/why-crowe", "Why Crowe"),
               ("https://www.crowe.com/careers/what-we-do", "What we do"),
               ("https://careers.crowe.com/", "Search careers"),
               ("https://www.crowe.com/careers", "Learn about careers at Crowe"),
               ("https://www.instagram.com/crowecareers/", "")]
    html = "".join(f'<a href="{h}">{t}</a>' for h, t in anchors)
    links = ats._career_links("https://www.crowe.com/", html)
    assert links[0] == "https://careers.crowe.com/"
    assert not any("international-tax" in l or "instagram" in l or "crm-system" in l for l in links)


def test_base_href_respected():
    # lincolnfinancial.com uses <base href="/"> with relative links like "public/aboutus/careers".
    html = '<base href="/"><a href="public/aboutus/careers">Careers</a>'
    assert ats._career_links("https://www.lincolnfinancial.com/public/individuals", html) == [
        "https://www.lincolnfinancial.com/public/aboutus/careers"]


def test_sf_rmk_detect_and_parse():
    html = ('<a href="/search/?q=">Search</a><table><tr class="data-row"><td><a class="jobTitle-link" '
            'href="/job/Fort-Wayne-IT-Intern-IN/1429/">IT Intern</a></td><td><span class="jobLocation">Fort Wayne, IN, US</span></td></tr>'
            '<tr class="data-row"><td><a class="jobTitle-link" href="/job/Fort-Wayne-IT-Intern-IN/1429/">IT Intern</a></td></tr></table>')
    m = ats.match_ats("https://jobs.lincolnfinancial.com/go/All-Jobs/8874000/", html)
    assert m == {"ats": "sf_rmk", "origin": "https://jobs.lincolnfinancial.com"}
    s = FakeSession([(r"/search/", html)])
    jobs = ats.fetch_sf_rmk(s, "Lincoln Financial", m)
    assert len(jobs) == 1 and jobs[0].location == "Fort Wayne, IN, US"
    assert jobs[0].url == "https://jobs.lincolnfinancial.com/job/Fort-Wayne-IT-Intern-IN/1429/"


def test_sf_rmk_j2w_and_slug_location():
    assert ats.match_ats("https://careers.patrickind.com/", '<script src="/platform/js/j2w/min/x.js">')["ats"] == "sf_rmk"
    html = '<a class="jobTitle-link" href="/job/Elkhart-IT-Intern-IN-46514/1150/">IT Intern</a>'
    jobs = ats.fetch_sf_rmk(FakeSession([(r"/search/", html)]), "Patrick", {"origin": "https://careers.patrickind.com"})
    assert FLT.apply(jobs[0]).region == "Northern Indiana"


def test_polite_session_backs_off_on_429(monkeypatch):
    import requests
    from radar import http
    calls = []

    class R:
        def __init__(self, code, ra=""):
            self.status_code, self.headers = code, {"Retry-After": ra} if ra else {}

    seq = [R(429, "1"), R(429), R(200)]
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: (calls.append(u), seq.pop(0))[1])
    monkeypatch.setattr(http.time, "sleep", lambda s: calls.append(("sleep", round(s))))
    monkeypatch.setattr(http.random, "uniform", lambda a, b: 0)
    r = http.make_session().get("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x/jobs")
    assert r.status_code == 200
    sleeps = [c[1] for c in calls if isinstance(c, tuple) and c[1] >= 1]
    assert sleeps[0] == 1 and 5 in sleeps  # honoured Retry-After, then default backoff


def test_stale_cache_is_redetected(monkeypatch):
    import datetime as dt
    from radar import main
    monkeypatch.setattr(main.ats, "detect", lambda s, c: {"ats": "greenhouse", "slug": "new"})
    today = dt.date(2026, 11, 21)
    old = {"Acme": {"ats": "icims", "host": "cookie-policy-scripts.icims.com", "detected": "2026-11-20"}}
    b = main.board_for({"name": "Acme", "website": "acme.com"}, old, None, today, False)
    assert b["slug"] == "new" and b["v"] == main.ats.DETECT_VERSION
    fresh = {"Acme": {"ats": "lever", "slug": "x", "detected": "2026-11-20", "v": main.ats.DETECT_VERSION}}
    assert main.board_for({"name": "Acme", "website": "acme.com"}, fresh, None, today, False)["ats"] == "lever"


@pytest.mark.parametrize("title,loc,local,ok", [
    # rows that were wrong in the 2nd live run
    ("Discover Internship, Finance, Intercompany", "Warsaw, Poland", True, False),
    ("Intern Bachelors - Engineering Program Management (m/f/d)", "Amsterdam, Noord-Holland, Netherlands", True, False),
    ("Intern, Human Resources, South Bend, IN (Summer 2027)", "South Bend, IN", True, False),
    ("Intern, Legal Department, South Bend, IN", "South Bend, IN", True, False),
    ("2027 Co-op – Mechanical Engineer", "Lansing, Michigan", True, False),
    ("Summer 2027 Internship - RA/QA - Regulatory Affairs - Michigan", "Portage, Michigan", False, False),
    ("Digital Marketing Intern", "", True, False),
    # still kept
    ("Intern", "Mishawaka, Indiana", True, True),
    ("Summer Intern - Elkhart (2027)", "Elkhart, IN", True, True),
    ("IT/OT Security Intern", "Kalamazoo - Kilgore Road", True, True),
    ("Intern, Information Security, South Bend, IN (Summer 2027)", "South Bend, IN", True, True),
    ("Marketing Analyst Intern", "", True, True),
])
def test_second_run_regressions(title, loc, local, ok):
    assert (FLT.apply(Job("Acme", title, loc, "u", "x", local_company=local)) is not None) == ok


def test_multi_site_detection():
    from radar.main import _is_multi_site
    mk = lambda *locs: [Job("A", "t", l, "u", "x") for l in locs]
    assert _is_multi_site(mk("Warsaw, Indiana", "Warsaw, Poland"))
    assert _is_multi_site(mk("Portage, Michigan", "Mahwah, New Jersey"))
    assert not _is_multi_site(mk("South Bend, IN", "Niles, MI", "Elkhart, IN"))
    assert not _is_multi_site(mk("Mishawaka", ""))


def test_page_scan_skips_articles():
    html = ('<a href="/blog/1">Career Takeoff: Kaylee’s Journey From Intern To Director Of Lending</a>'
            '<a href="/jobs/2">Data Engineering Intern</a>')
    jobs = ats.fetch_page(FakeSession([(r"acme", html)]), "Acme", {"url": "https://acme.com/careers"})
    assert [j.title for j in jobs] == ["Data Engineering Intern"]


def test_supply_chain_survives_manufacturing_word():
    assert FLT.apply(Job("GM", "2027 Summer Intern – Manufacturing Supply Chain", "Lansing, Michigan", "u", "x"))
    assert not FLT.apply(Job("GM", "Manufacturing Engineering Intern", "Lansing, Michigan", "u", "x"))
