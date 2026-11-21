"""Interface languages.

The English text in the code is the source; omr/locales/<code>.json maps each English text to its
translation (a text missing from a file shows in English). The language is chosen in Settings
("automatic" = the browser's language) and applies per request.

  Templates:  {{ _("Exam sets") }}   {{ _("%(n)s of %(total)s students", n=x, total=y) }}
              {{ ngettext("%(n)s student", "%(n)s students", n) }}
              Translations may contain HTML (they are written by us, not by users); the values
              passed in are escaped.
  Python:     from omr.i18n import tr, ntr      tr("Saved."), tr("%(n)s saved", n=3)
  JavaScript: t("Loading…"), t("{n} of {total}", {n: 1, total: 2})   (catalog in /i18n.js)

The phone app's /api/... answers stay in English whatever the setting.
"""
from __future__ import annotations

import contextvars
import json
import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup, escape

LANGUAGES = {                     # code -> name shown in the drop-downs (in that language)
    "en": "English",
    "pt_BR": "Português (Brasil)",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "zh_CN": "中文（简体）",
}
HTML_LANG = {"en": "en", "pt_BR": "pt-BR", "es": "es", "fr": "fr", "de": "de", "it": "it", "zh_CN": "zh-CN"}
LOCALE_DIR = Path(__file__).resolve().parent / "locales"

_lang: contextvars.ContextVar[str] = contextvars.ContextVar("mcq_lang", default="en")


@lru_cache(maxsize=None)
def catalog(code: str) -> dict[str, str]:
    if code == "en" or code not in LANGUAGES:
        return {}
    p = LOCALE_DIR / f"{code}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip() and not k.startswith("@")}


def set_language(code: str):
    return _lang.set(code if code in LANGUAGES else "en")


def current() -> str:
    return _lang.get()


def match(accept: str | None) -> str:
    """Best interface language for a browser Accept-Language header (or an OS locale like 'pt_BR.UTF-8')."""
    if not accept:
        return "en"
    for part in accept.replace("-", "_").split(","):
        tag = part.split(";")[0].split(".")[0].strip()
        if not tag:
            continue
        low = tag.lower()
        for code in LANGUAGES:
            if low == code.lower():
                return code
        base = low.split("_")[0]
        if base == "pt":
            return "pt_BR"
        if base == "zh":
            return "zh_CN"
        if base in LANGUAGES:
            return base
    return "en"


def choose(setting: str | None, accept: str | None) -> str:
    """The language for a request: the one set in Settings, or (automatic) the browser's."""
    return setting if setting in LANGUAGES else match(accept)


def _fmt(text: str, values: dict, html: bool):
    if not values:
        return Markup(text) if html else text
    if html:
        return Markup(text) % {k: (v if isinstance(v, Markup) else escape(v)) for k, v in values.items()}
    return text % values


def tr(text: str, **values) -> str:
    """Translate (plain text, for Python code: messages, PDFs, spreadsheets)."""
    return _fmt(catalog(_lang.get()).get(text, text), values, html=False)


def ntr(singular: str, plural: str, n, **values) -> str:
    values.setdefault("n", n)
    key = singular if n == 1 else plural
    return _fmt(catalog(_lang.get()).get(key, key), values, html=False)


def jinja_gettext(text: str, **values):
    return _fmt(catalog(_lang.get()).get(text, text), values, html=True)


def jinja_ngettext(singular: str, plural: str, n, **values):
    values.setdefault("n", n)
    key = singular if n == 1 else plural
    return _fmt(catalog(_lang.get()).get(key, key), values, html=True)


def N_(text: str) -> str:
    """Marks a text for translation without translating it here: for values that code compares (such
    as the question flags "hard" or "easy"), translated where they are shown with _() or t()."""
    return text


# Flags stored with each scan are written in English by the reader (the phone app shows them as
# they are); on the pages they are translated by pattern.
_FLAG_PATTERNS = [
    (re.compile(r"^several marks in question\(s\) (?P<q>.+)$"),
     N_("several marks in question(s) %(q)s")),
    (re.compile(r"^weak or no mark in question\(s\) (?P<q>.+?) \(counted as blank\)$"),
     N_("weak or no mark in question(s) %(q)s (counted as blank)")),
    (re.compile(r"^weak or no mark in question\(s\) (?P<q>.+)$"),
     N_("weak or no mark in question(s) %(q)s")),
    (re.compile(r"^blank question\(s\) (?P<q>.+)$"),
     N_("blank question(s) %(q)s")),
    (re.compile(r"^a second bubble is nearly as dark in question\(s\) (?P<q>.+?) \(check it\)$"),
     N_("a second bubble is nearly as dark in question(s) %(q)s (check it)")),
    (re.compile(r"^student number incomplete: (?P<no>.+)$"),
     N_("student number incomplete: %(no)s")),
    (re.compile(r"^student number already scanned \(#(?P<id>\d+)\); this newer scan is the one that counts$"),
     N_("student number already scanned (#%(id)s); this newer scan is the one that counts")),
    (re.compile(r"^corrected by hand$"),
     N_("corrected by hand")),
    (re.compile(r"^weak mark in question\(s\) (?P<q>.+)$"),                 # older wording
     N_("weak mark in question(s) %(q)s")),
    (re.compile(r"^more than one answer sheet in the picture - photograph one sheet at a time$"),
     N_("more than one answer sheet in the picture - photograph one sheet at a time")),
    (re.compile(r"^markers: only (?P<n>\d+) of 6 squares found - alignment may be poor$"),
     N_("markers: only %(n)s of 6 squares found - alignment may be poor")),
    (re.compile(r"^markers: fewer than 2 of the 6 black squares were found - retake the photo with the whole sheet in view$"),
     N_("markers: fewer than 2 of the 6 black squares were found - retake the photo with the whole sheet in view")),
    (re.compile(r"^markers: could not compute the page transform$"),
     N_("markers: could not compute the page transform")),
    (re.compile(r"^alignment: marker error (?P<px>[\d.]+)px - sheet may be bent or folded$"),
     N_("alignment: marker error %(px)spx - sheet may be bent or folded")),
    (re.compile(r"^qr: the QR code could not be read - set the version by hand on the review page$"),
     N_("qr: the QR code could not be read - set the version by hand on the review page")),
    (re.compile(r"^set: exam set '(?P<id>[^']*)' is not in this database$"),
     N_("set: exam set '%(id)s' is not in this database")),
]


def flag(text: str) -> str:
    """A stored English flag, translated if it is one of the known kinds."""
    if _lang.get() == "en" or not text:
        return text
    for rx, template in _FLAG_PATTERNS:
        m = rx.match(text)
        if m:
            return tr(template, **m.groupdict())
    return tr(text)


def js_flag_patterns(code: str) -> list[list[str]]:
    """The flag patterns for JavaScript: [regex source, translated template with %(name)s]."""
    cat = catalog(code)
    out = []
    for rx, template in _FLAG_PATTERNS:
        text = cat.get(template, template).replace("%%", "%")
        out.append([rx.pattern.replace("(?P<", "(?<"), text])
    return out


def js_catalog(code: str) -> dict[str, str]:
    """The translations the JavaScript files use (all of them: the file is cached by the browser)."""
    return catalog(code)
