"""Small shared helpers."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


_UMASK = os.umask(0)
os.umask(_UMASK)


class RapportError(Exception):
    """A user-facing error: printed without a traceback."""


def json_block(obj, **kw) -> str:
    """json.dumps safe to embed in a <script> element: '</' is escaped, since an
    HTML parser ends the element at the first literal '</script' no matter how
    deep in the JSON it sits. '\\/' is a legal JSON escape for '/'."""
    return json.dumps(obj, ensure_ascii=False, **kw).replace("</", "<\\/")


def short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "section"


def did_you_mean(word: str, options) -> str:
    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return f" (did you mean '{match[0]}'?)" if match else ""


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o666 & ~_UMASK)  # mkstemp creates 0600; match a normal file
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def deep_get(data: dict, dotted: str, default=None):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def toml_repr(v) -> str:
    """A value as TOML source (for docs and generated config)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_repr(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {toml_repr(x)}" for k, x in v.items()) + "}"
    return '""'
