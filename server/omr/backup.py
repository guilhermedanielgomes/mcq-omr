"""Backups: everything MCQ-OMR keeps (exam sets, scans and their photos, students, courses, settings
and the uploaded logo) in one .zip file, and restoring such a file, on this or another computer.

A backup holds:
    manifest.json   what it is, when it was made, how many of everything
    mcq.sqlite3     the database (a consistent copy, even while the server is in use)
    logo.png        the uploaded header image, if there is one
    scans/...       the photos and the "as read" images

Restoring first saves the current data as a backup in <data>/backups/ (the last few are kept), then
replaces it. A file that is not a complete MCQ-OMR backup is refused before anything is changed.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zlib
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__, db
from .i18n import tr

FORMAT = 1
MANIFEST = "manifest.json"
DB_NAME = "mcq.sqlite3"
LOGO_NAME = "logo.png"
SAFETY_DIR = "backups"            # inside the data folder; never itself put into a backup
KEEP_SAFETY = 5                   # automatic "before restore" backups kept
MAX_FILES = 200_000
MAX_TOTAL_BYTES = 20 * 1024 ** 3  # uncompressed
_SCAN_NAME = re.compile(r"^scans/[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
_REQUIRED = {                      # columns the app reads; a backup without them is refused
    "sets": {"id", "created", "data"},
    "scans": {"id", "set_id", "version", "student_no", "answers", "score", "max_score", "status", "flags",
              "image", "overlay", "created", "raw"},
    "students": {"student_no", "name", "course", "email", "email_custom"},
    "settings": {"key", "value"},
}
_RESTORE_LOCK = threading.Lock()   # one restore at a time (browser and app window share the data)
_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError, OSError)


class BackupError(Exception):
    """A problem to show to the person (the message is written for them)."""


def default_name() -> str:
    return f"mcq-omr-backup-{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"


def _counts(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        out = {}
        for table in ("sets", "scans", "students"):
            try:
                out[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                out[table] = 0
        return out
    finally:
        con.close()


def create(dest: Path | str) -> dict:
    """Write a backup of the current data to `dest` (a .zip path). Returns the manifest."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with db.connect():                                  # makes sure the data folder and database exist
        pass
    with tempfile.TemporaryDirectory(prefix="mcq-backup-") as tmp:
        snap = Path(tmp) / DB_NAME
        src = sqlite3.connect(db.DB_PATH)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)                             # consistent copy even with the server running
        finally:
            dst.close()
            src.close()
        photos = sorted(p for p in db.SCAN_DIR.iterdir() if p.is_file()) if db.SCAN_DIR.exists() else []
        logo = db.DATA_DIR / LOGO_NAME
        manifest = {
            "app": "MCQ-OMR", "format": FORMAT, "app_version": __version__,
            "created": datetime.now().isoformat(timespec="seconds"),
            "counts": {**_counts(snap), "photos": len(photos)},
            "has_logo": logo.exists(),
        }
        part = dest.with_name(dest.name + ".part")
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(snap, DB_NAME)
            if logo.exists():
                z.write(logo, LOGO_NAME)
            for p in photos:
                if _SCAN_NAME.match("scans/" + p.name):
                    # photos are JPEGs already: storing them is as small and much faster than deflating
                    z.write(p, "scans/" + p.name, compress_type=zipfile.ZIP_STORED)
        part.replace(dest)
    return manifest


def inspect(path: Path | str) -> dict:
    """Check that `path` is a complete MCQ-OMR backup and return its manifest. Raises BackupError."""
    path = Path(path)
    try:
        return _inspect(path)
    except BackupError:
        raise
    except _ZIP_ERRORS:
        raise BackupError(tr("This backup file is damaged, so it was not used. Nothing was changed.")) from None


def _inspect(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        raise BackupError(tr("This file is not an MCQ-OMR backup (it is not a .zip file)."))
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if MANIFEST not in names or DB_NAME not in names:
            raise BackupError(tr("This .zip file is not an MCQ-OMR backup (it has no database in it)."))
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise BackupError(tr("This backup is larger than MCQ-OMR accepts."))
        for n in names:
            if n in (MANIFEST, DB_NAME, LOGO_NAME, "scans/") or (
                    _SCAN_NAME.match(n) and not _WINDOWS_RESERVED.match(n[6:]) and not n.endswith(".")):
                continue
            raise BackupError(tr("This backup contains a file MCQ-OMR does not expect (%(name)s), so it was not used.",
                                  name=n[:80]))
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BackupError(tr("This backup is damaged (its description cannot be read).")) from None
    if not isinstance(manifest, dict) or manifest.get("app") != "MCQ-OMR":
        raise BackupError(tr("This .zip file is not an MCQ-OMR backup."))
    try:
        fmt = int(manifest.get("format", 0))
    except (TypeError, ValueError):
        raise BackupError(tr("This backup is damaged (its description cannot be read).")) from None
    if fmt > FORMAT:
        raise BackupError(tr("This backup was made by a newer version of MCQ-OMR. Update MCQ-OMR, then try again."))
    return manifest


def _check_db(path: Path):
    con = sqlite3.connect(path)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError(tr("The database in this backup is damaged, so it was not used."))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sets", "scans"} <= tables:
            raise BackupError(tr("The database in this backup is incomplete, so it was not used."))
    except sqlite3.DatabaseError:
        raise BackupError(tr("The database in this backup is damaged, so it was not used.")) from None
    finally:
        con.close()
    try:
        db.init(path)                         # bring an older backup up to date, on the copy only
    except sqlite3.Error:
        raise BackupError(tr("The database in this backup is incomplete, so it was not used.")) from None
    con = sqlite3.connect(path)
    try:
        for table, needed in _REQUIRED.items():
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if not needed <= cols:
                raise BackupError(tr("The database in this backup is incomplete, so it was not used."))
    except sqlite3.DatabaseError:
        raise BackupError(tr("The database in this backup is damaged, so it was not used.")) from None
    finally:
        con.close()


def _prune_safety(folder: Path):
    old = sorted(folder.glob("before-restore-*.zip"))
    for p in old[:-KEEP_SAFETY]:
        p.unlink(missing_ok=True)


def restore(path: Path | str) -> dict:
    """Replace the current data with the backup at `path`. The current data is saved first as
    <data>/backups/before-restore-<time>.zip. Returns {"manifest", "safety"}.
    Everything is checked on an unpacked copy before the current data is touched."""
    if not _RESTORE_LOCK.acquire(blocking=False):
        raise BackupError(tr("Another restore is running. Wait for it to finish, then try again."))
    try:
        return _restore(Path(path))
    finally:
        _RESTORE_LOCK.release()


def _restore(path: Path) -> dict:
    manifest = inspect(path)
    data = db.DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    cleanup_leftovers()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    work = data / f".restore-{stamp}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        # 1. unpack next to the data (same disk, so the photo folder can be swapped in one move)
        try:
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    n = info.filename
                    if n == "scans/" or n == MANIFEST:
                        continue
                    target = work / n                   # names were checked by inspect(): no "..", no paths
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
        except _ZIP_ERRORS:
            raise BackupError(tr("This backup file is damaged, so it was not used. Nothing was changed.")) from None
        (work / "scans").mkdir(exist_ok=True)
        _check_db(work / DB_NAME)                       # also upgrades the copy to the current columns

        # 2. keep the current data, in case this was the wrong file
        safety_dir = data / SAFETY_DIR
        safety = safety_dir / f"before-restore-{stamp}.zip"
        n = 2
        while safety.exists():                # two restores within one second
            safety = safety_dir / f"before-restore-{stamp}-{n}.zip"
            n += 1
        create(safety)
        _prune_safety(safety_dir)

        # 3. swap the photo folder in (renames: all or nothing), then the database; if a later step
        #    fails, the old photos go back, so data and photos never get mixed
        old_scans = data / f".scans-old-{stamp}"
        had_scans = db.SCAN_DIR.exists()
        moved_old = False
        try:
            if had_scans:
                db.SCAN_DIR.replace(old_scans)
                moved_old = True
            (work / "scans").replace(db.SCAN_DIR)
        except OSError:
            if moved_old:
                old_scans.replace(db.SCAN_DIR)
            raise BackupError(tr("The photos could not be replaced (is a photo open in another program?). "
                                 "Nothing was changed.")) from None
        try:
            src = sqlite3.connect(work / DB_NAME)
            dst = sqlite3.connect(db.DB_PATH)
            try:
                src.backup(dst)               # page by page into the live file: open connections stay valid
            finally:
                dst.close()
                src.close()
        except sqlite3.Error:
            new_photos = data / f".scans-new-{stamp}"
            db.SCAN_DIR.replace(new_photos)
            if moved_old:
                old_scans.replace(db.SCAN_DIR)
            shutil.rmtree(new_photos, ignore_errors=True)
            raise BackupError(tr("The database could not be replaced. Nothing was changed.")) from None
        shutil.rmtree(old_scans, ignore_errors=True)
        db.init()                             # nothing left to add (the copy was upgraded), kept for safety
        logo = data / LOGO_NAME
        if (work / LOGO_NAME).exists():
            (work / LOGO_NAME).replace(logo)
        else:
            logo.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"manifest": manifest, "safety": str(safety)}


def describe(manifest: dict) -> str:
    """One line for the person: what is in a backup."""
    c = manifest.get("counts")
    c = c if isinstance(c, dict) else {}
    when = str(manifest.get("created") or "")[:16].replace("T", " ")
    return tr("made %(when)s: %(sets)s exam sets, %(scans)s scanned sheets, %(students)s students, %(photos)s images",
              when=when, sets=c.get("sets", 0), scans=c.get("scans", 0), students=c.get("students", 0),
              photos=c.get("photos", 0))


def cleanup_leftovers():
    """Folders left by a restore that was interrupted (power cut, crash), more than an hour old."""
    for p in [*db.DATA_DIR.glob(".restore-*"), *db.DATA_DIR.glob(".scans-old-*"), *db.DATA_DIR.glob(".scans-new-*")]:
        if p.is_dir() and time.time() - p.stat().st_mtime > 3600:
            shutil.rmtree(p, ignore_errors=True)
