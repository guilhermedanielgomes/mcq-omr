var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
/* Sortable tables: click a column heading to sort the rows by that column, click it again to reverse.

   Opt in with data-sortable on the table, or on an element around tables (tables added to it later
   are sortable too, e.g. the statistics page, which is rebuilt every few seconds: the order chosen
   is kept). data-nosort on a table or a heading leaves it alone; headings with no text are not sortable.
   The heading row is the <thead> row, or else the first row when it has <th> cells.

   On a heading <th>:
     data-sort-id="score"           a name for the column (for the options below)
     data-sort-first="desc"         the first click sorts from high to low (default: low to high, A to Z)
     data-sort-then="name student"  ties are ordered by these columns (low to high), then as on the page
   On a cell:  data-sort-value="0.85"   sort by this instead of the text shown
   On a row:   data-sort-group="1"      groups stay in this order whatever the column (e.g. older scans last)
               data-sort-follow         stays right below the row before it (a detail row)
   On the table:
     data-sort-default="score"      (or "score:asc") the order when the page opens
     data-sort-remember="key"       this browser remembers the last choice (localStorage)

   Values: numbers sort as numbers ("17 / 20", "85.7%", "+4", "−3", dates such as 2025-03-14 10:30);
   text by the page's language, ignoring case and accents; empty cells ("", "–", "-", "?") come last
   both ways. In a column where most cells are numbers, cells with words (e.g. "absent") come after
   the numbers. Drop-downs and text boxes in a cell count with the value they show.
   Only the order of the rows changes: links, forms, filters (hidden rows) and buttons keep working.

   Lists that are not tables (e.g. result cards): see MCQSort.list below.
*/
(function () {
  var lang = (document.documentElement && document.documentElement.lang) || undefined;
  var coll;
  try { coll = new Intl.Collator(lang, { sensitivity: "base", numeric: true }); }
  catch (e) { coll = new Intl.Collator(undefined, { sensitivity: "base", numeric: true }); }
  var EMPTY = /^[\s–—\-?]*$/;
  var DATE = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/;
  var NUM = /^([+\-−]?)\s*(\d+(?:\.\d+)?)/;
  var MORE = /\d+(?:\.\d+)?/g;
  var memory = {};            // order chosen per table on this page, for tables that are built again
  var counter = 0;            // page order of rows, for ties

  // kind 0 = number(s), 1 = text, 2 = empty
  function key(text) {
    var s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    if (EMPTY.test(s)) return { kind: 2, s: "" };
    var m = DATE.exec(s);
    if (m) return { kind: 0, n: m.slice(1).map(function (x) { return Number(x || 0); }), s: s };
    m = NUM.exec(s);
    if (m) {
      var first = Number(m[2]) * (m[1] === "-" || m[1] === "−" ? -1 : 1);
      var rest = (s.slice(m[0].length).match(MORE) || []).map(Number);
      return { kind: 0, n: [first].concat(rest), s: s };
    }
    return { kind: 1, s: s };
  }

  function numericMode(keys) {
    var nums = 0, words = 0;
    keys.forEach(function (k) { if (k.kind === 0) nums++; else if (k.kind === 1) words++; });
    return nums > 0 && nums >= words;
  }

  // dir: 1 = low to high, -1 = high to low; empty values last both ways
  function compare(a, b, dir, numeric) {
    var ka = a.kind === 0 && !numeric ? 1 : a.kind, kb = b.kind === 0 && !numeric ? 1 : b.kind;
    if (ka !== kb) return ka - kb;
    if (ka === 2) return 0;
    if (ka === 1) return coll.compare(a.s, b.s) * dir;
    for (var i = 0; i < Math.max(a.n.length, b.n.length); i++) {
      if (i >= a.n.length) return -dir;
      if (i >= b.n.length) return dir;
      if (a.n[i] !== b.n[i]) return (a.n[i] - b.n[i]) * dir;
    }
    return 0;
  }

  function textOf(node) {
    if (node.nodeType === 3) return node.nodeValue;
    if (node.nodeType !== 1) return "";
    if (node.hidden || node.hasAttribute("data-sort-skip") || (node.style && node.style.display === "none")) return "";
    var tag = node.tagName;
    if (tag === "SELECT") { var o = node.options[node.selectedIndex]; return o ? o.text : ""; }
    if (tag === "INPUT") return /^(text|email|number|search|url|tel|date)$/.test(node.type) ? node.value : "";
    if (tag === "TEXTAREA") return node.value;
    if (tag === "BUTTON" || tag === "SCRIPT" || tag === "STYLE" || tag === "TEMPLATE") return "";
    var out = "";
    for (var i = 0; i < node.childNodes.length; i++) out += textOf(node.childNodes[i]);
    return out;
  }

  function cellValue(cell) {
    if (!cell) return "";
    var v = cell.getAttribute("data-sort-value");
    return v !== null ? v : textOf(cell);
  }

  function cellAt(row, col) {
    var c = 0;
    for (var i = 0; i < row.cells.length; i++) {
      var span = row.cells[i].colSpan || 1;
      if (col < c + span) return row.cells[i];
      c += span;
    }
    return null;
  }

  function colOf(th) {
    var c = 0, cells = th.parentNode.cells;
    for (var i = 0; i < cells.length && cells[i] !== th; i++) c += cells[i].colSpan || 1;
    return c;
  }

  function headerRow(table) {
    if (table.tHead && table.tHead.rows.length) return table.tHead.rows[0];
    var first = table.rows[0];
    return first && first.querySelector("th") ? first : null;
  }

  // the rows to sort, each with the rows that follow it
  function units(table) {
    var head = table._sort.head, out = [], seen = false;
    Array.prototype.forEach.call(table.rows, function (r) {
      if (r === head) { seen = true; return; }
      var sec = r.parentNode && r.parentNode.tagName;
      if (!seen || sec === "THEAD" || sec === "TFOOT") return;
      if (r._sortIndex === undefined) r._sortIndex = ++counter;
      if (r.hasAttribute("data-sort-follow") && out.length) out[out.length - 1].follow.push(r);
      else out.push({ row: r, follow: [], group: parseFloat(r.getAttribute("data-sort-group")) || 0 });
    });
    return out;
  }

  function headings(table) { return table._sort.ths; }
  function byId(table, id) {
    return headings(table).filter(function (th) { return th.getAttribute("data-sort-id") === id; })[0] || null;
  }
  function firstDir(th) { return th.getAttribute("data-sort-first") === "desc" ? -1 : 1; }

  function sortTable(table, th, dir) {
    var list = units(table);
    var cols = [[colOf(th), dir]];
    (th.getAttribute("data-sort-then") || "").split(/\s+/).forEach(function (id) {
      var other = id && byId(table, id);
      if (other && other !== th) cols.push([colOf(other), 1]);
    });
    var keys = cols.map(function (c) {
      var ks = list.map(function (u) { return key(cellValue(cellAt(u.row, c[0]))); });
      return { ks: ks, numeric: numericMode(ks), dir: c[1] };
    });
    var order = list.map(function (u, i) { return i; });
    order.sort(function (i, j) {
      var a = list[i], b = list[j];
      if (a.group !== b.group) return a.group - b.group;
      for (var k = 0; k < keys.length; k++) {
        var r = compare(keys[k].ks[i], keys[k].ks[j], keys[k].dir, keys[k].numeric);
        if (r) return r;
      }
      return a.row._sortIndex - b.row._sortIndex;
    });
    order.forEach(function (i) {
      var u = list[i], parent = u.row.parentNode;
      parent.appendChild(u.row);
      u.follow.forEach(function (f) { parent.appendChild(f); });
    });
    headings(table).forEach(function (h) {
      if (h === th) h.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
      else h.removeAttribute("aria-sort");
    });
    table._sort.th = th; table._sort.dir = dir;
    memory[table._sort.mem] = { col: colOf(th), dir: dir };
    var store = table.getAttribute("data-sort-remember");
    if (store) {
      var id = th.getAttribute("data-sort-id") || "col" + colOf(th);
      try { localStorage.setItem(store, dir === firstDir(th) ? id : id + ":" + (dir > 0 ? "asc" : "desc")); } catch (e) {}
    }
  }

  function parseChoice(table, text) {
    if (!text) return null;
    var p = String(text).split(":"), th = byId(table, p[0]);
    if (!th && /^col\d+$/.test(p[0])) {
      var c = Number(p[0].slice(3));
      th = headings(table).filter(function (h) { return colOf(h) === c; })[0] || null;
    }
    if (!th) return null;
    return { th: th, dir: p[1] === "asc" ? 1 : p[1] === "desc" ? -1 : firstDir(th) };
  }

  function eligible(table) {
    return table.closest && !table.hasAttribute("data-nosort") && !!table.closest("[data-sortable]");
  }

  function setup(table) {
    if (table._sort || !eligible(table)) return;
    var head = headerRow(table);
    if (!head) return;
    var ths = Array.prototype.filter.call(head.cells, function (th) {
      return th.tagName === "TH" && !th.hasAttribute("data-nosort") && (th.textContent.trim() || th.hasAttribute("data-sort-id"));
    });
    if (!ths.length) return;
    var sig = Array.prototype.map.call(head.cells, function (c) { return c.textContent.trim(); }).join("|");
    var same = Array.prototype.filter.call(document.querySelectorAll("table"), function (x) {
      return x._sort && x._sort.sig === sig;
    }).length;
    table._sort = { head: head, ths: ths, sig: sig, mem: sig + "#" + same, th: null, dir: 0 };
    ths.forEach(function (th) {
      th.classList.add("sortable");
      if (!th.hasAttribute("tabindex")) th.setAttribute("tabindex", "0");
      var arrow = document.createElement("span");
      arrow.className = "sortarrow";
      arrow.setAttribute("aria-hidden", "true");
      th.appendChild(arrow);
      function go(e) {
        if (e.target !== th && e.target.closest && e.target.closest("a, button, input, select, textarea, label") ) return;
        if (e.preventDefault) e.preventDefault();
        var s = table._sort;
        sortTable(table, th, s.th === th ? -s.dir : firstDir(th));
      }
      th.addEventListener("click", go);
      th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") go(e); });
    });
    units(table);                                  // number the rows in their page order
    var choice = null;
    var store = table.getAttribute("data-sort-remember");
    if (store) { try { choice = parseChoice(table, localStorage.getItem(store)); } catch (e) {} }
    if (!choice && memory[table._sort.mem]) {
      var m = memory[table._sort.mem];
      var th = ths.filter(function (h) { return colOf(h) === m.col; })[0];
      if (th) choice = { th: th, dir: m.dir };
    }
    if (!choice) choice = parseChoice(table, table.getAttribute("data-sort-default"));
    if (choice) sortTable(table, choice.th, choice.dir);
  }

  function scan(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.tagName === "TABLE") setup(node);
    else if (node.tagName !== "TR" && node.tagName !== "TD" && node.querySelectorAll) {
      Array.prototype.forEach.call(node.querySelectorAll("table"), setup);
    }
  }

  /* Lists that are not tables:
       <div data-sortlist="#log" data-sort-default="order:desc">
         <a href="#" data-sort-by="order">…</a> <a href="#" data-sort-by="score" data-sort-first="desc">…</a>
       </div>
     sorts the children of #log by their data-sort-<key> attribute (same rules as cells), also when
     items are added later. The active control gets the class sorted-asc or sorted-desc. */
  function list(bar) {
    if (bar._sort) return;
    var box = document.querySelector(bar.getAttribute("data-sortlist"));
    if (!box) return;
    var controls = Array.prototype.slice.call(bar.querySelectorAll("[data-sort-by]"));
    var state = { by: null, dir: 1 }, observer = null;
    function apply() {
      if (!state.by) return;
      var items = Array.prototype.slice.call(box.children);
      var keys = items.map(function (el) { return key(el.getAttribute("data-sort-" + state.by)); });
      var numeric = numericMode(keys);
      items.forEach(function (el) { if (el._sortIndex === undefined) el._sortIndex = ++counter; });
      var order = items.map(function (el, i) { return i; });
      order.sort(function (i, j) { return compare(keys[i], keys[j], state.dir, numeric) || items[i]._sortIndex - items[j]._sortIndex; });
      order.forEach(function (i) { box.appendChild(items[i]); });
      if (observer) observer.takeRecords();        // our own moves are not new items
      controls.forEach(function (c) {
        c.classList.toggle("sorted-asc", c.getAttribute("data-sort-by") === state.by && state.dir > 0);
        c.classList.toggle("sorted-desc", c.getAttribute("data-sort-by") === state.by && state.dir < 0);
      });
    }
    controls.forEach(function (c) {
      c.addEventListener("click", function (e) {
        if (e.preventDefault) e.preventDefault();
        var by = c.getAttribute("data-sort-by");
        state.dir = state.by === by ? -state.dir : (c.getAttribute("data-sort-first") === "desc" ? -1 : 1);
        state.by = by;
        apply();
      });
    });
    var d = (bar.getAttribute("data-sort-default") || "").split(":");
    if (d[0]) { state.by = d[0]; state.dir = d[1] === "desc" ? -1 : 1; }
    bar._sort = { apply: apply, state: state };
    if (typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(function (records) {
        if (records.some(function (r) { return r.addedNodes.length; })) apply();
      });
      observer.observe(box, { childList: true });
    }
    apply();
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("table"), setup);
    Array.prototype.forEach.call(document.querySelectorAll("[data-sortlist]"), list);
    if (typeof MutationObserver !== "undefined") {
      new MutationObserver(function (records) {
        records.forEach(function (r) { Array.prototype.forEach.call(r.addedNodes, scan); });
      }).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  window.MCQSort = { setup: setup, sort: sortTable, list: list, key: key, compare: compare, numericMode: numericMode };
})();
