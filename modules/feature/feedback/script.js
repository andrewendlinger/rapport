/* feedback: the reader's half of the loop.
   Select text → a window (or slim bar) to comment or flag it with a quick
   action; hover a block → a margin handle for whole-block comments. Every
   change is saved to the review server immediately. Existing items show as
   highlights (CSS Custom Highlight API, no DOM changes) plus margin counts;
   the panel lists everything. After a round, thin bars in the right margin mark
   what changed; hovering one says what was asked and how it was addressed. Actions, their labels, keys, colours and
   instructions all come from [modules.feedback] config. */
Rapport.module("feedback", {
  init: function (ctx) {
    "use strict";
    var cfg = ctx.config || {};
    var MODE = cfg.on_select === "bar" ? "bar" : "window";
    var AUTOFOCUS = cfg.autofocus !== false;      // cursor goes into the comment box on select
    var ENTER_SAVES = cfg.enter_saves !== false;  // Enter saves; Shift/Cmd/Ctrl+Enter is a new line
    var ACTIONS = (cfg.actions || []).filter(function (a) { return a && a.id && a.enabled !== false; });
    var BY_ID = {};
    ACTIONS.forEach(function (a) { BY_ID[a.id] = a; });
    var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    var HL = !!(window.CSS && CSS.highlights && window.Highlight);
    var SCOPE = document.querySelector(".rp-shell") || document.body;
    var MAIN = document.querySelector(".rp-content") || SCOPE;
    var SERVE = (ctx.data && ctx.data.serve) || {};
    // per report, not per address: a draft must never come back on another report served there
    var DRAFT_KEY = "rp-fb-draft:" + (SERVE.dir || location.pathname);
    var items = [];
    var changes = null;           // since the last feedback round: {since, changed, removed, general}
    var st = { mode: null, target: null, item: null, selected: [], barTarget: null };

    /* ── tiny DOM helpers ─────────────────────────────── */
    function h(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    }
    function ui(e) { e.setAttribute("data-rp-ui", ""); e.classList.add("rp-fb"); return e; }
    function strikes(a) { return a && (a.strike === true || (a.strike === undefined && a.id === "delete")); }
    function label(id) { return (BY_ID[id] && BY_ID[id].label) || id; }
    function keyHint(a) {
      if (!a.key) return "";
      return MODE === "bar" ? a.key.toUpperCase() : (IS_MAC ? "⌥" : "Alt+") + a.key.toUpperCase();
    }

    /* ── text model: normalized text with a map back to DOM points ── */
    function isUI(node) {
      var el = node && (node.nodeType === 1 ? node : node.parentElement);
      return !el || !!el.closest("[data-rp-ui],.rp-fb,script,style,noscript");
    }
    function norm(s) { return (s || "").replace(/\s+/g, " ").trim(); }
    function textIndex(root) {
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) { return isUI(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
      });
      var chars = [], nodes = [], offs = [], lastSpace = true, n;
      while ((n = walker.nextNode())) {
        var t = n.nodeValue;
        for (var i = 0; i < t.length; i++) {
          var c = t.charAt(i);
          if (/\s/.test(c)) { if (lastSpace) continue; c = " "; lastSpace = true; } else lastSpace = false;
          chars.push(c); nodes.push(n); offs.push(i);
        }
      }
      return { text: chars.join(""), nodes: nodes, offs: offs };
    }
    function charIn(range, node, off) {
      try { return range.comparePoint(node, off) === 0 && range.comparePoint(node, off + 1) === 0; }
      catch (e) { return false; }
    }
    function rangeFrom(idx, a, b) {
      var r = document.createRange();
      r.setStart(idx.nodes[a], idx.offs[a]);
      r.setEnd(idx.nodes[b - 1], idx.offs[b - 1] + 1);
      return r;
    }
    /* quote + 48 chars of context on each side, skipping UI chrome */
    function rangeInfo(range) {
      var root = range.commonAncestorContainer;
      if (root.nodeType !== 1) root = root.parentElement;
      var block = root.closest("[data-rp-id]");
      var idx = textIndex(block && block.parentElement ? block.parentElement : root);
      var first = -1, last = -1;
      for (var i = 0; i < idx.nodes.length; i++) {
        if (charIn(range, idx.nodes[i], idx.offs[i])) { if (first < 0) first = i; last = i; }
        else if (first >= 0) break;
      }
      if (first < 0) return null;
      var quote = norm(idx.text.slice(first, last + 1));
      if (!quote) return null;
      return { quote: quote, prefix: idx.text.slice(Math.max(0, first - 48), first),
               suffix: idx.text.slice(last + 1, last + 49) };
    }
    function commonTail(a, b) { var n = 0; while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++; return n; }
    function commonHead(a, b) { var n = 0; while (n < a.length && n < b.length && a[n] === b[n]) n++; return n; }
    function findQuote(root, t) {
      if (!root) return null;
      var idx = textIndex(root), at = findAt(idx, t);
      return at < 0 ? null : rangeFrom(idx, at, at + norm(t.quote).length);
    }
    /* where t.quote is in idx.text, the occurrence whose surroundings best match its context; -1: absent */
    function findAt(idx, t) {
      var q = norm(t.quote);
      if (!q) return -1;
      var best = -1, bestScore = -1, from = 0, pos;
      var pre = norm(t.prefix), suf = norm(t.suffix);
      while ((pos = idx.text.indexOf(q, from)) !== -1) {
        var score = commonTail(idx.text.slice(0, pos).trim(), pre) + commonHead(idx.text.slice(pos + q.length).trim(), suf);
        if (score > bestScore) { bestScore = score; best = pos; }
        from = pos + 1;
      }
      return best;
    }
    /* idx.text[a:b] as one range per text node, so UI chrome in between stays unmarked */
    function nodeRanges(idx, a, b) {
      var out = [];
      for (var i = a; i < b;) {
        var j = i + 1;
        while (j < b && idx.nodes[j] === idx.nodes[i]) j++;
        out.push(rangeFrom(idx, i, j));
        i = j;
      }
      return out;
    }

    /* ── blocks ─────────────────────────────────────────── */
    function byId(id) { return SCOPE.querySelector('[data-rp-id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]'); }
    function blockOf(node) {
      var el = node && (node.nodeType === 1 ? node : node.parentElement);
      el = el && el.closest("[data-rp-id]");
      return el && SCOPE.contains(el) && !el.hasAttribute("data-rp-ui") ? el : null;
    }
    function selects(range, n) {     // does the range cover a visible character of text node n?
      var t = n.nodeValue;
      for (var i = 0; i < t.length; i++) if (/\S/.test(t.charAt(i)) && charIn(range, n, i)) return true;
      return false;
    }
    /* the blocks that own selected text: a text node belongs to its nearest
       block, so a collapse's title belongs to the collapse and its body
       paragraphs to themselves */
    function ownerBlocks(range) {
      var root = range.commonAncestorContainer;
      if (root.nodeType !== 1) root = root.parentElement;
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) { return isUI(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
      });
      var out = [], n;
      while ((n = walker.nextNode())) {
        if (!range.intersectsNode(n)) continue;
        var b = blockOf(n);
        if (b && out.indexOf(b) < 0 && selects(range, n)) out.push(b);
      }
      return out;
    }
    function regionOf(el) {
      var r = el && el.closest("[data-rp-label]");
      return r && SCOPE.contains(r) ? r.getAttribute("data-rp-label") : "";
    }
    function blockLabel(el) {
      var m = el.getAttribute("data-rp-module") || "block";
      var region = regionOf(el);
      return "Whole " + (m === "header" ? "title block" : m) + (region ? " · " + region : "");
    }

    /* ── selection → target ─────────────────────────────── */
    function selectionTarget() {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
      var range = sel.getRangeAt(0);
      var anc = range.commonAncestorContainer;
      if (!SCOPE.contains(anc.nodeType === 1 ? anc : anc.parentElement) || isUI(anc)) return null;
      var blocks = ownerBlocks(range);
      if (!blocks.length) return null;
      var info = rangeInfo(range);
      if (!info) return null;
      return { kind: "text", blocks: blocks.map(function (b) { return b.getAttribute("data-rp-id"); }),
               quote: info.quote, prefix: info.prefix, suffix: info.suffix, range: range.cloneRange(),
               region: regionOf(blocks[0]) };
    }
    function sig(t) { return t ? [t.kind, (t.blocks || []).join(","), t.quote || ""].join("|") : ""; }
    function payloadTarget(t) {
      return { kind: t.kind, blocks: t.blocks || [], quote: t.quote || "", prefix: t.prefix || "",
               suffix: t.suffix || "", region: t.region || "" };
    }

    /* ── server: our routes (server.py), through Rapport.api ── */
    function api(method, path, body) {
      return Rapport.api("feedback", path, { method: method, body: body }).then(function (data) {
        banner(false);
        return data;
      }, function (e) {
        if (e.status === 0) {
          banner(true);
          e.message = "Can't reach the review server. Your text is kept — try again.";
        } else if (e.status === 403) {
          e.message = "reloading";      // the kernel reloads the page: see session:stale below
        }
        throw e;
      });
    }
    function load() {
      return api("GET", "/").then(function (d) {
        items = d.items || [];
        changes = d.changes || null;
        items.forEach(anchor);
        paint();
      }).catch(function (e) { if (e.status && e.status !== 403) toast(e.message); });   // api() shows the offline banner
    }

    /* ── anchoring items to the current page ─────────────── */
    function anchor(it) {
      it._range = null; it._block = null; it._detached = false;
      var t = it.target || {};
      if (t.kind === "report") return;
      var blocks = (t.blocks || []).map(byId).filter(Boolean);
      it._block = blocks[0] || null;
      if (t.kind === "text" && t.quote) {
        var r = null;
        if (blocks.length) r = findQuote(blocks.length === 1 ? blocks[0] : commonParent(blocks), t);
        if (!r) r = findQuote(SCOPE, t);
        it._range = r;
        if (r && !it._block) it._block = blockOf(r.startContainer);
      }
      if (!it._block && !it._range) it._detached = true;
    }
    /* the outermost part hiding this node: a closed <details>, or an element a module hides
       that names the control left visible with aria-labelledby (a tab panel). Marks for what
       it hides go on that control instead (labelOf). */
    function hiddenBy(node) {
      var el = node && (node.nodeType === 1 ? node : node.parentElement), out = null;
      for (var p = el; p && p !== SCOPE; p = p.parentElement) {
        var par = p.parentElement;
        if (par && par.tagName === "DETAILS" && !par.open && p.tagName !== "SUMMARY") out = par;
        else if (p.getAttribute("aria-labelledby") && !p.getClientRects().length && labelOf(p) !== p) out = p;
      }
      return out;
    }
    /* where marks for a hidden part go: the summary; the tab list (one place for every tab in it,
       so marks for two hidden tabs don't stack), else the control itself */
    function labelOf(d) {
      if (d.tagName === "DETAILS") return d.querySelector(":scope > summary") || d;
      var l = document.getElementById(d.getAttribute("aria-labelledby").split(/\s+/)[0]);
      return l ? (l.closest('[role="tablist"]') || l) : d;
    }
    function hiddenWhere(d) {
      return d.tagName === "DETAILS" ? "in this collapsed section" : "in a tab not shown";
    }
    function commonParent(els) {
      var p = els[0];
      while (p && !els.every(function (e) { return p.contains(e); })) p = p.parentElement;
      return p || SCOPE;
    }

    /* ── painting: highlights, margin markers, panel, chip ── */
    var TONES = ["neutral", "accent", "ok", "warn", "violet", "danger", "strike"];
    function styleOf(it) {
      for (var i = 0; i < (it.actions || []).length; i++) if (strikes(BY_ID[it.actions[i]])) return "strike";
      var a = BY_ID[(it.actions || [])[0]];
      return (a && a.tone) || "neutral";
    }
    function paintHighlights() {
      if (!HL) return;
      var groups = {};
      items.forEach(function (it) {
        if (it.status !== "open" || !it._range) return;
        var s = styleOf(it);
        (groups[s] = groups[s] || []).push(it._range);
      });
      TONES.forEach(function (t) {
        var name = "rp-fb-" + t;
        if (groups[t]) { var hl = new Highlight(); groups[t].forEach(function (r) { hl.add(r); }); CSS.highlights.set(name, hl); }
        else CSS.highlights.delete(name);
      });
    }
    var layer = ui(h("div", "rp-fb-layer"));
    document.body.appendChild(layer);
    function placeMarkers() {
      [].slice.call(layer.querySelectorAll(".rp-fb-marker")).forEach(function (m) { m.remove(); });
      var groups = new Map();
      items.forEach(function (it) {
        if (it.status !== "open" || !it._block) return;
        if (!groups.has(it._block)) groups.set(it._block, []);
        groups.get(it._block).push(it);
      });
      // items inside a closed section are counted on its summary line instead
      var shown = new Map();
      groups.forEach(function (list, block) {
        var d = hiddenBy(block);
        var at = d ? labelOf(d) : block;
        if (!shown.has(at)) shown.set(at, { list: [], block: block, hidden: d });
        var g = shown.get(at);
        g.list = g.list.concat(list);
        if (g.block !== block) g.block = null;
      });
      var mainR = MAIN.getBoundingClientRect();
      var vw = document.documentElement.clientWidth;
      shown.forEach(function (g, at) {
        var list = g.list, r = at.getBoundingClientRect();
        if (!r.width && !r.height) return;
        var m = ui(h("button", "rp-fb-marker", String(list.length)));
        m.type = "button";
        m.title = (g.hidden ? hiddenWhere(g.hidden).replace(/^i/, "I") + " · " : "") + (list.length === 1 ?
          (list[0].id + ": " + (list[0].comment || list[0].actions.map(label).join(", "))) : list.length + " items");
        var x = Math.max(mainR.right, r.right) + 12;
        if (x + 30 > vw) x = r.right - 28;
        m.style.left = (x + window.scrollX) + "px";
        m.style.top = (r.top + window.scrollY) + "px";
        m.addEventListener("click", function () {
          if (list.length === 1) focusItem(list[0]);
          else if (g.hidden) { Rapport.reveal(g.hidden); placeSoon(); }
          else openPanel(g.block && g.block.getAttribute("data-rp-id"));
        });
        layer.appendChild(m);
      });
      placeChanges();
    }

    /* ── change bars: what changed since the last feedback round ── */
    var CHANGES_KEY = "rp-fb-changes";
    var showChanges = cfg.changes !== false;
    try { var pref = localStorage.getItem(CHANGES_KEY); if (pref) showChanges = pref === "on"; } catch (e) { /* default */ }
    function itemById(id) {
      for (var i = 0; i < items.length; i++) if (items[i].id === id) return items[i];
      return null;
    }
    function hasChanges() { return !!(changes && ((changes.changed || []).length || (changes.removed || []).length)); }
    function placeChanges() {
      if (!card.hidden) hideCard();       // its mark is about to go: no mouseleave will come
      [].slice.call(layer.querySelectorAll(".rp-fb-change")).forEach(function (m) { m.remove(); });
      if (!showChanges || !hasChanges()) return;
      // a bar runs along an added or edited block (or the summary of the closed section
      // hiding it); a cut sits in the gap where blocks were removed
      var bars = new Map(), cuts = new Map();
      function note(map, el, ids, change, cut) {
        var s = map.get(el);
        if (!s) { s = { ids: [], changes: [], cuts: [], hidden: false }; map.set(el, s); }
        ids.forEach(function (id) { if (s.ids.indexOf(id) < 0) s.ids.push(id); });
        if (change) s.changes.push(change);
        if (cut) s.cuts.push(cut);
        return s;
      }
      changes.changed.forEach(function (c) {
        var b = byId(c.id);
        if (!b) return;
        var d = hiddenBy(b);
        note(bars, d ? labelOf(d) : b, c.items || [], c).hidden = d;
      });
      (changes.removed || []).forEach(function (r) {
        var b = r.before ? byId(r.before) : null, d = b && hiddenBy(b);
        if (d) note(bars, labelOf(d), r.items || [], null, r).hidden = d;
        else note(cuts, b, r.items || [], null, r);
      });
      var mainR = MAIN.getBoundingClientRect();
      var vw = document.documentElement.clientWidth;
      function column(r) { return Math.min(Math.max(mainR.right, r.right) + 6, vw - 8); }
      function mark(s, cls, x, top, height) {
        var m = ui(h("button", "rp-fb-change " + cls + " rp-" + kindOf(s)));
        m.type = "button";
        m.setAttribute("aria-label", cardTitle(s) + (s.ids.length ? " · " + s.ids.join(", ") : ""));
        m.style.left = (x - 6 + window.scrollX) + "px";
        m.style.top = (top + window.scrollY) + "px";
        if (height != null) m.style.height = height + "px";
        m.addEventListener("mouseenter", function (e) { showCard(m, s, e.clientY); });
        m.addEventListener("mouseleave", hideCardSoon);
        m.addEventListener("focus", function () { showCard(m, s, m.getBoundingClientRect().top); });
        m.addEventListener("blur", hideCardSoon);
        m.addEventListener("click", function () { showChangeItems(s); });
        layer.appendChild(m);
      }
      bars.forEach(function (s, el) {
        var r = el.getBoundingClientRect();
        if (r.width || r.height) mark(s, "rp-bar", column(r), r.top, r.height);
      });
      var all = MAIN.querySelectorAll("[data-rp-id]");
      cuts.forEach(function (s, el) {
        var last = all[all.length - 1];
        var r = (el || last || MAIN).getBoundingClientRect();
        if (!r.width && !r.height) return;
        mark(s, "rp-cut", column(r), (el ? r.top - 10 : r.bottom + 4), null);
      });
    }
    function changeItems(s) {
      var ids = s.ids.slice();
      if (!ids.length && !s.cuts.length) ids = (changes.general || []).slice();
      return ids.map(itemById).filter(Boolean);
    }
    /* edited: some words in, some out (blue); added: all new (green); removed: gone (red) */
    function kindOf(s) {
      if (s.changes.some(function (c) { return c.kind === "edited"; })) return "edited";
      return s.changes.length ? "added" : "removed";
    }
    function cardTitle(s) {
      var kind = kindOf(s);
      if (kind === "removed") {
        var n = s.cuts.reduce(function (a, r) { return a + (r.count || 1); }, 0);
        return "Removed" + (n > 1 ? " " + n + " blocks" : "");
      }
      return (kind === "edited" ? "Edited" : "Added") + (s.ids.length ? " since v" + (changes.since || "?") : " in this round") +
        (s.hidden ? ", " + hiddenWhere(s.hidden) : "");
    }
    /* words that are gone: the removed blocks' text, or the runs taken out of an edited block */
    function removedText(s) {
      var runs = s.cuts.map(function (r) { return r.text || r.module || "a block"; });
      s.changes.forEach(function (c) { runs = runs.concat(c.removed || []); });
      return runs.slice(0, 3);
    }
    /* the added words, as ranges on the page: all of an added block, the runs of an edited one */
    function addedRanges(s) {
      var out = [];
      if (s.hidden) return out;
      s.changes.forEach(function (c) {
        var b = byId(c.id);
        if (!b) return;
        var idx = textIndex(b);
        if (c.kind !== "edited") { out = out.concat(nodeRanges(idx, 0, idx.text.length)); return; }
        (c.added || []).forEach(function (run) {
          var at = findAt(idx, run);
          if (at >= 0) out = out.concat(nodeRanges(idx, at, at + norm(run.quote).length));
        });
      });
      return out;
    }
    function markAdded(ranges) {
      if (!HL) return;
      if (!ranges || !ranges.length) { CSS.highlights.delete("rp-fb-added"); return; }
      var hl = new Highlight();
      ranges.forEach(function (r) { hl.add(r); });
      hl.priority = 1;          // over feedback highlights on the same words
      CSS.highlights.set("rp-fb-added", hl);
    }
    var card = ui(h("div", "rp-fb-card"));
    card.hidden = true;
    card.setAttribute("role", "tooltip");
    document.body.appendChild(card);
    var cardTimer = 0, CARD_MAX = 2;
    function hideCard() { clearTimeout(cardTimer); card.hidden = true; markAdded(null); }
    function hideCardSoon() { clearTimeout(cardTimer); cardTimer = setTimeout(hideCard, 200); }
    card.addEventListener("mouseenter", function () { clearTimeout(cardTimer); });
    card.addEventListener("mouseleave", hideCardSoon);
    function showCard(m, s, y) {
      clearTimeout(cardTimer);
      card.textContent = "";
      card.appendChild(h("div", "rp-fb-card-what", cardTitle(s)));
      var gone = removedText(s);
      if (gone.length) {
        var del = h("div", "rp-fb-card-del");
        gone.forEach(function (t, i) {
          if (i) del.appendChild(document.createTextNode(" · "));
          del.appendChild(h("del", null, t));
        });
        card.appendChild(del);
      }
      var list = changeItems(s);
      list.slice(0, CARD_MAX).forEach(function (it) {
        var row = h("div", "rp-fb-card-item");
        row.appendChild(tagRow(it, it.resolution ? "v" + it.resolution.version : ""));
        if ((it.resolution || {}).note) row.appendChild(h("div", "rp-fb-card-note", "✓ " + it.resolution.note));
        var t = it.target || {};
        var was = it.comment || (t.quote && !gone.length ? "“" + norm(t.quote) + "”" : "");   // the struck words already say it
        if (was) row.appendChild(h("div", "rp-fb-card-was", was));
        card.appendChild(row);
      });
      if (list.length > CARD_MAX) {
        var more = h("button", "rp-fb-card-more", "+" + (list.length - CARD_MAX) + " more →");
        more.type = "button";
        more.addEventListener("click", function () { showChangeItems(s); });
        card.appendChild(more);
      }
      card.hidden = false;
      // beside the mark if there's room; else below or above its block, off the words it marks.
      // Never over the column itself: the next mark must stay in reach
      var r = m.getBoundingClientRect(), w = card.offsetWidth, ht = card.offsetHeight;
      var vw = document.documentElement.clientWidth, vh = window.innerHeight;
      var left = r.right + 4, top = Math.min(Math.max(8, y - 16), vh - ht - 8);
      if (left + w > vw - 8) {
        left = Math.max(8, r.left - w - 4);
        if (r.bottom + 6 + ht <= vh - 8) top = r.bottom + 6;
        else if (r.top - 6 - ht >= 8) top = r.top - 6 - ht;
      }
      card.style.left = (left + window.scrollX) + "px";
      card.style.top = (top + window.scrollY) + "px";
      markAdded(addedRanges(s));
    }
    function showChangeItems(s) {
      var list = changeItems(s);
      hideCard();
      if (list.length) openPanel(null, list.map(function (it) { return it.id; }));
    }
    function setShowChanges(on) {
      showChanges = on;
      try { localStorage.setItem(CHANGES_KEY, on ? "on" : "off"); } catch (e) { /* this view only */ }
      if (!on) hideCard();
      paintChangesToggle();
      placeChanges();
    }
    var raf = 0;
    function placeSoon() { cancelAnimationFrame(raf); raf = requestAnimationFrame(placeMarkers); }
    function paint() {
      paintHighlights();
      placeMarkers();
      renderPanel();
      var n = items.filter(function (i) { return i.status === "open"; }).length;
      chipN.textContent = n;
      chip.classList.toggle("rp-hot", n > 0);
      paintChangesToggle();
    }

    /* ── the window ─────────────────────────────────────── */
    var win = ui(h("div", "rp-fb-win"));
    win.hidden = true;
    win.setAttribute("role", "dialog");
    win.setAttribute("aria-label", "Feedback");
    var head = h("div", "rp-fb-head"), what = h("span", "rp-fb-what"), wid = h("span", "rp-fb-id");
    head.appendChild(what); head.appendChild(wid);
    var ta = h("textarea");
    ta.rows = 3;
    ta.placeholder = "Type a comment…";
    var chips = h("div", "rp-fb-chips");
    ACTIONS.forEach(function (a) {
      var b = h("button", "rp-fb-chip rp-tone-" + (a.tone || "neutral"));
      b.type = "button";
      b.setAttribute("data-a", a.id);
      b.setAttribute("aria-pressed", "false");
      b.title = a.instruction ? a.label + ": " + a.instruction : a.label;
      if (a.icon) b.appendChild(h("span", "rp-fb-ico", a.icon));
      b.appendChild(document.createTextNode(a.label || a.id));
      var k = keyHint(a);
      if (k) b.appendChild(h("kbd", null, k));
      b.addEventListener("click", function () { onAction(a.id); });
      chips.appendChild(b);
    });
    var resolvedNote = h("div", "rp-fb-resolved");
    resolvedNote.hidden = true;
    var err = h("div", "rp-fb-err");
    var foot = h("div", "rp-fb-foot"), tip = h("span", "rp-fb-tip");
    var delBtn = h("button", "rp-fb-btn rp-danger", "Delete");
    var cancelBtn = h("button", "rp-fb-btn", "Cancel");
    var saveBtn = h("button", "rp-fb-btn rp-primary", "Save");
    [delBtn, cancelBtn, saveBtn].forEach(function (b) { b.type = "button"; });
    foot.appendChild(tip); foot.appendChild(delBtn); foot.appendChild(cancelBtn); foot.appendChild(saveBtn);
    win.appendChild(head); win.appendChild(ta); win.appendChild(chips); win.appendChild(resolvedNote);
    win.appendChild(err); win.appendChild(foot);
    document.body.appendChild(win);

    function winOpen() { return !win.hidden; }
    function dirty() {
      if (!winOpen()) return false;
      if (st.mode === "edit") {
        return ta.value.trim() !== (st.item.comment || "") || st.selected.join() !== (st.item.actions || []).join();
      }
      return !!ta.value.trim() || st.selected.length > 0;
    }
    function refreshWin() {
      chips.querySelectorAll(".rp-fb-chip").forEach(function (b) {
        b.setAttribute("aria-pressed", st.selected.indexOf(b.getAttribute("data-a")) >= 0 ? "true" : "false");
      });
      var empty = !ta.value.trim() && !st.selected.length;
      var keys = ENTER_SAVES ? "↵ to save · ⇧↵ new line" : (IS_MAC ? "⌘↵" : "Ctrl+↵") + " to save";
      tip.textContent = st.mode === "edit" ? keys :
        (empty ? "Type a comment, or pick an action." : keys + " · Esc to discard");
      saveBtn.disabled = st.mode === "edit" ? !dirty() : empty;
    }
    function showErr(msg) { err.textContent = msg; err.classList.toggle("rp-show", !!msg); }
    function anchorRect(t) {
      if (t && t.range) {
        var rects = t.range.getClientRects();
        if (rects.length) return rects[rects.length - 1];
        return t.range.getBoundingClientRect();
      }
      var b = t && t.el ? t.el : null;
      return b ? b.getBoundingClientRect() : { left: 40, right: 40, top: 80, bottom: 80 };
    }
    function place(el, rect, above) {
      el.hidden = false;
      var w = el.offsetWidth, ht = el.offsetHeight;
      var vw = document.documentElement.clientWidth, vh = window.innerHeight;
      var left = Math.min(Math.max(8, rect.left), vw - w - 8);
      var top = above ? rect.top - ht - 10 : rect.bottom + 10;
      if (!above && top + ht > vh - 8) top = rect.top - ht - 10;
      if (top < 8) top = Math.min(rect.bottom + 10, Math.max(8, vh - ht - 8));
      el.style.left = (left + window.scrollX) + "px";
      el.style.top = (top + window.scrollY) + "px";
    }
    function setPending(range) {
      if (!HL) return;
      if (range) CSS.highlights.set("rp-fb-pending", new Highlight(range));
      else CSS.highlights.delete("rp-fb-pending");
    }
    function openNew(t, focus) {
      hideBar();
      st.mode = "new"; st.target = t; st.item = null; st.selected = [];
      what.textContent = t.kind === "text" ? "“" + t.quote + "”" : (t.kind === "report" ? "Whole report" : t.label || "Whole block");
      wid.textContent = "";
      ta.disabled = false;
      chips.hidden = false;
      resolvedNote.hidden = true;
      delBtn.hidden = true;
      saveBtn.textContent = "Save";
      var d = readDraft();
      ta.value = d && d.sig === sig(t) ? d.comment : "";
      if (d && d.sig === sig(t)) st.selected = (d.selected || []).filter(function (id) { return BY_ID[id]; });
      showErr("");
      refreshWin();
      place(win, anchorRect(t), false);
      setPending(t.range || null);      // keeps the passage marked once focus leaves the page text
      if (focus) {
        ta.focus({ preventScroll: true });
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }
    }
    function openItem(it) {
      hideBar();
      st.mode = "edit"; st.item = it; st.target = it.target; st.selected = (it.actions || []).slice();
      var t = it.target || {};
      what.textContent = t.kind === "text" ? "“" + norm(t.quote) + "”" : (t.kind === "report" ? "Whole report" :
        (it._block ? blockLabel(it._block) : "Whole block"));
      wid.textContent = it.id;
      ta.value = it.comment || "";
      var resolved = it.status !== "open";
      ta.disabled = resolved;
      chips.hidden = resolved;
      resolvedNote.hidden = !resolved;
      resolvedNote.textContent = resolved ? "Resolved in v" + ((it.resolution || {}).version || "?") +
        ((it.resolution || {}).note ? ": " + it.resolution.note : "") : "";
      delBtn.hidden = false;
      saveBtn.textContent = "Save";
      saveBtn.hidden = resolved;
      showErr("");
      refreshWin();
      var rect = it._range ? anchorRect({ range: it._range }) : (it._block ? it._block.getBoundingClientRect() : chip.getBoundingClientRect());
      place(win, rect, false);
      setPending(it._range || null);
      if (AUTOFOCUS && !resolved) {
        ta.focus({ preventScroll: true });
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }
    }
    function closeWin(discard) {
      if (discard) clearDraft(); else saveDraft();
      win.hidden = true;
      saveBtn.hidden = false;
      setPending(null);
      st.mode = null; st.target = null; st.item = null; st.selected = [];
    }
    function onAction(id) {
      var a = BY_ID[id];
      if (!a || st.mode === null) return;
      if (st.mode === "new" && !ta.value.trim() && !st.selected.length && a.comment !== "required") {
        return saveNew(st.target, [id], "");
      }
      var i = st.selected.indexOf(id);
      if (i >= 0) st.selected.splice(i, 1); else st.selected.push(id);
      if (a.comment === "required" && i < 0) ta.focus();
      saveDraft();
      refreshWin();
    }
    function validate(actions, comment) {
      if (!actions.length && !comment) { shake(); return false; }
      var need = actions.filter(function (id) { return BY_ID[id] && BY_ID[id].comment === "required"; });
      if (need.length && !comment) { showErr("“" + label(need[0]) + "” needs a comment."); ta.focus(); return false; }
      return true;
    }
    function shake() { win.classList.remove("rp-fb-shake"); void win.offsetWidth; win.classList.add("rp-fb-shake"); }
    function trySave() { if (saveBtn.disabled) shake(); else saveBtn.click(); }
    function saveNew(t, actions, comment) {
      if (!validate(actions, comment)) return;
      saveBtn.disabled = true;
      api("POST", "/", { target: payloadTarget(t), actions: actions, comment: comment }).then(function (item) {
        clearDraft();
        closeWin(true);
        var sel = window.getSelection();
        if (sel) sel.removeAllRanges();
        var what = actions.length ? actions.map(label).join(" + ") : "Comment";
        if (item.duplicate) toast("Already flagged · " + item.id);
        else toast(what + " saved · " + item.id, function () { api("DELETE", "/" + item.id).then(load); });
        load();
      }).catch(function (e) { showErr(e.message); saveDraft(); refreshWin(); });
    }
    function saveEdit() {
      var comment = ta.value.trim();
      if (!validate(st.selected, comment)) return;
      var it = st.item;
      api("PATCH", "/" + it.id, { actions: st.selected, comment: comment }).then(function () {
        closeWin(true);
        toast(it.id + " updated");
        load();
      }).catch(function (e) { showErr(e.message); });
    }
    saveBtn.addEventListener("click", function () {
      if (st.mode === "edit") saveEdit(); else saveNew(st.target, st.selected.slice(), ta.value.trim());
    });
    /* Esc / Cancel: close and drop the text, with Undo, so a dismissed draft
       never pops back up on the next reload */
    function dismiss() {
      if (!dirty()) { closeWin(true); return; }
      var snap = { mode: st.mode, target: st.target, item: st.item, comment: ta.value, selected: st.selected.slice() };
      closeWin(true);
      toast("Draft discarded", function () {
        if (snap.mode === "edit") openItem(snap.item); else openNew(snap.target, false);
        ta.value = snap.comment;
        st.selected = snap.selected;
        ta.focus();
        saveDraft();
        refreshWin();
      });
    }
    cancelBtn.addEventListener("click", dismiss);
    delBtn.addEventListener("click", function () {
      var it = st.item;
      if (!it) return;
      api("DELETE", "/" + it.id).then(function () { closeWin(true); toast(it.id + " deleted"); load(); })
        .catch(function (e) { showErr(e.message); });
    });
    ta.addEventListener("input", function () { showErr(""); saveDraft(); refreshWin(); });

    /* ── drafts survive reloads ─────────────────────────── */
    function saveDraft() {
      if (!st.mode || st.mode !== "new") return;
      try {
        if (!ta.value.trim() && !st.selected.length) { sessionStorage.removeItem(DRAFT_KEY); return; }
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ sig: sig(st.target), target: payloadTarget(st.target),
          label: st.target.label || "", comment: ta.value, selected: st.selected }));
      } catch (e) { /* storage blocked */ }
    }
    function readDraft() { try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null"); } catch (e) { return null; } }
    function clearDraft() { try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) { /* ok */ } }
    function restoreDraft() {
      var d = readDraft();
      if (!d || !d.target) return;
      var t = d.target;
      var probe = { target: t };
      anchor(probe);
      if (t.kind === "text" && !probe._range) return;
      var target = { kind: t.kind, blocks: t.blocks, quote: t.quote, prefix: t.prefix, suffix: t.suffix,
                     range: probe._range, el: probe._block, label: d.label, region: t.region };
      openNew(target, true);
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }

    /* ── the slim bar (on_select = "bar") ───────────────── */
    var bar = ui(h("div", "rp-fb-bar"));
    bar.hidden = true;
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Feedback actions");
    ACTIONS.forEach(function (a) {
      var b = h("button", "rp-tone-" + (a.tone || "neutral"), a.icon || a.label.charAt(0));
      b.type = "button";
      b.title = a.label + (a.key ? " (" + a.key.toUpperCase() + ")" : "");
      b.setAttribute("aria-label", a.label);
      b.addEventListener("click", function () { barAction(a.id); });
      bar.appendChild(b);
    });
    bar.appendChild(h("span", "rp-fb-sep"));
    var cBtn = h("button", null, "💬");
    cBtn.type = "button";
    cBtn.title = "Comment (C)";
    cBtn.setAttribute("aria-label", "Comment");
    cBtn.addEventListener("click", function () { expandBar(); });
    bar.appendChild(cBtn);
    document.body.appendChild(bar);
    function barOpen() { return !bar.hidden; }
    function showBar(t) {
      st.barTarget = t;
      var rects = t.range.getClientRects();
      place(bar, rects.length ? rects[0] : t.range.getBoundingClientRect(), true);
    }
    function hideBar() { bar.hidden = true; st.barTarget = null; }
    function barAction(id) {
      var t = st.barTarget, a = BY_ID[id];
      if (!t || !a) return;
      if (a.comment === "required") { openNew(t, true); st.selected = [id]; refreshWin(); return; }
      hideBar();
      st.target = t;
      saveNew(t, [id], "");
    }
    function expandBar() { var t = st.barTarget; if (t) openNew(t, true); }

    /* ── block handle (whole-block comments) ────────────── */
    var handle = ui(h("button", "rp-fb-handle", "+"));
    handle.type = "button";
    handle.title = "Comment on this block";
    handle.setAttribute("aria-label", "Comment on this block");
    document.body.appendChild(handle);
    var hoverBlock = null, hideTimer = 0;
    function showHandle(block) {
      if (hoverBlock && hoverBlock !== block) hoverBlock.classList.remove("rp-fb-hover-block");
      hoverBlock = block;
      var r = block.getBoundingClientRect(), mainR = MAIN.getBoundingClientRect();
      var x = Math.min(r.left, mainR.left) - 34;
      if (x < 4) x = r.left - 30 < 4 ? 4 : r.left - 30;
      handle.style.left = (x + window.scrollX) + "px";
      handle.style.top = (r.top + window.scrollY + 2) + "px";
      handle.classList.add("rp-show");
      block.classList.add("rp-fb-hover-block");
    }
    function hideHandle() {
      handle.classList.remove("rp-show");
      if (hoverBlock) hoverBlock.classList.remove("rp-fb-hover-block");
      hoverBlock = null;
    }
    if (cfg.block_handles !== false) {
      document.addEventListener("mousemove", function (e) {
        if (e.target === handle) { clearTimeout(hideTimer); return; }
        if (isUI(e.target) && !e.target.closest(".rp-shell")) return;
        var b = blockOf(e.target);
        if (b) { clearTimeout(hideTimer); if (b !== hoverBlock) showHandle(b); }
        else if (hoverBlock) { clearTimeout(hideTimer); hideTimer = setTimeout(hideHandle, 450); }
      }, { passive: true });
      handle.addEventListener("click", function () {
        var b = hoverBlock;
        if (!b) return;
        if (st.mode && dirty()) { shake(); return; }
        openNew({ kind: "block", blocks: [b.getAttribute("data-rp-id")], el: b, label: blockLabel(b),
                  region: regionOf(b) }, true);
      });
    }

    /* ── selection, keys, clicks ────────────────────────── */
    function insideChrome(node) { return !!(node && node.closest && node.closest(".rp-fb")); }
    function onSelectionDone(byMouse) {
      var t = selectionTarget();
      if (!t) {
        // focus moving into the box collapses the page selection: that is not "selected nothing"
        // (a fast double-click runs this twice, the second time after the box took focus)
        if (insideChrome(document.activeElement)) return;
        if (barOpen()) hideBar();
        if (winOpen() && st.mode === "new" && !dirty() && st.target && st.target.kind === "text") closeWin(false);
        return;
      }
      if (winOpen() && dirty()) return;     // never clobber a comment in progress
      // a keyboard selection (Shift+arrows) may still grow, so focus stays on the page
      if (MODE === "bar") showBar(t); else openNew(t, AUTOFOCUS && byMouse);
    }
    document.addEventListener("mouseup", function (e) {
      if (insideChrome(e.target)) return;
      setTimeout(function () { onSelectionDone(true); }, 0);
    });
    document.addEventListener("keyup", function (e) {
      if (e.key === "Shift" || (e.shiftKey && /Arrow|Home|End|Page/.test(e.key))) {
        if (!insideChrome(document.activeElement)) onSelectionDone(false);
      }
    });
    document.addEventListener("mousedown", function (e) {
      if (insideChrome(e.target)) return;
      if (winOpen() && !dirty()) closeWin(false);
      if (barOpen()) hideBar();
    });
    /* the letter the reader pressed: e.key when it is one (layout-correct),
       else the physical key (macOS Option turns S into ß) */
    function keyOf(e) {
      var k = e.key || "";
      if (k.length === 1 && /[a-z0-9]/i.test(k)) return k.toLowerCase();
      var m = /^(?:Key([A-Z])|Digit([0-9]))$/.exec(e.code || "");
      return m ? (m[1] || m[2]).toLowerCase() : "";
    }
    var COMMENT_KEY = "c";
    function actionForEvent(e) {
      var k = keyOf(e);
      if (!k) return null;
      for (var i = 0; i < ACTIONS.length; i++) {
        if ((ACTIONS[i].key || "").toLowerCase() === k) return ACTIONS[i];
      }
      return null;
    }
    function insertText(el, text) {
      var s = el.selectionStart, e = el.selectionEnd;
      el.value = el.value.slice(0, s) + text + el.value.slice(e);
      el.selectionStart = el.selectionEnd = s + text.length;
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (winOpen()) { dismiss(); e.preventDefault(); }
        else if (barOpen()) hideBar();
        else if (!panel.hidden) closePanel();
        return;
      }
      var inField = document.activeElement && /^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement.tagName)
        && !win.contains(document.activeElement);
      if (inField) return;
      if (winOpen()) {
        var inBox = document.activeElement === ta;
        if (e.key === "Enter" && inBox && !e.isComposing && e.keyCode !== 229) {   // 229: IME still composing
          var mod = e.metaKey || e.ctrlKey;
          if (ENTER_SAVES ? (e.shiftKey || e.altKey || mod) : !mod) {
            if (mod) { e.preventDefault(); insertText(ta, "\n"); saveDraft(); refreshWin(); }
            return;                       // Shift/Option+Enter: the browser adds the new line
          }
          e.preventDefault();
          trySave();
          return;
        }
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !inBox) { e.preventDefault(); trySave(); return; }
        // Cmd/Ctrl+C with nothing selected in the box copies the marked passage, as if focus never moved
        if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && keyOf(e) === "c" && inBox
            && ta.selectionStart === ta.selectionEnd && st.target && st.target.range && navigator.clipboard) {
          e.preventDefault();
          navigator.clipboard.writeText(st.target.range.toString()).then(function () { toast("Copied"); },
            function () { /* clipboard refused */ });
          return;
        }
        // Option/Alt+key is an action shortcut until you've typed; then it types (umlauts, ś…)
        if (e.altKey && !e.metaKey && !e.ctrlKey && st.mode && !ta.disabled && (!inBox || !ta.value)) {
          var a = actionForEvent(e);
          if (a) { e.preventDefault(); onAction(a.id); }
          return;
        }
        if (document.activeElement !== ta && !ta.disabled && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
          e.preventDefault();
          ta.focus();
          insertText(ta, e.key);
          showErr(""); saveDraft(); refreshWin();
        }
      } else if (barOpen()) {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (keyOf(e) === COMMENT_KEY) { e.preventDefault(); expandBar(); return; }
        var b = actionForEvent(e);
        if (b) { e.preventDefault(); barAction(b.id); }
      }
    });
    function itemAt(x, y) {
      var hits = items.filter(function (it) {
        if (!it._range || hiddenBy(it._range.startContainer)) return false;
        return [].some.call(it._range.getClientRects(), function (r) {
          return x >= r.left - 1 && x <= r.right + 1 && y >= r.top - 1 && y <= r.bottom + 1;
        });
      });
      hits.sort(function (a, b) { return (a.status === "open" ? 0 : 1) - (b.status === "open" ? 0 : 1); });
      return hits[0] || null;
    }
    document.addEventListener("click", function (e) {
      if (insideChrome(e.target) || e.target.closest("a,button,summary,input,textarea,select,label")) return;
      var sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      if (winOpen() && dirty()) return;
      var it = itemAt(e.clientX, e.clientY);
      if (it && it.status === "open") openItem(it);
    });

    /* ── panel ──────────────────────────────────────────── */
    var dock = ui(h("div", "rp-fb-dock"));
    var chgBtn = h("button", "rp-fb-chgbtn");
    chgBtn.type = "button";
    chgBtn.hidden = true;
    chgBtn.addEventListener("click", function () { setShowChanges(!showChanges); });
    var chip = h("button", "rp-fb-chipbtn");
    chip.type = "button";
    chip.setAttribute("aria-expanded", "false");
    chip.appendChild(document.createTextNode("Feedback "));
    var chipN = h("span", "rp-n", "0");
    chip.appendChild(chipN);
    dock.appendChild(chgBtn); dock.appendChild(chip);
    document.body.appendChild(dock);
    function paintChangesToggle() {
      chgBtn.hidden = !hasChanges();
      chgBtn.setAttribute("aria-pressed", showChanges ? "true" : "false");
      var what = "changes since v" + ((changes && changes.since) || "?");
      chgBtn.title = (showChanges ? "Hide " : "Show ") + what;
      chgBtn.setAttribute("aria-label", chgBtn.title);
    }
    var panel = ui(h("aside", "rp-fb-panel"));
    panel.hidden = true;
    panel.setAttribute("aria-label", "Feedback");
    var ph = h("div", "rp-fb-ph", "Feedback");
    var px = h("button", "rp-x", "✕");
    px.type = "button";
    px.setAttribute("aria-label", "Close");
    ph.appendChild(px);
    var filters = h("div", "rp-fb-filters");
    var list = h("div", "rp-fb-list");
    var general = h("div", "rp-fb-general");
    var gta = h("textarea");
    gta.rows = 1;
    gta.placeholder = "Comment on the whole report…";
    var gbtn = h("button", "rp-fb-btn rp-primary", "Add");
    gbtn.type = "button";
    general.appendChild(gta); general.appendChild(gbtn);
    var hint = h("div", "rp-fb-hint", cfg.panel_hint || "");
    panel.appendChild(ph); panel.appendChild(filters); panel.appendChild(list); panel.appendChild(general);
    if (cfg.panel_hint) panel.appendChild(hint);
    document.body.appendChild(panel);
    var view = { status: "open", action: null, block: null, ids: null };
    function filterChip(text, active, onClick, tone) {
      var b = h("button", "rp-fb-chip" + (tone ? " rp-tone-" + tone : ""), text);
      b.type = "button";
      b.setAttribute("aria-pressed", active ? "true" : "false");
      b.addEventListener("click", onClick);
      return b;
    }
    function renderPanel() {
      filters.textContent = "";
      [["open", "Open"], ["resolved", "Resolved"], ["all", "All"]].forEach(function (s) {
        filters.appendChild(filterChip(s[1], view.status === s[0], function () { view.status = s[0]; renderPanel(); }));
      });
      ACTIONS.forEach(function (a) {
        if (!items.some(function (it) { return (it.actions || []).indexOf(a.id) >= 0; })) return;
        filters.appendChild(filterChip(a.label, view.action === a.id, function () {
          view.action = view.action === a.id ? null : a.id; renderPanel();
        }, a.tone));
      });
      if (view.block) filters.appendChild(filterChip("This block ✕", true, function () { view.block = null; renderPanel(); }));
      if (view.ids) filters.appendChild(filterChip("These changes ✕", true, function () { view.ids = null; renderPanel(); }));
      var shown = items.filter(function (it) {
        if (view.status !== "all" && (it.status === "open") !== (view.status === "open")) return false;
        if (view.action && (it.actions || []).indexOf(view.action) < 0) return false;
        if (view.block && (it.target.blocks || []).indexOf(view.block) < 0) return false;
        if (view.ids && view.ids.indexOf(it.id) < 0) return false;
        return true;
      });
      shown.sort(function (a, b) {
        var la = a.target.kind === "report" ? 1e9 : ((a.target.lines || [0])[0] || 0);
        var lb = b.target.kind === "report" ? 1e9 : ((b.target.lines || [0])[0] || 0);
        return la - lb;
      });
      list.textContent = "";
      if (!shown.length) {
        list.appendChild(h("div", "rp-fb-empty", items.length ? "Nothing here with these filters." :
          "No feedback yet. Select any passage to comment or flag it."));
      }
      var lastGroup = null;
      shown.forEach(function (it) {
        var t = it.target || {};
        var group = t.kind === "report" ? "Whole report" : (t.heading_path && t.heading_path[0]) || (t.module === "header" ? "Title" : "Top");
        if (group !== lastGroup) { list.appendChild(h("div", "rp-fb-group", group)); lastGroup = group; }
        var row = h("div", "rp-fb-item" + (it.status === "open" ? "" : " rp-res"));
        row.appendChild(tagRow(it));
        if (t.quote) row.appendChild(h("div", "rp-fb-q", "“" + norm(t.quote) + "”"));
        else if (t.kind === "block") row.appendChild(h("div", "rp-fb-q", it._block ? blockLabel(it._block) : "Whole block"));
        if (it.comment) row.appendChild(h("div", "rp-fb-c", it.comment));
        if (it.status !== "open" && it.resolution) {
          row.appendChild(h("div", "rp-fb-r", "✓ v" + it.resolution.version + (it.resolution.note ? " — " + it.resolution.note : "")));
        } else if (it._detached || (t.kind === "text" && !it._range)) {
          row.appendChild(h("div", "rp-fb-detached", "Passage changed since — can't point at it any more."));
        }
        row.addEventListener("click", function () { focusItem(it); });
        list.appendChild(row);
      });
    }
    function tagRow(it, extra) {
      var top = h("div", "rp-fb-row");
      (it.actions && it.actions.length ? it.actions : [null]).forEach(function (id) {
        var a = id && BY_ID[id];
        top.appendChild(h("span", "rp-fb-tag" + (a ? " rp-tone-" + (a.tone || "neutral") : ""), id ? label(id) : "Comment"));
      });
      top.appendChild(h("span", "rp-fb-iid", it.id + (extra ? " · " + extra : "")));
      return top;
    }
    function openPanel(blockId, ids) {
      view.block = blockId || null;
      view.ids = ids || null;
      if (blockId) view.status = "open";
      if (ids) view.status = "all";
      renderPanel();
      panel.hidden = false;
      chip.setAttribute("aria-expanded", "true");
    }
    function closePanel() { panel.hidden = true; chip.setAttribute("aria-expanded", "false"); }
    chip.addEventListener("click", function () { if (panel.hidden) openPanel(); else closePanel(); });
    px.addEventListener("click", closePanel);
    var GENERAL_KEY = DRAFT_KEY + ":general";
    function saveGeneral() {
      try {
        if (gta.value.trim()) sessionStorage.setItem(GENERAL_KEY, gta.value);
        else sessionStorage.removeItem(GENERAL_KEY);
      } catch (e) { /* storage blocked */ }
    }
    gta.addEventListener("input", saveGeneral);
    gbtn.addEventListener("click", function () {
      var c = gta.value.trim();
      if (!c) { gta.focus(); return; }
      api("POST", "/", { target: { kind: "report" }, actions: [], comment: c }).then(function (item) {
        gta.value = "";
        saveGeneral();
        toast("Comment saved · " + item.id);
        load();
      }).catch(function (e) { toast(e.message); });
    });
    function restoreGeneral() {
      var v = null;
      try { v = sessionStorage.getItem(GENERAL_KEY); } catch (e) { /* none */ }
      if (!v) return;
      gta.value = v;
      openPanel();
    }
    function focusItem(it) {
      var el = it._range ? it._range.startContainer : it._block;
      if (!el) { openItem(it); return; }
      el = el.nodeType === 1 ? el : el.parentElement;
      Rapport.reveal(el);
      anchor(it);
      (it._block || el).scrollIntoView({ behavior: "smooth", block: "center" });
      flash(it);
      setTimeout(function () { openItem(it); }, 420);
    }
    function flash(it) {
      if (HL && it._range) {
        CSS.highlights.set("rp-fb-flash", new Highlight(it._range));
        setTimeout(function () { CSS.highlights.delete("rp-fb-flash"); }, 1300);
      } else if (it._block) {
        it._block.classList.add("rp-fb-flash-block");
        setTimeout(function () { it._block.classList.remove("rp-fb-flash-block"); }, 1300);
      }
    }

    /* ── toast + banner ─────────────────────────────────── */
    var toastEl = ui(h("div", "rp-fb-toast"));
    toastEl.setAttribute("role", "status");
    document.body.appendChild(toastEl);
    var toastTimer = 0;
    function toast(msg, undo) {
      toastEl.textContent = "";
      toastEl.appendChild(h("span", null, msg));
      if (undo) {
        var u = h("button", null, "Undo");
        u.type = "button";
        u.addEventListener("click", function () { toastEl.classList.remove("rp-show"); undo(); });
        toastEl.appendChild(u);
      }
      toastEl.classList.add("rp-show");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { toastEl.classList.remove("rp-show"); }, undo ? 6000 : 2600);
    }
    var bannerEl = ui(h("div", "rp-fb-banner", "Can't reach the review server for this report, so comments can't be " +
      "saved right now; your draft is kept. The server stops by itself when unused for a while." +
      (SERVE.dir ? " To start it again: rapport serve '" + SERVE.dir + "'" : "")));
    bannerEl.hidden = true;
    document.body.appendChild(bannerEl);
    function banner(on) { bannerEl.hidden = !on; }

    /* ── wiring ─────────────────────────────────────────── */
    Rapport.busy(function () {
      if (winOpen() || barOpen()) return true;
      if (gta.value.trim() || document.activeElement === gta) return true;   // writing a general comment
      var sel = window.getSelection();
      return !!(sel && !sel.isCollapsed && sel.rangeCount && SCOPE.contains(sel.getRangeAt(0).commonAncestorContainer));
    });
    Rapport.on("feedback:changed", load);
    Rapport.on("session:stale", function () { toast("Session changed — reloading…"); });
    Rapport.on("server:down", function () { banner(true); });
    Rapport.on("server:up", function () { banner(false); });
    Rapport.on("before-reload", function () { saveDraft(); saveGeneral(); });
    window.addEventListener("resize", placeSoon);
    document.addEventListener("toggle", placeSoon, true);
    if (window.ResizeObserver) new ResizeObserver(placeSoon).observe(MAIN);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeSoon);
    load().then(function () { restoreGeneral(); restoreDraft(); });
  }
});
