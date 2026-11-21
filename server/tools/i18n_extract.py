"""Collect every translatable text from the code and update the translation files.

    cd server && python tools/i18n_extract.py            # update omr/locales/*.json
    cd server && python tools/i18n_extract.py --check    # only report missing / unused texts

Texts are found in:
  templates/*.html   _("..."), ngettext("...", "...")   (Jinja) and t("...") in inline scripts
  static/*.js        t("...")
  *.py               tr("..."), ntr("...", "..."), N_("...")
Each omr/locales/<code>.json maps the English text to its translation; new texts are added with an
empty translation (shown in English until translated), texts no longer used are removed.
To add a language: add it to LANGUAGES in omr/i18n.py, run this script, translate the new file.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from jinja2 import Environment
from jinja2.ext import extract_from_ast

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
from omr.i18n import LANGUAGES, LOCALE_DIR  # noqa: E402

JS_CALL = re.compile(r"""(?<![\w.$])t\(\s*(?P<q>["'`])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)\s*[,)]""", re.S)
PY_FILES = ["app.py", "desktop.py"] + [str(p.relative_to(SERVER)) for p in (SERVER / "omr").glob("*.py")]


def _js_unescape(s: str) -> str:
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", lambda m: chr(int(m.group(1)[1:], 16)) if m.group(1)[0] == "u"
                  else {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def collect() -> dict[str, set[str]]:
    """{text: {where, ...}}"""
    found: dict[str, set[str]] = {}

    def add(text, where):
        if text and text.strip():
            found.setdefault(text, set()).add(where)

    env = Environment()
    for p in sorted((SERVER / "templates").glob("*.html")):
        src = p.read_text(encoding="utf-8")
        for lineno, func, msg in extract_from_ast(env.parse(src), gettext_functions=("_", "ngettext")):
            for m in (msg if isinstance(msg, tuple) else (msg,)):
                if isinstance(m, str):
                    add(m, f"templates/{p.name}:{lineno}")
        for m in JS_CALL.finditer(src):
            if "${" not in m.group("s"):
                add(_js_unescape(m.group("s")), f"templates/{p.name}")
    for p in sorted((SERVER / "static").glob("*.js")):
        for m in JS_CALL.finditer(p.read_text(encoding="utf-8")):
            if "${" not in m.group("s"):
                add(_js_unescape(m.group("s")), f"static/{p.name}")
    for rel in PY_FILES:
        p = SERVER / rel
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = (node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                    if isinstance(node.func, ast.Attribute) else None) if isinstance(node, ast.Call) else None
            if name in ("tr", "ntr", "N_"):
                n_args = 2 if name == "ntr" else 1
                for a in node.args[:n_args]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        add(a.value, f"{rel}:{node.lineno}")
    return found


def main():
    check = "--check" in sys.argv
    found = collect()
    LOCALE_DIR.mkdir(exist_ok=True)
    print(f"{len(found)} texts in the code")
    status = 0
    for code in LANGUAGES:
        if code == "en":
            continue
        path = LOCALE_DIR / f"{code}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        meta = {k: v for k, v in old.items() if k.startswith("@")}
        new = {k: old.get(k, "") for k in sorted(found)}
        missing = [k for k, v in new.items() if not v]
        unused = [k for k in old if not k.startswith("@") and k not in found]
        print(f"  {code}: {len(new) - len(missing)} translated, {len(missing)} missing, {len(unused)} unused")
        if missing or unused:
            status = 1
        if not check:
            meta.setdefault("@language", LANGUAGES[code])
            path.write_text(json.dumps({**meta, **new}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sys.exit(status if check else 0)


if __name__ == "__main__":
    main()
