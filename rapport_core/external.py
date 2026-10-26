"""Running local tools from modules (diagram renderers, MathJax), with a cache.

Renders are cached by content under $XDG_CACHE_HOME/rapport/render (or
~/.cache/...), keyed on what produced them plus rapport's version, so a
report rebuilds fast and identical diagrams render once. A failure raises
ToolError (a ValueError, so prepare.py turns it into a build error).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from . import __version__


class ToolError(ValueError):
    pass


class ToolMissing(ToolError):
    """The tool isn't installed (or RAPPORT_TOOLS=off): modules show a fallback, not an error."""


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "rapport"


def cached(key: str, produce) -> bytes:
    """produce() -> bytes, stored under a hash of key (and rapport's version)."""
    digest = hashlib.sha256(f"{__version__}\0{key}".encode()).hexdigest()
    path = cache_root() / "render" / digest[:2] / digest
    if path.is_file():
        return path.read_bytes()
    data = produce()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def enabled() -> bool:
    """RAPPORT_TOOLS=off makes every external renderer count as missing (tests, CI, quick builds)."""
    return os.environ.get("RAPPORT_TOOLS", "").lower() not in ("off", "0", "no", "false")


def tool(command: str) -> str | None:
    """Absolute path of a command (a name on PATH, or a path), or None (also when tools are off)."""
    if not command or not enabled():
        return None
    p = Path(command).expanduser()
    if p.is_absolute() or os.sep in command:
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    return shutil.which(command)


def run(argv: list[str], input: bytes | None = None, timeout: float = 120, cwd: str | None = None,
        env: dict | None = None) -> bytes:
    """Run a command; stdout as bytes. ToolError with the stderr tail if it fails."""
    try:
        res = subprocess.run(argv, input=input, capture_output=True, timeout=timeout, cwd=cwd,
                             env={**os.environ, **(env or {})})
    except FileNotFoundError:
        raise ToolError(f"{argv[0]}: not found") from None
    except subprocess.TimeoutExpired:
        raise ToolError(f"{Path(argv[0]).name} timed out after {timeout:.0f}s") from None
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).decode("utf-8", "replace").strip().splitlines()[-6:]
        raise ToolError(f"{Path(argv[0]).name} failed (exit {res.returncode}): " + " | ".join(tail))
    return res.stdout
