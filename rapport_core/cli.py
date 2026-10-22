"""rapport command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from . import config as config_mod
from .schema import is_leaf, load_core_tree
from .util import RapportError, state_dir, toml_repr

REPORT_TEMPLATE = """+++
title = {title}
subtitle = ""
date = "{today}"
+++

## Summary

Lead with the conclusions.
"""

VALUE_OPTIONS = {"-C", "--set", "--key", "--kind", "--scope", "--out", "--note", "--quote", "--block", "--action"}

# commands whose first positional is an action: `rapport feedback <dir>` means `feedback list <dir>`
DEFAULT_ACTIONS = {
    "feedback": ("list", {"list", "resolve", "reopen", "add"}),
    "config": ("show", {"show", "validate", "defaults", "paths"}),
    "modules": ("list", {"list", "info", "new", "check"}),
}


# ── helpers ──────────────────────────────────────────────────────────────────── #
def report_dir_of(args) -> Path:
    d = getattr(args, "dir", None) or getattr(args, "C", None) or "."
    p = Path(d).expanduser().resolve()
    if p.is_file():
        p = p.parent
    return p


def load_cfg(args):
    d = report_dir_of(args)
    try:
        cfg, _ = config_mod.load_for_report(d, getattr(args, "set", None))
    except RapportError:
        if (d / "report.md").exists():
            raise
        cfg = config_mod.load(d, None, getattr(args, "set", None))
    return cfg


def print_messages(lines, stream=sys.stderr):
    for line in lines:
        print(line, file=stream)


# ── commands ─────────────────────────────────────────────────────────────────── #
def cmd_init(args) -> int:
    target = args.dir or getattr(args, "C", None)
    if not target:
        raise RapportError("usage: rapport init <dir> [--title TITLE]")
    d = Path(target).expanduser().resolve()
    src = d / "report.md"
    if src.exists() and not args.force:
        raise RapportError(f"{src} exists (use --force to overwrite)")
    d.mkdir(parents=True, exist_ok=True)
    (d / "assets").mkdir(exist_ok=True)
    title = args.title or d.name.replace("-", " ").replace("_", " ").capitalize()
    src.write_text(REPORT_TEMPLATE.format(title=toml_repr(title), today=date.today().isoformat()),
                   encoding="utf-8")
    print(f"created {src}")
    print(f"next: rapport brief {d}   (writing rules + modules), then write report.md")
    return 0


def cmd_build(args) -> int:
    from .build import build
    d = report_dir_of(args)
    if args.watch:
        from .serve import watch_loop
        return watch_loop(d, args.set)
    res = build(d, args.set)
    print_messages(res.messages())
    print(f"{'built' if res.ok else 'built with errors'} {res.config.output_path}")
    return 0 if res.ok else 1


def cmd_serve(args) -> int:
    from . import serve
    d = report_dir_of(args)
    if args.stop:
        return serve.stop(d)
    if args.status:
        return serve.status(d)
    if args.detach:
        return serve.detach(d, args)
    return serve.run(d, args)


def cmd_feedback(args) -> int:
    from . import feedback
    return feedback.cli(args, report_dir_of(args))


def cmd_lint(args) -> int:
    from .lint import lint
    d = report_dir_of(args)
    problems, res = lint(d, args.set)
    print_messages(problems, sys.stdout)
    if not problems:
        print("lint: clean")
    return 1 if problems else 0


def cmd_brief(args) -> int:
    from .brief import brief
    print(brief(load_cfg(args)))
    return 0


def cmd_config(args) -> int:
    if args.action == "defaults":
        print(config_defaults_text())
        return 0
    if args.action == "paths":
        return config_paths(report_dir_of(args), args.json)
    cfg = load_cfg(args)
    if args.action == "validate":
        print_messages(cfg.warnings, sys.stdout)
        print("config: valid" if not cfg.warnings else f"config: {len(cfg.warnings)} problem(s)")
        return 1 if cfg.warnings else 0
    # show
    if args.json:
        print(json.dumps(cfg.data, indent=2, ensure_ascii=False, default=str))   # TOML dates → strings
        return 0
    print(f"# resolved config for {cfg.report_dir}")
    print("# layers: " + " → ".join(layer.name for layer in cfg.layers))
    for line in _flatten(cfg.data, (), cfg.origins if args.origin else None, args.key):
        print(line)
    print_messages([f"warning: {w}" for w in cfg.warnings])
    return 0


def _flatten(data, path, origins, prefix):
    for k, v in data.items():
        p = path + (k,)
        dotted = ".".join(p)
        if isinstance(v, dict) and v and not (p[0] in ("meta",) and len(p) > 1):
            yield from _flatten(v, p, origins, prefix)
            continue
        if prefix and not dotted.startswith(prefix):
            continue
        line = f"{dotted} = {json.dumps(v, ensure_ascii=False)}"
        if origins is not None:
            line += f"    # {_origin_for(origins, dotted, v)}"
        yield line


def _origin_for(origins, dotted, value):
    if dotted in origins:
        return origins[dotted]
    if isinstance(value, list):
        ids = {origins[k] for k in origins if k.startswith(dotted + "[")}
        if ids:
            return " + ".join(sorted(ids))
    return "defaults"


def _status(p: Path | None) -> str:
    return "disabled" if p is None else "exists" if p.exists() else "missing"


def config_paths(report_dir: Path, as_json: bool) -> int:
    """Where config is read from. Reports a bad RAPPORT_CONFIG instead of failing on it."""
    up = config_mod.user_paths(strict=False)
    user = {"config": up.config_file, "learned": up.learned_file, "modules": up.modules_dir,
            "presets": up.presets_dir}
    env = {var: os.environ[var] for var in (config_mod.CONFIG_ENV, config_mod.CONFIG_DIR_ENV, "XDG_CONFIG_HOME")
           if os.environ.get(var)}
    notes = [f"{up.ignored} ignored because {config_mod.CONFIG_ENV} is set"] if up.ignored else []
    if up.config_file is None:
        notes.append(f"{config_mod.CONFIG_ENV} isn't a regular file: no user config, learned rules, modules or presets")
    mine = {p.resolve() for p in (up.config_file, up.learned_file) if p}
    project = [p for d in config_mod.project_dirs_for(report_dir)
               for p in (d / config_mod.PROJECT_FILE, d / config_mod.LEARNED_PROJECT) if p.is_file()]
    state = state_dir(report_dir)
    if as_json:
        print(json.dumps({
            "source": up.source, "env": env, "error": up.error, "notes": notes,
            "user": {k: {"path": str(p) if p else None, "status": _status(p)} for k, p in user.items()},
            "project": [{"path": str(p), "loaded_as_user": p.resolve() in mine} for p in project],
            "state_dir": {"path": str(state), "status": _status(state)},
        }, indent=2))
        return 0
    chosen = f"{up.source}={env[up.source]}" if up.source in env else "default (~/.config/rapport)"
    print(f"user config (chosen by {chosen})")
    for k, p in user.items():
        print(f"  {k:<8} {p or '-'}  [{_status(p)}]")
    if up.error:
        print(f"  error: {up.error} (every other command fails until this is fixed)")
    for n in notes:
        print(f"  note: {n}")
    print(f"project files for {report_dir}")
    for p in project:
        print(f"  {p}" + ("  [same file as the user config: loaded once, as user]" if p.resolve() in mine else ""))
    if not project:
        print("  (none)")
    print(f"state folder\n  {state}  [{_status(state)}]")
    return 0


def config_defaults_text() -> str:
    core = load_core_tree()
    out = ["# rapport configuration reference (generated from config/schema.toml).",
           "# Put any of this in your user config (`rapport config paths` shows where), a",
           "# rapport.toml next to (or above) a report, or the report's +++ front matter.",
           "# Nearer wins.", ""]

    def emit(tree, path):
        leaves = [(k, v) for k, v in tree.items() if is_leaf(v)]
        sections = [(k, v) for k, v in tree.items() if not is_leaf(v) and isinstance(v, dict)]
        if leaves and path:
            out.append(f"[{'.'.join(path)}]")
        for k, spec in leaves:
            doc = spec.get("doc", "")
            extra = []
            if spec.get("merge") == "append":
                extra.append('appends across layers; a "" entry drops what came before it')
            if spec.get("choices"):
                extra.append("one of: " + ", ".join(spec["choices"]))
            out.append(f"# {doc}" + (f" ({'; '.join(extra)})" if extra else ""))
            out.append(f"{config_mod.toml_key(k)} = {toml_repr(spec.get('default'))}")
        if leaves:
            out.append("")
        for k, sub in sections:
            emit(sub, path + (k,))

    emit(core, ())
    out += ["# ── modules ──", "# Each module's settings live at [modules.<name>]; see",
            "# `rapport modules info <name>`. Every module takes `enabled = true|false`."]
    return "\n".join(out)


def cmd_modules(args) -> int:
    from . import modules_cmd
    from .registry import NAME_RE
    # `modules list ./reports/x`: a path is never a module name
    if args.name and not args.dir and not NAME_RE.match(args.name) and Path(args.name).expanduser().is_dir():
        args.dir, args.name = args.name, None
    return modules_cmd.cli(args, load_cfg(args))


def cmd_gallery(args) -> int:
    from . import modules_cmd
    return modules_cmd.gallery(args, load_cfg(args))


def cmd_writing(args) -> int:
    from .lint import writing_add
    return writing_add(report_dir_of(args), args.rule, args.scope, args.avoid)


# ── parser ───────────────────────────────────────────────────────────────────── #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rapport", description="Modular HTML reports with in-page reader feedback.")
    ap.add_argument("--version", action="version", version=f"rapport {__version__}")
    ap.add_argument("-C", metavar="DIR", help="report folder (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, fn, help_, dir_arg=True):
        p = sub.add_parser(name, help=help_, description=help_)
        p.set_defaults(fn=fn)
        p.add_argument("-C", metavar="DIR", default=argparse.SUPPRESS, help="report folder")
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a config key for this run")
        if dir_arg:
            p.add_argument("dir", nargs="?", help="report folder (default: current directory)")
        return p

    p = add("init", cmd_init, "scaffold a report folder (report.md + assets/)")
    p.add_argument("--title")
    p.add_argument("--force", action="store_true")

    p = add("build", cmd_build, "build report.html")
    p.add_argument("--watch", action="store_true", help="rebuild whenever sources change")

    p = add("serve", cmd_serve, "serve the report for review: comments autosave, page live-reloads")
    p.add_argument("--port", type=int, help="pin a port (fails if busy)")
    p.add_argument("--no-open", action="store_true", help="don't open a browser")
    p.add_argument("--detach", action="store_true", help="run in the background and print the URL")
    p.add_argument("--status", action="store_true", help="show the running server for this report")
    p.add_argument("--stop", action="store_true", help="stop the background server")

    p = sub.add_parser("feedback", help="list open reader feedback (agent loop); subcommands: resolve, reopen, add",
                       description="List open reader feedback, or resolve/reopen/add items.")
    p.set_defaults(fn=cmd_feedback, fb_cmd="list")
    p.add_argument("-C", metavar="DIR", default=argparse.SUPPRESS, help="report folder")
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--all", action="store_true", help="include resolved items")
    fsub = p.add_subparsers(dest="fb_cmd", metavar="ACTION")
    fl = fsub.add_parser("list", help="list items (default)")
    fl.add_argument("dir", nargs="?")
    # SUPPRESS: `feedback --json list` must keep the --json given before `list`
    fl.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    fl.add_argument("--all", action="store_true", default=argparse.SUPPRESS)
    fr = fsub.add_parser("resolve", help="mark items done: rapport feedback resolve FB-1 FB-2 --note '…' [dir]")
    fr.add_argument("ids", nargs="+", help="item ids; a trailing folder is the report folder")
    fr.add_argument("--note", default="", help="one line on what changed (shown to the reader)")
    fo = fsub.add_parser("reopen", help="reopen resolved items")
    fo.add_argument("ids", nargs="+", help="item ids; a trailing folder is the report folder")
    fa = fsub.add_parser("add", help="record feedback given in chat")
    fa.add_argument("comment", nargs="?", default="")
    fa.add_argument("dir", nargs="?")
    fa.add_argument("--quote", help="passage the feedback is about (exact text)")
    fa.add_argument("--block", help="block id (from .rapport/blocks.json)")
    fa.add_argument("--action", action="append", default=[], help="action id (repeatable)")
    for sp in (fl, fr, fo, fa):
        sp.add_argument("-C", metavar="DIR", default=argparse.SUPPRESS, help="report folder")

    add("lint", cmd_lint, "check writing rules (avoid-list, paragraph length) and build problems")
    add("brief", cmd_brief, "print the writing rules and module catalog the agent writes against")

    p = add("config", cmd_config, "inspect configuration: show, validate, defaults, paths (where config is read from)",
            dir_arg=False)
    p.add_argument("action", nargs="?", default="show", choices=["show", "validate", "defaults", "paths"])
    p.add_argument("dir", nargs="?", help="report folder (default: current directory)")
    p.add_argument("--origin", action="store_true", help="show which file set each key")
    p.add_argument("--json", action="store_true", help="(show, paths) machine-readable output")
    p.add_argument("--key", help="only keys starting with this prefix")

    p = add("modules", cmd_modules, "discover and author modules", dir_arg=False)
    p.add_argument("action", nargs="?", default="list", choices=["list", "info", "new", "check"])
    p.add_argument("name", nargs="?")
    p.add_argument("dir", nargs="?", help="report folder (default: current directory)")
    p.add_argument("--kind", choices=["block", "element", "inline", "layout", "theme", "feature"])
    p.add_argument("--scope", choices=["report", "project", "user"], default="project")
    p.add_argument("--js", action="store_true", help="(new) include a script.js")

    p = add("gallery", cmd_gallery, "render every module's example into one servable report")
    p.add_argument("--out", default="rapport-gallery", help="output folder (default: ./rapport-gallery)")

    p = sub.add_parser("writing", help="save a learned writing rule: rapport writing add \"…\"")
    p.set_defaults(fn=cmd_writing)
    p.add_argument("-C", metavar="DIR", default=argparse.SUPPRESS)
    p.add_argument("action", choices=["add"])
    p.add_argument("rule")
    p.add_argument("dir", nargs="?", help="report folder (default: current directory)")
    p.add_argument("--scope", choices=["user", "project"], default="user")
    p.add_argument("--avoid", action="store_true", help="a phrase to avoid rather than a rule")
    return ap


def with_default_action(argv: list[str]) -> list[str]:
    """`rapport feedback ./r` → `rapport feedback list ./r` (same for config, modules)."""
    argv, i = list(argv), 0
    while i < len(argv):
        if argv[i] == "-C":
            i += 2
            continue
        if argv[i].startswith("-"):
            i += 1
            continue
        cmd = argv[i]
        if cmd in DEFAULT_ACTIONS:
            default, actions = DEFAULT_ACTIONS[cmd]
            j = i + 1
            while j < len(argv) and argv[j].startswith("-"):     # flags may come first: --all .
                j += 2 if argv[j] in VALUE_OPTIONS else 1
            if j < len(argv) and argv[j] not in actions:
                argv.insert(j, default)
        break
    return argv


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(with_default_action(sys.argv[1:] if argv is None else argv))
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    try:
        config_mod.export_env()         # absolute, so the detached server sees the same files
        diagnostic = args.fn is cmd_config and args.action == "paths"
        lifecycle = args.fn is cmd_serve and (args.stop or args.status)     # never blocked by config
        if not (diagnostic or lifecycle):
            config_mod.user_paths()     # a RAPPORT_CONFIG that doesn't exist fails every command
        return args.fn(args) or 0
    except RapportError as e:
        print(f"rapport: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
