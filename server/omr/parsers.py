"""Bulk import of questions from plain text.

Format A ("star" format):

    1. What does GPIO stand for?
    *A) General Purpose Input Output
    B) Graphical Port Interface Object
    C) General Port I/O

    What is 2 + 2?
    A) 3
    *B) 4
    C) All of the above [fixed]

Format B (Aiken, used by Moodle):

    What does GPIO stand for?
    A. General Purpose Input Output
    B. Graphical Port Interface Object
    ANSWER: A

Rules: questions are separated by blank lines; the question text is every
line before the first choice line and may span several lines (a ``` fenced
code block inside the text is kept as code); a choice line starts with an
optional * (correct), a letter A-F, then ) or . and the text; "[fixed]" or
"[keep last]" at the end of a choice keeps it in place when choices are
shuffled.  A leading "1." / "Q1:" / "1)" on the question is removed.
An optional line "TOPIC: <name>" anywhere in the block labels the question with
a topic; it is written after the choices on export. Only exactly "TOPIC:", in
capital letters at the very start of a line and outside a ``` code block, counts:
"Topic: sorting. Which..." is question text. To start a line of question (or
choice) text with "TOPIC:", write a backslash before it: "\\TOPIC: ..." imports as
the text "TOPIC: ..." (one leading backslash is removed from any line that starts
with backslashes and then TOPIC:). The export writes such lines that way.
"""
from __future__ import annotations

import re

CHOICE_RE = re.compile(r"^\s*(\*?)\s*([A-Fa-f])\s*[\)\.:]\s*(.*)$")
ANSWER_RE = re.compile(r"^\s*ANSWER\s*:\s*([A-Fa-f])\s*$", re.IGNORECASE)
QNUM_RE = re.compile(r"^\s*(?:Q\s*)?\d{1,3}\s*[\.\):]\s*", re.IGNORECASE)
FIXED_RE = re.compile(r"\s*\[(fixed|keep last|keep)\]\s*$", re.IGNORECASE)
TOPIC_RE = re.compile(r"^TOPIC:(.*)$")                 # exactly this, at the very start of the line
ESCAPED_TOPIC_RE = re.compile(r"^\\+TOPIC:")             # \TOPIC: = a text line starting with TOPIC:


def _split_blocks(text: str) -> list[list[str]]:
    blocks, cur, in_code = [], [], False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def parse_bulk(text: str) -> tuple[list[dict], list[str]]:
    questions, errors = [], []
    for n, block in enumerate(_split_blocks(text), start=1):
        q_lines, choices, correct, in_code, topic = [], [], None, False, ""
        for line in block:
            if line.strip().startswith("```"):
                in_code = not in_code
            m_topic = TOPIC_RE.match(line) if not in_code else None
            if m_topic:
                topic = " ".join(m_topic.group(1).split())
                continue
            if not in_code and ESCAPED_TOPIC_RE.match(line):
                line = line[1:]                  # \TOPIC: ... is text that starts with TOPIC:
            m_ans = ANSWER_RE.match(line)
            if m_ans and not in_code:
                letter = m_ans.group(1).upper()
                correct = ord(letter) - 65
                continue
            m = CHOICE_RE.match(line) if not in_code else None
            if m and (choices or q_lines):
                star, letter, ctext = m.group(1), m.group(2).upper(), m.group(3)
                fixed = bool(FIXED_RE.search(ctext))
                ctext = FIXED_RE.sub("", ctext).strip()
                choices.append({"text": ctext, "fixed": fixed})
                if star:
                    correct = len(choices) - 1
            elif choices:
                # continuation line of the previous choice
                choices[-1]["text"] += "\n" + line.strip()
            else:
                q_lines.append(line)
        if not q_lines and not choices:
            continue
        qtext = "\n".join(q_lines).strip()
        qtext = QNUM_RE.sub("", qtext, count=1) if not qtext.startswith("```") else qtext
        if not qtext:
            errors.append(f"Block {n}: no question text.")
            continue
        if len(choices) < 2:
            errors.append(f"Block {n} ('{qtext[:40]}'): fewer than 2 choices.")
            continue
        if correct is None or correct >= len(choices):
            errors.append(f"Block {n} ('{qtext[:40]}'): no correct choice (mark it with * or ANSWER: X).")
            continue
        q = {"text": qtext, "choices": choices, "correct": correct}
        if topic:
            q["topic"] = topic
        questions.append(q)
    return questions, errors


def questions_to_text(questions: list[dict]) -> str:
    """The reverse: export in the star format (for editing in a text editor)."""
    out = []
    for i, q in enumerate(questions, start=1):
        lines = [f"{i}. {q['text']}"]
        for j, c in enumerate(q["choices"]):
            star = "*" if j == q["correct"] else ""
            fixed = " [fixed]" if c.get("fixed") else ""
            lines.append(f"{star}{chr(65 + j)}) {c['text']}{fixed}")
        lines = _escape_topic_lines("\n".join(lines).split("\n"))
        if str(q.get("topic") or "").strip():
            lines.append(f"TOPIC: {' '.join(str(q['topic']).split())}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _escape_topic_lines(lines: list[str]) -> list[str]:
    """Text lines that parse_bulk would take for a TOPIC line (or for an escaped one) get one more
    backslash, outside code blocks, exactly as parse_bulk tells code blocks apart."""
    out, in_code = [], False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and (TOPIC_RE.match(line) or ESCAPED_TOPIC_RE.match(line)):
            line = "\\" + line
        out.append(line)
    return out
