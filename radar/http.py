"""Shared HTTP session: browser-like headers, per-site throttling, polite 429 handling."""
from __future__ import annotations

import json
import random
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
TIMEOUT = 25

# Shared job-board hosts get one request at a time across all threads, with a gap.
# Workday rate-limits by client IP across *all* tenants, so it's one bucket.
SHARED_BUCKETS = {
    "myworkdayjobs.com": 1.2,
    "oraclecloud.com": 0.6,
    "icims.com": 0.6,
    "ultipro.com": 0.5,
    "paylocity.com": 0.5,
    "adp.com": 0.5,
    "smartrecruiters.com": 0.4,
}
DEFAULT_GAP = 0.4  # per individual host
_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_registry = threading.Lock()


def _bucket(url: str) -> tuple[str, float]:
    host = (urlparse(url).hostname or "").lower()
    for suffix, gap in SHARED_BUCKETS.items():
        if host == suffix or host.endswith("." + suffix):
            return suffix, gap
    return host, DEFAULT_GAP


def _throttle(url: str) -> None:
    key, gap = _bucket(url)
    with _registry:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        wait = _last.get(key, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[key] = time.monotonic()


class PoliteSession(requests.Session):
    """requests.Session that throttles per site and backs off on 429 using Retry-After."""

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        delay = 5.0
        for attempt in range(5):
            _throttle(url)
            r = super().request(method, url, *args, **kwargs)
            if r.status_code != 429 or attempt == 4:
                return r
            ra = r.headers.get("Retry-After", "")
            if ra.isdigit():
                wait = float(ra)
            else:
                wait, delay = delay, delay * 2
            wait = min(wait, 60) + random.uniform(0, 1.5)
            key, _ = _bucket(url)
            with _registry:  # push the whole bucket back so other threads wait too
                _last[key] = time.monotonic() + wait
            time.sleep(wait)
        return r


def make_session() -> requests.Session:
    s = PoliteSession()
    retry = Retry(total=2, connect=2, read=1, backoff_factor=1.0,
                  status_forcelist=(500, 502, 503, 504), allowed_methods=frozenset(["GET", "POST"]),
                  raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(BROWSER_HEADERS)
    return s


def get_text(s: requests.Session, url: str, **kw) -> tuple[str, str]:
    """Return (final_url, text). Raises for HTTP errors."""
    r = s.get(url, allow_redirects=True, **kw)
    r.raise_for_status()
    return r.url, r.text


def _api_headers(extra: dict) -> dict:
    return {"Accept": "application/json, text/plain, */*", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin", **extra}


def get_json(s: requests.Session, url: str, **kw):
    r = s.get(url, headers=_api_headers(kw.pop("headers", {})), **kw)
    r.raise_for_status()
    return r.json()


def post_json(s: requests.Session, url: str, payload, **kw):
    headers = _api_headers({"Content-Type": "application/json", **kw.pop("headers", {})})
    r = s.post(url, data=json.dumps(payload), headers=headers, **kw)
    r.raise_for_status()
    return r.json()


def extract_js_object(text: str, marker: str):
    """Find `marker` (e.g. 'window.pageData =') and decode the JSON object after it."""
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("{", i + len(marker))
    if j < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[j:])
        return obj
    except ValueError:
        return None
