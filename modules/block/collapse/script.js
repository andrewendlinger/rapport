/* collapse: open when something inside needs to be shown (feedback, links). */
Rapport.module("collapse", {
  reveal: function (el) { var d = el.matches("details") ? el : el.querySelector("details"); if (d) d.open = true; }
});
