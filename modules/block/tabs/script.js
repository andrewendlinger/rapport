/* tabs: one visible panel; reveal switches to the panel holding the target. */
(function () {
  function panels(el) { return el.querySelectorAll(":scope > .rp-tabs-panels > .rp-tab"); }
  function show(el, i) {
    el.querySelectorAll(":scope > .rp-tabs-bar > .rp-tabs-btn").forEach(function (b, k) {
      b.classList.toggle("rp-on", k === i); b.setAttribute("aria-selected", k === i ? "true" : "false");
    });
    panels(el).forEach(function (p, k) { p.classList.toggle("rp-hidden", k !== i); });
  }
  Rapport.module("tabs", {
    mount: function (el) {
      el.querySelectorAll(":scope > .rp-tabs-bar > .rp-tabs-btn").forEach(function (b, i) {
        b.addEventListener("click", function () { show(el, i); });
      });
      show(el, 0);
    },
    reveal: function (el, target) {
      panels(el).forEach(function (p, i) { if (p.contains(target)) show(el, i); });
    }
  });
})();
