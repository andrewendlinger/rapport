"""Node tree → self-contained HTML, entirely through modules.

Every block is rendered by its module's Jinja template; the renderer injects
the anchor attributes (data-rp-id / -module / -lines) into the template's root
element, collects CSS/JS only from modules the page actually uses, and inlines
everything (images, gifs, and fonts or images that CSS url()s become data: URIs).
rapport's CSS sits in cascade layers (theme, then modules); [theme] css files
follow unlayered, so they win.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import jinja2
from markupsafe import Markup
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.token import STANDARD_TYPES
from pygments.util import ClassNotFound

from . import __version__
from . import css as css_mod
from .parse import Builder, Diag, Document, Node, _close_index, assign_ids, make_markdown, parse
from .registry import KIND_ORDER, Module, resolve_params
from .util import is_image_file, json_block

# leading whitespace and HTML comments are skipped: anchors go on the first real tag
FIRST_TAG_RE = re.compile(r"^((?:\s|<!--.*?-->)*)<([a-zA-Z][\w-]*)((?:\s[^>]*?)?)(/?)>", re.S)
CLASS_ATTR_RE = re.compile(r'\sclass="([^"]*)"')
SCRIPT_END_RE = re.compile(r"</(script)", re.I)
# tokens rapport's own CSS relies on, for a theme that doesn't define them
CORE_TOKENS = {"width": "70ch", "body-size": "17px"}


def _attr(v) -> str:
    return html.escape(str(v), quote=True)


def inject_root(out: str, attrs: dict, classes: list[str]) -> str:
    """Add attributes and classes to the first start tag of a template's output."""
    m = FIRST_TAG_RE.match(out)
    if not m:
        return out
    lead, tag, rest, selfclose = m.groups()
    cls = " ".join(classes)
    cm = CLASS_ATTR_RE.search(rest)
    if cm:
        rest = rest[:cm.start()] + f' class="{cls} {cm.group(1)}"' + rest[cm.end():]
    elif cls:
        rest = f' class="{cls}"' + rest
    extra = "".join(f' {k}="{_attr(v)}"' for k, v in attrs.items() if v is not None)
    return f"{lead}<{tag}{rest}{extra}{selfclose}>" + out[m.end():]


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _RootCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.roots = 0

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            self.roots += 1
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.depth == 0:
            self.roots += 1

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.depth == 0 and data.strip():
            self.roots += 1


def count_roots(fragment: str) -> int:
    c = _RootCounter()
    c.feed(fragment)
    c.close()
    return c.roots


class _TextOf(HTMLParser):
    """The text a reader sees in a fragment, as the feedback script indexes it:
    no UI chrome (data-rp-ui), scripts or styles; whitespace collapsed."""
    HIDDEN = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            return
        hidden = (self.stack and self.stack[-1][1]) or tag in self.HIDDEN or any(k == "data-rp-ui" for k, _ in attrs)
        self.stack.append((tag, bool(hidden)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]):
            self.parts.append(data)


def text_of(fragment: str) -> str:
    t = _TextOf()
    t.feed(fragment)
    t.close()
    return re.sub(r"\s+", " ", "".join(t.parts)).strip()


def _script_safe(js: str) -> str:
    return SCRIPT_END_RE.sub(r"<\\/\1", js)


def _css_class(ttype) -> str:
    while ttype not in STANDARD_TYPES:
        ttype = ttype.parent
    return STANDARD_TYPES[ttype]


def highlight_lines(code: str, lang: str, hl=None) -> Markup:
    """Pygments tokens → one <span class="rp-line"> per source line, so lines
    can be numbered and highlighted with CSS; text stays plain in textContent."""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    hl = set(hl or [])
    lines: list[list] = [[]]
    for ttype, value in lexer.get_tokens(code):
        parts = value.split("\n")
        for idx, part in enumerate(parts):
            if idx:
                lines.append([])
            if part:
                lines[-1].append((ttype, part))
    if len(lines) > 1 and not lines[-1]:
        lines.pop()
    out = []
    for n, toks in enumerate(lines, 1):
        inner = "".join(
            f'<span class="{c}">{html.escape(v, quote=False)}</span>' if (c := _css_class(t))
            else html.escape(v, quote=False)
            for t, v in toks)
        cls = "rp-line rp-hl" if n in hl else "rp-line"
        out.append(f'<span class="{cls}">{inner}</span>')
    return Markup("\n".join(out))


def pygments_css(light: str, dark: str) -> str:
    def defs(style, prefix):
        try:
            lines = HtmlFormatter(style=style).get_token_style_defs(prefix)
        except ClassNotFound:
            lines = HtmlFormatter(style="default").get_token_style_defs(prefix)
        return "\n".join(lines) if isinstance(lines, list) else lines
    light_css = defs(light, ".rp-code")
    dark_forced = defs(dark, ':root[data-theme="dark"] .rp-code')
    dark_auto = defs(dark, ':root:not([data-theme="light"]) .rp-code')
    return f"{light_css}\n{dark_forced}\n@media (prefers-color-scheme:dark){{\n{dark_auto}\n}}"


class Lazy:
    """Markup computed on first use: an element's default HTML is only
    rendered (and its images only inlined) if the template asks for it."""

    def __init__(self, fn):
        self._fn, self._value = fn, None

    def _get(self) -> Markup:
        if self._value is None:
            self._value = Markup(self._fn())
        return self._value

    def __html__(self) -> str:
        return self._get()

    def __str__(self) -> str:
        return str(self._get())

    def __bool__(self) -> bool:
        return bool(self._get())


@dataclass
class View:
    """What a template sees as `node`."""
    module: str
    id: str
    params: dict
    positional: str = ""
    body: Markup = Markup("")
    html: Markup = Markup("")
    text: str = ""
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    lines: tuple = (0, 0)
    heading_path: list = field(default_factory=list)
    section: str = ""


class Context:
    """Helpers exposed to templates and prepare.py as `ctx`."""

    def __init__(self, renderer: "Renderer"):
        self._r = renderer

    def highlight(self, code: str, lang: str = "", hl=None) -> Markup:
        self._r.used.add("code")
        return highlight_lines(code, lang, hl)

    def markdown(self, text: str) -> Markup:
        return self._r.render_markdown(text)

    def inline(self, text: str) -> Markup:
        return Markup(self._r.md.renderInline(text, {"rp": self._r}))

    def asset(self, src: str, line: int | None = None, where: str | None = None) -> str:
        return self._r.asset(src, line, where=where)

    def icon(self, value, tint: bool | None = None, where: str | None = None) -> Markup:
        return self._r.icon(value, tint, where)

    def href(self, url, where: str | None = None) -> str:
        return self._r.href(url, where)

    def module(self, name: str) -> dict:
        return self._r.config.module(name)

    @property
    def config(self) -> dict:
        return self._r.config.data

    @property
    def meta(self) -> dict:
        return self._r.meta


class Renderer:
    def __init__(self, config, serve: bool = False, extra_data: dict | None = None,
                 state: dict | None = None, strict: bool = False):
        self.strict = strict
        self.config = config
        self.registry = config.registry
        self.report_dir: Path = config.report_dir
        self.serve = serve
        self.extra_data = extra_data or {}
        self.state = state or {}
        self.md = make_markdown(self.registry, bool(config.get("report.raw_html", False)))
        self._install_render_rules()
        self.env = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True,
                                      undefined=jinja2.ChainableUndefined)
        self.env.globals.update(today=date.today().isoformat(), generator=f"rapport {__version__}")
        self.templates: dict[str, jinja2.Template] = {}
        self.ctx = Context(self)
        self.used: set[str] = set()
        self.blocks: dict[str, dict] = {}
        self.errors: list[Diag] = []
        self.warnings: list[Diag] = []
        self.inlined_bytes = 0
        self._data_uris: dict[Path, str] = {}
        self.asset_paths: set[Path] = set()      # found or not: the server watches them
        self.meta: dict = {}
        self._line: int | None = None

    # -- markdown-it render rules ---------------------------------------------- #
    def _install_render_rules(self) -> None:
        md = self.md
        orig_image = md.renderer.rules["image"]

        def image(this, tokens, idx, options, env):
            tok = tokens[idx]
            tok.attrSet("src", self.asset(tok.attrGet("src") or "", self._line))
            return orig_image(tokens, idx, options, env)

        def rp_inline(this, tokens, idx, options, env):
            return self.render_inline_module(tokens[idx].meta)

        def html_block(this, tokens, idx, options, env):
            return tokens[idx].content

        md.add_render_rule("image", image)
        md.add_render_rule("rp_inline", rp_inline)
        md.add_render_rule("html_block", html_block)

    # -- templates --------------------------------------------------------------- #
    def template(self, mod: Module) -> jinja2.Template | None:
        if mod.name not in self.templates:
            path = mod.template_path
            if path is None:
                return None
            try:
                self.templates[mod.name] = self.env.from_string(path.read_text(encoding="utf-8"))
            except jinja2.TemplateSyntaxError as e:
                self.errors.append(Diag(None, f"module '{mod.name}' template.html:{e.lineno}: {e.message}"))
                return None
        return self.templates[mod.name]

    def _render_template(self, mod: Module, **kw) -> str:
        tpl = self.template(mod)
        if tpl is None:
            return ""
        try:
            return tpl.render(ctx=self.ctx, config=self.config.data, meta=self.meta,
                              cfg=self.config.module(mod.name), **kw)
        except Exception as e:  # noqa: BLE001 - a broken module must not kill the build
            self.errors.append(Diag(self._line, f"module '{mod.name}' failed to render: {e}"))
            return f'<div class="rp-error">module {html.escape(mod.name)} failed: {html.escape(str(e))}</div>'

    # -- assets ------------------------------------------------------------------ #
    def asset(self, src: str, line: int | None = None, base: Path | None = None, where: str | None = None) -> str:
        """A local file as a data: URI. base: the folder src is relative to (default:
        the report folder); where: "brand.css:12" for errors outside report.md."""
        def error(msg: str) -> str:
            self.errors.append(Diag(None, f"{where}: {msg}") if where else Diag(line, msg))
            return ""
        if not src or src.startswith("data:") or src.startswith("#"):
            return src
        if css_mod.SCHEME_RE.match(src):
            return error(f"remote asset {src!r}: reports are self-contained; "
                         f"download it into assets/ and reference it relatively")
        # markdown-it percent-encodes link targets: "my file.png" arrives as "my%20file.png"
        name = urllib.parse.unquote(src)
        path = ((base or self.report_dir) / name).resolve()
        self.asset_paths.add(path)
        if not path.is_file():
            return error(f"asset not found: {name} (looked in {path.parent})")
        size = path.stat().st_size
        self.inlined_bytes += size          # every use is another copy in the page
        budget = float(self.config.get("report.max_inline_mb", 8)) * 1024 * 1024
        if self.inlined_bytes > budget:
            error(f"inlined assets exceed report.max_inline_mb ({self.inlined_bytes / 1048576:.1f} MB); "
                  f"shrink {name} or raise the budget")
        if path not in self._data_uris:
            mime = css_mod.MIME.get(path.suffix[1:].lower()) or mimetypes.guess_type(path.name)[0] \
                or "application/octet-stream"
            self._data_uris[path] = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
        return self._data_uris[path]

    def icon(self, value, tint: bool | None = None, where: str | None = None) -> Markup:
        """A symbol (emoji, any text), or an image file inlined. tint draws the image in the
        text colour; by default an SVG that uses currentColor is (as an <img> it would be
        black: the page's colour doesn't reach into it), anything else keeps its colours."""
        if not value:
            return Markup("")
        if not is_image_file(value):
            return Markup('<span class="rp-icon" aria-hidden="true" data-rp-ui>{}</span>').format(value)
        uri = self.asset(value, where=where)
        if not uri:
            return Markup("")
        if tint is None or isinstance(tint, jinja2.Undefined):     # e.tint of an entry without one
            tint = uri.startswith("data:image/svg+xml;") and \
                b"currentcolor" in base64.b64decode(uri.partition(",")[2]).lower()
        if tint:
            return Markup('<span class="rp-icon rp-icon-tint" style="--rp-icon:url({})" aria-hidden="true" '
                          'data-rp-ui></span>').format(uri)
        return Markup('<img class="rp-icon" src="{}" alt="" data-rp-ui>').format(uri)

    def href(self, url, where: str | None = None) -> str:
        """A link target for a template: "" for none, or one markdown-it wouldn't link (javascript: …)."""
        if not url:
            return ""
        if not self.md.validateLink(str(url)):
            self.errors.append(Diag(None, f"{where + ': ' if where else ''}unsafe link {str(url)!r} "
                                          "(javascript:, vbscript:, file: and non-image data: links are blocked)"))
            return ""
        return self.md.normalizeLink(str(url))

    def inline_css(self, css: str, base: Path, where: str, fix: str = "list the file in theme.css instead") -> str:
        """Inline the files a stylesheet url()s, relative to base (its folder)."""
        def inline(ref):
            if ref.kind == "import":        # dropped: no remote url left in the page
                self.errors.append(Diag(None, f"{where}:{ref.line}: @import isn't supported; {fix}"))
                return ""
            if ref.kind == "var":
                self.errors.append(Diag(None, f"{where}:{ref.line}: var() inside {ref.value}(): a url can't be "
                                              "checked or inlined through a variable; write it out"))
                return None
            try:
                src, frag = css_mod.target(ref.value)
            except ValueError as e:
                self.errors.append(Diag(None, f"{where}:{ref.line}: {e}"))
                return ""
            return None if src is None else self.asset(src, base=base, where=f"{where}:{ref.line}") + frag
        return css_mod.rewrite(css, css_mod.scan(css), inline)

    def user_css(self) -> list[str]:
        """[theme] css files, outer layers first: unlayered, after rapport's CSS."""
        out = []
        for p in self.config.get("theme.css", []) or []:
            path = Path(p)
            self.asset_paths.add(path)
            if path.suffix.lower() != ".css":
                self.errors.append(Diag(None, f"theme.css: {path} isn't a .css file"))
            elif not path.is_file():
                self.errors.append(Diag(None, f"theme.css: file not found: {path}"))
            else:
                css = self.inline_css(path.read_text(encoding="utf-8-sig"), path.parent, path.name)
                out.append(f"/* theme.css: {path.name} */\n{css}")
        return out

    # -- nodes --------------------------------------------------------------------- #
    def render_nodes(self, nodes: list[Node]) -> Markup:
        return Markup("\n".join(self.render_node(n) for n in nodes))

    def default_html(self, node: Node) -> Markup:
        """markdown-it's own rendering of an element, with nested directives
        (e.g. inside list items) rendered through their modules."""
        if node.module in ("heading", "paragraph") and node.inline is not None:
            return Markup(self.md.renderer.renderInline(node.inline.children or [], self.md.options,
                                                        {"rp": self}))
        out, toks, i = [], node.tokens, 0
        while i < len(toks):
            t = toks[i]
            if t.type in ("rp_directive_open", "rp_directive"):
                k = _close_index(toks, i) if t.type == "rp_directive_open" else i
                b = Builder(self.registry, self.config, self._source)
                sub = b.nodes(toks, i, k + 1)
                self.errors.extend(b.errors)
                self.warnings.extend(b.warnings)
                assign_ids(sub, node.heading_path, node.section, self._seen, {}, None, top=False)
                line = self._line
                rendered = Markup(self.render_nodes(sub))
                self._line = line           # the rest of this element reports on its own line
                ht = type(t)("html_block", "", 0)
                ht.content = str(rendered) + "\n"
                out.append(ht)
                i = k + 1
            else:
                out.append(t)
                i += 1
        return Markup(self.md.renderer.render(out, self.md.options, {"rp": self}))

    def plain_element_html(self, node: Node) -> str:
        """An element whose module is switched off: markdown-it's own HTML."""
        inner = self.default_html(node) if node.tokens else Markup("")
        if node.module == "heading":
            lvl = node.attrs.get("level", 2)
            return f'<h{lvl} id="{_attr(node.attrs.get("slug", ""))}">{inner}</h{lvl}>'
        if node.module == "paragraph":
            return f"<p>{inner}</p>"
        return str(inner)

    def render_node(self, node: Node) -> Markup:
        mod = self.registry.get(node.module)
        line = self._line = node.lines[0] or None
        if mod is None:
            return Markup(f'<div class="rp-error">unknown module {html.escape(node.module)}</div>')
        if not node.directive and not self.config.enabled(mod.name):
            out = self.plain_element_html(node)
        else:
            self.used.add(mod.name)
            view = View(module=mod.name, id=node.id, params=node.params, positional=node.positional,
                        text=node.text, attrs=node.attrs, lines=node.lines,
                        heading_path=node.heading_path, section=node.section)
            if node.directive:
                view.children = [self._child_view(c) for c in node.children]
                view.body = Markup("\n".join(str(c.html) for c in view.children))
                self._line = line           # the children moved it: this block's own problems are on its ::: line
            elif node.tokens:
                view.html = Lazy(lambda: self.default_html(node))
            extra = {}
            prep = mod.prepare_fn()
            if mod.load_error:
                self.errors.append(Diag(None, f"module '{mod.name}' {mod.load_error}"))
            if prep:
                try:
                    extra = prep(view, self.ctx) or {}
                except Exception as e:  # noqa: BLE001
                    self.errors.append(Diag(line, f"module '{mod.name}' prepare.py failed: {e}"))
            out = self._render_template(mod, node=view, **extra)
        if not out.strip():
            return Markup("")
        if self.strict and (n := count_roots(out)) != 1:
            self.warnings.append(Diag(line, f"module '{mod.name}' template renders {n} root "
                                            f"elements; wrap them in one (anchors attach to the first)"))
        attrs = {"data-rp-id": node.id, "data-rp-module": mod.name,
                 "data-rp-lines": f"{node.lines[0]}-{node.lines[1]}"}
        if mod.js_path and node.params and mod.name in self.used:
            attrs["data-rp-params"] = json_block(node.params)
        if mod.feedback_unit == "none":
            attrs["data-rp-ui"] = ""
        out = inject_root(out, attrs, [f"rp-m-{mod.name}", "rp-block"])
        self.blocks[node.id] = {"module": mod.name, "lines": list(node.lines), "src": node.src,
                                "heading_path": node.heading_path, "section": node.section, "text": text_of(out)}
        kids = [c.id for c in node.children if c.id in self.blocks]
        if kids:
            self.blocks[node.id]["children"] = kids
        return Markup(out)

    def _child_view(self, node: Node) -> View:
        rendered = self.render_node(node)
        return View(module=node.module, id=node.id, params=node.params, positional=node.positional,
                    html=rendered, text=node.text, attrs=node.attrs, lines=node.lines,
                    heading_path=node.heading_path, section=node.section)

    def render_inline_module(self, meta: dict) -> str:
        mod = self.registry.get(meta["name"])
        if mod is None:
            return html.escape(meta.get("content", ""))
        if not self.config.enabled(mod.name):
            self.errors.append(Diag(self._line, f"inline module '{mod.name}' is switched off "
                                                f"(modules.{mod.name}.enabled = false)"))
            return self.md.renderInline(meta.get("content", ""), {"rp": self})
        self.used.add(mod.name)
        params, errs = resolve_params(mod, self.config.module(mod.name), meta.get("params", {}))
        for e in errs + ([meta["error"]] if meta.get("error") else []):
            self.errors.append(Diag(self._line, e))
        content = Markup(self.md.renderInline(meta.get("content", ""), {"rp": self}))
        view = View(module=mod.name, id="", params=params, body=content, text=meta.get("content", ""))
        out = self._render_template(mod, node=view)
        return inject_root(out, {"data-rp-module": mod.name}, [f"rp-m-{mod.name}"])

    def render_markdown(self, text: str) -> Markup:
        doc = parse(text, self.registry, self.config, self.md)
        self.errors.extend(doc.errors)
        return self.render_nodes(doc.nodes)

    # -- page ---------------------------------------------------------------------- #
    def features(self) -> list[Module]:
        out = []
        for mod in self.registry.by_kind("feature"):
            if not self.config.enabled(mod.name):
                continue
            if mod.when == "serve" and not self.serve:
                continue
            if mod.when == "static" and self.serve:
                continue
            out.append(mod)
        return out

    def module_css(self, mod: Module) -> str:
        return self.inline_css(mod.css_path.read_text(encoding="utf-8-sig"), mod.dir,
                               f"module '{mod.name}' style.css", "put the rules in style.css")

    def theme_css(self, theme: Module) -> str:
        overrides = dict(self.config.get("theme.tokens", {}) or {})
        dark_over = overrides.pop("dark", {}) if isinstance(overrides.get("dark"), dict) else {}
        light = {**theme.tokens, **overrides}
        dark = {**theme.dark_tokens, **dark_over}
        light["width"] = self.config.get("layout.width") or light.get("width") or CORE_TOKENS["width"]
        light.setdefault("body-size", CORE_TOKENS["body-size"])
        decl = lambda d: "".join(f"--rp-{k}:{v};" for k, v in d.items())  # noqa: E731
        css = f":root{{color-scheme:light dark;{decl(light)}}}\n"
        # rem = body-size in every theme, so sizes in rem scale with it
        css += "html{font-size:var(--rp-body-size)}\n"
        css += ':root[data-theme="light"]{color-scheme:light}\n'
        if dark:
            css += f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{decl(dark)}}}}}\n'
            css += f':root[data-theme="dark"]{{color-scheme:dark;{decl(dark)}}}\n'
        return css

    def render_document(self, doc: Document) -> str:
        # doc is shared by the static and the served render: never mutate it
        self.meta = dict(doc.meta)
        nodes, headings = doc.nodes, doc.headings
        title_lines = None
        first = nodes[0] if nodes else None
        if "title" not in self.meta and first is not None and first.module == "heading" \
                and first.attrs.get("level") == 1:
            # a leading "# Title" is the report title: it moves into the header
            self.meta["title"] = first.attrs.get("text", "")
            title_lines = first.lines
            nodes = nodes[1:]
            headings = [h for h in headings if h.get("id") != first.attrs.get("slug")]
        self.meta.setdefault("title", self.report_dir.name.replace("-", " ").replace("_", " ").capitalize())
        self.meta.setdefault("version", self.state.get("version", 1))
        self._source = doc.source
        self._source_lines = doc.source.split("\n")
        self._seen: dict = {}
        content = self.render_nodes(nodes)

        regions = {}
        fm = self.config.front_matter_lines or title_lines or (0, 0)
        if title_lines and self.config.front_matter_lines:
            fm = (fm[0], max(fm[1], title_lines[1]))
        for region in ("top", "side", "bottom"):
            parts = []
            for name in self.config.get(f"layout.{region}", []) or []:
                mod = self.registry.get(name)
                if mod is None or mod.kind != "layout":
                    self.errors.append(Diag(None, f"layout.{region}: '{name}' is not a layout module"))
                    continue
                if not self.config.enabled(name):
                    continue
                self.used.add(name)
                out = self._render_template(mod, headings=headings, region=region)
                if not out.strip():
                    continue
                attrs = {"data-rp-module": name}
                if mod.feedback_unit == "none":
                    attrs["data-rp-ui"] = ""
                else:
                    attrs["data-rp-id"] = name
                    attrs["data-rp-lines"] = f"{fm[0]}-{fm[1]}"
                    self.blocks[name] = {"module": name, "lines": list(fm),
                                         "src": "\n".join(self._source_lines[fm[0] - 1:fm[1]]) if fm[0] else "",
                                         "heading_path": [], "section": "", "text": text_of(out)}
                parts.append(inject_root(out, attrs, [f"rp-m-{name}"]))
            regions[region] = Markup("\n".join(parts))

        feature_html = []
        for mod in self.features():
            self.used.add(mod.name)
            if mod.template_path:
                feature_html.append(self._render_template(mod))

        page = self.registry.get(self.config.get("layout.page", "page"))
        if page is None or page.kind != "layout":
            self.errors.append(Diag(None, f"layout.page: no layout module '{self.config.get('layout.page')}'"))
            page = self.registry.get("page")
        self.used.add(page.name)
        theme_name = self.config.get("theme.name", "default")
        theme = self.registry.get(theme_name)
        if theme is None or theme.kind != "theme":
            self.errors.append(Diag(None, f"theme.name: no theme module '{theme_name}'"))
            theme = self.registry.get("default")
        used_mods = self.registry.ordered(self.used)
        theme_parts = [self.theme_css(theme)] + ([self.module_css(theme)] if theme.css_path else [])
        mod_parts = [f"/* {mod.name} */\n" + self.module_css(mod)
                     for mod in used_mods if mod.css_path and mod.kind != "theme"]
        if "code" in self.used:
            code_cfg = self.config.module("code")
            mod_parts.append(pygments_css(code_cfg.get("light_style", "friendly"),
                                          code_cfg.get("dark_style", "github-dark")))
        css = css_mod.style_safe("\n".join([css_mod.layered(theme_parts, mod_parts)] + self.user_css()))
        js_mods = [m for m in used_mods if m.js_path]
        js_mods.sort(key=lambda m: (0 if m.name == "runtime" else 1, KIND_ORDER[m.kind], m.name))
        scripts = "\n".join(
            f"<script>/* {m.name} */\n{_script_safe(m.js_path.read_text(encoding='utf-8'))}\n</script>"
            for m in js_mods)
        # no local paths in the page: report.html is meant to be shared
        data = {
            "report": {"title": self.meta.get("title"), "version": self.meta.get("version"),
                       "served": self.serve, "source": self.config.get("report.source")},
            "modules": {m.name: self.config.module(m.name) for m in js_mods},
            "present": sorted(self.used),
            **self.extra_data,
        }
        mode = self.config.get("theme.mode", "auto")
        return self._render_template(
            page, content=content, regions=regions, css=Markup(css),
            scripts=Markup(scripts), features=Markup("\n".join(feature_html)),
            data_json=Markup(json_block(data)), lang=self.config.get("report.lang", "en"),
            theme_mode=None if mode == "auto" else mode, headings=headings)
