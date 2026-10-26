"""TeX → SVG with MathJax; surrounding $$…$$ or \\[…\\] are optional."""
from markupsafe import Markup

from rapport_core.external import ToolMissing
from rapport_core.mathjax import tex_to_svg

DELIMS = (("$$", "$$"), ("\\[", "\\]"))


def prepare(node, ctx):
    tex = node.text.strip()
    for a, b in DELIMS:
        if tex.startswith(a) and tex.endswith(b) and len(tex) > len(a) + len(b):
            tex = tex[len(a):-len(b)].strip()
    if not tex:
        raise ValueError("latex needs a formula")
    cfg = ctx.module("latex")
    try:
        svg = tex_to_svg(tex, True, cfg.get("node", "node"), cfg.get("npm", "npm"))
    except ToolMissing as e:
        ctx.warn(f"latex: {e}; showing the TeX source")
        return {"fallback": tex, "tex": tex}
    return {"svg": Markup(svg), "tex": tex}


def confluence(node, ctx, cf):
    tex = node.text.strip()
    for a, b in DELIMS:
        if tex.startswith(a) and tex.endswith(b) and len(tex) > len(a) + len(b):
            tex = tex[len(a):-len(b)].strip()
    return {"tex": tex}
