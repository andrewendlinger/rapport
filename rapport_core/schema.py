"""Config schema: spec lookup, type checks, defaults, and the layered merge.

A spec tree is nested dicts; a dict with a string `type` is a leaf spec, any
other dict is a section. The core tree comes from config/schema.toml; module
trees are synthesized from each manifest ([params.*] + [config.*] + enabled)
and mounted at modules.<name>.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from . import SCHEMA_FILE
from .css import SCHEME_RE
from .util import did_you_mean, is_image_file

TYPES = ("string", "int", "number", "bool", "enum", "list", "table", "tables", "any")


def is_leaf(spec) -> bool:
    return isinstance(spec, dict) and isinstance(spec.get("type"), str)


def load_core_tree(path: Path = SCHEMA_FILE) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def defaults_of(tree: dict) -> dict:
    out: dict = {}
    for key, spec in tree.items():
        if is_leaf(spec):
            if "default" in spec:
                out[key] = copy.deepcopy(spec["default"])
        elif isinstance(spec, dict):
            sub = defaults_of(spec)
            if sub:
                out[key] = sub
    return out


# ── value checks ─────────────────────────────────────────────────────────── #
def _type_name(v) -> str:
    return {bool: "bool", int: "int", float: "number", str: "string", list: "list",
            dict: "table"}.get(type(v), type(v).__name__)


def check_value(spec: dict, value, where: str):
    """Return (coerced_value, error_or_None)."""
    t = spec.get("type", "any")
    if t == "any":
        return value, None
    if t == "string":
        if isinstance(value, str):
            return value, None
    elif t == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
    elif t == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value, None
    elif t == "bool":
        if isinstance(value, bool):
            return value, None
    elif t == "enum":
        choices = spec.get("choices", [])
        if value in choices:
            return value, None
        return value, f"{where}: '{value}' is not one of {', '.join(map(str, choices))}" \
            f"{did_you_mean(str(value), map(str, choices))}"
    elif t == "list":
        if isinstance(value, list):
            item_t = spec.get("items")
            if item_t:
                for i, item in enumerate(value):
                    _, err = check_value({"type": item_t}, item, f"{where}[{i}]")
                    if err:
                        return value, err
            return value, None
    elif t == "table":
        if isinstance(value, dict):
            return value, None
    elif t == "tables":
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value, None
    return value, f"{where}: expected {t}, got {_type_name(value)} ({value!r})"


# ── schema with module trees ─────────────────────────────────────────────── #
class Schema:
    def __init__(self, core: dict, registry=None):
        self.core = core
        self.registry = registry

    def module_tree(self, name: str) -> dict | None:
        if self.registry is None:
            return None
        mod = self.registry.get(name)
        return mod.schema_tree() if mod else None

    def lookup(self, path: tuple) -> dict | None:
        """Leaf spec, section dict, or None if unknown."""
        if len(path) >= 2 and path[0] == "modules" and path[1] != "paths":
            tree = self.module_tree(path[1])
            if tree is None:
                return None
            node = tree
            rest = path[2:]
        else:
            node = self.core
            rest = path
        for part in rest:
            if is_leaf(node):
                # inside an open table / tables value: no deeper spec
                return {"type": "any"} if node["type"] in ("table", "tables", "any") else None
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def siblings(self, path: tuple) -> list[str]:
        parent = self.lookup(path[:-1]) if path[:-1] else self.core
        if path[:-1] == ("modules",):
            names = list(self.registry.names()) if self.registry else []
            return names + ["paths"]
        if isinstance(parent, dict) and not is_leaf(parent):
            return list(parent.keys())
        return []

    def strategy(self, spec: dict | None) -> str:
        if not is_leaf(spec):
            return "merge"
        if "merge" in spec:
            return spec["merge"]
        return {"tables": "by-id", "table": "merge"}.get(spec["type"], "replace")


# ── merge ──────────────────────────────────────────────────────────────────── #
def _resolve_path_value(value, base: Path | None, mode=True):
    """mode true: a path. "icon": a path only if it names an image file; a symbol stays as written."""
    if base is None:
        return value
    # "" stays: it's the reset entry of an append list; a url stays for the build to reject as remote
    if isinstance(value, str) and value and not SCHEME_RE.match(value) and (mode is True or is_image_file(value)):
        p = Path(value).expanduser()
        return str(p if p.is_absolute() else (base / p).resolve())
    if isinstance(value, list):
        return [_resolve_path_value(v, base, mode) for v in value]
    return value


def _resolve_paths(spec: dict, value, base: Path | None):
    if spec.get("path"):
        return _resolve_path_value(value, base, spec["path"])
    fields = spec.get("fields")
    if spec.get("type") == "tables" and isinstance(fields, dict) and isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                item = dict(item)
                for k, fspec in fields.items():
                    if k in item and isinstance(fspec, dict) and fspec.get("path"):
                        item[k] = _resolve_path_value(item[k], base, fspec["path"])
            out.append(item)
        return out
    return value


def _dedupe(items: list) -> list:
    out, seen = [], set()
    for it in items:
        key = repr(it)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def merge_layer(dst: dict, src: dict, schema: Schema, origins: dict, origin: str,
                base: Path | None, path: tuple = ()) -> None:
    for key, value in src.items():
        p = path + (key,)
        dotted = ".".join(p)
        spec = schema.lookup(p)
        if is_leaf(spec) and spec["type"] != "any":
            if spec["type"] == "list" and isinstance(value, str):
                value = [value]                 # --set theme.css=x.css arrives as a string
            value = _resolve_paths(spec, value, base)
            strat = schema.strategy(spec)
            if strat == "append" and isinstance(value, list):
                cur = dst.get(key) if isinstance(dst.get(key), list) else []
                if "" in value:                 # git-style reset: "" drops everything before it
                    cur, value = [], value[len(value) - value[::-1].index(""):]
                prev = origins.get(dotted) if cur else None
                dst[key] = _dedupe(cur + copy.deepcopy(value))
                origins[dotted] = f"{prev} + {origin}" if prev and value else (prev or origin)
                continue
            if strat == "by-id" and isinstance(value, list):
                cur = dst.get(key) if isinstance(dst.get(key), list) else []
                dst[key] = _merge_by_id(cur, value, origins, dotted, origin)
                continue
            if strat == "merge" and isinstance(value, dict):
                sub = dst.get(key) if isinstance(dst.get(key), dict) else {}
                dst[key] = sub
                _merge_plain(sub, value, origins, dotted, origin)
                continue
            dst[key] = copy.deepcopy(value)
            origins[dotted] = origin
        elif isinstance(value, dict) and (spec is None or not is_leaf(spec)):
            sub = dst.get(key)
            if not isinstance(sub, dict):
                sub = {}
                dst[key] = sub
            merge_layer(sub, value, schema, origins, origin, base, p)
        else:
            dst[key] = copy.deepcopy(value)
            origins[dotted] = origin


def _merge_plain(dst: dict, src: dict, origins: dict, dotted: str, origin: str) -> None:
    for k, v in src.items():
        d = f"{dotted}.{k}"
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_plain(dst[k], v, origins, d, origin)
        else:
            dst[k] = copy.deepcopy(v)
            origins[d] = origin


def _merge_by_id(cur: list, new: list, origins: dict, dotted: str, origin: str) -> list:
    out = [copy.deepcopy(x) for x in cur]
    index = {x.get("id"): i for i, x in enumerate(out) if isinstance(x, dict) and "id" in x}
    for item in new:
        if not isinstance(item, dict) or "id" not in item:
            origins[f"{dotted}[#{len(out)}]"] = origin      # # : a position, not an id
            out.append(copy.deepcopy(item))
            continue
        iid = item["id"]
        if iid in index:
            out[index[iid]].update(copy.deepcopy(item))
        else:
            index[iid] = len(out)
            out.append(copy.deepcopy(item))
        origins[f"{dotted}[{iid}]"] = origin
    return out


def finalize(data: dict, schema: Schema, path: tuple = ()) -> None:
    """Drop by-id entries switched off with enabled = false."""
    for key, value in list(data.items()):
        p = path + (key,)
        spec = schema.lookup(p)
        if is_leaf(spec) and schema.strategy(spec) == "by-id" and isinstance(value, list):
            data[key] = [x for x in value if not (isinstance(x, dict) and x.get("enabled") is False)]
        elif isinstance(value, dict) and not is_leaf(spec):
            finalize(value, schema, p)


def validate(data: dict, schema: Schema, path: tuple = ()) -> list[str]:
    warnings: list[str] = []
    for key, value in data.items():
        p = path + (key,)
        dotted = ".".join(p)
        spec = schema.lookup(p)
        if spec is None:
            if p[:1] == ("modules",) and len(p) == 2:
                warnings.append(f"[modules.{key}]: no module named '{key}'"
                                f"{did_you_mean(key, schema.siblings(p))}")
            else:
                warnings.append(f"unknown config key '{dotted}'{did_you_mean(key, schema.siblings(p))}")
            continue
        if is_leaf(spec):
            _, err = check_value(spec, value, dotted)
            if err:
                warnings.append(err)
            elif spec["type"] == "tables" and isinstance(spec.get("fields"), dict):
                for i, item in enumerate(value):
                    for fk, fv in item.items():
                        fspec = spec["fields"].get(fk)
                        where = f"{dotted}[{item.get('id', i)}].{fk}"
                        if fspec is None:
                            warnings.append(f"unknown field '{where}'"
                                            f"{did_you_mean(fk, spec['fields'].keys())}")
                        else:
                            _, ferr = check_value(fspec, fv, where)
                            if ferr:
                                warnings.append(ferr)
        elif isinstance(value, dict):
            warnings.extend(validate(value, schema, p))
        else:
            warnings.append(f"'{dotted}' is a section; expected a table, got {value!r}")
    return warnings
