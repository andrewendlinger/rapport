"""Render PlantUML to light and dark SVGs (plantuml -tsvg -pipe [-darkmode]), cached.
The background is transparent so the diagram sits on the page's own colour."""
import base64


def prepare(node, ctx):
    cfg = ctx.module("plantuml")
    src = node.text.strip("\n")
    if "@start" not in src:
        src = f"@startuml\n{src}\n@enduml"
    exe = ctx.tool(cfg.get("command", "plantuml"))
    if not exe:
        ctx.warn(f"plantuml: {cfg.get('command', 'plantuml')} isn't installed (brew install plantuml); showing the source")
        return {"fallback": src}
    out = {"alt": "PlantUML diagram"}
    for mode, extra in (("light", []), ("dark", ["-darkmode"])):
        argv = [exe, "-tsvg", "-pipe", "-charset", "UTF-8", "-SbackgroundColor=transparent", *extra]
        svg = ctx.cached("plantuml\0" + "\0".join(argv[1:]) + "\0" + src,
                         lambda argv=argv: ctx.run(argv, input=src.encode(), timeout=120))
        out[mode] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    return out


def confluence(node, ctx, cf):
    src = node.text.strip("\n")
    return {"src": src if "@start" in src else f"@startuml\n{src}\n@enduml", "options": None}
