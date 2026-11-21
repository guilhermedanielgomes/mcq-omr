"""SQLite storage. One row per exam set (the whole exam as JSON) and one row per scan."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("MCQ_DATA", Path(__file__).resolve().parent.parent / "data"))
SCAN_DIR = DATA_DIR / "scans"
DB_PATH = DATA_DIR / "mcq.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    id TEXT PRIMARY KEY,
    created TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id TEXT,
    version INTEGER,
    student_no TEXT,
    answers TEXT,
    score INTEGER,
    max_score INTEGER,
    status TEXT,
    flags TEXT,
    image TEXT,
    overlay TEXT,
    created TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS scans_set ON scans(set_id);
CREATE TABLE IF NOT EXISTS students (
    student_no TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path: Path | str | None = None):
    """Create or upgrade the database (the live one, or the file at db_path, e.g. a backup to check)."""
    if db_path is not None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        conn = connect()
    try:
        with conn as c:
            c.executescript(SCHEMA)
            try:
                rows = c.execute("SELECT student_no, name FROM roster").fetchall()
                if rows:
                    c.executemany("INSERT OR IGNORE INTO students(student_no, name) VALUES(?,?)",
                                  [(r["student_no"], r["name"]) for r in rows])
                    c.execute("DROP TABLE roster")
            except sqlite3.OperationalError:
                pass
            # course of each student (optional, added later: older databases get the column here)
            cols = [r["name"] for r in c.execute("PRAGMA table_info(students)").fetchall()]
            if "course" not in cols:
                c.execute("ALTER TABLE students ADD COLUMN course TEXT")
            # email: email_custom 0 = student number @ the domain in Settings (the default), 1 = the email below
            if "email" not in cols:
                c.execute("ALTER TABLE students ADD COLUMN email TEXT")
            if "email_custom" not in cols:
                c.execute("ALTER TABLE students ADD COLUMN email_custom INTEGER NOT NULL DEFAULT 0")
    finally:
        if db_path is not None:
            conn.close()


# ---- exam sets --------------------------------------------------------------
def list_sets() -> list[dict]:
    out = []
    with connect() as c:
        rows = c.execute("SELECT id, created, data FROM sets ORDER BY created DESC").fetchall()
        counts = dict(c.execute("SELECT set_id, COUNT(*) FROM scans GROUP BY set_id").fetchall())
    for r in rows:
        exam = json.loads(r["data"])
        out.append({
            "id": r["id"], "created": r["created"],
            "title": exam.get("meta", {}).get("title", ""),
            "module": exam.get("meta", {}).get("module", ""),
            "n_questions": len(exam.get("questions", [])),
            "n_versions": len(exam.get("versions", [])),
            "n_scans": counts.get(r["id"], 0),
        })
    return out


def get_set(set_id: str) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT id, created, data FROM sets WHERE id=?", (set_id,)).fetchone()
    if not r:
        return None
    exam = json.loads(r["data"])
    exam["id"] = r["id"]
    exam["created"] = r["created"]
    return exam


def save_set(exam: dict):
    exam = dict(exam)
    set_id = exam["id"]
    created = exam.pop("created", None) or now()
    exam.pop("id", None)
    with connect() as c:
        c.execute("INSERT INTO sets(id, created, data) VALUES(?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                  (set_id, created, json.dumps(exam, ensure_ascii=False)))


def delete_set(set_id: str):
    with connect() as c:
        c.execute("DELETE FROM scans WHERE set_id=?", (set_id,))
        c.execute("DELETE FROM sets WHERE id=?", (set_id,))


# ---- scans ------------------------------------------------------------------
def _scan_row(r) -> dict:
    d = dict(r)
    d["answers"] = json.loads(d["answers"] or "[]")
    d["flags"] = json.loads(d["flags"] or "[]")
    return d


def add_scan(**f) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO scans(set_id, version, student_no, answers, score, max_score, status, flags, "
            "image, overlay, created, raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f.get("set_id"), f.get("version"), f.get("student_no"), json.dumps(f.get("answers", [])),
             f.get("score"), f.get("max_score"), f.get("status"), json.dumps(f.get("flags", [])),
             f.get("image"), f.get("overlay"), now(), json.dumps(f.get("raw", {}))))
        return cur.lastrowid


def update_scan(scan_id: int, **f):
    cols, vals = [], []
    for k, v in f.items():
        if k in ("answers", "flags", "raw"):
            v = json.dumps(v)
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(scan_id)
    with connect() as c:
        c.execute(f"UPDATE scans SET {', '.join(cols)} WHERE id=?", vals)


def get_scan(scan_id: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    return _scan_row(r) if r else None


def list_scans(set_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM scans WHERE set_id=? ORDER BY id", (set_id,)).fetchall()
    scans = [_scan_row(r) for r in rows]
    # a student number that appears more than once: the latest scan is the one that counts
    latest = {}
    for s in scans:
        if s["student_no"]:
            latest[s["student_no"]] = s["id"]
    for i, s in enumerate(scans):
        s["superseded"] = bool(s["student_no"]) and latest.get(s["student_no"]) != s["id"]
        s["seq"] = i + 1
    return scans


def scan_seq(scan: dict | None) -> int | None:
    """Number of a scan within its exam set (1, 2, 3...), for display only. The database id stays
    the scan's real identifier (links, the phone app); this one starts again for every set."""
    if not scan or not scan.get("set_id"):
        return None
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM scans WHERE set_id=? AND id<=?",
                         (scan["set_id"], scan["id"])).fetchone()[0]


def delete_scan(scan_id: int):
    s = get_scan(scan_id)
    if not s:
        return
    for key in ("image", "overlay"):
        if s.get(key):
            try:
                (SCAN_DIR / s[key]).unlink()
            except OSError:
                pass
    with connect() as c:
        c.execute("DELETE FROM scans WHERE id=?", (scan_id,))


# ---- students (one list for all exam sets: a student number is unique) -----
def students_get() -> dict[str, str]:
    with connect() as c:
        rows = c.execute("SELECT student_no, name FROM students ORDER BY student_no").fetchall()
    return {r["student_no"]: r["name"] for r in rows}


def students_merge(rows: list[tuple], replace: bool = False, course: str | None = None) -> int:
    """Add or update students. rows = (number, name) or (number, name, email); an email given in the
    list becomes the student's own email (box "number@domain" unticked); without one the student keeps
    whatever email setting they had. course: given = set it for all these rows; None/"" = keep the
    course a student already has. Replace = the new list replaces the old one, but a student still on
    it keeps their course and email."""
    course = (course or "").strip() or None
    rows = [(r[0], r[1], (r[2] if len(r) > 2 else "") or None) for r in rows]
    with connect() as c:
        old = {}
        if replace:
            old = {r["student_no"]: dict(r) for r in c.execute("SELECT student_no, course, email, email_custom FROM students")}
            c.execute("DELETE FROM students")
        data = []
        for no, name, email in rows:
            o = old.get(no, {})
            data.append((no, name, course or o.get("course"), email or o.get("email"),
                         1 if email else (o.get("email_custom") or 0), 1 if email else 0))
        for no, name, crs, email, custom, has_email in data:
            c.execute("INSERT INTO students(student_no, name, course, email, email_custom) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(student_no) DO UPDATE SET name=excluded.name, "
                      "course=COALESCE(excluded.course, students.course)"
                      + (", email=excluded.email, email_custom=1" if has_email else ""),
                      (no, name, crs, email, custom))
    return len(rows)


def students_info() -> dict[str, dict]:
    """{student_no: {name, course, email, email_custom}} for the Settings list."""
    with connect() as c:
        rows = c.execute("SELECT student_no, name, course, email, email_custom FROM students ORDER BY student_no").fetchall()
    return {r["student_no"]: {"name": r["name"], "course": (r["course"] or "").strip(), "email": r["email"] or "",
                              "email_custom": bool(r["email_custom"])} for r in rows}


def students_courses() -> dict[str, str]:
    """{student_no: course} for the students that have one."""
    with connect() as c:
        rows = c.execute("SELECT student_no, course FROM students WHERE course IS NOT NULL AND TRIM(course)!=''").fetchall()
    return {r["student_no"]: r["course"].strip() for r in rows}


def email_domain() -> str:
    return (settings_get().get("email_domain") or "").strip().lstrip("@")


def default_email(student_no: str, domain: str) -> str:
    return f"{student_no}@{domain}" if domain and student_no and "?" not in student_no else ""


def students_emails() -> dict[str, str]:
    """{student_no: email} of the students that have one: their own email, or else (box ticked)
    student number @ the domain set in Settings."""
    domain = email_domain()
    out = {}
    for no, s in students_info().items():
        e = s["email"].strip() if s["email_custom"] else default_email(no, domain)
        if e:
            out[no] = e
    return out


def students_update(student_no: str, course: str, email: str, email_custom: bool):
    with connect() as c:
        c.execute("UPDATE students SET course=?, email=?, email_custom=? WHERE student_no=?",
                  ((course or "").strip() or None, (email or "").strip() or None, 1 if email_custom else 0, student_no))


def students_set_course(student_no: str, course: str):
    with connect() as c:
        c.execute("UPDATE students SET course=? WHERE student_no=?", ((course or "").strip() or None, student_no))


def students_delete(student_no: str):
    with connect() as c:
        c.execute("DELETE FROM students WHERE student_no=?", (student_no,))


def roster_get(set_id: str = "") -> dict[str, str]:
    """Kept for the export module: names are global now."""
    return students_get()


# ---- settings ---------------------------------------------------------------
DEFAULT_SETTINGS = {
    "pass_mark": 40,
    "bands": "70:Excellent|60:Very good|50:Good|40:Pass|0:Fail",   # names shown translated while unchanged
    "reveal_show_name": True,
    "reveal_show_percent": True,
    "reveal_show_score": True,
    "language": "",           # interface language code (see omr/i18n.py); "" = the browser's language
    "reveal_hide_after": 0,   # seconds after which a revealed result hides by itself (0 = only by a tap)
    "email_domain": "",       # students' default email: student number @ this (e.g. student.university.edu)
    "course_list": [],        # the courses offered in the drop-down next to each student in Settings
    "default_lecturer": "",
    "default_institution": "",   # up to 3 lines printed at the top of the paper, set in Settings
    "default_id_prefix": "",     # printed before the boxes, e.g. letters every student number starts with
    "default_id_digits": 6,
    "logo": "default",   # "default" = omr/assets/header.png if present, "custom" = data/logo.png, "none"
    # reading the bubbles: darkness in % from which a bubble counts as filled, and from which a
    # fainter one is still read but flagged as "weak or no mark" (see omr/reader.py)
    "mark_pct": 35,
    "weak_pct": 20,
    # colours of the student's page, the full-size view and the PDF report (visuals only)
    "color_right": "#27a844",
    "color_wrong": "#e03131",
    "color_blank": "#f0a500",
    "color_multiple": "#8e24aa",
}
COLOR_KEYS = ("color_right", "color_wrong", "color_blank", "color_multiple")


def settings_get() -> dict:
    out = dict(DEFAULT_SETTINGS)
    with connect() as c:
        for r in c.execute("SELECT key, value FROM settings").fetchall():
            try:
                out[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                pass
    return out


def settings_set(**values):
    with connect() as c:
        for k, v in values.items():
            c.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, json.dumps(v)))


def parse_bands(text: str) -> list[tuple[float, str]]:
    """'70:First|60:2.1|40:Pass|0:Fail' -> [(70,'First'), (60,'2.1'), ...] sorted high to low."""
    bands = []
    for part in str(text).replace("\n", "|").split("|"):
        if ":" in part:
            a, b = part.split(":", 1)
            try:
                bands.append((float(a.strip()), b.strip()))
            except ValueError:
                pass
    bands.sort(key=lambda t: -t[0])
    return bands or [(float(t), n) for t, n in ((70, "First"), (60, "2.1"), (50, "2.2"), (40, "Pass"), (0, "Fail"))]


def last_scan(set_id: str | None = None) -> dict | None:
    with connect() as c:
        if set_id:
            r = c.execute("SELECT * FROM scans WHERE set_id=? ORDER BY id DESC LIMIT 1", (set_id,)).fetchone()
        else:
            r = c.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    return _scan_row(r) if r else None
