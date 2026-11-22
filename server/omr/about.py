"""Who makes MCQ-OMR, and the optional check for a newer version.

The update check is OFF unless the person turns it on in Settings. When on, it asks GitHub's public
API for the latest release at most once a day (nothing about the person or their data is sent;
the request is the same for everyone). When off, MCQ-OMR never contacts the internet.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from . import __version__

APP_NAME = "MCQ-OMR"
DEVELOPER = "GGSDev"
WEBSITE = "https://guilherme.ie"
EMAIL = "contact@guilherme.ie"
REPO = "https://github.com/guilhermedanielgomes/mcq-omr"
RELEASES = REPO + "/releases"
LATEST_API = "https://api.github.com/repos/guilhermedanielgomes/mcq-omr/releases/latest"
LICENSE_NAME = "MIT"
COPYRIGHT = "© 2026 Guilherme Daniel Gomes Silva (GGSDev)"
CHECK_EVERY = 24 * 3600            # seconds between two checks
TIMEOUT = 6

_lock = threading.Lock()
_cache: dict = {"checked": 0.0, "latest": None, "url": None, "error": None}


def version() -> str:
    return __version__


def _parse(v: str | None) -> tuple:
    """'v1.10.2' -> (1, 10, 2); anything odd -> ()."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def newer(latest: str | None, current: str | None = None) -> bool:
    a, b = _parse(latest), _parse(current or __version__)
    return bool(a) and bool(b) and a > b


def _fetch() -> tuple[str | None, str | None]:
    req = urllib.request.Request(LATEST_API, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "MCQ-OMR"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:            # noqa: S310 (fixed https URL)
        data = json.loads(r.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    url = str(data.get("html_url") or RELEASES)
    if not url.startswith(REPO + "/"):                                 # only ever link to our own releases
        url = RELEASES
    return (tag or None), url


def check(enabled: bool, force: bool = False) -> dict:
    """{"enabled", "current", "latest", "url", "newer", "error"}. Contacts GitHub only when enabled,
    and at most once per CHECK_EVERY unless force."""
    out = {"enabled": bool(enabled), "current": __version__, "latest": None, "url": RELEASES,
           "newer": False, "error": None}
    if not enabled:
        return out
    with _lock:
        if force or time.time() - _cache["checked"] > CHECK_EVERY:
            try:
                _cache["latest"], _cache["url"] = _fetch()
                _cache["error"] = None
            except Exception as exc:                                    # offline, rate limit, …
                _cache["error"] = type(exc).__name__
            _cache["checked"] = time.time()
        out.update(latest=_cache["latest"], url=_cache["url"] or RELEASES, error=_cache["error"])
    out["newer"] = newer(out["latest"])
    return out
