"""`rapport lint` (writing rules + build problems) and `rapport writing add`."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import date
from pathlib import Path

from . import config as config_mod
from .build import build
from .parse import Node, inline_text
from .util import RapportError

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# a line that would be an HTML block if raw HTML were on (autolinks like <https://…> are fine)
HTML_LINE_RE = re.compile(r"^\s{0,3}(<!--|</?[A-Za-z][A-Za-z0-9-]*(\s|/?>|$))")


def _phrase_re(phrase: str) -> re.Pattern:
    p = re.escape(phrase.strip())
    left = r"\b" if re.match(r"\w", phrase.strip()) else ""
    right = r"\b" if re.search(r"\w$", phrase.strip()) else ""
    return re.compile(left + p.replace(r"\ ", r"\s+") + right, re.I)


def _prose_lines(source: str, fm_lines):
    """(line_no, text) for lines outside front matter and fenced code."""
    fence = None
    for i, line in enumerate(source.split("\n"), 1):
        if fm_lines and fm_lines[0] <= i <= fm_lines[1]:
            continue
        m = FENCE_RE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        yield i, line


def _verbatim_lines(nodes: list[Node], registry) -> set[int]:
    """Lines of modules that take their body as is (html-embed's HTML, a diagram's source):
    raw HTML there is the point, not a mistake."""
    out: set[int] = set()
    for n in nodes:
        mod = registry.get(n.module)
        if mod and mod.body != "markdown" and n.lines and n.lines[0]:
            out.update(range(n.lines[0], n.lines[1] + 1))
        elif n.children:
            out |= _verbatim_lines(n.children, registry)
    return out


def _paragraphs(nodes: list[Node]):
    for n in nodes:
        if n.module == "paragraph":
            yield n
        if n.children:
            yield from _paragraphs(n.children)


def lint(report_dir: Path, sets=None) -> tuple[list[str], object]:
    res = build(report_dir, sets, write=False)
    cfg = res.config
    name = cfg.get("report.source", "report.md")
    problems = [f"{name}: error: {d.message}" if not d.line else f"{name}:{d.line}: error: {d.message}"
                for d in res.errors]
    problems += [f"config: {w}" for w in cfg.warnings]
    problems += [f"warning: {w}" for w in res.warnings if w not in cfg.warnings]
    source = cfg.source_path.read_text(encoding="utf-8")

    for phrase in cfg.get("writing.avoid", []) or []:
        if not phrase.strip():
            continue
        rx = _phrase_re(phrase)
        for i, line in _prose_lines(source, cfg.front_matter_lines):
            if rx.search(line):
                problems.append(f"{name}:{i}: avoid \"{phrase}\" (writing.avoid)")

    if not cfg.get("report.raw_html", False):
        verbatim = _verbatim_lines(res.doc.nodes, cfg.registry) if res.doc else set()
        for i, line in _prose_lines(source, cfg.front_matter_lines):
            if HTML_LINE_RE.match(line) and i not in verbatim:
                problems.append(f"{name}:{i}: raw HTML shows up as literal text (report.raw_html = false); "
                                f"use Markdown or a module instead")

    limit = int(cfg.get("writing.max_paragraph_words", 0) or 0)
    if limit and res.doc:
        for p in _paragraphs(res.doc.nodes):
            words = len(inline_text(p.inline).split())
            if words > limit:
                a, b = p.lines
                where = f"{a}" if a == b else f"{a}-{b}"
                problems.append(f"{name}:{where}: paragraph has {words} words (writing.max_paragraph_words = {limit})")
    return problems, res


LEARNED_HEADER = """# Learned writing rules, appended by `rapport writing add` (usually after a
# reader flagged something). Each [[learned]] entry adds to [writing] rules
# (kind = "rule") or [writing] avoid (kind = "avoid"). Edit or delete freely.
"""


def writing_add(report_dir: Path, rule: str, scope: str, avoid: bool, approved: bool = False) -> int:
    rule = " ".join(rule.split())
    if not rule:
        raise RapportError("empty rule")
    if not approved:                # a rule shapes every later report: the user decides, never the agent
        raise RapportError("not saved: a writing rule needs the user's explicit yes. Show them the exact rule and "
                           "where it applies (--scope user: all their reports; --scope project: this project), "
                           "ask, and rerun with --approved only after they agree")
    if scope == "user":
        path = config_mod.user_paths().learned_file
        if path is None:
            raise config_mod.no_user_config()
    else:
        path = config_mod.nearest_project_dir(report_dir) / config_mod.LEARNED_PROJECT
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    before = path.read_text(encoding="utf-8") if exists else LEARNED_HEADER
    entry = (f"\n[[learned]]\nrule = {json.dumps(rule, ensure_ascii=False)}\n"
             f"kind = \"{'avoid' if avoid else 'rule'}\"\nadded = \"{date.today().isoformat()}\"\n")
    after = before + entry
    try:
        tomllib.loads(after)
    except tomllib.TOMLDecodeError as e:
        raise RapportError(f"{path} would become invalid TOML ({e}); fix it by hand first") from None
    with open(path, "a" if exists else "w", encoding="utf-8") as fh:
        fh.write(entry if exists else after)
    print(f"saved to {path}: {'avoid' if avoid else 'rule'} — {rule}\n  to undo: delete that [[learned]] entry there")
    return 0
