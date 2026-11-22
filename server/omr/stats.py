"""Class statistics for one exam set, computed from the scans read so far.

Everything is computed on the live scans only (the latest scan of each student, scans with a
score). Per-question figures are mapped back to the ORIGINAL question numbers, so the shuffling
of the versions does not matter.

compute()          the whole class (statistics page)
question_detail()  one question: who picked each answer (question page)
"""
from __future__ import annotations

import math
from statistics import mean, median, pstdev, stdev

from . import layout as L
from .i18n import N_, tr

BLANK, MULTIPLE = "blank", "multiple"


def _skewness(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = mean(xs)
    s = stdev(xs)
    if s == 0:
        return 0.0
    g1 = sum(((x - m) / s) ** 3 for x in xs) / n
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)      # adjusted Fisher-Pearson


def _kurtosis(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    m = mean(xs)
    s = stdev(xs)
    if s == 0:
        return 0.0
    g2 = sum(((x - m) / s) ** 4 for x in xs) / n - 3.0
    return g2


def _quantile(sorted_xs: list[float], q: float) -> float:
    if not sorted_xs:
        return 0.0
    pos = (len(sorted_xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def _shape_text(skew: float | None, n: int) -> str:
    if skew is None:
        return tr("Not enough students yet to judge the shape (need at least 3).")
    few = n < 8          # whole sentences for the translators: with and without the warning
    if abs(skew) < 0.5:
        if few:
            return tr("Roughly symmetric: scores spread evenly around the mean, close to a bell curve "
                      "(only %(n)s students, so take this with care).", n=n)
        return tr("Roughly symmetric: scores spread evenly around the mean, close to a bell curve.")
    if skew <= -1:
        if few:
            return tr("Skewed to the left (strongly): most students scored above the mean and a few low scores "
                      "form a tail on the left - the test was on the easy side for this class "
                      "(only %(n)s students, so take this with care).", n=n)
        return tr("Skewed to the left (strongly): most students scored above the mean and a few low scores "
                  "form a tail on the left - the test was on the easy side for this class.")
    if skew < 0:
        if few:
            return tr("Skewed to the left (moderately): most students scored above the mean and a few low scores "
                      "form a tail on the left - the test was on the easy side for this class "
                      "(only %(n)s students, so take this with care).", n=n)
        return tr("Skewed to the left (moderately): most students scored above the mean and a few low scores "
                  "form a tail on the left - the test was on the easy side for this class.")
    if skew >= 1:
        if few:
            return tr("Skewed to the right (strongly): most students scored below the mean and a few high scores "
                      "form a tail on the right - the test was on the hard side for this class "
                      "(only %(n)s students, so take this with care).", n=n)
        return tr("Skewed to the right (strongly): most students scored below the mean and a few high scores "
                  "form a tail on the right - the test was on the hard side for this class.")
    if few:
        return tr("Skewed to the right (moderately): most students scored below the mean and a few high scores "
                  "form a tail on the right - the test was on the hard side for this class "
                  "(only %(n)s students, so take this with care).", n=n)
    return tr("Skewed to the right (moderately): most students scored below the mean and a few high scores "
              "form a tail on the right - the test was on the hard side for this class.")


def _kr20(item_matrix: list[list[int]], totals: list[float]) -> float | None:
    """Kuder-Richardson 20 reliability (0..1) from the 1/0 item matrix."""
    k = len(item_matrix[0]) if item_matrix else 0
    n = len(item_matrix)
    if k < 2 or n < 2:
        return None
    var_total = pstdev(totals) ** 2
    if var_total == 0:
        return None
    pq = 0.0
    for j in range(k):
        p = sum(row[j] for row in item_matrix) / n
        pq += p * (1 - p)
    return (k / (k - 1)) * (1 - pq / var_total)


def _reliability_text(r: float | None) -> str:
    if r is None:
        return tr("Needs at least 2 students and 2 questions.")
    if r >= 0.8:
        return tr("Good: the test separates stronger and weaker students consistently.")
    if r >= 0.7:
        return tr("Acceptable for a class test.")
    if r >= 0.5:
        return tr("Low: scores are noisy - look at the questions flagged below.")
    return tr("Very low: results depend a lot on luck or on a few questions; check the flagged questions and the key.")


def _band(pct: float, bands: list[tuple[float, str]]) -> str:
    """Name of the grade band a percentage falls in (bands sorted from high to low)."""
    for threshold, name in bands:
        if pct >= threshold:
            return name
    return ""


def text_parts(text: str) -> list[dict]:
    """Question or choice text split into plain parts and ``` fenced code parts (as on the PDF)."""
    out = []
    for i, part in enumerate(str(text).split("```")):
        if i % 2:
            code = part.strip("\n")
            if code.strip():
                out.append({"code": True, "text": code})
        elif part.strip():
            out.append({"code": False, "text": part.strip()})
    return out


# ---------------------------------------------------------------------------
# shared preparation
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# courses (optional: set per student in Settings; nothing changes while no student has one)
# ---------------------------------------------------------------------------
NO_COURSE = "__none__"      # filter value: students with no course (also unknown or unreadable numbers)


def course_key(course: str | None) -> str:
    return " ".join((course or "").split()).casefold()


def course_names(courses: dict[str, str]) -> dict[str, str]:
    """{key: the spelling used most} so 'software eng ' and 'Software Eng' are one course."""
    spell: dict[str, dict[str, int]] = {}
    for c in courses.values():
        k = course_key(c)
        if k:
            spell.setdefault(k, {})
            spell[k][" ".join(c.split())] = spell[k].get(" ".join(c.split()), 0) + 1
    return {k: max(v.items(), key=lambda kv: (kv[1], kv[0]))[0] for k, v in spell.items()}


def course_of(student_no: str | None, courses: dict[str, str]) -> str:
    """Normalised course key of a student number ('' = none)."""
    return course_key(courses.get(student_no or "", ""))


def filter_by_course(scans: list[dict], courses: dict[str, str], course: str) -> list[dict]:
    """The scans of one course ('' = everybody, NO_COURSE = students without a course)."""
    if not course:
        return scans
    want = "" if course == NO_COURSE else course_key(course)
    return [s for s in scans if course_of(s.get("student_no"), courses) == want]


def course_options(scans: list[dict], courses: dict[str, str]) -> list[dict]:
    """Courses of the students in these scans, for the drop-down: [{value, label, n}] (empty when no
    student has a course, so the page looks as it always did)."""
    if not courses:
        return []
    names = course_names(courses)
    count: dict[str, int] = {}
    for s in scans:
        if s.get("superseded"):
            continue
        k = course_of(s.get("student_no"), courses)
        count[k] = count.get(k, 0) + 1
    opts = [{"value": names[k], "label": names[k], "n": n} for k, n in count.items() if k]
    opts.sort(key=lambda o: o["label"].casefold())
    if count.get(""):
        opts.append({"value": NO_COURSE, "label": tr("No course set"), "n": count[""]})
    return opts


def course_label(course: str, courses: dict[str, str]) -> str:
    if not course:
        return ""
    if course == NO_COURSE:
        return tr("No course set")
    return course_names(courses).get(course_key(course), course)


# ---------------------------------------------------------------------------
# topics (optional: a label per question; nothing changes while no question has one)
# ---------------------------------------------------------------------------
TOPIC_BELOW_POINTS = 20.0   # a student this many points or more under the class on a topic: "worth revising"


def question_topic(q: dict) -> str:
    return " ".join(str(q.get("topic") or "").split())


def topic_groups(questions: list[dict]) -> list[tuple[str | None, list[int]]]:
    """[(topic, [original question indices])], topics in alphabetical order (capitals and spaces do not
    count: the first spelling is shown), then (None, ...) for the questions without a topic. Empty when
    no question has a topic."""
    names: dict[str, str] = {}
    groups: dict[str, list[int]] = {}
    none = []
    for qi, q in enumerate(questions):
        t = question_topic(q)
        if not t:
            none.append(qi)
            continue
        k = t.casefold()
        names.setdefault(k, t)
        groups.setdefault(k, []).append(qi)
    if not groups:
        return []
    out = [(names[k], groups[k]) for k in sorted(groups, key=lambda k: (names[k].casefold(), k))]
    if none:
        out.append((None, none))
    return out


def _topic_pct(item_matrix: list[list[int]], qis: list[int]) -> float | None:
    """% of right answers of these students on these questions."""
    if not item_matrix or not qis:
        return None
    return round(100.0 * sum(r[qi] for r in item_matrix for qi in qis) / (len(item_matrix) * len(qis)), 1)


def _topics_summary(prep: dict) -> list[dict]:
    """Class % correct per topic, the hardest topic first; the questions without a topic last."""
    out = [{"topic": t, "n": len(qis), "questions": [qi + 1 for qi in qis], "pct_correct": _topic_pct(prep["item_matrix"], qis)}
           for t, qis in topic_groups(prep["questions"])]
    named = sorted((x for x in out if x["topic"] is not None),
                   key=lambda x: (x["pct_correct"] if x["pct_correct"] is not None else 101, x["topic"].casefold()))
    return named + [x for x in out if x["topic"] is None]


def _prepare(exam: dict, scans: list[dict], students: dict[str, str]) -> dict:
    """Everything both pages need, worked out once and in the same way."""
    questions = exam.get("questions", [])
    versions = {v["version"]: v for v in exam.get("versions", [])}
    n_q = len(questions)
    live = [s for s in scans if not s.get("superseded") and s.get("score") is not None
            and s.get("version") in versions and len(s.get("answers", [])) == len(versions[s["version"]]["questions"])]

    rows = []
    for s in live:
        pct = 100.0 * s["score"] / s["max_score"] if s["max_score"] else 0.0
        rows.append({"scan_id": s["id"], "student_no": s["student_no"], "name": students.get(s["student_no"] or "", ""),
                     "version": s["version"], "score": s["score"], "max_score": s["max_score"], "percent": round(pct, 1),
                     "status": s["status"], "created": s["created"]})

    # what every student marked, by ORIGINAL question:
    # marks[i][qi] = (choice, position, letter) where choice is the original choice index, BLANK or
    # MULTIPLE; position is where the question was printed on that student's sheet (0-based); letter
    # is what they filled there, as printed on their version ("" = blank, "*" = several marks)
    marks = []
    item_matrix = []          # rows = students, cols = original questions, 1/0
    choice_counts = [[0] * (len(q["choices"]) + 2) for q in questions]   # + blank + multiple
    for s in live:
        v = versions[s["version"]]
        mrow = [None] * n_q
        row = [0] * n_q
        for p, item in enumerate(v["questions"]):
            qi = item["q"]
            a = s["answers"][p]
            if a == "*":
                choice_counts[qi][-1] += 1
                mrow[qi] = (MULTIPLE, p, "*")
            elif a and a in L.LETTERS and L.LETTERS.index(a) < len(item["choices"]):
                orig = item["choices"][L.LETTERS.index(a)]
                choice_counts[qi][orig] += 1
                mrow[qi] = (orig, p, a)
                if orig == questions[qi]["correct"]:
                    row[qi] = 1
            else:
                choice_counts[qi][-2] += 1
                mrow[qi] = (BLANK, p, "")
        marks.append(mrow)
        item_matrix.append(row)

    # top and bottom 27 % of the students by number of correct answers (indices into live)
    totals = [sum(r) for r in item_matrix]
    order = sorted(range(len(live)), key=lambda i: totals[i])
    k = max(1, int(round(0.27 * len(live))))
    return {"questions": questions, "versions": versions, "live": live, "rows": rows, "marks": marks,
            "item_matrix": item_matrix, "choice_counts": choice_counts, "totals": totals,
            "k": k, "lower": order[:k], "upper": order[-k:]}


def _question_summary(prep: dict, qi: int) -> dict:
    """% correct, discrimination, flags and answer counts of one original question."""
    q = prep["questions"][qi]
    item_matrix, k = prep["item_matrix"], prep["k"]
    n = len(item_matrix)
    correct = sum(r[qi] for r in item_matrix)
    p_all = correct / n
    disc = None
    if len(prep["live"]) >= 4:
        disc = (sum(item_matrix[i][qi] for i in prep["upper"]) - sum(item_matrix[i][qi] for i in prep["lower"])) / k
    flags = []
    if p_all < 0.3:
        flags.append(N_("hard"))
    elif p_all > 0.9:
        flags.append(N_("easy"))
    if disc is not None and disc < 0.2:
        flags.append(N_("weak discrimination"))
    counts = prep["choice_counts"][qi]
    most = max(range(len(q["choices"])), key=lambda j: counts[j])
    if counts[most] > counts[q["correct"]]:
        flags.append(N_("distractor chosen more than the key - check the key"))
    return {
        "q": qi + 1, "text": " ".join(q["text"].replace("```", " ").split())[:140],
        "correct_choice": L.LETTERS[q["correct"]], "pct_correct": round(100.0 * p_all, 1),
        "discrimination": None if disc is None else round(disc, 2),
        "choices": [{"letter": L.LETTERS[j], "text": c["text"][:80], "count": counts[j],
                     "is_key": j == q["correct"]} for j, c in enumerate(q["choices"])],
        "blank": counts[-2], "multiple": counts[-1], "flags": flags,
    }


# ---------------------------------------------------------------------------
# statistics page
# ---------------------------------------------------------------------------
def compute(exam: dict, scans: list[dict], students: dict[str, str], settings: dict) -> dict:
    prep = _prepare(exam, scans, students)
    questions = prep["questions"]
    n_q = len(questions)
    pass_mark = float(settings.get("pass_mark", 40))
    bands = settings.get("bands_parsed") or []

    n_total = len([s for s in scans if not s.get("superseded")])
    n_review = len([s for s in scans if not s.get("superseded") and s.get("status") != "ok"])

    rows = prep["rows"]
    pcts = [r["percent"] for r in rows]
    out = {
        "set_id": exam["id"], "title": exam.get("meta", {}).get("title", ""), "n_questions": n_q,
        "n_scanned": n_total, "n_scored": len(rows), "n_review": n_review, "pass_mark": pass_mark,
    }
    if not rows:
        out.update({"empty": True, "shape_text": tr("No scored sheets yet."), "histogram": [], "per_question": [],
                    "bands": [], "top": [], "bottom": [], "students": []})
        return out

    srt = sorted(pcts)
    mx, mn = max(pcts), min(pcts)
    m = mean(pcts)
    sd = stdev(pcts) if len(pcts) > 1 else 0.0
    skew = _skewness(pcts)
    kurt = _kurtosis(pcts)
    out.update({
        "mean": round(m, 1), "median": round(median(pcts), 1), "sd": round(sd, 1),
        "min": mn, "max": mx, "q1": round(_quantile(srt, 0.25), 1), "q3": round(_quantile(srt, 0.75), 1),
        "mean_score": round(mean(r["score"] for r in rows), 2),
        "pass_rate": round(100.0 * sum(1 for p in pcts if p >= pass_mark) / len(pcts), 1),
        "n_pass": sum(1 for p in pcts if p >= pass_mark),
        "skewness": None if skew is None else round(skew, 2),
        "kurtosis": None if kurt is None else round(kurt, 2),
        "shape_text": _shape_text(skew, len(pcts)),
        "top": sorted([r for r in rows if r["percent"] == mx], key=lambda r: r["student_no"] or ""),
        "bottom": sorted([r for r in rows if r["percent"] == mn], key=lambda r: r["student_no"] or ""),
        "students": sorted(rows, key=lambda r: (-r["percent"], r["student_no"] or "")),
    })

    # histogram of percentages, 10 bins, with the normal curve of the same mean/sd for comparison
    bins = []
    for b in range(10):
        lo, hi = b * 10, (b + 1) * 10
        cnt = sum(1 for p in pcts if (lo <= p < hi) or (b == 9 and p == 100))
        if sd > 0:
            z_lo, z_hi = (lo - m) / sd, (hi - m) / sd
            expected = len(pcts) * 0.5 * (math.erf(z_hi / math.sqrt(2)) - math.erf(z_lo / math.sqrt(2)))
        else:
            expected = len(pcts) if lo <= m < hi else 0.0
        bins.append({"label": f"{lo}-{hi - 1 if b < 9 else 100}", "lo": lo, "hi": hi, "count": cnt,
                     "expected": round(expected, 2)})
    out["histogram"] = bins

    # grade bands
    band_rows = []
    for i, (threshold, name) in enumerate(bands):
        upper = bands[i - 1][0] if i > 0 else 101.0
        cnt = sum(1 for p in pcts if threshold <= p < upper)
        band_rows.append({"name": name, "from": threshold, "to": upper if upper <= 100 else 100, "count": cnt,
                          "percent": round(100.0 * cnt / len(pcts), 1)})
    out["bands"] = band_rows

    # per question, original numbering
    per_q = [_question_summary(prep, qi) for qi in range(n_q)]
    out["per_question"] = per_q
    r = _kr20(prep["item_matrix"], prep["totals"])
    out["kr20"] = None if r is None else round(r, 2)
    out["kr20_text"] = _reliability_text(r)
    out["hardest"] = sorted(per_q, key=lambda x: x["pct_correct"])[:5]
    out["easiest"] = sorted(per_q, key=lambda x: -x["pct_correct"])[:5]
    out["flagged"] = [x for x in per_q if x["flags"]]
    if topic_groups(questions):        # only when questions have topics: otherwise stats.json is as it was
        out["topics"] = _topics_summary(prep)
    return out


# ---------------------------------------------------------------------------
# question page
# ---------------------------------------------------------------------------
def _note(is_key: bool, count: int, top: int, bottom: int, groups: bool, beats_key: bool) -> tuple[str, bool]:
    """One line on what the numbers of an answer suggest, and whether it is worth a look."""
    if is_key:
        if count == 0:
            return tr("Nobody got this question right. Check the key and the wording."), True
        if not groups:
            return "", False
        if bottom > top:
            return tr("More of the bottom group than the top group got this right. Check the key and the wording."), True
        if top == 0:
            return tr("Nobody in the top group got this right. Check the key and the wording."), True
        if top > bottom:
            return tr("More of the top group than the bottom group got this right, as it should be."), False
        return tr("The top and bottom groups got it right equally often, so this question doesn't separate them."), False
    if count == 0:
        return tr("Nobody picked it, so it isn't working as a distractor (it may be too obviously wrong)."), False
    if beats_key:
        return tr("Picked by more students than the correct answer. Check the key."), True
    if not groups:
        return "", False
    if top > bottom:
        return tr("Picked more by the top group than the bottom group. Check whether it could also be read as correct."), True
    if bottom > top:
        return tr("Picked mostly by the bottom group: a distractor doing its job."), False
    if top == 0:
        return tr("Picked only by students in the middle of the class."), False
    return tr("Picked equally by the top and bottom groups."), False


def question_detail(exam: dict, scans: list[dict], students: dict[str, str], settings: dict, qi: int) -> dict:
    """One original question: every answer with the students who picked it, their results and
    a short note on what the numbers suggest."""
    prep = _prepare(exam, scans, students)
    q = prep["questions"][qi]
    rows, marks = prep["rows"], prep["marks"]
    n = len(rows)
    correct = q.get("correct")
    key_letter = L.LETTERS[correct] if isinstance(correct, int) and 0 <= correct < len(q["choices"]) else "?"
    out = {"set_id": exam["id"], "title": exam.get("meta", {}).get("title", ""), "q": qi + 1,
           "n_questions": len(prep["questions"]), "text_parts": text_parts(q["text"]),
           "key_letter": key_letter, "n_scored": n, "topic": question_topic(q)}
    if not n:
        out.update({"empty": True, "options": []})
        return out

    summary = _question_summary(prep, qi)
    bands = settings.get("bands_parsed") or []
    groups = n >= 4                     # the same rule as the discrimination
    top_set, bottom_set = set(prep["upper"]), set(prep["lower"])

    def student(i: int) -> dict:
        _, pos, letter = marks[i][qi]
        group = ""
        if groups:
            group = "top" if i in top_set else "bottom" if i in bottom_set else ""
        return {**rows[i], "position": pos + 1, "marked": letter,
                "band": _band(rows[i]["percent"], bands), "group": group}

    key_count = prep["choice_counts"][qi][q["correct"]]
    options = []
    picked = [(j, L.LETTERS[j], c["text"]) for j, c in enumerate(q["choices"])]
    picked += [(BLANK, tr("Left blank"), ""), (MULTIPLE, tr("More than one mark"), "")]
    for j, label, text in picked:
        members = [i for i in range(n) if marks[i][qi] and marks[i][qi][0] == j]
        top = sum(1 for i in members if i in top_set) if groups else 0
        bottom = sum(1 for i in members if i in bottom_set) if groups else 0
        is_key = j == q["correct"]
        if j == BLANK:
            note, warn = (tr("Open a scan to check it if you think a light mark was missed.") if members else "", False)
        elif j == MULTIPLE:
            note, warn = (tr("Several marks score zero. If a student clearly crossed one out, open the scan and "
                             "correct it.") if members else "", False)
        else:
            note, warn = _note(is_key, len(members), top, bottom, groups, len(members) > key_count)
        options.append({
            "id": f"choice-{label}" if isinstance(j, int) else j, "letter": label if isinstance(j, int) else "",
            "label": label, "is_answer": isinstance(j, int), "text_parts": text_parts(text), "is_key": is_key,
            "count": len(members), "percent": round(100.0 * len(members) / n, 1),
            "mean": round(mean(rows[i]["percent"] for i in members), 1) if members else None,
            "top": top, "bottom": bottom, "note": note, "warn": warn,
            "students": sorted((student(i) for i in members), key=lambda r: (-r["percent"], r["student_no"] or "")),
        })
    out.update({"empty": False, "pct_correct": summary["pct_correct"], "discrimination": summary["discrimination"],
                "flags": summary["flags"], "class_mean": round(mean(r["percent"] for r in rows), 1),
                "groups": groups, "k": prep["k"], "options": options})
    return out


# ---------------------------------------------------------------------------
# one student (student's page and PDF report)
# ---------------------------------------------------------------------------
EASY_PCT, HARD_PCT = 70.0, 40.0     # class % correct from which a question counts as easy / under which hard
MIN_CLASS_FOR_DIFFICULTY = 5        # fewer scored students: the class % correct says too little


def student_detail(exam: dict, scans: list[dict], students: dict[str, str], settings: dict, scan: dict,
                   history: list[dict] | None = None) -> dict:
    """One scan compared with the class (the same live scans as the statistics page). Only figures
    about the class as a whole are given, never other students' names, so the page and the PDF can
    be handed to the student. `history` = this student's results in other sets (see app.py)."""
    prep = _prepare(exam, scans, students)
    rows, questions = prep["rows"], prep["questions"]
    versions = prep["versions"]
    version = versions.get(scan.get("version"))
    out = {"available": False, "history": history or [], "set_id": exam["id"]}
    if not version or scan.get("score") is None or not scan.get("max_score") \
            or len(scan.get("answers") or []) != len(version["questions"]):
        return out
    # compared at the precision the class rows use (one decimal), so ties, quartiles and differences agree
    pct = round(100.0 * scan["score"] / scan["max_score"], 1)
    counted = any(r["scan_id"] == scan["id"] for r in rows)
    me = scan.get("student_no") or None
    # the class without this student (an older scan is compared with the others, not with the newer one)
    others = [r["percent"] for r in rows if r["scan_id"] != scan["id"] and not (me and r["student_no"] == me)]
    pcts = [r["percent"] for r in rows] if counted else others + [pct]
    n = len(pcts)
    out.update({"available": True, "counted": counted, "n_class": len(rows), "percent": pct})

    # place in the class (ties share the better place)
    out["rank"] = 1 + sum(1 for p in others if p > pct)
    out["n_ranked"] = n
    out["n_above"] = sum(1 for p in others if p > pct)
    out["n_equal"] = sum(1 for p in others if p == pct)
    out["n_below"] = sum(1 for p in others if p < pct)
    out["beats_pct"] = round(100.0 * out["n_below"] / len(others), 1) if others else None
    # mean, median, range etc. of a handful of sheets give away the other students' scores (the
    # report may be handed out): only from MIN_CLASS_FOR_DIFFICULTY scored sheets on
    out["class_ok"] = n >= MIN_CLASS_FOR_DIFFICULTY
    m = mean(pcts)
    sd = stdev(pcts) if n > 1 else 0.0
    nz = lambda x: round(x, 1) or 0.0        # no "-0"
    out.update({"class_mean": round(m, 1), "class_median": round(median(pcts), 1), "class_sd": round(sd, 1),
                "class_max": max(pcts), "class_min": min(pcts),
                "diff_mean": nz(pct - m), "diff_median": nz(pct - median(pcts)),
                "z": (round((pct - m) / sd, 2) or 0.0) if sd > 0 else None})
    out["quartile"] = None
    if not out["class_ok"]:
        for k in ("class_mean", "class_median", "class_sd", "class_max", "class_min", "diff_mean", "diff_median", "z"):
            out[k] = None
    else:
        srt = sorted(pcts)
        q1, q3 = _quantile(srt, 0.25), _quantile(srt, 0.75)
        if q3 > q1:     # all (or nearly all) the same score: quarters mean nothing
            out["quartile"] = (tr("top quarter of the class") if pct >= q3 else tr("bottom quarter of the class")
                               if pct <= q1 else tr("middle half of the class"))

    # what this student got right, by original question and by position on the sheet
    key = [it["answer"] for it in version["questions"]]
    right_q = {}
    for p, item in enumerate(version["questions"]):
        right_q[item["q"]] = scan["answers"][p] == key[p]
    n_pos = len(key)
    half = (n_pos + 1) // 2
    first = sum(1 for p in range(half) if scan["answers"][p] == key[p])
    second = sum(1 for p in range(half, n_pos) if scan["answers"][p] == key[p])
    out["halves"] = [] if n_pos < 2 else [{"label": tr("positions %(a)s-%(b)s", a=1, b=half), "right": first, "n": half,
                      "percent": round(100.0 * first / half, 1) if half else None},
                     {"label": tr("positions %(a)s-%(b)s", a=half + 1, b=n_pos), "right": second, "n": n_pos - half,
                      "percent": round(100.0 * second / (n_pos - half), 1) if n_pos - half else None}]
    blanks_second = sum(1 for p in range(half, n_pos) if not scan["answers"][p])
    blanks_first = sum(1 for p in range(half) if not scan["answers"][p])
    out["halves_note"] = ""
    if out["halves"]:
        d1, d2 = out["halves"][0]["percent"], out["halves"][1]["percent"]
        if blanks_second >= 3 and blanks_second > 2 * blanks_first:
            out["halves_note"] = tr("%(n)s blank answers in the second half of the sheet: "
                                     "possibly ran out of time.", n=blanks_second)
        elif d1 - d2 >= 25:
            out["halves_note"] = tr("Clearly fewer right answers in the second half of the sheet.")
        elif d2 - d1 >= 25:
            out["halves_note"] = tr("Clearly fewer right answers in the first half of the sheet.")

    # by how hard each question was for the class (needs a few students to mean anything)
    out["difficulty"] = []
    out["revise"], out["strong"] = [], []
    out["difficulty_ok"] = len(rows) >= MIN_CLASS_FOR_DIFFICULTY
    if out["difficulty_ok"]:
        im = prep["item_matrix"]
        class_pct = [100.0 * sum(r[qi] for r in im) / len(im) for qi in range(len(questions))]
        groups = [("easy", tr("easy (≥ %(pct)s%% of the class right)", pct=f"{EASY_PCT:g}"), lambda c: c >= EASY_PCT),
                  ("medium", tr("medium (%(lo)s-%(hi)s%%)", lo=f"{HARD_PCT:g}", hi=f"{EASY_PCT:g}"),
                   lambda c: HARD_PCT <= c < EASY_PCT),
                  ("hard", tr("hard (< %(pct)s%%)", pct=f"{HARD_PCT:g}"), lambda c: c < HARD_PCT)]
        for gid, label, test in groups:
            qs = [qi for qi in range(len(questions)) if qi in right_q and test(class_pct[qi])]
            r = sum(1 for qi in qs if right_q[qi])
            out["difficulty"].append({"id": gid, "label": label, "n": len(qs), "right": r,
                                      "percent": round(100.0 * r / len(qs), 1) if qs else None})

        def q_item(qi):
            return {"q": qi + 1, "class_pct": round(class_pct[qi], 1),
                    "text": " ".join(questions[qi]["text"].replace("```", " ").split())[:90]}
        out["revise"] = [q_item(qi) for qi in sorted(right_q, key=lambda qi: (-class_pct[qi], qi))
                         if not right_q[qi] and class_pct[qi] >= EASY_PCT]
        out["strong"] = [q_item(qi) for qi in sorted(right_q, key=lambda qi: (class_pct[qi], qi))
                         if right_q[qi] and class_pct[qi] < HARD_PCT]

    # by topic (only when questions have topics): the student's own % and, from 5 scored sheets, the class %
    out["topics"], out["topic_below"] = [], TOPIC_BELOW_POINTS
    for topic, qis in topic_groups(questions):
        qs = [qi for qi in qis if qi in right_q]
        if not qs:
            continue
        r = sum(1 for qi in qs if right_q[qi])
        mine = round(100.0 * r / len(qs), 1)
        cls = _topic_pct(prep["item_matrix"], qs) if out["difficulty_ok"] else None
        out["topics"].append({"topic": topic, "n": len(qs), "right": r, "percent": mine, "class_pct": cls,
                              "below": topic is not None and cls is not None and cls - mine >= TOPIC_BELOW_POINTS})
    return out


def n_counted(exam: dict, scans: list[dict]) -> int:
    """How many of these sheets count in the figures (scored, the newest of each student)."""
    return len(_prepare(exam, scans, {})["rows"])


def class_compared_alone(exam: dict, scans: list[dict], courses: dict[str, str] | None) -> bool:
    """True when the students of the set's class can be compared with the class only: the class has at
    least CLASS_VIEW_MIN counted sheets and the rest (everybody else) 0 or at least CLASS_VIEW_MIN.
    Otherwise the class figures and the figures of all students, put side by side, would give away a
    few students' scores (all minus the class = the rest), so everybody is compared with everybody."""
    cls = " ".join(str((exam.get("meta") or {}).get("course") or "").split())
    if not cls or courses is None:
        return False
    n_class = n_counted(exam, filter_by_course(scans, courses, cls))
    n_rest = n_counted(exam, scans) - n_class
    return n_class >= CLASS_VIEW_MIN and (n_rest == 0 or n_rest >= CLASS_VIEW_MIN)


def class_scans(exam: dict, scans: list[dict], student_no: str | None, courses: dict[str, str] | None):
    """The sheets one student is compared with: for an exam set with a class and a student of that class,
    the sheets of the class only (when class_compared_alone allows it); otherwise every sheet. Returns
    (scans, scope) with scope None (no class, or a class student compared with everybody because the
    class or the rest is too small), "in" (compared within the class) or "out" (not in the class or
    unreadable number: compared with all)."""
    cls = " ".join(str((exam.get("meta") or {}).get("course") or "").split())
    if not cls or courses is None:
        return scans, None
    if readable_no(student_no) and course_of(student_no, courses) == course_key(cls):
        if class_compared_alone(exam, scans, courses):
            return filter_by_course(scans, courses, cls), "in"
        return scans, None
    return scans, "out"


def student_history(sets: list[dict], student_no: str, scans_of, get_set, current_set_id: str | None = None,
                    courses: dict[str, str] | None = None) -> list[dict]:
    """This student's counted result in every exam set (oldest first), with the class mean and the
    place in that set. scans_of(set_id) / get_set(set_id) are db.list_scans / db.get_set.
    With current_set_id: the earlier MCQs of the same module (see set_group) that the student did not sit
    are listed too ("absent": True), and each result gets the change since the previous one of that module.
    With courses (students' courses): an MCQ with a class compares a student of that class within the class only."""
    if not student_no or "?" in student_no:
        return []
    by_id = {s["id"]: s for s in sets}
    group = set_group(by_id[current_set_id]) if current_set_id in by_id else None
    out = []
    for s in sorted(sets, key=lambda x: x["created"]):
        if not s.get("n_scans"):
            continue
        exam = get_set(s["id"])
        if not exam or not exam.get("versions"):
            continue
        same = group is not None and set_group(s) == group
        scans, _ = class_scans(exam, scans_of(s["id"]), student_no, courses)
        prep = _prepare(exam, scans, {})
        mine = [r for r in prep["rows"] if r["student_no"] == student_no]
        if not mine:
            sat = any(x.get("student_no") == student_no and not x.get("superseded") for x in scans)
            # only MCQs before this one: a report printed later must not say "did not sit" the next MCQ
            before = current_set_id in by_id and s["created"] < by_id[current_set_id]["created"]
            if same and not sat and before:
                out.append({"set_id": s["id"], "title": s["title"], "created": s["created"][:10], "absent": True,
                            "same_module": True})
            continue
        r = mine[0]
        pcts = [x["percent"] for x in prep["rows"]]
        big = len(pcts) >= MIN_CLASS_FOR_DIFFICULTY
        out.append({"set_id": s["id"], "title": s["title"], "created": s["created"][:10], "scan_id": r["scan_id"],
                    "score": r["score"], "max_score": r["max_score"], "percent": r["percent"],
                    "class_mean": round(mean(pcts), 1) if big else None, "rank": 1 + sum(1 for p in pcts if p > r["percent"]),
                    "n": len(pcts), "absent": False, "same_module": same,
                    # percentile place in the class (only from 5 sheets, like the class mean)
                    "position": round(_position(pcts, r["percent"]), 1) if big else None})
    # change since the previous MCQ of the same module that the student sat
    prev = None
    for h in out:
        h["change"] = h["pos_change"] = h["prev_title"] = None
        if h["absent"] or not h["same_module"]:
            continue
        if prev is not None:
            h["change"] = round(h["percent"] - prev["percent"], 1)
            h["prev_title"] = prev["title"]
            if h["position"] is not None and prev["position"] is not None:
                h["pos_change"] = round(h["position"] - prev["position"], 1)
        prev = h
    return out


# ---------------------------------------------------------------------------
# progress page: the same students over several exam sets
# ---------------------------------------------------------------------------
STEADY_POINTS = 5.0     # a change of up to this many percentage points counts as "about the same"
BIG_DROP_POINTS = 10.0  # a drop of at least this many points between the last two MCQs is flagged


def readable_no(no: str | None) -> bool:
    return bool(no) and "?" not in no


def set_group(s: dict) -> str:
    """A guess of which exam sets belong together (same module), for the ticks on the progress page:
    the module code (first word of the module) or else the title after its first ' - '."""
    module = (s.get("module") or "").strip()
    if module:
        first = module.split()[0].strip(" -")
        if any(ch.isdigit() for ch in first):
            return first.casefold()
        return course_key(module)
    title = s.get("title") or ""
    return course_key(title.split(" - ", 1)[1] if " - " in title else title)


def _position(pcts: list[float], p: float) -> float:
    """Percentile position in the class, 0-100: share of the class below, plus half of those equal."""
    if not pcts:
        return 0.0
    below = sum(1 for x in pcts if x < p)
    equal = sum(1 for x in pcts if x == p)
    return 100.0 * (below + 0.5 * equal) / len(pcts)


def progress(exams: list[dict], scans_by_set: dict[str, list[dict]], students: dict[str, str],
             courses: dict[str, str], course: str, settings: dict) -> dict:
    """Who sat which of these MCQs (oldest first) and how their results moved.
    'Sat' = a counted sheet with a readable student number, even one still waiting for a review.
    The group: everybody who sat at least one of these MCQs; with a course chosen, also the students of
    that course on the student list who sat none. Percent changes only over students who sat both."""
    pass_mark = float(settings.get("pass_mark", 40))
    want = None if not course else ("" if course == NO_COURSE else course_key(course))
    in_course = (lambda no: True) if want is None else (lambda no: course_of(no, courses) == want)

    per_set = []
    for exam in exams:
        scans = scans_by_set.get(exam["id"], [])
        counted = [s for s in scans if not s.get("superseded")]
        prep = _prepare(exam, scans, {})
        all_pcts = [r["percent"] for r in prep["rows"]]
        result = {}                               # student_no -> info
        for s in counted:
            if readable_no(s.get("student_no")):
                result[s["student_no"]] = {"scan_id": s["id"], "percent": None, "status": s.get("status")}
        for r in prep["rows"]:
            if not readable_no(r["student_no"]):      # scored, but the number is unreadable: not attributable
                continue
            result[r["student_no"]] = {"scan_id": r["scan_id"], "percent": r["percent"], "status": r["status"],
                                       "position": round(_position(all_pcts, r["percent"]), 1)}
        per_set.append({"exam": exam, "result": result, "all_pcts": all_pcts,
                        "unreadable": sum(1 for s in counted if not readable_no(s.get("student_no")))})

    sat_any = {no for ps in per_set for no in ps["result"] if in_course(no)}
    group = set(sat_any)
    if want:                                      # a real course: its students on the list count too
        group |= {no for no in students if course_of(no, courses) == want}
    listed_sat_none = sorted(no for no in students if no not in sat_any and in_course(no)) if not want else []

    names = course_names(courses)
    def who(no):
        return {"student_no": no, "name": students.get(no, ""), "course": names.get(course_of(no, courses), ""),
                "listed": no in students}

    # one row per student
    rows = []
    last = len(per_set) - 1
    for no in group:
        cells, sat = [], []
        for i, ps in enumerate(per_set):
            r = ps["result"].get(no)
            cells.append(r)
            if r:
                sat.append(i)
        scored = [(i, cells[i]["percent"]) for i in sat if cells[i]["percent"] is not None]
        row = dict(who(no), cells=cells, n_sat=len(sat), missed=[i for i in range(len(per_set)) if i not in sat])
        row["change"] = round(scored[-1][1] - scored[0][1], 1) if len(scored) >= 2 else None
        row["last_change"] = None
        if len(scored) >= 2:
            row["last_change"] = round(scored[-1][1] - scored[-2][1], 1)
        flags = []
        if per_set and last in row["missed"] and row["n_sat"]:
            flags.append(tr("missed the latest"))
        if not row["n_sat"]:
            flags.append(tr("sat none"))
        if per_set and cells[last] and cells[last]["percent"] is not None and cells[last]["percent"] < pass_mark:
            flags.append(tr("below the pass mark in the latest"))
        if row["last_change"] is not None and row["last_change"] <= -BIG_DROP_POINTS:
            flags.append(tr("dropped %(n)s points", n=f"{abs(row['last_change']):g}"))
        row["flags"] = flags
        rows.append(row)
    rows.sort(key=lambda r: ((r["name"] or "~").casefold(), r["student_no"]))

    sets_out = []
    for i, ps in enumerate(per_set):
        mine = [no for no in ps["result"] if no in group]
        pcts = [ps["result"][no]["percent"] for no in mine if ps["result"][no]["percent"] is not None]
        sets_out.append({"id": ps["exam"]["id"], "title": ps["exam"].get("meta", {}).get("title", ""),
                         "created": (ps["exam"].get("created") or "")[:10],
                         "n_sat": len(mine), "n_missed": len(group) - len(mine), "n_scored": len(pcts),
                         "mean": round(mean(pcts), 1) if pcts else None,
                         "pass_rate": round(100.0 * sum(1 for p in pcts if p >= pass_mark) / len(pcts), 1) if pcts else None,
                         "unreadable": ps["unreadable"]})

    # consecutive pairs: who came to one and not the other, and how the ones who sat both moved
    pairs = []
    for i in range(1, len(per_set)):
        a, b = per_set[i - 1]["result"], per_set[i]["result"]
        both = sorted(no for no in group if no in a and no in b)
        changes = []
        for no in both:
            pa, pb = a[no]["percent"], b[no]["percent"]
            if pa is not None and pb is not None:
                changes.append({**who(no), "a": pa, "b": pb, "change": round(pb - pa, 1),
                                "pos_change": round(b[no]["position"] - a[no]["position"], 1),
                                "scan_a": a[no]["scan_id"], "scan_b": b[no]["scan_id"]})
        ch = [c["change"] for c in changes]
        pair = {
            "a": sets_out[i - 1]["title"], "b": sets_out[i]["title"],
            "both": len(both),
            "only_a": [dict(who(no), percent=a[no]["percent"], scan_id=a[no]["scan_id"])
                       for no in sorted(group) if no in a and no not in b],
            "only_b": [dict(who(no), percent=b[no]["percent"], scan_id=b[no]["scan_id"])
                       for no in sorted(group) if no in b and no not in a],
            "neither": [who(no) for no in sorted(group) if no not in a and no not in b],
            "n_compared": len(changes),
            "improved": sum(1 for x in ch if x > STEADY_POINTS),
            "steady": sum(1 for x in ch if -STEADY_POINTS <= x <= STEADY_POINTS),
            "declined": sum(1 for x in ch if x < -STEADY_POINTS),
            "mean_change": round(mean(ch), 1) if ch else None,
            "median_change": round(median(ch), 1) if ch else None,
            "mean_a": round(mean(c["a"] for c in changes), 1) if changes else None,
            "mean_b": round(mean(c["b"] for c in changes), 1) if changes else None,
            "up": sorted([c for c in changes if c["change"] > STEADY_POINTS], key=lambda c: (-c["change"], c["student_no"]))[:8],
            "down": sorted([c for c in changes if c["change"] < -STEADY_POINTS], key=lambda c: (c["change"], c["student_no"]))[:8],
        }
        for k in ("only_a", "only_b", "neither"):
            pair[k].sort(key=lambda r: ((r["name"] or "~").casefold(), r["student_no"]))
        pairs.append(pair)

    n = len(group)
    return {
        "sets": sets_out, "rows": rows, "pairs": pairs, "n_group": n, "course": course,
        "course_label": course_label(course, courses), "pass_mark": pass_mark,
        "sat_all": sum(1 for r in rows if r["n_sat"] == len(per_set)) if per_set else 0,
        "missed_some": sum(1 for r in rows if 0 < r["n_sat"] < len(per_set)),
        "sat_none": sum(1 for r in rows if r["n_sat"] == 0),
        "missed_latest": sum(1 for r in rows if per_set and r["n_sat"] and last in r["missed"]),
        "listed_sat_none": [who(no) for no in listed_sat_none],
        "not_listed": sorted(no for no in sat_any if no not in students),
        "flagged": [r for r in rows if r["flags"] and r["n_sat"]],
        "steady_points": STEADY_POINTS,
    }


# ---------------------------------------------------------------------------
# Statistics for students: what the class can be shown about an exam without anyone's marks
CLASS_VIEW_MIN = 5            # fewer scored sheets: nothing is shown (individual marks would show through)
CLASS_VIEW_HISTOGRAM_MIN = 10  # fewer: no score histogram (with few students each bar is a person)


def class_view(exam: dict, full: dict) -> dict:
    """Built from compute()'s result, keeping only class-level figures: no names, student numbers,
    individual scores, ranking, top/bottom lists, minimum or maximum."""
    n = full.get("n_scored", 0)
    out = {"title": exam.get("meta", {}).get("title", ""), "n_scored": n, "n_questions": full.get("n_questions", 0),
           "enough": n >= CLASS_VIEW_MIN, "min_needed": CLASS_VIEW_MIN}
    if not out["enough"]:
        return out
    questions = exam.get("questions", [])
    per_q = []
    for pq in full.get("per_question", []):
        q = questions[pq["q"] - 1] if 0 < pq["q"] <= len(questions) else {"text": pq.get("text", ""), "choices": []}
        total = sum(c["count"] for c in pq["choices"]) + pq.get("blank", 0) + pq.get("multiple", 0)
        choices = []
        for i, c in enumerate(pq["choices"]):
            text = q["choices"][i]["text"] if i < len(q.get("choices", [])) else c.get("text", "")
            choices.append({"letter": c["letter"], "text_parts": text_parts(text), "count": c["count"],
                            "percent": round(100.0 * c["count"] / total, 1) if total else 0.0, "is_key": c["is_key"]})
        per_q.append({"q": pq["q"], "text_parts": text_parts(q["text"]), "pct_correct": pq["pct_correct"],
                      "choices": choices, "blank": pq.get("blank", 0), "multiple": pq.get("multiple", 0)})
    if full.get("topics"):              # class figures per topic, like the questions themselves
        out["topics"] = [{"topic": x["topic"], "n": x["n"], "questions": list(x["questions"]),
                          "pct_correct": x["pct_correct"]} for x in full["topics"]]
    out.update({
        "mean": full.get("mean"), "median": full.get("median"), "pass_rate": full.get("pass_rate"),
        "pass_mark": full.get("pass_mark"),
        "bands": [{"name": b["name"], "from": b["from"], "to": b["to"], "count": b["count"], "percent": b["percent"]}
                  for b in full.get("bands", [])] if n >= CLASS_VIEW_HISTOGRAM_MIN else [],
        "histogram": [{"label": h["label"], "count": h["count"]} for h in full.get("histogram", [])]
        if n >= CLASS_VIEW_HISTOGRAM_MIN else [],
        "per_question": per_q,
        "hardest": class_hardest(per_q),
        "easiest": sorted(per_q, key=lambda r: -r["pct_correct"])[:5],
        "hardest_below": CLASS_HARD_BELOW,
    })
    return out


CLASS_HARD_BELOW = 60.0       # Statistics for students: "among the hardest" only under this % correct


def class_hardest(per_q: list[dict]) -> list[dict]:
    """The questions the class found hardest: under CLASS_HARD_BELOW % correct, the lowest first (ties by
    question number), at most min(5, a third of the questions rounded up)."""
    cap = min(5, math.ceil(len(per_q) / 3))
    low = sorted((r for r in per_q if r["pct_correct"] < CLASS_HARD_BELOW), key=lambda r: (r["pct_correct"], r["q"]))
    return low[:cap]
