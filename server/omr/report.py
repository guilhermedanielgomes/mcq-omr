"""One student's PDF report: the original photo, the sheet as read, the sheet with every question
marked right / wrong / blank / several, the last two side by side, and the question-by-question
table with a close-up of each row of bubbles (ReportLab)."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

import cv2
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm as MM
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, Image, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from . import sheetview
from .i18n import N_, flag, tr
from .pdfgen import pdf_draw, pdf_markup as pm, pdf_text as pt

ST_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=2)
ST_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceAfter=3)
ST_SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, leading=13, textColor=colors.HexColor("#444444"))
ST_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10)
ST_CELL_B = ParagraphStyle("cellb", parent=ST_CELL, fontName="Helvetica-Bold")
ST_SMALL = ParagraphStyle("small", parent=ST_CELL, fontSize=7, leading=9, textColor=colors.HexColor("#555555"))
ST_WARN = ParagraphStyle("warn", parent=ST_SMALL, textColor=colors.HexColor("#b26a00"))
MARGIN = 12 * MM
STATUS_TEXT = {"ok": N_("ok"), "review": N_("review"), "failed": N_("failed")}


def _jpeg_reader(img_bgr, quality: int = 85) -> bytes:
    """JPEG bytes of a BGR image (the platypus Image flowable takes a file-like object)."""
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


class _Picture(Image):
    """An image flowable from JPEG bytes, scaled to fit a box."""

    def __init__(self, data: bytes, max_w: float, max_h: float):
        iw, ih = ImageReader(io.BytesIO(data)).getSize()
        s = min(max_w / iw, max_h / ih)
        super().__init__(io.BytesIO(data), width=iw * s, height=ih * s)


def _page_title(canvas, doc, text: str):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    pdf_draw(canvas, MARGIN, doc.pagesize[1] - 8 * MM, text, "Helvetica", 8)
    pdf_draw(canvas, doc.pagesize[0] - MARGIN, 6 * MM, tr("page %(n)s", n=doc.page), "Helvetica", 8, "right")
    canvas.restoreState()


def _num(x) -> str:
    if x is None:
        return "–"
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def _signed(x) -> str:
    if x is None:
        return "–"
    return ("+" if x > 0 else "−" if x < 0 else "") + _num(abs(x))


def _individual(ind: dict, box_w: float) -> list:
    """The individual statistics (stats.student_detail) as flowables."""
    grid = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    out = [Paragraph(pt(tr("Individual statistics")), ST_H2)]
    if not ind["counted"]:
        out.append(Paragraph(pt(tr("This scan is not the one that counts for this student (or is not scored yet): it is "
                                   "compared with the class, but is not part of the class figures.")), ST_WARN))
    if ind.get("class_scope") == "out":       # only for an exam set with a class
        out.append(Paragraph(pt(tr("This student is not in the class of this exam (%(course)s), so the comparison is "
                                   "with everyone who sat it.", course=ind["exam_class"])), ST_SMALL))
    elif ind.get("class_scope") == "in":
        out.append(Paragraph(pt(tr("The class figures are for the class %(course)s only.", course=ind["exam_class"])),
                             ST_SMALL))
    if ind["n_ranked"] > 1:
        if ind["n_equal"]:
            place = pt(tr("%(rank)s of %(n)s (shared with %(equal)s)", rank=ind["rank"], n=ind["n_ranked"],
                          equal=ind["n_equal"]))
        else:
            place = pt(tr("%(rank)s of %(n)s", rank=ind["rank"], n=ind["n_ranked"]))
        beats = pt(tr("scored higher than %(pct)s%% of the others", pct=_num(ind["beats_pct"])))
    else:
        place, beats = "–", pt(tr("first sheet scored so far"))
    head = [Paragraph("<b>" + pt(tr("Place in the class")) + "</b>", ST_CELL)]
    body = [[Paragraph(place, ST_CELL_B), Paragraph(beats, ST_SMALL)]]
    if ind["class_ok"]:
        sign = "+" if ind["diff_mean"] > 0 else ""
        extra = " · ".join(x for x in (ind["quartile"] or "", tr("z-score %(z)s", z=ind["z"]) if ind["z"] is not None else "")
                           if x)
        head += [Paragraph("<b>" + pt(tr("Compared with the class mean")) + "</b>", ST_CELL),
                 Paragraph("<b>" + pt(tr("Class range")) + "</b>", ST_CELL)]
        body += [[Paragraph(pt(tr("%(diff)s points", diff=f"{sign}{_num(ind['diff_mean'])}")), ST_CELL_B),
                  Paragraph(pt(tr("class mean %(mean)s%% · median %(median)s%%", mean=_num(ind["class_mean"]),
                                  median=_num(ind["class_median"]))), ST_SMALL)],
                 [Paragraph(f"{_num(ind['class_min'])}–{_num(ind['class_max'])}%", ST_CELL_B), Paragraph(pt(extra), ST_SMALL)]]
    else:
        head += [Paragraph("<b>" + pt(tr("Class mean, median, range")) + "</b>", ST_CELL)]
        body += [[Paragraph("–", ST_CELL_B),
                  Paragraph(pt(tr("shown from 5 scored sheets on (%(n)s so far)", n=ind["n_ranked"])), ST_SMALL)]]
    cells = [head + [Paragraph("<b>" + pt(tr("Right, %(positions)s", positions=h["label"])) + "</b>", ST_CELL)
                     for h in ind["halves"]],
             body + [[Paragraph(f"{h['right']} / {h['n']}", ST_CELL_B), Paragraph(f"{_num(h['percent'])}%", ST_SMALL)]
                for h in ind["halves"]]]
    w = box_w / len(cells[0])
    t = Table(cells, colWidths=[w] * len(cells[0]))
    t.setStyle(TableStyle(grid))
    out += [t]
    if ind["halves_note"]:
        out.append(Paragraph(pt(ind["halves_note"]), ST_WARN))
    out.append(Spacer(0, 3 * MM))
    if ind["difficulty_ok"]:
        diff = [[Paragraph("<b>" + pt(tr("Questions, by how hard they were for the class")) + "</b>", ST_CELL),
                 Paragraph("<b>" + pt(tr("Right")) + "</b>", ST_CELL)]]
        for g in ind["difficulty"]:
            diff.append([Paragraph(pt(g["label"]), ST_CELL),
                         Paragraph(f"{g['right']} / {g['n']} ({_num(g['percent'])}%)" if g["n"] else pt(tr("none")), ST_CELL)])
        t = Table(diff, colWidths=[70 * MM, 40 * MM], hAlign="LEFT")
        t.setStyle(TableStyle(grid))
        out += [t, Spacer(0, 2 * MM)]
        for title, items in ((N_("Worth revising: wrong, but most of the class got it right"), ind["revise"]),
                             (N_("Strong points: right, though most of the class got it wrong"), ind["strong"])):
            text = "; ".join("<b>" + pt(tr("Q%(n)s", n=q["q"])) + "</b> " + pt(tr("(%(pct)s%% right)", pct=_num(q["class_pct"])))
                             + f" {escape(q['text'])}" for q in items) or pt(tr("none"))
            out.append(Paragraph(f"<b>{pt(tr(title))}:</b> {text}", ST_CELL))
            out.append(Spacer(0, 1.5 * MM))
    else:
        out.append(Paragraph(pt(tr("The comparison by question difficulty needs 5 or more scored sheets of this exam "
                                   "(%(n)s so far).", n=ind["n_class"])), ST_SMALL))
    if ind.get("topics"):          # only when the questions have topics
        out.append(Spacer(0, 2 * MM))
        head = [N_("By topic"), N_("Right")] + ([N_("Class")] if ind["difficulty_ok"] else []) + [""]
        rows = [[Paragraph(f"<b>{pt(tr(h))}</b>" if h else "", ST_CELL) for h in head]]
        for x in ind["topics"]:
            name = escape(x["topic"]) if x["topic"] is not None else "<i>" + pt(tr("No topic")) + "</i>"
            row = [Paragraph(name, ST_CELL), Paragraph(f"{x['right']} / {x['n']} ({_num(x['percent'])}%)", ST_CELL)]
            if ind["difficulty_ok"]:
                row.append(Paragraph(f"{_num(x['class_pct'])}%", ST_CELL))
            row.append(Paragraph("<b>" + pt(tr("Worth revising")) + "</b>", ST_WARN) if x["below"] else "")
            rows.append(row)
        widths = [70 * MM, 34 * MM] + ([22 * MM] if ind["difficulty_ok"] else []) + [34 * MM]
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle(grid))
        out.append(t)
        if ind["difficulty_ok"]:
            out.append(Paragraph(pt(tr("Class = the share of right answers of the whole class on that topic. \"Worth revising\" "
                                       "marks a topic at least %(n)s points under the class.", n=_num(ind["topic_below"]))),
                                 ST_SMALL))
        else:
            out.append(Paragraph(pt(tr("The class figure for each topic appears once %(min)s or more sheets of this exam "
                                       "are scored (%(n)s so far).", min=5, n=ind["n_class"])), ST_SMALL))
    if len(ind["history"]) > 1:
        out.append(Spacer(0, 2 * MM))
        cur = next((h for h in ind["history"] if h.get("set_id") == ind.get("set_id")), None)
        missed = [h["title"] for h in ind["history"] if h.get("absent")]
        line = ""
        if cur and cur.get("change") is not None:
            since = {"title": escape(cur["prev_title"]), "change": _signed(cur["change"])}
            pc = cur.get("pos_change")
            if pc is None:
                line = tr("Since %(title)s: <b>%(change)s points</b>.", **since)
            elif pc > 0:
                line = tr("Since %(title)s: <b>%(change)s points</b>, place in the class up by %(n)s percentile points.",
                          n=_num(abs(pc)), **since)
            elif pc < 0:
                line = tr("Since %(title)s: <b>%(change)s points</b>, place in the class down by %(n)s percentile points.",
                          n=_num(abs(pc)), **since)
            else:
                line = tr("Since %(title)s: <b>%(change)s points</b>, place in the class unchanged.", **since)
            line = pm(line)
        if missed:
            line += (" " if line else "") + pm(tr("Did not sit %(titles)s.", titles=escape(", ".join(missed))))
        if line:
            out.append(Paragraph(line, ST_CELL))
            out.append(Spacer(0, 1.5 * MM))
        hist = [[Paragraph(f"<b>{pt(tr(h)) if h != '%' else h}</b>", ST_CELL)
                 for h in (N_("All exams of this student"), N_("Date"), N_("Score"), "%", N_("Change"), N_("Class mean"),
                           N_("Place"))]]
        for h in ind["history"]:
            if h.get("absent"):
                hist.append([Paragraph(escape(h["title"]), ST_CELL), Paragraph(h["created"], ST_CELL),
                             Paragraph("<i>" + pt(tr("did not sit")) + "</i>", ST_CELL), "", "", "", ""])
                continue
            hist.append([Paragraph(escape(h["title"]), ST_CELL), Paragraph(h["created"], ST_CELL),
                         Paragraph(f"{h['score']} / {h['max_score']}", ST_CELL), Paragraph(f"{_num(h['percent'])}%", ST_CELL),
                         Paragraph(_signed(h.get("change")), ST_CELL),
                         Paragraph(f"{_num(h['class_mean'])}%" if h["class_mean"] is not None else pt(tr("under 5 sheets")), ST_CELL),
                         Paragraph(pt(tr("%(rank)s of %(n)s", rank=h["rank"], n=h["n"])), ST_CELL)])
        t = Table(hist, colWidths=[box_w - 124 * MM, 22 * MM, 20 * MM, 16 * MM, 16 * MM, 24 * MM, 26 * MM])
        t.setStyle(TableStyle(grid))
        out.append(t)
    out.append(Spacer(0, 4 * MM))
    return out


def build_report(d: dict, exam: dict, photo_bgr, overlay_bgr, cols: dict[str, str], ind: dict | None = None) -> bytes:
    """d = sheetview.build(...); photo / overlay = the stored images (BGR); cols = outcome colours;
    ind = stats.student_detail(...) for the individual statistics (left out when None)."""
    marked = sheetview.render_marked(overlay_bgr, d["rows"], cols) if overlay_bgr is not None else None
    who = d["student_no"] + (f" · {d['name']}" if d["name"] else "")
    header = tr("%(who)s · %(title)s · version %(v)s · scan #%(id)s", who=who, title=exam["meta"].get("title", ""),
                v=f"{d['version']:02d}", id=d["scan_id"])

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * MM,
                          bottomMargin=12 * MM, title=f"{d['student_no']} - {exam['meta'].get('title', '')}",
                          author="MCQ-OMR")
    pw, ph = A4
    lw, lh = landscape(A4)
    frame_p = Frame(MARGIN, 12 * MM, pw - 2 * MARGIN, ph - 26 * MM, id="p", leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0)
    frame_l = Frame(MARGIN, 12 * MM, lw - 2 * MARGIN, lh - 26 * MM, id="l", leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="portrait", frames=[frame_p], pagesize=A4, onPage=lambda c, dd: _page_title(c, dd, header)),
        PageTemplate(id="landscape", frames=[frame_l], pagesize=landscape(A4),
                     onPage=lambda c, dd: _page_title(c, dd, header)),
    ])
    box_w, box_h = pw - 2 * MARGIN, ph - 26 * MM - 8 * MM
    story = []

    def full_page(data, caption, next_template="portrait"):
        story.append(Paragraph(caption, ST_SUB))
        story.append(Spacer(0, 2 * MM))
        story.append(_Picture(data, box_w, box_h))
        story.append(NextPageTemplate(next_template))    # the page after this one
        story.append(PageBreak())

    if photo_bgr is not None:
        full_page(_jpeg_reader(photo_bgr), pt(tr("1. The photo as taken")))
    if overlay_bgr is not None:
        full_page(_jpeg_reader(overlay_bgr),
                  pt(tr("2. As read: green = the mark read, orange = weak or no mark, red = several marks")))
    if marked is not None:
        full_page(_jpeg_reader(marked),
                  pt(tr("3. Question by question: right / wrong (ring = the correct bubble) / blank / several marks")),
                  next_template="landscape")
        # the two side by side on a landscape page
        story.append(Paragraph(pt(tr("4. As read (left) and question by question (right)")), ST_SUB))
        story.append(Spacer(0, 2 * MM))
        half_w = (lw - 2 * MARGIN - 6 * MM) / 2
        half_h = lh - 26 * MM - 20 * MM        # leaves room for the caption on the same page
        a, b = _Picture(_jpeg_reader(overlay_bgr), half_w, half_h), _Picture(_jpeg_reader(marked), half_w, half_h)
        t = Table([[a, b]], colWidths=[half_w + 3 * MM, half_w + 3 * MM])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        story.append(t)
        story.append(NextPageTemplate("portrait"))
        story.append(PageBreak())

    # summary
    story.append(Paragraph(pm(tr("5. Summary · %(who)s", who=escape(who))), ST_H1))
    pct = f"{d['percent']:g}%" if d["percent"] is not None else "–"
    status = tr(STATUS_TEXT[d["status"]]) if d["status"] in STATUS_TEXT else d["status"]
    story.append(Paragraph(
        pm(tr("%(title)s · version %(v)s · scanned %(created)s · status %(status)s",
              title=escape(exam["meta"].get("title", "")), v=f"{d['version']:02d}", created=d["created"], status=status))
        + (" · " + pt(" · ".join(flag(f) for f in d["flags"])) if d["flags"] else ""), ST_SUB))
    story.append(Spacer(0, 3 * MM))
    c = d["counts"]
    tiles = [[Paragraph(f"<b>{pt(tr(h))}</b>", ST_CELL)
              for h in (N_("Score"), N_("Right"), N_("Wrong"), N_("Blank"), N_("Several marks"))],
             [Paragraph(f"{d['score']} / {d['max_score']}  ({pct}{' · ' + d['band'] if d['band'] else ''})", ST_CELL_B),
              Paragraph(str(c["right"]), ST_CELL_B), Paragraph(str(c["wrong"]), ST_CELL_B),
              Paragraph(str(c["blank"]), ST_CELL_B), Paragraph(str(c["multiple"]), ST_CELL_B)]]
    tt = Table(tiles, colWidths=[52 * MM, 30 * MM, 30 * MM, 30 * MM, 40 * MM])
    tt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2"))]))
    story.append(tt)
    story.append(Spacer(0, 4 * MM))
    if ind and ind.get("available"):
        story += _individual(ind, box_w)

    story.append(Paragraph(pt(tr("Close-ups: a ring in the 'wrong' colour (red by default) is the bubble a wrong answer was "
                                 "marked in, a ring in the 'right' colour (green by default) the correct bubble.")), ST_SMALL))
    story.append(Spacer(0, 1.5 * MM))
    rows = [[Paragraph(f"<b>{pt(tr(h))}</b>", ST_CELL)
             for h in (N_("On sheet"), N_("Bubbles as photographed"), N_("Marked"), N_("Key"), N_("Result"), N_("Question"))]]
    styles = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    tint = {k: colors.HexColor(v) for k, v in cols.items()}
    crop_w = 46 * MM
    for i, r in enumerate(d["rows"], start=1):
        cell_img = ""
        if overlay_bgr is not None:
            cr = sheetview.crop_ringed(overlay_bgr, r, cols)
            if cr.size:
                cell_img = _Picture(_jpeg_reader(cr, 80), crop_w, 9 * MM)
        if r["outcome"] == "multiple":
            marked = pt(tr("several marks"))
        elif r["outcome"] == "blank":
            marked = pt(tr("blank"))
        else:
            marked = escape(r["marked"])
        marked_p = [Paragraph(marked, ST_CELL_B)]
        if r["marked_text"] and r["outcome"] in ("right", "wrong"):
            marked_p.append(Paragraph(escape(r["marked_text"]), ST_SMALL))
        if r["weak"]:
            blank = r["outcome"] == "blank"
            if r["next_pct"] is not None:
                weak = (tr("weak or no mark · %(pct)s%% dark, next %(next)s%% · counted as blank", pct=r["max_pct"],
                           next=r["next_pct"]) if blank else
                        tr("weak or no mark · %(pct)s%% dark, next %(next)s%%", pct=r["max_pct"], next=r["next_pct"]))
            else:
                weak = (tr("weak or no mark · %(pct)s%% dark · counted as blank", pct=r["max_pct"]) if blank else
                        tr("weak or no mark · %(pct)s%% dark", pct=r["max_pct"]))
            marked_p.append(Paragraph(pt(weak), ST_WARN))
        rows.append([Paragraph(str(r["pos"]), ST_CELL_B), cell_img, marked_p,
                     [Paragraph(r["key"], ST_CELL_B), Paragraph(escape(r["key_text"]), ST_SMALL)],
                     Paragraph(pt(r["outcome_text"]), ST_CELL),
                     Paragraph("<b>" + pt(tr("Q%(n)s", n=r["q"])) + "</b> " + escape(r["text"]), ST_CELL)])
        styles.append(("BACKGROUND", (4, i), (4, i), colors.Color(tint[r["outcome"]].red, tint[r["outcome"]].green,
                                                                  tint[r["outcome"]].blue, alpha=0.25)))
    table = Table(rows, colWidths=[13 * MM, crop_w + 2 * MM, 36 * MM, 30 * MM, 20 * MM, box_w - (13 + 46 + 2 + 36 + 30 + 20) * MM],
                  repeatRows=1)
    table.setStyle(TableStyle(styles))
    story.append(table)
    doc.build(story)
    return buf.getvalue()
