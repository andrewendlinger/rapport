/* image: click to view full size. */
Rapport.module("image", {
  mount: function (el) {
    var img = el.querySelector("img[data-rp-zoom]");
    if (!img) return;
    img.addEventListener("click", function () {
      if (window.getSelection && !window.getSelection().isCollapsed) return;
      var ov = document.createElement("div");
      ov.className = "rp-zoom-overlay";
      ov.setAttribute("data-rp-ui", "");
      var big = document.createElement("img");
      big.src = img.src;
      big.alt = img.alt;
      ov.appendChild(big);
      function close() { ov.remove(); document.removeEventListener("keydown", onKey); }
      function onKey(e) { if (e.key === "Escape") close(); }
      ov.addEventListener("click", close);
      document.addEventListener("keydown", onKey);
      document.body.appendChild(ov);
    });
  }
});
