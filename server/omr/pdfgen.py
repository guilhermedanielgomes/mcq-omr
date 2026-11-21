"""PDF generation: answer sheets, question papers and answer keys (ReportLab)."""
from __future__ import annotations

import io
import os
import re
from xml.sax.saxutils import escape

import cv2
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm as MM
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Preformatted, Spacer)

from . import i18n
from . import layout as L
from .i18n import tr
from .layout import SheetLayout
from .randomize import options_per_position

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def P(x_mm: float, y_mm: float):
    """Top-left millimetres -> PDF points (bottom-left origin)."""
    return x_mm * MM, (L.PAGE_H - y_mm) * MM


# ---------------------------------------------------------------------------
# translated texts: Helvetica has no Chinese characters, so in Chinese the runs of Chinese characters
# use ReportLab's built-in CID font STSong-Light (no font file needed; the PDF viewer or printer
# supplies the glyphs) and everything else keeps its font. Other languages draw exactly as before.
# ---------------------------------------------------------------------------
CJK_FONT = "STSong-Light"
_CJK_RUN = re.compile("[\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]+")
_cjk_registered = False


def _cjk() -> bool:
    global _cjk_registered
    if i18n.current() != "zh_CN":
        return False
    if not _cjk_registered:
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))
        for bold in (0, 1):                     # <b> and <i> inside Paragraphs map to the same font
            for italic in (0, 1):
                addMapping(CJK_FONT, bold, italic, CJK_FONT)
        _cjk_registered = True
    return True


def _runs(text: str, font: str) -> list[tuple[str, str]]:
    """[(piece, font)]: the Chinese pieces in the Chinese font, the rest in `font`."""
    out, pos = [], 0
    for m in _CJK_RUN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], font))
        out.append((m.group(0), CJK_FONT))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], font))
    return out


def _width(text: str, font: str, size: float) -> float:
    if not _cjk():
        return stringWidth(text, font, size)
    return sum(stringWidth(t, f, size) for t, f in _runs(text, font))


def _fit_font(text: str, font: str, size: float, max_w_mm: float, min_size: float = 6.0) -> float:
    while size > min_size and _width(text, font, size) > max_w_mm * MM:
        size -= 0.5
    return size


def _truncate(text: str, font: str, size: float, max_w_mm: float) -> str:
    if _width(text, font, size) <= max_w_mm * MM:
        return text
    while text and _width(text + "…", font, size) > max_w_mm * MM:
        text = text[:-1]
    return text + "…"


def pdf_draw(c, x, y, text: str, font: str, size: float, align: str = "left"):
    """drawString / drawCentredString / drawRightString ("left", "centre", "right") of a text that may
    hold a translation, with the font and size already set on the canvas."""
    if not _cjk() or not _CJK_RUN.search(text):
        {"left": c.drawString, "centre": c.drawCentredString, "right": c.drawRightString}[align](x, y, text)
        return
    runs = _runs(text, font)
    w = sum(stringWidth(t, f, size) for t, f in runs)
    x -= {"left": 0, "centre": w / 2, "right": w}[align]
    for t, f in runs:
        c.setFont(f, size)
        c.drawString(x, y, t)
        x += stringWidth(t, f, size)
    c.setFont(font, size)


def _cjk_markup(markup: str) -> str:
    return _CJK_RUN.sub(lambda m: f'<font name="{CJK_FONT}">{m.group(0)}</font>', markup) if _cjk() else markup


def pdf_text(text: str) -> str:
    """A translated text for Paragraph markup: escaped, its Chinese characters in the Chinese font."""
    return _cjk_markup(escape(text).replace("\n", "<br/>"))


def pdf_markup(markup: str) -> str:
    """A translated text that already is Paragraph markup (its values escaped by the caller)."""
    return _cjk_markup(markup)


def _draw_fit(c, x, y, text: str, font: str, size: float, max_w_mm: float, centred: bool = False):
    """Draw a translated label with the font and size already set on the canvas, smaller if the
    translation is wider than max_w_mm, cut if it is still too wide at the smallest size (the English
    texts fit, so they are drawn exactly as before)."""
    s = _fit_font(text, font, size, max_w_mm)
    text = _truncate(text, font, s, max_w_mm)
    if s != size:
        c.setFont(font, s)
    pdf_draw(c, x, y, text, font, s, "centre" if centred else "left")
    if s != size:
        c.setFont(font, size)


# ---------------------------------------------------------------------------
# Answer sheet (drawn directly on the canvas)
# ---------------------------------------------------------------------------
def _bubble(c, x, y, label, r=L.BUBBLE_R):
    cx, cy = P(x, y)
    c.circle(cx, cy, r * MM, stroke=1, fill=0)
    c.setFont("Helvetica", 6)
    c.drawCentredString(cx, cy - 2.1, label)


def _markers(c):
    cell = L.MARKER_SIZE / L.MARKER_CELLS
    for mid, (mx, my) in L.MARKERS.items():
        mat = cv2.aruco.generateImageMarker(ARUCO_DICT, mid, L.MARKER_CELLS)
        for i in range(L.MARKER_CELLS):
            for j in range(L.MARKER_CELLS):
                if mat[i, j] == 0:
                    x, y = P(mx + j * cell, my + (i + 1) * cell)
                    c.rect(x, y, cell * MM, cell * MM, stroke=0, fill=1)


def _qr(c, payload: str):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    mat = qr.get_matrix()
    n = len(mat)
    cell = L.QR_SIZE / n
    for i in range(n):
        for j in range(n):
            if mat[i][j]:
                x, y = P(L.QR_X + j * cell, L.QR_Y + (i + 1) * cell)
                c.rect(x, y, cell * MM, cell * MM, stroke=0, fill=1)


def _example_bubble(c, x_mm, y_mm, letter="B"):
    cx, cy = P(x_mm, y_mm)
    c.circle(cx, cy, L.BUBBLE_R * MM, stroke=1, fill=0)
    c.setFont("Helvetica", 6)
    c.drawCentredString(cx, cy - 2.1, letter)
    return cx, cy


def _mark_examples(c, y_mm: float):
    """Row of example bubbles: one correctly filled, then the common wrong ways seen on real sheets
    (tick, cross, dot in the middle, half filled, mark beside the bubble)."""
    c.saveState()
    c.setLineWidth(0.5)
    r = L.BUBBLE_R * MM
    # right: fully filled
    cx, cy = P(18, y_mm)
    c.circle(cx, cy, r, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 7)
    _draw_fit(c, cx + r + 1.2 * MM, cy - 2.4, tr("right"), "Helvetica-Bold", 7, 19)
    # wrong examples
    c.setLineWidth(0.7)
    x = 44.0
    cx, cy = _example_bubble(c, x, y_mm)                  # tick
    c.lines([(cx - 1.4 * MM, cy + 0.1 * MM, cx - 0.3 * MM, cy - 1.1 * MM),
             (cx - 0.3 * MM, cy - 1.1 * MM, cx + 2.2 * MM, cy + 1.9 * MM)])
    cx, cy = _example_bubble(c, x + 7, y_mm)              # cross
    c.line(cx - 1.5 * MM, cy - 1.5 * MM, cx + 1.5 * MM, cy + 1.5 * MM)
    c.line(cx - 1.5 * MM, cy + 1.5 * MM, cx + 1.5 * MM, cy - 1.5 * MM)
    cx, cy = _example_bubble(c, x + 14, y_mm)             # small dot in the middle
    c.circle(cx, cy, 0.55 * MM, stroke=0, fill=1)
    cx, cy = _example_bubble(c, x + 21, y_mm)             # half filled
    c.wedge(cx - r, cy - r, cx + r, cy + r, 90, 180, stroke=0, fill=1)
    cx, cy = _example_bubble(c, x + 28, y_mm)             # mark beside the bubble instead of inside it
    c.circle(cx + 2.6 * MM, cy - 1.6 * MM, 1.6 * MM, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 7)
    _draw_fit(c, cx + r + 4.6 * MM, cy - 2.4, tr("wrong"), "Helvetica-Bold", 7, 38)
    c.restoreState()


# optional header image shipped with the program (not in the public repository; upload one in Settings)
DEFAULT_LOGO = os.path.join(os.path.dirname(__file__), "assets", "header.png")


def draw_sheet(c, lay: SheetLayout, info: dict):
    """info: institution(list[str]), title, module, semester, lecturer, set_id, version, payload"""
    c.saveState()
    c.setStrokeColor(colors.black)
    c.setFillColor(colors.black)
    c.setLineWidth(0.5)
    _markers(c)
    _qr(c, info["payload"])

    # header between the top markers: the header image (logo + institution name) when one is set,
    # otherwise the institution text lines
    drawn = False
    logo = info.get("logo")
    if logo:
        try:
            img = ImageReader(logo)
            iw, ih = img.getSize()
            h = L.LOGO_H
            w = h * iw / ih
            if w > L.LOGO_MAX_W:
                w = L.LOGO_MAX_W
                h = w * ih / iw
            x, y = P(L.LOGO_X, L.LOGO_Y + (L.LOGO_H - h) / 2 + h)
            c.drawImage(img, x, y, w * MM, h * MM, mask="auto")
            drawn = True
        except Exception:
            drawn = False
    if not drawn:
        c.setFont("Helvetica", 7.5)
        for k, line in enumerate((info.get("institution") or [])[:3]):
            c.drawString(*P(26.0, 14 + 4 * k), _truncate(line, "Helvetica", 7.5, 158))

    # exam identification (left block)
    title = info.get("title", "")
    size = _fit_font(title, "Helvetica-Bold", 10.5, 107)
    c.setFont("Helvetica-Bold", size)
    c.drawString(*P(10, 31.5), title)
    c.setFont("Helvetica", 8)
    _draw_fit(c, *P(10, 38), _truncate(tr("Module: %(module)s", module=info.get("module", "")), "Helvetica", 8, 66),
              "Helvetica", 8, 66)
    _draw_fit(c, *P(80, 38), _truncate(tr("Sem: %(semester)s", semester=info.get("semester", "")), "Helvetica", 8, 38),
              "Helvetica", 8, 38)
    _draw_fit(c, *P(10, 45), tr("Name:"), "Helvetica", 8, 11.5)
    c.line(*P(22, 45.6), *P(118, 45.6))
    _draw_fit(c, *P(10, 52), tr("Signature:"), "Helvetica", 8, 15.5)
    c.line(*P(26, 52.6), *P(74, 52.6))
    _draw_fit(c, *P(78, 52), tr("Date:"), "Helvetica", 8, 8.5)
    c.line(*P(87, 52.6), *P(118, 52.6))
    _draw_fit(c, *P(10, 58.5), _truncate(tr("Lecturer: %(name)s", name=info.get("lecturer", "")), "Helvetica", 8, 107),
              "Helvetica", 8, 107)

    # instructions
    c.setDash(2, 1.5)
    c.rect(*P(10, 96), 108 * MM, 34 * MM, stroke=1, fill=0)
    c.setDash()
    c.setFont("Helvetica-Bold", 7)
    _draw_fit(c, *P(12, 66.5), tr("Instructions"), "Helvetica-Bold", 7, 104)
    c.setFont("Helvetica", 7)
    _draw_fit(c, *P(12, 72), tr("1. Use a black or blue pen and fill the whole bubble. Only a full bubble counts:"),
              "Helvetica", 7, 104)
    _mark_examples(c, 76.4)
    c.setFont("Helvetica", 7)
    _draw_fit(c, *P(12, 83), tr("2. One answer per question: two filled bubbles cancel the question."),
              "Helvetica", 7, 104)
    _draw_fit(c, *P(12, 88), tr("3. Student number: fill one digit per column and write the digits in the boxes above."),
              "Helvetica", 7, 104)
    _draw_fit(c, *P(12, 93), tr("4. Keep the sheet flat. Never write on the black squares or the QR code."),
              "Helvetica", 7, 104)

    # QR labels
    c.setFont("Helvetica", 6.5)
    _draw_fit(c, *P(L.QR_X + L.QR_SIZE / 2, 57.5), tr("Set %(id)s", id=info["set_id"]), "Helvetica", 6.5, 30,
              centred=True)
    c.setFont("Helvetica-Bold", 8.5)
    _draw_fit(c, *P(L.QR_X + L.QR_SIZE / 2, 62), tr("Version %(v)s", v=f"{info['version']:02d}"), "Helvetica-Bold",
              8.5, 30, centred=True)

    # student number block
    c.setLineWidth(0.8)
    c.rect(*P(L.ID_X, L.ID_Y + L.ID_H), L.ID_W * MM, L.ID_H * MM, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.setFont("Helvetica-Bold", 7.5)
    _draw_fit(c, *P(L.ID_X + 2, 30.5), tr("Student number"), "Helvetica-Bold", 7.5, L.ID_W - 4)
    ax, ay = lay.id_prefix_anchor()
    psize = _fit_font(lay.id_prefix, "Helvetica-Bold", 9, lay.id_prefix_max_width())
    c.setFont("Helvetica-Bold", psize)
    px, py = P(ax, ay)
    c.drawRightString(px, py - psize * 0.35, lay.id_prefix)
    for col in range(lay.id_digits):
        bxm, bym, s = lay.id_write_box(col)
        x, y = P(bxm, bym + s)
        c.rect(x, y, s * MM, s * MM, stroke=1, fill=0)
        for d, (cx, cy) in enumerate(lay.id_bubbles(col)):
            _bubble(c, cx, cy, str(d))

    # answers
    c.setLineWidth(0.4)
    c.line(*P(10, 101.5), *P(200, 101.5))
    c.setFont("Helvetica-Bold", 8.5)
    _draw_fit(c, *P(L.GRID_LEFT, 105.5), tr("Answers"), "Helvetica-Bold", 8.5, 80)
    c.setLineWidth(0.6)
    for (bx_, by_, bw, bh) in lay.column_boxes():
        x, y = P(bx_, by_ + bh)
        c.roundRect(x, y, bw * MM, bh * MM, 1.2 * MM, stroke=1, fill=0)
    c.setLineWidth(0.5)
    for p in range(lay.n_questions):
        nx, ny = lay.question_number_anchor(p)
        x, y = P(nx, ny)
        c.setFont("Helvetica", 7)
        c.drawRightString(x, y - 2.4, str(p + 1))
        for j, (cx, cy) in enumerate(lay.question_bubbles(p)):
            _bubble(c, cx, cy, L.LETTERS[j])

    # footer
    c.setFont("Helvetica", 6)
    c.setFillColor(colors.grey)
    pdf_draw(c, *P(105, 290), _truncate(
        tr("%(title)s · set %(id)s · version %(v)s · %(n)s questions", title=title, id=info["set_id"],
           v=f"{info['version']:02d}", n=lay.n_questions),
        "Helvetica", 6, 150), "Helvetica", 6, "centre")
    c.restoreState()


# ---------------------------------------------------------------------------
# Question papers (platypus)
# ---------------------------------------------------------------------------
ST_TITLE = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=3)
ST_META = ParagraphStyle("m", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#333333"))
ST_BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13)
ST_Q = ParagraphStyle("q", parent=ST_BODY, spaceBefore=7)
ST_CHOICE = ParagraphStyle("c", parent=ST_BODY, leftIndent=9 * MM, firstLineIndent=-6 * MM, spaceBefore=1)
ST_CODE = ParagraphStyle("code", fontName="Courier", fontSize=8.5, leading=10.5, leftIndent=6 * MM,
                         backColor=colors.HexColor("#f2f2f2"), borderPadding=2, spaceBefore=2, spaceAfter=2)
ST_CODE_CHOICE = ParagraphStyle("codec", parent=ST_CODE, leftIndent=12 * MM)


def _para(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


def render_text(text: str, style: ParagraphStyle, code_style: ParagraphStyle, prefix: str = ""):
    """Text with optional ``` fenced code blocks -> list of flowables."""
    out = []
    parts = str(text).split("```")
    for k, part in enumerate(parts):
        if k % 2 == 0:
            t = part.strip("\n")
            if t.strip() or (k == 0 and prefix):
                out.append(Paragraph(prefix + _para(t), style))
                prefix = ""
        else:
            code = part.strip("\n")
            if code.strip():
                out.append(Preformatted(code, code_style))
    if not out and prefix:
        out.append(Paragraph(prefix, style))
    return out


class _Mark(Flowable):
    """Zero-size flowable that stores information on the canvas for the page callbacks."""

    def __init__(self, **attrs):
        super().__init__()
        self.attrs = attrs

    def wrap(self, aw, ah):
        return 0, 0

    def draw(self):
        for k, v in self.attrs.items():
            setattr(self.canv, k, v)


def _paper_story(exam: dict, version: dict):
    meta = exam.get("meta", {})
    qs = exam["questions"]
    v = version["version"]
    story = [_Mark(_mcq_version=v, _mcq_sheet=None)]
    story.append(Paragraph(_para(meta["title"]) if "title" in meta else pdf_text(tr("Exam")), ST_TITLE))
    bits = [_para(b) for b in (meta.get("module"), meta.get("semester")) if b]     # markup from here on
    if meta.get("lecturer"):
        bits.append(pdf_text(tr("Lecturer: %(name)s", name=meta["lecturer"])))
    bits.append("<b>" + pdf_text(tr("Version %(v)s", v=f"{v:02d}")) + "</b>")
    bits.append(pdf_text(tr("Set %(id)s", id=exam["id"])))
    story.append(Paragraph(" &nbsp;·&nbsp; ".join(bits), ST_META))
    story.append(Spacer(1, 4))
    story.append(Paragraph(pdf_text(tr("Name:")) + " ______________________________________ &nbsp;&nbsp; "
                           + pdf_text(tr("Student number:")) + " ____________________", ST_META))
    if meta.get("instructions"):
        story.append(Spacer(1, 4))
        story.extend(render_text(meta["instructions"], ST_META, ST_CODE))
    story.append(Spacer(1, 6))
    for p, item in enumerate(version["questions"]):
        q = qs[item["q"]]
        block = render_text(q["text"], ST_Q, ST_CODE, prefix=f"<b>{p + 1}.</b> ")
        for j, ci in enumerate(item["choices"]):
            block.extend(render_text(q["choices"][ci]["text"], ST_CHOICE, ST_CODE_CHOICE,
                                     prefix=f"<b>{L.LETTERS[j]})</b> "))
        story.append(KeepTogether(block))
    return story


def _make_doc(buf, exam: dict):
    meta = exam.get("meta", {})
    title = meta["title"] if "title" in meta else tr("Exam")
    set_id = exam["id"]

    def on_paper_end(canv, doc):
        v = getattr(canv, "_mcq_version", None)
        counts = canv.__dict__.setdefault("_mcq_pages", {})
        counts[v] = counts.get(v, 0) + 1
        canv.saveState()
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.grey)
        if v:
            left = tr("%(title)s · set %(id)s · version %(v)s", title=title, id=set_id, v=f"{v:02d}")
        else:
            left = tr("%(title)s · set %(id)s", title=title, id=set_id)
        pdf_draw(canv, 15 * MM, 9 * MM, _truncate(left, "Helvetica", 8, 150), "Helvetica", 8)
        pdf_draw(canv, A4[0] - 15 * MM, 9 * MM, tr("page %(n)s", n=counts[v]), "Helvetica", 8, "right")
        canv.restoreState()

    def on_sheet_end(canv, doc):
        sheet = getattr(canv, "_mcq_sheet", None)
        if sheet:
            draw_sheet(canv, *sheet)

    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=15 * MM, rightMargin=15 * MM,
                          topMargin=14 * MM, bottomMargin=16 * MM,
                          title=title, author=meta.get("lecturer", ""))
    paper_frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="paper")
    sheet_frame = Frame(0, 0, A4[0], A4[1], leftPadding=0, rightPadding=0, topPadding=0,
                        bottomPadding=0, id="sheet")
    doc.addPageTemplates([PageTemplate(id="paper", frames=[paper_frame], onPageEnd=on_paper_end),
                          PageTemplate(id="sheet", frames=[sheet_frame], onPageEnd=on_sheet_end)])
    return doc


def sheet_info(exam: dict, version: dict, logo: str | None = None) -> tuple[SheetLayout, dict]:
    """logo: path of the header image; None = the bundled image if present; "" = none (text lines instead)."""
    if logo is None:
        logo = DEFAULT_LOGO if os.path.exists(DEFAULT_LOGO) else ""
    meta = exam.get("meta", {})
    settings = exam.get("settings", {})
    opts = options_per_position(exam["questions"], version)
    lay = SheetLayout(n_questions=len(opts), options=opts,
                      id_digits=int(settings.get("id_digits", 6)),
                      id_prefix=str(settings.get("id_prefix", "")))
    info = {
        "institution": meta.get("institution", []),
        "title": meta.get("title", ""),
        "module": meta.get("module", ""),
        "semester": meta.get("semester", ""),
        "lecturer": meta.get("lecturer", ""),
        "set_id": exam["id"],
        "version": version["version"],
        "payload": L.qr_payload(exam["id"], version["version"], lay.n_questions, lay.max_opts, lay.id_digits),
        "logo": logo or None,
    }
    return lay, info


def build_pdf(exam: dict, versions: list[dict], papers: bool = True, sheets: bool = True,
              logo: str | None = None) -> bytes:
    """One PDF with, for each version, its answer sheet followed by its question paper."""
    buf = io.BytesIO()
    doc = _make_doc(buf, exam)
    if sheets:
        doc.pageTemplates.reverse()  # the document starts on the sheet template
    story = []
    for i, version in enumerate(versions):
        if sheets:
            lay, info = sheet_info(exam, version, logo)
            if i > 0:
                story += [NextPageTemplate("sheet"), PageBreak()]
            story += [_Mark(_mcq_sheet=(lay, info))]
        if papers:
            if sheets or i > 0:
                story += [NextPageTemplate("paper"), PageBreak()]
            story += _paper_story(exam, version)
    doc.build(story)
    return buf.getvalue()


def build_pdf_duplex(exam: dict, versions: list[dict], logo: str | None = None) -> bytes:
    """Print set for DOUBLE-SIDED printing. For each version:
         answer sheet    ->  blank page
         question pages  ->  blank page if the question pages are an odd number
    so the answer sheet is always on its own physical sheet with a blank back, and the next
    version always starts on a fresh sheet too. With a one-page question paper this gives
    exactly: answer sheet, blank, questions, blank."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for version in versions:
        sheet = PdfReader(io.BytesIO(build_pdf(exam, [version], papers=False, sheets=True, logo=logo)))
        writer.append(sheet)
        writer.add_blank_page(width=A4[0], height=A4[1])
        papers = PdfReader(io.BytesIO(build_pdf(exam, [version], papers=True, sheets=False, logo=logo)))
        writer.append(papers)
        if len(papers.pages) % 2:
            writer.add_blank_page(width=A4[0], height=A4[1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def build_keys_pdf(exam: dict, versions: list[dict]) -> bytes:
    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    meta = exam.get("meta", {})
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * MM, rightMargin=15 * MM,
                            topMargin=15 * MM, bottomMargin=15 * MM, title=tr("Answer keys"))
    story = [Paragraph(pdf_text(tr("Answer keys · %(title)s · set %(id)s", title=meta.get("title", ""), id=exam["id"])),
                       ST_TITLE),
             Paragraph(pdf_text(tr("Each line: sheet position – answer letter (original question number).")), ST_META),
             Spacer(1, 8)]
    for version in versions:
        items = [f"{p + 1}-<b>{q['answer']}</b>({q['q'] + 1})" for p, q in enumerate(version["questions"])]
        story.append(KeepTogether([Paragraph("<b>" + pdf_text(tr("Version %(v)s", v=f"{version['version']:02d}")) + "</b>",
                                             ST_Q),
                                   Paragraph(" &nbsp; ".join(items), ST_BODY)]))
    doc.build(story)
    return buf.getvalue()
