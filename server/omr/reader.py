"""Reading a photographed answer sheet with OpenCV.

Pipeline
1. find the six ArUco markers (their ids tell us which corner is which, so the
   photo may be rotated any way);
2. compute the perspective transform photo -> template and "flatten" the page
   into a 1680x2376 image where every bubble sits at a known position
   (two transforms, one per half of the page, so a slightly curled sheet is
   still read correctly);
3. decode the QR code of the flattened page -> exam set + version;
4. measure the darkness inside every bubble of the student-number block and of
   the answer grid, decide blank / marked / several marks;
5. draw an overlay image (green = read answer, red = several marks,
   orange = weak or no mark) that the review page shows.

The two darkness thresholds have defaults below; the app passes the values from Settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

import cv2
import numpy as np

from . import layout as L
from .layout import SheetLayout, parse_qr_payload

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
MAX_SIDE = 3000            # photos are downscaled to this before processing
T_MARK = 0.35              # darkness >= this  -> a filled bubble
T_WEAK = 0.20              # darkness between T_WEAK and T_MARK -> weak or no mark (blank, but flagged)
RATIO = 0.55               # a second bubble counts as "also marked" if >= RATIO x the darkest one
CHECK_RATIO = 0.40         # a filled-looking second bubble between CHECK_RATIO and RATIO x the darkest
                           # one is not counted, but the sheet goes to review so someone looks at it
MAX_MARKER_ERR_PX = 6.0    # reprojection error above this -> flag "alignment"


@dataclass
class ReadResult:
    ok: bool = False
    flags: list[str] = field(default_factory=list)
    markers_found: list[int] = field(default_factory=list)
    marker_error: float = 0.0
    qr_raw: str = ""
    qr: Optional[dict] = None
    layout: Optional[SheetLayout] = None
    student_digits: list[str] = field(default_factory=list)   # '0'..'9', '' blank, '*' several
    student_no: str = ""                                      # e.g. S0023?732 (prefix + digits, ? = unreadable)
    answers: list[str] = field(default_factory=list)          # letter, '' blank, '*' several
    weak: list[int] = field(default_factory=list)             # 0-based positions
    multiple: list[int] = field(default_factory=list)
    blank: list[int] = field(default_factory=list)
    check: list[int] = field(default_factory=list)            # read, but a second bubble is nearly as dark
    darkness: list[list[float]] = field(default_factory=list)
    id_darkness: list[list[float]] = field(default_factory=list)
    rectified: Optional[np.ndarray] = None
    overlay: Optional[np.ndarray] = None

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "flags": self.flags,
            "markers_found": self.markers_found,
            "marker_error_px": round(self.marker_error, 2),
            "qr": self.qr_raw,
            "student_no": self.student_no,
            "student_digits": self.student_digits,
            "answers": self.answers,
            "weak": [p + 1 for p in self.weak],
            "multiple": [p + 1 for p in self.multiple],
            "blank": [p + 1 for p in self.blank],
            "check": [p + 1 for p in self.check],
        }


# ---------------------------------------------------------------------------
def load_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("The uploaded file is not an image I can decode.")
    h, w = img.shape[:2]
    s = MAX_SIDE / max(h, w)
    if s < 1.0:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return img


def _detector(win_max: int = 75, min_perim: float = 0.015):
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMin = 5
    params.adaptiveThreshWinSizeMax = win_max
    params.adaptiveThreshWinSizeStep = 10
    params.minMarkerPerimeterRate = min_perim
    return cv2.aruco.ArucoDetector(ARUCO_DICT, params)


def detect_markers(gray: np.ndarray) -> dict[int, np.ndarray]:
    """id -> 4x2 float32 corners (TL, TR, BR, BL as printed)."""
    found: dict[int, np.ndarray] = {}

    def run(img, det):
        corners, ids, _ = det.detectMarkers(img)
        if ids is None:
            return
        for c, i in zip(corners, ids.flatten()):
            i = int(i)
            if i in L.MARKERS and i not in found:
                found[i] = c.reshape(4, 2).astype(np.float32)

    run(gray, _detector())
    if len(found) < len(L.MARKERS):
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        run(clahe.apply(gray), _detector(win_max=115, min_perim=0.01))
    return found


def count_duplicate_markers(gray: np.ndarray) -> int:
    """How many of our marker ids appear more than once (= more than one sheet in the photo)."""
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None:
        return 0
    seen = {}
    for i in ids.flatten():
        i = int(i)
        if i in L.MARKERS:
            seen[i] = seen.get(i, 0) + 1
    return sum(1 for v in seen.values() if v > 1)


def _homography(found: dict[int, np.ndarray], ids) -> Optional[np.ndarray]:
    src, dst = [], []
    for i in ids:
        if i in found:
            src.append(found[i])
            dst.append(np.array(L.marker_corners_mm(i), np.float32) * L.PX_PER_MM)
    if len(src) < 2:
        return None
    src = np.vstack(src)
    dst = np.vstack(dst)
    H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    return H


def _reprojection_error(H: np.ndarray, found: dict[int, np.ndarray]) -> float:
    errs = []
    for i, pts in found.items():
        tpl = np.array(L.marker_corners_mm(i), np.float32) * L.PX_PER_MM
        proj = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H).reshape(-1, 2)
        errs.append(np.linalg.norm(proj - tpl, axis=1).mean())
    return float(np.mean(errs)) if errs else 0.0


def rectify(img: np.ndarray, found: dict[int, np.ndarray]):
    """Returns (rectified BGR image, reprojection error px) or (None, 0)."""
    W, Hh = L.TEMPLATE_PX
    H_all = _homography(found, L.MARKERS.keys())
    if H_all is None:
        return None, 0.0
    err = _reprojection_error(H_all, found)
    white = (255, 255, 255)
    top_ids = [i for i in L.TOP_MARKERS if i in found]
    bot_ids = [i for i in L.BOTTOM_MARKERS if i in found]
    if len(top_ids) >= 3 and len(bot_ids) >= 3 and len(found) >= 5:
        H_top = _homography(found, top_ids)
        H_bot = _homography(found, bot_ids)
        mid = L.mm2px(L.MID_Y)
        out = cv2.warpPerspective(img, H_top, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=white)
        bot = cv2.warpPerspective(img, H_bot, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=white)
        out[mid:] = bot[mid:]
        return out, err
    out = cv2.warpPerspective(img, H_all, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=white)
    return out, err


# ---------------------------------------------------------------------------
def _qr_detectors():
    dets = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        dets.append(cv2.QRCodeDetectorAruco())
    return dets


def decode_qr(rect_gray: np.ndarray) -> str:
    m = 3.0
    x0, y0 = L.mm2px(L.QR_X - m), L.mm2px(L.QR_Y - m)
    x1, y1 = L.mm2px(L.QR_X + L.QR_SIZE + m), L.mm2px(L.QR_Y + L.QR_SIZE + m)
    crop = rect_gray[y0:y1, x0:x1]
    variants = [crop]
    for s in (2, 3):
        variants.append(cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC))
    _, otsu = cv2.threshold(variants[1], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    variants.append(cv2.GaussianBlur(variants[1], (0, 0), 1.2))
    for v in variants:
        for det in _qr_detectors():
            try:
                data, pts, _ = det.detectAndDecode(v)
            except cv2.error:
                continue
            if data:
                return data
    # last resort: the whole rectified page
    for det in _qr_detectors():
        try:
            data, pts, _ = det.detectAndDecode(rect_gray)
        except cv2.error:
            continue
        if data:
            return data
    return ""


# ---------------------------------------------------------------------------
def normalize(gray: np.ndarray) -> np.ndarray:
    """Divide by the local paper brightness so shadows do not look like ink. 255 = paper."""
    k = L.mm2px(10) | 1
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), 6)
    bg = np.maximum(bg, 1)
    norm = cv2.divide(gray, bg, scale=255)
    return np.clip(norm, 0, 255).astype(np.uint8)


@lru_cache(maxsize=8)
def _disc(r: int) -> np.ndarray:
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= r * r


def darkness(norm: np.ndarray, cx_mm: float, cy_mm: float) -> float:
    r = L.mm2px(L.SAMPLE_R)
    cx, cy = L.mm2px(cx_mm), L.mm2px(cy_mm)
    patch = norm[cy - r:cy + r + 1, cx - r:cx + r + 1]
    mask = _disc(r)
    if patch.shape != mask.shape:
        return 0.0
    return 1.0 - float(patch[mask].mean()) / 255.0


def decide(ds: list[float], t_mark: float = T_MARK, t_weak: float = T_WEAK):
    """-> (status, chosen indices). status: blank | weak | ok | multiple"""
    if not ds:
        return "blank", []
    m = max(ds)
    k = int(np.argmax(ds))
    if m < t_weak:
        return "blank", []
    if m < t_mark:
        return "weak", [k]
    cands = [i for i, d in enumerate(ds) if d >= t_mark and d >= RATIO * m]
    if len(cands) == 1:
        return "ok", cands
    return "multiple", cands


# ---------------------------------------------------------------------------
GREEN, RED, ORANGE, GREY, BLUE = (40, 170, 40), (30, 30, 230), (0, 140, 255), (170, 170, 170), (200, 90, 0)


def _circle(img, cx_mm, cy_mm, color, thick):
    cv2.circle(img, (L.mm2px(cx_mm), L.mm2px(cy_mm)), L.mm2px(L.BUBBLE_R) + 3, color, thick, cv2.LINE_AA)


def read_sheet(image_bytes: bytes,
               layout_for: Callable[[dict], Optional[SheetLayout]] | None = None,
               t_mark: float = T_MARK, t_weak: float = T_WEAK) -> ReadResult:
    """`layout_for(qr_dict)` returns the SheetLayout of that set/version, or None if unknown.
    t_mark / t_weak: darkness (0..1) from which a bubble counts as filled / as a weak mark."""
    if not (0.0 < t_weak < t_mark <= 1.0):
        t_mark, t_weak = T_MARK, T_WEAK
    res = ReadResult()
    img = load_image(image_bytes)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    found = detect_markers(gray)
    res.markers_found = sorted(found)
    if len(found) >= 2 and count_duplicate_markers(gray) >= 2:
        res.flags.append("more than one answer sheet in the picture - photograph one sheet at a time")
        return res
    if len(found) < 2:
        res.flags.append("markers: fewer than 2 of the 6 black squares were found - retake the photo "
                         "with the whole sheet in view")
        return res
    if len(found) < 4:
        res.flags.append(f"markers: only {len(found)} of 6 squares found - alignment may be poor")

    rect, err = rectify(img, found)
    if rect is None:
        res.flags.append("markers: could not compute the page transform")
        return res
    res.rectified = rect
    res.marker_error = err
    if err > MAX_MARKER_ERR_PX:
        res.flags.append(f"alignment: marker error {err:.1f}px - sheet may be bent or folded")

    rgray = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
    res.qr_raw = decode_qr(rgray)
    res.qr = parse_qr_payload(res.qr_raw) if res.qr_raw else None
    if not res.qr:
        res.flags.append("qr: the QR code could not be read - set the version by hand on the review page")

    lay = None
    if res.qr and layout_for:
        lay = layout_for(res.qr)
        if lay is None:
            res.flags.append(f"set: exam set '{res.qr['set_id']}' is not in this database")
    if lay is None and res.qr:
        q = res.qr
        try:
            lay = SheetLayout(q["n_questions"], [q["max_opts"]] * q["n_questions"], q["id_digits"])
        except ValueError:
            lay = None
    res.layout = lay

    ink = cv2.min(cv2.min(rect[:, :, 0], rect[:, :, 1]), rect[:, :, 2])   # darkest channel: blue pen reads as dark
    norm = normalize(ink)
    overlay = rect.copy()

    if lay is not None:
        # student number
        digits = []
        for col in range(lay.id_digits):
            ds = [darkness(norm, cx, cy) for (cx, cy) in lay.id_bubbles(col)]
            res.id_darkness.append([round(d, 3) for d in ds])
            status, chosen = decide(ds, t_mark, t_weak)
            if status == "ok":
                digits.append(str(chosen[0]))
                _circle(overlay, *lay.id_bubbles(col)[chosen[0]], GREEN, 3)
            elif status == "weak":
                # A faint column is unreadable, not a digit: the printed digit inside an empty bubble
                # can be that dark, and a wrong but plausible number would file the sheet under
                # another student. Left as "?" so the sheet goes to review.
                digits.append("")
                _circle(overlay, *lay.id_bubbles(col)[chosen[0]], ORANGE, 3)
            elif status == "multiple":
                digits.append("*")
                for k in chosen:
                    _circle(overlay, *lay.id_bubbles(col)[k], RED, 3)
            else:
                digits.append("")
        res.student_digits = digits
        res.student_no = lay.id_prefix + "".join(d if d.isdigit() else "?" for d in digits)
        if any(not d.isdigit() for d in digits):
            res.flags.append("student number incomplete: " + res.student_no)

        # answers
        for p in range(lay.n_questions):
            bubbles = lay.question_bubbles(p)
            ds = [darkness(norm, cx, cy) for (cx, cy) in bubbles]
            res.darkness.append([round(d, 3) for d in ds])
            status, chosen = decide(ds, t_mark, t_weak)
            if status == "ok":
                res.answers.append(L.LETTERS[chosen[0]])
                _circle(overlay, *bubbles[chosen[0]], GREEN, 3)
                m = ds[chosen[0]]
                second = max((d for i, d in enumerate(ds) if i != chosen[0]), default=0.0)
                if second >= max(t_mark, CHECK_RATIO * m):
                    res.check.append(p)
                    _circle(overlay, *bubbles[ds.index(second)], ORANGE, 3)
            elif status == "weak":
                # Counted as blank until someone confirms it: in practice a faint reading is far more
                # often an empty question (printed letter, smudge) than a very light mark.
                res.answers.append("")
                res.weak.append(p)
                _circle(overlay, *bubbles[chosen[0]], ORANGE, 3)
            elif status == "multiple":
                res.answers.append("*")
                res.multiple.append(p)
                for k in chosen:
                    _circle(overlay, *bubbles[k], RED, 3)
            else:
                res.answers.append("")
                res.blank.append(p)
        if res.multiple:
            res.flags.append("several marks in question(s) " + ", ".join(str(p + 1) for p in res.multiple))
        if res.weak:
            res.flags.append("weak or no mark in question(s) " + ", ".join(str(p + 1) for p in res.weak)
                             + " (counted as blank)")
        if res.check:
            res.flags.append("a second bubble is nearly as dark in question(s) "
                             + ", ".join(str(p + 1) for p in res.check) + " (check it)")
        if res.blank:
            res.flags.append("blank question(s) " + ", ".join(str(p + 1) for p in res.blank))

    # text banner on the overlay
    cv2.rectangle(overlay, (L.mm2px(24), L.mm2px(0)), (L.mm2px(186), L.mm2px(9)), (255, 255, 255), -1)
    banner = f"{res.student_no or '?'}   QR: {res.qr_raw or 'not read'}   markers: {len(found)}/6"
    cv2.putText(overlay, banner, (L.mm2px(26), L.mm2px(6.5)), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                BLUE, 2, cv2.LINE_AA)
    res.overlay = overlay
    res.ok = (lay is not None) and (res.qr is not None) and all(d.isdigit() for d in res.student_digits)
    return res


def annotate(overlay: np.ndarray, text: str, color=BLUE) -> np.ndarray:
    """Second banner line, used by the app to add the score."""
    out = overlay.copy()
    cv2.putText(out, text, (L.mm2px(60), L.mm2px(105)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
    return out


def encode_jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()
