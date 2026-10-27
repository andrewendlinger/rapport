/* live-reload: poll the server; reload when the report was rebuilt, unless the
   reader is busy (Rapport.busy(): a comment is being typed, text is selected),
   in which case offer a pill instead. Also relays feedback changes and build errors. */
Rapport.module("live-reload", {
  init: function (ctx) {
    var serve = ctx.data.serve || {};
    var api = serve.api || "/api";
    var buildId = serve.build_id;
    var rev = null, pill = null, errBox = null, errKey = "", dismissed = "";

    function showPill() {
      if (pill) return;
      pill = document.createElement("div");
      pill.className = "rp-lr-pill";
      pill.setAttribute("data-rp-ui", "");
      pill.setAttribute("role", "status");
      pill.innerHTML = "<span>Report updated</span>";
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = "Reload";
      b.addEventListener("click", Rapport.reload);
      pill.appendChild(b);
      document.body.appendChild(pill);
    }

    function showErrors(errors) {
      var key = (errors || []).join("\n");
      if (key === errKey) return;
      errKey = key;
      if (errBox) { errBox.remove(); errBox = null; }
      if (!key || key === dismissed) return;
      errBox = document.createElement("div");
      errBox.className = "rp-lr-errors";
      errBox.setAttribute("data-rp-ui", "");
      errBox.setAttribute("role", "alert");
      var close = document.createElement("button");
      close.textContent = "✕";
      close.setAttribute("aria-label", "Dismiss");
      close.addEventListener("click", function () { dismissed = key; errBox.remove(); errBox = null; });
      var title = document.createElement("b");
      title.textContent = "The report has build errors (showing the last build):";
      var ul = document.createElement("ul");
      errors.forEach(function (e) { var li = document.createElement("li"); li.textContent = e; ul.appendChild(li); });
      errBox.appendChild(close); errBox.appendChild(title); errBox.appendChild(ul);
      document.body.appendChild(errBox);
    }

    var down = false;
    function poll() {
      fetch(api + "/meta", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (m) {
        if (down) { down = false; Rapport.emit("server:up"); }
        if (buildId != null && m.build_id !== buildId) {
          if (Rapport.busy()) showPill(); else return Rapport.reload();
        }
        if (rev !== null && m.feedback_rev !== rev) Rapport.emit("feedback:changed", m);
        rev = m.feedback_rev;
        showErrors(m.errors);
      }).catch(function () {
        if (!down) { down = true; Rapport.emit("server:down"); }
      }).then(function () {
        setTimeout(poll, ctx.config.interval_ms || 1000);
      });
    }
    poll();
  }
});
