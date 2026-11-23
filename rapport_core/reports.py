"""Every report under some folders, and what each is up to: `rapport list`, `rapport hub`
and the `reports` module (modules/block/reports), which shows them on a page.

A report is a folder holding `.rapport/` (made by its first build or serve). What is
shown comes from files rapport already keeps: the front matter of report.md (title,
subtitle, date, status, tags), state.json (version), feedback.json (open items),
server.json (last serve, URL) and confluence/page.json (the recorded page). Nothing is
written, so listing is safe while the reports are being served or edited.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from .util import STATE_DIR, RapportError, read_state, state_dir

SOURCE = "report.md"
OUTPUT = "report.html"
MAX_DEPTH = 8
# folders never searched: tools' caches and environments, and hidden folders
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "site-packages"}
HUB_TEMPLATE = """+++
title = "Rapports"
subtitle = "Every rapport, most recent first"

[modules.feedback]
enabled = false

[modules.header]
show_version = false

[modules.reports]
enabled = true

[serve]
port = 4840
+++

::: reports
:::
"""


# ── where to look ──────────────────────────────────────────────────────────── #
def roots_of(cfg) -> list[Path]:
    """[hub] roots, absolute (config resolved them against the file that set them)."""
    return [Path(r) for r in cfg.get("hub.roots", []) or [] if isinstance(r, str) and r]


def excludes_of(cfg) -> list[Path]:
    return [Path(r).resolve() for r in cfg.get("hub.exclude", []) or [] if isinstance(r, str) and r]


def hub_dir(cfg) -> Path:
    """[hub] dir, or $XDG_STATE_HOME/rapport/hub (~/.local/state/rapport/hub)."""
    configured = cfg.get("hub.dir", "")
    if configured:
        return Path(configured)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base).expanduser() / "rapport" / "hub"


def find(roots: list[Path], exclude: list[Path] = (), depth: int = MAX_DEPTH) -> list[Path]:
    """The report folders under the roots, each once. Hidden folders and
    SKIP_DIRS are skipped, and a report's own folders aren't searched for more reports."""
    found, seen = [], set()
    exclude = [Path(e).resolve() for e in exclude]
    for root in roots:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        base = len(root.parts)
        for dirpath, dirnames, _ in os.walk(root):
            here = Path(dirpath)
            if any(here == e or here.is_relative_to(e) for e in exclude):
                dirnames[:] = []
                continue
            if STATE_DIR in dirnames and (here / STATE_DIR).is_dir():
                if here not in seen:
                    seen.add(here)
                    found.append(here)
                dirnames[:] = []
                continue
            if len(here.parts) - base >= depth:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
    return found


# ── one report ─────────────────────────────────────────────────────────────── #
def _stamp(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _front_matter(source: Path) -> tuple[dict, str | None]:
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return {}, f"no {source.name}"
    try:
        fm, body, _ = config_mod.split_front_matter(text)
    except RapportError as e:
        return {}, str(e)
    first = next((line for line in body.split("\n") if line.strip()), "")
    if first.startswith("# ") and "title" not in fm:        # a leading "# Title" is the title
        fm["title"] = first[2:].strip()
    return fm, None


def source_of(report_dir: Path) -> Path:
    """report.md, or the [report] source the nearest rapport.toml names (a full config load
    per report would be slow, and only this one setting moves the file)."""
    for d in reversed(config_mod.project_dirs_for(report_dir)):
        f = d / config_mod.PROJECT_FILE
        if f.is_file():
            try:
                src = config_mod.read_toml(f).get("report", {}).get("source")
            except RapportError:
                continue
            if isinstance(src, str) and src:
                return report_dir / src
    return report_dir / SOURCE


def _home(path: Path) -> str:
    home = Path.home().resolve()
    return "~/" + str(path.relative_to(home)) if path.is_relative_to(home) and path != home else str(path)


def project_of(report_dir: Path) -> str:
    """The git repository the report is in, else the folder holding its reports/ folder
    (rapport's default place for reports), else the folder above the report."""
    repo = config_mod.git_root(report_dir)
    if repo:
        return repo.name
    up = report_dir.parent
    if up.name == "reports" and up.parent != up:
        return up.parent.name
    return up.name


def confluence_url(site: str, page_id) -> str:
    """The page's address on [confluence] site (https://example.atlassian.net/wiki), or ""."""
    if not site or not page_id:
        return ""
    return f"{site.rstrip('/')}/pages/viewpage.action?pageId={page_id}"


def info(report_dir: Path, site: str = "", live: bool = True) -> dict:
    """What `rapport list --json` prints for one report. live: ask a server that
    server.json says runs whether it really answers (a stale pid lies)."""
    from .serve import running
    report_dir = report_dir.resolve()
    state = state_dir(report_dir)
    source = source_of(report_dir)
    fm, error = _front_matter(source)
    meta = fm.get("meta") if isinstance(fm.get("meta"), dict) else {}
    pick = lambda k: fm.get(k, meta.get(k))         # noqa: E731 - top level or [meta]
    tags = pick("tags")
    tags = [str(t) for t in tags] if isinstance(tags, list) else [str(tags)] if tags else []

    server = _json(state / "server.json")
    serving = (running(report_dir) or {}).get("url") if live and server.get("pid") else None
    feedback = _json(state / "feedback.json").get("items")
    open_items = sum(1 for i in feedback if isinstance(i, dict) and i.get("status") == "open") \
        if isinstance(feedback, list) else 0
    page = _json(state / "confluence" / "page.json")

    edited = _mtime(source)
    started = server.get("started")
    try:
        served_ts = datetime.fromisoformat(started).timestamp() if started else None
    except (TypeError, ValueError):
        served_ts = None
    # not server.log: every running server writes it when a module or the user config changes
    times = [t for t in (edited, served_ts, _mtime(state / "feedback.json"), _mtime(state / "state.json")) if t]
    out = {
        "dir": str(report_dir),
        "where": _home(report_dir),
        "project": project_of(report_dir),
        "title": str(pick("title") or report_dir.name),
        "subtitle": str(pick("subtitle") or ""),
        "date": str(pick("date") or ""),
        "status": str(pick("status") or ""),
        "tags": tags,
        "thumbnail": str(pick("thumbnail") or ""),
        "version": read_state(report_dir).get("version", 1),
        "open_feedback": open_items,
        "edited": _stamp(edited),
        "served": started or None,
        "serving": serving,
        "active": _stamp(max(times)) if times else None,
        "confluence": None,
    }
    if page.get("page_id"):
        out["confluence"] = {"page_id": str(page["page_id"]), "version": page.get("version"),
                             "title": page.get("title"), "recorded": page.get("recorded"),
                             "url": confluence_url(site, page["page_id"])}
    if error:
        out["error"] = error
    return out


_RECENT: dict = {}          # (roots, exclude, site, live) → (time, rows, stamps): a page renders twice per build
RECENT_S = 3


def collect(roots: list[Path], exclude: list[Path] = (), site: str = "", live: bool = True,
            cache: bool = False) -> list[dict]:
    """Every report under the roots, most recently active first. cache: reuse a result
    from the last few seconds (copies, so callers may add to the rows)."""
    key = (tuple(map(str, roots)), tuple(map(str, exclude)), site, live)
    hit = _RECENT.get(key) if cache else None
    if hit and time.time() - hit[0] < RECENT_S and hit[2] == _stamps(hit[1]):
        return copy.deepcopy(hit[1])
    rows = [info(d, site, live) for d in find(roots, exclude)]
    rows.sort(key=lambda r: (r["active"] or "", r["dir"]), reverse=True)
    if cache:
        _RECENT.clear()
        _RECENT[key] = (time.time(), copy.deepcopy(rows), _stamps(rows))
    return rows


def _stamps(rows: list[dict]) -> tuple:
    """The watched files' mtimes: a cached result is stale once one moves (a serve, an edit)."""
    def stamp(p: Path):
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None
    return tuple(stamp(f) for r in rows for f in watch_files(Path(r["dir"])))


def listed(cfg, page_dir: Path | None = None) -> list[Path]:
    """The report folders a page (or `rapport list`) shows: under [hub] roots, minus [hub] exclude
    and the page's own folder. A page's routes accept only these."""
    return find(roots_of(cfg), excludes_of(cfg) + ([Path(page_dir)] if page_dir else []))


def watch_files(report_dir: Path) -> list[Path]:
    """The files whose change alters a report's row (a page that lists it rebuilds on them):
    report.html too, where its thumbnail comes from. Not server.log: a running server writes
    it on every build."""
    state = state_dir(report_dir)
    return [source_of(Path(report_dir)), Path(report_dir) / OUTPUT, state / "server.json", state / "state.json", state / "feedback.json",
            state / "confluence" / "page.json"]


# ── `rapport list` ─────────────────────────────────────────────────────────── #
def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return "-"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    days = (now.date() - then.date()).days
    if days <= 0:
        return then.strftime("today %H:%M")
    if days == 1:
        return "yesterday"
    return f"{days} d ago" if days < 14 else then.strftime("%Y-%m-%d")


def flags(row: dict) -> list[str]:
    """The labels a row gets besides its tags: status, serving, open feedback, Confluence."""
    out = [row["status"]] if row["status"] else []
    if row["serving"]:
        out.append("serving")
    if row["open_feedback"]:
        out.append(f"{row['open_feedback']} open")
    if row["confluence"]:
        out.append(f"Confluence v{row['confluence']['version']}" if row["confluence"].get("version")
                   else "Confluence")
    return out


def list_text(rows: list[dict], now: datetime | None = None) -> str:
    now = now or datetime.now()
    lines = []
    for r in rows:
        labels = " ".join(f"[{x}]" for x in flags(r) + [f"#{t}" for t in r["tags"]])
        lines.append(f"{_ago(r['active'], now):<12} {r['title']}  ({r['project']}, v{r['version']})"
                     + (f"  {labels}" if labels else ""))
        lines.append(f"{'':<12} {r['where']}" + (f"  → {r['serving']}" if r["serving"] else ""))
    return "\n".join(lines)


def list_cmd(cfg, roots: list[str], as_json: bool) -> int:
    chosen = [Path(r).expanduser().resolve() for r in roots] if roots else roots_of(cfg)
    if not chosen:
        raise RapportError("no folders to search: give them (`rapport list ~/work`) or set "
                           "[hub] roots in your user config (`rapport config paths` shows where)")
    missing = [str(r) for r in chosen if not r.is_dir()]
    rows = collect(chosen, excludes_of(cfg) + [hub_dir(cfg)], cfg.get("confluence.site", ""))
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    for m in missing:
        print(f"note: {m} is not a folder", file=sys.stderr)
    print(list_text(rows) if rows else f"no reports under {', '.join(map(_home, chosen))}")
    return 0


# ── `rapport hub` ──────────────────────────────────────────────────────────── #
def ensure_hub(cfg) -> Path:
    """The hub's report folder, with its report.md written the first time. The file is
    yours after that: edit it (more text, another title, a second `::: reports`) freely."""
    d = hub_dir(cfg)
    src = d / SOURCE
    if not src.exists():
        d.mkdir(parents=True, exist_ok=True)
        src.write_text(HUB_TEMPLATE, encoding="utf-8")
        print(f"created {src}")
    return d
