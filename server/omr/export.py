"""Results spreadsheet (openpyxl) and answer-key CSV."""
from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .i18n import flag, tr
from .randomize import answer_key

HEAD = Font(bold=True)
FILL = PatternFill("solid", fgColor="DDEBF7")
BAD = PatternFill("solid", fgColor="F8CBAD")
WARN = PatternFill("solid", fgColor="FFE699")


def _sheet_title(text: str) -> str:
    """A translated sheet name as Excel accepts it (no []:*?/\\, at most 31 characters)."""
    for ch in "[]:*?/\\":
        text = text.replace(ch, "")
    return text[:31] or "Sheet"


def _autosize(ws, min_w=6, max_w=60):
    for col in ws.columns:
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(max_w, width + 2))


def build_results_xlsx(exam: dict, scans: list[dict], roster: dict[str, str],
                       courses: dict[str, str] | None = None, emails: dict[str, str] | None = None) -> bytes:
    """courses / emails: {student_no: value}. Their columns are only added when some student has one,
    so a spreadsheet without courses or emails looks exactly as before."""
    courses = courses or {}
    emails = emails or {}
    versions = {v["version"]: v for v in exam.get("versions", [])}
    questions = exam.get("questions", [])
    n_q = len(questions)
    live = [s for s in scans if not s.get("superseded")]

    wb = Workbook()

    # ---- Results -----------------------------------------------------------
    ws = wb.active
    ws.title = _sheet_title(tr("Results"))
    extra = ([tr("Course")] if courses else []) + ([tr("Email")] if emails else [])
    head = [tr("Student number"), tr("Name")] + extra + [tr("Version"), tr("Score"), tr("Max"), tr("Percent"), tr("Status"),
                                                    tr("Flags"), tr("Scanned")]
    n_info = len(head)
    head += [tr("Q%(n)s", n=p + 1) for p in range(n_q)]
    ws.append(head)
    for s in sorted(live, key=lambda s: (s["student_no"] or "zzz")):
        pct = round(100.0 * s["score"] / s["max_score"], 1) if s.get("max_score") else None
        no = s["student_no"] or ""
        row = [s["student_no"], roster.get(no, "")]
        if courses:
            row.append(courses.get(no, ""))
        if emails:
            row.append(emails.get(no, ""))
        row += [s["version"], s["score"], s["max_score"], pct, s["status"], "; ".join(flag(f) for f in s["flags"]), s["created"]]
        row += [(a if a else "-") for a in s["answers"]] + [""] * (n_q - len(s["answers"]))
        ws.append(row)
        if s["status"] == "failed":
            for c in ws[ws.max_row][:n_info]:
                c.fill = BAD
        elif s["status"] == "review":
            for c in ws[ws.max_row][:n_info]:
                c.fill = WARN
    for c in ws[1]:
        c.font = HEAD
        c.fill = FILL
    ws.freeze_panes = "D2"
    _autosize(ws)

    # ---- Per question, in the ORIGINAL order (1 = right, 0 = wrong/blank) ---
    ws2 = wb.create_sheet(_sheet_title(tr("Per question")))
    ws2.append([tr("Student number"), tr("Version"), tr("Score")] + [tr("Q%(n)s", n=i + 1) for i in range(n_q)])
    totals = [0] * n_q
    count = 0
    for s in sorted(live, key=lambda s: (s["student_no"] or "zzz")):
        v = versions.get(s["version"])
        if not v or len(s["answers"]) != len(v["questions"]):
            continue
        key = answer_key(v)
        per_orig = [None] * n_q
        for p, item in enumerate(v["questions"]):
            per_orig[item["q"]] = 1 if s["answers"][p] == key[p] else 0
        ws2.append([s["student_no"], s["version"], s["score"]] + per_orig)
        count += 1
        for i, x in enumerate(per_orig):
            totals[i] += x or 0
    if count:
        ws2.append([])
        ws2.append([tr("% correct"), "", ""] + [round(100.0 * t / count, 1) for t in totals])
        ws2[ws2.max_row][0].font = HEAD
    ws2.append([])
    ws2.append([tr("Original question texts")])
    ws2[ws2.max_row][0].font = HEAD
    for i, q in enumerate(questions):
        text = q["text"].replace("\n", " ")
        ws2.append([tr("Q%(n)s", n=i + 1), text[:200], tr("correct: %(answer)s", answer=q["choices"][q["correct"]]["text"][:120])])
    for c in ws2[1]:
        c.font = HEAD
        c.fill = FILL
    ws2.freeze_panes = "D2"
    _autosize(ws2, max_w=80)

    # ---- Answer keys --------------------------------------------------------
    ws3 = wb.create_sheet(_sheet_title(tr("Answer keys")))
    ws3.append([tr("Version")] + [tr("Pos %(n)s", n=p + 1) for p in range(n_q)])
    for vno in sorted(versions):
        v = versions[vno]
        ws3.append([f"{vno:02d}"] + [f"{q['answer']} ({tr('Q%(n)s', n=q['q'] + 1)})" for q in v["questions"]])
    for c in ws3[1]:
        c.font = HEAD
        c.fill = FILL
    for row in ws3.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(horizontal="center")
    _autosize(ws3, min_w=9, max_w=12)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_keys_csv(exam: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["version", "position", "answer", "original_question", "correct_choice_text"])
    for v in exam.get("versions", []):
        for p, item in enumerate(v["questions"]):
            q = exam["questions"][item["q"]]
            w.writerow([v["version"], p + 1, item["answer"], item["q"] + 1, q["choices"][q["correct"]]["text"]])
    return buf.getvalue()
