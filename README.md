# rapport

Agent-written reports as self-contained HTML pages you review in the browser. Select a passage to comment on it, or flag it in one click: **Delete**, **AI slop**, **Too verbose**, **Unclear**, or your own actions. Everything autosaves. Then tell your agent "apply the feedback": it gets every item with its location, your comment, and what the action means, and revises the report in place while the page live-reloads.

Two things hold it together:

- **Everything is a module.** The page shell, header, TOC, theme, each Markdown element, every `:::` block, and the feedback UI itself are folders under `modules/`. To add a module or replace a built-in, drop in a folder.
- **Everything is configured in TOML.** Layout, theme tokens, writing rules, feedback actions and their instructions, and server behaviour all live in layered config files, from user-wide down to a single report.

It works in any harness that supports Agent Skills (`SKILL.md`): Claude Code, pi, Codex, and others.

## Install

```sh
gh repo clone andrewendlinger/rapport ~/code/rapport     # private repo; or wherever
~/code/rapport/install.sh --bin
```

`install.sh` does three things:
- symlinks the folder to `~/.claude/skills/rapport` (Claude Code) and `~/.agents/skills/rapport` (pi, Codex, and other Agent Skills harnesses);
- with `--bin`, puts `rapport` on your PATH;
- warms the dependency cache.

Other options:
- `--target DIR` adds another harness folder, e.g. `~/.pi/agent/skills` for older pi versions.
- `--only DIR` links into DIR instead of the two default folders (repeatable).
- `--copy` copies instead of symlinking.
- `--venv` builds a `.venv` in the repo for machines without uv or network access.
- `--dry-run` shows what would happen; `--uninstall` removes only what the script installed.

**Requirements:** Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/). The CLI declares its dependencies (markdown-it-py, mdit-py-plugins, Jinja2, Pygments) inline, so `uv` handles them. Without uv, or in a sandbox without network access, run `./install.sh --venv` once (or `pip install -r requirements.txt`). The generated reports themselves need nothing: one HTML file, no CDN, works offline.

## Try it

```sh
rapport gallery --out /tmp/rapport-gallery     # every module, rendered
rapport serve /tmp/rapport-gallery             # open it, select text, leave feedback
rapport feedback -C /tmp/rapport-gallery       # what your agent will see
```

In your agent, just ask for a report: *"write up the cache regression as a rapport report"*. It scaffolds `reports/<slug>/`, reads `rapport brief` (your writing rules plus the module catalog), writes `report.md`, and serves it. Once you've reviewed it: *"apply the feedback"*.

## Reading and reviewing

- **Select text.** A window opens with the cursor in the comment box: type and press Enter to save (Shift+Enter or ⌘↵ for a new line), or click an action to save it straight away. With text typed, actions work as tags. Cmd+C still copies the passage. Shortcuts before you type: ⌥ + the action's key. `autofocus` and `enter_saves` under `[modules.feedback]` switch both off.
  Prefer something quieter? Set `[modules.feedback] on_select = "bar"` for a slim icon bar, with single-key shortcuts and 💬 / `C` to comment.
- **Hover a block.** A **+** appears in the margin for whole-block comments (images, gifs, code).
- **Existing feedback** stays highlighted in the action's colour (Delete shows struck through), with counts in the margin. Click a highlight to edit or delete it.
- **The panel** (bottom right) lists everything with filters, has a box for whole-report comments, and shows the agent's note on each resolved item.
- **Live reload.** Rebuilds reload the page, but never while you're mid-comment (in the window or the panel's whole-report box): you get an "updated" pill instead, and drafts survive reloads. Esc or Cancel discards a draft, with Undo.

A double-clicked `report.html` is the same document, read-only, with a hint showing the serve command.

## Configuration

Layers, nearest wins:
1. built-in defaults
2. preset
3. your user config, `~/.config/rapport/config.toml` (or wherever `RAPPORT_CONFIG` / `RAPPORT_CONFIG_DIR` point)
4. learned rules
5. every `rapport.toml` from `$HOME` down to the report folder
6. the report's `+++` front matter
7. `--set key=value`

Tables merge, writing rules accumulate, and feedback actions merge by `id`.

```toml
# rapport.toml in your repo
[theme]
name = "paper"
tokens = { accent = "#0B6E4F" }

[layout]
side = []                        # no TOC column

[writing]
audience = "the on-call team"
rules = ["Times in UTC"]

[[modules.feedback.actions]]
id = "cite"
label = "Needs source"
icon = "📎"
key = "n"
tone = "ok"
instruction = "Add a source or link supporting this claim."
```

- `rapport config show --origin` explains every value; `rapport config paths` lists the files it reads.
- Config in a dotfiles repo? `export RAPPORT_CONFIG=~/dotfiles/rapport.toml` (see [references/config.md](references/config.md#keeping-your-config-elsewhere)).
- `rapport config defaults` prints the full annotated reference.
- More recipes: [references/config.md](references/config.md).

## Modules

```
modules/
  layout/   page header toc footer
  theme/    default paper
  element/  heading paragraph list code table image blockquote hr html footnotes
  block/    callout collapse compare pane
  inline/   badge
  feature/  runtime theme-toggle copy-code print static-hint live-reload feedback
```

To add one:
1. `rapport modules new tabs --kind block --scope project` scaffolds a commented folder (`module.toml`, `template.html`, `style.css`, `example.md`).
2. Edit it, then run `rapport modules check tabs`.
3. It's now usable as `::: tabs … :::`, listed in `rapport brief`, and shown in `rapport gallery`.

`rapport modules new callout` copies the built-in so you can restyle it. The contract is in [references/modules.md](references/modules.md).

## Map

```
SKILL.md             what agents follow
bin/rapport          CLI wrapper (.venv → uv run → python3)
scripts/rapport.py   entry point with inline (PEP 723) dependencies
rapport_core/        config · schema · registry · parse · render · build · serve · feedback · lint · brief
config/schema.toml   core config keys (types, defaults, docs, merge strategy)
modules/             every built-in module
presets/             named config bundles
references/          authoring.md · modules.md · config.md
tests/               pytest suite; tests/e2e drives a real browser
```

## Development

```sh
uv run --with pytest --with-requirements requirements.txt pytest tests
# browser tests (Playwright; needs a Chromium: `uvx playwright install chromium`, or set RP_CHROME)
uv run --with pytest --with playwright --with-requirements requirements.txt pytest tests/e2e
```
