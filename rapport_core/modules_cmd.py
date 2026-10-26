"""`rapport modules list|info|new|check|diff|state` and `rapport gallery`."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import jinja2

from . import BUILTIN_MODULES, __version__
from . import config as config_mod
from . import css as css_mod
from .brief import _param_line, module_entry
from .build import build
from . import palette
from .registry import KINDS, NAME_BOUND, NAME_RE, Module, hidden
from .render import CORE_TOKENS, Renderer
from .routes import load_routes
from .store import module_store
from .util import RapportError, did_you_mean, toml_repr, unique

# opaque literal colours; translucent rgba()/hsla() overlays and shadows are fine
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgb\(|\bhsl\(")
TOKEN_RE = re.compile(r"var\(--rp-([a-z0-9-]+)")
IMPORTANT_RE = re.compile(r"!\s*important", re.I)
FONT_RE = re.compile(r"(?<![-\w])(font-size|font)\s*:([^;{}]*)", re.I)
AT_PRELUDE_RE = re.compile(r"@[-\w]+[^{};]*")      # @media (max-width:640px) …: conditions, not sizes


def scope(mod: Module, cfg) -> str:
    """Where a module lives, in a word where there is one (built-in, user, report, project); else its folder."""
    if mod.builtin:
        return "built-in"
    user = config_mod.user_paths(strict=False).modules_dir
    if user and mod.dir.is_relative_to(user.resolve()):
        return "user"
    if mod.dir.is_relative_to((cfg.report_dir / config_mod.MODULE_DIR).resolve()):
        return "report"
    return "project" if config_mod.MODULE_DIR in mod.dir.parts else str(mod.dir)


def named(mod: Module, cfg) -> str:
    return f"{mod.name} ({scope(mod, cfg)})"


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
    if action == "diff":
        return do_diff(cfg, _need_name(args))
    if action == "state":
        return do_state(cfg, _need_name(args), args.json)
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
                flags.append("custom" + (" (overrides built-in)" if not m.base and any(
                    Path(s).is_relative_to(BUILTIN_MODULES.resolve()) for s in m.shadows) else ""))
            if m.base:
                flags.append(f"extends {m.base.name} ({'built-in' if m.base.builtin else 'custom'})")
            if m.has_code:
                flags.append("runs prepare.py")
            if m.has_server:
                flags.append("runs server.py")
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
    print(f"# {mod.name} ({mod.kind}) — {mod.origin}")
    if mod.base:
        print("chain: " + " extends ".join(named(m, cfg) for m in mod.chain))
    for line in module_entry(mod, with_example=False)[1:]:
        print(line)
    if mod.config:
        print("config: " + "; ".join(_param_line(k, v) for k, v in mod.config.items()))
    if mod.kind == "theme":
        light, dark = mod.tokens, mod.dark_tokens
        names = list(light) + [k for k in dark if k not in light]
        w = max(map(len, names), default=0)
        print("tokens (each is --rp-<name>; set them in [theme.tokens] / [theme.tokens.dark]):")
        for k in names:
            print(f"  {k:<{w}}  {light.get(k, '-')}" + (f"    dark: {dark[k]}" if k in dark else ""))
    if mod.kind == "feature":
        print(f"when: {mod.when}" + (" · required" if mod.required else ""))
    current = cfg.module(mod.name)
    if current:
        print(f"current [modules.{mod.name}]:")
        for k, v in current.items():
            src = cfg.origin(f"modules.{mod.name}.{k}", v)
            print(f"  {k} = {json.dumps(v, ensure_ascii=False)[:160]}    # {src}")
    if mod.base:
        rows = file_origins(mod, cfg)
        w = max(len(rel) for rel, _ in rows)
        print("files:")
        for rel, how in rows:
            print(f"  {rel:<{w}}  {how}")
    else:
        files = [f for f in ("module.toml", "template.html", "confluence.html", "style.css", "script.js", "prepare.py",
                             "server.py", "example.md") if (mod.dir / f).is_file()]
        print("files: " + ", ".join(files))
    if isinstance(mod.manifest.get("upstream"), dict) and (up := upstream(mod)):
        changed = drift(mod)
        print(f"upstream: made from {named(up, cfg)} at rapport {mod.manifest['upstream'].get('version', '?')}; "
              + (f"changed there since: {', '.join(changed)} (`rapport modules diff {mod.name}`)" if changed
                 else "nothing it replaces has changed there since"))
    if mod.has_server:
        print_routes(mod)
    if mod.shadows:
        print("overrides: " + ", ".join(map(str, mod.shadows)))
    if mod.example_path:
        print("example:")
        for line in mod.example_path.read_text(encoding="utf-8").rstrip().split("\n"):
            print("    " + line)
    return 0


def file_origins(mod: Module, cfg) -> list[tuple[str, str]]:
    """For an extending module: each file, and which module(s) of the chain it comes from."""
    def owner(path: Path) -> Module:
        return next(m for m in mod.chain if path.is_relative_to(m.dir))
    rows = []
    for rel, path in mod.files().items():
        have = [m for m in mod.chain if (m.dir / rel).is_file()]       # nearest first
        if rel == "module.toml":
            how = " on top of ".join(scope(m, cfg) for m in have) + " (merged)"
        elif rel == "style.css" and len(have) > 1:
            how = ", then ".join(scope(m, cfg) for m in reversed(have)) + " (each added after the one before)"
        else:
            replaced = mod.base.file(rel) if mod.inherits(rel) else None
            how = scope(owner(path), cfg) + (" (inherited)" if owner(path) is not mod else
                                             f", replacing {scope(owner(replaced), cfg)}'s" if replaced else "")
        rows.append((rel, how))
    return sorted(rows, key=lambda r: (r[0] != "module.toml", r[0]))


def print_routes(mod: Module) -> None:
    try:
        router = load_routes(mod)
    except RapportError as e:
        print(f"routes: error: {e}")
        return
    print(f"routes (under /api/m/{mod.name}, `rapport serve` only):")
    for r in router.routes:
        print(f"  {r.method:<6} {r.path:<20} {getattr(r.fn, '__name__', '?')}()")
    print(f"state: `rapport modules state {mod.name}` reads it")


# ── state ───────────────────────────────────────────────────────────────────── #
def do_state(cfg, name: str, as_json: bool) -> int:
    """A module's stored state, for the agent: what its describe(state, config) says, else the JSON."""
    mod = _get(cfg, name)
    if not mod.has_server:
        raise RapportError(f"'{name}' has no server.py, so it keeps no state")
    state = module_store(cfg.report_dir, name).read()
    describe = None if as_json else mod.server_hook("describe")    # --json never runs the module's code
    problem = None if as_json else mod.server_error
    if describe:
        try:
            print(str(describe(state, cfg.module(name))).rstrip())
            return 0
        except Exception as e:  # noqa: BLE001 - a module's own code: report it, still show the state
            problem = f"describe() in server.py failed: {type(e).__name__}: {e}"
    if problem:
        print(f"warning: module '{name}' {problem}; here is the raw state", file=sys.stderr)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 1 if problem else 0


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
.rp-{name}-title{{font:600 .75rem var(--rp-sans);color:var(--rp-ink-soft);margin:0 0 6px}}
.rp-{name}>:last-child{{margin-bottom:0}}
''',
        "confluence.html": '''{{# How `rapport export confluence` writes it: Confluence's HTML (see references/confluence.md).
   cf.macro(...), cf.extension(...), cf.has("macro-pack"), cf.warn(...) help; this keeps the content. #}}
{{% if node.params.title %}}<p><strong>{{{{ node.params.title }}}}</strong></p>{{% endif %}}
{{{{ node.body }}}}
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
        "confluence.html": '''{{# How `rapport export confluence` writes it (see references/confluence.md). #}}
{{{{ node.body }}}}
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
        "style.css": '''.rp-{name}{{font:.75rem var(--rp-sans);color:var(--rp-ink-soft);padding:12px 0}}
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


# `modules new <name> --server`: a feature with routes; a small working sign-off box
SERVER_SCAFFOLD = {
    "module.toml": '''name = "{name}"
kind = "feature"
summary = "Reviewers sign off at the end of the report."
# serve: only in `rapport serve`, where server.py's routes answer
when = "serve"
''',
    "server.py": '''"""{name}: routes under /api/m/{name}/ while `rapport serve` runs; script.js calls
them with Rapport.api("{name}", path, opts). Contract: "server.py" in references/modules.md.
Handlers may run in several threads at once: keep state in req.store, not in globals."""

import time


def routes(r):
    r.get("/", show)
    r.post("/", sign)                   # body: {{"name": "Ada"}}
    r.delete("/{{who}}", unsign)          # the path param arrives as req.params["who"]


def show(req):
    return {{"signed": req.store.read().get("signed", [])}}


def sign(req):
    who = str(req.body.get("name") or "").strip()[:80]
    if not who:
        raise req.Refused("type your name first")           # → 400; the page shows the message
    with req.store.edit() as state:                        # locked; saved when the block ends
        signed = [s for s in state.get("signed", []) if s["name"] != who]
        state["signed"] = signed + [{{"name": who, "version": req.version, "at": time.strftime("%Y-%m-%d %H:%M")}}]
    return {{"signed": state["signed"]}}


def unsign(req):
    who = req.params["who"]
    with req.store.edit() as state:
        signed = state.get("signed", [])
        if not any(s["name"] == who for s in signed):
            raise req.NotFound(f"{{who}} has not signed off")   # → 404; nothing is saved
        state["signed"] = [s for s in signed if s["name"] != who]
    return {{"signed": state["signed"]}}


def describe(state, config):
    """What `rapport modules state {name}` prints for the agent."""
    signed = state.get("signed", [])
    if not signed:
        return "Nobody has signed off yet."
    return "\\n".join(f"{{s['name']}} signed off on v{{s['version']}} at {{s['at']}}" for s in signed)
''',
    "script.js": '''/* {name}: a sign-off line at the end of the report. Rapport.api("{name}", path, opts)
   calls server.py; "{name}:changed" fires when its state changes (this tab, another, the CLI). */
Rapport.module("{name}", {{
  init: function () {{
    var box = document.createElement("section");
    box.className = "rp-{name}";
    box.setAttribute("data-rp-ui", "");           // chrome: never part of a feedback quote
    box.innerHTML = '<p></p><input type="text" placeholder="Your name" aria-label="Your name">' +
      '<button type="button">Sign off</button>';
    var line = box.querySelector("p"), input = box.querySelector("input");
    (document.querySelector(".rp-content") || document.body).appendChild(box);

    function show(res) {{
      line.textContent = res.signed.length ? "Signed off: " + res.signed.map(function (s) {{
        return s.name + " (v" + s.version + ")";
      }}).join(", ") : "Nobody has signed off yet.";
    }}
    function fail(err) {{ line.textContent = err.message; }}    // the server's message
    function load() {{ Rapport.api("{name}", "/").then(show, fail); }}

    box.querySelector("button").addEventListener("click", function () {{
      Rapport.api("{name}", "/", {{ body: {{ name: input.value }} }}).then(show, fail);
    }});
    Rapport.on("{name}:changed", load);
    load();
  }}
}});
''',
    "style.css": '''/* {name}: theme tokens only (var(--rp-*)), so every theme applies */
.rp-{name}{{margin:2.5em 0 1em;padding:12px 16px;border:1px solid var(--rp-line);border-radius:var(--rp-radius);
  display:flex;flex-wrap:wrap;gap:8px;align-items:center;font:14px var(--rp-sans)}}
.rp-{name} p{{flex:1 1 100%;margin:0;color:var(--rp-ink-soft)}}
@media print{{.rp-{name}{{display:none}}}}
''',
}


def scope_dir(args, cfg) -> Path:
    if args.scope == "user":
        d = config_mod.user_paths().modules_dir
        if d is None:
            raise config_mod.no_user_config()
        return d
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
    if existing is not None and args.extends not in (None, name):
        raise RapportError(f"'{name}' is already installed, and a module of that name overrides it; "
                           f"pick another name for a module based on '{args.extends}'")
    # an installed name: an override of it; --extends, or a new theme: a module based on another
    base = name if existing else args.extends or ("default" if args.kind == "theme" else None)
    if base:
        return new_based_on(cfg, args, name, base, dest)
    if args.copy:
        raise RapportError(f"--copy copies an installed module, and there's no '{name}'"
                           f"{did_you_mean(name, cfg.registry.names())}")
    kind = args.kind or ("feature" if args.server else "block")
    if args.server and kind != "feature":
        raise RapportError(f"--server scaffolds a feature module; to give a {kind} module routes, add a "
                           "server.py to it by hand (\"server.py\" in references/modules.md)")
    if kind == "element":
        elements = [m.name for m in cfg.registry.by_kind("element")]
        raise RapportError("element modules render a Markdown element; override one by name, e.g. "
                           f"`rapport modules new code` (elements: {', '.join(elements)})")
    files = SERVER_SCAFFOLD if args.server else SCAFFOLD[kind]
    dest.mkdir(parents=True)
    for fname, text in files.items():
        (dest / fname).write_text(text.format(name=name), encoding="utf-8")
    if args.js and "script.js" not in files:
        (dest / "script.js").write_text(
            f'/* {name}: mount(el, ctx) runs for every element this module renders.\n'
            f'   ctx.params = its params, ctx.config = [modules.{name}]. */\n'
            f'Rapport.module("{name}", {{\n  mount: function (el, ctx) {{\n  }}\n}});\n', encoding="utf-8")
    print(f"created {kind} module {dest}" + (" with server routes" if args.server else ""))
    _next_steps(name, kind, dest, args.server)
    return 0


def new_based_on(cfg, args, name: str, base_name: str, dest: Path) -> int:
    """A thin module that extends base_name, or with --copy a whole copy of it."""
    base = cfg.registry.get(base_name)
    if base is None:
        raise RapportError(f"no module '{base_name}' to extend{did_you_mean(base_name, cfg.registry.names())} "
                           "(see `rapport modules list`)")
    same = name == base_name
    if args.server:
        raise RapportError(f"'{name}' is already installed; --server only scaffolds new modules (pick another name)"
                           if same else "--server scaffolds a module of its own; it doesn't combine with --extends")
    if args.kind and args.kind != base.kind:
        raise RapportError(f"'{base_name}' is a {base.kind} module; " + (f"pick another name to make a {args.kind}"
                           if same else "a module based on it is one too (leave out --kind)"))
    if same and (there := _root_index(cfg, base.dir)) is not None and there < (_root_index(cfg, dest) or 0):
        raise RapportError(f"'{name}' in {base.dir} comes first in the module search, so a module in {dest.parent} "
                           "would never be used; pick a nearer --scope, or change that one")
    if not same and base.kind == "element":
        raise RapportError("an element module renders the Markdown element it is named after, so under a new name "
                           f"it would render nothing; override it under its own: `rapport modules new {base_name}`")
    if args.copy:
        if not same:
            raise RapportError("--copy copies a module under its own name; for a new module based on "
                               f"'{base_name}', leave --copy out and copy in only the files you change")
        if base.base:
            raise RapportError(f"'{name}' in {base.dir} extends {named(base.base, cfg)}, so it isn't one folder to "
                               "copy; leave --copy out for a thin override, or copy files into that one")
        copy_module(base, dest)
        print(f"copied {named(base, cfg)} to {dest}; this copy now overrides it (nearest wins), and "
              "`rapport modules check` warns when the original changes")
        _next_steps(name, base.kind, dest)
        return 0
    renamed = write_thin(cfg, name, base, dest)
    inherited = {rel: p for rel, p in base.files().items() if rel != "module.toml" and rel not in renamed}
    print(f"created {dest}: " + (f"overrides {named(base, cfg)}" if same else f"a {base.kind} based on "
                                 f"{named(base, cfg)}") + ", inheriting every file it doesn't have")
    if renamed:
        print(f"  {' and '.join(renamed)}: copied, and renamed to '{name}' (they name the module inside)")
    for folder in unique(p.parent for p in inherited.values()):
        print(f"  inherits from {folder}: " + ", ".join(rel for rel, p in inherited.items() if p.parent == folder))
    _next_steps(name, base.kind, dest, thin=True)
    return 0


def _root_index(cfg, path: Path) -> int | None:
    """Which search root a module folder is in (0: nearest)."""
    path = path.parent.resolve() / path.name
    return next((i for i, r in enumerate(cfg.registry.roots) if path.is_relative_to(r)), None)


def write_thin(cfg, name: str, base: Module, dest: Path) -> list[str]:
    """module.toml with `extends`; under a new name also script.js and example.md, which name the
    module inside, renamed. Returns those."""
    renamed = []
    dest.mkdir(parents=True)
    if name != base.name:
        for rel in NAME_BOUND:
            if src := base.file(rel):
                (dest / rel).write_text(rename(src.read_text(encoding="utf-8"), rel, base.name, name),
                                        encoding="utf-8")
                renamed.append(rel)
    (dest / "module.toml").write_text(thin_manifest(cfg, name, base, renamed), encoding="utf-8")
    return renamed


def rename(text: str, rel: str, old: str, new: str) -> str:
    """A base's script.js or example.md, made to name another module."""
    q = re.escape(old)
    if rel == "script.js":
        # Rapport.module("old", …), .api("old", …), .config("old"), and "old:changed"-style events
        text = re.sub(rf"""(Rapport\.(?:module|api|config)\(\s*)(["']){q}\2""", rf"\g<1>\g<2>{new}\g<2>", text)
        return re.sub(rf"""(["']){q}:""", rf"\g<1>{new}:", text)
    text = re.sub(rf"^(\s*:{{3,}}\s*){q}(?![\w-])", rf"\g<1>{new}", text, flags=re.M)      # ::: old
    return re.sub(rf"(?<![\w:-]):{q}\[", f":{new}[", text)                                   # :old[text]


def thin_manifest(cfg, name: str, base: Module, renamed: list[str]) -> str:
    files = {rel: p for rel, p in base.files().items() if rel != "module.toml"}
    inherited = unique(rel.split("/")[0] + ("/" if "/" in rel else "") for rel in files if rel not in renamed)
    lines = [f"# {'Overrides' if name == base.name else 'A module based on'} {named(base, cfg)}: it inherits "
             "everything not set here.",
             "# These keys go on top of the base's module.toml, and tables merge key by key"]
    knobs = {**{("params", k): v for k, v in base.params.items()}, **{("config", k): v for k, v in base.config.items()}}
    if knobs:
        (section, key), spec = next(iter(knobs.items()))
        lines += [f"# (`rapport modules info {base.name}` lists them), so changing one default takes two lines:",
                  f"#   [{section}.{key}]", f"#   default = {toml_repr(spec.get('default', ''))}"]
    else:
        lines[-1] += "."
    lines += [f"# Inherited files: {', '.join(inherited) or 'none'}. To change one, copy it into this",
              "# folder and edit it: from then on it is yours. Except style.css: yours is added after",
              "# the base's, so write only the rules you change.",
              f'name = "{name}"', f'extends = "{base.name}"']
    if name != base.name:
        lines.append('summary = "One line: what it is and when to use it (shown in `rapport brief`)."')
    if base.kind == "theme":
        lines += ["", f"# Set only the tokens you change; `rapport modules info {base.name}` lists them.",
                  "[tokens]", "", "[tokens.dark]"]
    return "\n".join(lines) + "\n" + stamp(files)


# ── provenance ──────────────────────────────────────────────────────────────── #
UPSTREAM_TABLE_RE = re.compile(r"^\s*\[\s*upstream\s*(?:\.\s*files\s*)?\]")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def stamp(files: dict[str, Path]) -> str:
    """[upstream]: the rapport version and a hash of each file of what a module is made from."""
    lines = ["", "# What this module was made from: `rapport modules check` warns when one of these files",
             "# changes there, and `rapport modules diff` shows how (and how to update this).",
             "[upstream]", f'version = "{__version__}"', "", "[upstream.files]"]
    lines += [f'{json.dumps(rel, ensure_ascii=False)} = "{file_hash(p)}"' for rel, p in sorted(files.items())]
    return "\n".join(lines) + "\n"


def copy_module(mod: Module, dest: Path) -> None:
    """The whole folder (no __pycache__), its module.toml stamped with [upstream]."""
    shutil.copytree(mod.dir, dest, ignore=lambda d, names: [n for n in names if hidden(n)])
    mf = dest / "module.toml"
    mf.write_text(drop_upstream(mf.read_text(encoding="utf-8")).rstrip("\n") + "\n" + stamp(mod.files()),
                  encoding="utf-8")


def drop_upstream(text: str) -> str:
    """module.toml without its [upstream] tables and the comment over them (a copy of a copy)."""
    out, skip = [], False
    for line in text.split("\n"):
        if UPSTREAM_TABLE_RE.match(line):
            skip = True
            while out and out[-1].startswith("#"):
                out.pop()
            continue
        if skip and line.lstrip().startswith("["):
            skip = False
        if not skip:
            out.append(line)
    return "\n".join(out)


def upstream(mod: Module) -> Module | None:
    """What a module was made from: what it extends, else what it overrides (the next of its name down)."""
    return mod.base or mod.below


def drift(mod: Module) -> list[str]:
    """Files of its upstream that changed since [upstream] recorded them, among those this module
    has its own copy of (a copy: all): "template.html", "server.py (new)", "example.md (gone)"."""
    rec, up = mod.manifest.get("upstream"), upstream(mod)
    then = rec.get("files") if isinstance(rec, dict) else None
    if up is None or not isinstance(then, dict):
        return []
    now = {rel: file_hash(p) for rel, p in up.files().items()}
    own = mod.own_files()
    # an override's style.css adds to the base's, and its module.toml goes on top: neither replaces anything
    names = [r for r in own if r not in ("module.toml", "style.css")] if mod.base else sorted(set(then) | set(now))
    out = []
    for rel in names:
        if then.get(rel) == now.get(rel) or (rel in own and now.get(rel) == file_hash(own[rel])):
            continue
        out.append(rel + (" (new)" if rel not in then else " (gone)" if rel not in now else ""))
    return out


def check_upstream(mod: Module, cfg) -> list[str]:
    rec = mod.manifest.get("upstream")
    if not isinstance(rec, dict):
        return []
    up = upstream(mod)
    if up is None:
        return [f"warning: [upstream] records what this was made from, but there's no other '{mod.name}' "
                "below it to compare with"]
    changed = drift(mod)
    if not changed:
        return []
    return [f"warning: {named(up, cfg)} has changed since this {'override' if mod.base else 'copy'} was made "
            f"(rapport {rec.get('version', '?')}): {', '.join(changed)}; compare: `rapport modules diff {mod.name}`"]


def _next_steps(name: str, kind: str, dest: Path, server: bool = False, thin: bool = False) -> None:
    use = {
        "block": f"use it in report.md:  ::: {name} Title {{key = \"value\"}} … :::",
        "inline": f"use it in report.md:  :{name}[text]",
        "element": "it now renders every matching Markdown element",
        "layout": "it is the page shell (`[layout] page`); place [layout.regions] in its template as {{ regions.<name> }}"
                  if name == "page" else f"place it:  [layout] top = [\"header\", \"{name}\"]   (or side / bottom)",
        "theme": f"select it:  [theme] name = \"{name}\"",
        "feature": "it is on by default; switch off with [modules." + name + "] enabled = false",
    }[kind]
    if server:
        use += f"\n      `rapport serve` answers its routes at /api/m/{name}/; `rapport modules state {name}` reads its state"
    edit = ("copy in only the files you change, and edit them there (your style.css is added after the\n"
            f"      base's, so it needs only the rules you change); then `rapport modules check {name}`" if thin
            else f"edit {dest}/*, then `rapport modules check {name}`")
    print(f"next: {edit}\n      {use}\n      contract: references/modules.md in the rapport skill")


# ── check ───────────────────────────────────────────────────────────────────── #
def do_check(cfg, name: str | None) -> int:
    reg = cfg.registry
    mods = [_get(cfg, name)] if name else sorted(reg.modules.values(), key=lambda m: (m.kind, m.name))
    theme = reg.get(cfg.get("theme.name", "default")) or reg.get("default")
    tokens = set(theme.tokens) | set(theme.dark_tokens) | set(CORE_TOKENS) if theme else set()
    tokens |= palette.all_token_names() | {"on-light", "on-dark"}
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
    if mod.kind == "theme" and (missing := [t for t in CORE_TOKENS if t not in mod.tokens]):
        out.append(f"warning: no {' or '.join(missing)} token; the page falls back to "
                   + ", ".join(f"{t} = {CORE_TOKENS[t]}" for t in missing))
    if mod.has_css():
        own = set(mod.tokens) | set(mod.dark_tokens) | set(CORE_TOKENS) if mod.kind == "theme" else tokens
        out += check_css(mod, cfg, own)
    if mod.kind in ("block", "inline", "element") and not mod.file("confluence.html"):
        out.append("warning: no confluence.html (`rapport export confluence` can't export it)")
    if mod.kind in ("block", "inline") and not mod.example_path:
        out.append("warning: no example.md (brief, gallery and check use it)")
    if mod.example_path and mod.kind in ("block", "inline", "element"):
        out += render_example(mod, cfg)
    if mod.has_server:
        out += check_server(mod)
    return out + check_upstream(mod, cfg)


def check_server(mod: Module) -> list[str]:
    """Load server.py and run its routes(r) against a registrar that only records them."""
    try:
        router = load_routes(mod)
    except RapportError as e:
        return [f"error: {e}"]
    if mod.when == "static":
        return ['warning: server.py is never used: modules with when = "static" get no routes']
    return [] if router.routes else ["warning: routes(r) registers no routes"]


def check_css(mod: Module, cfg, tokens: set) -> list[str]:
    r = Renderer(cfg)
    r.module_css(mod)          # url() targets resolve and inline exactly as in a build
    out = [f"error: {d.message.removeprefix(f'module {mod.name!r} ')}" for d in r.errors]
    code = css_mod.code_only("\n".join(p.read_text(encoding="utf-8-sig") for p in mod.css_paths))
    if COLOR_RE.search(code):
        out.append("warning: hard-coded colours in style.css; use var(--rp-*) tokens so themes apply")
    # reduced motion is an accessibility override: the one place a theme may insist
    if mod.kind != "feature" and IMPORTANT_RE.search(css_mod.without_media(code, "prefers-reduced-motion")):
        out.append("warning: !important in style.css can't be overridden by theme.css; drop it "
                   "(cascade layers already put module rules over theme rules)")
    if mod.kind != "feature" and px_font_size(code):
        out.append("warning: font-size in px won't scale with body-size; use rem or em")
    local = set(re.findall(r"--rp-([a-z0-9-]+)\s*:", code))
    optional = set(re.findall(r"var\(--rp-([a-z0-9-]+)\s*,", code))    # with a fallback: themes may set it
    unknown = sorted({t for t in TOKEN_RE.findall(code) if t not in tokens | local | optional})
    if unknown:
        out.append("warning: unknown theme tokens " + ", ".join(f"--rp-{t}" for t in unknown))
    return out


def px_font_size(code: str) -> bool:
    """A px size in font-size or a font shorthand (not its /line-height); @rule conditions don't count."""
    for m in FONT_RE.finditer(AT_PRELUDE_RE.sub("", code)):
        value = m[2] if m[1].lower() == "font-size" else re.sub(r"/\s*[^\s/]+", "", m[2])
        if re.search(r"\dpx\b", value, re.I):
            return True
    return False


def _roots_set(cfg) -> str:
    return "modules.paths=" + json.dumps([str(r) for r in cfg.registry.roots if r.is_dir()])


def render_example(mod: Module, cfg) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="rapport-check-") as tmp:
        d = Path(tmp)
        (d / "report.md").write_text(mod.example_path.read_text(encoding="utf-8"), encoding="utf-8")
        for assets in mod.asset_dirs:
            shutil.copytree(assets, d / "assets", dirs_exist_ok=True)
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


# ── diff ────────────────────────────────────────────────────────────────────── #
def do_diff(cfg, name: str) -> int:
    """A unified diff of the files the module has its own copy of against its upstream's."""
    mod = _get(cfg, name)
    up = upstream(mod)
    if up is None:
        raise RapportError(f"'{name}' ({mod.origin}) neither extends nor overrides another module, "
                           "so there's nothing to compare it with")
    own, theirs, changed = mod.own_files(), up.files(), drift(mod)
    names = [r for r in own if r != "module.toml"] if mod.base else sorted(set(own) | set(theirs))
    print(f"# {named(mod, cfg)} against {named(up, cfg)}")
    for rel in names:
        if mod.base and rel == "style.css":
            print("# style.css: added after the base's, not instead of it (not compared)")
        elif rel not in theirs or rel not in own:
            print(f"# {rel}: only in {named(mod if rel in own else up, cfg)}")
        else:
            try:
                a, b = theirs[rel].read_text(encoding="utf-8"), own[rel].read_text(encoding="utf-8")
            except UnicodeDecodeError:
                if file_hash(theirs[rel]) != file_hash(own[rel]):
                    print(f"# {rel}: binary files differ")
                continue
            for line in difflib.unified_diff(a.splitlines(), b.splitlines(), str(theirs[rel]), str(own[rel]),
                                             lineterm=""):
                print(line)
    if changed:
        mf = mod.dir / "module.toml"
        print(f"\n# Changed in {named(up, cfg)} since this was made: {', '.join(changed)}. Once you have "
              f"merged that,\n# record it as it is now in {mf}:\n[upstream]\nversion = \"{__version__}\"\n\n"
              "[upstream.files]")
        for rel, p in sorted(up.files().items()):
            if not (mod.base and rel == "module.toml"):
                print(f'{json.dumps(rel, ensure_ascii=False)} = "{file_hash(p)}"')
    return 0


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
            for assets in mod.asset_dirs:
                shutil.copytree(assets, out / "assets", dirs_exist_ok=True)
    (out / "report.md").write_text("\n".join(parts) + "\n", encoding="utf-8")
    res = build(out, [_roots_set(cfg)] + (args.set or []))
    for m in res.messages():
        print(m)
    print(f"gallery: {res.config.output_path}\nreview it: rapport serve {out}")
    return 0 if res.ok else 1
