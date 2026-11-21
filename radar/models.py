from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str  # e.g. "workday", "simplify", "adzuna"
    posted: str = ""  # ISO date (YYYY-MM-DD) when known
    remote: bool = False
    local_company: bool = False  # came from our Northern IN / SW MI company list
    # filled in by filters
    categories: list[str] = field(default_factory=list)
    region: str = ""

    @property
    def key(self) -> str:
        """Stable id used to dedupe across sources and across days."""
        base = f"{_norm(self.company)}|{_norm(self.title)}|{_norm(self.location)[:40]}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def loose_key(self) -> str:
        """Company + title only: merges the same posting seen via two sources."""
        return hashlib.sha1(f"{_norm(self.company)}|{_norm(self.title)}".encode()).hexdigest()[:16]
