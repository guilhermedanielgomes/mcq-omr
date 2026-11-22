"""MCQ-OMR web application (FastAPI).

Pages (open them in the browser on your computer):
  /                     list of exam sets
  /sets/{id}            edit questions, generate versions, downloads, results
  /scans/{id}           review / correct one scanned sheet
  /scans/{id}/sheet     the student's page: the sheet with right/wrong highlighted, one row per question
  /scans/{id}/sheet/full    the photo, the sheet as read and the marked sheet side by side, full size
  /scans/{id}/report.pdf    all of that in one PDF, with the question-by-question table
  /scans/{id}/marked.jpg    the marked sheet as an image
  /sets/{id}/stats      class statistics (live)
  /sets/{id}/questions/{n}  one question: which students picked each answer, with their results
  /sets/{id}/reveal     tap-to-reveal page for the last scanned student (also /reveal for any set)
  /settings             student names and courses, logo, pass mark, defaults
  /progress             the same students over several MCQs: who missed which one, how results moved
  /scan                 scan without the app: phone camera, webcam of this computer, photos or a zip
API (used by the Android app and the pages):
  GET  /api/ping        connection test
  POST /api/scan        multipart "file" = photo  ->  JSON result
  POST /api/scan_zip    multipart "file" = zip of photos  ->  {"results": [one JSON result per photo]}
  GET  /api/last_scan   last scan (optional ?set_id=) for the reveal page
  GET  /sets/{id}/stats.json
"""
from __future__ import annotations

import io
import ipaddress
import json
import logging
import re
from urllib.parse import quote, quote_plus, urlencode, urlsplit
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from omr import backup as backupmod
from omr import about as aboutmod
from omr import db
from omr import i18n
from omr.i18n import tr
from omr import layout as L
from omr import network
from omr.export import build_keys_csv, build_results_xlsx
from omr.parsers import parse_bulk, questions_to_text
from omr.pdfgen import DEFAULT_LOGO, build_keys_pdf, build_pdf, build_pdf_duplex, sheet_info
from omr.randomize import answer_key, make_versions, new_set_id, refresh_answers, score_answers, validate_questions
from omr.reader import T_MARK, T_WEAK, annotate, encode_jpeg, load_image, read_sheet
from omr import report as reportmod
from omr import sheetview
from omr import stats as statsmod

BASE = Path(__file__).resolve().parent
TOKEN = os.environ.get("MCQ_TOKEN", "").strip()   # optional shared secret (see README)

db.init()
if not TOKEN and db.settings_get().get("lan_protect"):   # a server without a window (Docker, run.py): the password is in the log
    logging.getLogger("uvicorn.error").info("Other devices need this password: %s (Settings > Network)",
                                            db.settings_get().get("lan_password"))
app = FastAPI(title="MCQ-OMR")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/files", StaticFiles(directory=db.SCAN_DIR), name="files")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["LETTERS"] = L.LETTERS
templates.env.globals["_"] = i18n.jinja_gettext
templates.env.globals["ngettext"] = i18n.jinja_ngettext
templates.env.globals["LANGUAGES"] = i18n.LANGUAGES
templates.env.globals["ABOUT"] = aboutmod
templates.env.globals["update_check_on"] = lambda: bool(db.settings_get().get("update_check"))
templates.env.globals["current_lang"] = i18n.current
templates.env.globals["html_lang"] = lambda: i18n.HTML_LANG.get(i18n.current(), "en")
templates.env.filters["flag"] = i18n.flag


def _num(x, digits: int = 1) -> str:
    """85.7 -> '85.7', 50.0 -> '50', None -> '–' (the same as the statistics page)."""
    if x is None:
        return "–"
    s = f"{x:.{digits}f}"
    return s[:-2] if s.endswith(".0") else s


templates.env.filters["num"] = _num
# the emails of a list of student numbers (those that have one), for the "Copy emails" buttons
templates.env.filters["emails_of"] = lambda nos, emails: [emails[n] for n in nos if n and n in emails]


# ---------------------------------------------------------------------------
# optional access token
# ---------------------------------------------------------------------------
@app.middleware("http")
async def language(request: Request, call_next):
    """The interface language for this request: Settings, or the browser's ("automatic"). The phone
    app's /api/ answers always stay in English."""
    token = i18n.set_language(_language_for(request))
    try:
        return await call_next(request)
    finally:
        i18n._lang.reset(token)


def _language_for(request: Request) -> str:
    """The language of the answer to this request (see the language middleware)."""
    if request.url.path.startswith(("/api/", "/static/", "/files/")):
        return "en"
    return i18n.choose(db.settings_get().get("language"), request.headers.get("accept-language"))


@app.get("/i18n.js")
def i18n_js(lang: str = "en"):
    """Translations for the JavaScript files, and t(): t("text") or t("{n} of {total}", {n: 1, total: 2})."""
    body = ("window.I18N = " + json.dumps(i18n.js_catalog(lang), ensure_ascii=False, sort_keys=True) + ";\n"
            "window.t = function (s, v) { var x = (window.I18N && window.I18N[s]) || s;"
            " if (v) { x = x.replace(/\\{(\\w+)\\}/g, function (m, k) { return k in v ? v[k] : m; }); } return x; };\n"
            # scan flags arrive in English from /api/...; tflag() translates the known kinds for display
            "window.MCQ_FLAGS = " + json.dumps(i18n.js_flag_patterns(lang), ensure_ascii=False) + ";\n"
            "window.tflag = function (f) { var P = window.MCQ_FLAGS || []; for (var i = 0; i < P.length; i++) {"
            " var m = new RegExp(P[i][0]).exec(f); if (m) { var g = m.groups || {};"
            " return P[i][1].replace(/%\\((\\w+)\\)s/g, function (x, k) { return k in g ? g[k] : x; }); } } return f; };\n")
    return Response(body, media_type="application/javascript; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    st = db.settings_get()
    return templates.TemplateResponse(request, "about.html", {"update": aboutmod.check(bool(st.get("update_check")))})


@app.get("/updates.json")
def updates_json(force: int = 0):
    """For the notice at the top of the pages: only when the person turned the check on in Settings."""
    return JSONResponse(aboutmod.check(bool(db.settings_get().get("update_check")), force=bool(force)))


@app.post("/settings/updates")
def settings_updates(update_check: str = Form("")):
    db.settings_set(update_check=update_check == "on")
    return RedirectResponse("/settings#updates", status_code=303)


@app.post("/settings/language")
def settings_language(language: str = Form("")):
    db.settings_set(language=language if language in i18n.LANGUAGES else "")
    return RedirectResponse("/settings#language", status_code=303)


# ---------------------------------------------------------------------------
# password for other devices (Settings > Network) and the MCQ_TOKEN access token
# ---------------------------------------------------------------------------
# MCQ_TOKEN set: every request needs it, from this computer too (as before). Otherwise, with
# "lan_protect" on, every request that does not come from this computer needs "lan_password".
# Either one is accepted from the x-token header (the phone app), ?token= or the mcq_token cookie.
OPEN_PATHS = ("/static", "/login", "/i18n.js")
LAN_COOKIE_AGE = 400 * 24 * 3600     # a projector laptop signs in once


def _supplied(request: Request) -> str:
    return (request.headers.get("x-token") or request.query_params.get("token")
            or request.cookies.get("mcq_token") or "")


def _lan_key(text: str) -> bytes:
    """The password as compared: capitals, spaces and dashes do not matter (typed on a phone)."""
    return re.sub(r"[\s-]+", "", str(text)).lower().encode("utf-8")


def _lan_password_ok(supplied: str, password: str) -> bool:
    return bool(password) and secrets.compare_digest(_lan_key(supplied), _lan_key(password))


def _network() -> tuple[bool, str]:
    st = db.settings_get()
    return bool(st.get("lan_protect")), str(st.get("lan_password") or "")


OWN_ADDRESS_SECONDS = 10           # this computer's network address is looked up again after this
_OWN: dict = {}


def _ip(text: str | None):
    """An IP address from a text (brackets of [::1] allowed; ::ffff:1.2.3.4 as 1.2.3.4), or None."""
    try:
        ip = ipaddress.ip_address(str(text or "").strip("[]"))
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _own_ips() -> set:
    """This computer's own address on the network (cached for a few seconds: the Wi-Fi can change)."""
    now = time.monotonic()
    if not _OWN or now - _OWN["at"] > OWN_ADDRESS_SECONDS:
        ip = _ip(network.lan_ip())
        _OWN.update(at=now, ips={ip} if ip is not None else set())
    return _OWN["ips"]


def _allowed_host(request: Request) -> bool:
    """The address in the browser (the Host header) names this computer: localhost, 127.x.x.x, [::1] or
    this computer's own network address. Any other name could be a web site whose name was pointed at
    this computer (DNS rebinding), so it is not trusted as this computer."""
    hp = _host_port("http", request.headers.get("host", ""))
    if not hp or not hp[0]:
        return False
    if hp[0] == "localhost":
        return True
    ip = _ip(hp[0])
    return ip is not None and (ip.is_loopback or ip in _own_ips())


def _ip_host(request: Request) -> bool:
    """The address in the browser is an IP address (not a name)."""
    hp = _host_port("http", request.headers.get("host", ""))
    return bool(hp and hp[0]) and _ip(hp[0]) is not None


def _local_connection(request: Request) -> bool:
    """The connection comes from this computer: a loopback address or this computer's own network address."""
    host = request.client.host if request.client else None
    if network.is_loopback(host):
        return True
    ip = _ip(host)
    return ip is not None and ip in _own_ips()


def _from_this_computer(request: Request) -> bool:
    return _local_connection(request) and _allowed_host(request)


def _authenticated(request: Request) -> bool:
    """The request carries the access token (MCQ_TOKEN) or the password for other devices."""
    if TOKEN:
        return secrets.compare_digest(_supplied(request).encode("utf-8"), TOKEN.encode("utf-8"))
    return _lan_password_ok(_supplied(request), _network()[1])


def _may_see_password(request: Request) -> bool:
    """The password and the pairing QR code: only for this computer or a request that has the password."""
    if TOKEN:
        return _authenticated(request)
    return _from_this_computer(request) or _authenticated(request)


def _safe_next(target: str) -> str:
    """Only a path on this server (no //other.host, no backslashes) to go back to after signing in."""
    target = str(target or "")
    if not target.startswith("/") or target.startswith("//") or "\\" in target or any(ord(ch) < 32 for ch in target):
        return "/"
    return target


def _to_login(request: Request) -> RedirectResponse:
    """To /login, which then goes back to the page asked for (without a ?token= that did not work)."""
    query = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "token"])
    asked = request.url.path + ("?" + query if query else "")
    return RedirectResponse("/login" + ("?" + urlencode({"next": asked}) if asked != "/" else ""), status_code=303)


@app.middleware("http")
async def token_guard(request: Request, call_next):
    if TOKEN and not request.url.path.startswith(OPEN_PATHS):
        if not secrets.compare_digest(_supplied(request).encode("utf-8"), TOKEN.encode("utf-8")):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "invalid token"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    elif not TOKEN and not request.url.path.startswith(OPEN_PATHS) and not _from_this_computer(request):
        protect, password = _network()
        if protect and not _lan_password_ok(_supplied(request), password):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "invalid token"}, status_code=401)
            return _to_login(request)
    return await call_next(request)


def _host_port(scheme: str, netloc: str) -> tuple[str, int] | None:
    try:
        u = urlsplit(f"{scheme}://{netloc}")
        return (u.hostname or "").lower(), u.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None


def _same_origin(url: str, host: str) -> bool:
    """True if url (an Origin or Referer header) is this server, as the browser addressed it (Host)."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.netloc or not host:
        return False
    mine = _host_port(u.scheme, host)
    return mine is not None and _host_port(u.scheme, u.netloc.rsplit("@", 1)[-1]) == mine


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """No form posts from other web sites (CSRF): a POST, PUT, PATCH or DELETE whose Origin (or else
    Referer) is another site is refused. Requests with neither header (the phone app, scripts) pass.
    A page at a name other than this computer's own (see _allowed_host) may only post with the password
    or the access token: otherwise it could be another web site pointed at this computer (DNS rebinding).
    A page at any IP address may post (a web site cannot make the browser use an IP address as its name)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        host = request.headers.get("host", "")
        cross = (request.headers.get("sec-fetch-site", "").lower() == "cross-site"
                 or (origin is not None and not _same_origin(origin, host))
                 or (origin is None and referer is not None and not _same_origin(referer, host)))
        if not cross and (origin is not None or referer is not None):
            # a page at a name (not an address) other than this computer's: only with the password or token.
            # Any IP address is fine here (DNS rebinding always uses the attacker's name), so a device using
            # another address of this computer (Docker, a second network) can still save its forms.
            cross = not (_allowed_host(request) or _ip_host(request) or _authenticated(request))
        if cross:
            token = i18n.set_language(_language_for(request))    # this runs before the language middleware
            try:
                text = tr("Refused: this request came from another web site.")
            finally:
                i18n._lang.reset(token)
            return Response(text, status_code=403, media_type="text/plain; charset=utf-8")
    return await call_next(request)


def _login_form(request: Request, next_url: str, wrong: bool = False):
    return templates.TemplateResponse(request, "login.html", {"token_mode": bool(TOKEN), "next": _safe_next(next_url),
                                                              "wrong": wrong}, status_code=401 if wrong else 200)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return _login_form(request, next)


@app.post("/login")
def login(request: Request, token: str = Form(""), next: str = Form("/")):
    if TOKEN:                                 # as before: the guard sends a wrong token back here
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("mcq_token", token, httponly=True, samesite="lax")
        return resp
    password = _network()[1]
    if not _lan_password_ok(token, password):
        return _login_form(request, next, wrong=True)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    resp.set_cookie("mcq_token", password, max_age=LAN_COOKIE_AGE, httponly=True, samesite="lax")
    return resp


def _server_address(request: Request) -> str:
    """The address the phone app connects to: this computer on the network, on the port of this page."""
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return f"http://{network.lan_ip()}:{port}"


def _network_card(request: Request) -> dict:
    protect, password = _network()
    card = {"protect": protect, "token_mode": bool(TOKEN), "show": _may_see_password(request), "password": "",
            "server": _server_address(request)}
    if card["show"]:
        card["password"] = password
    return card


@app.get("/settings/network/qr.png")
def settings_network_qr(request: Request):
    """The pairing QR code: mcqomr://pair?u=<server address>&t=<password>."""
    if not _may_see_password(request):
        raise HTTPException(404)
    png = network.qr_png(network.pair_url(_server_address(request), _network()[1]))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/settings/network")
def settings_network(request: Request, lan_protect: str = Form("")):
    if not _may_see_password(request):
        return _back(tr("Only this computer, or a device signed in with the password, can change this."), "network")
    on = lan_protect in ("1", "on", "yes", "true")
    made = db.set_lan_protect(on)            # switched on: always a new password
    if made:
        resp = _back(tr("Other devices now need the password. It is a new one: type it into the phone app, "
                        "or scan the QR code."), "network")
        if not TOKEN and not _from_this_computer(request):      # this device stays signed in
            resp.set_cookie("mcq_token", db.settings_get()["lan_password"], max_age=LAN_COOKIE_AGE, httponly=True,
                            samesite="lax")
        return resp
    return _back(tr("Other devices now need the password.") if on
                 else tr("Other devices can now open MCQ-OMR without a password."), "network")


@app.post("/settings/network/password")
def settings_network_password(request: Request):
    if not _may_see_password(request):
        return _back(tr("Only this computer, or a device signed in with the password, can change this."), "network")
    password = network.new_password()
    db.settings_set(lan_password=password)
    resp = _back(tr("New password made. Type it into the phone app again, or scan the new QR code."), "network")
    if not TOKEN and not _from_this_computer(request):      # this device stays signed in
        resp.set_cookie("mcq_token", password, max_age=LAN_COOKIE_AGE, httponly=True, samesite="lax")
    return resp


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _logo_path() -> str | None:
    """None = bundled logo, "" = no logo, else path of the uploaded logo (see Settings)."""
    mode = db.settings_get().get("logo", "default")
    custom = db.DATA_DIR / "logo.png"
    if mode == "none":
        return ""
    if mode == "custom" and custom.exists():
        return str(custom)
    return None


def _parse_student_rows(text: str) -> list[tuple[str, str, str]]:
    """'12345678, Jane Doe' per line (comma, semicolon or tab; number first or name first), optionally
    with the student's email anywhere on the line. Returns (number, name, email or "")."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        for delim in (",", ";", "\t"):
            if delim in line:
                parts = [c.strip().strip('"') for c in line.split(delim)]
                break
        else:
            parts = line.split(None, 1)
        # an email anywhere on the line (a word with @) is the student's own email, not part of the name
        emails = [w.strip("<>;,") for c in parts for w in c.split() if "@" in w]
        parts = [" ".join(w for w in c.split() if "@" not in w) for c in parts]
        parts = [c for c in parts if c]
        if len(parts) < 2:
            continue
        looks_like_no = [any(ch.isdigit() for ch in c) and len(c.replace(" ", "")) <= 12 for c in parts]
        if looks_like_no[0]:
            no, name = parts[0], " ".join(parts[1:])
        elif looks_like_no[-1]:
            no, name = parts[-1], " ".join(parts[:-1])
        else:
            continue
        no = no.upper().replace(" ", "")
        if no.lower() in ("student number", "number", "id", "student no", "studentno"):
            continue
        rows.append((no, name, emails[0] if emails else ""))
    return rows


def _exam_or_404(set_id: str) -> dict:
    exam = db.get_set(set_id)
    if not exam:
        raise HTTPException(404, "exam set not found")
    return exam


def _with_msg(path: str, msg: str) -> str:
    """path?msg=<message>, encoded as the messages always were (spaces as "+", ":" kept)."""
    return f"{path}?msg={quote_plus(msg, safe=':')}"


def _version(exam: dict, vno) -> dict | None:
    for v in exam.get("versions", []):
        if v["version"] == vno:
            return v
    return None


def _layout_for(qr: dict):
    exam = db.get_set(qr["set_id"])
    if not exam:
        return None
    version = _version(exam, qr["version"])
    if not version:
        return None
    lay, _ = sheet_info(exam, version)
    return lay


def _status(res, exam, version) -> str:
    if res.rectified is None:
        return "failed"
    if not res.qr or exam is None or version is None or not res.ok or res.multiple or res.weak or res.check:
        return "review"
    if any(f.startswith(("alignment", "markers")) for f in res.flags):
        return "review"
    return "ok"


def _rescore_set(exam: dict):
    for s in db.list_scans(exam["id"]):
        v = _version(exam, s["version"])
        if v and len(s["answers"]) == len(v["questions"]):
            key = answer_key(v)
            db.update_scan(s["id"], score=score_answers(s["answers"], key), max_score=len(key))


def _thresholds() -> tuple[float, float]:
    """(t_mark, t_weak) as fractions, from Settings; falls back to the reader's defaults."""
    st = db.settings_get()
    try:
        mark, weak = float(st.get("mark_pct", 35)) / 100.0, float(st.get("weak_pct", 20)) / 100.0
    except (TypeError, ValueError):
        return T_MARK, T_WEAK
    return (mark, weak) if 0.0 < weak < mark <= 1.0 else (T_MARK, T_WEAK)


def process_photo(data: bytes) -> dict:
    """Read one photo, store it, return the JSON the phone shows."""
    t_mark, t_weak = _thresholds()
    res = read_sheet(data, _layout_for, t_mark=t_mark, t_weak=t_weak)
    exam = version = None
    if res.qr:
        exam = db.get_set(res.qr["set_id"])
        version = _version(exam, res.qr["version"]) if exam else None
    score = max_score = None
    if exam and version and res.layout is not None:
        key = answer_key(version)
        score, max_score = score_answers(res.answers, key), len(key)
    status = _status(res, exam, version)

    stamp = f"{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    img_name = f"{stamp}_photo.jpg"
    (db.SCAN_DIR / img_name).write_bytes(encode_jpeg(load_image(data), 85))
    overlay_name = None
    if res.overlay is not None:
        text = f"score {score}/{max_score}" if score is not None else "not scored"
        text += f"   status: {status}"
        (db.SCAN_DIR / f"{stamp}_overlay.jpg").write_bytes(encode_jpeg(annotate(res.overlay, text), 80))
        overlay_name = f"{stamp}_overlay.jpg"

    duplicate_of = None
    if exam and res.student_no and res.ok:
        for s in db.list_scans(exam["id"]):
            if s["student_no"] == res.student_no:
                duplicate_of = s["id"]
    flags = list(res.flags)
    if duplicate_of:
        flags.append(f"student number already scanned (#{duplicate_of}); this newer scan is the one that counts")

    scan_id = db.add_scan(set_id=exam["id"] if exam else (res.qr["set_id"] if res.qr else None),
                          version=version["version"] if version else (res.qr["version"] if res.qr else None),
                          student_no=res.student_no, answers=res.answers, score=score, max_score=max_score,
                          status=status, flags=flags, image=img_name, overlay=overlay_name,
                          raw={**res.summary(), "darkness": res.darkness, "id_darkness": res.id_darkness})
    return {
        "ok": status != "failed",
        "status": status,
        "scan_id": scan_id,
        "set_id": exam["id"] if exam else None,
        "set_title": exam["meta"].get("title") if exam else None,
        "version": version["version"] if version else None,
        "student_no": res.student_no,
        "name": db.students_get().get(res.student_no, "") if res.student_no else "",
        "score": score,
        "max_score": max_score,
        "flags": flags,
        "duplicate": duplicate_of is not None,
        "review_url": f"/scans/{scan_id}",
    }


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, msg: str = ""):
    return templates.TemplateResponse(request, "index.html", {"sets": db.list_sets(), "msg": msg})


@app.get("/sets/new", response_class=HTMLResponse)
def set_new(request: Request):
    return templates.TemplateResponse(request, "set_new.html", {"defaults": db.settings_get(),
                                                                 "course_list": _course_list()})


def _exam_class(exam: dict | None) -> str:
    """The class (course) an exam set is meant for, as stored ("" = none: all students)."""
    return " ".join(str(((exam or {}).get("meta") or {}).get("course") or "").split())


def _set_class(exam: dict, course: str):
    """Store the class chosen in the form: a course on the list (in its spelling), the class the set
    already has (also when it was removed from the list since), or none."""
    k = statsmod.course_key(course)
    if not k:
        exam["meta"].pop("course", None)
        return
    valid = {statsmod.course_key(c): c for c in _course_list()}
    if k in valid:
        exam["meta"]["course"] = valid[k]
    # anything else (the class the set already has, or a course that is not on the list): left as it was


def _course_default(exam: dict, course: str | None, fallback: bool = True) -> str:
    """The course filter of a page: the one asked for (course= in the address, "" = all students), or
    with no course= at all the class of the exam set, in the spelling the drop-downs use. A class with no
    counted sheet in this set yet opens on all students instead (see _class_empty), unless fallback=False."""
    if course is not None:
        return course
    cls = _exam_class(exam)
    if not cls or (fallback and _class_empty(exam)):
        return ""
    return statsmod.course_names(db.students_courses()).get(statsmod.course_key(cls), cls)


def _class_empty(exam: dict, scans: list[dict] | None = None) -> str:
    """The set's class when it has no counted sheet in this set yet ("" otherwise): its pages then show
    all students, with a note, instead of an empty view."""
    cls = _exam_class(exam)
    if not cls:
        return ""
    scans = db.list_scans(exam["id"]) if scans is None else scans
    return cls if statsmod.n_counted(exam, statsmod.filter_by_course(scans, db.students_courses(), cls)) == 0 else ""


def _all_topics() -> list[str]:
    """Every topic used in any exam set, for the suggestions of the question editor."""
    seen: dict[str, str] = {}
    for s in db.list_sets():
        exam = db.get_set(s["id"]) or {}
        for q in exam.get("questions", []):
            t = " ".join(str(q.get("topic") or "").split())
            if t:
                seen.setdefault(t.casefold(), t)
    return sorted(seen.values(), key=str.casefold)


@app.post("/sets")
def set_create(title: str = Form(...), module: str = Form(""), semester: str = Form(""),
               lecturer: str = Form(""), institution: str = Form(""), instructions: str = Form(""),
               id_prefix: str = Form(""), id_digits: int = Form(6), course: str = Form("")):
    exam = {
        "id": new_set_id(),
        "meta": {"title": title.strip(), "module": module.strip(), "semester": semester.strip(),
                 "lecturer": lecturer.strip(), "instructions": instructions.strip(),
                 "institution": [ln.strip() for ln in institution.splitlines() if ln.strip()][:3]},
        "settings": {"id_prefix": id_prefix.strip(), "id_digits": max(1, min(L.MAX_ID_DIGITS, id_digits)),
                     "shuffle_questions": True, "shuffle_choices": True},
        "questions": [],
        "versions": [],
    }
    _set_class(exam, course)
    db.save_set(exam)
    return RedirectResponse(f"/sets/{exam['id']}", status_code=303)


@app.get("/sets/{set_id}", response_class=HTMLResponse)
def set_page(request: Request, set_id: str, msg: str = ""):
    exam = _exam_or_404(set_id)
    scans = db.list_scans(set_id) if exam.get("versions") else []
    max_opts = max((len(q["choices"]) for q in exam["questions"]), default=5)
    courses = db.students_courses()
    roster = db.students_get()
    cls = _exam_class(exam)
    course_opts = statsmod.course_options(scans, courses)
    course_list = _course_list()
    class_empty = _class_empty(exam, scans) if scans else ""
    not_scanned = []
    if cls and scans:     # the students of the set's class with no counted sheet in this set
        sat = {s["student_no"] for s in scans if not s["superseded"] and s["student_no"]}
        not_scanned = sorted((no for no, c in courses.items()
                              if statsmod.course_key(c) == statsmod.course_key(cls) and no not in sat),
                             key=lambda no: ((roster.get(no) or "~").casefold(), no))
    return templates.TemplateResponse(request, "set.html", {
        "exam": exam, "scans": scans, "msg": msg,
        "locked": bool(exam.get("versions")),
        "problems": validate_questions(exam["questions"]) if exam["questions"] else [],
        "capacity": L.capacity(max(L.MIN_OPTIONS, min(L.MAX_OPTIONS, max_opts))),
        "roster": roster,
        "emails": db.students_emails(),
        "course_of": {no: statsmod.course_key(c) for no, c in courses.items()},
        "course_opts": course_opts,
        "no_course": statsmod.NO_COURSE, "course_key": statsmod.course_key,
        "n_ok": sum(1 for s in scans if s["status"] == "ok" and not s["superseded"]),
        "n_review": sum(1 for s in scans if s["status"] != "ok" and not s["superseded"]),
        # sheets still to check (status not ok, or a student number that could not be read): never hidden by the
        # course filter, and the students not scanned yet may be among them
        "n_pending": sum(1 for s in scans if not s["superseded"] and (s["status"] != "ok" or not statsmod.readable_no(s["student_no"]))),
        "exam_class": cls, "class_key": statsmod.course_key(cls), "not_scanned": not_scanned,
        "class_empty": class_empty, "class_select": "" if class_empty else statsmod.course_key(cls),
        "course_list": course_list, "topics": _all_topics(),
        # a class that is not among the choices (no sheet of it yet, or removed from the course list since)
        "class_missing_opt": statsmod.course_key(cls) not in {statsmod.course_key(o["value"]) for o in course_opts
                                                              if o["value"] != statsmod.NO_COURSE},
        "class_missing_list": statsmod.course_key(cls) not in {statsmod.course_key(c) for c in course_list},
    })


@app.post("/sets/{set_id}/meta")
def set_meta(set_id: str, title: str = Form(...), module: str = Form(""), semester: str = Form(""),
             lecturer: str = Form(""), institution: str = Form(""), instructions: str = Form(""),
             id_prefix: str = Form(""), id_digits: int = Form(6), course: str = Form("")):
    exam = _exam_or_404(set_id)
    _set_class(exam, course)     # not printed: it can change also once versions exist
    exam["meta"].update({"title": title.strip(), "module": module.strip(), "semester": semester.strip(),
                         "lecturer": lecturer.strip(), "instructions": instructions.strip(),
                         "institution": [ln.strip() for ln in institution.splitlines() if ln.strip()][:3]})
    if not exam.get("versions"):   # the sheet geometry is frozen once versions exist
        exam["settings"]["id_prefix"] = id_prefix.strip()
        exam["settings"]["id_digits"] = max(1, min(L.MAX_ID_DIGITS, id_digits))
    db.save_set(exam)
    return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("Details saved")), status_code=303)


def _clean_questions(questions: list) -> list[dict]:
    """Questions as sent by the editor, reduced to the stored fields."""
    clean = []
    for q in questions:
        clean.append({
            "text": str(q.get("text", "")).strip(),
            "choices": [{"text": str(c.get("text", "")).strip(), "fixed": bool(c.get("fixed"))}
                        for c in q.get("choices", [])],
            "correct": int(q.get("correct", -1)) if str(q.get("correct", "")).lstrip("-").isdigit() else -1,
        })
        topic = " ".join(str(q.get("topic") or "").split())   # optional, not printed
        if topic:
            clean[-1]["topic"] = topic
    return clean


@app.post("/sets/{set_id}/questions")
async def set_questions(set_id: str, request: Request):
    _exam_or_404(set_id)
    body = await request.json()
    exam = _exam_or_404(set_id)         # read after the body arrived: a details save meanwhile is kept
    clean = _clean_questions(body.get("questions", []))
    if exam.get("versions"):
        old = exam["questions"]
        same = len(old) == len(clean) and all(len(a["choices"]) == len(b["choices"]) for a, b in zip(old, clean))
        if not same:
            return JSONResponse({"ok": False, "problems": [
                tr("Versions were already generated: you can fix texts and the correct answer, "
                   "but not add/remove questions or choices. Unlock the set to change the structure.")]})
    exam["questions"] = clean
    if exam.get("versions"):
        # a changed correct answer must reach the answer keys of the versions before rescoring
        refresh_answers(clean, exam["versions"])
    db.save_set(exam)
    out = {"ok": True, "problems": validate_questions(clean), "n": len(clean),
           "rescored": bool(exam.get("versions"))}
    # what the page shows from the saved questions, so the editor can update it without a reload
    max_opts = max((len(q["choices"]) for q in clean), default=5)
    out["capacity"] = L.capacity(max(L.MIN_OPTIONS, min(L.MAX_OPTIONS, max_opts)))
    if exam.get("versions"):
        _rescore_set(exam)
        out["scores"] = {str(s["id"]): [s["score"], s["max_score"]] for s in db.list_scans(set_id)}
    return JSONResponse(out)


@app.post("/sets/{set_id}/import")
def set_import(set_id: str, text: str = Form(""), mode: str = Form("append"), current: str = Form("")):
    exam = _exam_or_404(set_id)
    if exam.get("versions"):
        return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("Unlock the set before importing")), status_code=303)
    qs, errors = parse_bulk(text)
    # "current" = the questions as the editor shows them (unsaved removals and edits included), so
    # appending does not bring back questions removed in the editor but not saved yet
    base = exam["questions"]
    if current:
        try:
            base = _clean_questions(json.loads(current))
        except (ValueError, TypeError, AttributeError):
            base = exam["questions"]
        # the editor of an empty set shows one blank question: never store wholly empty ones
        base = [q for q in base if q["text"] or any(c["text"] for c in q["choices"])]
    exam["questions"] = (base + qs) if mode == "append" else qs
    db.save_set(exam)
    if errors:
        msg = tr("Imported %(n)s question(s). Problems: %(problems)s", n=len(qs), problems=" | ".join(errors))
    else:
        msg = tr("Imported %(n)s question(s).", n=len(qs))
    # encoded the way the address was before (spaces as %20), but now also safe for & # + in the text
    return RedirectResponse(f"/sets/{set_id}?msg=" + quote(msg, safe=":/@[]!$'()*,;"), status_code=303)


@app.get("/sets/{set_id}/questions.txt")
def set_questions_txt(set_id: str):
    exam = _exam_or_404(set_id)
    return Response(questions_to_text(exam["questions"]), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{set_id}_questions.txt"'})


@app.post("/sets/{set_id}/generate")
def set_generate(set_id: str, n_versions: int = Form(...), shuffle_questions: str = Form("on"),
                 shuffle_choices: str = Form("on")):
    exam = _exam_or_404(set_id)
    problems = validate_questions(exam["questions"])
    if problems:
        return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("Fix the questions first")), status_code=303)
    max_opts = max(len(q["choices"]) for q in exam["questions"])
    cap = L.capacity(max_opts)
    if len(exam["questions"]) > cap:
        return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("Too many questions: max %(cap)s with %(n)s choices",
                                                               cap=cap, n=max_opts)), status_code=303)
    n_versions = max(1, min(500, n_versions))
    exam["settings"]["shuffle_questions"] = shuffle_questions == "on"
    exam["settings"]["shuffle_choices"] = shuffle_choices == "on"
    exam["versions"] = make_versions(exam["questions"], n_versions, exam["id"],
                                     exam["settings"]["shuffle_questions"], exam["settings"]["shuffle_choices"])
    db.save_set(exam)
    return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("%(n)s versions generated", n=n_versions)), status_code=303)


@app.post("/sets/{set_id}/unlock")
def set_unlock(set_id: str):
    exam = _exam_or_404(set_id)
    for s in db.list_scans(set_id):
        db.delete_scan(s["id"])
    exam["versions"] = []
    db.save_set(exam)
    return RedirectResponse(_with_msg(f"/sets/{set_id}", tr("Set unlocked - versions and scans removed")),
                            status_code=303)


@app.post("/sets/{set_id}/delete")
def set_delete(set_id: str):
    for s in db.list_scans(set_id):
        db.delete_scan(s["id"])
    db.delete_set(set_id)
    return RedirectResponse("/", status_code=303)


@app.get("/sets/{set_id}/pdf/{kind}")
def set_pdf(set_id: str, kind: str):
    exam = _exam_or_404(set_id)
    if not exam.get("versions"):
        raise HTTPException(400, "generate the versions first")
    if kind == "keys":
        data = build_keys_pdf(exam, exam["versions"])
    elif kind == "duplex":
        data = build_pdf_duplex(exam, exam["versions"], logo=_logo_path())
    elif kind in ("both", "sheets", "papers"):
        data = build_pdf(exam, exam["versions"], papers=kind != "sheets", sheets=kind != "papers",
                         logo=_logo_path())
    else:
        raise HTTPException(404)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{set_id}_{kind}.pdf"'})


@app.get("/sets/{set_id}/keys.csv")
def set_keys_csv(set_id: str):
    exam = _exam_or_404(set_id)
    return Response(build_keys_csv(exam), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{set_id}_answer_keys.csv"'})


@app.get("/sets/{set_id}/results.xlsx")
def set_results(set_id: str):
    exam = _exam_or_404(set_id)
    data = build_results_xlsx(exam, db.list_scans(set_id), db.students_get(),
                              db.students_courses(), db.students_emails())
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{set_id}_results.xlsx"'})


# ---------------------------------------------------------------------------
# settings: student names, logo, pass mark, defaults
# ---------------------------------------------------------------------------
def _course_list() -> list[str]:
    """The courses of the drop-downs: the list kept in Settings, plus any course a student already has
    that is not on it (spelling differences in capitals or spaces count as the same course)."""
    saved = [c for c in (db.settings_get().get("course_list") or []) if str(c).strip()]
    seen = {statsmod.course_key(c) for c in saved}
    extra = [c for k, c in statsmod.course_names(db.students_courses()).items() if k not in seen]
    return sorted([" ".join(str(c).split()) for c in saved] + extra, key=str.casefold)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = ""):
    st = db.settings_get()
    info = db.students_info()
    names = {k: statsmod.course_key(v["course"]) for k, v in info.items()}
    courses = _course_list()
    return templates.TemplateResponse(request, "settings.html", {
        "s": st, "students": db.students_get(), "info": info, "msg": msg,
        "course_list": courses, "email_domain": db.email_domain(),
        # the drop-down value of each student = the spelling on the course list
        "student_course": {no: next((c for c in courses if statsmod.course_key(c) == k), "") for no, k in names.items()},
        "course_counts": {c: sum(1 for k in names.values() if k == statsmod.course_key(c)) for c in courses},
        "custom_logo": (db.DATA_DIR / "logo.png").exists(),
        "bundled_logo": os.path.exists(DEFAULT_LOGO),
        "network": _network_card(request),
    })


def _back(msg: str, anchor: str = "students", refused: bool = False) -> RedirectResponse:
    return RedirectResponse("/settings?" + urlencode({"msg": msg}) + ("&error=1" if refused else "") + "#" + anchor,
                            status_code=303)


def _autosave(request: Request) -> bool:
    """The request comes from a form that saves itself (static/autosave.js); it is told about refused values
    with error=1 in the redirect, so the value is not shown as saved. Normal form posts are unchanged."""
    return request.headers.get("x-autosave") == "1"


@app.get("/settings/backup")
def settings_backup():
    """Download a backup of everything (exam sets, scans with photos, students, settings) as a .zip."""
    folder = tempfile.mkdtemp(prefix="mcq-download-")
    path = Path(folder) / backupmod.default_name()
    backupmod.create(path)
    return FileResponse(path, filename=path.name, media_type="application/zip",
                        background=BackgroundTask(shutil.rmtree, folder, True))


@app.post("/settings/restore")
def settings_restore(file: UploadFile = File(...), confirm: str = Form("")):
    """Replace all data with an uploaded backup. The current data is saved first in the data folder."""
    if confirm != "yes":
        return _back(tr("Nothing was restored: tick the box to confirm that the current data is replaced."), "backup")
    folder = tempfile.mkdtemp(prefix="mcq-restore-")
    try:
        path = Path(folder) / "upload.zip"
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        result = backupmod.restore(path)
    except backupmod.BackupError as e:
        return _back(str(e), "backup")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return _back(tr("Backup restored (%(what)s). The data you had before was saved as %(name)s in the backups folder "
                    "of the data folder.", what=backupmod.describe(result["manifest"]), name=Path(result["safety"]).name),
                 "backup")


@app.post("/settings/students")
async def settings_students(text: str = Form(""), mode: str = Form("merge"), course: str = Form(""),
                            file: UploadFile | None = File(None)):
    if file is not None and file.filename:
        text = (await file.read()).decode("utf-8-sig", errors="replace") + "\n" + text
    rows = _parse_student_rows(text)
    course = " ".join(course.split())
    n = db.students_merge(rows, replace=(mode == "replace"), course=course or None)
    n_mail = sum(1 for r in rows if r[2])
    if mode == "replace":
        msg = (tr("%(n)s students loaded, course %(course)s", n=n, course=course) if course
               else tr("%(n)s students loaded", n=n))
    else:
        msg = (tr("%(n)s students added/updated, course %(course)s", n=n, course=course) if course
               else tr("%(n)s students added/updated", n=n))
    if n_mail:
        msg = tr("%(message)s, %(n)s with their own email", message=msg, n=n_mail)
    return _back(msg)


@app.post("/settings/students/update")
async def settings_students_update(request: Request):
    """The stored list: course drop-down and email of every student (fields <kind>_<number>)."""
    form = await request.form()
    info = db.students_info()
    valid = {statsmod.course_key(c): c for c in _course_list()}
    n, bad = 0, []
    for no in form.getlist("rows"):
        if no not in info:
            continue
        course = valid.get(statsmod.course_key(str(form.get(f"course_{no}", ""))), "")
        custom = form.get(f"default_{no}") != "on"
        email = " ".join(str(form.get(f"email_{no}", "")).split())
        if email and ("@" not in email or " " in email):
            bad.append(no)
            email, custom = info[no]["email"], info[no]["email_custom"]
        old = info[no]
        if (course, email, custom) != (old["course"], old["email"], old["email_custom"]):
            db.students_update(no, course, email, custom)
            n += 1
    if bad:
        return _back(tr("Saved: %(n)s student(s) changed; not an email address, left as it was: %(numbers)s",
                        n=n, numbers=", ".join(bad)), refused=_autosave(request))
    return _back(tr("Saved: %(n)s student(s) changed", n=n))


@app.post("/settings/courses/add")
def settings_courses_add(name: str = Form("")):
    name = " ".join(name.split())
    if not name:
        return _back(tr("Type the name of the course first"))
    have = _course_list()
    if statsmod.course_key(name) in {statsmod.course_key(c) for c in have}:
        return _back(tr("%(name)s is already on the list", name=name))
    db.settings_set(course_list=have + [name])
    return _back(tr("Course %(name)s added - pick it in the list of students below", name=name))


@app.post("/settings/courses/remove")
def settings_courses_remove(name: str = Form("")):
    k = statsmod.course_key(name)
    for no, c in db.students_courses().items():
        if statsmod.course_key(c) == k:
            db.students_set_course(no, "")
    db.settings_set(course_list=[c for c in _course_list() if statsmod.course_key(c) != k])
    return _back(tr("Course %(name)s removed; its students have no course now", name=name))


@app.post("/settings/email")
def settings_email(request: Request, email_domain: str = Form("")):
    d = email_domain.strip().lstrip("@").strip()
    if d and (" " in d or "." not in d):
        return _back(tr("That does not look like an email domain (e.g. student.university.edu)"), refused=_autosave(request))
    db.settings_set(email_domain=d)
    return _back(tr("Email domain saved: student number @%(domain)s", domain=d) if d else tr("Email domain cleared"))


@app.post("/settings/students/delete")
def settings_students_delete(student_no: str = Form(...)):
    db.students_delete(student_no.strip().upper())
    return RedirectResponse(_with_msg("/settings", tr("Student removed")), status_code=303)


@app.post("/settings/students/clear")
def settings_students_clear():
    db.students_merge([], replace=True)
    return RedirectResponse(_with_msg("/settings", tr("Student list cleared")), status_code=303)


@app.post("/settings/general")
def settings_general(pass_mark: float = Form(40), bands: str = Form(""), reveal_show_name: str = Form(""),
                     reveal_show_percent: str = Form(""), reveal_show_score: str = Form(""),
                     reveal_hide_after: int = Form(0),
                     default_lecturer: str = Form(""), default_institution: str = Form(""),
                     default_id_prefix: str = Form(""), default_id_digits: int = Form(6)):
    db.settings_set(pass_mark=max(0.0, min(100.0, pass_mark)), bands=bands.strip() or db.DEFAULT_SETTINGS["bands"],
                    reveal_show_name=reveal_show_name == "on", reveal_show_percent=reveal_show_percent == "on",
                    reveal_show_score=reveal_show_score == "on",
                    reveal_hide_after=max(0, min(600, reveal_hide_after)), default_lecturer=default_lecturer.strip(),
                    default_institution=default_institution.strip(), default_id_prefix=default_id_prefix.strip(),
                    default_id_digits=max(1, min(L.MAX_ID_DIGITS, default_id_digits)))
    return RedirectResponse(_with_msg("/settings", tr("Settings saved")), status_code=303)


@app.post("/settings/reading")
def settings_reading(mark_pct: int = Form(35), weak_pct: int = Form(20)):
    mark_pct = max(10, min(90, mark_pct))
    weak_pct = max(5, min(mark_pct - 5, weak_pct))
    db.settings_set(mark_pct=mark_pct, weak_pct=weak_pct)
    return RedirectResponse(_with_msg("/settings", tr("Reading thresholds saved - they apply to sheets scanned from now on")),
                            status_code=303)


@app.post("/settings/colours")
async def settings_colours(request: Request):
    form = await request.form()
    if form.get("reset"):
        db.settings_set(**{k: db.DEFAULT_SETTINGS[k] for k in db.COLOR_KEYS})
        return RedirectResponse(_with_msg("/settings", tr("Colours back to the defaults")), status_code=303)
    values = {}
    for k in db.COLOR_KEYS:
        v = str(form.get(k, "")).strip().lower()
        if len(v) == 7 and v[0] == "#" and all(ch in "0123456789abcdef" for ch in v[1:]):
            values[k] = v
    db.settings_set(**values)
    return RedirectResponse(_with_msg("/settings", tr("Colours saved")), status_code=303)


@app.post("/settings/logo")
async def settings_logo(request: Request, mode: str = Form("default"), file: UploadFile | None = File(None)):
    if file is not None and file.filename:
        data = await file.read()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            return RedirectResponse(_with_msg("/settings", tr("The logo file is not an image I can read"))
                                    + ("&error=1" if _autosave(request) else ""), status_code=303)
        cv2.imwrite(str(db.DATA_DIR / "logo.png"), img)
        mode = "custom"
    if mode == "custom" and not (db.DATA_DIR / "logo.png").exists():
        mode = "default"
    db.settings_set(logo=mode)
    return RedirectResponse(_with_msg("/settings", tr("Logo setting saved")), status_code=303)


# ---------------------------------------------------------------------------
# statistics and the reveal page
# ---------------------------------------------------------------------------
def _stats_settings() -> dict:
    st = db.settings_get()
    st["bands_parsed"] = db.parse_bands(st.get("bands", ""))
    if st.get("bands") == db.DEFAULT_SETTINGS["bands"]:     # the default names, in the interface language
        names = {"Excellent": i18n.N_("Excellent"), "Very good": i18n.N_("Very good"), "Good": i18n.N_("Good"),
                 "Pass": i18n.N_("Pass"), "Fail": i18n.N_("Fail")}
        st["bands_parsed"] = [(lo, i18n.tr(names[n]) if n in names else n) for lo, n in st["bands_parsed"]]
    return st


def _course_scans(set_id: str, course: str):
    """The scans of a set for one course ('' = all), plus the drop-down choices and the label."""
    scans = db.list_scans(set_id)
    courses = db.students_courses()
    return (statsmod.filter_by_course(scans, courses, course), statsmod.course_options(scans, courses),
            statsmod.course_label(course, courses))


def _stats_for(set_id: str, course: str | None = "") -> dict:
    exam = _exam_or_404(set_id)
    course = _course_default(exam, course)
    scans, options, label = _course_scans(set_id, course)
    out = statsmod.compute(exam, scans, db.students_get(), _stats_settings())
    if options or course:          # only when courses are in use: otherwise stats.json is as it was
        out.update({"course": course, "course_label": label, "courses": options})
    emails = db.students_emails()
    if emails:                     # the same: only once students have emails (for the Copy emails button)
        out["emails"] = {r["student_no"]: emails[r["student_no"]] for r in out.get("students", []) if r["student_no"] in emails}
    return out


@app.get("/sets/{set_id}/stats", response_class=HTMLResponse)
def stats_page(request: Request, set_id: str, course: str | None = None):
    exam = _exam_or_404(set_id)
    course = _course_default(exam, course)
    _, options, _ = _course_scans(set_id, "")
    return templates.TemplateResponse(request, "stats.html", {"exam": exam, "course": course, "courses": options,
                                                               "sets": _stats_sets(),
                                                               "class_empty": "" if course else _class_empty(exam)})


@app.get("/sets/{set_id}/stats.json")
def stats_json(set_id: str, course: str | None = None):
    return JSONResponse(_stats_for(set_id, course))


def _stats_sets() -> list[dict]:
    """Exam sets that can have statistics (versions generated), newest first."""
    return [s for s in db.list_sets() if s["n_versions"]]


@app.get("/stats")
def stats_latest():
    """The Statistics button in the menu: the statistics of the newest exam set with scanned sheets
    (or the newest one at all). The statistics page has a drop-down to switch to another set."""
    sets = _stats_sets()
    if not sets:
        return RedirectResponse("/?msg=" + quote(tr("No exam set has versions yet, so there are no statistics.")),
                                status_code=303)
    with_scans = [s for s in sets if s["n_scans"]]
    return RedirectResponse(f"/sets/{(with_scans or sets)[0]['id']}/stats", status_code=303)


@app.get("/sets/{set_id}/class-stats", response_class=HTMLResponse)
def class_stats_page(request: Request, set_id: str, course: str | None = None):
    """Statistics for students: the exam as the class did it, with no names, numbers or marks."""
    exam = _exam_or_404(set_id)
    course = _course_default(exam, course)     # the set's class goes through the same check below
    settings = _stats_settings()
    all_scans = db.list_scans(set_id)
    courses = db.students_courses()
    # Only courses that cannot be subtracted to reveal a small group: the course itself and the rest
    # of the class must both have 0 or at least CLASS_VIEW_MIN scored sheets, and the courses left
    # out must not add up to 1..CLASS_VIEW_MIN-1 (all students minus the courses shown).
    n_all = statsmod.compute(exam, all_scans, {}, settings)["n_scored"]
    allowed = []
    for o in statsmod.course_options(all_scans, courses):
        n = statsmod.compute(exam, statsmod.filter_by_course(all_scans, courses, o["value"]), {}, settings)["n_scored"]
        if n >= statsmod.CLASS_VIEW_MIN and (n_all - n == 0 or n_all - n >= statsmod.CLASS_VIEW_MIN):
            allowed.append(dict(o, n=n))
    left_out = n_all - sum(o["n"] for o in allowed)
    if 0 < left_out < statsmod.CLASS_VIEW_MIN:
        allowed = []
    if course and course not in {o["value"] for o in allowed}:
        course = ""                                 # a course that is not offered shows all students
    scans = statsmod.filter_by_course(all_scans, courses, course) if course else all_scans
    full = statsmod.compute(exam, scans, {}, settings)     # no names passed in at all
    return templates.TemplateResponse(request, "class_stats.html", {
        "exam": exam, "d": statsmod.class_view(exam, full), "course": course,
        "class_empty": "" if course else _class_empty(exam, all_scans),
        "course_label": statsmod.course_label(course, courses) if course else "",
        "courses": allowed, "sets": _stats_sets()})


@app.get("/sets/{set_id}/questions/{qno}", response_class=HTMLResponse)
def question_page(request: Request, set_id: str, qno: int, course: str | None = None):
    """One question (original numbering): which students picked each answer, with their results."""
    exam = _exam_or_404(set_id)
    course = _course_default(exam, course)
    if not 1 <= qno <= len(exam.get("questions", [])):
        raise HTTPException(404, "question not found")
    scans, _, label = _course_scans(set_id, course)
    d = statsmod.question_detail(exam, scans, db.students_get(), _stats_settings(), qno - 1)
    return templates.TemplateResponse(request, "question.html", {"exam": exam, "d": d, "course": course,
                                                                  "course_label": label, "emails": db.students_emails(),
                                                                  "class_empty": "" if course else _class_empty(exam)})


@app.get("/progress", response_class=HTMLResponse)
def progress_page(request: Request, course: str | None = None):
    """The same students over several MCQs: who missed which one and how the results moved."""
    picked = request.query_params.getlist("sets")
    submitted = request.query_params.get("picked") == "1"    # the form was sent (maybe with nothing ticked)
    usable = [s for s in sorted(db.list_sets(), key=lambda s: s["created"]) if s["n_scans"] and s["n_versions"]]
    for s in usable:
        s["group"] = statsmod.set_group(s)
    if not picked and usable and not submitted:   # first visit: the MCQs of the same module as the latest one
        g = usable[-1]["group"]
        picked = [s["id"] for s in usable if s["group"] == g]
    chosen = [s for s in usable if s["id"] in picked]
    exams = [e for e in (db.get_set(s["id"]) for s in chosen) if e]
    scans_by_set = {e["id"]: db.list_scans(e["id"]) for e in exams}
    courses = db.students_courses()
    if course is None:      # no course asked for: the class of the MCQs when they all have the same one
        keys = {statsmod.course_key(_exam_class(e)) for e in exams}
        course = _course_default(exams[0], None, fallback=False) if len(keys) == 1 and "" not in keys else ""
    d = statsmod.progress(exams, scans_by_set, db.students_get(), courses, course, _stats_settings())
    return templates.TemplateResponse(request, "progress.html", {
        "usable": usable, "picked": [s["id"] for s in chosen], "d": d, "course": course,
        "emails": db.students_emails(),
        "course_list": sorted(statsmod.course_names(courses).values(), key=str.casefold),
        "no_course": statsmod.NO_COURSE,
        "set_class": {e["id"]: _exam_class(e) for e in exams},
    })


def _last_scan_payload(set_id: str | None) -> dict:
    s = db.last_scan(set_id or None)
    st = db.settings_get()
    if not s:
        return {"scan_id": None}
    exam = db.get_set(s["set_id"]) if s["set_id"] else None
    pct = round(100.0 * s["score"] / s["max_score"], 1) if s.get("max_score") else None
    return {
        "scan_id": s["id"], "seq": db.scan_seq(s), "set_id": s["set_id"], "set_title": exam["meta"].get("title") if exam else None,
        "student_no": s["student_no"], "name": db.students_get().get(s["student_no"] or "", ""),
        "score": s["score"], "max_score": s["max_score"], "percent": pct, "status": s["status"],
        "created": s["created"], "flags": s["flags"],
        "show_name": bool(st.get("reveal_show_name", True)),
        "show_percent": bool(st.get("reveal_show_percent", True)),
        "show_score": bool(st.get("reveal_show_score", True)),
        "hide_after": int(st.get("reveal_hide_after") or 0),
    }


@app.get("/api/last_scan")
def api_last_scan(set_id: str = ""):
    return JSONResponse(_last_scan_payload(set_id or None))


@app.get("/reveal", response_class=HTMLResponse)
def reveal_any(request: Request):
    return templates.TemplateResponse(request, "reveal.html", {"exam": None})


@app.get("/sets/{set_id}/reveal", response_class=HTMLResponse)
def reveal_set(request: Request, set_id: str):
    exam = _exam_or_404(set_id)
    return templates.TemplateResponse(request, "reveal.html", {"exam": exam})


# ---------------------------------------------------------------------------
# scans
# ---------------------------------------------------------------------------
@app.get("/scans/{scan_id}", response_class=HTMLResponse)
def scan_page(request: Request, scan_id: int):
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404)
    exam = db.get_set(scan["set_id"]) if scan["set_id"] else None
    sets = db.list_sets()
    version = _version(exam, scan["version"]) if exam else None
    opts = []
    key = []
    if exam and version:
        opts = [len(exam["questions"][q["q"]]["choices"]) for q in version["questions"]]
        key = answer_key(version)
    raw = json.loads(scan.get("raw") or "{}")
    return templates.TemplateResponse(request, "review.html", {
        "scan": scan, "seq": db.scan_seq(scan), "exam": exam, "sets": sets, "version": version, "opts": opts, "key": key,
        "name": db.students_get().get(scan["student_no"] or "", ""),
        "darkness": raw.get("darkness", []), "id_darkness": raw.get("id_darkness", []),
        "no_pattern": _student_no_pattern(exam),
    })


def _student_no_pattern(exam: dict | None) -> str:
    """A complete student number of this exam set as a regular expression for the review page, which saves
    the number as soon as it is complete (and otherwise only when the field is left). "" = not known."""
    if not exam:
        return ""
    prefix = str(exam["settings"].get("id_prefix") or "")
    digits = int(exam["settings"].get("id_digits") or 0)
    if not digits:
        return ""
    return "^\\s*" + "\\s*".join(re.escape(ch) for ch in prefix) + "(\\s*\\d){" + str(digits) + "}\\s*$"


@app.post("/scans/{scan_id}")
async def scan_update(request: Request, scan_id: int):
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404)
    form = await request.form()
    set_id = form.get("set_id") or scan["set_id"]
    exam = db.get_set(set_id) if set_id else None
    try:
        vno = int(form.get("version") or 0)
    except ValueError:
        vno = 0
    version = _version(exam, vno) if exam else None
    student_no = (form.get("student_no") or "").strip().upper().replace(" ", "")
    fields = {"student_no": student_no, "set_id": set_id, "version": vno or None}
    if exam and version:
        key = answer_key(version)
        answers = []
        for p in range(len(key)):
            a = (form.get(f"q{p}") or "").strip().upper()
            answers.append(a if a in ("*",) or (len(a) == 1 and a in L.LETTERS) else "")
        fields.update(answers=answers, score=score_answers(answers, key), max_score=len(key))
    flags = [f for f in scan["flags"] if not f.startswith("corrected")]
    flags.append("corrected by hand")
    fields.update(status="ok" if (exam and version and student_no and "?" not in student_no) else "review",
                  flags=flags)
    db.update_scan(scan_id, **fields)
    if _autosave(request):
        # the review page saves each correction by itself and shows the new score and status in place
        new = db.get_scan(scan_id)
        name = db.students_get().get(new["student_no"] or "", "")
        return JSONResponse({
            "score": new["score"], "max_score": new["max_score"], "status": new["status"],
            "status_label": {"ok": tr("ok"), "review": tr("review"), "failed": tr("failed")}.get(new["status"], new["status"]),
            "flags": " · ".join(i18n.flag(f) for f in new["flags"]),
            "label": (tr("Student number (%(name)s)", name=name) if name else tr("Student number")),
            # another set or version: the answer boxes on the page belong to the old one
            "reload": set_id != scan["set_id"] or (vno or None) != scan["version"],
        })
    target = f"/sets/{set_id}#results" if set_id else "/"
    return RedirectResponse(target, status_code=303)


@app.get("/scans/{scan_id}/sheet", response_class=HTMLResponse)
def sheet_page(request: Request, scan_id: int):
    """The student's page: the sheet with every question marked right / wrong / blank / several."""
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404)
    exam = db.get_set(scan["set_id"]) if scan["set_id"] else None
    version = _version(exam, scan["version"]) if exam else None
    if not (exam and version and len(scan["answers"]) == len(version["questions"])):
        # nothing to mark yet (unknown set or version, or the photo could not be read): the review page
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)
    lay, _ = sheet_info(exam, version)
    st = _stats_settings()
    d = sheetview.build(exam, version, lay, scan, db.students_get(), st)
    superseded = any(s["id"] == scan_id and s["superseded"] for s in db.list_scans(exam["id"]))
    return templates.TemplateResponse(request, "sheet.html", {"exam": exam, "d": d, "superseded": superseded,
                                                               "seq": db.scan_seq(scan),
                                                               "cols": sheetview.colours(st),
                                                               "ind": _student_stats(exam, scan, st)})


def _student_stats(exam: dict, scan: dict, st: dict) -> dict:
    """The individual statistics of one scan (student's page and PDF report)."""
    courses = db.students_courses()
    history = statsmod.student_history(db.list_sets(), scan.get("student_no") or "", db.list_scans, db.get_set,
                                       exam["id"], courses)
    # an exam set with a class: a student of the class is compared with the class only, anybody else with all
    scans, scope = statsmod.class_scans(exam, db.list_scans(exam["id"]), scan.get("student_no"), courses)
    ind = statsmod.student_detail(exam, scans, db.students_get(), st, scan, history)
    if scope:
        ind.update(class_scope=scope, exam_class=_exam_class(exam))
    return ind


def _sheet_bundle(scan_id: int):
    """(scan, exam, version, d, photo, overlay) for the full-size view, the marked image and the PDF."""
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404)
    exam = db.get_set(scan["set_id"]) if scan["set_id"] else None
    version = _version(exam, scan["version"]) if exam else None
    if not (exam and version and len(scan["answers"]) == len(version["questions"]) and scan.get("overlay")):
        raise HTTPException(404, "this scan has no marked sheet (use the review page)")
    lay, _ = sheet_info(exam, version)
    st = _stats_settings()
    d = sheetview.build(exam, version, lay, scan, db.students_get(), st)
    overlay = cv2.imread(str(db.SCAN_DIR / scan["overlay"]))
    photo = cv2.imread(str(db.SCAN_DIR / scan["image"])) if scan.get("image") else None
    if overlay is None:
        raise HTTPException(404, "the image of this scan is missing")
    return scan, exam, version, d, photo, overlay, sheetview.colours(st)


@app.get("/scans/{scan_id}/sheet/full", response_class=HTMLResponse)
def sheet_full(request: Request, scan_id: int):
    scan, exam, version, d, photo, overlay, cols = _sheet_bundle(scan_id)
    return templates.TemplateResponse(request, "sheet_full.html", {"exam": exam, "d": d, "cols": cols})


@app.get("/scans/{scan_id}/marked.jpg")
def sheet_marked(scan_id: int):
    scan, exam, version, d, photo, overlay, cols = _sheet_bundle(scan_id)
    img = sheetview.render_marked(overlay, d["rows"], cols)
    return Response(encode_jpeg(img, 85), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/scans/{scan_id}/report.pdf")
def sheet_report(scan_id: int):
    scan, exam, version, d, photo, overlay, cols = _sheet_bundle(scan_id)
    data = reportmod.build_report(d, exam, photo, overlay, cols, ind=_student_stats(exam, scan, _stats_settings()))
    name = f"{d['student_no']}_{exam['id']}_report.pdf".replace("?", "x")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


def _set_one_answer(scan_id: int, pos: int, letter: str):
    """One click on the student's page: set one position to a letter or to blank ("") and rescore.
    The scan is marked as corrected by hand, like on the review page."""
    scan = db.get_scan(scan_id)
    if not scan:
        raise HTTPException(404)
    exam = db.get_set(scan["set_id"]) if scan["set_id"] else None
    version = _version(exam, scan["version"]) if exam else None
    if not (exam and version and 1 <= pos <= len(scan["answers"]) == len(version["questions"])):
        raise HTTPException(400, "nothing to correct here")
    n_opts = len(version["questions"][pos - 1]["choices"])
    if letter and (letter not in L.LETTERS[:n_opts] or len(letter) != 1):
        raise HTTPException(400, "not a choice of this question")
    answers = list(scan["answers"])
    answers[pos - 1] = letter
    key = answer_key(version)
    flags = [f for f in scan["flags"] if not f.startswith("corrected")]
    flags.append("corrected by hand")
    student_no = scan["student_no"] or ""
    # ok only when nothing else on the sheet still needs a look: every other position the reader
    # flagged (weak, nearly double, several marks) has been changed by hand since it was read
    raw = json.loads(scan.get("raw") or "{}")
    read = raw.get("answers") or []
    flagged = set(raw.get("weak") or []) | set(raw.get("check") or []) | set(raw.get("multiple") or [])
    for f in scan["flags"]:                   # older scans: the positions are only in the flag texts
        if f.startswith(("weak", "several marks", "a second bubble")):
            m = re.search(r"question\(s\) ([\d, ]+)", f)
            if m:
                flagged |= {int(x) for x in re.findall(r"\d+", m.group(1))}
    # still open: not this one, and not changed by hand since it was read (unknown counts as open)
    open_left = [p for p in flagged if p != pos and 0 < p <= len(answers)
                 and (p - 1 >= len(read) or answers[p - 1] == read[p - 1])]
    done = bool(student_no) and "?" not in student_no and not open_left
    db.update_scan(scan_id, answers=answers, score=score_answers(answers, key), max_score=len(key), flags=flags,
                   status="ok" if done else "review")
    return RedirectResponse(f"/scans/{scan_id}/sheet#pos-{pos}", status_code=303)


@app.post("/scans/{scan_id}/blank/{pos}")
def scan_blank(scan_id: int, pos: int):
    """Count a weak-or-no mark as blank (it was nothing)."""
    return _set_one_answer(scan_id, pos, "")


@app.post("/scans/{scan_id}/answer/{pos}")
def scan_answer(scan_id: int, pos: int, letter: str = Form(...)):
    """Count a faint mark that was read as blank as the letter it is (it was a light but real mark)."""
    return _set_one_answer(scan_id, pos, letter.strip().upper())


@app.post("/scans/{scan_id}/delete")
def scan_delete(scan_id: int):
    scan = db.get_scan(scan_id)
    if scan:
        db.delete_scan(scan_id)
        if scan["set_id"]:
            return RedirectResponse(f"/sets/{scan['set_id']}#results", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/scan", response_class=HTMLResponse)
def scan_web(request: Request):
    return templates.TemplateResponse(request, "scan.html", {})


# ---------------------------------------------------------------------------
# API for the phone
# ---------------------------------------------------------------------------
@app.get("/api/ping")
def api_ping():
    return {"ok": True, "app": "MCQ-OMR", "sets": len(db.list_sets())}


ZIP_MAX_BYTES = 400 * 1024 * 1024
ZIP_MAX_FILES = 1000
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


@app.post("/api/scan_zip")
async def api_scan_zip(file: UploadFile = File(...)):
    """A zip of photos (e.g. copied from a phone or a camera): every image inside is read as one
    sheet, in file-name order. Folders inside the zip are fine; anything that is not an image is skipped."""
    import zipfile

    data = await file.read()
    if len(data) > ZIP_MAX_BYTES:
        return JSONResponse({"ok": False, "error": "zip larger than 400 MB"}, status_code=400)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "error": "not a zip file"}, status_code=400)
    names = sorted(n for n in zf.namelist()
                   if n.lower().endswith(IMAGE_EXT) and not n.startswith("__MACOSX") and not os.path.basename(n).startswith("."))
    if not names:
        return JSONResponse({"ok": False, "error": "no photos (jpg/png) in the zip"}, status_code=400)
    results = []
    for n in names[:ZIP_MAX_FILES]:
        try:
            r = process_photo(zf.read(n))
        except ValueError as e:
            r = {"ok": False, "status": "failed", "flags": [str(e)]}
        r["filename"] = os.path.basename(n)
        results.append(r)
    return JSONResponse({"ok": True, "n": len(results), "skipped": len(names) - len(results), "results": results})


@app.post("/api/scan")
async def api_scan(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        return JSONResponse({"ok": False, "status": "failed", "flags": ["empty upload"]}, status_code=400)
    try:
        result = process_photo(data)
    except ValueError as e:
        return JSONResponse({"ok": False, "status": "failed", "flags": [str(e)]}, status_code=400)
    return JSONResponse(result)
