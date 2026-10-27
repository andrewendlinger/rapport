/* rapport client kernel. Modules register with Rapport.module(name, def):
     init(ctx)        once, if the module is on the page
     mount(el, ctx)   for every element rendered by the module
     reveal(el, target)  make `target` (inside el) visible: open, switch tab…
   Page data (report info + each scripted module's config) comes from #rp-data.
   The whole API, events included, is documented in references/modules.md. */
(function () {
  "use strict";
  var defs = {}, order = [], listeners = {}, busyChecks = [];
  var data = {};
  var SCROLL_KEY = "rp-scroll:" + location.pathname;
  try { data = JSON.parse(document.getElementById("rp-data").textContent); } catch (e) { /* none */ }

  function params(el) {
    try { return JSON.parse(el.getAttribute("data-rp-params") || "{}"); } catch (e) { return {}; }
  }

  var R = {
    data: data,
    module: function (name, def) { if (!defs[name]) order.push(name); defs[name] = def || {}; },
    config: function (name) { return (data.modules || {})[name] || {}; },
    params: params,
    on: function (evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); },
    emit: function (evt, payload) {
      (listeners[evt] || []).slice().forEach(function (fn) {
        try { fn(payload); } catch (e) { console.error("rapport:", evt, e); }
      });
    },
    /* busy(fn) registers a predicate; busy() asks whether any reader work
       (a comment being typed, an active selection) must not be interrupted. */
    busy: function (fn) {
      if (fn) { busyChecks.push(fn); return; }
      return busyChecks.some(function (f) { try { return !!f(); } catch (e) { return false; } });
    },
    /* reload() reloads the page and comes back to the same scroll position.
       "before-reload" listeners run first: save drafts there. */
    reload: function () {
      try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ y: window.scrollY })); } catch (e) { /* ok */ }
      R.emit("before-reload");
      location.reload();
    },
    /* api(module, path, opts) calls a route of the module's server.py (served pages only).
       opts.method defaults to POST when opts.body is given, else GET. Resolves with the
       parsed answer; rejects with an Error carrying the server's message and .status
       (0: no answer). A 403 on a write means the session token went stale: reload. */
    api: function (module, path, opts) {
      opts = opts || {};
      path = path || "/";
      function fail(message, status) { var e = new Error(message); e.status = status; return e; }
      if (!data.serve) {
        return Promise.reject(fail("Rapport.api needs the review server; this page is a static file (run rapport serve)", 0));
      }
      var method = (opts.method || (opts.body === undefined ? "GET" : "POST")).toUpperCase();
      var url = (data.serve.api || "/api") + "/m/" + module + (path.charAt(0) === "/" ? "" : "/") + path;
      return fetch(url, {
        method: method, cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Rapport-Token": data.serve.token || "" },
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
      }).then(function (r) {
        return r.json().catch(function () { return null; }).then(function (res) {
          if (r.ok) return res;
          if (r.status === 403 && method !== "GET") setTimeout(R.reload, 0);
          throw fail((res && res.error) || "HTTP " + r.status, r.status);
        });
      }, function () {
        throw fail("can't reach the review server", 0);
      });
    },
    reveal: function (target) {
      for (var el = target; el && el !== document.body; el = el.parentElement) {
        if (el.tagName === "DETAILS" && !el.open) el.open = true;
        var name = el.getAttribute && el.getAttribute("data-rp-module");
        if (name && defs[name] && defs[name].reveal) defs[name].reveal(el, target);
      }
      return target;
    },
    start: function () {
      order.forEach(function (name) {
        var def = defs[name], ctx = { config: R.config(name), data: data };
        try { if (def.init) def.init(ctx); } catch (e) { console.error("rapport:", name, e); }
        if (def.mount) {
          document.querySelectorAll('[data-rp-module="' + name + '"]').forEach(function (el) {
            try { def.mount(el, { config: ctx.config, params: params(el), data: data }); }
            catch (e) { console.error("rapport:", name, e); }
          });
        }
      });
      try {
        var saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
        if (saved) { sessionStorage.removeItem(SCROLL_KEY); window.scrollTo(0, saved.y || 0); }
      } catch (e) { /* none */ }
      R.emit("ready");
    }
  };
  window.Rapport = R;
})();
