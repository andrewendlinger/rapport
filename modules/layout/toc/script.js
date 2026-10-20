/* toc: highlight the section currently in view. */
Rapport.module("toc", {
  mount: function (el) {
    var links = [].slice.call(el.querySelectorAll("a[href^='#']"));
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
    if (!links.length || !("IntersectionObserver" in window)) return;
    var visible = {};
    function update() {
      var current = null;
      for (var i = 0; i < targets.length; i++) {
        if (!targets[i]) continue;
        if (targets[i].getBoundingClientRect().top < window.innerHeight * 0.3) current = i;
      }
      links.forEach(function (a, i) { a.classList.toggle("rp-active", i === current); });
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
      update();
    }, { rootMargin: "0px 0px -60% 0px" });
    targets.forEach(function (t) { if (t) io.observe(t); });
    window.addEventListener("scroll", update, { passive: true });
    update();
  }
});
