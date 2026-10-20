# rapport configuration

## Layers (later wins)

| # | layer | file |
|---|---|---|
| 1 | defaults | `config/schema.toml` + every module manifest |
| 2 | preset | `presets/<name>.toml`: built-in, `~/.config/rapport/presets/`, or `rapport-presets/` next to a rapport.toml |
| 3 | user | `~/.config/rapport/config.toml` (`$XDG_CONFIG_HOME` respected) |
| 4 | learned (user) | `~/.config/rapport/learned.toml` (written by `rapport writing add`) |
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

## Recipes

Change the look of every report (user-wide):

```toml
# ~/.config/rapport/config.toml
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
