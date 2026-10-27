"""Module routes: a module's server.py extends the review server.

server.py defines routes(r) and registers handlers with r.get / post / put /
patch / delete(path, fn). They answer under /api/m/<module>/. A handler takes
a Request and returns something JSON-serialisable. The server checks the Host
header, the session token (every method but GET) and the body before any
handler runs, so a module never has to. Routes come from the registry of the
last successful build: an edit to server.py applies on the next rebuild.
The contract is "server.py" in references/modules.md.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .store import StateFile
from .util import NotFound, RapportError, Refused, read_state

PARAM_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+")


class RouteError(RapportError):
    """routes(r) registered something unusable."""


def split(path: str) -> list[str]:
    path = path.strip("/")
    return path.split("/") if path else []


@dataclass
class Route:
    method: str
    path: str
    parts: list         # per segment: (literal, None) or (None, param name)
    fn: object

    def bind(self, segs: list[str]) -> dict | None:
        """The path params if segs fit this route, else None."""
        if len(segs) != len(self.parts):
            return None
        params = {}
        for (literal, name), seg in zip(self.parts, segs):
            if name and seg:
                params[name] = seg
            elif seg != literal:
                return None
        return params


class Router:
    """What routes(r) gets. r.get("/items/{id}", fn): fn(req) answers
    GET /api/m/<module>/items/<id>, with req.params["id"] set."""

    def __init__(self, module: str):
        self.module = module
        self.routes: list[Route] = []

    def get(self, path: str, fn) -> None:
        self._add("GET", path, fn)

    def post(self, path: str, fn) -> None:
        self._add("POST", path, fn)

    def put(self, path: str, fn) -> None:
        self._add("PUT", path, fn)

    def patch(self, path: str, fn) -> None:
        self._add("PATCH", path, fn)

    def delete(self, path: str, fn) -> None:
        self._add("DELETE", path, fn)

    def _add(self, method: str, path: str, fn) -> None:
        where = f"r.{method.lower()}({path!r})"
        if not isinstance(path, str) or not path.startswith("/"):
            raise RouteError(f'{where}: a path starts with "/", e.g. "/items/{{id}}"')
        if not callable(fn):
            raise RouteError(f"{where}: the handler must be a function, not {type(fn).__name__}")
        parts = []
        for seg in split(path):
            m = PARAM_RE.fullmatch(seg)
            if m:
                parts.append((None, m[1]))
            elif SEGMENT_RE.fullmatch(seg):
                parts.append((seg, None))
            else:
                raise RouteError(f"{where}: bad segment {seg!r}; use letters, digits and - . _ ~, "
                                 "or a lowercase {param}")
        names = [n for _, n in parts if n]
        if len(names) != len(set(names)):
            raise RouteError(f"{where}: a {{param}} name appears twice")
        shape = [literal for literal, _ in parts]
        if any(r.method == method and [literal for literal, _ in r.parts] == shape for r in self.routes):
            raise RouteError(f"{where}: registered twice")
        self.routes.append(Route(method, "/" + "/".join(split(path)), parts, fn))
        self.routes.sort(key=lambda r: [literal is None for literal, _ in r.parts])    # /items/new before /items/{id}

    def match(self, method: str, path: str) -> tuple[Route | None, dict, list[str]]:
        """(route, path params, []), or (None, {}, the methods the path does take)."""
        segs = [urllib.parse.unquote(s) for s in split(path)]
        others = []
        for r in self.routes:
            params = r.bind(segs)
            if params is None:
                continue
            if r.method == method:
                return r, params, []
            others.append(r.method)
        return None, {}, others


@dataclass
class Request:
    """What a handler gets: the request, and what the last successful build knows."""
    method: str
    body: dict              # the JSON body; {} for GET
    params: dict            # path params: "/items/{id}" → params["id"]
    query: dict             # ?a=1 → {"a": "1"}
    store: StateFile        # this module's state; read-only in a GET
    config: dict            # this module's resolved [modules.<name>]
    full_config: object     # the whole resolved Config: .get("report.title"), .module(name)
    blocks: dict            # the block index: {id: {module, lines, src, heading_path, section, text}}
    report_dir: Path
    Refused = Refused       # raise req.Refused("…") → 400
    NotFound = NotFound     # raise req.NotFound("…") → 404

    @property
    def version(self) -> int:
        """The report version now (`rapport feedback resolve` bumps it)."""
        return read_state(self.report_dir).get("version", 1)


def load_routes(mod) -> Router:
    """Run the module's routes(r). A RapportError says what is wrong with its server.py."""
    fn = mod.server_hook("routes")
    if mod.server_error:
        raise RapportError(mod.server_error)
    if not callable(fn):
        raise RapportError("server.py has no routes(r) function")
    r = Router(mod.name)
    try:
        fn(r)
    except RouteError as e:
        raise RapportError(f"server.py: {e}") from None
    except Exception as e:  # noqa: BLE001 - a module's own code: report it, don't crash
        raise RapportError(f"server.py: routes(r) failed: {type(e).__name__}: {e}") from None
    return r


def serves(mod, cfg) -> bool:
    """Does the module get routes? It has a server.py, is switched on, and isn't static-only."""
    return mod.has_server and mod.when != "static" and cfg.enabled(mod.name)


def mount(cfg) -> tuple[dict[str, Router], list[str]]:
    """The routes of every module that serves some, and what went wrong loading the others."""
    table, errors = {}, []
    for name in sorted(cfg.registry.names()):
        mod = cfg.registry.get(name)
        if not serves(mod, cfg):
            continue
        try:
            table[name] = load_routes(mod)
        except RapportError as e:
            errors.append(f"module '{name}' {e}")
    return table, errors
