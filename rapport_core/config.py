"""Layered TOML config.

Layers, merged in order (later wins):
  1. defaults   core schema defaults + every module manifest's defaults
  2. preset     resolved across all layers first, then slotted in here
  3. user       the user config file (see user_paths)
  4. learned    the user learned rules beside it
  5. project    every rapport.toml from $HOME down to the report folder, each
                followed by the rapport-learned.toml beside it
  6. front matter of report.md
  7. --set key=value
Module search roots follow the same nearness: each file-based layer contributes
the rapport-modules/ folder beside it plus its [modules] paths, and the git
root's rapport-modules/ ranks just below the rapport.toml files inside the repo.

User config lives in ~/.config/rapport/ ($XDG_CONFIG_HOME respected):
config.toml, learned.toml, rapport-modules/, presets/. RAPPORT_CONFIG_DIR
replaces that folder. RAPPORT_CONFIG names the file itself; its siblings then
follow the project names (rapport-learned.toml, rapport-modules/,
rapport-presets/), and a non-file such as /dev/null means no user config.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import BUILTIN_MODULES, BUILTIN_PRESETS
from .registry import Registry
from .schema import Schema, defaults_of, finalize, load_core_tree, merge_layer, validate
from .util import RapportError, deep_get, state_dir

FRONT_FENCE = "+++"
LEARNED_USER = "learned.toml"
LEARNED_PROJECT = "rapport-learned.toml"
PROJECT_FILE = "rapport.toml"
MODULE_DIR = "rapport-modules"
PRESET_DIR = "rapport-presets"
CONFIG_ENV = "RAPPORT_CONFIG"
CONFIG_DIR_ENV = "RAPPORT_CONFIG_DIR"


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "rapport"


def env_path(var: str) -> Path | None:
    """An env var as an absolute path (~ expanded, relative to cwd); unset or empty → None."""
    v = os.environ.get(var)
    return Path(os.path.abspath(os.path.expanduser(v))) if v else None


def export_env() -> None:
    """Re-export the config env vars as absolute paths, so child processes
    started from another folder (the detached server) see the same files."""
    for var in (CONFIG_ENV, CONFIG_DIR_ENV):
        p = env_path(var)
        if p:
            os.environ[var] = str(p)


@dataclass
class UserPaths:
    source: str                     # RAPPORT_CONFIG | RAPPORT_CONFIG_DIR | XDG_CONFIG_HOME | default
    dir: Path
    config_file: Path | None        # None: no user config (RAPPORT_CONFIG isn't a regular file)
    learned_file: Path | None
    modules_dir: Path | None
    presets_dir: Path | None
    ignored: str | None = None      # an env var that lost to RAPPORT_CONFIG
    error: str | None = None        # RAPPORT_CONFIG is unusable (only with strict=False)


def user_paths(strict: bool = True) -> UserPaths:
    """Where user-level config lives. strict: a RAPPORT_CONFIG that doesn't
    exist is an error (`config paths` passes False to report it instead)."""
    f = env_path(CONFIG_ENV)
    if f:
        ignored = CONFIG_DIR_ENV if env_path(CONFIG_DIR_ENV) else None
        if f.is_dir():
            error = f"{CONFIG_ENV} points at the folder {f}; name a file, or set {CONFIG_DIR_ENV} instead"
        elif f.exists() and not f.is_file():            # /dev/null: no user config
            return UserPaths(CONFIG_ENV, f.parent, None, None, None, None, ignored)
        else:
            error = None if f.exists() else f"{CONFIG_ENV} points at {f}, which doesn't exist"
        if error and strict:
            raise RapportError(error)
        return UserPaths(CONFIG_ENV, f.parent, f, f.parent / LEARNED_PROJECT, f.parent / MODULE_DIR,
                         f.parent / PRESET_DIR, ignored, error)
    d = env_path(CONFIG_DIR_ENV)
    source = CONFIG_DIR_ENV if d else "XDG_CONFIG_HOME" if os.environ.get("XDG_CONFIG_HOME") else "default"
    d = d or config_home()
    return UserPaths(source, d, d / "config.toml", d / LEARNED_USER, d / MODULE_DIR, d / "presets")


def no_user_config() -> RapportError:
    return RapportError(f"no user config: {CONFIG_ENV}={os.environ.get(CONFIG_ENV)} isn't a regular file "
                        f"(use --scope project, or point {CONFIG_ENV} at a file)")


@dataclass
class Layer:
    kind: str       # defaults | preset | user | learned-user | project | learned-project | front matter | --set
    name: str
    data: dict
    path: Path | None = None
    base: Path | None = None  # relative paths in this layer resolve against this


@dataclass
class Config:
    data: dict
    origins: dict
    layers: list[Layer]
    warnings: list[str]
    report_dir: Path
    project_dirs: list[Path]
    registry: Registry
    schema: Schema
    preset: str = "default"
    front_matter_lines: tuple[int, int] | None = None

    def get(self, dotted: str, default=None):
        return deep_get(self.data, dotted, default)

    def module(self, name: str) -> dict:
        return self.data.get("modules", {}).get(name, {})

    def enabled(self, name: str) -> bool:
        mod = self.registry.get(name)
        if mod is None:
            return False
        return mod.required or bool(self.module(name).get("enabled", mod.default_enabled))

    @property
    def source_path(self) -> Path:
        return self.report_dir / self.get("report.source", "report.md")

    @property
    def output_path(self) -> Path:
        return self.report_dir / self.get("report.output", "report.html")

    @property
    def state_dir(self) -> Path:
        return state_dir(self.report_dir)


# ── reading files ──────────────────────────────────────────────────────────── #
def read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise RapportError(f"{path}: invalid TOML: {e}") from None


def split_front_matter(text: str) -> tuple[dict, str, tuple[int, int] | None]:
    """Return (front matter, body, (first, last) 1-based line range or None).

    The front matter lines are blanked rather than removed, so every line
    number the Markdown parser reports still matches report.md."""
    lines = text.split("\n")
    if not lines or lines[0].strip().lstrip("﻿") != FRONT_FENCE:
        return {}, text, None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONT_FENCE:
            raw = "\n".join(lines[1:i])
            try:
                data = tomllib.loads(raw)
            except tomllib.TOMLDecodeError as e:
                raise RapportError(f"front matter (lines 1-{i + 1}): invalid TOML: {e}") from None
            body = "\n" * (i + 1) + "\n".join(lines[i + 1:])
            return data, body, (1, i + 1)
    raise RapportError("front matter opened with +++ on line 1 but never closed")


def learned_to_layer(data: dict) -> dict:
    rules, avoid = [], []
    for entry in data.get("learned", []):
        if not isinstance(entry, dict) or not entry.get("rule"):
            continue
        (avoid if entry.get("kind") == "avoid" else rules).append(str(entry["rule"]))
    out: dict = {}
    if rules or avoid:
        out["writing"] = {}
        if rules:
            out["writing"]["rules"] = rules
        if avoid:
            out["writing"]["avoid"] = avoid
    return out


def parse_set(items) -> dict:
    out: dict = {}
    for item in items or []:
        if "=" not in item:
            raise RapportError(f"--set expects key=value, got {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        try:
            value = tomllib.loads(f"v = {raw.strip()}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw.strip()
        cur = out
        parts = key.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    return out


def project_dirs_for(report_dir: Path) -> list[Path]:
    """Directories from the report folder up to $HOME (or /), outermost first."""
    home = Path.home().resolve()
    dirs, d = [], report_dir.resolve()
    while True:
        dirs.append(d)
        if d == home or d.parent == d:
            break
        d = d.parent
    return list(reversed(dirs))


def git_root(report_dir: Path) -> Path | None:
    return next((d for d in reversed(project_dirs_for(report_dir)) if (d / ".git").exists()), None)


def nearest_project_dir(report_dir: Path) -> Path:
    """Where project-scoped files go: nearest rapport.toml, else the git root,
    else the report folder itself. Each of these is a module search root."""
    for d in reversed(project_dirs_for(report_dir)):
        if (d / PROJECT_FILE).is_file():
            return d
    return git_root(report_dir) or report_dir.resolve()


def normalize_front_matter(fm: dict, core: dict) -> dict:
    """Top-level front matter keys that are not config sections become meta."""
    out, meta = {}, {}
    for k, v in fm.items():
        if k in core:
            out[k] = v
        else:
            meta[k] = v
    if meta:
        out.setdefault("meta", {})
        if isinstance(out["meta"], dict):
            out["meta"] = {**out["meta"], **meta}
    return out


def find_preset(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        p = d / f"{name}.toml"
        if p.is_file():
            return p
    return None


# ── loading ────────────────────────────────────────────────────────────────── #
def load(report_dir: Path, front_matter: dict | None = None, sets=None,
         front_matter_lines=None) -> Config:
    report_dir = Path(report_dir).resolve()
    core = load_core_tree()
    user = user_paths()

    # file-based layers, outermost first
    file_layers: list[Layer] = []
    if user.config_file and user.config_file.is_file():
        file_layers.append(Layer("user", "user", read_toml(user.config_file), user.config_file,
                                 user.config_file.parent))
    if user.learned_file and user.learned_file.is_file():
        file_layers.append(Layer("learned-user", "learned (user)", learned_to_layer(read_toml(user.learned_file)),
                                 user.learned_file, user.learned_file.parent))
    user_files = {p.resolve() for p in (user.config_file, user.learned_file) if p}
    project_dirs = []
    for d in project_dirs_for(report_dir):
        pf = d / PROJECT_FILE
        if pf.is_file() and pf.resolve() not in user_files:     # RAPPORT_CONFIG=~/rapport.toml loads once
            project_dirs.append(d)
            file_layers.append(Layer("project", f"project {pf}", read_toml(pf), pf, d))
        lf = d / LEARNED_PROJECT
        if lf.is_file() and lf.resolve() not in user_files:
            file_layers.append(Layer("learned-project", f"learned {lf}", learned_to_layer(read_toml(lf)), lf, d))

    top_layers: list[Layer] = []
    if front_matter:
        top_layers.append(Layer("front matter", "front matter", normalize_front_matter(front_matter, core),
                                report_dir / "report.md", report_dir))
    cli = parse_set(sets)
    if cli:
        top_layers.append(Layer("--set", "--set", cli, None, Path.cwd()))

    # preset: resolved across all layers first
    preset_name = "default"
    for layer in file_layers + top_layers:
        if isinstance(layer.data.get("preset"), str):
            preset_name = layer.data["preset"]
    preset_dirs = [d / PRESET_DIR for d in reversed(project_dirs)]
    preset_dirs += [user.presets_dir] if user.presets_dir else []
    preset_dirs.append(BUILTIN_PRESETS)
    preset_layers: list[Layer] = []
    preset_file = find_preset(preset_name, preset_dirs)
    if preset_file:
        pdata = read_toml(preset_file)
        pdata.pop("preset", None)
        preset_layers.append(Layer("preset", f"preset {preset_name}", pdata, preset_file, preset_file.parent))
    elif preset_name != "default":
        raise RapportError(f"preset '{preset_name}' not found in: " + ", ".join(map(str, preset_dirs)))

    # module roots, nearest layer first
    roots: list[Path] = []

    def add_root(p: Path):
        p = p.resolve()
        if p not in roots:
            roots.append(p)

    def layer_paths(layer: Layer) -> list[Path]:
        paths = deep_get(layer.data, "modules.paths", []) or []
        if isinstance(paths, str):
            paths = [paths]
        base = layer.base or report_dir
        return [(base / Path(p).expanduser()) for p in paths if isinstance(p, str)]

    for layer in reversed(top_layers):
        for p in layer_paths(layer):
            add_root(p)
    add_root(report_dir / MODULE_DIR)
    repo = git_root(report_dir)
    for layer in reversed(file_layers):
        in_project = layer.kind in ("project", "learned-project")
        if repo and not (in_project and layer.base and layer.base.is_relative_to(repo)):
            add_root(repo / MODULE_DIR)        # below the layers inside the repo, above the rest
        if layer.kind == "project":
            add_root(layer.path.parent / MODULE_DIR)
        if layer.kind == "user" and user.modules_dir:
            add_root(user.modules_dir)
        for p in layer_paths(layer):
            add_root(p)
    if repo:
        add_root(repo / MODULE_DIR)
    if user.modules_dir:
        add_root(user.modules_dir)
    for layer in preset_layers:
        for p in layer_paths(layer):
            add_root(p)
    add_root(BUILTIN_MODULES)

    registry = Registry(roots)
    schema = Schema(core, registry)

    defaults = defaults_of(core)
    defaults["modules"] = {**defaults.get("modules", {})}
    for name, mod in registry.modules.items():
        defaults["modules"][name] = defaults_of(mod.schema_tree())
    layers = [Layer("defaults", "defaults", defaults)] + preset_layers + file_layers + top_layers

    data: dict = {}
    origins: dict = {}
    for layer in layers:
        merge_layer(data, layer.data, schema, origins, layer.name, layer.base)
    finalize(data, schema)
    data["preset"] = preset_name

    warnings = list(registry.errors) + validate(data, schema)
    warnings += _check_theme_tokens(data, registry)
    warnings += _module_checks(data, registry)
    return Config(data=data, origins=origins, layers=layers, warnings=warnings,
                  report_dir=report_dir, project_dirs=project_dirs, registry=registry,
                  schema=schema, preset=preset_name, front_matter_lines=front_matter_lines)


def _check_theme_tokens(data: dict, registry: Registry) -> list[str]:
    name = deep_get(data, "theme.name", "default")
    theme = registry.get(name)
    if theme is None or theme.kind != "theme":
        return [f"theme.name: no theme module named '{name}'"]
    known = set(theme.tokens) | set(theme.dark_tokens)
    out = []
    tokens = deep_get(data, "theme.tokens", {}) or {}
    for k, v in tokens.items():
        if k == "dark" and isinstance(v, dict):
            out += [f"theme.tokens.dark.{dk}: theme '{name}' has no token '{dk}'"
                    for dk in v if dk not in known]
        elif k not in known:
            from .util import did_you_mean
            out.append(f"theme.tokens.{k}: theme '{name}' has no token '{k}'{did_you_mean(k, known)}")
    return out


def _module_checks(data: dict, registry: Registry) -> list[str]:
    """Modules may validate their own settings: check(config) in prepare.py."""
    out = []
    for name, mod in registry.modules.items():
        section = deep_get(data, f"modules.{name}", {}) or {}
        if not (mod.required or section.get("enabled", mod.default_enabled)):
            continue
        try:
            fn = mod.hook("check")
            if fn is not None:
                out += [f"modules.{name}: {w}" for w in (fn(section) or [])]
        except Exception as e:  # noqa: BLE001 - a broken check must not break loading
            out.append(f"modules.{name}: check() in prepare.py failed: {e}")
    return out


def load_for_report(report_dir: Path, sets=None) -> tuple[Config, str]:
    """Load config for a report folder, reading front matter from its source.
    Returns (config, markdown body with front matter blanked)."""
    report_dir = Path(report_dir).resolve()
    # the source name itself is configurable; read it from a front-matter-free pass
    pre = load(report_dir, None, sets)
    src = pre.source_path
    if not src.is_file():
        raise RapportError(f"no {src.name} in {report_dir} (run `rapport init {report_dir}`)")
    text = src.read_text(encoding="utf-8")
    fm, body, fm_lines = split_front_matter(text)
    cfg = load(report_dir, fm, sets, fm_lines) if fm else pre
    cfg.front_matter_lines = fm_lines
    return cfg, body


TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_key(k: str) -> str:
    return k if TOML_BARE_KEY.match(k) else '"' + k.replace('"', '\\"') + '"'
