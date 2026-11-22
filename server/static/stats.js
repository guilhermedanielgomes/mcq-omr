var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
/* Statistics page: fetches /sets/<id>/stats.json and draws everything with plain SVG (works offline). */
(function () {
  const root = document.getElementById("stats");
  const SERIES = "#2a78d6";        // histogram bars
  const CURVE = "#52514e";         // normal-curve reference line (secondary ink)
  const GRID = "#e1e0d9", AXIS = "#c3c2b7", MUTED = "#898781", INK = "#0b0b0b";
  const CRITICAL = "#d03b3b", GOOD = "#0ca30c", WARN = "#c98500";

  function h(tag, attrs, ...children) {
    const ns = ["svg", "rect", "path", "line", "text", "g", "circle", "polyline"].includes(tag);
    const e = ns ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.setAttribute("class", v);
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) e.setAttribute(k, v);
    }
    for (const c of children) if (c !== null && c !== undefined) e.append(c);
    return e;
  }
  const fmt = (x, d = 1) => (x === null || x === undefined) ? "–" : Number(x).toFixed(d).replace(/\.0$/, "");
  const who = r => r.student_no + (r.name ? " · " + r.name : "");
  // question page: which students picked each answer (anchor = one answer, e.g. "choice-B", "blank")
  // course filter (drop-down in the toolbar, only there when students have a course): kept in the URL
  let course = window.COURSE || "";
  // an exam set with a class opens on it when no course is given, so "All students" is sent as course= (empty)
  const cq = () => course ? `?course=${encodeURIComponent(course)}` : (window.HAS_CLASS ? "?course=" : "");
  const qurl = (q, anchor) => `/sets/${encodeURIComponent(window.SET_ID)}/questions/${q}` + cq() + (anchor ? `#${anchor}` : "");
  const sel = document.getElementById("course");
  if (sel) sel.addEventListener("change", () => {
    course = sel.value;
    history.replaceState(null, "", location.pathname + cq());
    root.innerHTML = '<p class="muted">' + t("Loading…") + '</p>';
    load();
  });
  let tableOpen = false;   // the page is rebuilt every 10 s: keep the full table open if it was open

  function tile(label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "tile-label" }, label),
      h("div", { class: "tile-value" }, value), sub ? h("div", { class: "tile-sub" }, sub) : null);
  }

  function histogram(bins, n) {
    const W = 640, H = 260, L = 40, R = 16, T = 18, B = 40;
    const iw = W - L - R, ih = H - T - B;
    const maxY = Math.max(1, ...bins.map(b => Math.max(b.count, b.expected)));
    const yScale = v => T + ih - (v / maxY) * ih;
    const bw = iw / bins.length;
    const svg = h("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": t("Distribution of percentages") });
    // grid + y axis labels
    const steps = Math.min(5, maxY);
    for (let i = 0; i <= steps; i++) {
      const v = Math.round((maxY / steps) * i);
      const y = yScale(v);
      svg.append(h("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: GRID, "stroke-width": 1 }));
      svg.append(h("text", { x: L - 6, y: y + 4, "text-anchor": "end", "font-size": 11, fill: MUTED }, String(v)));
    }
    svg.append(h("line", { x1: L, x2: W - R, y1: T + ih, y2: T + ih, stroke: AXIS, "stroke-width": 1 }));
    // bars
    bins.forEach((b, i) => {
      const x = L + i * bw + 2, w = bw - 4;
      const y = yScale(b.count), hh = T + ih - y;
      const bar = h("rect", { x, y, width: w, height: Math.max(0, hh), rx: 4, fill: SERIES });
      bar.append(h("title", {}, b.count === 1
        ? t("{label}%: {count} student (normal curve: {expected})", { label: b.label, count: b.count, expected: fmt(b.expected) })
        : t("{label}%: {count} students (normal curve: {expected})", { label: b.label, count: b.count, expected: fmt(b.expected) })));
      svg.append(bar);
      // square off the bottom of the rounded bar
      if (hh > 4) svg.append(h("rect", { x, y: T + ih - 4, width: w, height: 4, fill: SERIES }));
      svg.append(h("text", { x: x + w / 2, y: T + ih + 16, "text-anchor": "middle", "font-size": 11, fill: MUTED }, b.label));
    });
    // normal curve (expected count per bin, drawn through the bin centres)
    if (n >= 2) {
      const pts = bins.map((b, i) => `${L + i * bw + bw / 2},${yScale(b.expected)}`).join(" ");
      svg.append(h("polyline", { points: pts, fill: "none", stroke: CURVE, "stroke-width": 2, "stroke-linejoin": "round" }));
      bins.forEach((b, i) => svg.append(h("circle", { cx: L + i * bw + bw / 2, cy: yScale(b.expected), r: 3.5, fill: CURVE, stroke: "#fff", "stroke-width": 2 })));
    }
    svg.append(h("text", { x: W / 2, y: H - 4, "text-anchor": "middle", "font-size": 11, fill: MUTED }, t("score (%)")));
    const legend = h("div", { class: "legend" },
      h("span", {}, h("i", { style: `background:${SERIES}` }), " " + t("students per 10 % band")),
      h("span", {}, h("i", { style: `background:${CURVE};height:2px;width:18px;border-radius:0` }), " " + t("normal curve with the same mean and SD")));
    return h("div", {}, svg, legend);
  }

  function bars(items, valueKey, labelFn, titleFn, max = 100, hrefFn = null) {
    const wrap = h("div", { class: "hbars" });
    items.forEach(it => {
      const v = it[valueKey];
      const row = h(hrefFn ? "a" : "div", { class: "hbar", title: titleFn(it), href: hrefFn ? hrefFn(it) : null },
        h("span", { class: "hbar-label" }, labelFn(it)),
        h("span", { class: "hbar-track" }, h("span", { class: "hbar-fill", style: `width:${Math.max(0, Math.min(100, 100 * v / max))}%` })),
        h("span", { class: "hbar-value" }, fmt(v) + (max === 100 ? "%" : "")));
      wrap.append(row);
    });
    return wrap;
  }

  function flagChip(f) {
    const serious = /check the key|hard/.test(f);
    const col = serious ? CRITICAL : WARN;
    return h("span", { class: "chip", style: `border-color:${col};color:${col}` }, t(f));
  }

  function render(d) {
    root.innerHTML = "";
    if (d.course_label) {   // the course name goes in bold where {course} is in the sentence
      const parts = t("Showing course {course} only: every figure below is worked out for these students. Choose \"All students\" in the Course list for the whole class.").split("{course}");
      root.append(h("p", { class: "coursenote" }, parts[0], h("b", {}, d.course_label), parts.slice(1).join(d.course_label)));
    }
    const head = h("div", { class: "tiles" },
      tile(t("Sheets scanned"), String(d.n_scanned), t("{scored} scored · {review} to review", { scored: d.n_scored, review: d.n_review })),
      tile(t("Mean"), d.empty ? "–" : fmt(d.mean) + "%", d.empty ? "" : t("median {median}% · SD {sd}", { median: fmt(d.median), sd: fmt(d.sd) })),
      tile(t("Pass rate"), d.empty ? "–" : fmt(d.pass_rate) + "%", d.empty ? "" : t("{pass} of {scored} at ≥ {mark}%", { pass: d.n_pass, scored: d.n_scored, mark: fmt(d.pass_mark) })),
      tile(t("Highest"), d.empty ? "–" : fmt(d.max) + "%", d.empty ? "" : d.top.map(who).join(", ")),
      tile(t("Lowest"), d.empty ? "–" : fmt(d.min) + "%", d.empty ? "" : d.bottom.map(who).join(", ")));
    // the names in the two tiles open the student's page
    [d.top, d.bottom].forEach((list, k) => { if (list && list.length === 1) {
      const sub = head.children[3 + k].querySelector(".tile-sub");
      sub.innerHTML = ""; sub.append(h("a", { href: `/scans/${list[0].scan_id}/sheet` }, who(list[0])));
    } });
    root.append(head);
    if (d.empty) { root.append(h("p", { class: "muted" }, t("No scored sheets yet - scan the first one and this page fills in."))); return; }

    // distribution
    const dist = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, t("Distribution of scores")));
    dist.append(histogram(d.histogram, d.n_scored));
    dist.append(h("p", {}, h("b", {}, t("Shape:") + " "), d.shape_text));
    dist.append(h("p", { class: "small muted" },
      t("Skewness {skewness} (0 = symmetric; negative = tail on the low side), kurtosis {kurtosis} (0 = like a normal curve; positive = more peaked with a few extreme scores; negative = flat, spread out).",
        { skewness: fmt(d.skewness, 2), kurtosis: fmt(d.kurtosis, 2) }) + " " +
      t("Quartiles: 25 % of the class at or below {q1}%, 75 % at or below {q3}%.", { q1: fmt(d.q1), q3: fmt(d.q3) })));
    root.append(dist);

    // bands + reliability
    const two = h("div", { class: "grid2" });
    const bandCard = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, t("Grade bands")));
    const tb = h("table", {}, h("tr", {}, h("th", {}, t("Band")), h("th", {}, t("Range")), h("th", {}, t("Students")), h("th", {}, "")));
    d.bands.forEach(b => tb.append(h("tr", {}, h("td", {}, b.name), h("td", {}, `${fmt(b.from)} – ${fmt(b.to)}%`),
      h("td", {}, `${b.count} (${fmt(b.percent)}%)`),
      h("td", { style: "width:40%" }, h("span", { class: "hbar-track" }, h("span", { class: "hbar-fill", style: `width:${b.percent}%` }))))));
    bandCard.append(tb, h("p", { class: "hint" }, t("Bands and pass mark are set in Settings.")));
    const rel = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, t("Reliability of the test")),
      h("div", { class: "tile-value" }, d.kr20 === null ? "–" : fmt(d.kr20, 2)),
      h("p", {}, h("b", {}, t("KR-20:") + " "), d.kr20_text),
      h("p", { class: "small muted" }, t("KR-20 goes from 0 to 1 and says how consistently the questions rank the students: above 0.8 is good for a class test, below 0.5 means the result is noisy.") + " " +
        t("It rises with more questions and with questions that discriminate.")));
    two.append(bandCard, rel);
    root.append(two);

    // topics (only when the questions have topics): class % correct per topic, hardest first
    if (d.topics && d.topics.length) {
      const tc = h("div", { class: "card", id: "topics" }, h("h2", { style: "margin-top:0" }, t("Topics")));
      const tt = h("table", {}, h("tr", {}, h("th", {}, t("Topic")), h("th", {}, t("Questions")), h("th", {}, t("% correct")), h("th", {}, "")));
      d.topics.forEach(x => {
        const qs = h("span", { class: "small" });
        x.questions.forEach((q, i) => { if (i) qs.append(" "); qs.append(h("a", { class: "qlink", href: qurl(q) }, t("Q{n}", { n: q }))); });
        tt.append(h("tr", {}, h("td", {}, x.topic === null ? h("span", { class: "muted" }, t("No topic")) : x.topic),
          h("td", {}, h("span", {}, String(x.n) + " · "), qs),
          h("td", {}, fmt(x.pct_correct) + "%"),
          h("td", { style: "width:35%" }, h("span", { class: "hbar-track" }, h("span", { class: "hbar-fill", style: `width:${Math.max(0, Math.min(100, x.pct_correct || 0))}%` })))));
      });
      tc.append(tt, h("p", { class: "hint" }, t("Share of right answers on the questions of each topic, from the hardest topic to the easiest. Topics are set per question in the exam set.")));
      root.append(tc);
    }

    // per question
    const pq = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, t("Questions (original numbering)")));
    if (d.flagged.length) {
      const fl = h("div", { class: "flags" }, h("b", {}, (d.flagged.length === 1
        ? t("{n} question worth a look:", { n: d.flagged.length })
        : t("{n} questions worth a look:", { n: d.flagged.length })) + " "));
      d.flagged.forEach(q => fl.append(h("span", { class: "flagq" }, h("a", { class: "qlink", href: qurl(q.q) }, t("Q{n}", { n: q.q })), " ", ...q.flags.map(flagChip))));
      pq.append(fl);
    }
    pq.append(h("h3", {}, t("Percentage of students who answered correctly")));
    pq.append(h("p", { class: "small muted" }, t("From the most answered correctly to the most answered wrongly (ties in question order).")));
    const byCorrect = d.per_question.slice().sort((x, y) => y.pct_correct - x.pct_correct || x.q - y.q);
    pq.append(bars(byCorrect, "pct_correct", q => t("Q{n}", { n: q.q }), q => t("Q{n}: {text} (click to see who picked what)", { n: q.q, text: q.text }), 100, q => qurl(q.q)));
    pq.append(h("p", { class: "small muted" }, t("Discrimination = (share of the top 27 % of students who got it right) − (share of the bottom 27 %).") + " " +
      t("Above 0.3 is good; below 0.2 the question does not separate strong from weak students; negative means the weak students did better - usually a wrong key or an ambiguous question.")));
    const tbl = h("table", { class: "qtable" }, h("tr", {}, h("th", {}, t("Q")), h("th", {}, t("Question")), h("th", {}, t("Key")), h("th", {}, t("% correct")),
      h("th", {}, t("Discr.")), h("th", {}, t("Answers chosen (original letters; key in bold)")), h("th", {}, t("Blank / double"))));
    // a count that is not zero links to the students behind it
    const countLink = (q, anchor, cls, text, title) => anchor
      ? h("a", { class: cls, href: qurl(q.q, anchor), title: t("{what} - click to see who", { what: title }) }, text)
      : h("span", { class: cls + " zero", title }, text);
    d.per_question.forEach(q => {
      const choices = h("span", {});
      q.choices.forEach(c => choices.append(countLink(q, c.count ? `choice-${c.letter}` : null,
        "choicecount" + (c.is_key ? " key" : ""), `${c.letter}: ${c.count}`, c.text), " "));
      const blanks = h("span", {}, countLink(q, q.blank ? "blank" : null, "choicecount", String(q.blank), t("left blank")), " / ",
        countLink(q, q.multiple ? "multiple" : null, "choicecount", String(q.multiple), t("more than one mark")));
      tbl.append(h("tr", { class: q.flags.length ? "flagged" : "" }, h("td", {}, h("a", { class: "qlink", href: qurl(q.q) }, t("Q{n}", { n: q.q }))),
        h("td", { class: "small" }, q.text),
        h("td", {}, q.correct_choice), h("td", {}, fmt(q.pct_correct) + "%"), h("td", {}, fmt(q.discrimination, 2)),
        h("td", {}, choices), h("td", {}, blanks)));
    });
    const details = h("details", { ontoggle: () => { tableOpen = details.open; } },
      h("summary", { class: "small" }, t("Full table per question: click a number to see which students picked that answer")), tbl);
    if (tableOpen) details.open = true;
    pq.append(details);
    root.append(pq);

    // ranking
    const rk = h("div", { class: "card" }, h("h2", { style: "margin-top:0" }, t("Students, best to worst")));
    if (d.emails) {          // only once students have emails (Settings)
      const btn = h("button", { type: "button", class: "small secondary", title: t("copy the email addresses of the students below, separated by ;") },
        t("Copy emails of these {n} students", { n: d.students.length }));
      btn.addEventListener("click", () => window.copyEmails(d.students.map(r => d.emails[r.student_no]),
        d.students.filter(r => !d.emails[r.student_no]).length, btn));
      rk.append(h("p", {}, btn));
    }
    const rt = h("table", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, t("Student")), h("th", {}, t("Name")), h("th", {}, t("Version")), h("th", {}, t("Score")), h("th", {}, "%"), h("th", {}, "")));
    d.students.forEach((r, i) => rt.append(h("tr", {}, h("td", {}, String(i + 1)),
      h("td", {}, h("a", { href: `/scans/${r.scan_id}/sheet`, title: t("the student's page") }, h("code", {}, r.student_no || "?"))),
      h("td", {}, h("a", { href: `/scans/${r.scan_id}/sheet`, title: t("the student's page") }, r.name || "")),
      h("td", {}, String(r.version)), h("td", {}, `${r.score} / ${r.max_score}`), h("td", {}, fmt(r.percent) + "%"),
      h("td", {}, h("a", { href: `/scans/${r.scan_id}`, class: "small" }, t("edit"))))));
    rk.append(rt);
    root.append(rk);
  }

  async function load() {
    try {
      const want = course;
      const r = await fetch(`/sets/${window.SET_ID}/stats.json` + cq(), { cache: "no-store" });
      const d = await r.json();
      if (want !== course) return;          // the course was changed while this was loading
      render(d);
    } catch (e) {
      root.innerHTML = '<p class="problems">' + t("Could not load the statistics: {error}", { error: String(e) }) + "</p>";
    }
  }
  load();
  setInterval(() => { if (document.getElementById("auto").checked && !document.hidden) load(); }, 10000);
})();
