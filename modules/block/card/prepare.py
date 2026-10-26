"""Only web links make cards; show the host."""
from urllib.parse import urlparse


def prepare(node, ctx):
    u = urlparse(node.params["url"])
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"card needs an http(s) URL, got {node.params['url']!r}")
    return {"host": u.netloc.removeprefix("www.")}


def confluence(node, ctx, cf):
    return prepare(node, ctx)
