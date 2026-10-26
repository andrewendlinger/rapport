"""Render a .drawio page to light and dark SVGs with the draw.io CLI (cached by file content)."""
import base64
from html import escape
import hashlib
import tempfile
from pathlib import Path

from markupsafe import Markup


def prepare(node, ctx):
    cfg = ctx.module("drawio")
    if not node.params["src"]:
        if not node.params["confluence_name"]:
            raise ValueError("drawio needs a .drawio file (src), or confluence_name for a diagram that lives in Confluence")
        return {"remote": True}
    path = ctx.file(node.params["src"])
    data = path.read_bytes()
    exe = ctx.tool(cfg.get("command", "drawio"))
    if not exe:
        ctx.warn(f"drawio: {cfg.get('command', 'drawio')} isn't installed (brew install --cask drawio); showing a link")
        return {"fallback": f"draw.io diagram: {node.params['src']}"}
    page = str(node.params["page"])
    digest = hashlib.sha256(data).hexdigest()
    out = {"alt": f"draw.io diagram {path.name}"}
    for mode in ("light", "dark"):
        def produce(mode=mode):
            with tempfile.TemporaryDirectory(prefix="rapport-drawio-") as d:
                i, o = Path(d) / "in.drawio", Path(d) / "out.svg"
                i.write_bytes(data)
                ctx.run([exe, "-x", "-f", "svg", "--theme", mode, "-t", "-b", "10", "--embed-svg-fonts", "false",
                         "-p", page, "-o", o, i], timeout=180)
                if not o.is_file() or not o.stat().st_size:
                    raise ValueError(f"drawio: exporting {path.name} page {page} produced nothing")
                return o.read_bytes()
        svg = ctx.cached(f"drawio\0{mode}\0{page}\0{digest}", produce)
        out[mode] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    return out


def confluence(node, ctx, cf):
    """Always a native draw.io diagram: an embed once the user has inserted it in
    Confluence (confluence_page + confluence_name), until then a placeholder and a
    step for the user. On the page it was inserted on (the report's recorded page)
    that's the native macro itself, elsewhere an embed of it. (Macro Pack's
    draw.io XML is deliberately not used.)"""
    p = node.params
    if p["confluence_page"] and p["confluence_name"]:
        if not cf.has("drawio"):
            cf.warn("drawio: no draw.io app on the target site ([confluence] apps); writing it anyway")
        size = (p["confluence_width"] or None, p["confluence_height"] or None)
        if str(p["confluence_page"]) == cf.page_id():         # inserted on this very page
            return {"out": cf.drawio_macro(p["confluence_page"], p["confluence_name"], p["confluence_revision"] or None,
                                           *size)}
        return {"out": cf.drawio_embed(p["confluence_page"], p["confluence_name"], *size)}
    if not p["src"]:
        raise ValueError("drawio: export needs confluence_page and confluence_name, or the .drawio file (src)")
    name = p["src"].rsplit("/", 1)[-1]
    cf.handover(f"insert {p['src']} where the page says 'rapport draw.io: {p['src']}' as a native draw.io diagram "
                f"(/draw.io Diagram, Extras > Edit Diagram, paste the file), name it {name}; then the agent sets "
                "confluence_page and confluence_name on the block")
    return {"out": Markup(f'<div data-type="panel-note"><p>📐 rapport draw.io: {escape(p["src"])} '
                          "(insert the diagram here)</p></div>")}
