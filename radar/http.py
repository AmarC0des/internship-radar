"""Shared HTTP session with retries, timeouts and a polite user agent."""
from __future__ import annotations

import json
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 internship-radar/1.0"
)
TIMEOUT = 25


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def get_text(s: requests.Session, url: str, **kw) -> tuple[str, str]:
    """Return (final_url, text). Raises for HTTP errors."""
    r = s.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)
    r.raise_for_status()
    return r.url, r.text


def get_json(s: requests.Session, url: str, **kw):
    headers = {"Accept": "application/json", **kw.pop("headers", {})}
    r = s.get(url, timeout=TIMEOUT, headers=headers, **kw)
    r.raise_for_status()
    return r.json()


def post_json(s: requests.Session, url: str, payload, **kw):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **kw.pop("headers", {}),
    }
    r = s.post(url, data=json.dumps(payload), timeout=TIMEOUT, headers=headers, **kw)
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


def polite_pause(sec: float = 0.3) -> None:
    time.sleep(sec)
