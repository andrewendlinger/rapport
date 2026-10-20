/* print: expand collapsed sections for printing, restore afterwards. */
Rapport.module("print", {
  init: function () {
    var opened = [];
    window.addEventListener("beforeprint", function () {
      document.querySelectorAll(".rp-content details:not([open])").forEach(function (d) { d.open = true; opened.push(d); });
    });
    window.addEventListener("afterprint", function () {
      opened.forEach(function (d) { d.open = false; });
      opened = [];
    });
  }
});
