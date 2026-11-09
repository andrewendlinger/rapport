# rapport configuration

## Layers (later wins)

| # | layer | file |
|---|---|---|
| 1 | defaults | `config/schema.toml` + every module manifest |
| 2 | preset | `presets/<name>.toml`: built-in, the user presets folder (`~/.config/rapport/presets/`), or `rapport-presets/` next to a rapport.toml |
| 3 | user | `~/.config/rapport/config.toml` (`$XDG_CONFIG_HOME` respected; [movable](state.md#keeping-your-config-elsewhere)) |
| 4 | learned (user) | `~/.config/rapport/learned.toml` beside it (written by `rapport writing add`) |
| 5 | project | every `rapport.toml` from `$HOME` down to the report folder (from `/` if the report is outside `$HOME`), each followed by its `rapport-learned.toml` |
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
- `rapport config defaults` prints the annotated reference for the core keys. A module's own keys come from `rapport modules info <name>`.
- `rapport config paths [--json]` lists the files rapport reads: user config, learned rules, user modules and presets (and which variable chose them), the report's `rapport.toml` chain, and its state folder.

## Styling

Three levels, lightest first: tokens, your own CSS, a thin override of a module.

**Themes.** `default` is minimal: white ground, the system sans for all text, one blue accent, 16px. `classic` (the default before 0.4) has a cool stone ground, sans headings and serif text at 17px. `paper` is warm and serif throughout. `atlassian` looks like a Confluence page (Atlassian colours, panel styles, the 760 px column), for reports that will be published there. Pick one with `[theme] name`.

**Tokens** hold the palette, fonts, corner radius, column width and body size. The theme module defines them; `[theme.tokens]` overrides them and `[theme.tokens.dark]` sets dark-mode values:

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
| palettes | `text-<name>` `hl-<name>` `bg-<name>`: Confluence's fixed text, highlight and background colours, added only when a module that uses them is on the page (`color`, `highlight`, `callout`, `grid`). Names are the modules' enum choices, e.g. `text-bold-red`, `hl-yellow`, `bg-light-blue` |

`rapport modules info default` prints each token with its light and dark value. Code colours are Pygments styles: `[modules.code] light_style` and `dark_style`.

**`body-size` scales all report text.** It is the page's root font size, and rapport's themes and modules size their text in `rem` or `em`. So `body-size = "19px"` also enlarges headings, code, tables, the header, callout titles, and the table of contents with its column. A `ch` width widens with it too. A `rem` or `%` value is relative to the reader's browser setting. Feature UI keeps its px sizes: the feedback window, toasts, copy buttons, the theme toggle, and hints. In your own CSS, `1rem` is the body size.

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
- `url()`s are inlined as `data:` URIs, resolved relative to the CSS file: fonts (woff2, woff, ttf, otf, eot) and images (svg, png, jpg, gif, webp, avif). Remote URLs, root-relative paths (`/x`), `@import` and a `var()` inside `image-set()` (a url the build can't see) fail the build, because the page must stay one file. List each CSS file in `theme.css` instead of importing it.

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

**Structural changes** need an override. For new markup or a different template, `rapport modules new callout --scope project` makes a thin override in your project that extends the built-in. Copy in only the file you change, say its template; the rest keeps getting the built-in's fixes. `--copy` takes the whole module instead, and `rapport modules check` says when the built-in changes under it. `rapport modules new brand --kind theme` starts a theme that extends `default`: set only the tokens you change. Its style.css is added after `default`'s and can url() files in its own folder, such as fonts. See "Overriding and extending" in `references/modules.md`.

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

A region of your own, such as a banner or a right rail: write the layout module (`rapport modules new notice --kind layout`), name it in `[layout.regions]`, and place the region in a `page` override. The default page places only `top`, `side` and `bottom`, and the build warns about a region the page doesn't place.

```toml
[layout.regions]
banner = ["notice"]               # name = the layout modules in it, in order
```

```
rapport modules new page --scope project    # then copy template.html in and add {{ regions.banner }} where it goes
```

Region names are lowercase letters, digits and hyphens (`regions["right-rail"]` in the template); `top`, `side` and `bottom` keep their own `[layout]` keys. See "Extra regions" in `references/modules.md`.

Logo, title icon, and header or footer entries with icons:

```toml
# paths are relative to this file; images are inlined into report.html
[modules.header]
logo = "brand/logo.svg"
logo_dark = "brand/logo-dark.svg"   # optional: shown in dark mode instead
logo_alt = "ACME"
logo_align = "left"                 # left | right
logo_height = 32                    # px at a 17px body size; scales with body-size
icon = "📊"                         # before every title; a report's front matter `icon` wins

[[modules.header.entries]]          # byline, after author and date
icon = "brand/github.svg"
href = "https://github.com/acme/platform"
label = "Source on GitHub"          # tooltip; screen-reader name of an entry without text

[modules.footer]
logo = "brand/logo.svg"
logo_align = "right"
show_date = false                   # also show_title, show_version
text = "© 2026 ACME"

[[modules.footer.entries]]
id = "conf"
icon = "🔒"
text = "Confidential"
```

- **An icon** is an emoji or any other symbol, or an image file (svg, png, jpg, gif, webp, avif). It's a file when it ends in one of those extensions.
- **A one-colour icon follows the text colour**, so it stays visible in dark mode. An SVG that draws in `currentColor` (as Lucide, Feather, Tabler and Heroicons do) is tinted automatically. `tint = true` tints any image, and `tint = false` keeps an image's own colours.
- **Entries add up across layers**: the user config's entries come first, then the project's, then the report's. Give an entry an `id` to change it or remove it (`enabled = false`) from a nearer layer, like feedback actions.
- **Links** go to `https:`, `mailto:`, a relative path or a `#heading`. A `javascript:` link fails the build.

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
learn = "none"                 # none | ask (propose a writing rule; saved only if the user agrees) | global | project (the same, suggesting that scope)
instruction = "Add a source or link supporting this claim."
# instruction_file = "prompts/cite.md"   # longer text, relative to this file
```

Selection behaviour: a slim icon bar instead of the window, no block handles, and the change bars (margin marks for what changed since the last feedback round) off until the reader switches them on:

```toml
[modules.feedback]
on_select = "bar"
block_handles = false
changes = false
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
idle_hours = 8      # stop after 8 h without an edit of the report, a page load or a comment (default 3; 0: never)
```

Sharing report.html: the built file contains no local paths and none of the review UI, so it's ready to send. To have a double-clicked copy show a hint with `rapport serve '<its folder>'`, switch the hint on; if `rapport` isn't on the reader's PATH, also set the command it shows:

```toml
[modules.static-hint]
enabled = true
command = "~/code/rapport/bin/rapport"
```
