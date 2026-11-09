---
name: rapport
description: Write a report as a self-contained HTML page with rapport, and revise it from the reader's in-browser feedback: they select text to comment or flag it (Delete, AI slop, Too verbose, Unclear…). Use when the user asks for a report, write-up, analysis, summary or "HTML report"; says "apply/revise the rapport feedback" or "I left comments"; has a folder with report.md and .rapport/; wants a report published to Confluence or a Confluence page's edits brought back; wants report style or behaviour changed (rapport.toml); or wants a new report module (side-by-side code, collapsible sections, gifs…).
---

# rapport

A report is a folder: `report.md` (Markdown plus `:::` modules, written by you) builds into one self-contained `report.html`. The reader reviews the served page and leaves comments; you apply them.

**The CLI** is `bin/rapport` in this folder, written `rapport` below. Call it by absolute path each time (shell variables don't survive between your calls), or as plain `rapport` if `command -v rapport` finds it. It needs `uv`, a `.venv` here, or `python3` with `requirements.txt` installed. Every command takes `-C <report-dir>` (default: the current directory) and `--help`.

## Writing a report

1. **Scaffold.** `rapport init <dir> --title "…"`; suggest `reports/<slug>/` in the repo unless the user names a place. Images and gifs go in `<dir>/assets/`. Replace the placeholder line "Lead with the conclusions."
2. **Read the brief.** `rapport brief -C <dir>`, in full: this user's writing rules, the syntax, and every installed module with params and an example (Confluence-style elements, diagrams and math among them). Copy the examples; don't guess syntax.
3. **Write `report.md`** (syntax: `references/authoring.md`). What breaks builds:
   - Params are **one single-line TOML inline table at the end of the `:::` line**: `::: callout Heads up {tone = "warning"}`.
   - Nest containers by giving the outer one more colons (`::::`).
   - No raw HTML (it shows as literal text). No remote images: download them into `assets/`. Inlined files total at most `report.max_inline_mb` (8), so shrink big gifs.
4. **Lint.** `rapport lint -C <dir>` until it prints `lint: clean`; problems point at `report.md:LINE`. Lint never writes `report.html`.
5. **Serve.** `rapport serve --detach -C <dir>` prints the URL. Plain `serve` blocks until Ctrl-C. Either kind stops by itself after `serve.idle_hours` (3) without use; re-running starts it again on the same URL, or prints the URL if it's running. `--stop` ends it; `--no-open` skips opening the browser. If it reports a sandbox, give the user the command it prints.
6. **Hand over the served URL**, not `report.html`, and tell the user: select text to comment or flag it, the panel is bottom-right, then say "apply the rapport feedback". If they may not get to it for hours, say the server will have stopped and you'll restart it (or they run the command the page shows). A double-clicked `report.html` is a clean read-only copy with no feedback UI: send that one around.

## Applying feedback

For "apply the feedback", "I left comments", "revise": start here, skip the steps above.

1. Run `rapport feedback -C <dir>` **once** (or `--json`) and work from that list. Don't list again before resolving: edited items then show as `STALE`, `moved` or `fuzzy`. Reading an item:
   - Lines are in the *current* `report.md`; trust them for `moved` and `fuzzy`. For `STALE`, find the passage by its quote or tell the user it's gone. "The quoted words are no longer in that passage" means it was edited already.
   - `do:` is the user's instruction for the action they chose. A comment-only item has no `do:`: the comment is the request.
   - `whole report` items have no lines. `[whole image]` (table, code…) is about that block. `title block (front matter)` is the `+++` block.
2. **Edit only what the items ask.** After a round the page puts a margin bar on every block changed since it began, and hovering shows the reader your note. A drive-by rewrite is a bar nobody can explain. If an edit needs a matching change elsewhere (summary, cross-reference, a number), make it and say so in the note.
3. **Resolve each item you addressed:** `rapport feedback resolve FB-1 --note "<what you changed>" -C <dir>`. Write the note for the reader, specific ("Cut to two sentences; kept the p99 figure"). One call per item; several ids only when one change covers them, and all must be open or the call fails. Leave an item you didn't act on open, and say why.
4. **`learn:` lines** ask you to propose one short, general writing rule. Show the user the exact rule and where it would apply (`--scope user`: all their reports; `project`: beside the nearest `rapport.toml`, else the git root, else the report folder), and ask. Only on a yes, run the `writing add` command with `--approved -C <dir>`; `--avoid` is for a banned phrase.
5. Tell the user briefly what changed, and the new version. Run `rapport serve --detach -C <dir>` and give the URL: the server may have stopped while the reader was away.

Feedback given in chat gets the same treatment. To record it: `rapport feedback add "<comment>" --quote "<exact text>" [--action verbose]`, or `--block <id>` for an image, table or code block (ids: the `blocks` keys in `.rapport/blocks.json`, written by `build` and `serve`). With neither it's about the whole report.

## Publishing to Confluence

`report.md` stays the source; the page is a copy people may edit. `rapport export confluence -C <dir>` writes the page body (`.rapport/confluence/export.html`) and prints the title; publish it with the user's Confluence tools, fetch the page back, and `rapport confluence record <page>`. Later updates must never overwrite other people's edits or comments: fetch the live page (save the getConfluencePage JSON to a file), `rapport confluence diff <page>`, apply each change to `report.md` yourself (surgical edits, like feedback), then publish only what `rapport confluence prepare <page>` produces (it refuses while Confluence has changes `report.md` lacks or a comment would come loose), only while the page is still at the version it checked, and `record` the result. Confluence comments become feedback items. Pass on every `for the user:` line. Follow the playbook in `references/confluence.md` (steps, outline format, module mapping, what's lossy).

## Rules

- Never hand-edit `report.html` (generated) or `.rapport/` (review state). To read what readers did in a module with state (a sign-off, a poll): `rapport modules state <name> [--json]`.
- **Never save a writing rule the user hasn't said yes to.** That holds for `learn:` lines, feedback given in chat and your own ideas alike: propose the exact rule and its scope, ask, wait. Saving without asking and reporting it afterwards is not allowed; `writing add` refuses without `--approved`.
- A `note for the user:` line from `build` or `lint` is git advice, not a failure. Pass on the note and its command; don't change git state yourself.

## Changing style or behaviour

Config is TOML in layers, weakest to strongest: defaults, preset, user config, each `rapport.toml` from `$HOME` down to the report folder (with its `rapport-learned.toml`), the report's `+++` front matter, `--set key=value`. Project conventions go in the project's `rapport.toml`, one-offs in front matter; `rapport config paths -C <dir>` lists the files read (moving the user config, committing feedback: `references/state.md`).

- `rapport config show --origin [--key theme] -C <dir>` says which file set a value; `config validate` catches typos. `config defaults` lists core keys only: a module's keys come from `modules info <name>` and are set at `[modules.<name>]`. `enabled = false` switches a module off (not `runtime`).
- Style, lightest first: theme tokens (`[theme.tokens]`; `modules info default` lists them), then the user's CSS (`[theme] css = ["brand.css"]`, which beats rapport's), then a thin override for structure.
- Recipes (logos, icons, header and footer entries, feedback actions): `references/config.md`.

## Adding or changing a module

- **Change an installed module:** `rapport modules new <installed-name> -C <dir>` makes a thin override that `extends` it. Copy in only the files you change; the rest keeps following rapport. Your `style.css` is *added* after the base's. `modules info <name>` shows where each file comes from. `--copy` takes the whole module.
- **New module:** `rapport modules new <name> --kind block|inline|layout|theme|feature`, or `--extends <base>` to build on one. Add `--server` for state or requests while serving (sign-offs, polls, checklists). `--scope project` (default) puts it in `rapport-modules/` beside the nearest `rapport.toml` or git root.
- Run `rapport modules check <name>`. A fresh scaffold passes at once, so "done" also means: summary and `example.md` written, the module used in `report.md`, `lint` clean. After a rapport upgrade `check` warns when a file you copied changed upstream; `modules diff <name>` shows how.
- `rapport gallery --out <tmp dir>` renders every module's example (without `--out`, into `./rapport-gallery` in the current directory).

Read `references/modules.md` before writing a module.
