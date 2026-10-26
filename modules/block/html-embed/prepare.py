"""Static HTML only: scripts, event handlers and active elements are removed (with a
warning), local src= files and CSS url()s are inlined, remote ones are build errors."""
import re

from markupsafe import Markup

SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/?>", re.I | re.S)
ACTIVE_RE = re.compile(r"<(iframe|object|embed|frame|frameset|applet)\b[^>]*>(?:.*?</\1\s*>)?"
                       r"|<(link|base|meta)\b[^>]*>", re.I | re.S)
HANDLER_RE = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
JS_URL_RE = re.compile(r"""\s+(href|src|action|formaction|xlink:href)\s*=\s*(["']?)\s*javascript:[^"'\s>]*\2""", re.I)
SRC_RE = re.compile(r"""(\ssrc\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
URL_RE = re.compile(r"""url\(\s*(["']?)([^"')]+)\1\s*\)""", re.I)
REMOTE_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def prepare(node, ctx):
    html = node.text
    removed = 0
    for rx in (SCRIPT_RE, ACTIVE_RE, HANDLER_RE, JS_URL_RE):
        html, n = rx.subn("", html)
        removed += n

    def local(ref: str) -> str:
        ref = ref.strip()
        if ref.startswith(("data:", "#")):
            return ref
        if REMOTE_RE.match(ref):
            raise ValueError(f"html-embed: remote resource {ref!r}; put the file in assets/ (reports are self-contained)")
        return ctx.asset(ref, node.lines[0])

    html = SRC_RE.sub(lambda m: f"{m[1]}{m[2]}{local(m[3])}{m[2]}", html)
    html = URL_RE.sub(lambda m: f"url({m[1]}{local(m[2])}{m[1]})", html)
    if removed:
        ctx.warn(f"html-embed: removed {removed} script, event handler or active element(s); only static HTML and CSS render")
    return {"html": Markup(html)}
