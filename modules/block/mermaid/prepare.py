"""Render the Mermaid source to light and dark SVGs with mmdc (cached)."""
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

# browsers mmdc (puppeteer) can drive when no puppeteer_config says which
BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]


# Chrome must not touch the macOS keychain (it would ask the user) or need a password store
QUIET_ARGS = ["--use-mock-keychain", "--password-store=basic"]


def find_browser():
    if os.environ.get("PUPPETEER_EXECUTABLE_PATH"):
        return os.environ["PUPPETEER_EXECUTABLE_PATH"]
    for b in BROWSERS:
        path = b if os.path.isabs(b) and os.access(b, os.X_OK) else shutil.which(b)
        if path:
            return path
    return None


def prepare(node, ctx):
    cfg = ctx.module("mermaid")
    src = node.text.strip("\n")
    exe = ctx.tool(cfg.get("command", "mmdc"))
    if not exe:
        ctx.warn(f"mermaid: {cfg.get('command', 'mmdc')} isn't installed (brew install mermaid-cli); showing the source")
        return {"fallback": src}
    pcfg = cfg.get("puppeteer_config") or ""
    browser = None if pcfg else find_browser()
    out = {"alt": "Mermaid diagram"}
    for mode in ("light", "dark"):
        theme = cfg.get(f"theme_{mode}", "default" if mode == "light" else "dark")

        def produce(theme=theme):
            with tempfile.TemporaryDirectory(prefix="rapport-mmd-") as d:
                i, o = Path(d) / "in.mmd", Path(d) / "out.svg"
                i.write_text(src, encoding="utf-8")
                argv = [exe, "-q", "-i", i, "-o", o, "-t", theme, "-b", "transparent"]
                if pcfg:
                    argv += ["-p", pcfg]
                else:                       # our own config: the found browser, kept quiet
                    pc = Path(d) / "puppeteer.json"
                    conf = {"headless": "shell", "args": QUIET_ARGS}
                    if browser:
                        conf["executablePath"] = browser
                    pc.write_text(json.dumps(conf), encoding="utf-8")
                    argv += ["-p", pc]
                ctx.run(argv, timeout=180)
                return o.read_bytes()
        svg = ctx.cached(f"mermaid\0{theme}\0{pcfg}\0{src}", produce)
        out[mode] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    return out


def confluence(node, ctx, cf):
    return {"src": node.text.strip("\n"), "options": {"mermaid": {"linksNewTab": True}}}
