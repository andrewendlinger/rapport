/* tabs: one visible panel; reveal switches to the panel holding the target. Each panel
   names its button with aria-labelledby and fires toggle when shown or hidden, as <details>
   does: feedback puts marks for a hidden panel on the tab list and re-places them. */
(function () {
  var seq = 0;
  function panels(el) { return el.querySelectorAll(":scope > .rp-tabs-panels > .rp-tab"); }
  function show(el, i) {
    el.querySelectorAll(":scope > .rp-tabs-bar > .rp-tabs-btn").forEach(function (b, k) {
      b.classList.toggle("rp-on", k === i); b.setAttribute("aria-selected", k === i ? "true" : "false");
    });
    panels(el).forEach(function (p, k) {
      if (p.classList.contains("rp-hidden") === (k !== i)) return;
      p.classList.toggle("rp-hidden", k !== i);
      p.dispatchEvent(new Event("toggle"));
    });
  }
  Rapport.module("tabs", {
    mount: function (el) {
      var ps = panels(el);
      el.querySelectorAll(":scope > .rp-tabs-bar > .rp-tabs-btn").forEach(function (b, i) {
        b.id = b.id || "rp-tab-" + (++seq);
        if (ps[i]) { ps[i].setAttribute("role", "tabpanel"); ps[i].setAttribute("aria-labelledby", b.id); }
        b.addEventListener("click", function () { show(el, i); });
      });
      show(el, 0);
    },
    reveal: function (el, target) {
      panels(el).forEach(function (p, i) { if (p.contains(target)) show(el, i); });
    }
  });
})();
