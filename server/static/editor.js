var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
/* Question editor: renders window.EXAM.questions, lets you edit them and saves them as JSON. */
(function () {
  const LETTERS = "ABCDEF";
  const exam = window.EXAM;
  const locked = window.LOCKED;
  const root = document.getElementById("questions");
  let questions = (exam.questions || []).map(q => ({
    text: q.text || "",
    choices: (q.choices || []).map(c => ({ text: c.text || "", fixed: !!c.fixed })),
    correct: typeof q.correct === "number" ? q.correct : -1,
  }));
  if (!questions.length && !locked) questions.push(blankQuestion());

  // unsaved changes in the editor: warn before leaving the page (another form, a link, a reload)
  let dirty = false;
  const markDirty = () => { dirty = true; };
  window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  function blankQuestion() {
    return { text: "", choices: [{text:"",fixed:false},{text:"",fixed:false},{text:"",fixed:false},{text:"",fixed:false}], correct: -1 };
  }

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c !== null && c !== undefined) e.append(c);
    return e;
  }

  function render() {
    root.innerHTML = "";
    questions.forEach((q, i) => {
      const head = el("div", { class: "head" },
        el("span", { class: "num" }, t("Question {n}", { n: i + 1 })),
        locked ? el("span", { class: "muted small" }, t("{n} choices", { n: q.choices.length })) :
          el("span", {},
            el("button", { class: "small secondary", type: "button", onclick: () => { move(i, -1); } }, t("Up")), " ",
            el("button", { class: "small secondary", type: "button", onclick: () => { move(i, 1); } }, t("Down")), " ",
            el("button", { class: "small secondary", type: "button", onclick: () => { addChoice(i); } }, t("+ choice")), " ",
            el("button", { class: "small danger", type: "button", onclick: () => { questions.splice(i, 1); markDirty(); render(); } }, t("remove"))));
      const ta = el("textarea", { placeholder: t("Question text (``` for a code block)"), rows: 2 }, q.text);
      ta.addEventListener("input", () => { q.text = ta.value; markDirty(); });
      const box = el("div", { class: "q" }, head, ta);
      q.choices.forEach((c, j) => {
        const radio = el("input", { type: "radio", name: `correct${i}`, title: t("correct answer") });
        radio.checked = q.correct === j;
        radio.addEventListener("change", () => { q.correct = j; markDirty(); });
        const txt = el("input", { type: "text", placeholder: t("Choice {letter}", { letter: LETTERS[j] }), value: c.text });
        txt.addEventListener("input", () => { c.text = txt.value; markDirty(); });
        const fixed = el("input", { type: "checkbox", title: t("keep this choice in its place when shuffling") });
        fixed.checked = c.fixed;
        fixed.disabled = locked;
        fixed.addEventListener("change", () => { c.fixed = fixed.checked; markDirty(); });
        const row = el("div", { class: "choice" },
          radio, el("span", { class: "letter" }, LETTERS[j]), txt,
          el("label", { class: "inline" }, fixed, " " + t("keep last")),
          locked ? el("span") : el("button", { class: "small secondary", type: "button", title: t("remove choice"),
            onclick: () => { q.choices.splice(j, 1); if (q.correct === j) q.correct = -1; else if (q.correct > j) q.correct--; markDirty(); render(); } }, t("Remove")));
        box.append(row);
      });
      root.append(box);
    });
    document.getElementById("qcount").textContent = `(${questions.length})`;
  }

  function move(i, d) {
    const j = i + d;
    if (j < 0 || j >= questions.length) return;
    [questions[i], questions[j]] = [questions[j], questions[i]];
    markDirty();
    render();
  }
  function addChoice(i) {
    if (questions[i].choices.length >= 6) { alert(t("Maximum 6 choices per question (A–F).")); return; }
    questions[i].choices.push({ text: "", fixed: false });
    markDirty();
    render();
  }

  const addBtn = document.getElementById("addq");
  if (addBtn) addBtn.addEventListener("click", () => { questions.push(blankQuestion()); markDirty(); render(); window.scrollTo(0, document.body.scrollHeight); });

  document.getElementById("saveq").addEventListener("click", async () => {
    const status = document.getElementById("savestatus");
    status.textContent = t("Saving…");
    const r = await fetch(`/sets/${exam.id}/questions`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions }) });
    const j = await r.json();
    const probs = document.getElementById("problems");
    probs.innerHTML = "";
    (j.problems || []).forEach(p => probs.append(el("div", {}, p)));
    if (j.ok) {
      dirty = false;
      status.textContent = (j.problems && j.problems.length
        ? t("Saved {n} question(s) – fix the problems above before generating.", { n: j.n })
        : t("Saved {n} question(s).", { n: j.n })) + (j.rescored ? " " + t("Scores recomputed.") : "");
      if (!j.problems.length) setTimeout(() => location.reload(), 600);
    } else {
      status.textContent = t("Not saved.");
    }
  });

  // importing appends to the questions as shown here (unsaved removals/edits included), not to the
  // last saved list - otherwise removed questions came back after an import
  const importForm = document.getElementById("importform");
  if (importForm) importForm.addEventListener("submit", () => {
    importForm.elements.current.value = JSON.stringify(questions);
    dirty = false;
  });

  render();
})();
