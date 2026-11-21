"""The student's page: one scanned sheet with every question marked right / wrong / blank / several,
plus the numbers the reader measured, so a flagged mark can be judged without re-reading the photo.

Everything positional comes from layout.py, in the pixels of the reader's rectified image, so the
highlights drawn by the browser land exactly on the bubbles of the stored overlay image.
"""
from __future__ import annotations

import json
from statistics import mean

from . import layout as L
from .i18n import N_, tr
from .layout import SheetLayout
from .randomize import answer_key
from .stats import text_parts

PAD_MM = 0.9          # margin of the tinted row box around the bubbles
# rings on the close-up of one row (question-by-question table, PDF report): bubbles are 48 px apart
# and the crop is 48 px high, so the ring must stay inside 24 px from the centre
CROP_RING_R = 20      # px, around a bubble of 16.8 px
CROP_RING_W = 4       # px, stroke (outer edge 22 px < 24)
# English here (this is evaluated at import time); translated with tr() where the rows are built
OUTCOME_TEXT = {"right": N_("right"), "wrong": N_("wrong"), "blank": N_("blank"), "multiple": N_("several marks")}


def _band(pct: float, bands: list[tuple[float, str]]) -> str:
    for threshold, name in bands:
        if pct >= threshold:
            return name
    return ""


def build(exam: dict, version: dict, lay: SheetLayout, scan: dict, students: dict[str, str],
          settings: dict) -> dict:
    questions = exam["questions"]
    key = answer_key(version)
    raw = json.loads(scan.get("raw") or "{}")
    darkness = raw.get("darkness") or []
    weak_read = set(raw.get("weak") or [])             # 1-based positions, as read at scan time
    multiple_read = set(raw.get("multiple") or [])
    px = L.PX_PER_MM
    r_px = L.BUBBLE_R * px

    rows, counts = [], {"right": 0, "wrong": 0, "blank": 0, "multiple": 0}
    for p, item in enumerate(version["questions"]):
        q = questions[item["q"]]
        a, k = scan["answers"][p], key[p]
        n_opts = len(item["choices"])
        if a == k:
            outcome = "right"
        elif a == "*":
            outcome = "multiple"
        elif a and a in L.LETTERS and L.LETTERS.index(a) < n_opts:    # ("" in "ABCDEF" is True in Python)
            outcome = "wrong"
        else:
            outcome = "blank"
        counts[outcome] += 1
        bubbles = lay.question_bubbles(p)
        marked_idx = L.LETTERS.index(a) if outcome in ("right", "wrong") else None
        key_idx = L.LETTERS.index(k)
        ds = darkness[p] if p < len(darkness) and darkness[p] else []
        others = sorted(ds, reverse=True)[1:2]
        # geometry in pixels of the rectified image
        nx, ny = lay.question_number_anchor(p)
        x0 = (nx - L.NUM_W + 1.0 - PAD_MM) * px
        x1 = (bubbles[-1][0] + L.BUBBLE_R + PAD_MM) * px
        y0 = (bubbles[0][1] - L.BUBBLE_R - PAD_MM) * px
        y1 = (bubbles[0][1] + L.BUBBLE_R + PAD_MM) * px
        rows.append({
            "pos": p + 1, "q": item["q"] + 1,
            "text": " ".join(q["text"].replace("```", " ").split())[:110],
            "outcome": outcome, "outcome_text": tr(OUTCOME_TEXT[outcome]),
            "marked": a, "key": k,
            "marked_text": q["choices"][item["choices"][marked_idx]]["text"][:70] if marked_idx is not None else "",
            "key_text": q["choices"][q["correct"]]["text"][:70],
            "weak": (p + 1) in weak_read, "multiple_read": (p + 1) in multiple_read,
            # the faint mark the reader saw, offered as "count as X" when it was counted as blank
            "suggest": L.LETTERS[ds.index(max(ds))] if ((p + 1) in weak_read and outcome == "blank" and ds
                                                       and ds.index(max(ds)) < n_opts) else "",
            "marked_pct": round(100 * ds[marked_idx]) if marked_idx is not None and marked_idx < len(ds) else None,
            "max_pct": round(100 * max(ds)) if ds else None,
            "next_pct": round(100 * others[0]) if others else None,
            "box": [round(x0), round(y0), round(x1 - x0), round(y1 - y0)],
            "key_bubble": [round(bubbles[key_idx][0] * px), round(bubbles[key_idx][1] * px)],
            "marked_bubble": [round(bubbles[marked_idx][0] * px), round(bubbles[marked_idx][1] * px)]
            if marked_idx is not None else None,
        })
        # the same rings for the close-up of the row, relative to its box: red on the bubble a wrong
        # answer was marked in, green on the correct bubble whenever the answer was not right
        bx, by = rows[-1]["box"][0], rows[-1]["box"][1]
        kb, mb = rows[-1]["key_bubble"], rows[-1]["marked_bubble"]
        rows[-1]["crop_key"] = [kb[0] - bx, kb[1] - by] if outcome != "right" else None
        rows[-1]["crop_marked"] = [mb[0] - bx, mb[1] - by] if outcome == "wrong" and mb else None

    pct = round(100.0 * scan["score"] / scan["max_score"], 1) if scan.get("max_score") else None
    bands = settings.get("bands_parsed") or []
    return {
        "scan_id": scan["id"], "student_no": scan["student_no"] or "?",
        "name": students.get(scan["student_no"] or "", ""),
        "version": scan["version"], "score": scan["score"], "max_score": scan["max_score"],
        "percent": pct, "band": _band(pct, bands) if pct is not None else "",
        "status": scan["status"], "flags": scan["flags"], "created": scan["created"],
        "image": scan.get("overlay") or scan.get("image"), "photo": scan.get("image"),
        "has_overlay": bool(scan.get("overlay")),
        "width": L.TEMPLATE_PX[0], "height": L.TEMPLATE_PX[1], "r_px": round(r_px),
        "rows": rows, "counts": counts, "n": len(rows),
        "crop_ring_r": CROP_RING_R, "crop_ring_w": CROP_RING_W,
        "n_weak": sum(1 for r in rows if r["weak"]),
        "mark_pct": settings.get("mark_pct", 35), "weak_pct": settings.get("weak_pct", 20),
        "mean_dark_marked": round(mean(r["marked_pct"] for r in rows if r["marked_pct"] is not None))
        if any(r["marked_pct"] is not None for r in rows) else None,
    }


# ---------------------------------------------------------------------------
# the same highlights drawn on the image itself (full-size view, PDF report)
# ---------------------------------------------------------------------------
def hex_to_bgr(h: str, fallback=(0, 0, 0)) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) != 6:
        return fallback
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return fallback
    return (b, g, r)


def colours(settings: dict) -> dict[str, str]:
    """Outcome -> hex colour, from Settings (with the defaults as fallback)."""
    from .db import DEFAULT_SETTINGS
    out = {}
    for outcome in ("right", "wrong", "blank", "multiple"):
        key = f"color_{outcome}"
        val = str(settings.get(key) or "")
        out[outcome] = val if len(val) == 7 and val.startswith("#") else DEFAULT_SETTINGS[key]
    return out


def render_marked(overlay, rows: list[dict], cols: dict[str, str], alpha: float = 0.28):
    """The reader's overlay image with every row tinted by its outcome, a ring in the 'right'
    colour on the correct bubble when the answer was not right, and a ring in the 'wrong' colour
    on the bubble a wrong answer was marked in. Returns a new BGR image."""
    import cv2
    import numpy as np

    img = overlay.copy()
    tint = img.copy()
    bgr = {k: hex_to_bgr(v) for k, v in cols.items()}
    for r in rows:
        x, y, w, h = r["box"]
        cv2.rectangle(tint, (x, y), (x + w, y + h), bgr[r["outcome"]], -1)
    img = cv2.addWeighted(tint, alpha, img, 1 - alpha, 0)
    r_ring = int(round(L.BUBBLE_R * L.PX_PER_MM)) + 7
    for r in rows:
        if r["outcome"] == "wrong" and r["marked_bubble"]:
            cv2.circle(img, tuple(r["marked_bubble"]), r_ring, bgr["wrong"], 6, cv2.LINE_AA)
        if r["outcome"] != "right":
            cv2.circle(img, tuple(r["key_bubble"]), r_ring, bgr["right"], 6, cv2.LINE_AA)
    # a small legend strip at the bottom of the page (the sheet's bottom margin is empty)
    y0 = img.shape[0] - int(5.5 * L.PX_PER_MM)
    x = int(24 * L.PX_PER_MM)
    for outcome, label in (("right", "right"), ("wrong", "wrong (ring = correct bubble)"), ("blank", "blank"),
                           ("multiple", "several marks")):
        sw = np.full((22, 22, 3), bgr[outcome], np.uint8)
        patch = img[y0:y0 + 22, x:x + 22]
        img[y0:y0 + 22, x:x + 22] = cv2.addWeighted(sw, 0.6, patch, 0.4, 0)
        cv2.putText(img, label, (x + 30, y0 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (60, 60, 60), 2, cv2.LINE_AA)
        x += 30 + 15 * len(label) + 40
    return img


def crop(overlay, row: dict):
    """The row of bubbles of one position, as photographed (BGR)."""
    x, y, w, h = row["box"]
    return overlay[max(0, y):y + h, max(0, x):x + w].copy()


def crop_ringed(overlay, row: dict, cols: dict[str, str]):
    """crop() with the rings of the question-by-question table: the 'wrong' colour on the bubble a
    wrong answer was marked in, the 'right' colour on the correct bubble when the answer was not right."""
    import cv2

    img = crop(overlay, row)
    if not img.size:
        return img
    ox, oy = min(0, row["box"][0]), min(0, row["box"][1])   # crop() clamps a box that starts off the image
    for key, outcome in (("crop_marked", "wrong"), ("crop_key", "right")):
        c = row.get(key)
        if c:
            cv2.circle(img, (c[0] + ox, c[1] + oy), CROP_RING_R, hex_to_bgr(cols[outcome]), CROP_RING_W, cv2.LINE_AA)
    return img
