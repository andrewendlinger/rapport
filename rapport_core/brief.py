"""`rapport brief`: everything the agent needs before writing, in one compact page.

Writing rules (resolved config), the syntax, and a catalog generated from the
module manifests, so new modules show up without anyone editing docs.
"""

from __future__ import annotations

from .registry import Module
from .util import toml_repr


def _param_line(name: str, spec: dict) -> str:
    t = spec.get("type", "any")
    if t == "enum":
        t = "|".join(map(str, spec.get("choices", [])))
    elif t == "list" and spec.get("items"):
        t = f"list of {spec['items']}"
    default = spec.get("default")
    d = f" = {toml_repr(default)}" if default not in (None, "", [], {}) else ""
    doc = spec.get("doc", "").rstrip(".")
    return f"{name} ({t}{d}){': ' + doc if doc else ''}"


def _example(mod: Module, limit: int = 14) -> list[str]:
    path = mod.example_path
    if not path:
        return []
    lines = path.read_text(encoding="utf-8").strip("\n").split("\n")
    if len(lines) > limit:
        lines = lines[:limit] + ["…"]
    return ["    " + line for line in lines]


def module_entry(mod: Module, with_example: bool = True) -> list[str]:
    out = [f"### {mod.name}" + ("" if mod.builtin else f"  (custom: {mod.dir})"), mod.summary]
    if mod.kind == "block":
        form = f"::: {mod.name}" + (f" <{mod.positional}>" if mod.positional else "") + " {params}"
        body = {"markdown": "Markdown body", "text": "raw text body", "toml": "TOML body",
                "none": "no body"}[mod.body]
        out.append(f"syntax: `{form}` … `:::` ({body})")
        if mod.children:
            out.append(f"children: {', '.join('::: ' + c for c in mod.children)} (outer container needs more colons, e.g. ::::)")
    elif mod.kind == "inline":
        out.append(f"syntax: `:{mod.name}[text]{{params}}`")
    if mod.params:
        out.append("params: " + "; ".join(_param_line(k, v) for k, v in mod.params.items()))
    if with_example:
        ex = _example(mod)
        if ex:
            out += ["example:"] + ex
    return out


def brief(cfg) -> str:
    w = cfg.get("writing", {}) or {}
    reg = cfg.registry
    out = [f"# rapport brief — {cfg.report_dir}", ""]

    out += ["## Writing rules (from config; follow them)"]
    out.append(f"- audience: {w.get('audience', '')}")
    out.append(f"- tone: {w.get('tone', '')}")
    out.append(f"- length: {w.get('length', '')}")
    for s in w.get("structure", []) or []:
        out.append(f"- structure: {s}")
    for r in w.get("rules", []) or []:
        out.append(f"- rule: {r}")
    if w.get("avoid"):
        out.append("- never use: " + ", ".join(f'"{a}"' for a in w["avoid"]))
    if w.get("max_paragraph_words"):
        out.append(f"- paragraphs: at most {w['max_paragraph_words']} words")
    out.append("")

    out += [
        "## Syntax",
        "- report.md is Markdown (CommonMark + tables, ~~strike~~, footnotes[^1], - [ ] task lists).",
        "- Front matter: TOML between +++ lines. Keys: title, subtitle, author, date, status, kicker (any",
        "  other top-level key also lands in meta), plus any config section ([theme], [layout], [modules.x] …).",
        "- Block modules: `::: name optional title {key = \"value\", flag = true}` … `:::`. Params are ONE",
        "  single-line TOML inline table at the end of the line (key = value, comma-separated).",
        "  Nest by giving the outer container more colons (`::::`).",
        "- Code fences take params after the language: ```python {title = \"app.py\", hl = [2, 3]}",
        "- Images/gifs: ![caption](assets/file.gif) on its own line → figure. Files must be local (inlined).",
        "- Raw HTML is " + ("allowed." if cfg.get("report.raw_html") else "escaped (report.raw_html = false): use modules."),
        "",
    ]

    blocks = reg.by_kind("block")
    out += ["## Block modules", ""]
    for mod in blocks:
        if not cfg.enabled(mod.name):
            continue
        out += module_entry(mod) + [""]
    inlines = [m for m in reg.by_kind("inline") if cfg.enabled(m.name)]
    if inlines:
        out += ["## Inline modules", ""]
        for mod in inlines:
            out += module_entry(mod) + [""]
    elements = reg.by_kind("element")
    out += ["## Markdown elements (rendered by element modules)"]
    out.append(", ".join(m.name + ("" if m.builtin else "*") for m in elements)
               + "   (* = customised)")
    code = reg.get("code")
    if code and code.params:
        out.append("code params: " + "; ".join(_param_line(k, v) for k, v in code.params.items()))
    out.append("")

    actions = cfg.module("feedback").get("actions", []) or []
    if actions:
        out += ["## Feedback readers can leave (what `rapport feedback` will ask of you)"]
        for a in actions:
            instr = a.get("instruction") or (f"(see {a['instruction_file']})" if a.get("instruction_file") else "")
            out.append(f"- {a.get('label', a.get('id'))} [{a.get('id')}]: {' '.join(str(instr).split())}")
        out.append("")
    out += ["Theme: " + str(cfg.get("theme.name")) + " · layout: top=" + ",".join(cfg.get("layout.top", []))
            + " side=" + ",".join(cfg.get("layout.side", [])) + " bottom=" + ",".join(cfg.get("layout.bottom", []))]
    if cfg.warnings:
        out += ["", "## Config warnings"] + [f"- {x}" for x in cfg.warnings]
    return "\n".join(out)
