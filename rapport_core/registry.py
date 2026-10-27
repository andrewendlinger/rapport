"""Module discovery, manifests, and param resolution.

A module is a folder with a module.toml. Search roots are scanned in priority
order (nearest first); the first module found for a name wins and later ones
are recorded as shadowed. Files are found by convention: template.html,
style.css, script.js, prepare.py, server.py, example.md.
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


@dataclass
class Module:
    name: str
    kind: str
    dir: Path
    manifest: dict
    builtin: bool
    shadows: list[Path] = field(default_factory=list)
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

    # -- files ----------------------------------------------------------------- #
    def file(self, name: str) -> Path | None:
        p = self.dir / name
        return p if p.is_file() else None

    @property
    def template_path(self) -> Path | None:
        return self.file("template.html")

    @property
    def css_path(self) -> Path | None:
        return self.file("style.css")

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
        """A function from prepare.py: prepare(node, ctx) -> dict, check(config) -> [warnings].
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

    def prepare_fn(self):
        """The module's prepare(node, ctx) -> dict hook, if it has one."""
        return self.hook("prepare")

    # -- schema --------------------------------------------------------------- #
    @functools.cached_property
    def schema_tree(self) -> dict:
        tree = {"enabled": {"type": "bool", "default": self.default_enabled,
                            "doc": "Switch this module on or off."}}
        tree.update(self.params)
        tree.update(self.config)
        return tree


class Registry:
    def __init__(self, roots: list[Path]):
        self.roots = [Path(r) for r in roots]
        self.modules: dict[str, Module] = {}
        self.errors: list[str] = []
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
                problem = self._check_manifest(name, data)
                if problem:
                    self.errors.append(f"{mf}: {problem}")
                    continue
                if name in self.modules:
                    self.modules[name].shadows.append(mdir)
                    continue
                self.modules[name] = Module(name=name, kind=data["kind"], dir=mdir, manifest=data,
                                            builtin=builtin)

    @staticmethod
    def _check_manifest(name: str, data: dict) -> str | None:
        if not isinstance(name, str) or not NAME_RE.match(name):
            return f"invalid module name {name!r} (lowercase letters, digits, hyphens)"
        if name in RESERVED:
            return f"'{name}' is reserved"
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
