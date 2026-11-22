/* Autosave: a <form data-autosave> saves itself when something in it changes, with no Save button.

   The form is sent exactly as its submit button would send it (same action, same fields), with
   fetch; the server's usual redirect is followed and its ?msg=... (if any) is shown next to the
   form. So any existing form can use it without server changes.

   Markup:
     <form method="post" action="/settings/general" data-autosave>   saves on every change
       data-autosave-reload        reload the page after saving (e.g. the language)
       data-autosave-delay="800"   wait after typing (ms) before saving text fields (default 700)
       data-autosave-validate      check the fields first (required, min, max...); nothing is sent if one is wrong
       data-autosave-sync          after saving, show the values the server stored (it may clamp or tidy them)
     <span class="autosave-status"></span>   optional, inside the form: where "Saved" appears
     <button data-autosave-hide>             hidden when autosave runs (kept for no-JavaScript use)
     <button name="reset" value="1">         a named button that is pressed is sent with the form, as usual
   On a field:
     data-autosave-change        save only when the field is left (or Enter), not while typing
     data-autosave-when="regex"  while typing, save only once the value matches (e.g. a complete student
                                 number); otherwise when the field is left. A half-typed value is never sent:
                                 until then the last complete or left value is sent in its place.
   On any element around one or more fields:
     data-autosave-spot          "Saving…" / "Saved" / the server's message appear here (in its first
                                 label, or in an .autosave-status inside it) for the fields it holds

   Fields outside the form element that belong to it (form="id") work too.
   The server can say a value was refused by adding error=1 to the redirect (only done for requests
   with the X-Autosave header): the message is then shown as an error, the field is marked, and the
   page is not reloaded. A chosen file is sent once. Nothing is sent when nothing changed since the
   last save.

   Saves never overlap: a change during a save is sent right after it. A change still waiting to be sent
   when the page is left (a link clicked, the tab closed or hidden) is sent at once, with fetch's keepalive
   so it arrives even though the page goes away. Only forms marked with
   data-autosave are touched, so actions such as delete, restore or generate keep their buttons.
   Events on the form: "autosaved" (detail: message, response, elements) after a save the server accepted.
*/
(function () {
  var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
  var FLASH = "mcq-autosave-flash";
  var TEXTLIKE = /^(text|email|number|search|url|tel)$/;

  function makeStatus() {
    var el = document.createElement("span");
    el.className = "autosave-status small";
    el.setAttribute("aria-live", "polite");
    return el;
  }

  function formStatus(form) {
    var el = form.querySelector(".autosave-status");
    if (!el) {
      el = makeStatus();
      var btn = form.querySelector("[data-autosave-hide]");
      if (btn && btn.parentNode) btn.parentNode.insertBefore(el, btn.nextSibling);
      else form.appendChild(el);
    }
    return el;
  }

  function spotStatus(spot) {
    var el = spot.querySelector(".autosave-status");
    if (!el) {
      el = makeStatus();
      var label = spot.querySelector("label");
      (label || spot).appendChild(el);
    }
    return el;
  }

  function show(el, text, kind) {
    el.textContent = text;
    el.classList.remove("saving", "saved", "failed");
    if (kind) el.classList.add(kind);
  }

  function params(url) {
    try { return new URL(url, location.href).searchParams; } catch (e) { return null; }
  }

  function setup(form) {
    if (form._autosave) return;
    var busy = false, again = false, timer = null;
    var delay = parseInt(form.getAttribute("data-autosave-delay") || "700", 10);
    var spots = [], changed = [], typedSince = [];     // pending status places and fields
    var extra = null;                                   // the button pressed, e.g. a reset button: [name, value]
    var committed = new Map();                          // data-autosave-when fields: value that may be sent

    function fields() { return Array.prototype.slice.call(form.elements || []); }
    fields().forEach(function (el) {
      if (el.hasAttribute && el.hasAttribute("data-autosave-hide")) el.hidden = true;
      if (el.hasAttribute && el.hasAttribute("data-autosave-when")) committed.set(el, el.value);
    });
    form.querySelectorAll("[data-autosave-hide]").forEach(function (b) { b.hidden = true; });

    function complete(el) {
      try { return new RegExp(el.getAttribute("data-autosave-when"), "i").test(el.value); } catch (e) { return true; }
    }

    function body() {
      var fd = new FormData(form);
      committed.forEach(function (v, el) { if (el.name && !el.disabled) fd.set(el.name, v); });
      return fd;
    }
    function fingerprint(fd) {
      var out = [];
      fd.forEach(function (v, k) { out.push([k, typeof v === "string" ? v : "file:" + Math.random()]); });
      return JSON.stringify(out);
    }
    var last = fingerprint(body());

    // keepalive lets a save finish after the page is left; browsers allow it only for small bodies (about
    // 64 kB), so not for files or a long pasted list
    function keepable(fd) {
      var n = 0, file = false;
      fd.forEach(function (v, k) {
        if (typeof v === "string") n += k.length + v.length;
        else if (v && (v.size || v.name)) file = true;
      });
      return !file && n < 60000;
    }
    function send(fd) {
      var opts = { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Autosave": "1" } };
      if (!keepable(fd)) return fetch(form.action, opts);
      opts.keepalive = true;
      return fetch(form.action, opts).catch(function (err) {
        // refused as too large for keepalive: once more the ordinary way
        if (err && err.name === "TypeError") { opts.keepalive = false; return fetch(form.action, opts); }
        throw err;
      });
    }
    function invalid() {
      return form.hasAttribute("data-autosave-validate") && typeof form.checkValidity === "function" && !form.checkValidity();
    }

    function targets(list) {
      var out = [];
      list.forEach(function (s) { var el = spotStatus(s); if (out.indexOf(el) < 0) out.push(el); });
      if (!out.length) out.push(formStatus(form));
      return out;
    }

    function note(el) {
      if (!el) return;
      if (changed.indexOf(el) < 0) changed.push(el);
      if (typedSince.indexOf(el) < 0) typedSince.push(el);
      var spot = el.closest ? el.closest("[data-autosave-spot]") : null;
      if (spot && spots.indexOf(spot) < 0) spots.push(spot);
    }

    function save() {
      if (timer) { clearTimeout(timer); timer = null; }
      if (busy) { again = true; return; }
      var places = targets(spots), els = changed;
      if (invalid()) {
        var bad = fields().filter(function (el) { return el.validity && !el.validity.valid; })[0];
        places.forEach(function (p) { show(p, (bad && bad.validationMessage) || t("Not saved: check the value."), "failed"); });
        if (typeof form.reportValidity === "function") form.reportValidity();
        return;
      }
      var fd = body(), fp = fingerprint(fd), base = fp;
      if (extra) { fd.append(extra[0], extra[1]); fp = fingerprint(fd); extra = null; }
      spots = []; changed = [];
      if (fp === last) {                                  // nothing new to store (e.g. changed back)
        places.forEach(function (p) { show(p, "", ""); });
        els.forEach(function (el) { el.classList.remove("autosave-invalid"); el.removeAttribute("aria-invalid"); });
        return;
      }
      busy = true; typedSince = [];
      places.forEach(function (p) { show(p, t("Saving…"), "saving"); });
      send(fd)
        .then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
          if (/\/login$/.test(r.url.split(/[?#]/)[0])) throw new Error("login");   // sent to the sign-in page: not saved
          var q = params(r.url), msg = (q && q.get("msg")) || "", refused = !!(q && q.get("error") === "1");
          fields().forEach(function (el) { if (el.type === "file" && (el.value || (el.files && el.files.length))) { try { el.value = ""; } catch (e) {} } });
          if (refused) {
            places.forEach(function (p) { show(p, msg || t("Not saved: check the value."), "failed"); });
            els.forEach(function (el) { el.classList.add("autosave-invalid"); el.setAttribute("aria-invalid", "true"); });
            return;
          }
          last = base;
          els.forEach(function (el) { el.classList.remove("autosave-invalid"); el.removeAttribute("aria-invalid"); });
          if (form.hasAttribute("data-autosave-reload")) {
            try { sessionStorage.setItem(FLASH, JSON.stringify({ action: form.getAttribute("action"), msg: msg })); } catch (e) {}
            location.reload();
            return;
          }
          places.forEach(function (p) { show(p, msg || t("Saved"), "saved"); });
          var done = form.hasAttribute("data-autosave-sync") ? r.text().then(sync) : Promise.resolve();
          return done.then(function () {
            if (!typedSince.length && !again) last = fingerprint(body());   // the values as stored
            form.dispatchEvent(new CustomEvent("autosaved", { detail: { message: msg, response: r, elements: els } }));
          });
        })
        .catch(function () { places.forEach(function (p) { show(p, t("Could not save. Check the connection and try again."), "failed"); }); })
        .then(function () {
          busy = false;
          if (again) { again = false; save(); }
        });
    }

    // the values the server stored, from the page it answered with (fields typed in meanwhile are left alone)
    function sync(html) {
      if (typeof DOMParser === "undefined") return;
      var doc = new DOMParser().parseFromString(html, "text/html");
      var action = form.getAttribute("action");
      var same = Array.prototype.filter.call(document.querySelectorAll("form"), function (f) { return f.getAttribute("action") === action; });
      var other = form.id ? doc.getElementById(form.id)
        : Array.prototype.filter.call(doc.querySelectorAll("form"), function (f) { return f.getAttribute("action") === action; })[same.indexOf(form)];
      if (!other) return;
      var mine = fields(), theirs = Array.prototype.slice.call(other.elements || []);
      mine.forEach(function (el, i) {
        var o = theirs[i];
        if (!o || o.name !== el.name || o.type !== el.type || !el.name || el.type === "file" || el.type === "hidden") return;
        if (typedSince.indexOf(el) >= 0 || el === document.activeElement) return;
        if (el.type === "checkbox" || el.type === "radio") el.checked = o.checked;
        else if (el.value !== o.value) el.value = o.value;
        if (committed.has(el)) committed.set(el, el.value);
      });
    }

    form._autosave = {
      change: function (e) {
        var el = e.target;
        if (el && el.type === "file" && !el.files.length) return;
        if (committed.has(el)) committed.set(el, el.value);
        note(el);
        save();
      },
      input: function (e) {
        var el = e.target, tag = el && el.tagName, type = el && el.type;
        if (!(tag === "TEXTAREA" || (tag === "INPUT" && TEXTLIKE.test(type)))) return;
        if (typedSince.indexOf(el) < 0) typedSince.push(el);
        if (el.hasAttribute("data-autosave-change")) return;
        if (committed.has(el)) {
          if (!complete(el)) return;
          committed.set(el, el.value);
        }
        note(el);
        if (timer) clearTimeout(timer);
        timer = setTimeout(save, delay);
      },
    };
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (e.submitter && e.submitter.name) extra = [e.submitter.name, e.submitter.value];
      committed.forEach(function (v, el) { committed.set(el, el.value); note(el); });
      save();
    });
    form.autosaveNow = save;

    // the page is being left: a change still waiting (for the pause after typing, or for the save that is
    // running) is sent now
    form._autosave.flush = function () {
      if (!timer && !again) return;
      if (!busy) { save(); return; }
      if (timer) { clearTimeout(timer); timer = null; }
      again = false;
      if (invalid()) return;
      var fd = body(), fp = fingerprint(fd);
      if (fp === last || !keepable(fd)) return;
      last = fp; spots = []; changed = []; typedSince = [];
      send(fd).catch(function () {});
    };

    try {                                                  // the message of a save that reloaded the page
      var f = JSON.parse(sessionStorage.getItem(FLASH) || "null");
      if (f && f.action === form.getAttribute("action")) {
        sessionStorage.removeItem(FLASH);
        show(formStatus(form), f.msg || t("Saved"), "saved");
      }
    } catch (e) {}
  }

  // events of fields that belong to an autosave form, also those placed outside it with form="id"
  function relay(kind) {
    document.addEventListener(kind, function (e) {
      var f = e.target && e.target.form;
      if (f && f._autosave && f.hasAttribute("data-autosave")) f._autosave[kind](e);
    });
  }
  relay("change");
  relay("input");

  function leaving() {
    document.querySelectorAll("form[data-autosave]").forEach(function (f) { if (f._autosave && f._autosave.flush) f._autosave.flush(); });
  }
  if (typeof window.addEventListener === "function") window.addEventListener("pagehide", leaving);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") leaving(); });

  function init() { document.querySelectorAll("form[data-autosave]").forEach(setup); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  window.MCQAutosave = { setup: setup };
})();
