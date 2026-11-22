"""Text import/export of questions (omr/parsers.py), including the optional TOPIC: line (exactly "TOPIC:"
in capitals at the start of a line; a text line that starts with TOPIC: is written as \\TOPIC: on export).

    cd server && PYTHONPATH=. python tests/parsers_test.py      (prints ALL PASS)
"""
from omr.parsers import parse_bulk, questions_to_text

FAIL = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        FAIL.append(what)


OLD = """1. What does GPIO stand for?
*A) General Purpose Input Output
B) Graphical Port Interface Object
C) General Port I/O
D) None of the above [fixed]

2. Topic modelling finds what?
A) Themes
*B) Words

Topics: which one is a protocol?
A) TCP
*B) Banana

What is 2 + 2?
A. 3
B. 4
ANSWER: B"""

qs, errors = parse_bulk(OLD)
check(not errors and len(qs) == 4, "a file without TOPIC lines parses")
check(all(set(q) == {"text", "choices", "correct"} for q in qs), "no topic key when there is no TOPIC line")
check(qs[0] == {"text": "What does GPIO stand for?", "choices": [
    {"text": "General Purpose Input Output", "fixed": False}, {"text": "Graphical Port Interface Object", "fixed": False},
    {"text": "General Port I/O", "fixed": False}, {"text": "None of the above", "fixed": True}], "correct": 0},
    "star format as before")
check(qs[1]["text"] == "Topic modelling finds what?", "a question starting with the word Topic is question text")
check(qs[2]["text"] == "Topics: which one is a protocol?", "'Topics:' is not a TOPIC line")
check(qs[3]["correct"] == 1, "Aiken ANSWER line as before")
txt = questions_to_text(qs)
check("TOPIC" not in txt, "export without topics writes no TOPIC line")
check(parse_bulk(txt)[0] == qs, "round trip without topics")

TOPICS = """TOPIC: Subnetting
1. How many hosts in a /30?
A) 1
*B) 2

2. Which layer is IP?
TOPIC:   network   layers
A) 2
*B) 3

3. What is DNS?
A) a cable
*B) a name service
TOPIC: Services

4. Topic : not the exact form
*A) yes
B) no

5. Code question
```
TOPIC: inside a code block
```
*A) x
B) y"""
qs, errors = parse_bulk(TOPICS)
check(not errors and len(qs) == 5, "a file with TOPIC lines parses")
check([q.get("topic") for q in qs] == ["Subnetting", "network layers", "Services", None, None],
      "TOPIC before the question, between text and choices, after the choices")
check(qs[0]["text"] == "How many hosts in a /30?", "the question number is still removed after a TOPIC line")
check(len(qs[2]["choices"]) == 2 and qs[2]["choices"][1]["text"] == "a name service",
      "a TOPIC line after the choices is not part of the last choice")
check(qs[3]["text"] == "Topic : not the exact form", "'Topic :' with a space is question text")
check("TOPIC: inside a code block" in qs[4]["text"], "a TOPIC line inside a code block stays code")
txt = questions_to_text(qs)
check("TOPIC: Subnetting" in txt and "TOPIC: network layers" in txt and "TOPIC: Services" in txt,
      "export writes the topics")
check(parse_bulk(txt)[0] == qs, "round trip with topics")
check(questions_to_text(parse_bulk(txt)[0]) == txt, "export of the re-import is identical")

# only the exact form is a TOPIC line: question text that merely starts with the word stays text
TEXTY = """Topic: sorting. Which algorithm is stable?
*A) Merge sort
B) Quicksort

1. Which is a sorting algorithm?
topic: not a topic line, lower case
*A) Merge sort
B) Dijkstra

2. Indented
  TOPIC: indented, so question text
*A) x
B) y"""
qs, errors = parse_bulk(TEXTY)
check(not errors and len(qs) == 3, "texts starting with Topic parse")
check(qs[0]["text"] == "Topic: sorting. Which algorithm is stable?" and "topic" not in qs[0],
      "'Topic: sorting. Which algorithm...' stays question text")
check(qs[1]["text"] == "Which is a sorting algorithm?\ntopic: not a topic line, lower case" and "topic" not in qs[1],
      "a lower-case 'topic:' line is question text")
check(qs[2]["text"] == "Indented\n  TOPIC: indented, so question text" and "topic" not in qs[2],
      "TOPIC: not at the start of the line is question text")
txt = questions_to_text(qs)
check(parse_bulk(txt)[0] == qs, "round trip of those texts")

# a text line that itself starts with TOPIC: is escaped on export (\TOPIC:) and imported back as text
TRICKY = [
    {"text": "Read this:\nTOPIC: not a topic, part of the question", "choices": [
        {"text": "a", "fixed": False}, {"text": "b\nTOPIC: second line of a choice", "fixed": False}], "correct": 0,
     "topic": "Real topic"},
    {"text": "Escaped already:\n\\TOPIC: one backslash\n\\\\TOPIC: two", "choices": [
        {"text": "x", "fixed": False}, {"text": "y", "fixed": False}], "correct": 1},
    {"text": "In code:\n```\nTOPIC: stays as it is in code\n\\TOPIC: also\n```", "choices": [
        {"text": "x", "fixed": False}, {"text": "y", "fixed": False}], "correct": 0},
    {"text": "TOPIC: on the first line", "choices": [
        {"text": "x", "fixed": False}, {"text": "y", "fixed": False}], "correct": 0},
]
txt = questions_to_text(TRICKY)
check("\n\\TOPIC: not a topic" in txt, "export escapes a question text line starting with TOPIC:")
check("\n\\TOPIC: second line of a choice" in txt, "and a choice's second line starting with TOPIC:")
check("\n\\\\TOPIC: one backslash" in txt and "\n\\\\\\TOPIC: two" in txt, "a line already starting with \\TOPIC: gets one more backslash")
check("\nTOPIC: stays as it is in code\n\\TOPIC: also\n" in txt, "nothing is escaped inside a code block")
back, errors = parse_bulk(txt)
check(not errors and back == TRICKY, "round trip of text lines that start with TOPIC: (escaped and unescaped)")
check(questions_to_text(back) == txt, "export of the re-import is identical")
check(parse_bulk("\\TOPIC: typed by hand\n*A) x\nB) y")[0][0]["text"] == "TOPIC: typed by hand",
      "\\TOPIC: typed by hand imports as question text")

print()
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED")
