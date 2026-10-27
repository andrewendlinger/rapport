"""code: the Pygments token colours, as the css(ctx) hook. The highlighting itself
(highlight_lines, what ctx.highlight calls) is core, in rapport_core/render.py."""

from rapport_core.render import pygments_css


def css(ctx):
    cfg = ctx.module("code")
    return pygments_css(cfg.get("light_style", "friendly"), cfg.get("dark_style", "github-dark"))
