"""Reader feedback: the store (feedback.json in the state folder, .rapport/) and
the agent-facing CLI.

Items are written by the review server (browser) and by `rapport feedback add`;
the agent reads them with `rapport feedback`, which re-locates every item in
the *current* report.md (exact → whitespace-insensitive → fuzzy) and joins it
with the configured instruction for its action(s).
"""

from __future__ import annotations

import contextlib
import difflib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .changes import round_for
from .util import RapportError, atomic_write, bump_version, collapse_ws, ensure_state_dir, read_state, state_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

class NotFound(RapportError):
    """No such feedback item (the server answers 404)."""


FEEDBACK_FILE = "feedback.json"
LOCK_FILE = "feedback.lock"
MAX_COMMENT = 10_000
MAX_QUOTE = 5_000
_THREAD_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── store ─────────────────────────────────────────────────────────────────── #
class Store:
    def __init__(self, report_dir: Path):
        self.report_dir = Path(report_dir).resolve()
        self.dir = state_dir(self.report_dir)
        self.path = self.dir / FEEDBACK_FILE

    @contextlib.contextmanager
    def locked(self):
        """Thread lock + an flock on a side file, so the server and the CLI
        (agent resolving while the reader comments) never interleave writes."""
        with _THREAD_LOCK:
            with open(ensure_state_dir(self.report_dir) / LOCK_FILE, "a+") as fh:
                if fcntl:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl:
                        fcntl.flock(fh, fcntl.LOCK_UN)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"format": 1, "next": 1, "items": []}
        except ValueError as e:
            raise RapportError(f"{self.path} is not valid JSON ({e}); fix or move it") from None
        data.setdefault("items", [])
        data.setdefault("next", 1 + max([_num(i.get("id")) for i in data["items"]] or [0]))
        return data

    def save(self, data: dict) -> None:
        atomic_write(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def rev(self) -> int:
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return 0

    def items(self) -> list[dict]:
        return self.load()["items"]

    def add(self, fields: dict, blocks: dict | None = None) -> dict:
        """Create an item; a repeat of an identical open quick action (same
        passage, same actions, no comment) returns the existing item instead.
        With the block index the comment was made on, the item joins the open
        feedback round, or starts one with those blocks as its baseline."""
        with self.locked():
            data = self.load()
            if not fields.get("comment"):
                dup = next((i for i in data["items"] if i.get("status") == "open" and not i.get("comment")
                            and sorted(i.get("actions", [])) == sorted(fields.get("actions", []))
                            and _same_target(i.get("target", {}), fields.get("target", {}))), None)
                if dup:
                    return {**dup, "duplicate": True}
            item = {"id": f"FB-{data['next']}", "status": "open", "created": now(), "updated": now(), **fields}
            if blocks is not None:
                item["round"] = round_for(self.report_dir, data["items"], blocks, fields.get("report_version"))
            data["next"] += 1
            data["items"].append(item)
            self.save(data)
            return item

    def update(self, item_id: str, fields: dict, validate=None) -> dict:
        """Change an item; validate(merged item) may raise to refuse the change."""
        with self.locked():
            data = self.load()
            item = _find(data, item_id)
            if validate:
                validate({**item, **fields})
            item.update(fields)
            item["updated"] = now()
            self.save(data)
            return item

    def delete(self, item_id: str) -> None:
        with self.locked():
            data = self.load()
            item = _find(data, item_id)
            data["items"].remove(item)
            self.save(data)

    def resolve(self, ids: list[str], note: str) -> tuple[list[dict], int]:
        """Resolve open items and bump the report version, as one step: parallel
        resolves (an agent's concurrent tool calls) each get their own version."""
        with self.locked():
            data = self.load()
            items = [_find(data, i) for i in ids]
            done = [i["id"] for i in items if i.get("status") != "open"]
            if done:
                raise RapportError(f"not open: {', '.join(done)} (see `rapport feedback --all`)")
            version = bump_version(self.report_dir, note)
            for item in items:
                item["status"] = "resolved"
                item["resolution"] = {"note": note, "at": now(), "version": version}
                item["updated"] = now()
            self.save(data)
            return items, version

    def reopen(self, ids: list[str]) -> list[dict]:
        with self.locked():
            data = self.load()
            items = [_find(data, i) for i in ids]
            for item in items:
                item["status"] = "open"
                item.pop("resolution", None)
                item["updated"] = now()
            self.save(data)
            return items


def _same_target(a: dict, b: dict) -> bool:
    return (a.get("kind"), a.get("blocks"), collapse_ws(a.get("quote", ""))) == \
        (b.get("kind"), b.get("blocks"), collapse_ws(b.get("quote", "")))


def _num(item_id) -> int:
    m = re.match(r"FB-(\d+)$", str(item_id or ""))
    return int(m.group(1)) if m else 0


def _find(data: dict, item_id: str) -> dict:
    norm = item_id.upper() if item_id.upper().startswith("FB-") else f"FB-{item_id}"
    for item in data["items"]:
        if item.get("id") == norm:
            return item
    raise NotFound(f"no feedback item {item_id}")


# ── building items from browser / CLI input ─────────────────────────────────── #
def actions_config(cfg) -> list[dict]:
    return [a for a in (cfg.module("feedback").get("actions") or []) if isinstance(a, dict) and a.get("id")]


def _typed(value, kind, what: str):
    if value is None:
        return kind()
    if not isinstance(value, kind):
        raise RapportError(f"{what} must be a {kind.__name__}")
    return value


def check_content(actions: list, comment: str, cfg) -> None:
    """An item needs an action or a comment, and a comment where an action requires one."""
    actions_cfg = {a["id"]: a for a in actions_config(cfg)}
    if not actions and not comment:
        raise RapportError("pick an action or write a comment")
    for a in actions:
        if actions_cfg.get(a, {}).get("comment") == "required" and not comment:
            raise RapportError(f"'{actions_cfg[a].get('label', a)}' needs a comment")


def blocks_with_quote(quote: str, blocks: dict) -> list[str]:
    """Block ids whose source contains the quote (markup ignored)."""
    want = loose(quote)
    return [bid for bid, b in blocks.items() if want and want in plain(b.get("src", "")).lower()]


def make_item(payload: dict, cfg, blocks: dict, version: int) -> dict:
    """Validate a create payload and enrich its target from the block index."""
    actions_cfg = {a["id"]: a for a in actions_config(cfg)}
    actions = [str(a) for a in _typed(payload.get("actions"), list, "actions")]
    for a in actions:
        if a not in actions_cfg:
            raise RapportError(f"unknown action '{a}'")
    comment = str(payload.get("comment") or "").strip()
    if len(comment) > MAX_COMMENT:
        raise RapportError("comment too long")
    check_content(actions, comment, cfg)
    t = _typed(payload.get("target"), dict, "target")
    kind = t.get("kind", "text")
    if kind not in ("text", "block", "report"):
        raise RapportError("target.kind must be text, block, or report")
    target = {"kind": kind}
    if kind != "report":
        ids = [str(b) for b in _typed(t.get("blocks"), list, "target.blocks") if str(b) in blocks]
        if not ids and kind == "text" and t.get("quote"):
            # the agent edited the block while the reader was writing: its id
            # changed, but the passage is usually still there
            ids = blocks_with_quote(str(t["quote"]), blocks)[:1]
        if not ids:
            raise RapportError("target block not found (the page may be stale; reload)")
        infos = [blocks[b] for b in ids]
        lines = [x["lines"] for x in infos if x.get("lines") and x["lines"][0]]
        target.update({
            "blocks": ids,
            "module": infos[0].get("module"),
            "heading_path": infos[0].get("heading_path", []),
            "section": infos[0].get("section", ""),
            "lines": [min(a for a, _ in lines), max(b for _, b in lines)] if lines else None,
            "src": "\n".join(x.get("src", "") for x in infos),
            "srcs": [x.get("src", "") for x in infos],
        })
        if kind == "text":
            quote = str(t.get("quote") or "")
            if not quote.strip():
                raise RapportError("empty selection")
            target.update({"quote": quote[:MAX_QUOTE], "prefix": str(t.get("prefix") or "")[-64:],
                           "suffix": str(t.get("suffix") or "")[:64]})
        region = collapse_ws(str(t.get("region") or ""))[:80]
        if region:
            target["region"] = region     # a named part of the block, e.g. the "After" pane
    snapshot = {a: {"label": actions_cfg[a].get("label", a)} for a in actions}
    return {"target": target, "actions": actions, "comment": comment, "action_labels": snapshot,
            "report_version": version}


def update_fields(payload: dict, cfg) -> dict:
    out = {}
    actions_cfg = {a["id"] for a in actions_config(cfg)}
    if "actions" in payload:
        acts = [str(a) for a in _typed(payload["actions"], list, "actions")]
        bad = [a for a in acts if a not in actions_cfg]
        if bad:
            raise RapportError(f"unknown action '{bad[0]}'")
        out["actions"] = acts
    if "comment" in payload:
        c = str(payload["comment"] or "").strip()
        if len(c) > MAX_COMMENT:
            raise RapportError("comment too long")
        out["comment"] = c
    return out


# ── locating items in the current source ─────────────────────────────────────── #
STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'')


def _param_text(params: str | None) -> str:
    """The visible words in a params table: its string values (titles, labels)."""
    return " ".join(a or b for a, b in STRING_RE.findall(params or ""))


MARKS_RE = re.compile(r"[*_`~]+")       # emphasis and code marks; plain() drops them
MD_STRIP = [(re.compile(r"^\s*:{3,}[ \t]*(?:[A-Za-z][\w-]*)?[ \t]*(.*?)[ \t]*(\{.*\})?[ \t]*$", re.M),
             lambda m: f"{m.group(1)} {_param_text(m.group(2))}"),
            (re.compile(r"^\s*(?:`{3,}|~{3,})[ \t]*[\w+#.-]*[ \t]*(\{.*\})?[ \t]*$", re.M),
             lambda m: _param_text(m.group(1))),
            (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"), (MARKS_RE, ""),
            (re.compile(r"^\s{0,3}(#{1,6}|>|[-+*]|\d+[.)])\s+", re.M), ""),
            (re.compile(r":[a-z][a-z0-9-]*\[([^\]]*)\](\{[^}]*\})?"), r"\1")]


def plain(md_text: str) -> str:
    for rx, rep in MD_STRIP:
        md_text = rx.sub(rep, md_text)
    return collapse_ws(md_text)


def loose(quote: str) -> str:
    """Rendered text (a reader's quote) made comparable with plain(source).lower(): the
    marks plain() drops go here too, or `snake_case` and ~/paths would never match."""
    return collapse_ws(MARKS_RE.sub("", quote)).lower()


RANK = {"exact": 0, "moved": 1, "fuzzy": 2, "n/a": 3, "stale": 4}


def locate(target: dict, source: str) -> tuple[list[int] | None, str]:
    """Where the target's block(s) are in the current source: (lines, status)
    with status exact | moved | fuzzy | stale | n/a. A selection spanning
    several blocks is located block by block and reported as their union."""
    srcs = [x for x in target.get("srcs") or [] if x.strip()]
    if len(srcs) > 1:
        near = (target.get("lines") or [None])[0]
        found = [_locate_one(x, None, source, near) for x in srcs]
        spans = [lines for lines, st in found if lines and st != "stale"]
        worst = max((st for _, st in found), key=RANK.get)
        if not spans:
            return target.get("lines"), "stale"
        lines = [min(a for a, _ in spans), max(b for _, b in spans)]
        if worst == "exact" and lines != target.get("lines"):
            worst = "moved"
        return lines, worst
    return _locate_one(target.get("src") or "", target.get("lines"), source)


def _locate_one(src: str, old, source: str, near: int | None = None) -> tuple[list[int] | None, str]:
    """Whole-line matches only (so "## Results" never matches inside "### Results");
    among several, the one nearest the old position wins."""
    if not src.strip():
        return (old, "n/a")
    rows = source.split("\n")
    want_rows = src.split("\n")
    n = len(want_rows)
    ref = (near if near is not None else (old[0] if old else 1)) - 1
    starts = range(0, max(0, len(rows) - n + 1))

    def nearest(cands):
        return min(cands, key=lambda i: (abs(i - ref), i)) if cands else None

    i = nearest([i for i in starts if rows[i] == want_rows[0] and rows[i:i + n] == want_rows])
    if i is not None:
        lines = [i + 1, i + n]
        return lines, ("exact" if old is None or lines == old else "moved")
    for norm in (collapse_ws, plain):          # whitespace-insensitive, then markup-insensitive
        want = norm(src)
        i = nearest([i for i in starts if norm("\n".join(rows[i:i + n])) == want])
        if i is not None:
            return [i + 1, i + n], "moved"
    want = collapse_ws(src)
    best, best_i, best_n = 0.0, -1, n
    for size in sorted({max(1, n - 1), n, n + 1}):
        for i in range(0, max(1, len(rows) - size + 1)):
            window = "\n".join(rows[i:i + size])
            sm = difflib.SequenceMatcher(None, want, collapse_ws(window), autojunk=False)
            if sm.real_quick_ratio() < best or sm.quick_ratio() < best:
                continue
            r = sm.ratio()
            if r > best or (r == best and abs(i - ref) < abs(best_i - ref)):
                best, best_i, best_n = r, i, size
    if best >= 0.6 and best_i >= 0:
        return [best_i + 1, best_i + best_n], "fuzzy"
    return (old, "stale")


def quote_in(quote: str, md_text: str) -> bool:
    """Is the (rendered-text) quote still in this Markdown? Tolerates markup
    and small differences: most of the quote must survive as one run."""
    q, p = loose(quote), plain(md_text).lower()
    if not q or q in p:
        return True
    m = difflib.SequenceMatcher(None, q, p, autojunk=False).find_longest_match(0, len(q), 0, len(p))
    return m.size >= 0.8 * len(q)


def instruction_for(action: dict) -> str:
    text = action.get("instruction") or ""
    path = action.get("instruction_file")
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            text = text or f"(instruction_file not found: {path})"
    return " ".join(str(text).split())


LEARN_TEXT = {
    "ask": "after fixing: distil the pattern into one short writing rule, propose it to the user, and ask "
           "whether to save it (rapport writing add \"<rule>\" --scope user|project).",
    "global": "after fixing: save one short writing rule that prevents this: rapport writing add \"<rule>\" --scope user",
    "project": "after fixing: save one short writing rule that prevents this: rapport writing add \"<rule>\" --scope project",
}


def describe(items: list[dict], cfg, source: str) -> list[dict]:
    acts = {a["id"]: a for a in actions_config(cfg)}
    out = []
    for item in items:
        t = item.get("target", {})
        lines, status = locate(t, source) if t.get("kind") != "report" else (None, "n/a")
        labels, todo, learn = [], [], []
        for a in item.get("actions", []):
            conf = acts.get(a)
            label = (conf or {}).get("label") or item.get("action_labels", {}).get(a, {}).get("label", a)
            labels.append(label)
            if conf:
                instr = instruction_for(conf)
                if instr:
                    todo.append(instr)
                if conf.get("learn", "none") in LEARN_TEXT:
                    learn.append(conf["learn"])
        quote_ok = None
        if t.get("quote") and lines:
            located = "\n".join(source.split("\n")[lines[0] - 1:lines[1]])
            quote_ok = quote_in(t["quote"], located)
        out.append({**item, "labels": labels or ["Comment"], "instructions": todo, "learn": learn,
                    "located": {"lines": lines, "status": status, "quote_found": quote_ok}})
    return out


def format_items(described: list[dict], source_name: str, source: str, version) -> str:
    open_n = sum(1 for d in described if d.get("status") == "open")
    rows = source.split("\n")
    out = [f"rapport feedback — {open_n} open · {source_name} · v{version}"]
    if not described:
        out.append("Nothing to do.")
        return "\n".join(out)
    out.append(f"Apply each item to {source_name}, then: rapport feedback resolve <ids> --note \"<what changed>\"")
    # whole-report items last
    for d in sorted(described, key=lambda x: x.get("target", {}).get("kind") == "report"):
        t = d.get("target", {})
        loc = d["located"]
        where = []
        if t.get("kind") == "report":
            where.append("whole report")
        else:
            if t.get("heading_path"):
                where.append(" › ".join(t["heading_path"]))
            elif t.get("module") == "header":
                where.append("title block (front matter)")
            if loc["lines"]:
                a, b = loc["lines"]
                where.append(f"{source_name}:{a}" + (f"-{b}" if b != a else ""))
            if loc["status"] in ("moved", "fuzzy"):
                where.append(f"({loc['status']} since the comment)")
            elif loc["status"] == "stale":
                where.append("(STALE: passage not found; it was rewritten or removed)")
            if t.get("kind") == "block":
                where.append(f"[whole {t.get('module', 'block')}]")
            if t.get("region"):
                where.append(f"in “{t['region']}”")
        status = "" if d.get("status") == "open" else f" · RESOLVED v{d.get('resolution', {}).get('version', '?')}"
        out.append("")
        out.append(f"{d['id']} · {', '.join(d['labels'])} · " + " · ".join(where) + status)
        for instr in d["instructions"]:
            out.append(f"  do: {instr}")
        if d.get("comment"):
            out.append(f"  comment: {d['comment']}")
        if t.get("quote"):
            q = collapse_ws(t["quote"])
            out.append(f"  quote: \"{q[:240]}{'…' if len(q) > 240 else ''}\"")
            if loc.get("quote_found") is False and loc["status"] != "stale":
                out.append("  note: the quoted words are no longer in that passage (already edited?)")
        if loc["lines"] and loc["status"] != "stale" and d.get("status") == "open":
            a, b = loc["lines"]
            shown = rows[a - 1:min(b, a + 5)]
            for i, line in enumerate(shown):
                out.append(f"  │ {a + i:>4}  {line}")
            if b > a + 5:
                out.append(f"  │  …    ({b - a - 5} more lines)")
        for learn in sorted(set(d["learn"])):
            out.append(f"  learn: {LEARN_TEXT[learn]}")
        if d.get("resolution", {}).get("note"):
            out.append(f"  resolved: {d['resolution']['note']}")
    return "\n".join(out)


# ── CLI ─────────────────────────────────────────────────────────────────────── #
def cli(args, report_dir: Path) -> int:
    from . import config as config_mod

    if getattr(args, "ids", None):
        # `feedback resolve FB-1 ./reports/x`: a trailing folder is the report folder
        ids = []
        for x in args.ids:
            if not re.fullmatch(r"(?i)(fb-)?\d+", x) and Path(x).expanduser().is_dir():
                args.dir = x
            else:
                ids.append(x)
        if not ids:
            raise RapportError(f"usage: rapport feedback {args.fb_cmd} FB-1 [FB-2 …]")
        args.ids = ids
    if getattr(args, "dir", None):
        report_dir = Path(args.dir).expanduser().resolve()
    cfg, _ = config_mod.load_for_report(report_dir, getattr(args, "set", None))
    store = Store(report_dir)
    action = args.fb_cmd or "list"
    source = cfg.source_path.read_text(encoding="utf-8")
    name = cfg.get("report.source", "report.md")

    if action == "list":
        items = store.items()
        if not getattr(args, "all", False):
            items = [i for i in items if i.get("status") == "open"]
        described = describe(items, cfg, source)
        version = read_state(report_dir).get("version", 1)
        if getattr(args, "json", False):
            print(json.dumps({"version": version, "source": str(cfg.source_path), "items": described},
                             indent=2, ensure_ascii=False))
        else:
            print(format_items(described, name, source, version))
        return 0
    if action == "resolve":
        items, version = store.resolve(args.ids, args.note)
        print(f"resolved {', '.join(i['id'] for i in items)} → v{version}")
        return 0
    if action == "reopen":
        items = store.reopen(args.ids)
        print("reopened " + ", ".join(i["id"] for i in items))
        return 0
    if action == "add":
        return _cli_add(args, cfg, store, report_dir)
    raise RapportError(f"unknown feedback action {action}")


def _cli_add(args, cfg, store: Store, report_dir: Path) -> int:
    from .build import build
    # the block index of report.md as it is now: blocks.json is only as fresh as the last written build
    blocks = build(report_dir, getattr(args, "set", None), write=False).blocks
    target: dict = {"kind": "report"}
    if args.block:
        if args.block not in blocks:
            raise RapportError(f"no block '{args.block}' in the current report (ids: .rapport/blocks.json after a build)")
        target = {"kind": "block", "blocks": [args.block]}
    if args.quote:
        hits = blocks_with_quote(args.quote, blocks)
        if not hits:
            raise RapportError("quote not found in any block")
        target = {"kind": "text", "blocks": hits[:1], "quote": args.quote}
    item = store.add(make_item({"target": target, "actions": args.action, "comment": args.comment},
                               cfg, blocks, read_state(report_dir).get("version", 1)), blocks)
    print(f"added {item['id']}")
    return 0
