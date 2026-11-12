# Changelog

## 0.7.0 (2026-11-12)

- **`preset = "confluence"`** sets up a report that becomes a Confluence page in one line: the `atlassian` theme, the Macro Pack and draw.io apps, and a writing rule plus a `lint` check against inline math, which Confluence can't show.
- **Python 3.11 works.** Commands where an option sits between two arguments, such as `writing add --approved "…" <dir>` or `config paths --json <dir>`, failed there with "unrecognized arguments". A `python3` older than 3.11 now gets a clear message instead of an import error.
- **`modules check`** no longer pins an unrelated error on a module whose name appears in it when the path holds the word "root".
- **MIT licence**, and CI on Linux and macOS.
- **SKILL.md front matter is strict YAML again.** 0.6.0's description held ": " unquoted, which skill loaders such as pi report as an error; it is now a block scalar, and a test keeps it that way.
- **Writing log.** With `[writing] log = true`, rapport keeps how you correct the agent's writing across all your reports: each resolved or reopened item and each block you edit between rounds, with its text before and after, never whole reports. `rapport writing digest` shows the patterns, and the playbook in `references/writing-digest.md` has the agent propose a few rule changes with evidence for you to approve; `rapport writing log` shows the state, `forget` removes entries. While it's on, flags no longer ask for a rule each time.
- **Writing rules need the user's yes.** `rapport writing add` refuses without `--approved`, every `learn` mode now proposes a rule and asks (`global` and `project` only suggest where it goes), and SKILL.md forbids saving a rule without asking, whether it comes from a flag, from chat or from the agent.
- **Confluence parity.** A report can become a Confluence page, be edited there, and have those edits brought back into `report.md`, which stays the source. `rapport export confluence` writes the page body from a `confluence.html` per module; `rapport confluence diff|check|prepare|record` track the published page so a re-publish never overwrites other people's edits or comments. rapport never talks to Confluence itself: the agent publishes with its own tools and follows `references/confluence.md`.
- **Confluence's elements.** Colour palettes, inline marks (status, color, highlight, underline, sub, sup, date, mention), columns, tabs, decisions, properties, cards, align, grid tables and panel tones; mermaid, plantuml, draw.io, LaTeX and math rendered to SVG at build time; `::: html-embed`. The new `atlassian` theme makes a report look like the page it becomes.
- **`modules check`**: two modules with one name in one folder are an error; tokens used with a fallback are optional. It now warns about a block, inline or element module without a `confluence.html`, your own included.
- **Callouts default to `info`**, Confluence's blue panel; `note` is now purple. A callout without a `tone` used to read "Note" and now reads "Info": add `{tone = "note"}` to keep the old look.
- **Marks for hidden content.** Comment markers and change bars for content in a tab that isn't showing sit on the tab list and move when you switch, as they do on a closed section's summary. A module that hides content gets the same with `aria-labelledby` and a `toggle` event (`references/modules.md`).
- **`lint`** no longer reports raw HTML inside a module that takes its body verbatim (`body = "text"`, as `html-embed` and `::: confluence` do).
- **Idle servers stop themselves.** A review server stops after `[serve] idle_hours` (3; 0 never) without an edit of the report, a page load or a comment, so servers left behind by agent sessions no longer pile up; an open tab doesn't keep it alive. `rapport serve` brings it back on the same port, and the open tab reconnects; meanwhile it shows the command, keeps the comment being written, and never turns into another report that took its address.

## 0.6.0 (2026-10-27)

- **Modules can extend the review server.** A `server.py` in a module folder registers routes under `/api/m/<module>/` and keeps the module's state in `.rapport/modules/<name>.json`. Its script calls them with `Rapport.api()` and hears about changes as `<module>:changed`. The server checks the host, the session token and the body before any module code runs. So a sign-off button, a poll or a checklist that remembers no longer needs a fork: `rapport modules new <name> --server` scaffolds one, and `rapport modules state <name>` shows the agent what readers did.
- **Feedback is an ordinary module.** Its routes live in `modules/feature/feedback/server.py`, so an override of `feedback` owns them too. The API moved from `/api/feedback` to `/api/m/feedback/`, and `revs.feedback` in `/api/meta` replaces `feedback_rev`. Comments stay in `.rapport/feedback.json`.
- **Thin overrides.** `rapport modules new <installed-name>` now makes an override that extends the module instead of copying it: copy in only the files you change, and the rest keeps getting rapport's fixes. Its module.toml merges onto the base's key by key, and its style.css is added after the base's. `--extends <base>` builds a new module on an existing one; `modules info` shows where each file comes from.
- **Copies say where they came from.** `modules new <name> --copy` records the rapport version and a hash of each file. `rapport modules check` warns when the original changes, and the new `rapport modules diff <name>` shows how.
- **One module contract for every kind.** `prepare()` now runs for inline, layout and feature modules too. A new `css(ctx)` hook returns CSS computed at build time; the `code` module's syntax colours use it. `[layout.regions]` adds page regions beyond top, side and bottom. `references/modules.md` now documents the script API (`Rapport.reload()`, `Rapport.api()`, the events, the page data) and the module names core relies on.
- **SKILL.md rewritten** around the two ways an agent comes in: writing a report and applying feedback. It covers the change bars, `serve --detach`, thin overrides and server modules. Review state and the user-config location moved to `references/state.md`.
- **A guide for changing rapport.** `AGENTS.md` maps the code and lists the rules a change must keep. `pyproject.toml` shortens the dev commands to `uv run pytest`, `uv run --group e2e pytest tests/e2e` and `uvx ruff check`.
- **Restarts.** A page left open across a server restart now reloads.
- **One rebuild per edit.** Editing a module's `prepare.py` no longer rebuilds and reloads the page twice.
- **`order`** in `module.toml` now orders scripts as well as CSS, as documented.
- **`install.sh`**: `--help` prints just the usage, and `--venv` reuses an existing `.venv`.

## 0.5.0 (2026-10-27)

- **Change bars.** After a feedback round, the served page marks what changed in the right margin: green for a new block, blue for an edited one, a red dash where something was removed. Hover a mark to highlight the added words and see the removed ones, your comment and the agent's note. Toggle with the button by Feedback.
- **Clean `report.html`.** The read-only hint on a double-clicked file is off by default. Turn it back on with `[modules.static-hint] enabled = true`.

## 0.4.2 (2026-10-22)

- **Feedback anchoring.** Quotes with backticks, underscores, asterisks or tildes (inline code, snake_case, `~/paths`) now match their passage, so `feedback add` accepts them and `rapport feedback` no longer reports them as edited. `feedback add` also reads the current `report.md`, not the last build's block index.
- **Parallel resolves.** Resolving several items at once no longer loses version bumps.
- **Clearer errors.** A `prepare.py` that fails to load is a build error, not a silent warning. A container's errors point at its `:::` line, and a title ending in `{x}` is title text, not a params error. A corrupt `feedback.json` is reported, and the page shows it. `serve --stop` says so when the server outlives SIGTERM.
- **Lighter `build --watch`.** It no longer reloads the config on every tick: idle CPU drops from 5% to 0.2% of a core.
- **Copy buttons** are left out where the browser gives the page no clipboard access.

## 0.4.1 (2026-10-22)

- **`feedback resolve` takes ids and a folder after `--note`.** `rapport feedback resolve FB-1 --note "…" <dir>` used to fail with "unrecognized arguments". It now works for `resolve` and `reopen`.

## 0.4.0 (2026-10-22)

- **A minimal default theme.** White ground (near-black in dark mode), zinc greys, one blue accent, the system sans-serif at 16px. The old stone-and-serif look is still there as `[theme] name = "classic"`.
- **A primer.** `examples/primer` is a report about rapport that doubles as a five-minute tutorial: quick start, the review loop, writing, config, modules.
- **A friendlier README** with a one-line pitch, a screenshot of the comment window, a three-step quick start, and the reference material folded away.

## 0.3.0 (2026-10-22)

- **Logos.** `[modules.header]` and `[modules.footer]` take a `logo`, with `logo_dark` for dark mode, `logo_alt`, `logo_height` and `logo_align`.
- **A title icon.** Set `icon` in front matter, or `[modules.header] icon` for every report. It can be an emoji, any symbol, or an image file.
- **Entries with icons.** `[[modules.header.entries]]` (the byline) and `[[modules.footer.entries]]` take an icon, text and a link. They add up across config layers and merge by `id`, like feedback actions. A one-colour SVG follows the text colour, so it stays visible in dark mode.
- **Footer options.** `show_title`, `show_version` and `show_date`; the footer now spans the full column.

## 0.2.0 (2026-10-22)

- **The review state ignores itself in git.** `.rapport/` keeps a managed `.gitignore`, so feedback, the block index and server files never land in a commit. `[report] commit_feedback = true` lets feedback and version history through. Builds point out state files that are still tracked, and an outer `.gitignore` that undoes the setting.
- **Your own CSS.** `[theme] css` lists stylesheets. rapport's CSS sits in cascade layers, so yours wins without `!important`. Fonts and images you `url()` are inlined; remote URLs and `@import` fail the build. `modules check` covers themes, `url()`s and `!important`.
- **Text scales with `body-size`.** Headings, code, tables, the header and the table of contents grow with it. Feedback UI, toasts and buttons keep their pixel sizes.
- **A settable user config.** `RAPPORT_CONFIG_DIR` replaces `~/.config/rapport`, `RAPPORT_CONFIG` names the config file (`/dev/null` turns user config off), and `rapport config paths` shows what is read from where.
- **Sturdier builds.** A theme without `width` or `body-size` tokens gets 70ch and 17px instead of an unbounded column. `layout.width` no longer overrides the theme's width, so paper's 64ch applies. Git advice is a note for the user and no longer fails `lint`. A repo's git config can't run a command on every build.

## 0.1.0 (2026-10-20)

- **Reports from Markdown.** `report.md` and its `:::` blocks build into one self-contained `report.html`: no CDN, works offline.
- **Review in the page.** Select a passage to comment on it, or flag it in one click (Delete, AI slop, Too verbose, Unclear). `rapport feedback` hands the agent each item with its location, and `feedback resolve` closes it while the page live-reloads.
- **Everything is a module.** Layout, themes, elements, blocks, inline marks and features are folders under `modules/`. Built in: callout, collapse, compare, badge, code, images and gifs, a table of contents, two themes.
- **Layered TOML config**, from defaults, presets, user and learned rules down through each `rapport.toml` to front matter and `--set`.
- **A CLI and an installer.** `init`, `brief`, `build`, `lint`, `serve`, `feedback`, `config`, `modules`, `gallery` and `writing add`; `install.sh` links the skill into Claude Code, pi, Codex and other Agent Skills harnesses.
