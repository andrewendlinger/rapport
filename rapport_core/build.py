"""config → parse → render → report.html (+ blocks.json in the state folder, for anchoring).

The state folder (.rapport/ beside report.md, see util.state_dir) ignores itself
in git through a .gitignore the build keeps in line with report.commit_feedback.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from .parse import Diag, Document, parse
from .render import Renderer
from .util import RapportError, atomic_write, ensure_state_dir, state_dir

STATE_FILE = "state.json"
BLOCKS_FILE = "blocks.json"


def read_state(report_dir: Path) -> dict:
    p = state_dir(report_dir) / STATE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1}


def write_state(report_dir: Path, state: dict) -> None:
    atomic_write(ensure_state_dir(report_dir) / STATE_FILE, json.dumps(state, indent=2) + "\n")


def bump_version(report_dir: Path, note: str = "") -> int:
    state = read_state(report_dir)
    state["version"] = int(state.get("version", 1)) + 1
    if note:
        state.setdefault("history", []).append({"version": state["version"], "note": note})
    write_state(report_dir, state)
    return state["version"]


@dataclass
class BuildResult:
    html: str
    served_html: str | None
    config: object
    doc: Document | None
    errors: list[Diag] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)       # git advice for the user: never a failure
    blocks: dict = field(default_factory=dict)
    assets: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def messages(self) -> list[str]:
        name = self.config.get("report.source", "report.md") if self.config else "report.md"
        out = [f"error: {d.format(name)}" for d in self.errors]
        out += [f"warning: {w}" for w in self.warnings]
        return out + self.notes()

    def notes(self) -> list[str]:
        return [f"note for the user: {n}" for n in self.notices]


def _dedupe_diags(diags: list[Diag]) -> list[Diag]:
    seen, out = set(), []
    for d in diags:
        key = (d.line, d.message)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def build(report_dir: Path, sets=None, serve_data: dict | None = None, write: bool = True,
          strict: bool = False) -> BuildResult:
    """Build the static report. With serve_data, also render the served page
    (serve-only modules included, serve_data merged into the page data)."""
    report_dir = Path(report_dir).resolve()
    try:
        cfg, body = config_mod.load_for_report(report_dir, sets)
    except RapportError:
        if write and (report_dir / "report.md").is_file():
            with contextlib.suppress(OSError):  # a failed build still leaves an ignored state folder
                ensure_state_dir(report_dir)
        raise
    state = read_state(report_dir)
    doc = parse(body, cfg.registry, cfg)

    static = Renderer(cfg, serve=False, state=state, strict=strict)
    html = static.render_document(doc)
    errors = _dedupe_diags(doc.errors + static.errors)
    warnings = list(cfg.warnings) + [w.format(cfg.get("report.source")) for w in doc.warnings + static.warnings]

    served_html, assets = None, set(static.asset_paths)
    if serve_data is not None:
        live = Renderer(cfg, serve=True, extra_data=serve_data, state=state)
        served_html = live.render_document(doc)
        assets |= live.asset_paths      # serve-only modules' CSS may url() files too

    commit = bool(cfg.get("report.commit_feedback", False))
    if write:
        atomic_write(cfg.output_path, html)
        atomic_write(ensure_state_dir(report_dir, commit) / BLOCKS_FILE, json.dumps(
            {"version": state.get("version", 1), "blocks": static.blocks}, ensure_ascii=False) + "\n")
    return BuildResult(html=html, served_html=served_html, config=cfg, doc=doc, errors=errors,
                       warnings=_unique(warnings), notices=git_notices(cfg.state_dir, commit), blocks=static.blocks,
                       assets=sorted(assets))


def _git(cwd: Path, *args) -> subprocess.CompletedProcess | None:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        # core.fsmonitor names a command: a repo's own config must not run code on every build
        return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(cwd), *args], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=3, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def git_notices(sd: Path, commit_feedback: bool) -> list[str]:
    """What git gets wrong about the state folder: files in it that are ignored
    but still tracked, and commit_feedback undone by an outer ignore. The fix
    changes git state, so it's the user's call, not a build problem. Runs git
    only if it's installed and a .git sits anywhere above (config.git_root
    stops at $HOME)."""
    root = next((d for d in sd.parents if (d / ".git").exists()), None) if sd.is_dir() else None
    if root is None or not shutil.which("git"):
        return []
    out, name = [], sd.name + "/"
    try:
        here = Path.cwd().resolve()
    except OSError:                                     # cwd deleted under a running server
        here = None

    def cmd(verb: str, files: list[str]) -> str:       # runnable from the cwd
        if here and here.is_relative_to(root):
            return f"git {verb} " + " ".join(shlex.quote(os.path.relpath(sd / f, here)) for f in files)
        return f"git -C {shlex.quote(str(root))} {verb} " + " ".join(
            shlex.quote(str((sd / f).relative_to(root))) for f in files)

    res = _git(sd, "ls-files", "-ci", "--exclude-standard", "-z", ".")
    tracked = [f for f in res.stdout.split("\0") if f] if res and res.returncode == 0 else []
    if tracked:
        out.append(f"{name} is git-ignored now, but these files in it are still tracked: {', '.join(tracked)}; "
                   f"untrack them with: {cmd('rm --cached', tracked)}")
    if commit_feedback:
        res = _git(sd, "check-ignore", "-q", "feedback.json")
        if res and res.returncode == 0:
            out.append(f"commit_feedback = true, but an outer .gitignore still ignores {name} "
                       f"({cmd('check-ignore -v', ['feedback.json'])} shows which)")
    return out


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
