/* rapport client kernel. Modules register with Rapport.module(name, def):
     init(ctx)        once, if the module is on the page
     mount(el, ctx)   for every element rendered by the module
     reveal(el, target)  make `target` (inside el) visible: open, switch tab…
   Page data (report info + each scripted module's config) comes from #rp-data. */
(function () {
  "use strict";
  var defs = {}, order = [], listeners = {}, busyChecks = [];
  var data = {};
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
      R.emit("ready");
    }
  };
  window.Rapport = R;
})();
