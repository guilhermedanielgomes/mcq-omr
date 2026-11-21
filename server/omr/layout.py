"""Sheet geometry shared by the PDF generator and the reader.

Everything is in millimetres with the origin at the TOP-LEFT corner of an A4
page.  The PDF generator converts to PDF points (origin bottom-left) and the
reader converts to pixels of its rectified image (PX_PER_MM).  Because both
sides call the same functions, the printed bubbles and the sampled positions
can never drift apart.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

PAGE_W, PAGE_H = 210.0, 297.0  # A4 in mm
PX_PER_MM = 8.0                # resolution of the reader's rectified image
TEMPLATE_PX = (int(PAGE_W * PX_PER_MM), int(PAGE_H * PX_PER_MM))  # 1680 x 2376

# --- ArUco markers (DICT_4X4_50). id -> (x, y) of top-left corner, mm -------
MARKER_SIZE = 12.0
MARKER_CELLS = 6  # 4x4 bits + 1-cell black border
MARKERS = {
    0: (10.0, 10.0),          # top-left
    1: (188.0, 10.0),         # top-right
    2: (10.0, 142.5),         # middle-left
    3: (188.0, 142.5),        # middle-right
    4: (10.0, 275.0),         # bottom-left
    5: (188.0, 275.0),        # bottom-right
}
TOP_MARKERS = (0, 1, 2, 3)
BOTTOM_MARKERS = (2, 3, 4, 5)
MID_Y = 142.5 + MARKER_SIZE / 2  # boundary between the two rectification bands

# --- bubbles -------------------------------------------------------------
BUBBLE_R = 2.1       # printed radius
SAMPLE_R = 1.45      # radius the reader samples (inside the ring)
LETTERS = "ABCDEF"
MAX_OPTIONS = 6
MIN_OPTIONS = 2
MAX_ID_DIGITS = 8

# --- header blocks --------------------------------------------------------
LOGO_X, LOGO_Y, LOGO_H = 26.0, 8.5, 15.0   # header image between the two top markers
LOGO_MAX_W = 156.0                          # may not reach the top-right marker (x = 188)
QR_X, QR_Y, QR_SIZE = 121.0, 27.0, 26.0
ID_X, ID_Y, ID_W, ID_H = 152.0, 26.0, 48.0, 72.0
ID_DIGITS_LEFT = ID_X + 8.0         # digit columns start here (prefix text sits to the left)
ID_WRITE_BOX_Y = 33.0               # small squares where the student writes the digits
ID_WRITE_BOX = 4.4
ID_ROW0_Y = 42.5                    # centre of the "0" row
ID_ROW_PITCH = 5.7

# --- answer grid -----------------------------------------------------------
GRID_TOP, GRID_BOTTOM = 108.0, 272.0
GRID_LEFT, GRID_RIGHT = 24.0, 186.0
ROWS_PER_COL = 25
ROW_PITCH = 6.3
GROUP = 5            # a little extra gap every GROUP rows
GROUP_GAP = 1.5
NUM_W = 7.0          # width of the question-number cell
OPT_PITCH = 6.0      # horizontal distance between bubbles
COL_GAP = 4.0        # distance between question columns


def mm2px(v: float) -> int:
    return int(round(v * PX_PER_MM))


def marker_corners_mm(marker_id: int):
    """Corners in the same order ArUco reports them: TL, TR, BR, BL."""
    x, y = MARKERS[marker_id]
    s = MARKER_SIZE
    return [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]


def column_width(max_opts: int) -> float:
    return NUM_W + max_opts * OPT_PITCH


def columns_that_fit(max_opts: int) -> int:
    return int((GRID_RIGHT - GRID_LEFT + COL_GAP) // (column_width(max_opts) + COL_GAP))


def capacity(max_opts: int) -> int:
    """Maximum number of questions that fit on one sheet for this option count."""
    return columns_that_fit(max_opts) * ROWS_PER_COL


def qr_payload(set_id: str, version: int, n_questions: int, max_opts: int, id_digits: int) -> str:
    return f"M1|{set_id}|{version}|{n_questions}|{max_opts}|{id_digits}"


def parse_qr_payload(data: str):
    """Returns dict or None."""
    try:
        parts = data.strip().split("|")
        if len(parts) != 6 or parts[0] != "M1":
            return None
        return {
            "set_id": parts[1],
            "version": int(parts[2]),
            "n_questions": int(parts[3]),
            "max_opts": int(parts[4]),
            "id_digits": int(parts[5]),
        }
    except Exception:
        return None


@dataclass
class SheetLayout:
    """Positions of everything on one answer sheet."""
    n_questions: int
    options: list[int]            # number of choices for each sheet position
    id_digits: int = 6
    id_prefix: str = ""
    max_opts: int = field(init=False)
    n_cols: int = field(init=False)
    col_w: float = field(init=False)
    x0: float = field(init=False)  # left edge of the first used column
    rows_per_col: int = field(init=False)

    def __post_init__(self):
        if self.n_questions != len(self.options):
            raise ValueError("options list must have one entry per question")
        if not (1 <= self.id_digits <= MAX_ID_DIGITS):
            raise ValueError(f"id_digits must be 1..{MAX_ID_DIGITS}")
        self.max_opts = max(self.options) if self.options else 2
        if self.max_opts > MAX_OPTIONS or self.max_opts < MIN_OPTIONS:
            raise ValueError(f"questions must have {MIN_OPTIONS}..{MAX_OPTIONS} choices")
        cap = capacity(self.max_opts)
        if self.n_questions > cap:
            raise ValueError(
                f"{self.n_questions} questions with up to {self.max_opts} choices do not fit "
                f"on one sheet (maximum {cap}). Reduce the number of questions or choices.")
        self.col_w = column_width(self.max_opts)
        # as few columns as needed (max ROWS_PER_COL rows each), then the questions are spread
        # evenly over them: 30 questions -> 2 columns of 15, 60 -> 3 of 20, 100 -> 4 of 25
        self.n_cols = max(1, math.ceil(self.n_questions / ROWS_PER_COL))
        self.rows_per_col = max(1, math.ceil(self.n_questions / self.n_cols))
        used = self.n_cols * self.col_w + (self.n_cols - 1) * COL_GAP
        self.x0 = GRID_LEFT + (GRID_RIGHT - GRID_LEFT - used) / 2.0

    # ---- answer grid -----------------------------------------------------
    def _row_y(self, row: int) -> float:
        return GRID_TOP + ROW_PITCH / 2 + row * ROW_PITCH + (row // GROUP) * GROUP_GAP

    def question_pos(self, p: int):
        """(col, row) of sheet position p (0-based)."""
        return p // self.rows_per_col, p % self.rows_per_col

    def question_number_anchor(self, p: int):
        """Right edge x and centre y of the number cell."""
        col, row = self.question_pos(p)
        x = self.x0 + col * (self.col_w + COL_GAP) + NUM_W - 1.0
        return x, self._row_y(row)

    def question_bubbles(self, p: int):
        """Centres (x, y) in mm of the bubbles of sheet position p."""
        col, row = self.question_pos(p)
        y = self._row_y(row)
        xb = self.x0 + col * (self.col_w + COL_GAP) + NUM_W
        return [(xb + j * OPT_PITCH + OPT_PITCH / 2, y) for j in range(self.options[p])]

    def column_boxes(self):
        """Rectangles (x, y, w, h) framing each used column."""
        boxes = []
        for c in range(self.n_cols):
            n_rows = min(self.rows_per_col, self.n_questions - c * self.rows_per_col)
            top = GRID_TOP - 1.0
            bottom = self._row_y(n_rows - 1) + ROW_PITCH / 2 + 1.0
            x = self.x0 + c * (self.col_w + COL_GAP) - 1.0
            boxes.append((x, top, self.col_w + 2.0, bottom - top))
        return boxes

    # ---- student number --------------------------------------------------
    def id_col_pitch(self) -> float:
        return (ID_X + ID_W - ID_DIGITS_LEFT - 1.0) / self.id_digits

    def id_col_x(self, col: int) -> float:
        return ID_DIGITS_LEFT + 0.5 + self.id_col_pitch() * (col + 0.5)

    def id_bubbles(self, col: int):
        """Centres of the digit bubbles 0..9 of column col."""
        x = self.id_col_x(col)
        return [(x, ID_ROW0_Y + d * ID_ROW_PITCH) for d in range(10)]

    def id_write_box(self, col: int):
        x = self.id_col_x(col)
        return (x - ID_WRITE_BOX / 2, ID_WRITE_BOX_Y, ID_WRITE_BOX)

    def id_prefix_anchor(self):
        """Right edge x and centre y for the printed prefix (e.g. 'S'), placed just left of the
        first write box so the row reads as the complete student number: S [2][3][7][7][3][2]."""
        first_box_left = self.id_col_x(0) - ID_WRITE_BOX / 2
        return first_box_left - 1.2, ID_WRITE_BOX_Y + ID_WRITE_BOX / 2

    def id_prefix_max_width(self) -> float:
        return self.id_prefix_anchor()[0] - (ID_X + 1.5)

    # ---- reader helpers --------------------------------------------------
    def qr_box_px(self, margin_mm: float = 3.0):
        x0 = mm2px(QR_X - margin_mm)
        y0 = mm2px(QR_Y - margin_mm)
        x1 = mm2px(QR_X + QR_SIZE + margin_mm)
        y1 = mm2px(QR_Y + QR_SIZE + margin_mm)
        return x0, y0, x1, y1
