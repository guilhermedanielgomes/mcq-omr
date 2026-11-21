"""End-to-end check without a printer or a phone.

1. builds a small exam set and renders one answer sheet to an image (PyMuPDF)
2. "fills" bubbles like a student would (including a blank question, a double
   mark and a weak pencil-like mark)
3. turns the page into a fake phone photo: perspective, rotation, blur,
   shadow gradient, noise, JPEG compression
4. runs the reader and compares what it read with what was filled

Run from the server folder:   python tests/synthetic_test.py [output_dir]
Needs:  pip install pymupdf
"""
from __future__ import annotations

import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymupdf  # noqa: E402

from omr import layout as L  # noqa: E402
from omr.pdfgen import build_pdf, sheet_info  # noqa: E402
from omr.randomize import answer_key, make_versions, score_answers  # noqa: E402
from omr.reader import read_sheet  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)
DPI = 200
PX = DPI / 25.4


def sample_exam(n_questions=30):
    qs = []
    for i in range(1, n_questions + 1):
        n = 5 if i % 3 else 4
        choices = [{"text": f"Choice {j} of question {i}", "fixed": False} for j in range(n)]
        qs.append({"text": f"Question {i}?", "choices": choices, "correct": i % n})
    exam = {"id": "k7f3x2", "meta": {"title": "Synthetic test", "module": "BIO101", "semester": "2026/1",
                                     "lecturer": "A. Teacher", "institution": ["Example University"]},
            "settings": {"id_digits": 6, "id_prefix": "S"}, "questions": qs}
    exam["versions"] = make_versions(qs, 3, exam["id"])
    return exam


def render_sheet(exam, version) -> np.ndarray:
    pdf = build_pdf(exam, [version], papers=False, sheets=True)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    return cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2BGR)


def fill(img, cx_mm, cy_mm, color=(25, 25, 25), scale=0.95):
    cv2.circle(img, (int(round(cx_mm * PX)), int(round(cy_mm * PX))), int(L.BUBBLE_R * scale * PX), color, -1, cv2.LINE_AA)


def fake_photo(page: np.ndarray, seed: int, long_side=2600, rotate_deg=0.0, blur=1.1, shadow=0.35, noise=6,
               tilt=0.08, jpeg_q=85) -> bytes:
    rng = random.Random(seed)
    h, w = page.shape[:2]
    # desk around the page
    pad = int(0.12 * h)
    desk = np.full((h + 2 * pad, w + 2 * pad, 3), (120, 140, 160), np.uint8)
    desk[pad:pad + h, pad:pad + w] = page
    H, W = desk.shape[:2]
    # perspective: move the corners randomly
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[rng.uniform(0, tilt) * W, rng.uniform(0, tilt) * H],
                      [W - rng.uniform(0, tilt) * W, rng.uniform(0, tilt) * H],
                      [W - rng.uniform(0, tilt) * W, H - rng.uniform(0, tilt) * H],
                      [rng.uniform(0, tilt) * W, H - rng.uniform(0, tilt) * H]])
    M = cv2.getPerspectiveTransform(src, dst)
    if rotate_deg:
        R = cv2.getRotationMatrix2D((W / 2, H / 2), rotate_deg, 1.0)
        R = np.vstack([R, [0, 0, 1]])
        M = R @ M
    photo = cv2.warpPerspective(desk, M, (W, H), borderValue=(110, 130, 150))
    # shadow gradient (one side darker)
    grad = np.linspace(1.0, 1.0 - shadow, W, dtype=np.float32)[None, :, None]
    photo = np.clip(photo.astype(np.float32) * grad, 0, 255)
    photo = np.clip(photo + np.random.default_rng(seed).normal(0, noise, photo.shape), 0, 255).astype(np.uint8)
    s = long_side / max(H, W)
    photo = cv2.resize(photo, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    if blur:
        photo = cv2.GaussianBlur(photo, (0, 0), blur)
    ok, buf = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
    return buf.tobytes()


def scribble_marker(page, marker_id, color=(40, 30, 120)):
    """Pen scribble across one of the black squares (as seen on a real sheet)."""
    x, y = L.MARKERS[marker_id]
    s = L.MARKER_SIZE
    pts = [(x - 3, y + s + 4), (x + 2, y - 4), (x + 5, y + s + 3), (x + s - 2, y - 5), (x + s + 3, y + s + 2)]
    for a, b in zip(pts, pts[1:]):
        cv2.line(page, (int(a[0] * PX), int(a[1] * PX)), (int(b[0] * PX), int(b[1] * PX)), color, max(2, int(0.5 * PX)), cv2.LINE_AA)


def tick(page, cx_mm, cy_mm, color=(25, 25, 25)):
    """A tick drawn through the bubble instead of filling it."""
    p = [(cx_mm - 1.4, cy_mm - 0.1), (cx_mm - 0.3, cy_mm + 1.2), (cx_mm + 2.4, cy_mm - 2.0)]
    for a, b in zip(p, p[1:]):
        cv2.line(page, (int(a[0] * PX), int(a[1] * PX)), (int(b[0] * PX), int(b[1] * PX)), color, max(2, int(0.45 * PX)), cv2.LINE_AA)


def run_case(exam, version, name, student="135790", seed=1, pen=(25, 25, 25), scribble=None, ticks=False, **photo_kw):
    lay, info = sheet_info(exam, version)
    key = answer_key(version)
    page = render_sheet(exam, version)
    if scribble is not None:
        scribble_marker(page, scribble)
    if ticks:
        # a student who ticks instead of filling: the reader must never read a DIFFERENT letter;
        # blank/weak flags are the expected outcome
        for col, d in enumerate(student):
            fill(page, *lay.id_bubbles(col)[int(d)], pen)
        for p in range(lay.n_questions):
            tick(page, *lay.question_bubbles(p)[L.LETTERS.index(key[p])], pen)
        photo = fake_photo(page, seed, **photo_kw)
        res = read_sheet(photo, lambda qr: lay)
        if res.overlay is not None:
            cv2.imwrite(os.path.join(OUT, f"{name}_overlay.jpg"), res.overlay, [cv2.IMWRITE_JPEG_QUALITY, 80])
        bad = [(p + 1, a, key[p]) for p, a in enumerate(res.answers) if a and a != "*" and a != key[p]]
        print(f"--- {name} (ticks instead of fills)")
        print(f"    read as ticked letter: {sum(1 for p, a in enumerate(res.answers) if a == key[p])}/{lay.n_questions}, "
              f"blank: {len(res.blank)}, weak-flagged: {len(res.weak)}, wrong letter: {bad}")
        print("    RESULT:", "PASS" if not bad else "FAIL")
        return not bad
    # student number
    for col, d in enumerate(student):
        fill(page, *lay.id_bubbles(col)[int(d)], pen)
    # answers: mostly right, some wrong, Q3 blank, Q7 double, Q10 weak (light grey)
    expected = []
    for p in range(lay.n_questions):
        bubbles = lay.question_bubbles(p)
        k = L.LETTERS.index(key[p])
        if p == 2:
            expected.append("")
            continue
        if p == 6:
            fill(page, *bubbles[k], pen)
            fill(page, *bubbles[(k + 1) % len(bubbles)], pen)
            expected.append("*")
            continue
        if p == 9:
            fill(page, *bubbles[k], (165, 165, 165), scale=0.85)  # faint pencil-like mark
            expected.append(key[p])
            continue
        if p % 4 == 1:  # a wrong answer now and then
            k = (k + 2) % len(bubbles)
        fill(page, *bubbles[k], pen)
        expected.append(L.LETTERS[k])
    photo = fake_photo(page, seed, **photo_kw)
    with open(os.path.join(OUT, f"{name}_photo.jpg"), "wb") as f:
        f.write(photo)

    res = read_sheet(photo, lambda qr: lay if qr["set_id"] == exam["id"] and qr["version"] == version["version"] else None)
    if res.overlay is not None:
        cv2.imwrite(os.path.join(OUT, f"{name}_overlay.jpg"), res.overlay, [cv2.IMWRITE_JPEG_QUALITY, 80])

    wrong = [(p + 1, e, a) for p, (e, a) in enumerate(zip(expected, res.answers)) if e != a]
    print(f"--- {name}")
    print(f"    markers {res.markers_found} err {res.marker_error:.2f}px | QR {res.qr_raw!r}")
    print(f"    student {res.student_no} (expected S{student}) | flags: {res.flags}")
    print(f"    mismatches: {wrong}")
    print(f"    score {score_answers(res.answers, key)}/{lay.n_questions} (expected {score_answers(expected, key)})")
    if res.darkness:
        empties = [d for row, a in zip(res.darkness, res.answers) for i, d in enumerate(row)
                   if a and a != '*' and L.LETTERS[i] != a]
        marked = [max(row) for row, a in zip(res.darkness, res.answers) if a and a != '*']
        print(f"    darkness: empty bubbles max {max(empties):.2f} | marked bubbles min {min(marked):.2f}")
    good = (res.student_no == f"S{student}" and not wrong and res.qr is not None)
    print("    RESULT:", "PASS" if good else "FAIL")
    return good


if __name__ == "__main__":
    exam = sample_exam(30)
    results = []
    results.append(run_case(exam, exam["versions"][1], "v2_upright", seed=1))
    results.append(run_case(exam, exam["versions"][2], "v3_rotated90", seed=2, rotate_deg=90, student="004518"))
    results.append(run_case(exam, exam["versions"][0], "v1_upsidedown_blue", seed=3, rotate_deg=180,
                            pen=(160, 60, 20), student="990071"))
    results.append(run_case(exam, exam["versions"][1], "v2_small_dark", seed=4, long_side=1800, shadow=0.5,
                            blur=1.6, tilt=0.12, student="123456"))
    results.append(run_case(exam, exam["versions"][0], "v1_scribbled_marker", seed=5, scribble=2, student="445566"))
    results.append(run_case(exam, exam["versions"][2], "v3_ticks", seed=6, ticks=True, student="778899"))
    print("\nALL PASS" if all(results) else "\nSOME CASES FAILED")
    sys.exit(0 if all(results) else 1)
