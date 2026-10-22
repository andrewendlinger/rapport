"""CSS that goes into the page: url() references (inlined as data: URIs, so a
theme can ship a font), the @layer wrapper, and </style escaping.

The scanner walks the CSS token by token, skipping comments and strings, so a
url( inside `content:"…"` or a comment is left alone, and a quoted url may
contain ")".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LAYERS = ("rp-theme", "rp-modules")
# what a url() may point at; anything else can't be inlined
MIME = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf",
        "eot": "application/vnd.ms-fontobject", "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "avif": "image/avif"}
IMAGE_SETS = {"image-set", "-webkit-image-set"}
SCHEME_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
STRING = r""""(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?"""
TOKEN_RE = re.compile(rf"""
  (?P<comment>/\*.*?(?:\*/|\Z))
| (?P<string>{STRING})
| (?P<url>url\(\s*(?:{STRING}|(?:\\.|[^)\s"'\\])*)\s*\))
| (?P<func>[-\w]+\()
| (?P<at>@[-\w]+)
| (?P<esc>\\.)
| (?P<open>\()
| (?P<close>\))
| (?P<end>[;{{}}])
""", re.X | re.S | re.I)
STRIP_RE = re.compile(rf"/\*.*?(?:\*/|\Z)|{STRING}", re.S)
ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n]?|(\n)|(.))", re.S)
STYLE_END_RE = re.compile(r"</(style)", re.I)


@dataclass
class Ref:
    """A url(…), or a string argument of image-set(): css[start:end] is replaced whole."""
    start: int
    end: int
    value: str
    line: int
    url: bool


def _unescape(s: str) -> str:
    return ESCAPE_RE.sub(lambda m: chr(min(int(m[1], 16), 0x10FFFF) or 0xFFFD) if m[1] else "" if m[2] else m[3], s)


def _unquote(s: str) -> str:
    if s and s[0] in "\"'":
        s = s[1:-1] if len(s) > 1 and s[-1] == s[0] else s[1:]
    return _unescape(s)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ") + '"'


def scan(css: str) -> tuple[list[Ref], list[int]]:
    """Every url() and image-set() string outside comments and strings, and the line of every @import."""
    refs, imports, stack, in_import = [], [], [], False
    line = lambda m: css.count("\n", 0, m.start()) + 1  # noqa: E731
    for m in TOKEN_RE.finditer(css):
        kind, text = m.lastgroup, m.group()
        if kind == "url" and not in_import:
            refs.append(Ref(m.start(), m.end(), _unquote(text[4:-1].strip()), line(m), True))
        elif kind == "string" and stack and stack[-1] in IMAGE_SETS and not in_import:
            refs.append(Ref(m.start(), m.end(), _unquote(text), line(m), False))
        elif kind == "func":
            stack.append(text[:-1].lower())
        elif kind == "open":
            stack.append("")
        elif kind == "close" and stack:
            stack.pop()
        elif kind == "at" and text.lower() == "@import":
            imports.append(line(m))
            in_import = True
        elif kind == "end":
            in_import = False
    return refs, imports


def rewrite(css: str, refs: list[Ref], fn) -> str:
    """Replace each ref with fn(ref), quoted; None leaves it as written."""
    out, pos = [], 0
    for r in refs:
        new = fn(r)
        if new is None:
            continue
        out += [css[pos:r.start], f"url({_quote(new)})" if r.url else _quote(new)]
        pos = r.end
    return "".join(out) + css[pos:]


def target(value: str) -> tuple[str | None, str]:
    """A url() value → (relative file to inline, #fragment to keep). (None, "")
    leaves it alone (data:, #id). Raises ValueError when it can't be inlined."""
    if not value or value.startswith("#") or value[:5].lower() == "data:":
        return None, ""
    if SCHEME_RE.match(value):
        raise ValueError(f"remote url({value}): reports are self-contained; save the file next to the CSS "
                         "and reference it relatively")
    if value.startswith("/"):
        raise ValueError(f"url({value}): root-relative paths aren't supported; use a path relative to the CSS file")
    path, _, frag = value.partition("#")
    path = path.split("?", 1)[0]
    ext = path.rpartition(".")[2].lower() if "." in path else ""
    if ext not in MIME:
        raise ValueError(f"url({value}): can't inline .{ext} files (fonts and images only: {', '.join(MIME)})"
                         if ext else f"url({value}): no file extension, so no known type to inline it as")
    return path, f"#{frag}" if frag else ""


def code_only(css: str) -> str:
    """The CSS without comments, strings or url() contents: what checks should look at."""
    return STRIP_RE.sub("", rewrite(css, scan(css)[0], lambda r: ""))


def without_media(css: str, word: str) -> str:
    """code_only() CSS minus every @media block whose condition mentions word."""
    while m := re.search(rf"@media[^{{;]*{word}[^{{;]*\{{", css, re.I):
        depth, j = 1, m.end()
        while j < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        css = css[:m.start()] + css[j:]
    return css


def layered(theme: list[str], modules: list[str]) -> str:
    """rapport's own CSS in cascade layers: anything unlayered after it (theme.css) wins regardless of specificity."""
    return f"@layer {','.join(LAYERS)};\n" + "".join(
        f"@layer {name}{{\n" + "\n".join(parts) + "\n}\n" for name, parts in zip(LAYERS, (theme, modules)))


def style_safe(css: str) -> str:
    return STYLE_END_RE.sub(r"<\\/\1", css)
