var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
/* Question editor: renders window.EXAM.questions, lets you edit them and saves them as JSON.
   Changes save themselves shortly after you stop typing (autosave); the Save button only shows when a
   save did not go through, to try again. */
(function () {
  const LETTERS = "ABCDEF";
  const AUTOSAVE_DELAY = 1200;      // ms after the last change
  const exam = window.EXAM;
  const locked = window.LOCKED;
  const root = document.getElementById("questions");
  let questions = (exam.questions || []).map(q => ({
    text: q.text || "",
    choices: (q.choices || []).map(c => ({ text: c.text || "", fixed: !!c.fixed })),
    correct: typeof q.correct === "number" ? q.correct : -1,
    topic: q.topic || "",
  }));
  if (!questions.length && !locked) questions.push(blankQuestion());

  // topics are optional: the Topic fields show once "Use topics" is ticked. It is on by itself when a
  // question has a topic, and the choice is remembered for this set in this browser. Unticking only
  // hides the fields: the topics already typed are kept.
  const topicBox = document.getElementById("usetopics");
  const topicKey = "mcq_topics_" + exam.id;
  let useTopics = !topicBox || questions.some(q => String(q.topic || "").trim() !== "");
  if (!useTopics) { try { useTopics = window.localStorage.getItem(topicKey) === "1"; } catch (e) { /* no storage */ } }
  if (topicBox) {
    topicBox.checked = useTopics;
    topicBox.addEventListener("change", () => {
      useTopics = topicBox.checked;
      try { window.localStorage.setItem(topicKey, useTopics ? "1" : "0"); } catch (e) { /* no storage */ }
      render();
    });
  }

  // topic suggestions: the topics of this set and of the other sets (a new one is simply typed)
  const topicList = document.createElement("datalist");
  topicList.id = "topiclist";
  document.body.append(topicList);
  function refreshTopics() {
    const seen = new Map();
    (window.TOPICS || []).concat(questions.map(q => q.topic)).forEach(x => {
      const v = String(x || "").trim().replace(/\s+/g, " ");
      if (v && !seen.has(v.toLowerCase())) seen.set(v.toLowerCase(), v);
    });
    topicList.innerHTML = "";
    Array.from(seen.values()).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }))
      .forEach(v => { const o = document.createElement("option"); o.value = v; topicList.append(o); });
  }

  // unsaved changes in the editor: saved after a short pause; warn before leaving while one is pending
  let dirty = false;
  let timer = null, busy = false, again = false;
  const markDirty = () => { dirty = true; schedule(); };
  window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  function blankQuestion() {
    return { text: "", choices: [{text:"",fixed:false},{text:"",fixed:false},{text:"",fixed:false},{text:"",fixed:false}], correct: -1, topic: "" };
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
      const radios = [];
      q.choices.forEach((c, j) => {
        const radio = el("input", { type: "radio", name: `correct${i}`, title: t("correct answer") });
        radio.checked = q.correct === j;
        radios.push(radio);
        radio.addEventListener("change", () => {
          // versions exist: a new correct answer marks every scanned sheet again, so ask first
          if (locked && q.correct !== j && typeof window.confirm === "function"
              && !window.confirm(t("Change the correct answer of question {n}? Every sheet already scanned for this set is marked again with the new answer.", { n: i + 1 }))) {
            radios.forEach((r, k) => { r.checked = q.correct === k; });
            return;
          }
          q.correct = j; markDirty();
        });
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
      // optional topic (not printed; it can be changed also once the versions are generated)
      const topic = el("input", { type: "text", list: "topiclist", value: q.topic, maxlength: 80, style: "max-width:320px",
        placeholder: t("optional, e.g. subnetting"), title: t("the topic of this question, for the statistics by topic") });
      topic.addEventListener("input", () => { q.topic = topic.value; markDirty(); });
      if (useTopics) box.append(el("div", { class: "row", style: "gap:8px;margin-top:6px;align-items:center" },
        el("span", { class: "small muted" }, t("Topic")), topic));
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

  const saveBtn = document.getElementById("saveq");
  const status = document.getElementById("savestatus");
  saveBtn.hidden = true;            // autosave; shown again when a save does not go through
  const genForm = document.querySelector('form[action$="/generate"]');
  const genBtn = genForm && genForm.querySelector('button[type="submit"]');

  // what the page showed from the saved questions (it used to reload after saving): the Generate
  // button, the capacity of an answer sheet, and the scores in the results once they are recomputed
  function refreshPage(j) {
    if (genBtn) {
      genBtn.disabled = !!(j.problems && j.problems.length) || !j.n;
      if (genBtn.disabled) genBtn.setAttribute("title", t("fix the questions first"));
      else genBtn.removeAttribute("title");
    }
    const hint = document.getElementById("problems").previousElementSibling;
    const bold = hint && hint.querySelectorAll ? hint.querySelectorAll("b") : [];
    const cap = bold.length ? bold[bold.length - 1] : null;      // the capacity is the last bold number
    if (cap && typeof j.capacity === "number") cap.textContent = String(j.capacity);
    if (j.scores) document.querySelectorAll("#resultstable tbody tr").forEach(tr => {
      const s = j.scores[tr.dataset.id];
      if (!s || s[0] === null || s[0] === undefined) return;
      tr.cells[4].innerHTML = "";
      tr.cells[4].append(el("b", {}, String(s[0])), ` / ${s[1]}`);
      tr.dataset.pct = s[1] ? String(s[0] / s[1]) : "-1";
      tr.cells[4].setAttribute("data-sort-value", s[1] ? String(s[0] / s[1]) : "");   // what static/sortable.js sorts by
    });
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; save(); }, AUTOSAVE_DELAY);
  }

  // one save at a time: a change made during a save is sent right after it. Resolves to the answer
  // of the server (or null when it could not be reached).
  let waiting = [];
  async function save() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (busy) { again = true; return new Promise(res => waiting.push(res)); }
    busy = true;
    status.textContent = t("Saving…");
    const sent = JSON.stringify({ questions });
    let j = null;
    try {
      const r = await fetch(`/sets/${exam.id}/questions`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: sent });
      if (!r.ok) throw new Error(String(r.status));
      j = await r.json();
      const probs = document.getElementById("problems");
      probs.innerHTML = "";
      (j.problems || []).forEach(p => probs.append(el("div", {}, p)));
      if (j.ok) {
        if (sent === JSON.stringify({ questions })) dirty = false;   // nothing changed meanwhile
        saveBtn.hidden = true;
        status.textContent = (j.problems && j.problems.length
          ? t("Saved {n} question(s) – fix the problems above before generating.", { n: j.n })
          : t("Saved {n} question(s).", { n: j.n })) + (j.rescored ? " " + t("Scores recomputed.") : "");
        try {                         // the save went through: a display problem must not say otherwise
          refreshPage(j);
          refreshTopics();
        } catch (e) { /* the page shows the new values after a reload */ }
      } else {
        saveBtn.hidden = false;
        status.textContent = t("Not saved.");
      }
    } catch (e) {
      j = null;
      saveBtn.hidden = false;
      status.textContent = t("Could not save. Check the connection and try again.");
    }
    busy = false;
    const done = waiting; waiting = [];
    if (again) { again = false; save().then(x => done.forEach(res => res(x))); }
    else done.forEach(res => res(j));
    return j;
  }

  saveBtn.addEventListener("click", () => { save(); });

  // generating uses the saved questions: save a pending change first
  if (genForm) genForm.addEventListener("submit", async e => {
    if (!dirty && !busy) return;
    e.preventDefault();
    const j = await save();
    if (j && j.ok && !(j.problems && j.problems.length) && !dirty) genForm.submit();
  });

  // importing appends to the questions as shown here (unsaved removals/edits included), not to the
  // last saved list - otherwise removed questions came back after an import
  const importForm = document.getElementById("importform");
  if (importForm) importForm.addEventListener("submit", () => {
    if (timer) { clearTimeout(timer); timer = null; }
    importForm.elements.current.value = JSON.stringify(questions);
    dirty = false;
  });

  refreshTopics();
  render();
  window.MCQEditor = { save: save, pending: () => dirty || busy || !!timer };
})();
