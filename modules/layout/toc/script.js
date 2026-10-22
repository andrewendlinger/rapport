/* toc: highlight the section currently in view. */
Rapport.module("toc", {
  mount: function (el) {
    var links = [].slice.call(el.querySelectorAll("a[href^='#']"));
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
    if (!links.length) return;
    var queued = false;
    function update() {
      queued = false;
      var current = null;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].getBoundingClientRect().top < window.innerHeight * 0.3) current = i;
      }
      links.forEach(function (a, i) { a.classList.toggle("rp-active", i === current); });
    }
    function soon() { if (!queued) { queued = true; requestAnimationFrame(update); } }
    window.addEventListener("scroll", soon, { passive: true });
    // sections also move without a scroll (a section opens or closes above them)
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(soon, { rootMargin: "0px 0px -60% 0px" });
      targets.forEach(function (t) { if (t) io.observe(t); });
    }
    update();
  }
});
