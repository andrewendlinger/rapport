"""Export a report as a Confluence page body (the HTML dialect Atlassian's HTML → ADF
converter accepts, e.g. through the Atlassian MCP tools).

Every module may ship a `confluence.html` template next to `template.html`; the
ConfluenceRenderer renders the report through those. Templates get `cf`, a helper
for Confluence's markup (macros, apps, palettes), plus the usual node/ctx/cfg.
A module whose template is missing is reported, not silently dropped.

Apps (Macro Pack, draw.io) are optional: `[confluence] apps` lists the ones the
target site has, and templates fall back to plain Confluence elements without them.
rapport never talks to Confluence itself: publishing is the agent's job.
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from dataclasses import dataclass, field

from markupsafe import Markup

from . import dom, palette
from .parse import Diag
from .registry import Module, resolve_params
from .render import Renderer, View, Lazy

MACRO_PACK = "1ef074bf-c90d-4af8-9ea9-32d2e6ae9a90/2256cafd-362d-4b27-a796-139875a465b5/static/macro-pack"
DRAWIO = "1afdce52-d22e-4d27-84db-5be989c3c83b/92d899e8-452f-4c9b-9f60-823ab11c8253/static/drawio"
INC_DRAWIO = "1afdce52-d22e-4d27-84db-5be989c3c83b/92d899e8-452f-4c9b-9f60-823ab11c8253/static/inc-drawio"
CORE = "com.atlassian.confluence.macro.core"
FETCHED_HIGHLIGHT = re.compile(r'<span style="color: (#[0-9a-fA-F]{6});?"><span style="background-color: (#[0-9a-fA-F]{6});?">'
                               r"((?:(?!<span).)*?)</span></span>", re.S)
ECOSYSTEM = "com.atlassian.ecosystem"

PANEL = {"info": "info", "note": "note", "success": "success", "tip": "success",
         "warning": "warning", "error": "error", "danger": "error"}
BADGE_STATUS = {"neutral": "neutral", "accent": "blue", "ok": "green", "warn": "yellow",
                "danger": "red", "violet": "purple"}
# a few common panel icons Confluence knows by shortname; others go by code point
EMOJI_SHORTNAMES = {"💡": ":bulb:", "🧲": ":magnet:", "🚀": ":rocket:", "⚠️": ":warning:", "✅": ":white_check_mark:",
                    "📌": ":pushpin:", "📝": ":memo:", "🔥": ":fire:", "❗": ":exclamation:", "❓": ":question:",
                    "ℹ️": ":information_source:", "🧠": ":brain:", "🎯": ":dart:", "🛠️": ":hammer_and_wrench:",
                    "📎": ":paperclip:", "🔒": ":lock:", "⭐": ":star:", "👉": ":point_right:", "🧪": ":test_tube:",
                    "📈": ":chart_with_upwards_trend:", "🐛": ":bug:", "✨": ":sparkles:", "🗒️": ":spiral_note_pad:"}


def attr_json(obj) -> str:
    """JSON for a single-quoted data-parameters attribute (only & < > ' escaped)."""
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")


def emoji_shortname(ch: str) -> str:
    return EMOJI_SHORTNAMES.get(ch) or ":" + "-".join(f"{ord(c):x}" for c in ch if ord(c) != 0xFE0F) + ":"


@dataclass
class ExportResult:
    title: str
    body: str
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    handovers: list = field(default_factory=list)     # steps the user has to do in Confluence

    @property
    def ok(self) -> bool:
        return not self.errors



def _num(v):
    """772.0 → 772: Confluence stores whole numbers as integers."""
    return int(v) if float(v).is_integer() else float(v)

class Cf:
    """`cf` in confluence.html templates."""

    MACRO_PACK, DRAWIO, INC_DRAWIO = MACRO_PACK, DRAWIO, INC_DRAWIO

    def __init__(self, r: "ConfluenceRenderer"):
        self._r = r

    # -- target site -------------------------------------------------------- #
    def has(self, app: str) -> bool:
        return app in (self._r.config.get("confluence.apps", []) or [])

    def setting(self, key: str, default=None):
        return self._r.config.get(f"confluence.{key}", default)

    def warn(self, message: str) -> str:
        self._r.warnings.append(Diag(self._r._line, message))
        return ""

    def handover(self, message: str) -> str:
        self._r.handovers.append(message)
        return ""

    # -- markup -------------------------------------------------------------- #
    def color(self, pal: str, name: str) -> str:
        return palette.stored(pal, name)

    def extension(self, key: str, params: dict, kind: str = "extension", ext_type: str = CORE,
                  body: str = "", label: str = "", layout: str | None = None) -> Markup:
        tag = "span" if kind == "inline-extension" else "div"
        lay = f' data-layout="{layout}"' if layout else ""
        inner = body if kind in ("bodied-extension", "multi-bodied-extension") else html.escape(label)
        return Markup(f'<{tag} data-type="{kind}" data-extension-key="{key}" data-extension-type="{ext_type}"{lay} '
                      f"data-parameters='{attr_json(params)}'>{inner}</{tag}>")

    def macro(self, key: str, title: str, params: dict | None = None, kind: str = "extension", body: str = "",
              label: str = "") -> Markup:
        """A native Confluence macro (toc, details, …)."""
        p = {"macroParams": {k: {"value": str(v)} for k, v in (params or {}).items()},
             "macroMetadata": {"schemaVersion": {"value": "1"}, "title": title}}
        return self.extension(key, p, kind=kind, body=body, label=label)

    def macro_pack(self, input: str, text: str, options: dict | None = None, layout: str | None = None) -> Markup:
        gp = {"input": input, "source": {"text": text, "type": "text"}, "version": 1}
        if options:
            gp["options"] = options
        params = {"layout": "extension", "guestParams": gp, "forgeEnvironment": "PRODUCTION",
                  "extensionId": f"ari:cloud:ecosystem::extension/{MACRO_PACK}", "extensionTitle": "Macro Pack"}
        return self.extension(MACRO_PACK, params, ext_type=ECOSYSTEM, label="Macro Pack", layout=layout)

    def page_id(self) -> str:
        """The Confluence page this report was published to (from `confluence record`), or ""."""
        meta = self._r.report_dir / ".rapport" / "confluence" / "page.json"
        try:
            return str(json.loads(meta.read_text(encoding="utf-8")).get("page_id") or "")
        except (OSError, ValueError):
            return ""

    def drawio_macro(self, page: str, name: str, revision=None, width=None, height=None) -> Markup:
        """A native draw.io diagram stored on this page (the user inserted it)."""
        gp = {"diagramName": name, "diagramDisplayName": name, "pageId": str(page), "zoom": 100, "lbox": True,
              "links": "auto", "tbstyle": "top"}
        if revision:
            gp["revision"] = _num(revision)
        if width and height:
            gp.update(width=_num(width), height=_num(height))
        params = {"layout": "extension", "guestParams": gp, "forgeEnvironment": "PRODUCTION",
                  "extensionId": f"ari:cloud:ecosystem::extension/{DRAWIO}", "extensionTitle": "draw.io Diagram"}
        return self.extension(DRAWIO, params, ext_type=ECOSYSTEM, label="draw.io Diagram")

    def drawio_embed(self, page: str, name: str, width=None, height=None) -> Markup:
        gp = {"simple": False, "zoom": 100, "pageId": str(page), "imgPageId": str(page), "diagramName": name,
              "diagramDisplayName": name, "lbox": True, "pCenter": False, "includedDiagram": True,
              "links": "auto", "tbstyle": "top"}
        if width and height:
            gp.update(width=_num(width), height=_num(height))
        params = {"layout": "extension", "guestParams": gp, "forgeEnvironment": "PRODUCTION",
                  "extensionId": f"ari:cloud:ecosystem::extension/{INC_DRAWIO}",
                  "extensionTitle": "Embed draw.io Diagram"}
        return self.extension(INC_DRAWIO, params, ext_type=ECOSYSTEM, label="Embed draw.io Diagram")

    def emoji(self, ch: str) -> str:
        return emoji_shortname(ch)

    def panel(self, tone: str) -> str:
        return PANEL.get(tone, "info")

    def badge(self, tone: str) -> str:
        return BADGE_STATUS.get(tone, "neutral")

    def check_panel(self, body) -> str:
        """Confluence panels can't hold tables, expands, quotes or other panels."""
        s = str(body)
        for needle, what in (('<table', 'a table'), ('<details', 'an expand'), ('<blockquote', 'a quote'),
                             ('data-type="panel-', 'another panel')):
            if needle in s:
                self.warn(f"callout: Confluence panels can't hold {what}; the page may reject it")
        return ""

    def tabs(self, children) -> Markup:
        tabs = [{"id": f"tab{i + 1}", "title": str(c.params.get("title", ""))} for i, c in enumerate(children)]
        frames = "".join(f'<div data-type="extension-frame">{c.html}</div>' for c in children)
        return self.extension("native-tabs", {"tabs": tabs}, kind="multi-bodied-extension",
                              ext_type="com.atlassian.confluence.native", body=frames)

    def strip_styles(self, fragment) -> Markup:
        return Markup(re.sub(r'\s+style="[^"]*"', "", str(fragment)))

    def footnotes(self, fragment) -> Markup:
        """Confluence has no footnotes: a rule and a numbered list of the notes."""
        root = dom.parse(str(fragment))
        items = []
        for li in root.find_all(lambda e: e.tag == "li" and "footnote-item" in e.get("class", "")):
            for a in li.find_all(lambda e: e.tag == "a" and "footnote-backref" in e.get("class", "")):
                a.parent.children.remove(a)
            inner = "".join(dom.serialize(c) for c in li.children).strip()
            inner = re.sub(r"^<p>(.*)</p>$", r"\1", inner, flags=re.S).strip()
            items.append(f"<li>{inner}</li>")
        self.warn("footnotes: Confluence has none; they become a numbered list at the end")
        return Markup("<hr><ol>" + "".join(items) + "</ol>")

    def esc(self, s) -> Markup:
        return Markup(html.escape(str(s), quote=True))

    # -- transforms of markdown-it HTML ------------------------------------------ #
    def tasks(self, fragment) -> Markup:
        """markdown-it task lists → Confluence task lists (a nested list follows its item)."""
        root = dom.parse(str(fragment))
        for ul in root.find_all(lambda e: e.tag == "ul" and "contains-task-list" in e.get("class", "")):
            ul.attrs = {"data-type": "task-list"}
            items = []
            for li in ul.elements:
                if li.tag != "li":
                    continue
                box = li.find(lambda e: e.tag == "input")
                checked = box is not None and "checked" in box.attrs
                nested = [c for c in li.elements if c.tag == "ul"]
                li.children = [c for c in li.children if c is not box and c not in nested]
                if li.children and isinstance(li.children[0], str):
                    li.children[0] = li.children[0].lstrip()
                while li.children and isinstance(li.children[-1], str):
                    li.children[-1] = li.children[-1].rstrip()
                    if li.children[-1]:
                        break
                    li.children.pop()
                li.attrs = {"data-type": "task-item"}
                li.children.insert(0, dom.El("input", {"type": "checkbox", **({"checked": ""} if checked else {})}))
                li.children.insert(1, " ")
                items.append(li)
                items += nested
            ul.children = []
            for it in items:
                ul.append(it)
        return Markup(dom.serialize(root))

    def cards(self, fragment) -> Markup:
        """Autolinks (<https://…>: link text = URL) become inline smart links."""
        def repl(m):
            href, text = m.group(1), m.group(2)
            if html.unescape(text) != html.unescape(href):
                return m.group(0)
            return f'<a href="{href}" data-card-appearance="inline"></a>'
        return Markup(re.sub(r'<a href="([^"]+)">([^<]*)</a>', repl, str(fragment)))

    def fix_fetched(self, fragment) -> Markup:
        """HTML copied from a fetched page, made safe to send back: fetched HTML nests a
        highlight as colour-outside/background-inside, which the converter turns into
        text coloured like its highlight (invisible). Swap each such pair."""
        return Markup(FETCHED_HIGHLIGHT.sub(r'<span style="background-color: \2"><span style="color: \1">\3</span></span>',
                                            str(fragment)))

    def recolor(self, fragment, hex_: str) -> Markup:
        """Set the text colour a highlight inside `fragment` carries to `hex_`."""
        s = str(fragment).replace('<span style="color: #172b4d">', f'<span style="color: {hex_}">')
        return Markup(s)

    def align(self, fragment, to: str) -> Markup:
        """Alignment is a paragraph/heading mark in Confluence: put it on each one."""
        value = "end" if to == "right" else "center"
        root = dom.parse(str(fragment))
        for el in root.find_all(lambda e: e.tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6")):
            el.attrs["style"] = f"text-align: {value}"
        return Markup(dom.serialize(root))

    def image(self, src: str, alt: str = "", caption: str = "", block: bool = True) -> Markup:
        """A local image: embedded through Macro Pack HTML when possible, else a placeholder
        panel plus a handover (the agent can't upload attachments)."""
        try:
            path = self._r.local_file(src)
        except ValueError as e:
            self._r.errors.append(Diag(self._r._line, str(e)))
            return Markup("")
        limit = float(self.setting("image_max_kb", 300)) * 1024
        mode = self.setting("images", "macro-pack")
        if block and mode == "macro-pack" and self.has("macro-pack") and path.stat().st_size <= limit:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            uri = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
            cap = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
            body = (f'<figure data-rapport-src="{html.escape(src, quote=True)}" style="margin:0;text-align:center">'
                    f'<img src="{uri}" alt="{html.escape(alt, quote=True)}" style="max-width:100%">{cap}</figure>')
            return self.macro_pack("html", body)
        self.handover(f"upload {src} where the page says 'rapport image: {src}'")
        note = f"📎 rapport image: {html.escape(src)} (upload the file here)"
        if not block:
            return Markup(f"<code>{note}</code>")
        return Markup(f'<div data-type="panel-note"><p>{note}</p></div>')


class ConfluenceRenderer(Renderer):
    def __init__(self, config):
        super().__init__(config, serve=False)
        self.cf = Cf(self)
        self.handovers: list[str] = []

    def _install_render_rules(self) -> None:
        md = self.md

        def image(this, tokens, idx, options, env):
            tok = tokens[idx]
            alt = "".join(t.content for t in (tok.children or []))
            return str(self.cf.image(tok.attrGet("src") or "", alt, block=False))

        def rp_inline(this, tokens, idx, options, env):
            return self.render_inline_module(tokens[idx].meta)

        def html_block(this, tokens, idx, options, env):
            return tokens[idx].content

        def footnote_ref(this, tokens, idx, options, env):
            n = tokens[idx].meta["id"] + 1
            return f"<sup>[{n}]</sup>"

        md.add_render_rule("image", image)
        md.add_render_rule("rp_inline", rp_inline)
        md.add_render_rule("html_block", html_block)
        md.add_render_rule("footnote_ref", footnote_ref)

    # -- templates: confluence.html ------------------------------------------------- #
    def template(self, mod: Module):
        key = f"confluence:{mod.name}"
        if key not in self.templates:
            path = mod.file("confluence.html")
            if path is None:
                return None
            try:
                self.templates[key] = self.env.from_string(path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                self.errors.append(Diag(None, f"module '{mod.name}' confluence.html: {e}"))
                return None
        return self.templates[key]

    def _render_template(self, mod: Module, **kw) -> str:
        tpl = self.template(mod)
        if tpl is None:
            self.errors.append(Diag(self._line, f"module '{mod.name}' can't be exported to Confluence "
                                                f"(it has no confluence.html)"))
            return ""
        try:
            return tpl.render(ctx=self.ctx, cf=self.cf, config=self.config.data, meta=self.meta,
                              cfg=self.config.module(mod.name), **kw)
        except Exception as e:  # noqa: BLE001
            self.errors.append(Diag(self._line, f"module '{mod.name}' failed to export: {e}"))
            return ""

    def _hook(self, mod: Module, view: View) -> dict:
        """prepare.py's confluence(node, ctx, cf) if it has one (prepare() renders for the page)."""
        fn = mod.hook("confluence")
        if mod.load_error:
            self.errors.append(Diag(None, f"module '{mod.name}' {mod.load_error}"))
        if not fn:
            return {}
        try:
            return fn(view, self.ctx, self.cf) or {}
        except Exception as e:  # noqa: BLE001
            self.errors.append(Diag(self._line, f"module '{mod.name}': {e}"))
            return {}

    # -- nodes ---------------------------------------------------------------------- #
    def render_node(self, node) -> Markup:
        mod = self.registry.get(node.module)
        line = self._line = node.lines[0] or None
        if mod is None:
            return Markup("")
        self.used.add(mod.name)
        view = View(module=mod.name, id=node.id, params=node.params, positional=node.positional,
                    text=node.text, attrs=node.attrs, lines=node.lines,
                    heading_path=node.heading_path, section=node.section)
        if node.directive:
            view.children = [self._child_view(c) for c in node.children]
            view.body = Markup("\n".join(str(c.html) for c in view.children))
            self._line = line
        elif node.tokens:
            view.html = Lazy(lambda: self.default_html(node))
        extra = self._hook(mod, view)
        return Markup(self._render_template(mod, node=view, **extra).strip())

    def render_inline_module(self, meta: dict) -> str:
        mod = self.registry.get(meta["name"])
        if mod is None:
            return html.escape(meta.get("content", ""))
        params, errs = resolve_params(mod, self.config.module(mod.name), meta.get("params", {}))
        for e in errs + ([meta["error"]] if meta.get("error") else []):
            self.errors.append(Diag(self._line, e))
        content = Markup(self.md.renderInline(meta.get("content", ""), {"rp": self}))
        view = View(module=mod.name, id="", params=params, body=content, text=meta.get("content", ""))
        extra = self._hook(mod, view)
        return self._render_template(mod, node=view, **extra).strip()

    def render_page(self, doc) -> ExportResult:
        self.meta = dict(doc.meta)
        nodes = doc.nodes
        first = nodes[0] if nodes else None
        if "title" not in self.meta and first is not None and first.module == "heading" \
                and first.attrs.get("level") == 1:
            self.meta["title"] = first.attrs.get("text", "")
            nodes = nodes[1:]
        self.meta.setdefault("title", self.report_dir.name.replace("-", " ").replace("_", " ").capitalize())
        self._source = doc.source
        self._source_lines = doc.source.split("\n")
        self._seen = {}
        parts = [str(self.render_node(n)) for n in nodes]
        if self.config.get("confluence.toc", True) and "toc" in (self.config.get("layout.side", []) or []) \
                and any(n.module == "heading" for n in nodes):
            parts.insert(0, str(self.cf.macro("toc", "Table of Contents", {"maxLevel": "3"})))
        body = "\n".join(p for p in parts if p.strip()) + "\n"
        return ExportResult(title=str(self.meta["title"]), body=body,
                            errors=doc.errors + self.errors, warnings=self.warnings, handovers=self.handovers)


def export(report_dir, sets=None) -> ExportResult:
    from . import config as config_mod
    from .parse import parse
    cfg, body = config_mod.load_for_report(report_dir, sets)
    doc = parse(body, cfg.registry, cfg)
    res = ConfluenceRenderer(cfg).render_page(doc)
    res.warnings = [w.format(cfg.get("report.source")) if hasattr(w, "format") else w for w in res.warnings]
    return res
