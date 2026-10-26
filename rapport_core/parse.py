"""Markdown + ::: modules → node tree with source line maps.

Syntax (one form for every block module):

    ::: name optional title text {key = "value", flag = true}
    body (Markdown, or raw text/TOML if the module declares body = "text"|"toml")
    :::

Containers nest with more colons, or with the same count (open/close lines
are told apart by whether a name follows the colons). The closing scan skips
fenced code, so a fence that *shows* ::: syntax never closes its container.
Inline modules: :name[content]{params}. Code fences take params too:
```python {title = "app.py", hl = [2, 3]}.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from .registry import Registry, resolve_params
from .util import collapse_ws, did_you_mean, short_hash, slugify

OPEN_RE = re.compile(r"^(:{3,})[ \t]*([A-Za-z][\w-]*)(.*)$")
CLOSE_RE = re.compile(r"^(:{3,})[ \t]*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
INLINE_RE = re.compile(r":([a-z][a-z0-9-]*)\[")

PARAMS_HINT = 'params must be one single-line TOML inline table at the end of the line, e.g. {tone = "warning", open = true}'
INLINE_PARAMS_HINT = 'params must be a TOML inline table right after the ], e.g. :badge[shipped]{tone = "ok"}'


@dataclass
class Diag:
    line: int | None
    message: str

    def format(self, filename: str = "report.md") -> str:
        return f"{filename}:{self.line}: {self.message}" if self.line else f"{filename}: {self.message}"


@dataclass
class Node:
    module: str
    lines: tuple[int, int]            # 1-based, inclusive
    src: str = ""
    params: dict = field(default_factory=dict)
    positional: str = ""
    children: list["Node"] = field(default_factory=list)
    text: str = ""                    # raw body (text/toml modules, code content)
    attrs: dict = field(default_factory=dict)
    tokens: list = field(default_factory=list)   # element token slice for default rendering
    inline: Token | None = None
    id: str = ""
    heading_path: list[str] = field(default_factory=list)
    section: str = ""
    directive: bool = False


@dataclass
class Document:
    nodes: list[Node]
    headings: list[dict]
    errors: list[Diag]
    warnings: list[Diag]
    source: str
    meta: dict


def split_params(rest: str) -> tuple[str, dict, str | None]:
    """'Title words {k = v}' → ('Title words', {'k': 'v'}, None). Braces that aren't a
    params table are title text, unless they look like a failed attempt at one (= or :)."""
    rest = rest.strip()
    if rest.endswith("}"):
        for i, ch in enumerate(rest):
            if ch == "{":
                try:
                    return rest[:i].strip(), tomllib.loads("p = " + rest[i:])["p"], None
                except tomllib.TOMLDecodeError:
                    continue
    if "{" in rest and ("=" in rest[rest.index("{"):] or ":" in rest[rest.index("{"):]):
        return rest, {}, PARAMS_HINT
    return rest, {}, None


# ── markdown-it rules ──────────────────────────────────────────────────────── #
def _directive_rule(registry: Registry):
    def rule(state, startLine: int, endLine: int, silent: bool) -> bool:
        if state.is_code_block(startLine):
            return False
        start = state.bMarks[startLine] + state.tShift[startLine]
        line = state.src[start:state.eMarks[startLine]]
        if not line.startswith(":::"):
            return False
        m = OPEN_RE.match(line)
        if not m:
            return False
        if silent:
            return True
        marker, name, rest = m.group(1), m.group(2), m.group(3)
        n = len(marker)

        nextLine, stack, fence, closed = startLine, [], None, False
        while True:
            nextLine += 1
            if nextLine >= endLine:
                break
            s = state.bMarks[nextLine] + state.tShift[nextLine]
            e = state.eMarks[nextLine]
            if s < e and state.sCount[nextLine] < state.blkIndent:
                break
            text = state.src[s:e]
            if fence:
                fm = FENCE_RE.match(text)
                if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence) \
                        and not text[len(fm.group(1)):].strip():
                    fence = None
                continue
            if state.is_code_block(nextLine):
                continue
            fm = FENCE_RE.match(text)
            if fm:
                fence = fm.group(1)
                continue
            cm = CLOSE_RE.match(text)
            if cm:
                c = len(cm.group(1))
                if stack:
                    if c >= stack[-1]:
                        stack.pop()
                    continue
                if c >= n:
                    closed = True
                    break
                continue
            om = OPEN_RE.match(text)
            if om:
                stack.append(len(om.group(1)))

        positional, params, perr = split_params(rest)
        mod = registry.get(name)
        meta = {"name": name, "positional": positional, "params": params,
                "line": startLine + 1, "errors": [], "warnings": []}
        if perr:
            meta["errors"].append(perr)
        if mod is None or mod.kind != "block":
            blocks = [b.name for b in registry.by_kind("block")]
            what = f"'{name}' is a {mod.kind} module, not a block" if mod else f"unknown module '{name}'"
            meta["errors"].append(f"{what}{did_you_mean(name, blocks)}")
        if not closed:
            meta["warnings"].append(f"':::{name}' is never closed; it runs to the end of its parent")
        body = mod.body if (mod and mod.kind == "block") else "markdown"
        end_map = nextLine + 1 if closed else nextLine

        if body == "markdown":
            old_parent, old_max = state.parentType, state.lineMax
            state.parentType = "rp_directive"
            state.lineMax = nextLine
            tok = state.push("rp_directive_open", "div", 1)
            tok.block, tok.map, tok.meta, tok.markup = True, [startLine, end_map], meta, marker
            state.md.block.tokenize(state, startLine + 1, nextLine)
            tok = state.push("rp_directive_close", "div", -1)
            tok.block = True
            state.parentType, state.lineMax = old_parent, old_max
        else:
            tok = state.push("rp_directive", "div", 0)
            tok.block, tok.map, tok.meta, tok.markup = True, [startLine, end_map], meta, marker
            tok.content = state.getLines(startLine + 1, nextLine, state.sCount[startLine], False)
            if body == "none" and tok.content.strip():
                meta["warnings"].append(f"'{name}' ignores its body")
        state.line = nextLine + (1 if closed else 0)
        return True

    return rule


def _inline_rule(registry: Registry):
    def rule(state, silent: bool) -> bool:
        pos = state.pos
        src = state.src
        if src[pos] != ":":
            return False
        m = INLINE_RE.match(src, pos)
        if not m or m.end() > state.posMax:
            return False
        mod = registry.get(m.group(1))
        if mod is None or mod.kind != "inline":
            return False
        # right after a letter or digit only for modules that attach to a word (H:sub[2]O)
        if pos > 0 and src[pos - 1].isalnum() and not mod.manifest.get("attach", False):
            return False
        label_end = state.md.helpers.parseLinkLabel(state, m.end() - 1, True)
        if label_end < 0:
            return False
        content = src[m.end():label_end]
        p = label_end + 1
        params: dict = {}
        error = None
        if p < state.posMax and src[p] == "{":
            j = p
            found = False
            while True:
                j = src.find("}", j + 1)
                if j < 0 or j >= state.posMax:
                    break
                try:
                    params = tomllib.loads("p = " + src[p:j + 1])["p"]
                    found = True
                    break
                except tomllib.TOMLDecodeError:
                    continue
            if found:
                p = j + 1
            else:
                error = INLINE_PARAMS_HINT
        if not silent:
            tok = state.push("rp_inline", "span", 0)
            tok.meta = {"name": mod.name, "params": params, "content": content, "error": error}
        state.pos = p
        return True

    return rule


def make_markdown(registry: Registry, raw_html: bool = False) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": raw_html, "typographer": False})
    md.enable(["table", "strikethrough"])
    md.use(footnote_plugin)
    md.use(tasklists_plugin)
    md.block.ruler.before("fence", "rp_directive", _directive_rule(registry),
                          {"alt": ["paragraph", "reference", "blockquote", "list"]})
    md.inline.ruler.before("emphasis", "rp_inline", _inline_rule(registry))
    return md


# ── tokens → nodes ─────────────────────────────────────────────────────────── #
ELEMENT_FOR = {
    "heading_open": "heading", "paragraph_open": "paragraph", "bullet_list_open": "list",
    "ordered_list_open": "list", "blockquote_open": "blockquote", "table_open": "table",
    "footnote_block_open": "footnotes",
    "fence": "code", "code_block": "code", "hr": "hr", "html_block": "html",
}


def _close_index(tokens: list, i: int) -> int:
    level = tokens[i].level
    k = i + 1
    while k < len(tokens):
        t = tokens[k]
        if t.level == level and t.nesting == -1:
            return k
        k += 1
    return len(tokens) - 1


def inline_text(tok: Token | None) -> str:
    if tok is None:
        return ""
    parts = []
    for c in tok.children or []:
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif c.type == "rp_inline":
            parts.append(c.meta.get("content", ""))
        elif c.type == "image":
            parts.append(c.content)
    return collapse_ws("".join(parts))


class Builder:
    def __init__(self, registry: Registry, config, source: str):
        self.registry = registry
        self.config = config
        self.lines = source.split("\n")
        self.errors: list[Diag] = []
        self.warnings: list[Diag] = []

    def src_of(self, a: int, b: int) -> str:
        return "\n".join(self.lines[a - 1:b])

    def span(self, tok: Token) -> tuple[int, int]:
        if tok.map:
            return (tok.map[0] + 1, max(tok.map[0] + 1, tok.map[1]))
        return (0, 0)

    def nodes(self, tokens: list, i: int = 0, j: int | None = None) -> list[Node]:
        j = len(tokens) if j is None else j
        out: list[Node] = []
        while i < j:
            tok = tokens[i]
            if tok.type == "rp_directive_open":
                k = _close_index(tokens, i)
                out.append(self.directive(tok, tokens[i + 1:k]))
                i = k + 1
            elif tok.type == "rp_directive":
                out.append(self.directive(tok, None))
                i += 1
            elif tok.type in ELEMENT_FOR:
                k = _close_index(tokens, i) if tok.nesting == 1 else i
                out.append(self.element(tok, tokens[i:k + 1]))
                i = k + 1
            else:
                i += 1
        return out

    def element(self, tok: Token, slice_: list) -> Node:
        name = ELEMENT_FOR[tok.type]
        a, b = self.span(tok)
        node = Node(module=name, lines=(a, b), src=self.src_of(a, b), tokens=slice_)
        if name == "heading":
            node.attrs["level"] = int(tok.tag[1])
            node.inline = slice_[1]
            node.attrs["text"] = inline_text(node.inline)
        elif name == "paragraph":
            node.inline = slice_[1]
            kids = [c for c in (node.inline.children or []) if not (c.type == "text" and not c.content.strip())
                    and c.type != "softbreak"]
            if len(kids) == 1 and kids[0].type == "image":
                img = kids[0]
                node.module = "image"
                node.attrs = {"src": img.attrGet("src") or "", "alt": img.content,
                              "title": img.attrGet("title") or ""}
        elif name == "code":
            info = (tok.info or "").strip()
            lang, _, rest = info.partition(" ")
            lang_params: dict = {}
            if lang.startswith("{"):
                lang, rest = "", info
            if rest.strip():
                _, lang_params, perr = split_params(rest)
                if perr:
                    self.errors.append(Diag(a, f"code fence: {PARAMS_HINT}"))
            node.attrs["lang"] = lang
            node.text = tok.content.rstrip("\n")
            node.params = lang_params
        elif name == "list":
            node.attrs["ordered"] = tok.type == "ordered_list_open"
        self._params(node, node.params, "", a)
        return node

    def directive(self, tok: Token, inner: list | None) -> Node:
        meta = tok.meta
        a, b = self.span(tok)
        node = Node(module=meta["name"], lines=(a, b), src=self.src_of(a, b),
                    positional=meta.get("positional", ""), directive=True)
        for msg in meta.get("errors", []):
            self.errors.append(Diag(meta["line"], msg))
        for msg in meta.get("warnings", []):
            self.warnings.append(Diag(meta["line"], msg))
        mod = self.registry.get(node.module)
        if inner is not None:
            node.children = self.nodes(inner)
        else:
            node.text = tok.content
            if mod is not None and mod.body == "toml":
                try:
                    node.attrs["data"] = tomllib.loads(tok.content)
                except tomllib.TOMLDecodeError as e:
                    self.errors.append(Diag(a, f"'{node.module}' body must be TOML: {e}"))
                    node.attrs["data"] = {}
        if mod is not None and mod.kind == "block" and not self.config.enabled(mod.name):
            self.errors.append(Diag(meta["line"], f"module '{mod.name}' is switched off "
                                                  f"(modules.{mod.name}.enabled = false)"))
        if mod is not None and mod.kind == "block":
            if mod.children:
                for child in node.children:
                    if child.directive and child.module not in mod.children:
                        self.errors.append(Diag(child.lines[0], f"'{child.module}' can't go inside "
                                                f"'{mod.name}' (allowed: {', '.join(mod.children)})"))
            self._params(node, meta.get("params", {}), node.positional, a)
        return node

    def _params(self, node: Node, given: dict, positional: str, line: int) -> None:
        mod = self.registry.get(node.module)
        if mod is None:
            if not node.directive:
                self.errors.append(Diag(line, f"no element module '{node.module}' installed"))
            return
        params, errs = resolve_params(mod, self.config.module(mod.name), given, positional)
        node.params = params
        for e in errs:
            self.errors.append(Diag(line, e))


def assign_ids(nodes: list[Node], path: list[str] | None = None, section: str = "",
               seen: dict | None = None, slugs: dict | None = None, headings: list | None = None,
               top: bool = True) -> None:
    """Heading paths, section slugs, and content-derived block ids (document order)."""
    seen = {} if seen is None else seen
    slugs = {} if slugs is None else slugs
    stack: list[tuple[int, str]] = []
    base = list(path or [])
    for node in nodes:
        if node.module == "heading":
            level = node.attrs["level"]
            while stack and stack[-1][0] >= level:
                stack.pop()
            slug = slugify(node.attrs.get("text", ""))
            n = slugs.get(slug, 0) + 1
            slugs[slug] = n
            node.attrs["slug"] = slug if n == 1 else f"{slug}-{n}"
            stack.append((level, node.attrs.get("text", "")))
            section = node.attrs["slug"]
            if headings is not None and top:
                headings.append({"level": level, "text": node.attrs.get("text", ""),
                                 "id": node.attrs["slug"]})
        node.heading_path = base + [t for _, t in stack]
        node.section = section
        key = "/".join(node.heading_path) + "|" + node.module + "|" + collapse_ws(node.src)
        bid = f"{node.module}-{short_hash(key)}"
        n = seen.get(bid, 0) + 1
        seen[bid] = n
        node.id = bid if n == 1 else f"{bid}-{n}"
        if node.children:
            assign_ids(node.children, node.heading_path, section, seen, slugs, headings, top=False)


def parse(body: str, registry: Registry, config, md: MarkdownIt | None = None) -> Document:
    md = md or make_markdown(registry, bool(config.get("report.raw_html", False)))
    env: dict = {}
    tokens = md.parse(body, env)
    builder = Builder(registry, config, body)
    nodes = builder.nodes(tokens)
    headings: list[dict] = []
    assign_ids(nodes, headings=headings)
    return Document(nodes=nodes, headings=headings, errors=builder.errors, warnings=builder.warnings,
                    source=body, meta=dict(config.get("meta", {}) or {}))
