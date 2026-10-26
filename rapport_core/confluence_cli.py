"""CLI glue for `rapport export confluence` and `rapport confluence record|diff|check|prepare`."""

from __future__ import annotations

import json
from pathlib import Path

from .util import RapportError, atomic_write, ensure_state_dir

STATE = "confluence"


def state_folder(report_dir: Path) -> Path:
    d = ensure_state_dir(report_dir) / STATE
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_page(path: str) -> dict:
    """A fetched page: the JSON a getConfluencePage call returns ({id, title,
    version: {number}, body} with an HTML body). The id, version and title are what
    the guards against overwriting rely on, so plain HTML isn't accepted."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise RapportError(f"no such file: {path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RapportError(f"{path}: not a page file; save the JSON getConfluencePage returns "
                           "(contentFormat \"html\")") from None
    body = data.get("body") if isinstance(data, dict) else None
    if isinstance(body, dict):      # {"storage": {...}} or {"view": ...} shapes
        body = (body.get("value") or next((v.get("value") for v in body.values() if isinstance(v, dict)), ""))
    version = data.get("version") if isinstance(data, dict) else None
    version = version.get("number") if isinstance(version, dict) else version
    if not isinstance(body, str) or not data.get("id") or not version:
        raise RapportError(f"{path}: needs id, version and an HTML body; fetch with contentFormat \"html\"")
    return {"body": body, "id": str(data["id"]), "title": data.get("title") or "", "version": int(version)}


def export_cmd(args, report_dir: Path) -> int:
    from .confluence import export
    res = export(report_dir, args.set)
    out = Path(args.out).expanduser().resolve() if args.out else state_folder(report_dir) / "export.html"
    if res.ok:
        atomic_write(out, res.body)
    if args.json:
        print(json.dumps({"ok": res.ok, "title": res.title, "body_file": str(out) if res.ok else None,
                          "bytes": len(res.body.encode()), "errors": [e.format("report.md") for e in res.errors],
                          "warnings": [str(w) for w in res.warnings], "handovers": res.handovers},
                         ensure_ascii=False, indent=2))
        return 0 if res.ok else 1
    for e in res.errors:
        print(f"error: {e.format('report.md')}")
    for w in res.warnings:
        print(f"warning: {w}")
    if not res.ok:
        return 1
    print(f"title: {res.title}\nbody: {out} ({len(res.body.encode()) // 1024 + 1} KB)")
    for h in res.handovers:
        print(f"for the user: {h}")
    return 0


def confluence_cmd(args, report_dir: Path) -> int:
    from . import confluence_sync as sync
    if args.action == "diff" and args.local:
        return sync.diff(report_dir, {}, local=True, sets=args.set)
    if not args.page:
        raise RapportError(f"`rapport confluence {args.action}` needs the page: save the getConfluencePage JSON "
                           "(contentFormat \"html\") to a file and pass it")
    page = read_page(args.page)
    if args.action == "record":
        return sync.record(report_dir, page, force=args.force, sets=args.set)
    if args.action == "diff":
        return sync.diff(report_dir, page, sets=args.set)
    if args.action == "prepare":
        return sync.prepare(report_dir, page, args.set, detach=args.detach)
    return sync.check(report_dir, page, args.set)
