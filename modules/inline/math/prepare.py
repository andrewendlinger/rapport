"""Inline TeX → SVG with MathJax."""
from markupsafe import Markup

from rapport_core.external import ToolMissing
from rapport_core.mathjax import tex_to_svg


def prepare(node, ctx):
    tex = node.text.strip().strip("$").strip()
    if not tex:
        raise ValueError("math needs a formula")
    cfg = ctx.module(node.module)   # its own name: an `extends` under another name has its own config
    try:
        svg = tex_to_svg(tex, False, cfg.get("node", "node"), cfg.get("npm", "npm"))
    except ToolMissing as e:
        ctx.warn(f"math: {e}; showing the TeX source")
        return {"fallback": tex, "tex": tex}
    return {"svg": Markup(svg), "tex": tex}
