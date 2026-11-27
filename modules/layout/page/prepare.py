"""The page's icon, as <link rel="icon"> and as the apple-touch-icon Safari's Add to Dock takes.

`rapport` is favicon.png beside this file: favicon.svg drawn at 256 px (rendered with Chrome,
transparent ground). The page carries the PNG because Safari ignores SVG touch icons."""

from pathlib import Path

from rapport_core.util import is_image_file

OWN = Path(__file__).with_name("favicon.png")


def prepare(node, ctx):
    value = ctx.module(node.module).get("favicon") or ""
    where = f"modules.{node.module}.favicon"
    if value == "rapport":
        return {"favicon": ctx.asset(str(OWN), where=where)}
    if value and not is_image_file(value):
        ctx.warn(f"{where} = {value!r}: give an image file, `rapport`, or empty for none")
        return {}
    return {"favicon": ctx.asset(value, where=where)} if value else {}
