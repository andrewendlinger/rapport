"""config → parse → render → report.html (+ .rapport/blocks.json for anchoring)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from .parse import Diag, Document, parse
from .render import Renderer
from .util import atomic_write

STATE_FILE = "state.json"
BLOCKS_FILE = "blocks.json"


def read_state(report_dir: Path) -> dict:
    p = Path(report_dir) / ".rapport" / STATE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1}


def write_state(report_dir: Path, state: dict) -> None:
    atomic_write(Path(report_dir) / ".rapport" / STATE_FILE, json.dumps(state, indent=2) + "\n")


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
    blocks: dict = field(default_factory=dict)
    assets: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def messages(self) -> list[str]:
        name = self.config.get("report.source", "report.md") if self.config else "report.md"
        out = [f"error: {d.format(name)}" for d in self.errors]
        out += [f"warning: {w}" for w in self.warnings]
        return out


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
    cfg, body = config_mod.load_for_report(report_dir, sets)
    state = read_state(report_dir)
    doc = parse(body, cfg.registry, cfg)

    static = Renderer(cfg, serve=False, state=state, strict=strict)
    html = static.render_document(doc)
    errors = _dedupe_diags(doc.errors + static.errors)
    warnings = list(cfg.warnings) + [w.format(cfg.get("report.source")) for w in doc.warnings + static.warnings]

    served_html = None
    if serve_data is not None:
        live = Renderer(cfg, serve=True, extra_data=serve_data, state=state)
        served_html = live.render_document(doc)

    if write:
        atomic_write(cfg.output_path, html)
        atomic_write(cfg.state_dir / BLOCKS_FILE, json.dumps(
            {"version": state.get("version", 1), "blocks": static.blocks}, ensure_ascii=False) + "\n")
        gi = cfg.state_dir / ".gitignore"
        if not gi.exists():
            atomic_write(gi, "server.json\nserver.log\n")
    return BuildResult(html=html, served_html=served_html, config=cfg, doc=doc, errors=errors,
                       warnings=_unique(warnings), blocks=static.blocks, assets=sorted(static.asset_paths))


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
