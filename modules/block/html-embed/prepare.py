"""Static HTML only: scripts, event handlers and active elements are removed (with a
warning). Every file it loads (src, poster, srcset, href outside links, CSS url()) is
inlined if local and a build error if remote; so is @import, which can't be inlined."""
import re

from markupsafe import Markup

SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/?>", re.I | re.S)
ACTIVE_RE = re.compile(r"<(iframe|object|embed|frame|frameset|applet)\b[^>]*>(?:.*?</\1\s*>)?"
                       r"|<(link|base|meta)\b[^>]*>", re.I | re.S)
HANDLER_RE = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
JS_URL_RE = re.compile(r"""\s+(href|src|action|formaction|xlink:href)\s*=\s*(["']?)\s*javascript:[^"'\s>]*\2""", re.I)


def attr_re(names: str) -> re.Pattern:
    """name="v", name='v' or name=v: groups prefix, quote, quoted value, bare value."""
    return re.compile(r"""(\s(?:%s)\s*=\s*)(?:(["'])(.*?)\2|([^\s"'>]+))""" % names, re.I | re.S)


SRC_RE = attr_re("src|poster|background")
SRCSET_RE = attr_re("srcset")
HREF_RE = attr_re("href|xlink:href")
TAG_RE = re.compile(r"<([a-z][\w:-]*)\b[^>]*>", re.I | re.S)
LINKS = {"a", "area"}                      # their href is a link to follow, not a file to load
IMPORT_RE = re.compile(r"@import\b", re.I)
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

    if IMPORT_RE.search(html):
        raise ValueError("html-embed: @import can't be inlined; put the CSS in the <style> block")

    def attr(m, fn) -> str:
        q = m[2] or ""
        return f"{m[1]}{q}{fn(m[3] if m[2] else m[4])}{q}"

    def srcset(value: str) -> str:          # "a.png 1x, b.png 2x"
        return ", ".join(" ".join([local(c.split()[0])] + c.split()[1:]) for c in value.split(",") if c.strip())

    def tag(m) -> str:
        t = SRC_RE.sub(lambda a: attr(a, local), m[0])
        t = SRCSET_RE.sub(lambda a: attr(a, srcset), t)
        return t if m[1].lower() in LINKS else HREF_RE.sub(lambda a: attr(a, local), t)

    html = TAG_RE.sub(tag, html)
    html = URL_RE.sub(lambda m: f"url({m[1]}{local(m[2])}{m[1]})", html)
    if removed:
        ctx.warn(f"html-embed: removed {removed} script, event handler or active element(s); only static HTML and CSS render")
    return {"html": Markup(html)}
