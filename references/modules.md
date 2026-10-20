# Writing a rapport module

Everything on a rapport page is a module. A module is **a folder with a `module.toml`**: no registration, no core changes. Drop the folder into a search root and it exists. Give it the same name as a built-in and it replaces that built-in.

```
side-note/
  module.toml     manifest: kind, summary, params, config, body, children, feedback unit
  template.html   Jinja2 template with ONE root element
  style.css       optional; scoped .rp-<name> classes, theme tokens only
  script.js       optional; Rapport.module("<name>", { init, mount, reveal })
  prepare.py      optional; prepare(node, ctx) -> extra template variables; check(config) -> warnings
  example.md      canonical usage, used by `brief`, `gallery` and `modules check`
  assets/         optional; files the example references (copied for check/gallery)
```

Start from a scaffold: `rapport modules new <name> --kind block --scope project`. Run `rapport modules new <existing-name>` to copy an installed module as an override.

## Kinds

| kind | what it is | how it's used |
|---|---|---|
| `block` | a `:::` directive | `::: name title {params}` … `:::` in report.md |
| `inline` | an inline directive | `:name[text]{params}` |
| `element` | renders a standard Markdown element: `heading`, `paragraph`, `list`, `code`, `table`, `image`, `blockquote`, `hr`, `html`, `footnotes` | automatic; override by name |
| `layout` | page shell (`page`) or a region module (`header`, `toc`, `footer`, yours) | `[layout] page`, `top`, `side`, `bottom` |
| `theme` | design tokens + base typography | `[theme] name = "…"` |
| `feature` | page-level behaviour, `when = "always"` \| `"serve"` \| `"static"` | on unless `[modules.<name>] enabled = false` |

## Search roots (nearest wins, by name)

1. `[modules] paths` given in front matter or `--set`
2. `<report>/rapport-modules/`
3. for each `rapport.toml`, from the report folder upward: the `rapport-modules/` beside it, then its `[modules] paths` (relative to that file)
4. the git repository root's `rapport-modules/` (after the `rapport.toml` files inside the repo), so `modules new --scope project` works in a repo with no `rapport.toml`
5. `~/.config/rapport/rapport-modules/`, then `paths` from the user config
6. built-ins (`<skill>/modules/`)

Each root is scanned two levels deep: `root/<name>/module.toml`, or `root/<pack>/<name>/module.toml`. So a module pack is just a folder or git repo you add to `paths`.

## module.toml

```toml
name = "side-note"        # defaults to the folder name; lowercase, digits, hyphens
kind = "block"
summary = "A margin-style aside for tangents."   # one line; shown in `rapport brief`
body = "markdown"         # block only: markdown | text | toml | none
positional = "title"      # block only: text after the name fills this param
children = ["pane"]       # block only, optional: allowed child directives
order = 50                # optional: CSS/JS order within its kind

[params.title]            # per-use params (::: side-note Title {tone = "warm"})
type = "string"           # string | int | number | bool | enum | list | table | tables | any
default = ""
doc = "Shown above the aside."
required = false          # params only: the build fails if it's missing or empty

[params.tone]
type = "enum"
choices = ["plain", "warm"]
default = "plain"
doc = "Colour."

[config.collapsed_on_print]   # module-wide settings (not per use)
type = "bool"
default = false
doc = "Hide when printing."

[feedback]
unit = "block"            # block | none (none = chrome: not commentable, never quoted)
```

Spec keys: `type`, `default`, `doc`, plus `choices` (enum), `items` (the item type of a `list`, e.g. `"string"`), `required` (params), `path = true` (a string resolved relative to the config file that sets it), and `merge` / `fields` (below).

Blocks nest, and every block is commentable on its own: a reader's comment lands on the innermost block, so each pane of a `compare` (or each code block in it) takes its own comments. To tell the agent *which* part of your module a comment is about, put `data-rp-label="…"` on that part (e.g. a pane or tab); `rapport feedback` shows it as `in “After”`.

- Users set defaults for every use at `[modules.side-note]`, with both params and config keys. Per-use params win. `enabled = true|false` is added to every module automatically.
- **Themes** add `[tokens]` and `[tokens.dark]` tables. Each token becomes `--rp-<name>`.
- **Features** add `when`, and `required = true` for the kernel only.
- For a list-of-tables setting merged by id (as `feedback.actions` is), declare `type = "tables"` with `merge = "by-id"`, a `[config.x.fields.*]` schema, and `[[config.x.default]]` entries.

## template.html

Jinja2 with autoescape on. The renderer injects `data-rp-id`, `data-rp-module`, `data-rp-lines` and the classes `rp-m-<name> rp-block` into the **first element** (leading HTML comments are skipped). So produce exactly one root element; `modules check` fails templates that don't.

| variable | meaning |
|---|---|
| `node.params` | validated params: manifest defaults < `[modules.<name>]` < per-use |
| `node.body` | the rendered Markdown body (block modules with `body = "markdown"`) |
| `node.children` | child views (`.html`, `.module`, `.params`, `.positional`), e.g. one per pane |
| `node.text` | raw body (`body = "text"`); for `body = "toml"` the parsed table is `node.attrs.data` |
| `node.attrs` | element-specific: `level`/`slug`/`text` (heading), `lang` (code), `src`/`alt`/`title` (image) |
| `node.html` | element modules: markdown-it's own rendering of the element |
| `node.id`, `node.lines`, `node.heading_path` | anchor id, source lines, enclosing headings |
| `cfg` | this module's resolved `[modules.<name>]` table |
| `meta` | report metadata (title, version, …) |
| `ctx.markdown(text)`, `ctx.inline(text)` | render more Markdown |
| `ctx.highlight(code, lang, hl)` | Pygments → line spans (what `code` uses) |
| `ctx.asset(path)` | a local file as a `data:` URI (enforces self-containment) |
| `today`, `generator` | build date, rapport version |

Layout modules also get `headings` (`level`, `text`, `id`) and `region`. The `page` module gets `content`, `regions`, `css`, `scripts`, `features`, `data_json`, `lang`, `theme_mode`.

## style.css

- Scope everything under `.rp-<name>` (or `.rp-m-<name>`).
- Use theme tokens only: `var(--rp-ink)`, `--rp-surface`, `--rp-accent`, `--rp-line`, `--rp-radius`, `--rp-sans`, …. Run `rapport modules info default` for the full list. Then every theme, light and dark, applies automatically. `modules check` warns about literal colours and unknown tokens. Custom properties your module defines itself are fine.
- CSS is included only when the module is used on the page.

## script.js

```js
Rapport.module("side-note", {
  init: function (ctx) { /* once per page if the module is present; ctx.config, ctx.data */ },
  mount: function (el, ctx) { /* per rendered element; ctx.params, ctx.config */ },
  reveal: function (el, target) { /* make target (inside el) visible: open, switch tab */ }
});
```

- **`reveal`** is how feedback and links reach content hidden inside your module. `<details>` ancestors open automatically.
- **`data-rp-ui`**: mark chrome with this attribute: buttons, and any text that isn't in report.md (a label made up from a param default, a tone name). It's then never part of a feedback quote, so the agent can find every quote in the source. Buttons marked this way are hidden when printing. Text the author wrote, such as a title, should stay quotable.
- **`data-rp-label="…"`**: names a part of your module (a pane, a tab) for feedback; see above.
- **Kernel helpers:**
  - `Rapport.on(evt, fn)` / `Rapport.emit(evt, payload)`
  - `Rapport.config(name)`
  - `Rapport.busy(fn)`: return true while the reader is mid-task, and live reload waits
  - `Rapport.reveal(el)`

## prepare.py

```python
def prepare(node, ctx):
    rows = node.attrs.get("data", {}).get("row", [])   # body = "toml"
    return {"total": sum(r["value"] for r in rows)}     # extra template variables
```

It may also define `check(config) -> list[str]`, which validates the module's `[modules.<name>]` settings whenever config loads; each string becomes a warning in `rapport config validate`, `lint` and `brief`. The built-in `feedback` module uses it to catch two actions sharing a shortcut key.

`prepare.py` runs as Python code, so it carries the same trust as the folder it lives in. `rapport modules list` marks modules that have one.

## Heavier libraries

Output must stay a single self-contained file: never load anything from a CDN. For charts and diagrams, render server-side in `prepare.py`, e.g. emit SVG. There's no supported way yet to bundle a third-party JS library into a module; it will come with the first module that needs one (such as Mermaid).

## Checklist

- `rapport modules check <name>` passes: manifest valid, template parses, example renders with exactly one root element.
- `example.md` shows realistic use; the brief prints it to every agent.
- `summary` says what the module is *for*, in one line.
