"""Creating the shuffled versions of an exam.

Exam questions are stored as:
    {"text": "...", "choices": [{"text": "...", "fixed": false}, ...], "correct": 2}
`correct` is the index of the right choice in the ORIGINAL order.
A choice with "fixed": true (e.g. "All of the above") keeps its place at the
end instead of being shuffled.

A version is:
    {"version": 3,
     "questions": [{"q": 17, "choices": [2, 0, 3, 1], "answer": "C"}, ...]}
`q` is the index of the original question shown at this sheet position,
`choices` lists original choice indices in the order they are printed, and
`answer` is the letter a student must fill for this position.

Every version is derived from a seed built from the set id and the version
number, so regenerating the same set always gives the same versions.
"""
from __future__ import annotations

import random
import secrets
import string

from .layout import LETTERS, MAX_OPTIONS, MIN_OPTIONS

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i to stay readable


def new_set_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(6))


def validate_questions(questions: list[dict]) -> list[str]:
    """Returns a list of human-readable problems (empty when all is fine)."""
    problems = []
    if not questions:
        problems.append("The exam has no questions.")
    for i, q in enumerate(questions, start=1):
        if not str(q.get("text", "")).strip():
            problems.append(f"Question {i} has no text.")
        choices = q.get("choices") or []
        texts = [str(c.get("text", "")).strip() for c in choices]
        if len(choices) < MIN_OPTIONS or len(choices) > MAX_OPTIONS:
            problems.append(f"Question {i} must have {MIN_OPTIONS}-{MAX_OPTIONS} choices (has {len(choices)}).")
        if any(not t for t in texts):
            problems.append(f"Question {i} has an empty choice.")
        c = q.get("correct")
        if not isinstance(c, int) or c < 0 or c >= len(choices):
            problems.append(f"Question {i} has no correct choice marked.")
    return problems


def make_versions(questions: list[dict], n_versions: int, set_id: str,
                  shuffle_questions: bool = True, shuffle_choices: bool = True) -> list[dict]:
    versions = []
    for v in range(1, n_versions + 1):
        rng = random.Random(f"{set_id}:{v}")
        order = list(range(len(questions)))
        if shuffle_questions:
            rng.shuffle(order)
        qs = []
        for qi in order:
            q = questions[qi]
            movable = [i for i, c in enumerate(q["choices"]) if not c.get("fixed")]
            fixed = [i for i, c in enumerate(q["choices"]) if c.get("fixed")]
            if shuffle_choices:
                rng.shuffle(movable)
            printed = movable + fixed
            qs.append({"q": qi, "choices": printed, "answer": LETTERS[printed.index(q["correct"])]})
        versions.append({"version": v, "questions": qs})
    return versions


def answer_key(version: dict) -> list[str]:
    return [q["answer"] for q in version["questions"]]


def refresh_answers(questions: list[dict], versions: list[dict]) -> int:
    """The correct choice of a question was changed after the versions were generated: update the
    letter a student must fill at every position that shows this question. The printed order of
    the choices stays as it is, so the sheets already handed out keep matching. Returns how many
    positions changed."""
    changed = 0
    for v in versions:
        for item in v["questions"]:
            correct = questions[item["q"]].get("correct")
            if correct not in item["choices"]:        # no (valid) correct choice yet: leave the old letter
                continue
            letter = LETTERS[item["choices"].index(correct)]
            if item["answer"] != letter:
                item["answer"] = letter
                changed += 1
    return changed


def options_per_position(questions: list[dict], version: dict) -> list[int]:
    return [len(questions[q["q"]]["choices"]) for q in version["questions"]]


def score_answers(answers: list[str], key: list[str]) -> int:
    """answers: one letter per position, '' for blank, '*' for several marks."""
    return sum(1 for a, k in zip(answers, key) if a == k)
