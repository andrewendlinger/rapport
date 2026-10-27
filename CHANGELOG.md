# Changelog

## Unreleased

- **`install.sh --help`** prints just the usage text; it no longer ends with the script's `set -eu` line.
- **`order` orders scripts too.** A module's `order` in `module.toml` now decides its `<script>` position within its kind, as documented. It used to affect only the CSS.
- **The module script API is documented.** `references/modules.md` now lists what a module's `script.js` can rely on: the `Rapport` functions (including the new `Rapport.reload()`, which keeps the scroll position), the events, the page data and the `init`/`mount` context. The template table also covers `config`, `ctx.module()`, `ctx.config` and `ctx.meta`.

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
