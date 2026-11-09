# Where rapport keeps things

The user config folder (and how to move it) and a report's review state. Settings and styling are in [config.md](config.md).

## Keeping your config elsewhere

User config lives in `~/.config/rapport/` (`$XDG_CONFIG_HOME/rapport/` if that's set): `config.toml`, `learned.toml`, `rapport-modules/` and `presets/`. To keep it in a dotfiles repo or a synced folder, set one of these in your shell profile:

```sh
export RAPPORT_CONFIG_DIR=~/dotfiles/rapport        # a folder laid out like ~/.config/rapport/
export RAPPORT_CONFIG=~/dotfiles/rapport.toml       # or: the config file itself, any name
```

- **`RAPPORT_CONFIG_DIR`** replaces the whole folder and wins over `XDG_CONFIG_HOME`. A folder that doesn't exist yet just means no user config.
- **`RAPPORT_CONFIG`** names the file and wins over `RAPPORT_CONFIG_DIR`. Its siblings follow the project names, as if it were a rapport.toml: learned rules in `rapport-learned.toml`, modules in `rapport-modules/`, presets in `rapport-presets/`, all beside it. Relative paths inside it resolve against its folder. If the file doesn't exist, every command stops with an error, except `config paths`, which reports it, and `serve --stop` / `serve --status`.
- `RAPPORT_CONFIG=/dev/null` turns user config off: no user layer, learned rules, modules or presets, and `writing add --scope user` and `modules new --scope user` refuse. Pointing it at a folder is an error; that's what `RAPPORT_CONFIG_DIR` is for.
- Empty values are ignored. `~` and relative paths work; rapport makes them absolute against the current folder.
- A `RAPPORT_CONFIG` that is also a project file (`~/rapport.toml`) loads once, as the user layer.

`rapport config paths` shows what's in effect.

## Review state

A report's review state lives in `.rapport/` next to report.md:

| file | holds |
|---|---|
| `feedback.json` | reader feedback, open and resolved |
| `state.json` | report version and resolve notes |
| `blocks.json` | block index, rewritten by `build` and each `serve` rebuild (`lint` and `feedback add` build without writing it) |
| `rounds.json` | the blocks as they were when each feedback round began, for the change bars |
| `modules/<name>.json` | the state of a module with a `server.py` (a sign-off, a poll); `rapport modules state <name>` reads it |
| `server.json`, `server.log`, `feedback.lock`, `modules/<name>.lock` | review server (URL, pid, local token) and locking |
| `confluence/` | Confluence sync ([confluence.md](confluence.md)): `export.html` from `export confluence`; `base.html` and `page.json`, the page as last recorded, which `diff` and `prepare` compare against; `publish.html`/`publish.json` from `prepare`, until `record`. Never committed, even with `commit_feedback`: in another checkout, `prepare` has no base and says how to make one |

The folder ignores itself: rapport keeps a `.gitignore` in it containing `*`, so `git add -A` skips it and a rebuild never dirties the tree. It's working data for one checkout: the block index churns, server.json holds a local token, and feedback is a conversation about a draft. The `.gitignore` starts with `# managed by rapport` and the build rewrites it to match the config. Replace that first line and rapport treats the file as yours and leaves it alone.

To commit feedback and version history with the report:

```toml
[report]
commit_feedback = true
```

The `.gitignore` then lets `feedback.json`, `state.json`, `rounds.json`, module state (`modules/*.json`) and itself through (so clones keep the rule and the change bars); everything else stays ignored. An outer `.gitignore` that ignores `.rapport/` still wins; `build` and `lint` say so in a note for the user, with the `git check-ignore -v` command that shows which file does it.

- `git clean -fd` skips ignored files, so it keeps `.rapport/`; `git clean -fdx` and `-fdX` delete it, unresolved feedback included. With `commit_feedback = true`, `feedback.json`, `state.json`, `rounds.json`, module state and the `.gitignore` aren't ignored: until they're committed they're untracked, and `git clean -fd` deletes them.
- Files committed before the folder ignored itself stay tracked. `build` and `lint` print a note for the user with the exact command, e.g. `git rm --cached reports/q3/.rapport/blocks.json reports/q3/.rapport/feedback.json`; it doesn't fail `lint`, since changing git state is the user's call. Once that's committed, collaborators' tracked copies are deleted on their next pull (they're recoverable from git history).

## Writing log

With `[writing] log = true`, rapport keeps how the user corrected the agent's writing, across all reports, for `rapport writing digest` and the playbook in [writing-digest.md](writing-digest.md):

- **Where:** `writing-log/` beside the user config (`rapport-writing-log/` beside a `RAPPORT_CONFIG` file), one `YYYY-MM.jsonl` per month plus `digest.json` (when the last digest was done). The folder is readable by the user only. Without a user config (`RAPPORT_CONFIG=/dev/null`) there is no log. Inside a git work tree it must be git-ignored, or nothing is written (`rapport writing log` says so).
- **What:** per resolved or reopened feedback item, and per block edited between rounds, the block's Markdown before and after (up to 2,000 characters each), its heading path, the neighbouring blocks' text, the actions, comment and the agent's note; per round, the title, headings and length. Never a whole report. Emails, URLs and `writing.log_redact` terms are blanked as entries are written.
- **Who edited:** rapport can't see who typed. `build`, `lint` and `feedback resolve` leave a snapshot (`.rapport/writing-snapshot.json`); `rapport feedback` (and `add`, `reopen`) log what changed since then as edited by "owner?". An agent that edits and then lints or resolves isn't mistaken for the user; edits made while the agent is mid-round are missed instead.
- **Never** in report.html, a served page, an export or `commit_feedback`. `rapport writing forget --report <dir>`, `--before YYYY-MM-DD` or `--entry <id>` removes entries.

Switch it off for one project or report with `[writing] log = false` in its `rapport.toml` or front matter.
