"""Parse "- [x] text" / "- [ ] text" lines into decision items."""
import re

ITEM = re.compile(r"^[-*+]\s+\[([ xX])\]\s+(.*)$")


def prepare(node, ctx):
    items = []
    for n, line in enumerate(node.text.splitlines(), 1):
        if not line.strip():
            continue
        m = ITEM.match(line.strip())
        if not m:
            raise ValueError(f"decisions line {n}: write '- [x] decided' or '- [ ] open', got {line.strip()!r}")
        items.append({"decided": m[1] != " ", "html": ctx.inline(m[2])})
    if not items:
        raise ValueError("decisions needs at least one '- [x] …' or '- [ ] …' line")
    return {"items": items}


def confluence(node, ctx, cf):
    return prepare(node, ctx)
