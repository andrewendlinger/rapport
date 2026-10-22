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
| 7 | `--set key=value` | command line (value parsed as TOML, else as a string; a string for a list key is a one-item list) |

The preset is chosen first, from whichever layer sets `preset = "…"`, and then slotted in as layer 2.

**Merge rules** (declared per key in the schema):
- **Tables** merge key by key.
- **Plain values and lists** are replaced.
- **`writing.rules`, `writing.avoid` and `theme.css`** are appended, so learned and project entries add up. An empty-string entry drops everything before it, inherited entries included: `avoid = ["", "very"]` leaves just `"very"`.
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

## Styling

Three levels, lightest first: tokens, your own CSS, a forked module.

**Tokens** hold the palette, fonts, corner radius, column width and body size. The theme module defines them (built-in: `default`, `paper`); `[theme.tokens]` overrides them and `[theme.tokens.dark]` sets dark-mode values:

```toml
[theme]
name = "paper"

[theme.tokens]
accent = "#0B6E4F"
width = "72ch"

[theme.tokens.dark]
accent = "#6FD3A8"
```

| | tokens |
|---|---|
| colour | `bg` `surface` `surface-2` `ink` `ink-soft` `ink-faint` `line` `accent` `accent-soft` `ok` `ok-soft` `warn` `warn-soft` `danger` `danger-soft` `violet` `violet-soft` `shadow` |
| type | `sans` `serif` `mono` `body-font` `heading-font` `body-size` |
| shape | `radius`, `width` (the reading column; `[layout] width` overrides it) |

`rapport modules info default` prints each token with its light and dark value. Code colours are Pygments styles: `[modules.code] light_style` and `dark_style`.

**Scope** is the layer you set it in: the user config for every report, a `rapport.toml` for a project, front matter for one report. A look you reuse under a name is a preset.

**Modules inherit it.** Every module styles itself through the `--rp-*` tokens only (`modules check` warns about literal colours), so a token change restyles every module, built-in or third-party, light and dark.

**Your own CSS** goes in `[theme] css`, a list of files relative to the config file that lists them:

```toml
[theme]
css = ["brand.css"]
```

- It always wins over rapport's CSS. rapport's CSS sits in cascade layers (theme first, then modules, so module rules beat theme rules) and yours comes after it, unlayered, so a plain `h2{color:#123}` beats the theme's `.rp-content h2`.
- Target one module with `.rp-m-<name>`, a class on every module's root element: `.rp-m-callout{font-size:1rem}`.
- Re-scope a token for one module: `.rp-m-callout{--rp-accent:#0B6E4F}`. For a different dark value, use `light-dark()`: `.rp-m-callout{--rp-accent:light-dark(#0B6E4F,#6FD3A8)}` follows the reader's OS setting and the theme toggle. It takes colours only.
- The lists append across layers, outer first (user, project, front matter), so a nearer file wins ties. `css = ["", "report.css"]` drops the inherited files.
- `url()`s are inlined as `data:` URIs, resolved relative to the CSS file: fonts (woff2, woff, ttf, otf, eot) and images (svg, png, jpg, gif, webp, avif). Remote URLs, root-relative paths (`/x`) and `@import` fail the build, because the page must stay one file. List each CSS file in `theme.css` instead of importing it.

A brand font:

```css
/* brand.css, next to rapport.toml */
@font-face{font-family:Inter;src:url(fonts/Inter-Regular.woff2) format("woff2");font-weight:400}
@font-face{font-family:Inter;src:url(fonts/Inter-SemiBold.woff2) format("woff2");font-weight:600}
```

```toml
[theme]
css = ["brand.css"]

[theme.tokens]
sans = "Inter, system-ui, sans-serif"
```

Each weight adds about 20 to 100 KB to every report.html and counts toward `report.max_inline_mb`. Ship only the weights you use.

**Structural changes** need a fork. For new markup or a different template, `rapport modules new callout --scope project` copies the module into your project, and the copy replaces the built-in. Later fixes to the built-in don't reach it. `rapport modules new brand --kind theme` starts a theme from a copy of `default`. Its style.css can url() files in its own folder, such as fonts.

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
