"""TeX → SVG through MathJax 3 (Node), for the latex and math modules.

MathJax is installed once into the rapport cache (npm, ~50 MB); later builds are
offline. The SVG draws in currentColor, so it follows the page's light/dark theme.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import external

MATHJAX = "mathjax-full@3"
SCRIPT = Path(__file__).with_name("mathjax.cjs")


def node_modules() -> Path:
    return external.cache_root() / "node" / "node_modules"


def ensure_mathjax(npm: str = "npm") -> Path:
    mods = node_modules()
    if (mods / "mathjax-full" / "js" / "mathjax.js").is_file():
        return mods
    exe = external.tool(npm)
    if not exe:
        raise external.ToolMissing("math needs Node.js and npm (brew install node), to install MathJax once")
    prefix = mods.parent
    prefix.mkdir(parents=True, exist_ok=True)
    external.run([exe, "install", "--prefix", str(prefix), "--no-audit", "--no-fund", "--silent", MATHJAX],
                 timeout=300)
    return mods


def tex_to_svg(tex: str, display: bool, node: str = "node", npm: str = "npm") -> str:
    def produce() -> bytes:
        mods = ensure_mathjax(npm)
        exe = external.tool(node)
        if not exe:
            raise external.ToolMissing("math needs Node.js (brew install node)")
        payload = json.dumps({"tex": tex, "display": display}).encode()
        return external.run([exe, str(SCRIPT)], input=payload, timeout=60, env={"NODE_PATH": str(mods)})
    return external.cached(f"mathjax\0{MATHJAX}\0{int(display)}\0{tex}", produce).decode()
