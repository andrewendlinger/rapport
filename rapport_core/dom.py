"""A minimal HTML tree for the Confluence export and import: parse a fragment,
walk and edit it, serialize it back. Text is stored unescaped; serialize()
escapes it. Not a full HTML5 parser: Confluence's HTML is well formed."""

from __future__ import annotations

import html as html_mod
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# tags that close an open <p> (as browsers do); enough for markdown-it and Confluence output
BLOCKS = {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figure", "footer",
          "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "ol", "p", "pre", "section", "table", "ul"}


class El:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, children: list | None = None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children: list = []
        self.parent: El | None = None
        for c in children or []:
            self.append(c)

    def append(self, child):
        if isinstance(child, El):
            child.parent = self
        self.children.append(child)
        return child

    def get(self, key: str, default=None):
        return self.attrs.get(key, default)

    @property
    def elements(self) -> list["El"]:
        return [c for c in self.children if isinstance(c, El)]

    def find(self, pred) -> "El | None":
        for c in self.elements:
            if pred(c):
                return c
            hit = c.find(pred)
            if hit is not None:
                return hit
        return None

    def find_all(self, pred) -> list["El"]:
        out = []
        for c in self.elements:
            if pred(c):
                out.append(c)
            out += c.find_all(pred)
        return out

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def inner(self) -> str:
        return "".join(serialize(c) for c in self.children)

    def __repr__(self) -> str:   # pragma: no cover - debugging aid
        return f"<El {self.tag} {self.attrs} {len(self.children)} children>"


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = El("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKS and self.stack[-1].tag == "p":
            self.stack.pop()
        el = self.stack[-1].append(El(tag, {k: ("" if v is None else v) for k, v in attrs}))
        if tag not in VOID:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].append(El(tag, {k: ("" if v is None else v) for k, v in attrs}))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            top = self.stack[-1]
            if top.children and isinstance(top.children[-1], str):
                top.children[-1] += data
            else:
                top.children.append(data)


def parse(fragment: str) -> El:
    b = _Builder()
    b.feed(fragment)
    b.close()
    return b.root


def _esc_attr(v: str) -> str:
    return html_mod.escape(str(v), quote=True)


def serialize(node) -> str:
    if isinstance(node, str):
        return html_mod.escape(node, quote=False)
    if node.tag == "#root":
        return node.inner()
    attrs = "".join(f' {k}="{_esc_attr(v)}"' if v != "" or k.startswith("data-") else f" {k}"
                    for k, v in node.attrs.items())
    if node.tag in VOID:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{node.inner()}</{node.tag}>"
