"""MCQ-OMR desktop launcher: what the installed app runs when someone double-clicks it.

It starts the server, opens the browser at it, and shows a small window with the address for this
computer, the address to type into the phone app, and buttons to open the browser or the data
folder and to stop. Closing the window stops the server.

  * The data (exams, scans, students, settings) lives in a per-user folder, never inside the app,
    so updating or uninstalling the app leaves it alone:
        Windows  %APPDATA%\\MCQ-OMR
        macOS    ~/Library/Application Support/MCQ-OMR
        Linux    $XDG_DATA_HOME/mcq-omr  (usually ~/.local/share/mcq-omr)
    An existing MCQ_DATA environment variable wins, as with run.py.
  * Port 8000 (what the phone app expects) unless it is taken, then the next free one up to 8019
    (MCQ_PORT changes the starting port).
  * Opening the app a second time just opens the browser at the copy already running.
  * The window's language: the one chosen in Settings (or in the window's Language drop-down), else
    the computer's language. The Windows installer may leave initial-language.txt (e.g. "pt_BR")
    in the data folder: on the first start it becomes the setting and the file is removed.

Developers can run it from the server folder too:   python desktop.py   (needs Tk; without Tk it
runs without the window and stops with Ctrl+C).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "MCQ-OMR"
FIRST_PORT = int(os.environ.get("MCQ_PORT") or 8000)   # 8000 is what the phone app expects
LAST_PORT = FIRST_PORT + 19


def user_data_dir() -> Path:
    if os.environ.get("MCQ_DATA"):
        return Path(os.environ["MCQ_DATA"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "mcq-omr"


DATA = user_data_dir()
os.environ["MCQ_DATA"] = str(DATA)          # before app/omr are imported: DATA_DIR is fixed at import
DATA.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA / "mcq-omr.log"

# A windowed build has no console: sys.stdout / sys.stderr are None and anything that writes to them
# (uvicorn's logging) would crash. Send all of it to a log file in the data folder instead.
CONSOLE = sys.stdout is not None and sys.stderr is not None
if not CONSOLE:
    _log = open(LOG_FILE, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

_handlers: list[logging.Handler] = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if CONSOLE:
    _handlers.append(logging.StreamHandler(sys.stderr))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=_handlers)
log = logging.getLogger("mcq-omr")

if not getattr(sys, "frozen", False):
    # From source: make `import app` work from any folder. (A PyInstaller build already has its
    # import path right; putting the bundle folder first there breaks OpenCV's loader on macOS.)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from omr import i18n
    from omr.i18n import tr
except Exception:                          # still able to show an error message, in English
    i18n = None

    def tr(text: str, **values) -> str:
        return text % values if values else text

INITIAL_LANGUAGE_FILE = DATA / "initial-language.txt"     # written by the Windows installer


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))    # nothing is sent (UDP connect); this only picks the network interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ours_running(port: int) -> bool:
    """True if MCQ-OMR already answers on this port (a copy opened earlier)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode()).get("app") == APP_NAME
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_port() -> tuple[int, bool]:
    """(port, already_running)."""
    for p in range(FIRST_PORT, LAST_PORT + 1):
        if ours_running(p):
            return p, True
        if port_free(p):
            return p, False
    raise SystemExit(f"No free port between {FIRST_PORT} and {LAST_PORT}.")


def open_folder(path: Path):
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 (Windows only)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("could not open %s", path)


class ServerThread:
    def __init__(self, port: int):
        import uvicorn

        from app import app                 # imported only now, after MCQ_DATA is set
        self.port = port
        # log_config=None: uvicorn uses the logging set up above instead of its own console handlers
        self.server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port,
                                                    log_config=None, log_level="info"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 60.0) -> bool:
        self.thread.start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.server.started:
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.1)
        return False

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


# ---------------------------------------------------------------------------
# language of the window
# ---------------------------------------------------------------------------
def os_language_tags() -> list[str]:
    """The computer's interface languages, most preferred first (tags like 'pt-BR' or 'pt_BR.UTF-8')."""
    tags: list[str] = []
    if sys.platform == "darwin":
        # apps opened from the Finder get no LANG: ask the system's language list
        for args in (["defaults", "read", "-g", "AppleLanguages"], ["defaults", "read", "-g", "AppleLocale"]):
            try:
                out = subprocess.run(args, capture_output=True, text=True, timeout=3).stdout
            except Exception:
                continue
            tags += re.findall(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*", out)
    if sys.platform == "win32":
        try:
            import ctypes
            import locale
            name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            if name:
                tags.append(name)
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        tags += [t for t in os.environ.get(var, "").split(":") if t]
    try:
        import locale
        loc = locale.getlocale()[0]
        if loc:
            tags.append(loc)
    except Exception:
        pass
    return [t for t in tags if t.split(".")[0].upper() not in ("C", "POSIX")]


def stored_language() -> str | None:
    """The language stored in Settings ("" = automatic), or None if none was ever stored."""
    from omr import db
    with db.connect() as c:
        row = c.execute("SELECT 1 FROM settings WHERE key = 'language'").fetchone()
    return (db.settings_get().get("language") or "") if row else None


def apply_initial_language():
    """First start after the Windows installer: its language choice becomes the setting."""
    if not INITIAL_LANGUAGE_FILE.exists():
        return
    try:
        from omr import db
        db.init()
        code = INITIAL_LANGUAGE_FILE.read_text(encoding="utf-8-sig").strip()
        if stored_language() is None and code in i18n.LANGUAGES:
            db.settings_set(language=code)
            log.info("interface language from the installer: %s", code)
        INITIAL_LANGUAGE_FILE.unlink()
    except Exception:
        log.exception("could not use %s", INITIAL_LANGUAGE_FILE)


def window_language() -> str:
    """The language set in Settings, or (automatic) the computer's language."""
    try:
        from omr import db
        code = db.settings_get().get("language") or ""
    except Exception:
        code = ""
    return code if code in i18n.LANGUAGES else i18n.match(",".join(os_language_tags()))


# ---------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------
class LauncherWindow:
    """The small window. Its contents are built by build(), and built again in the new language when
    the Language drop-down changes; the status line, a running backup and the server watch carry on."""

    def __init__(self, srv: ServerThread, local_url: str, phone_url: str):
        import tkinter as tk

        self.srv, self.local_url, self.phone_url = srv, local_url, phone_url
        self.root = tk.Tk()
        self.root.resizable(False, False)
        self.status_text = tk.StringVar(master=self.root, value="")
        self.status_colour = "#666"
        self.busy = False
        self.stopped = False
        self.closing = False
        self.frm = None
        self.status = None
        self.lang_box = None
        self.lang_codes: list[str] = []
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.root.after(2000, self.watch)

    def _title(self):
        if self.closing:
            self.root.title(tr("%(app)s - stopping…", app=APP_NAME))
        elif self.stopped:
            self.root.title(tr("%(app)s - stopped", app=APP_NAME))
        else:
            self.root.title(APP_NAME)

    def build(self):
        """(Re)build every text of the window in the current language."""
        from tkinter import ttk

        i18n.set_language(window_language())
        if self.frm is not None:
            self.frm.destroy()
        self._title()
        frm = self.frm = ttk.Frame(self.root, padding=18)
        frm.grid()

        ttk.Label(frm, text=tr("MCQ-OMR is running"), font=("TkDefaultFont", 14, "bold")).grid(
            column=0, row=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=tr("Keep this window open while you use it. Closing it stops MCQ-OMR.")).grid(
            column=0, row=1, columnspan=2, sticky="w", pady=(2, 12))

        def address_row(row: int, label: str, value: str):
            ttk.Label(frm, text=label).grid(column=0, row=row, sticky="w", padx=(0, 10))
            e = ttk.Entry(frm, width=34)
            e.insert(0, value)
            e.configure(state="readonly")       # selectable and copyable, not editable
            e.grid(column=1, row=row, sticky="w", pady=2)

        address_row(2, tr("On this computer:"), self.local_url)
        address_row(3, tr("In the phone app:"), self.phone_url)
        ttk.Label(frm, text=tr("The phone must be on the same network (Wi-Fi) as this computer."),
                  foreground="#666").grid(column=0, row=4, columnspan=2, sticky="w", pady=(2, 12))

        btns = ttk.Frame(frm)
        btns.grid(column=0, row=5, columnspan=2, sticky="w")
        ttk.Button(btns, text=tr("Open in browser"), command=lambda: webbrowser.open(self.local_url)).grid(column=0, row=0)
        ttk.Button(btns, text=tr("Open data folder"), command=lambda: open_folder(DATA)).grid(column=1, row=0, padx=6)
        ttk.Button(btns, text=tr("Stop and quit"), command=self.quit_app).grid(column=2, row=0)

        # backups: the same .zip as Settings > Backup in the browser
        bk = ttk.Frame(frm)
        bk.grid(column=0, row=6, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(bk, text=tr("Back up…"), command=self.do_backup).grid(column=0, row=0)
        ttk.Button(bk, text=tr("Restore a backup…"), command=self.do_restore).grid(column=1, row=0, padx=6)
        self.status = ttk.Label(frm, textvariable=self.status_text, foreground=self.status_colour, wraplength=460,
                                justify="left")
        self.status.grid(column=0, row=7, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(frm, text=tr("Data folder: %(folder)s", folder=DATA), foreground="#666").grid(
            column=0, row=8, columnspan=2, sticky="w", pady=(12, 0))

        # language of the window (and of the pages: the same setting as in Settings in the browser)
        lang = ttk.Frame(frm)
        lang.grid(column=0, row=10, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(lang, text=tr("Language:")).grid(column=0, row=0, padx=(0, 6))
        self.lang_codes = [""] + list(i18n.LANGUAGES)
        names = [tr("Automatic")] + list(i18n.LANGUAGES.values())
        self.lang_box = ttk.Combobox(lang, values=names, state="readonly", width=24)
        try:
            stored = stored_language() or ""
        except Exception:
            stored = ""
        self.lang_box.current(self.lang_codes.index(stored) if stored in self.lang_codes else 0)
        self.lang_box.grid(column=1, row=0)
        self.lang_box.bind("<<ComboboxSelected>>", lambda e: self.choose_language(self.lang_box.current()))

        if self.stopped:
            self._show_stopped()

    def choose_language(self, index: int):
        """The Language drop-down: save the choice and rebuild the window in that language."""
        from omr import db
        code = self.lang_codes[index] if 0 <= index < len(self.lang_codes) else ""
        db.settings_set(language=code)
        self.root.after_idle(self.build)          # not from inside the drop-down's own event

    def set_status(self, text: str, colour: str):
        self.status_text.set(text)
        self.status_colour = colour
        if self.status is not None:
            self.status.configure(foreground=colour)

    def quit_app(self):
        self.closing = True
        self._title()
        self.root.update_idletasks()
        self.srv.stop()
        self.root.destroy()

    def in_background(self, work, done_text):
        """Run work() in a thread (in the window's language, for the error messages); show
        done_text(result) or the error in the status line."""
        from tkinter import messagebox
        self.busy = True
        box = {}
        ctx = contextvars.copy_context()      # a new thread would start in English

        def run():
            try:
                box["result"] = ctx.run(work)
            except Exception as exc:          # BackupError messages are written for the person
                box["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.root.after(300, poll)
                return
            self.busy = False
            if "error" in box:
                log.error("backup/restore: %s", box["error"])
                self.set_status(str(box["error"]), "#b00")
                messagebox.showerror(APP_NAME, str(box["error"]), parent=self.root)
            else:
                self.set_status(done_text(box["result"]), "#2e7d32")

        poll()

    def do_backup(self):
        if self.busy:
            return
        from tkinter import filedialog

        from omr import backup
        path = filedialog.asksaveasfilename(parent=self.root, title=tr("Save a backup"), defaultextension=".zip",
                                            initialfile=backup.default_name(), filetypes=[(tr("Backup"), "*.zip")])
        if not path:
            return
        self.set_status(tr("Making the backup…"), "#666")
        self.in_background(lambda: backup.create(path),
                           lambda m: tr("Backup saved: %(path)s (%(what)s)", path=path, what=backup.describe(m)))

    def do_restore(self):
        if self.busy:
            return
        from tkinter import filedialog, messagebox

        from omr import backup
        path = filedialog.askopenfilename(parent=self.root, title=tr("Restore a backup"),
                                          filetypes=[(tr("Backup"), "*.zip")])
        if not path:
            return
        try:
            manifest = backup.inspect(path)
        except backup.BackupError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return
        if not messagebox.askyesno(APP_NAME, tr("Replace all current data with this backup?\n\n"
                                                "Backup %(what)s.\n\n"
                                                "The current data is saved first in the backups folder of the data folder.",
                                                what=backup.describe(manifest)),
                                   icon="warning", parent=self.root):
            return
        self.set_status(tr("Restoring…"), "#666")
        self.in_background(lambda: backup.restore(path),
                           lambda r: tr("Backup restored. Reload the page in the browser. The data you had before is in "
                                        "%(path)s", path=r["safety"]))

    def _show_stopped(self):
        from tkinter import ttk
        ttk.Label(self.frm, text=tr("The server stopped unexpectedly. Details: %(log)s", log=LOG_FILE),
                  foreground="#b00").grid(column=0, row=9, columnspan=2, sticky="w", pady=(8, 0))

    def watch(self):                          # if the server dies, say so instead of looking alive
        if not self.srv.thread.is_alive():
            self.stopped = True
            self._title()
            self._show_stopped()
            return
        self.root.after(2000, self.watch)

    def run(self):
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))
        self.root.mainloop()


def run_window(srv: ServerThread, local_url: str, phone_url: str):
    LauncherWindow(srv, local_url, phone_url).run()


def show_error(msg: str):
    log.error(msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, msg)
        r.destroy()
    except Exception:
        print(msg, file=sys.stderr)


def main():
    no_browser = "--no-browser" in sys.argv
    headless = "--headless" in sys.argv
    port, running = pick_port()
    local_url = f"http://localhost:{port}"
    if running:                              # second launch: just show the one already running
        log.info("already running on port %s, opening the browser", port)
        if not no_browser:
            webbrowser.open(local_url)
        return
    log.info("starting on port %s, data in %s", port, DATA)
    if i18n is not None:                     # the language of the window and messages, on this (main) thread
        apply_initial_language()
        try:
            from omr import db
            db.init()
            i18n.set_language(window_language())
        except Exception:
            log.exception("could not choose the language")
    try:
        srv = ServerThread(port)
    except Exception:
        log.exception("could not load the server")
        show_error(tr("MCQ-OMR could not start. Details are in:\n%(log)s", log=LOG_FILE))
        sys.exit(1)
    if not srv.start():
        show_error(tr("MCQ-OMR could not start on port %(port)s. Details are in:\n%(log)s", port=port, log=LOG_FILE))
        sys.exit(1)
    phone_url = f"http://{lan_ip()}:{port}"
    print("=" * 60)
    print(f"  On this computer:   {local_url}")
    print(f"  Phone app server:   {phone_url}")
    print(f"  Data folder:        {DATA}")
    print("=" * 60)
    if not no_browser:
        webbrowser.open(local_url)
    if not headless:
        try:
            run_window(srv, local_url, phone_url)
            return
        except Exception as exc:              # no Tk (or no display): keep running without the window
            # an installed build always has Tk, so there it is an error (the build check looks for it)
            (log.error if getattr(sys, "frozen", False) else log.info)(
                "no window (%s); running until stopped with Ctrl+C", exc)
    try:
        while srv.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    srv.stop()


if __name__ == "__main__":
    main()
