"""`rapport modules list|info|new|check` and `rapport gallery`."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import jinja2

from . import BUILTIN_MODULES
from . import config as config_mod
from .brief import _param_line, module_entry
from .build import build
from .registry import KINDS, NAME_RE, Module
from .util import RapportError, did_you_mean

# opaque literal colours; translucent rgba()/hsla() overlays and shadows are fine
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgb\(|\bhsl\(")
TOKEN_RE = re.compile(r"var\(--rp-([a-z0-9-]+)")


def origin(mod: Module) -> str:
    return "built-in" if mod.builtin else str(mod.dir)


def cli(args, cfg) -> int:
    action = args.action
    if action == "list":
        return do_list(cfg, args.kind)
    if action == "info":
        return do_info(cfg, _need_name(args))
    if action == "new":
        return do_new(cfg, args)
    if action == "check":
        return do_check(cfg, args.name)
    raise RapportError(f"unknown action {action}")


def _need_name(args) -> str:
    if not args.name:
        raise RapportError(f"usage: rapport modules {args.action} <name>")
    return args.name


def _get(cfg, name: str) -> Module:
    mod = cfg.registry.get(name)
    if mod is None:
        raise RapportError(f"no module '{name}'{did_you_mean(name, cfg.registry.names())} "
                           "(see `rapport modules list`)")
    return mod


# ── list / info ─────────────────────────────────────────────────────────────── #
def do_list(cfg, kind=None) -> int:
    reg = cfg.registry
    for k in KINDS:
        if kind and k != kind:
            continue
        mods = reg.by_kind(k)
        if not mods:
            continue
        print(f"{k}:")
        for m in mods:
            flags = []
            if not m.builtin:
                flags.append("custom" + (" (overrides built-in)" if any(
                    Path(s).is_relative_to(BUILTIN_MODULES.resolve()) for s in m.shadows) else ""))
            if m.has_code:
                flags.append("runs prepare.py")
            if not cfg.enabled(m.name):
                flags.append("disabled")
            if m.when != "always" and m.kind == "feature":
                flags.append(f"{m.when} only")
            extra = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {m.name:<14} {m.summary[:86]}{'…' if len(m.summary) > 86 else ''}{extra}")
    for e in reg.errors:
        print(f"warning: {e}")
    print("\nroots (nearest first): " + "; ".join(str(r) for r in reg.roots if r.is_dir()))
    return 0


def do_info(cfg, name: str) -> int:
    mod = _get(cfg, name)
    print(f"# {mod.name} ({mod.kind}) — {origin(mod)}")
    for line in module_entry(mod, with_example=False)[1:]:
        print(line)
    if mod.config:
        print("config: " + "; ".join(_param_line(k, v) for k, v in mod.config.items()))
    if mod.kind == "theme":
        print("tokens: " + ", ".join(mod.tokens))
    if mod.kind == "feature":
        print(f"when: {mod.when}" + (" · required" if mod.required else ""))
    current = cfg.module(mod.name)
    if current:
        print(f"current [modules.{mod.name}]:")
        for k, v in current.items():
            src = cfg.origins.get(f"modules.{mod.name}.{k}", "defaults")
            print(f"  {k} = {json.dumps(v, ensure_ascii=False)[:160]}    # {src}")
    files = [f for f in ("module.toml", "template.html", "style.css", "script.js", "prepare.py", "example.md")
             if (mod.dir / f).is_file()]
    print("files: " + ", ".join(files))
    if mod.shadows:
        print("overrides: " + ", ".join(map(str, mod.shadows)))
    if mod.example_path:
        print("example:")
        for line in mod.example_path.read_text(encoding="utf-8").rstrip().split("\n"):
            print("    " + line)
    return 0


# ── new ─────────────────────────────────────────────────────────────────────── #
SCAFFOLD = {
    "block": {
        "module.toml": '''name = "{name}"
kind = "block"
summary = "One line: what it is and when to use it (shown in `rapport brief`)."
# How the text between the ::: lines is read: markdown | text | toml | none
body = "markdown"
# Text after the name on the ::: line fills this param (delete if unused)
positional = "title"

# Per-use params: ::: {name} Title {{tone = "warning"}}. Defaults can also be set
# for every use at [modules.{name}] in any config file.
[params.title]
type = "string"
default = ""
doc = "Shown above the block."

# [params.tone]
# type = "enum"
# choices = ["note", "warning"]
# default = "note"
# doc = "Colour."

# Settings that aren't per-use go under [config.*] (also set at [modules.{name}]).
''',
        "template.html": '''{{# One root element: the renderer adds data-rp-* anchors and class rp-m-{name} to it.
   Available: node.params, node.body (rendered Markdown), node.children (child views
   with .html/.module/.params), node.text (raw body), node.attrs, cfg ([modules.{name}]),
   meta, ctx.markdown(text), ctx.inline(text), ctx.highlight(code, lang), ctx.asset(path). #}}
<div class="rp-{name}">
{{% if node.params.title %}}<p class="rp-{name}-title">{{{{ node.params.title }}}}</p>{{% endif %}}
{{{{ node.body }}}}
</div>
''',
        "style.css": '''/* {name}: style through theme tokens (var(--rp-*)) only, so every theme applies. */
.rp-{name}{{margin:1.2em 0;padding:12px 16px;border:1px solid var(--rp-line);border-radius:var(--rp-radius);
  background:var(--rp-surface)}}
.rp-{name}-title{{font:600 13px var(--rp-sans);color:var(--rp-ink-soft);margin:0 0 6px}}
.rp-{name}>:last-child{{margin-bottom:0}}
''',
        "example.md": '''::: {name} Example title
Some **Markdown** inside the block.
:::
''',
    },
    "inline": {
        "module.toml": '''name = "{name}"
kind = "inline"
summary = "One line: what it is (shown in `rapport brief`). Syntax: :{name}[text]{{params}}"

[params.tone]
type = "enum"
choices = ["neutral", "accent"]
default = "neutral"
doc = "Colour."
''',
        "template.html": '''{{# One root element. node.body = rendered inline text, node.params = params. #}}
<span class="rp-{name} rp-{name}-{{{{ node.params.tone }}}}">{{{{ node.body }}}}</span>
''',
        "style.css": '''.rp-{name}{{font-family:var(--rp-sans)}}
.rp-{name}-accent{{color:var(--rp-accent)}}
''',
        "example.md": '''Text with an inline :{name}[thing]{{tone = "accent"}} in it.
''',
    },
    "layout": {
        "module.toml": '''name = "{name}"
kind = "layout"
summary = "A page region module. Place it with [layout] top/side/bottom = [..., \\"{name}\\"]."

[feedback]
unit = "none"   # chrome; use "block" if readers should comment on it

[config.text]
type = "string"
default = ""
doc = "Example setting, read in the template as cfg.text."
''',
        "template.html": '''{{# Available: meta (title, subtitle, version, …), headings, cfg, config, region. #}}
<div class="rp-{name}">{{{{ cfg.text or meta.title }}}}</div>
''',
        "style.css": '''.rp-{name}{{font:13px var(--rp-sans);color:var(--rp-ink-soft);padding:12px 0}}
''',
    },
    "feature": {
        "module.toml": '''name = "{name}"
kind = "feature"
summary = "Page-level behaviour. Switch off with [modules.{name}] enabled = false."
# always | serve (only in `rapport serve`) | static (only in the built file)
when = "always"
''',
        "script.js": '''/* {name}: Rapport.module(name, {{ init(ctx) }}) runs once per page.
   ctx.config = [modules.{name}], ctx.data = page data. Mark any UI you add with
   data-rp-ui so it is never quoted in feedback and hides when printing. */
Rapport.module("{name}", {{
  init: function (ctx) {{
  }}
}});
''',
        "style.css": '''/* {name} */
''',
    },
}


def scope_dir(args, cfg) -> Path:
    if args.scope == "user":
        return config_mod.config_home() / config_mod.MODULE_DIR
    if args.scope == "report":
        return cfg.report_dir / config_mod.MODULE_DIR
    return config_mod.nearest_project_dir(cfg.report_dir) / config_mod.MODULE_DIR


def do_new(cfg, args) -> int:
    name = _need_name(args)
    if not NAME_RE.match(name):
        raise RapportError("module names are lowercase letters, digits and hyphens, e.g. side-note")
    dest = scope_dir(args, cfg) / name
    if dest.exists():
        raise RapportError(f"{dest} already exists")
    existing = cfg.registry.get(name)
    if existing is not None:
        # same name as an installed module: copy it as the starting point (an override)
        if args.kind and args.kind != existing.kind:
            raise RapportError(f"'{name}' is already a {existing.kind} module; pick another name to make a {args.kind}")
        shutil.copytree(existing.dir, dest)
        print(f"copied {origin(existing)} '{name}' to {dest}; this copy now overrides it (nearest wins)")
        _next_steps(name, existing.kind, dest)
        return 0
    kind = args.kind or "block"
    if kind == "element":
        elements = [m.name for m in cfg.registry.by_kind("element")]
        raise RapportError("element modules override a Markdown element; create one by copying, e.g. "
                           f"`rapport modules new code` (elements: {', '.join(elements)})")
    if kind == "theme":
        base = cfg.registry.get("default")
        shutil.copytree(base.dir, dest)
        mf = dest / "module.toml"
        mf.write_text(mf.read_text(encoding="utf-8").replace('name = "default"', f'name = "{name}"', 1),
                      encoding="utf-8")
        print(f"created theme {dest} (a copy of 'default': change the tokens)")
        _next_steps(name, kind, dest)
        return 0
    files = SCAFFOLD[kind]
    dest.mkdir(parents=True)
    for fname, text in files.items():
        (dest / fname).write_text(text.format(name=name), encoding="utf-8")
    if args.js and "script.js" not in files:
        (dest / "script.js").write_text(
            f'/* {name}: mount(el, ctx) runs for every element this module renders.\n'
            f'   ctx.params = its params, ctx.config = [modules.{name}]. */\n'
            f'Rapport.module("{name}", {{\n  mount: function (el, ctx) {{\n  }}\n}});\n', encoding="utf-8")
    print(f"created {kind} module {dest}")
    _next_steps(name, kind, dest)
    return 0


def _next_steps(name: str, kind: str, dest: Path) -> None:
    use = {
        "block": f"use it in report.md:  ::: {name} Title {{key = \"value\"}} … :::",
        "inline": f"use it in report.md:  :{name}[text]",
        "element": "it now renders every matching Markdown element",
        "layout": f"place it:  [layout] top = [\"header\", \"{name}\"]   (or side / bottom)",
        "theme": f"select it:  [theme] name = \"{name}\"",
        "feature": "it is on by default; switch off with [modules." + name + "] enabled = false",
    }[kind]
    print(f"next: edit {dest}/*, then `rapport modules check {name}`\n      {use}\n"
          "      contract: references/modules.md in the rapport skill")


# ── check ───────────────────────────────────────────────────────────────────── #
def do_check(cfg, name: str | None) -> int:
    reg = cfg.registry
    mods = [_get(cfg, name)] if name else sorted(reg.modules.values(), key=lambda m: (m.kind, m.name))
    theme = reg.get(cfg.get("theme.name", "default")) or reg.get("default")
    tokens = set(theme.tokens) | set(theme.dark_tokens) | {"width"} if theme else set()
    total = 0
    for e in reg.errors:
        print(f"error: {e}")
        total += 1
    for mod in mods:
        problems = check_module(mod, cfg, tokens)
        total += sum(1 for p in problems if p.startswith("error"))
        status = "ok" if not problems else "; ".join(problems)
        print(f"{mod.name:<14} {status}")
    print(f"\n{len(mods)} module(s) checked, {total} error(s)")
    return 1 if total else 0


def check_module(mod: Module, cfg, tokens: set) -> list[str]:
    out = []
    needs_tpl = mod.kind in ("block", "element", "inline", "layout")
    if needs_tpl and not mod.template_path:
        out.append("error: missing template.html")
    if mod.template_path:
        try:
            jinja2.Environment().parse(mod.template_path.read_text(encoding="utf-8"))
        except jinja2.TemplateSyntaxError as e:
            out.append(f"error: template.html:{e.lineno}: {e.message}")
    if mod.css_path and mod.kind != "theme":
        css = mod.css_path.read_text(encoding="utf-8")
        css_nocomment = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        if COLOR_RE.search(css_nocomment):
            out.append("warning: hard-coded colours in style.css; use var(--rp-*) tokens so themes apply")
        local = set(re.findall(r"--rp-([a-z0-9-]+)\s*:", css_nocomment))
        unknown = sorted({t for t in TOKEN_RE.findall(css_nocomment) if t not in tokens | local})
        if unknown:
            out.append("warning: unknown theme tokens " + ", ".join(f"--rp-{t}" for t in unknown))
    if mod.kind in ("block", "inline") and not mod.example_path:
        out.append("warning: no example.md (brief, gallery and check use it)")
    if mod.example_path and mod.kind in ("block", "inline", "element"):
        out += render_example(mod, cfg)
    return out


def _roots_set(cfg) -> str:
    return "modules.paths=" + json.dumps([str(r) for r in cfg.registry.roots if r.is_dir()])


def render_example(mod: Module, cfg) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="rapport-check-") as tmp:
        d = Path(tmp)
        (d / "report.md").write_text(mod.example_path.read_text(encoding="utf-8"), encoding="utf-8")
        if (mod.dir / "assets").is_dir():
            shutil.copytree(mod.dir / "assets", d / "assets")
        try:
            res = build(d, [_roots_set(cfg)], write=False, strict=True)
        except RapportError as e:
            return [f"error: example: {e}"]
        out = [f"error: example: {e.format('example.md')}" for e in res.errors]
        # anchors attach to the template's single root element: more than one is an error
        out += [f"error: {w}" for w in res.warnings if mod.name in w and "root" in w]
        if f'data-rp-module="{mod.name}"' not in res.html:
            out.append("error: example.md never renders this module")
        return out


# ── gallery ─────────────────────────────────────────────────────────────────── #
def gallery(args, cfg) -> int:
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    reg = cfg.registry
    parts = ['+++', 'title = "Module gallery"',
             'subtitle = "Every installed module, rendered from its example.md. Regenerate with `rapport gallery`."',
             'kicker = "rapport"', '+++', '']
    for kind in ("block", "inline", "element"):
        for mod in reg.by_kind(kind):
            if not mod.example_path or not cfg.enabled(mod.name):
                continue
            parts += [f"## {mod.name}", "", f":badge[{kind}] {mod.summary}", ""]
            parts += [mod.example_path.read_text(encoding="utf-8").rstrip(), ""]
            if (mod.dir / "assets").is_dir():
                shutil.copytree(mod.dir / "assets", out / "assets", dirs_exist_ok=True)
    (out / "report.md").write_text("\n".join(parts) + "\n", encoding="utf-8")
    res = build(out, [_roots_set(cfg)] + (args.set or []))
    for m in res.messages():
        print(m)
    print(f"gallery: {res.config.output_path}\nreview it: rapport serve {out}")
    return 0 if res.ok else 1
