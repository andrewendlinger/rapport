"""Module discovery, manifests, and param resolution.

A module is a folder with a module.toml. Search roots are scanned in priority
order (nearest first); the first module found for a name wins and later ones
are recorded as shadowed. Files are found by convention: template.html,
style.css, script.js, prepare.py, server.py, example.md.

A module with `extends = "<name>"` is built on another: on the next module of
its own name down the roots, or on the nearest module of another name. Its
manifest goes on top of the base's (tables merge key by key), a file it lacks
is the base's, and its style.css is added after the base's.
"""

from __future__ import annotations

import functools
import importlib.machinery
import importlib.util
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import BUILTIN_MODULES
from .schema import check_value, is_leaf
from .util import did_you_mean

KINDS = ("block", "element", "inline", "layout", "theme", "feature")
KIND_ORDER = {k: i for i, k in enumerate(("theme", "layout", "element", "block", "inline", "feature"))}
BODIES = ("markdown", "text", "toml", "none")
WHENS = ("always", "serve", "static")
UNITS = ("block", "none")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
RESERVED = {"paths"}
# the override's alone, never inherited: who it is, what it builds on, what it was made from
OWN_KEYS = ("name", "extends", "upstream")
# files that name the module inside (Rapport.module("callout", …), ::: callout): a module
# with another name doesn't inherit them
NAME_BOUND = ("script.js", "example.md")


class _NoBytecode(importlib.machinery.SourceFileLoader):
    """Loads a module's Python without writing __pycache__ into its folder: the
    watcher would take the new folder for an edit and rebuild a second time."""

    def set_data(self, path, data, *, _mode=0o666):
        pass


def load_code(path: Path | None, label: str):
    """(module, None) for a Python file; (False, why) if it fails to load; (False, None) without one."""
    if path is None:
        return False, None
    name = f"rapport_{label}"
    try:
        spec = importlib.util.spec_from_file_location(name, path, loader=_NoBytecode(name, str(path)))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:  # noqa: BLE001 - a module's own code: report it, don't crash
        return False, f"{path.name} failed to load: {type(e).__name__}: {e}"


def merge_manifest(base: dict, own: dict) -> dict:
    """The base's manifest with own on top: tables merge key by key at any depth, so one
    param's default or one token changes alone; anything else (a string, a list) is replaced."""
    def merge(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out
    return merge({k: v for k, v in base.items() if k not in OWN_KEYS}, own)


def hidden(rel: str) -> bool:
    """Not part of a module: __pycache__, dotfiles."""
    return any(part == "__pycache__" or part.startswith(".") for part in rel.split("/"))


@dataclass
class Module:
    name: str
    kind: str
    dir: Path
    manifest: dict                  # merged with what it extends
    builtin: bool
    shadows: list[Path] = field(default_factory=list)
    base: Module | None = None      # what it extends
    below: Module | None = None     # the next module of its name down the roots: what it overrides
    load_error: str | None = None   # prepare.py exists but failed to load (set by hook())
    server_error: str | None = None  # server.py exists but failed to load (set by server_hook())
    _prepare: object = None
    _server: object = None

    # -- manifest accessors -------------------------------------------------- #
    @property
    def summary(self) -> str:
        return self.manifest.get("summary", "")

    @property
    def body(self) -> str:
        return self.manifest.get("body", "markdown")

    @property
    def positional(self) -> str | None:
        return self.manifest.get("positional")

    @property
    def children(self) -> list[str]:
        return list(self.manifest.get("children", []))

    @property
    def params(self) -> dict:
        return self.manifest.get("params", {})

    @property
    def config(self) -> dict:
        return self.manifest.get("config", {})

    @property
    def feedback_unit(self) -> str:
        return self.manifest.get("feedback", {}).get("unit", "block")

    @property
    def when(self) -> str:
        return self.manifest.get("when", "always")

    @property
    def required(self) -> bool:
        return bool(self.manifest.get("required", False))

    @property
    def default_enabled(self) -> bool:
        return bool(self.manifest.get("enabled", True))

    @property
    def tokens(self) -> dict:
        return {k: v for k, v in self.manifest.get("tokens", {}).items() if not isinstance(v, dict)}

    @property
    def dark_tokens(self) -> dict:
        return dict(self.manifest.get("tokens", {}).get("dark", {}))

    @property
    def origin(self) -> str:
        return "built-in" if self.builtin else str(self.dir)

    # -- files ----------------------------------------------------------------- #
    def inherits(self, name: str) -> bool:
        return self.base is not None and (name not in NAME_BOUND or self.base.name == self.name)

    def file(self, name: str) -> Path | None:
        """Its own file, else the one it inherits."""
        p = self.dir / name
        if p.is_file():
            return p
        return self.base.file(name) if self.inherits(name) else None

    @property
    def chain(self) -> list[Module]:
        """This module, what it extends, what that extends, …"""
        return [self] + (self.base.chain if self.base else [])

    def own_files(self) -> dict[str, Path]:
        """The files in its folder, by path there."""
        found = ((p.relative_to(self.dir).as_posix(), p) for p in sorted(self.dir.rglob("*")) if p.is_file())
        return {rel: p for rel, p in found if not hidden(rel)}

    def files(self) -> dict[str, Path]:
        """Every file it is made of, by path in its folder: its own, else the inherited one."""
        out = {rel: p for rel, p in self.base.files().items() if self.inherits(rel)} if self.base else {}
        return {**out, **self.own_files()}

    @property
    def template_path(self) -> Path | None:
        return self.file("template.html")

    @property
    def css_paths(self) -> list[Path]:
        """Its style.css after each one it inherits, base first: an override's adds rules to its base's."""
        return [p for m in reversed(self.chain) if (p := m.dir / "style.css").is_file()]

    def has_css(self) -> bool:
        """A style.css, its own or inherited, or a css(ctx) hook in prepare.py."""
        return bool(self.css_paths or self.hook("css"))

    @property
    def asset_dirs(self) -> list[Path]:
        """assets/ folders for its example, base first (copied in that order for check and gallery)."""
        return [p for m in reversed(self.chain) if (p := m.dir / "assets").is_dir()]

    @property
    def js_path(self) -> Path | None:
        return self.file("script.js")

    @property
    def example_path(self) -> Path | None:
        return self.file("example.md")

    @property
    def has_code(self) -> bool:
        return self.file("prepare.py") is not None

    @property
    def has_server(self) -> bool:
        return self.file("server.py") is not None

    def hook(self, name: str):
        """A function from prepare.py: prepare(node, ctx) -> dict, css(ctx) -> str, check(config) -> [warnings].
        prepare.py is loaded once; if that fails, load_error says why and there are no hooks."""
        if self._prepare is None:
            self._prepare, self.load_error = load_code(self.file("prepare.py"), f"mod_{self.name.replace('-', '_')}")
        return getattr(self._prepare, name, None) if self._prepare else None

    def server_hook(self, name: str):
        """A function from server.py: routes(r), describe(state, config). Loaded once,
        like prepare.py; if that fails, server_error says why and there are no hooks."""
        if self._server is None:
            self._server, self.server_error = load_code(self.file("server.py"), f"srv_{self.name.replace('-', '_')}")
        return getattr(self._server, name, None) if self._server else None

    # -- schema --------------------------------------------------------------- #
    @functools.cached_property
    def schema_tree(self) -> dict:
        tree = {"enabled": {"type": "bool", "default": self.default_enabled,
                            "doc": "Switch this module on or off."}}
        tree.update(self.params)
        tree.update(self.config)
        return tree


@dataclass
class Found:
    """A module folder, before what it extends is merged in."""
    dir: Path
    manifest_path: Path
    data: dict
    builtin: bool


class Registry:
    def __init__(self, roots: list[Path]):
        self.roots = [Path(r) for r in roots]
        self.modules: dict[str, Module] = {}
        self.errors: list[str] = []
        self._found: dict[str, list[Found]] = {}        # name → every folder with it, nearest first
        self._resolved: dict[tuple[str, int], Module | None] = {}
        self._looped: set[tuple[str, int]] = set()
        self._discover()

    def _discover(self) -> None:
        seen_dirs: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            builtin = root.resolve() == BUILTIN_MODULES.resolve()
            # root/<name>/module.toml or root/<pack>/<name>/module.toml
            manifests = sorted(root.glob("*/module.toml")) + sorted(root.glob("*/*/module.toml"))
            for mf in manifests:
                mdir = mf.parent.resolve()
                if mdir in seen_dirs:
                    continue
                seen_dirs.add(mdir)
                try:
                    with open(mf, "rb") as fh:
                        data = tomllib.load(fh)
                except (OSError, tomllib.TOMLDecodeError) as e:
                    self.errors.append(f"{mf}: cannot read manifest: {e}")
                    continue
                name = data.get("name", mf.parent.name)
                problem = self._check_name(name)
                if problem:
                    self.errors.append(f"{mf}: {problem}")
                    continue
                self._found.setdefault(name, []).append(Found(mdir, mf, data, builtin))
        # every folder is resolved, shadowed ones too: their errors show, and an override finds its base
        for name, found in self._found.items():
            mods = [self._resolve(name, i) for i in range(len(found))]
            for mod, below in zip(mods, mods[1:] + [None]):
                if mod:
                    mod.below = below
            live = [m for m in mods if m]
            if live:
                live[0].shadows = [m.dir for m in live[1:]]
                self.modules[name] = live[0]

    def _resolve(self, name: str, i: int, stack: tuple = ()) -> Module | None:
        """The i-th module found for name (0: nearest), merged with what it extends;
        None, with an error, if it can't be used."""
        key = (name, i)
        if key in self._resolved:
            return self._resolved[key]
        found = self._found[name][i]
        if key in stack:
            loop = stack[stack.index(key):]
            self._looped.update(loop)
            path = " → ".join(f"{n} ({self._found[n][j].dir})" for n, j in loop)
            self.errors.append(f"{found.manifest_path}: `extends` goes round in a circle: {path} → {name}")
            return None
        mod, problem = self._build(name, i, found, stack + (key,))
        if problem:
            self.errors.append(f"{found.manifest_path}: {problem}")
        self._resolved[key] = mod
        return mod

    def _build(self, name: str, i: int, found: Found, stack: tuple) -> tuple[Module | None, str | None]:
        data, base = found.data, None
        ext = data.get("extends")
        if ext is not None:
            if not isinstance(ext, str) or not NAME_RE.match(ext):
                return None, f"extends must name a module, not {ext!r}"
            # the same name: the next one down (what this overrides); another: the nearest
            j = i + 1 if ext == name else 0
            if j >= len(self._found.get(ext, [])):
                if ext == name:
                    return None, f"extends '{name}', but there's no other '{name}' below it to extend"
                return None, f"extends '{ext}': no such module{did_you_mean(ext, self._found)}"
            base = self._resolve(ext, j, stack)
            if base is None:
                return None, None if (ext, j) in self._looped else \
                    f"extends '{ext}' ({self._found[ext][j].dir}), which has an error (see above)"
            kind = data.get("kind", base.kind)
            if kind != base.kind:
                return None, (f"kind = \"{kind}\", but '{ext}' ({base.origin}), which it extends, is a {base.kind}; "
                              "leave kind out")
            data = merge_manifest(base.manifest, data)
        problem = self._check_manifest(data)
        if problem:
            return None, problem
        if base and base.name != name and base.file("script.js") and not (found.dir / "script.js").is_file():
            return None, (f"can't inherit script.js from '{ext}': it registers itself as Rapport.module(\"{ext}\", …), "
                          f"not as '{name}'. Copy {base.file('script.js')} here and change the name in it "
                          f"(`rapport modules new {name} --extends {ext}` does that)")
        return Module(name=name, kind=data["kind"], dir=found.dir, manifest=data, builtin=found.builtin,
                      base=base), None

    @staticmethod
    def _check_name(name) -> str | None:
        if not isinstance(name, str) or not NAME_RE.match(name):
            return f"invalid module name {name!r} (lowercase letters, digits, hyphens)"
        if name in RESERVED:
            return f"'{name}' is reserved"
        return None

    @staticmethod
    def _check_manifest(data: dict) -> str | None:
        kind = data.get("kind")
        if kind not in KINDS:
            return f"kind must be one of {', '.join(KINDS)}{did_you_mean(str(kind), KINDS)}"
        if data.get("body", "markdown") not in BODIES:
            return f"body must be one of {', '.join(BODIES)}"
        if data.get("when", "always") not in WHENS:
            return f"when must be one of {', '.join(WHENS)}"
        if data.get("feedback", {}).get("unit", "block") not in UNITS:
            return f"[feedback] unit must be one of {', '.join(UNITS)}"
        for section in ("params", "config"):
            for key, spec in data.get(section, {}).items():
                if not is_leaf(spec):
                    return f"[{section}.{key}] needs a `type`"
        return None

    # -- queries --------------------------------------------------------------- #
    def get(self, name: str) -> Module | None:
        return self.modules.get(name)

    def names(self):
        return self.modules.keys()

    def by_kind(self, kind: str) -> list[Module]:
        return sorted((m for m in self.modules.values() if m.kind == kind), key=lambda m: m.name)

    def ordered(self, names) -> list[Module]:
        mods = [self.modules[n] for n in names if n in self.modules]
        return sorted(mods, key=lambda m: (KIND_ORDER[m.kind], m.manifest.get("order", 50), m.name))


def resolve_params(mod: Module, section: dict, given: dict, positional: str = "") -> tuple[dict, list[str]]:
    """Merge manifest defaults (already in the config section) with per-use params."""
    errors: list[str] = []
    params = {k: section[k] for k in mod.params if k in section}
    for k, spec in mod.params.items():
        if k not in params and "default" in spec:
            params[k] = spec["default"]
    given = dict(given or {})
    if positional:
        if mod.positional:
            given.setdefault(mod.positional, positional)
        else:
            errors.append(f"'{mod.name}' takes no title text (got {positional!r}); "
                          f"use params instead, e.g. {{{next(iter(mod.params), 'key')} = …}}")
    for k, v in given.items():
        spec = mod.params.get(k)
        if spec is None:
            known = ", ".join(mod.params) or "none"
            errors.append(f"'{mod.name}' has no param '{k}'{did_you_mean(k, mod.params)} (params: {known})")
            continue
        v, err = check_value(spec, v, f"{mod.name}.{k}")
        if err:
            errors.append(err)
        params[k] = v
    for k, spec in mod.params.items():
        if spec.get("required") and params.get(k) in (None, ""):
            errors.append(f"'{mod.name}' needs param '{k}'")
    return params, errors
