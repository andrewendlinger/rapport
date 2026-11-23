Rapport.module("reports", {
  mount: function (el) {
    var served = Rapport.data.report && Rapport.data.report.served;
    var input = el.querySelector(".rp-reports-filter");
    var count = el.querySelector(".rp-reports-count");
    var rows = el.querySelectorAll(".rp-reports-row");
    var groups = el.querySelectorAll(".rp-reports-group");
    var FILTER = "rp-reports-filter", OPEN = "rp-reports-open";
    var pending = 0, filtering = false;

    function load(key) {
      try { return sessionStorage.getItem(key); } catch (e) { return null; }
    }
    function save(key, value) {
      try { sessionStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }

    // which groups the reader opened or closed: kept across the reloads that follow a rebuild
    var opened = {};
    try { opened = JSON.parse(load(OPEN) || "{}") || {}; } catch (e) { opened = {}; }
    function restoreGroups() {
      for (var i = 0; i < groups.length; i++) {
        var id = groups[i].getAttribute("data-group");
        groups[i].hidden = false;
        groups[i].open = id in opened ? opened[id] : groups[i].getAttribute("data-open") === "1";
      }
    }

    function apply() {
      var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0, i;
      filtering = words.length > 0;
      for (i = 0; i < rows.length; i++) {
        var text = rows[i].getAttribute("data-search") || "";
        var ok = words.every(function (w) { return text.indexOf(w) !== -1; });
        rows[i].hidden = !ok;
        if (ok) shown++;
      }
      if (filtering) {         // every match in view: open the groups that have one, hide the others
        for (i = 0; i < groups.length; i++) {
          var any = groups[i].querySelector(".rp-reports-row:not([hidden])");
          groups[i].hidden = !any;
          groups[i].open = !!any;
        }
      } else {
        restoreGroups();
      }
      count.textContent = (filtering ? shown + " of " : "") + rows.length + (rows.length === 1 ? " report" : " reports");
      save(FILTER, input.value);
    }

    function fail(row, err) {
      var box = row.querySelector(".rp-reports-error");
      if (!box) {
        box = document.createElement("div");
        box.className = "rp-reports-error";
        row.appendChild(box);
      }
      box.textContent = err.message || String(err);
    }

    function act(btn) {
      var what = btn.getAttribute("data-act");
      var row = btn.closest(".rp-reports-row");
      var tab = null;
      if (what === "serve") {
        tab = window.open("", "_blank");      // now, in the click: later it'd be a blocked popup
        if (tab) tab.opener = null;
      }
      btn.disabled = true;
      pending++;
      Rapport.api("reports", "/" + what, { body: row ? { dir: row.getAttribute("data-dir") } : {} }).then(function (res) {
        pending--;
        btn.disabled = false;
        if (tab && res.url) tab.location.href = res.url;
        else if (tab) tab.close();
      }, function (err) {
        pending--;
        btn.disabled = false;
        if (tab) tab.close();
        if (row) fail(row, err); else window.alert(err.message);
      });
    }

    if (served) el.classList.add("is-live");
    if (served && el.hasAttribute("data-pending")) {      // screenshots left over: build again for them
      setTimeout(function () { Rapport.api("reports", "/refresh", { body: {} }).then(null, function () {}); }, 1500);
    }
    Rapport.busy(function () { return pending > 0; });
    input.value = load(FILTER) || "";
    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { input.value = ""; apply(); }
    });
    el.addEventListener("click", function (ev) {      // the reader's own toggles, not the filter's
      var summary = ev.target.closest(".rp-reports-group > summary");
      if (!summary || filtering) return;
      var group = summary.parentNode;
      opened[group.getAttribute("data-group")] = !group.open;      // the click toggles it right after
      save(OPEN, JSON.stringify(opened));
    });
    el.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-act], [data-filter]");
      if (!t || !el.contains(t)) return;
      if (t.hasAttribute("data-filter")) {
        input.value = t.getAttribute("data-filter");
        apply();
        return;
      }
      if (served) act(t);
    });
    apply();
  }
});
