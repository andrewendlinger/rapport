/* copy-code: a copy button per code block (UI chrome, excluded from feedback quotes). */
Rapport.module("copy-code", {
  init: function () {
    if (!navigator.clipboard) return;     // undefined outside a secure context
    document.querySelectorAll(".rp-code").forEach(function (fig) {
      var code = fig.querySelector("pre code");
      if (!code) return;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "rp-copy";
      b.setAttribute("data-rp-ui", "");
      b.textContent = "Copy";
      b.addEventListener("click", function () {
        navigator.clipboard.writeText(code.textContent).then(function () {
          b.textContent = "Copied";
          setTimeout(function () { b.textContent = "Copy"; }, 1400);
        }, function () { /* the browser refused: nothing to show */ });
      });
      fig.appendChild(b);
    });
  }
});
