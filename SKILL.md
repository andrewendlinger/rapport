---
name: rapport
description: Write reports as self-contained, modular HTML pages that the reader reviews in the browser. They highlight a passage to comment on it or flag it (Delete, AI slop, Too verbose, Unclear…), and you apply the feedback. Use when the user asks for a report, write-up, analysis, summary document, or "HTML report"; when they say "apply/revise the rapport feedback" or "I left comments"; when a folder has a report.md with a .rapport/ directory; or when they want to change report style/behaviour (rapport.toml) or add a report module (side-by-side code, collapsible sections, gifs, …).
---

# rapport

A report is a folder: `report.md` (Markdown + modules, written by you) builds into a self-contained `report.html`. Every part of the page is a **module**: the page shell, header, TOC, theme, each Markdown element, every `:::` block, and the feedback UI. Behaviour and style come from layered **TOML config**.

The point is the loop. The reader opens the served report, selects text, and comments or clicks a quick action. Everything autosaves to the server. You pick it up with `rapport feedback` and revise.

**CLI:** `RAPPORT=<this skill's folder>/bin/rapport`, i.e. the `bin/rapport` next to this SKILL.md. It runs with `uv` (inline dependencies) or the folder's `.venv`; no other setup is needed. Every command takes `-C <report-dir>` (the default is the current directory).

## Workflow

1. **Scaffold.** Run `$RAPPORT init <dir> --title "…"`, suggesting `reports/<slug>/` in the repo unless the user names a place. Put images and gifs in `<dir>/assets/`.
2. **Read the brief.** Run `$RAPPORT brief -C <dir>` and read it in full before writing. It holds this user's writing rules (audience, tone, rules, phrases to avoid), the exact syntax, and every installed module with its params and an example. Follow it; it reflects their config and the rules they've taught you.
3. **Write `report.md`.** Plain Markdown, plus modules from the brief:
   - `::: callout Heads up {tone = "warning"}` … `:::`
   - `::: collapse Details` … `:::` for long or secondary material
   - `::: compare {labels = ["Before", "After"]}` with two code fences for side-by-side code
   - `![caption](assets/demo.gif)` for images and gifs
   - code fences with params: ```` ```python {title = "app.py", hl = [3]} ````

   Params are **one single-line TOML inline table at the end of the `:::` line**. Nest containers by giving the outer one more colons (`::::`). Never write raw HTML, and never use remote images (they fail the build). Syntax details: `references/authoring.md`.
4. **Check.** `$RAPPORT lint -C <dir>` builds the report and reports errors (as `report.md:LINE`) and writing-rule hits. Fix them until it's clean.
5. **Serve.** `$RAPPORT serve --detach -C <dir>` starts the review server in the background and prints the URL. Give the user that exact URL; the port varies. If it says the environment is sandboxed or can't open a port, give the user the printed command to run in their own terminal. It's safe to re-run: an already-running server just prints its URL. `--status` and `--stop` manage it. It opens the user's browser unless `serve.open_browser = false`; add `--no-open` when you only want to check the page yourself.
6. **Apply feedback** when the user says they've reviewed (or "apply the feedback", "revise"):
   - `$RAPPORT feedback -C <dir>` lists every open item with its location in the *current* `report.md` (line numbers), the quoted passage, the reader's comment, and a `do:` line saying what the action means. That instruction comes from config; treat it as the user's request.
   - Make a **surgical edit per item** in `report.md` (never regenerate the whole file). After each edit, check that nearby text, the summary, and any cross-references still agree.
   - Items marked `moved` or `fuzzy` are still valid; trust the shown lines. For `STALE` items, find the passage by its quote, or tell the user it's gone. `in “After”` names the part of a block the comment is about (e.g. one pane of a comparison).
   - Then `$RAPPORT feedback resolve FB-1 --note "<one line: what you changed for it>" -C <dir>`. Resolve each item with its own note; group ids in one call only when one change covers them all. The open page live-reloads, bumps the version, and shows each note on its item.
   - **`learn:` lines.** `learn: ask` means distil the pattern into one short, general writing rule, propose it to the user, and save it only if they agree: `$RAPPORT writing add "<rule>" --scope user|project`. Add `--avoid` for a banned phrase. `global` / `project` mean save it directly with that scope.
   - Tell the user what changed, briefly, and the new version.
   - Feedback given in chat works the same way. Optionally record it with `$RAPPORT feedback add "<comment>" --quote "<exact text>" [--action verbose] -C <dir>`.
7. **Never hand-edit** `report.html` (it's generated) or `.rapport/*.json` (use the CLI).

## Changing behaviour or style

Config is TOML, merged nearest-wins in this order:
1. built-in defaults
2. preset
3. `~/.config/rapport/config.toml`
4. learned rules
5. every `rapport.toml` from `$HOME` down to the report folder
6. the report's `+++` front matter
7. `--set key=value`

Module settings live at `[modules.<name>]`. Every module, and every feedback action, can be switched off with `enabled = false`. Feedback actions are `[[modules.feedback.actions]]` entries, merged by `id`; each has a label, icon, key, tone, and `instruction`.

Before editing, check where a value comes from with `$RAPPORT config show --origin -C <dir>` (add `--key theme` to narrow it). For the full annotated reference run `$RAPPORT config defaults`. Prefer the project `rapport.toml` for project conventions and front matter for one report. Recipes: `references/config.md`.

## Adding a module

When the user wants something the catalog lacks (tabs, a chart, a timeline, a file tree…):

1. Run `$RAPPORT modules new <name> --kind block --scope project -C <dir>`. Scopes are `report`, `project` (a `rapport-modules/` folder next to the nearest `rapport.toml` or git root), or `user`. Using an existing module's name copies it as an override.
2. Edit its `module.toml` (params schema), `template.html` (Jinja, single root element), `style.css` (only `var(--rp-*)` tokens), optionally `script.js`, and `example.md`.
3. Run `$RAPPORT modules check <name> -C <dir>` until it passes. `$RAPPORT gallery` renders every module.
4. The new module now appears in `brief` automatically.

The contract is in `references/modules.md`; read it before writing a module.

## Command reference

| Command | What it does |
|---|---|
| `init <dir>` | scaffold a report folder |
| `brief` | writing rules, syntax, and the module catalog |
| `build [--watch]` | build `report.html` |
| `lint` | errors plus writing-rule checks |
| `serve [--detach\|--status\|--stop] [--port N]` | the review server |
| `feedback [--all\|--json]` | list open reader feedback |
| `feedback resolve\|reopen\|add` | manage feedback items |
| `writing add "<rule>" [--avoid] [--scope user\|project]` | save a learned writing rule |
| `config show [--origin] [--key K]\|validate\|defaults` | inspect configuration |
| `modules list\|info <name>\|new <name>\|check [name]` | discover and author modules |
| `gallery [--out DIR]` | render every module's example |
