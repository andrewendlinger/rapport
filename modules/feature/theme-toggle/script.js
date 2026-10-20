/* theme-toggle: system → light → dark, stored per browser. */
Rapport.module("theme-toggle", {
  init: function () {
    var btn = document.querySelector(".rp-theme-toggle");
    if (!btn) return;
    var MODES = ["system", "light", "dark"], KEY = "rp-theme";
    var pinned = document.documentElement.getAttribute("data-theme");
    function apply(mode) {
      if (mode === "system") {
        if (pinned) document.documentElement.setAttribute("data-theme", pinned);
        else document.documentElement.removeAttribute("data-theme");
      } else document.documentElement.setAttribute("data-theme", mode);
      btn.title = btn.ariaLabel = "Theme: " + mode;
    }
    var mode = "system";
    try { mode = localStorage.getItem(KEY) || "system"; } catch (e) { /* blocked */ }
    if (MODES.indexOf(mode) < 0) mode = "system";
    apply(mode);
    btn.addEventListener("click", function () {
      mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
      try { localStorage.setItem(KEY, mode); } catch (e) { /* blocked */ }
      apply(mode);
    });
  }
});
