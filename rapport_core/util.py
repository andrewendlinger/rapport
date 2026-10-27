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
    """json.dumps safe to embed in a <script> element: every '<' becomes \\u003c, since
    an HTML parser ends the element at the first literal '</script' (and '<!--' changes
    how it looks for one) no matter how deep in the JSON it sits. '<' only occurs in
    strings, where \\u003c is the same character to a JSON parser."""
    return json.dumps(obj, ensure_ascii=False, **kw).replace("<", "\\u003c")


def short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", text) or "section"


def did_you_mean(word: str, options) -> str:
    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return f" (did you mean '{match[0]}'?)" if match else ""


def unique(items, key=None) -> list:
    """items in order, without repeats; key(item) decides what counts as the same."""
    seen, out = set(), []
    for it in items:
        k = key(it) if key else it
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


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


STATE_DIR = ".rapport"
STATE_FILE = "state.json"      # version and history
BLOCKS_FILE = "blocks.json"    # block index of the last build, for anchoring
GITIGNORE_MARK = "# managed by rapport"
OLD_GITIGNORE = "server.json\nserver.log\n"      # what rapport 0.1.0 wrote


def state_dir(report_dir: Path) -> Path:
    """A report's review state (feedback, version, block index, server info).
    The only place that knows the folder's name."""
    return Path(report_dir) / STATE_DIR


def state_gitignore(commit_feedback: bool = False) -> str:
    """The managed .gitignore. Its first line must keep starting with GITIGNORE_MARK:
    that's how ensure_state_dir knows it may rewrite it (older headers included)."""
    keep = "!.gitignore\n!feedback.json\n!state.json\n!rounds.json\n" if commit_feedback else ""
    how = ("[report] commit_feedback = true lets feedback through" if commit_feedback
           else "set [report] commit_feedback = true to commit feedback")
    return f"{GITIGNORE_MARK}: {how}; edits are overwritten\n*\n{keep}"


def ensure_state_dir(report_dir: Path, commit_feedback: bool | None = None) -> Path:
    """Create the state folder with the .gitignore that ignores it. With
    commit_feedback=None (no config at hand) a missing .gitignore is created
    but an existing one is never rewritten; with a flag, a managed one (or the
    old default) is rewritten to match it. Anyone else's file is left alone."""
    d = state_dir(report_dir)
    d.mkdir(parents=True, exist_ok=True)
    gi, want = d / ".gitignore", state_gitignore(bool(commit_feedback))
    if not gi.exists():
        atomic_write(gi, want)
    elif commit_feedback is not None:
        try:
            have = gi.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return d
        if have != want and (have.startswith(GITIGNORE_MARK) or have == OLD_GITIGNORE):
            atomic_write(gi, want)
    return d


def read_state(report_dir: Path) -> dict:
    try:
        state = json.loads((state_dir(report_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    return state if isinstance(state, dict) else {"version": 1}


def write_state(report_dir: Path, state: dict) -> None:
    atomic_write(ensure_state_dir(report_dir) / STATE_FILE, json.dumps(state, indent=2) + "\n")


def bump_version(report_dir: Path, note: str = "") -> int:
    """Read-modify-write: callers that may race (feedback resolve) hold the feedback store's lock."""
    state = read_state(report_dir)
    state["version"] = int(state.get("version", 1)) + 1
    if note:
        state.setdefault("history", []).append({"version": state["version"], "note": note})
    write_state(report_dir, state)
    return state["version"]


IMAGE_FILE_RE = re.compile(r"\.(?:svg|png|jpe?g|gif|webp|avif)$", re.I)


def is_image_file(value) -> bool:
    """An icon names an image file when it ends in an image extension; else it's a symbol, drawn as text."""
    return isinstance(value, str) and bool(IMAGE_FILE_RE.search(value))


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
