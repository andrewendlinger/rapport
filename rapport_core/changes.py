"""What changed since the last feedback round, for the change bars on the served page.

A round starts when the reader comments while nothing is open: the block index
the comment was made on is saved as the round's baseline (rounds.json in the
state folder). Every item of the round carries its number. The page shows the
round of the most recently resolved item: the current blocks diffed against its
baseline, each changed block linked to the resolved items that explain it.

Blocks are compared by their own content (module + source, without nested
blocks and without the heading path), so renaming a heading marks the heading,
not its whole section, and editing a paragraph inside a callout marks the
paragraph, not the callout. Within an edited block, its text as the reader sees
it is compared word by word: the page highlights the added words (located like
a feedback quote, by text and context) and lists the removed ones.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .util import atomic_write, collapse_ws, short_hash, state_dir

ROUNDS_FILE = "rounds.json"
TEXT_MAX = 20_000   # characters of a block's text kept in a baseline, for the word diff
CUT_TEXT = 300      # characters of a removed block shown to the reader
MAX_RUNS = 60       # added runs in one block; past that, the whole block is marked
CONTEXT = 48        # characters of context around a run, as for a feedback quote


# ── outline: the block index as an ordered list of comparable entries ─────────── #
def own_src(blocks: dict, bid: str) -> str:
    """A block's Markdown without its nested blocks: what identifies the block itself."""
    b = blocks[bid]
    own = b.get("src") or ""
    for c in b.get("children") or []:
        own = own.replace(blocks.get(c, {}).get("src") or "\0", "", 1)
    return own


def outline(blocks: dict) -> list[dict]:
    """Blocks in reading order (a container before its children), each with a
    key for its own content and the text the reader sees."""
    children = {c for b in blocks.values() for c in b.get("children") or []}

    def start(bid):
        lines = blocks[bid].get("lines") or [0, 0]
        return lines[0] or 0

    out: list[dict] = []

    def walk(bid):
        b = blocks[bid]
        own = own_src(blocks, bid)
        out.append({"id": bid, "key": short_hash(f"{b.get('module')}|{collapse_ws(own)}", 12),
                    "module": b.get("module"), "text": collapse_ws(b.get("text") or "")[:TEXT_MAX]})
        for c in b.get("children") or []:
            if c in blocks:
                walk(c)

    for bid in sorted((b for b in blocks if b not in children), key=start):
        walk(bid)
    return out


# ── rounds ────────────────────────────────────────────────────────────────────── #
def _path(report_dir: Path) -> Path:
    return state_dir(report_dir) / ROUNDS_FILE


def load_rounds(report_dir: Path) -> dict:
    try:
        data = json.loads(_path(report_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rounds = data.get("rounds") if isinstance(data, dict) else None
    return rounds if isinstance(rounds, dict) else {}


def _round(item: dict) -> int | None:
    r = item.get("round")
    return r if isinstance(r, int) and not isinstance(r, bool) else None


def shown_round(items: list[dict]) -> int | None:
    """The round of the most recently resolved item: the one the page marks."""
    done = [i for i in items if i.get("status") == "resolved" and _round(i) is not None]
    if not done:
        return None
    last = max(done, key=lambda i: (i.get("resolution") or {}).get("version") or 0)
    return _round(last)


def round_for(report_dir: Path, items: list[dict], blocks: dict, version) -> int:
    """The round a new item joins: the open one, else a new one whose baseline
    is `blocks`. Callers hold the feedback store's lock."""
    rounds = load_rounds(report_dir)
    open_rounds = [r for i in items if i.get("status") == "open" and (r := _round(i)) is not None]
    if open_rounds:
        n = max(open_rounds)
        if str(n) in rounds:
            return n
    else:
        n = 1 + max([r for i in items if (r := _round(i)) is not None] + [int(k) for k in rounds if k.isdigit()],
                    default=0)
    keep = {str(r) for r in open_rounds} | {str(shown_round(items))}
    rounds = {k: v for k, v in rounds.items() if k in keep}
    rounds[str(n)] = {"version": version, "blocks": outline(blocks)}
    atomic_write(_path(report_dir), json.dumps({"format": 1, "rounds": rounds}, ensure_ascii=False) + "\n")
    return n


# ── the diff ──────────────────────────────────────────────────────────────────── #
ALIGN_MAX = 400     # old × new blocks in one changed run; past that, pair them by position


def block_steps(old: list[dict], new: list[dict]):
    """Every block that differs between two outlines, in reading order, as (op, old index,
    new index): ("pair", i, j) an edited block, ("del", i, None), ("ins", None, j)."""
    sm = difflib.SequenceMatcher(None, [e.get("key") for e in old], [e.get("key") for e in new], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            yield from _align(old, new, i1, i2, j1, j2)


def _align(old, new, i1, i2, j1, j2) -> list[tuple[str, int | None, int | None]]:
    """A run of old blocks replaced by a run of new ones, as ordered steps:
    ("pair", i, j) an edited block, ("del", i, None) a removed one, ("ins", None, j)
    an added one. As many blocks as possible are paired (an edit is likelier than
    a removal plus an addition), choosing the pairs that look most alike."""
    n, m = i2 - i1, j2 - j1
    if n == 0 or m == 0 or n == m or n * m > ALIGN_MAX:
        k = min(n, m)
        return ([("pair", i1 + x, j1 + x) for x in range(k)] + [("del", i, None) for i in range(i1 + k, i2)]
                + [("ins", None, j) for j in range(j1 + k, j2)])

    def sim(i, j):      # one more pair always outscores any likeness (at most 2 per pair)
        a, b = old[i], new[j]
        return 2 * min(n, m) + 1 + (a.get("module") == b.get("module")) + difflib.SequenceMatcher(
            None, a.get("text", ""), b.get("text", ""), autojunk=False).ratio()

    # best[x][y]: the best score aligning old[i1+x:] with new[j1+y:]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for x in range(n - 1, -1, -1):
        for y in range(m - 1, -1, -1):
            best[x][y] = max(best[x + 1][y], best[x][y + 1], sim(i1 + x, j1 + y) + best[x + 1][y + 1])
    out, x, y = [], 0, 0
    while x < n and y < m:
        if best[x][y] == best[x + 1][y]:
            out.append(("del", i1 + x, None))
            x += 1
        elif best[x][y] == best[x][y + 1]:
            out.append(("ins", None, j1 + y))
            y += 1
        else:
            out.append(("pair", i1 + x, j1 + y))
            x, y = x + 1, y + 1
    return out + [("del", i1 + i, None) for i in range(x, n)] + [("ins", None, j1 + j) for j in range(y, m)]


def word_diff(old: str, new: str) -> tuple[list[dict], list[str]]:
    """The words added to a block (each run with its context, to find it on the
    page) and the runs of words removed from it."""
    a, b = old.split(), new.split()
    added, removed = [], []
    for tag, i1, i2, j1, j2 in _cleanup(difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()):
        if tag in ("replace", "insert"):
            added.append({"quote": " ".join(b[j1:j2]), "prefix": " ".join(b[max(0, j1 - 12):j1])[-CONTEXT:],
                          "suffix": " ".join(b[j2:j2 + 12])[:CONTEXT]})
        if tag in ("replace", "delete"):
            removed.append(" ".join(a[i1:i2]))
    return added, removed


def _cleanup(ops: list[tuple]) -> list[tuple]:
    """Fold a few words that happen to survive a rewrite ("a", "key", "without") into
    the change around them, so a rewritten sentence reads as one run, not confetti:
    an unchanged island of up to 3 words between two changes, shorter than both."""
    ops = [list(o) for o in ops]
    changed = True
    while changed:
        changed = False
        for k in range(1, len(ops) - 1):
            tag, i1, i2, j1, j2 = ops[k]
            before, after = ops[k - 1], ops[k + 1]
            if tag != "equal" or before[0] == "equal" or after[0] == "equal":
                continue
            size = i2 - i1
            if size <= 3 and all(size < (o[2] - o[1]) + (o[4] - o[3]) for o in (before, after)):
                ops[k - 1:k + 2] = [["replace", before[1], after[2], before[3], after[4]]]
                changed = True
                break
    return [tuple(o) for o in ops]


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "…"


def changes(report_dir: Path, blocks: dict, items: list[dict]) -> dict | None:
    """Blocks edited or added, and removed, since the shown round's baseline, each
    with the ids of the resolved items it answers and, for an edited block, the
    words added and removed. None: nothing to show."""
    n = shown_round(items)
    base = load_rounds(report_dir).get(str(n)) if n is not None else None
    if not isinstance(base, dict) or not isinstance(base.get("blocks"), list):
        return None
    old, new = base["blocks"], outline(blocks)
    sm = difflib.SequenceMatcher(None, [e.get("key") for e in old], [e["key"] for e in new], autojunk=False)
    changed: dict[str, list[str]] = {}       # new block id → item ids, in reading order
    removed: list[dict] = []
    fate: dict[str, object] = {}             # old block id → [new id], or its removal record
    was: dict[str, dict] = {}                # edited block id → its old entry
    added_after: dict[str, list[str]] = {}   # old block id → blocks inserted right after it
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        rec, prev = None, (old[i1 - 1].get("id") if i1 > 0 else None)
        for op, i, j in _align(old, new, i1, i2, j1, j2):
            if op == "del":
                if rec is None:
                    rec = {"before": None, "module": old[i].get("module"),
                           "text": _clip(old[i].get("text", ""), CUT_TEXT), "count": 0, "items": []}
                    removed.append(rec)
                rec["count"] += 1
                fate[old[i].get("id")] = rec
                continue
            nid = new[j]["id"]
            changed[nid] = []
            if rec is not None and rec["before"] is None:
                rec["before"] = nid
            rec = None
            if op == "pair":
                fate[old[i].get("id")] = [nid]
                was[nid] = old[i]
                prev = old[i].get("id")
            elif prev is not None:
                added_after.setdefault(prev, []).append(nid)
        if rec is not None and rec["before"] is None:
            rec["before"] = new[j2]["id"] if j2 < len(new) else None

    # a changed block inside a changed container is part of the container's bar
    parent = {c: bid for bid, b in blocks.items() for c in b.get("children") or []}

    def top(bid):
        out, p = bid, parent.get(bid)
        while p is not None:
            if p in changed:
                out = p
            p = parent.get(p)
        return out

    general = []
    for it in items:
        if it.get("status") != "resolved" or _round(it) != n:
            continue
        t = it.get("target") or {}
        if t.get("kind") == "report":
            general.append(it["id"])
            continue
        for b in t.get("blocks") or []:
            f = fate.get(b) or added_after.get(b) or ([b] if b in changed else None)
            targets = [f["items"]] if isinstance(f, dict) else [changed[top(nid)] for nid in f or []]
            for lst in targets:
                if it["id"] not in lst:
                    lst.append(it["id"])
    text = {e["id"]: e["text"] for e in new}
    bars = []
    for nid, ids in changed.items():
        if top(nid) != nid:
            continue
        bar = {"id": nid, "items": ids, "kind": "edited" if nid in was else "added"}
        if nid in was:
            bar["added"], bar["removed"] = word_diff(was[nid].get("text", ""), text.get(nid, ""))
            if len(bar["added"]) > MAX_RUNS:
                bar["kind"], bar["added"] = "added", []           # rewritten: mark it all
            bar["removed"] = [_clip(r, 160) for r in bar["removed"][:6]]
        bars.append(bar)
    if not bars and not removed:
        return None
    return {"round": n, "since": base.get("version"), "changed": bars, "removed": removed, "general": general}
