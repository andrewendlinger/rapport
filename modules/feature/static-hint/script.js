/* static-hint: opened via file:// there is no server, so no commenting; say how to get it.
   The folder comes from the page's own location, so report.html embeds no local paths. */
Rapport.module("static-hint", {
  init: function (ctx) {
    if (location.protocol !== "file:") return;
    var KEY = "rp-hint-dismissed:" + location.pathname;
    try { if (sessionStorage.getItem(KEY)) return; } catch (e) { /* show */ }
    var dir = decodeURIComponent(location.pathname).replace(/\/[^/]*$/, "");
    if (/^\/[A-Za-z]:\//.test(dir)) dir = dir.slice(1);   // file:///C:/… on Windows
    var hint = document.createElement("div");
    hint.className = "rp-hint";
    hint.setAttribute("data-rp-ui", "");
    var label = document.createElement("span");
    label.textContent = "Read-only copy. To comment, ask your agent to serve it, or run:";
    var cmd = document.createElement("code");
    cmd.textContent = ((ctx.config && ctx.config.command) || "rapport") + " serve '" + dir + "'";
    var close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "✕";
    close.addEventListener("click", function () {
      try { sessionStorage.setItem(KEY, "1"); } catch (e) { /* per view */ }
      hint.remove();
    });
    hint.appendChild(label); hint.appendChild(cmd); hint.appendChild(close);
    document.body.appendChild(hint);
  }
});
