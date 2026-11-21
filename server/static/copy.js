var t = window.t || function (s, v) { return v ? s.replace(/\{(\w+)\}/g, function (m, k) { return k in v ? v[k] : m; }) : s; };
/* "Copy emails" buttons. A button either carries the addresses itself
     <button class="copyemails" data-emails="a@x.ie; b@x.ie" data-missing="2">
   or collects them from the visible rows of a table
     <button class="copyemails" data-from="#tableid">   (rows with data-email="..."; "" = no email)
   The addresses are copied separated by "; " (Outlook's separator; most mail programs split on it). */
(function () {
  const SEP = "; ";

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    // http://<ip> (another computer on the network) has no clipboard API: the old way
    return new Promise((resolve, reject) => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy refused")); }
      catch (e) { reject(e); } finally { ta.remove(); }
    });
  }

  function flash(btn, msg) {
    if (!btn) return;
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    btn.textContent = msg;
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.textContent = btn.dataset.label; }, 2500);
  }

  // emails: list of addresses; missing: how many of the students have none
  window.copyEmails = function (emails, missing, btn) {
    const list = [...new Set(emails.filter(Boolean))];
    const v = { n: list.length, missing: missing };
    if (!list.length) { flash(btn, missing ? t("No emails ({missing} without email)", v) : t("No emails")); return; }
    const done = list.length === 1
      ? (missing ? t("Copied {n} email ({missing} without email)", v) : t("Copied {n} email", v))
      : (missing ? t("Copied {n} emails ({missing} without email)", v) : t("Copied {n} emails", v));
    copyText(list.join(SEP)).then(
      () => flash(btn, done),
      () => { flash(btn, t("Copy failed - shown below")); window.prompt(t("Copy these addresses:"), list.join(SEP)); });
  };

  document.addEventListener("click", e => {
    const btn = e.target.closest("button.copyemails");
    if (!btn || !btn.dataset) return;
    e.preventDefault();
    if (btn.dataset.from) {
      const rows = [...document.querySelectorAll(btn.dataset.from + " [data-email]")]
        .filter(r => r.offsetParent !== null);            // visible rows only (filters, sorting)
      // "without email" counts students, not rows (a student scanned twice, unreadable numbers once each)
      const noEmail = new Set(rows.filter(r => !r.dataset.email).map((r, i) => r.dataset.student || "row" + i));
      window.copyEmails(rows.map(r => r.dataset.email), noEmail.size, btn);
    } else {
      window.copyEmails((btn.dataset.emails || "").split(SEP), Number(btn.dataset.missing || 0), btn);
    }
  });
})();
