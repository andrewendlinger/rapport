"""The writing log: how the reader corrects the agent's writing, kept when `[writing] log = true`
for `rapport writing digest` and the playbook that distils it into rules (references/writing-digest.md).

An entry per resolved or reopened feedback item, and per block edited between feedback rounds:
the block's Markdown before and after, its heading path, the neighbouring blocks' text, the
actions, comment and the agent's note. Never a whole report: a round gets one small record (title,
headings, length). Entries are appended to monthly JSON Lines files in a folder beside the user
config, as the log spans every report, readable by the user only. Emails, URLs and
`writing.log_redact` terms are blanked as entries are written. Nothing in it reaches report.html,
a served page or an export.

Who edited between rounds: rapport can't see who typed. Agent-side commands leave a snapshot of
the blocks (.rapport/writing-snapshot.json). Those that end the agent's work (build, lint,
feedback resolve) mark it "end"; those that begin it (feedback list, add, reopen) first log what
changed since an "end" snapshot, as edited by "owner?", then mark it "start". An agent's own edits
are followed by lint or resolve, which refresh the snapshot first, so they aren't taken for the
reader's; edits the reader makes while the agent is mid-round are missed rather than misfiled.

Capturing never fails the command it runs in: a problem is one line on stderr.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from .changes import block_steps, changes, outline, word_diff
from .store import Journal, StateFile
from .util import RapportError, collapse_ws, short_hash

LOG_DIR, LOG_DIR_BESIDE_FILE = "writing-log", "rapport-writing-log"
DIGEST_FILE = "digest.json"
SNAPSHOT, SNAPSHOT_LOCK = "writing-snapshot.json", "writing-snapshot.lock"
TEXT_MAX = 2000         # characters of a block's Markdown, before and after
NEAR_MAX = 300          # characters of a neighbouring block's text
QUOTE_MAX = 500
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"\bhttps?://[^\s)\]>\"']+", re.I)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── where, and whether ─────────────────────────────────────────────────────────── #
def log_dir() -> Path | None:
    """writing-log/ beside the user config (rapport-writing-log/ beside a RAPPORT_CONFIG file);
    None without a user config (RAPPORT_CONFIG=/dev/null)."""
    paths = config_mod.user_paths(strict=False)
    if paths.learned_file is None:
        return None
    beside_file = paths.learned_file.name == config_mod.LEARNED_PROJECT
    return paths.learned_file.parent / (LOG_DIR_BESIDE_FILE if beside_file else LOG_DIR)


def enabled(cfg) -> bool:
    return cfg.get("writing.log", False) is True and log_dir() is not None


_GIT_OK: dict[Path, str | None] = {}


def git_problem(folder: Path) -> str | None:
    """Why the log may not be written there: inside a git work tree and not ignored, one
    `git add -A` from being committed. None: fine."""
    if folder not in _GIT_OK:
        from .build import _git
        probe = folder if folder.is_dir() else folder.parent
        inside = _git(probe, "rev-parse", "--is-inside-work-tree")
        problem = None
        if inside and inside.returncode == 0 and inside.stdout.strip() == "true":
            ignored = _git(probe, "check-ignore", "-q", str(folder / "x.jsonl"))
            if not ignored or ignored.returncode != 0:
                problem = (f"{folder} is in a git repository and not ignored; add it to .gitignore "
                           "(the log holds report text)")
        _GIT_OK[folder] = problem
    return _GIT_OK[folder]


def redactor(cfg):
    """A function blanking emails, URLs and the writing.log_redact terms (case-insensitive;
    /…/ for a regular expression)."""
    pats = []
    for term in cfg.get("writing.log_redact", []) or []:
        term = str(term)
        if len(term) > 2 and term.startswith("/") and term.endswith("/"):
            pats.append(re.compile(term[1:-1], re.I))
        elif term.strip():
            pats.append(re.compile(re.escape(term), re.I))

    def redact(text):
        if not isinstance(text, str):
            return text
        text = URL_RE.sub("[url]", EMAIL_RE.sub("[email]", text))
        for p in pats:
            text = p.sub("[redacted]", text)
        return text
    return redact


def _scrub(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_scrub(v, redact) for v in value]
    if isinstance(value, dict):
        return {k: v if k in ("id", "at", "event", "v") else _scrub(v, redact) for k, v in value.items()}
    return value


def _clip(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= n else text[:n].rstrip() + " …"


def _home(path: Path) -> str:
    home = Path.home()
    return "~/" + str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


# ── the log ─────────────────────────────────────────────────────────────────────── #
class Log:
    def __init__(self, folder: Path):
        self.dir = folder

    def files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl")) if self.dir.is_dir() else []

    def append(self, entry: dict) -> None:
        Journal(self.dir / f"{entry['at'][:7]}.jsonl").append(json.dumps(entry, ensure_ascii=False))

    def entries(self) -> list[dict]:
        out = []
        for f in self.files():
            for line in f.read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError):
                    if isinstance(e := json.loads(line), dict):
                        out.append(e)
        return out

    def rewrite(self, keep) -> int:
        """Drop the entries keep(entry) is false for; the number dropped."""
        dropped = 0
        for f in self.files():
            lines = f.read_text(encoding="utf-8").splitlines()
            kept = [ln for ln in lines if not _parses(ln) or keep(json.loads(ln))]
            if len(kept) != len(lines):
                dropped += len(lines) - len(kept)
                fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("".join(ln + "\n" for ln in kept))
                os.replace(tmp, f)
        return dropped

    def digest_state(self) -> dict:
        try:
            return json.loads((self.dir / DIGEST_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def mark_digested(self) -> str:
        at = now()
        Journal(self.dir / DIGEST_FILE).replace(json.dumps({"last": at}))
        return at


def _parses(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


def write(cfg, entries: list[dict]) -> None:
    """Append entries, redacted; never raises (a problem is one line on stderr)."""
    if not entries:
        return
    try:
        folder = log_dir()
        problem = git_problem(folder)
        if problem:
            print(f"writing log not written: {problem}", file=sys.stderr)
            return
        redact, log = redactor(cfg), Log(folder)
        for e in entries:
            log.append({"v": 1, **_scrub(e, redact)})
    except Exception as e:  # noqa: BLE001 - logging must never fail the agent's command
        print(f"writing log: {e}", file=sys.stderr)


# ── context ─────────────────────────────────────────────────────────────────────── #
def _report(report_dir: Path, version=None, rnd=None) -> dict:
    return {"key": short_hash(str(report_dir), 8), "path": _home(report_dir), "version": version, "round": rnd}


def _neighbours(order: list[dict], at: int | None) -> dict:
    """The text of the blocks around position `at` in an outline (`at` itself excluded)."""
    if at is None:
        return {}
    prev = order[at - 1]["text"] if at > 0 else None
    nxt = order[at + 1]["text"] if at + 1 < len(order) else None
    return {"prev": _clip(prev, NEAR_MAX), "next": _clip(nxt, NEAR_MAX)}


def _round_record(report_dir: Path, res, rnd) -> dict:
    blocks = res.blocks
    leaves = [b for b in blocks.values() if not b.get("children")]
    meta = getattr(res.doc, "meta", None) or {}
    return {"event": "round", "at": now(), "report": _report(report_dir, None, rnd),
            "title": str(meta.get("title") or report_dir.name),
            "headings": [collapse_ws(b.get("text") or "") for b in outline(blocks)
                         if b.get("module") == "heading"][:40],
            "words": sum(len((b.get("text") or "").split()) for b in leaves),
            "theme": res.config.get("theme.name")}


# ── the snapshot: telling the reader's edits from the agent's ───────────────────── #
def _snapshot_file(report_dir: Path) -> StateFile:
    return StateFile(report_dir, SNAPSHOT, SNAPSHOT_LOCK)


def _snap(blocks: dict) -> list[dict]:
    return [{**e, "src": blocks[e["id"]].get("src") or "",
             "heading_path": blocks[e["id"]].get("heading_path") or []} for e in outline(blocks)]


def owner_edits(report_dir: Path, cfg, blocks: dict, command: str) -> None:
    """At a command that begins the agent's work: log the blocks edited since the agent's last
    finished work, as the reader's (probably), then remember the blocks as they are now."""
    if not enabled(cfg) or not blocks:      # no blocks: a failed build, not a report emptied by hand
        return
    try:
        _owner_edits(report_dir, cfg, blocks, command)
    except Exception as e:  # noqa: BLE001 - logging must never fail the agent's command
        print(f"writing log: {e}", file=sys.stderr)


def _owner_edits(report_dir: Path, cfg, blocks: dict, command: str) -> None:
    with _snapshot_file(report_dir).edit() as snap:
        old, new = snap.get("blocks"), _snap(blocks)
        entries = []
        if snap.get("by") == "end" and isinstance(old, list):
            for _, i, j in block_steps(old, new):
                was, now_ = (old[i] if i is not None else None), (new[j] if j is not None else None)
                entries.append({
                    "event": "edited", "at": now(), "id": "e:" + short_hash(
                        f"{report_dir}|{snap.get('at')}|{i}|{j}|{(now_ or was)['key']}", 10),
                    "report": _report(report_dir), "by": "owner?", "via": command,
                    "where": {"heading_path": (now_ or was).get("heading_path") or [],
                              "module": (now_ or was).get("module")},
                    "before": _clip(was["src"], TEXT_MAX) if was else None,
                    "after": _clip(now_["src"], TEXT_MAX) if now_ else None,
                    "neighbours": _neighbours(new, j) if j is not None else _neighbours(old, i)})
        snap.clear()
        snap.update({"at": now(), "by": "start", "command": command, "blocks": new,
                     "rounds_logged": snap.get("rounds_logged", [])})
    write(cfg, entries)


def agent_done(report_dir: Path, cfg, blocks: dict, command: str) -> None:
    """At a command that ends a stretch of the agent's work (build, lint, resolve): edits up to
    here are the agent's, so the snapshot moves on without logging them."""
    if not enabled(cfg) or not blocks:
        return
    try:
        with _snapshot_file(report_dir).edit() as snap:
            rounds = snap.get("rounds_logged", [])
            snap.clear()
            snap.update({"at": now(), "by": "end", "command": command, "blocks": _snap(blocks),
                         "rounds_logged": rounds})
    except Exception as e:  # noqa: BLE001 - logging must never fail the agent's command
        print(f"writing log: {e}", file=sys.stderr)


# ── feedback items ──────────────────────────────────────────────────────────────── #
def _item_fields(item: dict) -> dict:
    labels = item.get("action_labels") or {}
    return {"id": item["id"], "via": item.get("via", "browser"), "actions": item.get("actions", []),
            "labels": [labels.get(a, {}).get("label", a) for a in item.get("actions", [])],
            "comment": item.get("comment") or "", "note": (item.get("resolution") or {}).get("note")}


def _before(item: dict) -> str | None:
    t = item.get("target") or {}
    return _clip(t.get("src"), TEXT_MAX) if t.get("kind") != "report" else None


def resolved(report_dir: Path, cfg, items: list[dict], version) -> None:
    """After `feedback resolve`: an entry per item, with the block's Markdown when the comment was
    made and now. The change bars' own diff says which blocks answer which item; a block it can't
    place is found again by its passage (feedback.locate)."""
    if not enabled(cfg):
        return
    try:
        from .build import build
        from .feedback import Store, locate
        res = build(report_dir, None, write=False)
        blocks, order = res.blocks, outline(res.blocks)
        pos = {e["id"]: k for k, e in enumerate(order)}
        ch = changes(report_dir, blocks, Store(report_dir).items()) or {}
        source = res.config.source_path.read_text(encoding="utf-8").split("\n")
        entries = []
        rnd = next((i.get("round") for i in items if i.get("round") is not None), None)
        with _snapshot_file(report_dir).edit() as snap:
            logged = snap.setdefault("rounds_logged", [])
            if rnd is not None and rnd not in logged:
                logged.append(rnd)
                entries.append(_round_record(report_dir, res, rnd))
        for item in items:
            t = item.get("target") or {}
            bars = [b for b in ch.get("changed", []) if item["id"] in b.get("items", [])]
            cuts = [r for r in ch.get("removed", []) if item["id"] in r.get("items", [])]
            after, at, found = None, None, "n/a"
            if bars:
                after = "\n\n".join(blocks[b["id"]].get("src") or "" for b in bars if b["id"] in blocks)
                at, found = pos.get(bars[0]["id"]), "changed"
            elif cuts:
                at, found = pos.get(cuts[0].get("before")), "deleted"
            elif t.get("kind") != "report":
                lines, status = locate(t, "\n".join(source))
                if lines:
                    after, found = "\n".join(source[lines[0] - 1:lines[1]]), "unchanged?" if status == "exact" else status
                else:
                    found = "gone"
            entries.append({
                "event": "resolved", "at": now(), "id": "e:" + short_hash(f"{report_dir}|{item['id']}|{version}", 10),
                "report": _report(report_dir, version, item.get("round")), "item": _item_fields(item),
                "where": {"heading_path": t.get("heading_path") or [], "module": t.get("module"), "kind": t.get("kind")},
                "quote": _clip(t.get("quote"), QUOTE_MAX), "before": _before(item),
                "after": _clip(after, TEXT_MAX), "after_by": "agent", "found": found,
                "neighbours": _neighbours(order, at)})
        write(cfg, entries)
        agent_done(report_dir, cfg, blocks, "feedback resolve")
    except Exception as e:  # noqa: BLE001 - logging must never fail the agent's command
        print(f"writing log: {e}", file=sys.stderr)


def reopened(report_dir: Path, cfg, items: list[dict]) -> None:
    """Before `feedback reopen` clears them: the fix the reader rejected, and its note."""
    if not enabled(cfg):
        return
    try:
        from .feedback import locate
        source = cfg.source_path.read_text(encoding="utf-8")
        lines_of = source.split("\n")
        entries = []
        for item in items:
            t = item.get("target") or {}
            lines, _ = locate(t, source) if t.get("kind") != "report" else (None, "n/a")
            entries.append({
                "event": "reopened", "at": now(),
                "id": "e:" + short_hash(f"{report_dir}|{item['id']}|reopen|{now()}", 10),
                "report": _report(report_dir, (item.get("resolution") or {}).get("version"), item.get("round")),
                "item": _item_fields(item),
                "where": {"heading_path": t.get("heading_path") or [], "module": t.get("module"), "kind": t.get("kind")},
                "quote": _clip(t.get("quote"), QUOTE_MAX), "before": _before(item),
                "rejected": _clip("\n".join(lines_of[lines[0] - 1:lines[1]]), TEXT_MAX) if lines else None})
        write(cfg, entries)
    except Exception as e:  # noqa: BLE001 - logging must never fail the agent's command
        print(f"writing log: {e}", file=sys.stderr)


def rule_decision(cfg, rule: str, decision: str, kind: str = "rule", scope: str | None = None) -> None:
    """A proposed rule the user approved or turned down: the digest shows both, so a
    rejected one isn't proposed again."""
    if enabled(cfg):
        write(cfg, [{"event": "rule", "at": now(), "id": "e:" + short_hash(f"{rule}|{decision}|{now()}", 10),
                     "decision": decision, "rule": rule, "kind": kind, "scope": scope}])


# ── reading it: status, digest, forget ──────────────────────────────────────────── #
WORD_RE = re.compile(r"[a-z0-9']+")
STOP = set("a an the and or of to in on for is are was were be it this that with as by at from".split())


def _plain(md: str | None) -> str:
    from .feedback import plain
    return plain(md or "") if md else ""


def recurring_cuts(entries: list[dict], top: int = 15) -> list[tuple[str, int, int]]:
    """Word sequences (2–4 words) the reader's corrections took out again and again:
    (phrase, times, reports), seen at least 3 times in at least 2 reports."""
    times, reports = Counter(), defaultdict(set)
    for e in entries:
        if e.get("event") not in ("resolved", "edited") or not e.get("before"):
            continue
        _, removed = word_diff(_plain(e["before"]), _plain(e.get("after")))
        seen = set()
        for run in removed:
            words = WORD_RE.findall(run.lower())
            for n in (2, 3, 4):
                for k in range(len(words) - n + 1):
                    gram = words[k:k + n]
                    if all(w in STOP for w in gram):
                        continue
                    seen.add(" ".join(gram))
        for g in seen:
            times[g] += 1
            reports[g].add((e.get("report") or {}).get("key"))
    found = sorted(((g, times[g], len(reports[g])) for g in times if times[g] >= 3 and len(reports[g]) >= 2),
                   key=lambda x: (-x[2], -x[1], -len(x[0])))
    out = []
    for g, t, r in found:       # a phrase inside a longer one that's as frequent adds nothing
        if not any(g in o and t == ot for o, ot, _ in out):
            out.append((g, t, r))
    return out[:top]


def stats(entries: list[dict]) -> list[str]:
    acts, by, ratios = Counter(), Counter(), defaultdict(list)
    for e in entries:
        ev = e.get("event")
        if ev == "resolved":
            for label in (e.get("item") or {}).get("labels") or ["Comment"]:
                acts[label] += 1
                b, a = _plain(e.get("before")), _plain(e.get("after"))
                if b and e.get("found") in ("changed", "deleted"):
                    ratios[label].append(len(a.split()) / max(1, len(b.split())))
            by[(e.get("item") or {}).get("via", "browser")] += 1
        elif ev in ("edited", "reopened"):
            by[ev] += 1
    out = []
    if acts:
        out.append("resolved items by action: " + ", ".join(f"{k} {v}" for k, v in acts.most_common()))
    if by:
        out.append("by source: " + ", ".join(f"{k} {v}" for k, v in by.most_common())
                   + "   (edited = blocks probably edited by the reader; reopened = rejected fixes)")
    for label, rs in sorted(ratios.items()):
        if len(rs) >= 3:
            out.append(f"length after/before, {label}: median {statistics.median(rs):.2f} (n={len(rs)})")
    return out


def _since(entries: list[dict], since: str | None) -> list[dict]:
    return [e for e in entries if not since or (e.get("at") or "") > since]


def status(cfg, report_dir: Path) -> int:
    folder = log_dir()
    on = cfg.get("writing.log", False) is True
    print(f"writing log: {'on' if on and folder else 'off'} for {report_dir} "
          f"(writing.log = {str(on).lower()} · {cfg.origin('writing.log')})")
    if folder is None:
        print("  no user config (RAPPORT_CONFIG=/dev/null): nowhere to keep it")
        return 0
    problem = git_problem(folder)
    print(f"  folder: {folder}" + (f"\n  NOT WRITTEN: {problem}" if problem else ""))
    log = Log(folder)
    entries = log.entries()
    last = log.digest_state().get("last")
    keys = {(e.get("report") or {}).get("key") for e in entries if e.get("report")}
    print(f"  entries: {len(entries)} from {len(keys)} report(s); since the last digest "
          f"({last[:10] if last else 'none yet'}): {len(_since(entries, last))}")
    return 0


def _entry_text(e: dict) -> list[str]:
    r, w, it = e.get("report") or {}, e.get("where") or {}, e.get("item") or {}
    head = " › ".join(w.get("heading_path") or []) or "(top)"
    ev = e.get("event")
    what = {"resolved": " · ".join(it.get("labels") or []) or "Comment", "reopened": "REOPENED (fix rejected)",
            "edited": "edited, probably by the reader"}.get(ev, ev)
    out = [f"[{e.get('id')}] {(e.get('at') or '')[:10]} · {r.get('path')} · {head} · {what}"
           + (f" · via {it['via']}" if it.get("via") and it.get("via") != "browser" else "")]
    for key, label in (("comment", "comment"), ("note", "agent's note")):
        if it.get(key):
            out.append(f"  {label}: {it[key]}")
    if e.get("quote"):
        out.append(f"  quote: {e['quote']}")
    for key in ("before", "after", "rejected"):
        if key in e:
            val = e[key]
            text = "(none)" if val is None else _clip(val, 600).replace("\n", "\n    ")
            label = {"after": "after (agent)" if e.get("after_by") == "agent" else "after",
                     "rejected": "rejected fix"}.get(key, key)
            out.append(f"  {label}: {text}")
    if e.get("found") in ("unchanged?", "fuzzy", "moved", "gone"):
        out.append(f"  (after: {e['found']})")
    return out


def digest(cfg, everything: bool = False, as_json: bool = False, done: bool = False) -> int:
    folder = log_dir()
    if folder is None:
        raise RapportError("no user config (RAPPORT_CONFIG=/dev/null): there is no writing log")
    log = Log(folder)
    if done:
        print(f"digest marked done at {log.mark_digested()}: the next digest starts from here")
        return 0
    entries = log.entries()
    last = None if everything else log.digest_state().get("last")
    new = _since(entries, last)
    rules = [e for e in entries if e.get("event") == "rule"]
    material = [e for e in new if e.get("event") in ("resolved", "reopened", "edited")]
    rounds = [e for e in new if e.get("event") == "round"]
    cuts = recurring_cuts(entries)
    if as_json:
        print(json.dumps({"since": last, "entries": new, "recurring_cuts": cuts, "stats": stats(material),
                          "rules": {"current": cfg.get("writing.rules", []), "avoid": cfg.get("writing.avoid", []),
                                    "decided": rules}}, indent=2, ensure_ascii=False))
        return 0
    keys = {(e.get("report") or {}).get("key") for e in material}
    print(f"# writing log digest: {len(material)} entries from {len(keys)} report(s) "
          f"{'since ' + last[:10] if last else '(everything)'}")
    print("# playbook: references/writing-digest.md · when done: rapport writing digest --done\n")
    print("## counts")
    for line in stats(material) or ["(nothing yet)"]:
        print(line)
    print("\n## taken out again and again (whole log; times, reports)")
    for g, t, r in cuts or [("(nothing recurs yet)", 0, 0)]:
        print(f'"{g}" ×{t} in {r} reports' if t else g)
    print("\n## rules now in effect (here)")
    for r in cfg.get("writing.rules", []) or ["(none)"]:
        print(f"- {r}")
    print("avoid: " + ", ".join(f'"{a}"' for a in cfg.get("writing.avoid", []) or []))
    if rules:
        print("\n## proposals already decided")
        for e in rules:
            print(f"- {e.get('decision')} ({(e.get('at') or '')[:10]}): {e.get('rule')}")
    if rounds:
        print("\n## reports in these entries")
        for e in rounds:
            print(f"- {(e.get('report') or {}).get('path')} · round {(e.get('report') or {}).get('round')} · "
                  f"\"{e.get('title')}\" · {e.get('words')} words · theme {e.get('theme')}")
    print("\n## entries")
    for e in material:
        print("\n".join(_entry_text(e)) + "\n")
    return 0


def forget(report: str | None = None, before: str | None = None, entry: str | None = None) -> int:
    if not (report or before or entry):
        raise RapportError("say what to forget: --report <dir>, --before YYYY-MM-DD or --entry <id>")
    folder = log_dir()
    if folder is None:
        raise RapportError("no user config (RAPPORT_CONFIG=/dev/null): there is no writing log")
    key = short_hash(str(Path(report).expanduser().resolve()), 8) if report else None
    if before and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", before):
        raise RapportError("--before takes a date: YYYY-MM-DD")

    def keep(e: dict) -> bool:
        if key and (e.get("report") or {}).get("key") == key:
            return False
        if before and (e.get("at") or "") < before:
            return False
        return not (entry and e.get("id") == entry)
    n = Log(folder).rewrite(keep)
    print(f"forgot {n} entr{'y' if n == 1 else 'ies'}")
    return 0
