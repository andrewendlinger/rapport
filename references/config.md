# rapport configuration

## Layers (later wins)

| # | layer | file |
|---|---|---|
| 1 | defaults | `config/schema.toml` + every module manifest |
| 2 | preset | `presets/<name>.toml`: built-in, the user presets folder (`~/.config/rapport/presets/`), or `rapport-presets/` next to a rapport.toml |
| 3 | user | `~/.config/rapport/config.toml` (`$XDG_CONFIG_HOME` respected; [movable](#keeping-your-config-elsewhere)) |
| 4 | learned (user) | `~/.config/rapport/learned.toml` beside it (written by `rapport writing add`) |
| 5 | project | every `rapport.toml` from `$HOME` down to the report folder, each followed by its `rapport-learned.toml` |
| 6 | front matter | the `+++` block in report.md |
| 7 | `--set key=value` | command line (value parsed as TOML, else as a string) |

The preset is chosen first, from whichever layer sets `preset = "…"`, and then slotted in as layer 2.

**Merge rules** (declared per key in the schema):
- **Tables** merge key by key.
- **Plain values and lists** are replaced.
- **`writing.rules` and `writing.avoid`** are appended, so learned and project rules add up.
- **Arrays of tables with an `id`** (e.g. `[[modules.feedback.actions]]`) merge by id: you can change one field of one entry, add entries, or drop one with `enabled = false`.

**Inspect:**
- `rapport config show --origin [--key prefix]` prints each value and the file it came from.
- `rapport config validate` finds unknown keys (with did-you-mean) and type errors.
- `rapport config defaults` prints the annotated reference.
- `rapport config paths [--json]` lists the files rapport reads: user config, learned rules, user modules and presets (and which variable chose them), the report's `rapport.toml` chain, and its state folder.

## Keeping your config elsewhere

User config lives in `~/.config/rapport/` (`$XDG_CONFIG_HOME/rapport/` if that's set): `config.toml`, `learned.toml`, `rapport-modules/` and `presets/`. To keep it in a dotfiles repo or a synced folder, set one of these in your shell profile:

```sh
export RAPPORT_CONFIG_DIR=~/dotfiles/rapport        # a folder laid out like ~/.config/rapport/
export RAPPORT_CONFIG=~/dotfiles/rapport.toml       # or: the config file itself, any name
```

- **`RAPPORT_CONFIG_DIR`** replaces the whole folder and wins over `XDG_CONFIG_HOME`. A folder that doesn't exist yet just means no user config.
- **`RAPPORT_CONFIG`** names the file and wins over `RAPPORT_CONFIG_DIR`. Its siblings follow the project names, as if it were a rapport.toml: learned rules in `rapport-learned.toml`, modules in `rapport-modules/`, presets in `rapport-presets/`, all beside it. Relative paths inside it resolve against its folder. If the file doesn't exist, every command stops with an error (except `config paths`, which reports it).
- `RAPPORT_CONFIG=/dev/null` turns user config off: no user layer, learned rules, modules or presets, and `writing add --scope user` and `modules new --scope user` refuse. Pointing it at a folder is an error; that's what `RAPPORT_CONFIG_DIR` is for.
- Empty values are ignored. `~` and relative paths work; rapport makes them absolute against the current folder.
- A `RAPPORT_CONFIG` that is also a project file (`~/rapport.toml`) loads once, as the user layer.

`rapport config paths` shows what's in effect.

## Recipes

Change the look of every report (user-wide):

```toml
# user config (`rapport config paths` shows where)
[theme]
name = "paper"

[theme.tokens]
accent = "#0B6E4F"

[theme.tokens.dark]
accent = "#6FD3A8"
```

Single column, no table of contents, no footer:

```toml
[layout]
side = []
bottom = []
```

Change, add, or remove feedback actions:

```toml
[[modules.feedback.actions]]
id = "verbose"                 # existing: change just the label
label = "Shorter, please"

[[modules.feedback.actions]]
id = "unclear"                 # remove
enabled = false

[[modules.feedback.actions]]
id = "cite"                    # new
label = "Needs source"
icon = "📎"
key = "n"                      # Alt+N in window mode, N in bar mode
tone = "ok"                    # neutral | accent | ok | warn | danger | violet
comment = "optional"           # none | optional | required
learn = "none"                 # none | ask | global | project
instruction = "Add a source or link supporting this claim."
# instruction_file = "prompts/cite.md"   # longer text, relative to this file
```

Selection behaviour: a slim icon bar instead of the window, and no block handles:

```toml
[modules.feedback]
on_select = "bar"
block_handles = false
```

House writing rules for a project:

```toml
# <repo>/rapport.toml
[writing]
audience = "the on-call team"
rules = ["Times in UTC", "Link every dashboard you mention"]
avoid = ["obviously", "simply"]
max_paragraph_words = 90
```

Module defaults for every use:

```toml
[modules.code]
line_numbers = true
dark_style = "monokai"

[modules.collapse]
open = true
```

Switch a feature off:

```toml
[modules.theme-toggle]
enabled = false
```

Use a module pack:

```toml
[modules]
paths = ["~/code/rapport-modules-extra"]
```

Serving:

```toml
[serve]
port = 5000
open_browser = false
```

Sharing report.html: the built file contains no local paths. Opened by double-click, it shows a read-only hint with `rapport serve '<its folder>'`. If `rapport` isn't on the reader's PATH, set the command the hint shows:

```toml
[modules.static-hint]
command = "~/code/rapport/bin/rapport"
```

## Review state

A report's review state lives in `.rapport/` next to report.md:

| file | holds |
|---|---|
| `feedback.json` | reader feedback, open and resolved |
| `state.json` | report version and resolve notes |
| `blocks.json` | block index, rewritten on every build |
| `server.json`, `server.log`, `feedback.lock` | review server (URL, pid, local token) and locking |

The folder ignores itself: rapport keeps a `.gitignore` in it containing `*`, so `git add -A` skips it and a rebuild never dirties the tree. It's working data for one checkout: the block index churns, server.json holds a local token, and feedback is a conversation about a draft. The `.gitignore` starts with `# managed by rapport` and the build rewrites it to match the config. Replace that first line and rapport treats the file as yours and leaves it alone.

To commit feedback and version history with the report:

```toml
[report]
commit_feedback = true
```

The `.gitignore` then lets `feedback.json`, `state.json` and itself through (so clones keep the rule); everything else stays ignored. An outer `.gitignore` that ignores `.rapport/` still wins; the build warns and prints the `git check-ignore -v` command that shows which file does it.

- `git clean -fd` keeps `.rapport/` (it skips ignored files); `git clean -fdx` and `-fdX` delete it, unresolved feedback included.
- Files committed before the folder ignored itself stay tracked. `build` and `lint` warn and print the exact command, e.g. `git rm --cached reports/q3/.rapport/blocks.json reports/q3/.rapport/feedback.json`.
