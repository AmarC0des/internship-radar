"""Decide whether a Job is an internship we care about, in a place we care about."""
from __future__ import annotations

import re
from functools import lru_cache

from .models import Job

STRONG = {"Software", "IT", "Cybersecurity", "Data & AI", "Audit & Finance"}
BARE_INTERN_RE = re.compile(
    r"^\s*(\d{4}\s+)?((summer|fall|spring|winter|year[- ]round|paid|part[- ]time|full[- ]time)\s+)*"
    r"(intern|internship|co-?op|student intern)(\s*[-–(,].*)?$",
    re.I,
)


_CODES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
          "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_NAMES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
          "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
          "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
          "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
          "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
          "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
          "West Virginia", "Wisconsin", "Wyoming"]


@lru_cache(maxsize=4096)
def _rx(term: str) -> re.Pattern:
    t = re.escape(term.lower()).replace(r"\ ", r"[\s/-]+")
    return re.compile(rf"(?<![a-z0-9]){t}(?![a-z0-9])", re.I)


def _any(terms, text: str) -> bool:
    return any(_rx(t).search(text) for t in terms)


def _state_rx(states) -> re.Pattern:
    codes = [s for s in states if len(s) == 2]
    names = [s for s in states if len(s) > 2]
    parts = []
    if codes:  # two-letter codes are matched case-sensitively so "in" never counts
        parts.append(r"(?<![A-Za-z])(?:" + "|".join(codes) + r")(?![A-Za-z])")
    if names:
        parts.append(r"(?i:\b(?:" + "|".join(map(re.escape, names)) + r")\b)")
    return re.compile("|".join(parts))


US_STATE_RX = _state_rx(_CODES + _NAMES)


class Filter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.intern_terms = cfg["internship_terms"]
        self.exclude = cfg.get("exclude_terms", [])
        self.categories = cfg["categories"]
        self.non_cs = cfg.get("non_cs_engineering", [])
        self.include_remote = cfg.get("include_remote", True)
        self.non_us = cfg.get("non_us_markers", [])
        self.regions = []
        for name, r in cfg["regions"].items():
            self.regions.append((name, _state_rx(r["states"]), [_rx(c) for c in r["cities"]],
                                 [_rx(p) for p in r.get("phrases", [])]))

    # ---- what ----
    def is_internship(self, title: str) -> bool:
        return _any(self.intern_terms, title) and not _any(self.exclude, title)

    def tag(self, title: str) -> list[str]:
        tags = [cat for cat, terms in self.categories.items() if _any(terms, title)]
        if _any(self.non_cs, title) and not (set(tags) & STRONG):
            return []
        return tags

    # ---- where ----
    def region_of(self, job: Job) -> str:
        loc = job.location or ""
        segments = [s.strip() for s in re.split(r";|\||\n| / ", loc) if s.strip()] or [""]
        for seg in segments:
            for name, state_rx, cities, phrases in self.regions:
                if any(p.search(seg) for p in phrases):
                    return name
                if any(c.search(seg) for c in cities):
                    # City + matching state, or (for our local companies) a bare city name.
                    if state_rx.search(seg) or (job.local_company and not US_STATE_RX.search(seg)):
                        return name
        if self.include_remote and (job.remote or re.search(r"\b(remote|virtual|work from home|wfh|anywhere)\b", loc, re.I)):
            if not _any(self.non_us, loc):
                return "Remote"
        if job.local_company and not loc.strip():
            return "Location not listed"
        return ""

    # ---- both ----
    def apply(self, job: Job) -> Job | None:
        title = re.sub(r"\s+", " ", job.title or "").strip()
        if not title or not self.is_internship(title):
            return None
        tags = self.tag(title)
        if not tags:
            if job.local_company and BARE_INTERN_RE.match(title):
                tags = ["General"]
            else:
                return None
        region = self.region_of(job)
        if not region:
            return None
        job.title, job.categories, job.region = title, tags, region
        return job
