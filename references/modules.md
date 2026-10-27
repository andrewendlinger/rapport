# Writing a rapport module

Everything on a rapport page is a module. A module is **a folder with a `module.toml`**: no registration, no core changes. Drop the folder into a search root and it exists. Give it the same name as a built-in and it takes the built-in's place; with `extends` it changes only what you write and inherits the rest (see [Overriding and extending](#overriding-and-extending)).

```
side-note/
  module.toml     manifest: kind, summary, params, config, body, children, feedback unit
  template.html   Jinja2 template with ONE root element
  style.css       optional; scoped .rp-<name> classes, theme tokens only
  script.js       optional; Rapport.module("<name>", { init, mount, reveal })
  prepare.py      optional; prepare(node, ctx) -> extra template variables; css(ctx) -> CSS; check(config) -> warnings
  server.py       optional; routes(r) for the review server, describe(state, config) for the agent
  example.md      canonical usage, used by `brief`, `gallery` and `modules check`
  assets/         optional; files the example references (copied for check/gallery)
```

Start from a scaffold: `rapport modules new <name> --kind block --scope project`. Add `--server` for a feature with routes and stored state (see [server.py](#serverpy)). To change an installed module, `rapport modules new <existing-name>` makes a thin override that extends it: you copy in only the files you change.

## Kinds

| kind | what it is | how it's used |
|---|---|---|
| `block` | a `:::` directive | `::: name title {params}` … `:::` in report.md |
| `inline` | an inline directive | `:name[text]{params}` |
| `element` | renders a standard Markdown element: `heading`, `paragraph`, `list`, `code`, `table`, `image`, `blockquote`, `hr`, `html`, `footnotes` | automatic; override by name |
| `layout` | page shell (`page`) or a region module (`header`, `toc`, `footer`, yours) | `[layout] page`, `top`, `side`, `bottom`; more regions with `[layout.regions]` (see [Extra regions](#extra-regions)) |
| `theme` | design tokens + base typography | `[theme] name = "…"` |
| `feature` | page-level behaviour, `when = "always"` \| `"serve"` \| `"static"` | on by default; `[modules.<name>] enabled = false` turns it off (a manifest can default it to off) |

## Search roots (nearest wins, by name)

1. `[modules] paths` given in front matter or `--set`
2. `<report>/rapport-modules/`
3. for each `rapport.toml`, from the report folder upward: the `rapport-modules/` beside it, then its `[modules] paths` (relative to that file)
4. the git repository root's `rapport-modules/` (after the `rapport.toml` files inside the repo), so `modules new --scope project` works in a repo with no `rapport.toml`
5. the user `rapport-modules/` (`~/.config/rapport/rapport-modules/`, or beside the `RAPPORT_CONFIG` file; `rapport config paths` shows it), then `paths` from the user config
6. built-ins (`<skill>/modules/`)

Each root is scanned two levels deep: `root/<name>/module.toml`, or `root/<pack>/<name>/module.toml`. So a module pack is just a folder or git repo you add to `paths`.

## Overriding and extending

Lightest first:

| to | run | you own |
|---|---|---|
| change a default, add a param, change the template or a few rules of an installed module | `rapport modules new callout`: a thin override | only the files you copy in; the rest follows rapport's updates |
| make a new module from an existing one (a warning box from `callout`) | `rapport modules new warn-box --extends callout`; a new theme (`--kind theme`) extends `default` | the same |
| rewrite a module wholesale | `rapport modules new callout --copy` | every file; `modules check` says when the original changes |
| make something else | `rapport modules new side-note --kind block` | everything |

A module extends another with `extends = "<name>"` in its module.toml:

- **Same name** (`name = "callout"`, `extends = "callout"`): it builds on the next `callout` down the search roots, the one it overrides. A report's override can build on the project's, which builds on the built-in.
- **Another name** (`name = "warn-box"`, `extends = "callout"`): a new module built on the nearest `callout`.
- Its `kind` is the base's: leave it out. An `extends` that names no module, goes round in a circle, or gives another `kind` is an error, and the module isn't used.

What it inherits:

- **module.toml**: the base's, with yours on top. Tables merge key by key, at any depth: `[params.tone]` with only `default = "tip"` changes that default and keeps the type, choices and doc. A new `[params.icon]` adds a param; a theme's `[tokens] accent = "…"` changes one token. Strings and lists replace the base's (`choices` is the whole list). A param can't be removed. `name`, `extends` and `[upstream]` are the module's own.
- **Files it doesn't have**: `template.html`, `script.js`, `prepare.py`, `server.py`, `example.md` and `assets/` are the base's. `prepare.py` and `server.py` run from the base's folder. To change one, copy it in: from then on that file is yours.
- **`style.css` adds to the base's** rather than replacing it. Both go in the `rp-modules` layer, the base's first, so a rule of yours with the base's selector wins. Write only the rules you change. Each `url()` resolves in the folder of the file it's in.
- **State and routes go by name**: an override keeps its base's state (`.rapport/modules/<name>.json`, feedback's `feedback.json`) and `/api/m/<name>/`, so an override of `feedback` keeps the reader's comments. A module under another name has its own.
- **Under another name**, `script.js` and `example.md` aren't inherited, because they name the module inside: `Rapport.module("callout", …)`, `::: callout`. `modules new <name> --extends <base>` copies both in and renames them (also `Rapport.api` and `Rapport.config` calls and `"<base>:…"` events). A module with another name whose base has a `script.js` and that has none of its own is an error.
- Element modules render the Markdown element they're named after, so extend them only under their own name.

`rapport modules info <name>` shows the chain (`callout (project) extends callout (built-in)`) and where each file comes from. `rapport modules list` marks extending modules.

**Drift.** `modules new` records the base in `[upstream]`: the rapport version and a hash of each file. `rapport modules check` warns when the base has changed a file this module has its own copy of. For a `--copy` that is any file; for an override, a file it replaced (not `style.css`, which adds). `rapport modules diff <name>` prints a unified diff of the module's files against the base's, and after such a change the `[upstream]` lines to record once you have merged it. A copy made by an older rapport has no `[upstream]`; `modules diff` still compares it with what it overrides.

### Names core relies on

Core looks these modules up by name, so an override of one keeps working only if it keeps what core expects of it. Everything else about them can change.

- `runtime`: the client kernel. Required (it can't be switched off) and always the first script; every other `script.js` calls its `Rapport` API, so an override keeps the [kernel API](#the-client-kernel).
- `page`: the default for `[layout] page`, and the fallback if that names no layout module. An override must still output `css`, `features`, `data_json` (as `<script type="application/json" id="rp-data">`), `scripts`, `Rapport.start()` and an element with class `rp-content` around `content`, which the kernel and feedback look for.
- `header`, `toc`, `footer`: the defaults of `[layout] top`, `side` and `bottom` (set in `config/schema.toml`, not in code). `rapport feedback` also labels a comment on `header` "title block (front matter)".
- `default`: the default `[theme] name`, the fallback when that names no theme (also the token list `modules check` falls back to), and the base of a new theme (`modules new --kind theme`).
- `heading`, `paragraph`, `list`, `code`, `table`, `image`, `blockquote`, `hr`, `html`, `footnotes`: the elements. `rapport_core/parse.py` maps each Markdown token to one of these names, so a module with another name never renders an element. Beyond that, a leading `# Title` is lifted out of the content into `meta.title`, a paragraph holding only an image renders as `image`, and `html` renders raw HTML when `[report] raw_html` is on.
- `code`: `ctx.highlight()` marks it as used, so any module that highlights code gets `code`'s CSS, including the Pygments rules from the `css(ctx)` hook in its prepare.py; `rapport brief` prints its params. A `code` module that doesn't inherit that prepare.py loses the Pygments rules.
- `feedback`: `rapport feedback` and `rapport brief` read its `actions` from `[modules.feedback]`, and the CLI reads and writes its state in `.rapport/feedback.json` (the one state file not under `.rapport/modules/`). Its routes are `/api/m/feedback/`, and `commit_feedback` and the state folder's `.gitignore` name its file.

## module.toml

```toml
name = "side-note"        # defaults to the folder name; lowercase, digits, hyphens
kind = "block"
summary = "A margin-style aside for tangents."   # one line; shown in `rapport brief`
body = "markdown"         # block only: markdown | text | toml | none
positional = "title"      # block only: text after the name fills this param
children = ["pane"]       # block only, optional: allowed child directives
order = 50                # optional: CSS and script order within its kind (lower first, default 50)
# extends = "callout"     # optional: build on another module, inheriting what this one doesn't set (below)
# enabled = false         # optional: off by default; users turn it on with [modules.side-note] enabled = true

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

Spec keys: `type`, `default`, `doc`, plus `choices` (enum), `items` (the item type of a `list`, e.g. `"string"`), `required` (params), `path = true` (a string resolved relative to the config file that sets it), `path = "icon"` (resolved only if it names an image file, so a symbol stays as written), and `merge` / `fields` (below).

Blocks nest, and every block is commentable on its own: a reader's comment lands on the innermost block, so each pane of a `compare` (or each code block in it) takes its own comments. To tell the agent *which* part of your module a comment is about, put `data-rp-label="…"` on that part (e.g. a pane or tab); `rapport feedback` shows it as `in “After”`.

- Users set defaults for every use at `[modules.side-note]`, with both params and config keys. Per-use params win. `enabled = true|false` is added to every module automatically; it defaults to true, or to the manifest's top-level `enabled`.
- **Themes** add `[tokens]` and `[tokens.dark]` tables. Each token becomes `--rp-<name>`. Their style.css holds base typography. rapport sets the root font size to `--rp-body-size` for every theme, so give `body` `1rem` and size headings in `rem`. Define `width` and `body-size`: a theme without them gets rapport's 70ch and 17px, and `modules check` warns.
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
| `config`, `ctx.config` | the whole resolved config, all layers merged |
| `ctx.module(name)` | another module's resolved `[modules.<name>]` table |
| `meta`, `ctx.meta` | report metadata (title, version, …) |
| `ctx.markdown(text)`, `ctx.inline(text)` | render more Markdown |
| `ctx.highlight(code, lang, hl)` | Pygments → line spans (what `code` uses) |
| `ctx.asset(path)` | a local file as a `data:` URI (enforces self-containment) |
| `ctx.icon(value, tint)` | a symbol as text, or an image file inlined; tinted in the text colour when `tint`, or when it's an SVG drawn in `currentColor` |
| `ctx.href(url)` | a link target, or `""` (with a build error) for `javascript:` and other unsafe links |
| `today`, `generator` | build date, rapport version |

`prepare.py` gets no template variables, only `ctx`; that is why `config` and `meta` also hang off it.

Layout modules also get `headings` (`level`, `text`, `id`) and `region` (`top`, `side`, `bottom`, or the name of an [extra region](#extra-regions)). The `page` module gets `content`, `regions` (region name → its rendered HTML), `css`, `scripts`, `features`, `data_json`, `lang`, `theme_mode`. The variables `prepare()` returns are added to all of these.

## style.css

- Scope everything under `.rp-<name>` (or `.rp-m-<name>`).
- Use theme tokens only: `var(--rp-ink)`, `--rp-surface`, `--rp-accent`, `--rp-line`, `--rp-radius`, `--rp-sans`, …. Run `rapport modules info default` for the full list. Then every theme, light and dark, applies automatically. `modules check` warns about literal colours and unknown tokens. Custom properties your module defines itself are fine.
- **Cascade layers.** The theme's CSS goes in `@layer rp-theme` and module CSS in `@layer rp-modules`, so a module rule beats any theme rule regardless of specificity. A user's `[theme] css` comes after both, unlayered, and beats them. Don't use `!important`: in a layer it beats the user's CSS, which then can't override your module. `modules check` warns about it (feature modules and a theme's `prefers-reduced-motion` block excepted).
- **Font sizes in `rem` or `em`**, so they follow the theme's `body-size` (`1rem` is the body size). The built-ins write `calc(13.5rem/17)`: 13.5px at a 17px body size, 12.7px at the default 16px. Feature modules may use px, because their UI shouldn't grow with the report's text. Elsewhere `modules check` warns about px font sizes.
- **Bundled files.** `url()` paths resolve relative to the module folder, and the files are inlined as `data:` URIs (fonts and images only), so a theme can ship its fonts: `src:url(fonts/Brand.woff2)`. Remote URLs, root-relative paths, `@import` and a `var()` inside `image-set()` fail the build. Inlined files count toward `report.max_inline_mb`.
- CSS is included only when the module is used on the page.
- CSS that depends on a setting can come from a `css(ctx)` hook in [prepare.py](#preparepy).

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

### The client kernel

The first script on the page is the kernel, `Rapport`; the module scripts follow in `kind`, `order`, name order. Once they have all loaded, the kernel starts each module in that order (its `init`, then its `mount` for every element), and then emits `ready`.

| callback | called | `ctx` |
|---|---|---|
| `init(ctx)` | once per page, if the module is on it | `{config, data}` |
| `mount(el, ctx)` | for every element the module rendered | `{config, params, data}` |
| `reveal(el, target)` | when something must become visible inside `el` | none |

`ctx.config` is the module's resolved `[modules.<name>]` table, manifest defaults included. `ctx.params` is the use's validated params, read from the `data-rp-params` attribute the renderer writes on the root of a scripted module that has params; it is `{}` if the module has none, as inline and layout modules never do. `ctx.data` is the page data.

| API | does |
|---|---|
| `Rapport.module(name, def)` | registers a module; `def` holds `init`, `mount`, `reveal` (all optional) |
| `Rapport.on(evt, fn)` | calls `fn(payload)` on every `evt`; a throwing listener is logged, not fatal |
| `Rapport.emit(evt, payload)` | fires `evt`. Name your own `<module>:<what>`, like `side-note:opened` |
| `Rapport.config(name)` | a module's resolved config, or `{}` if it has no script.js or isn't on the page |
| `Rapport.params(el)` | the params of a rendered element |
| `Rapport.data` | the page data |
| `Rapport.busy(fn)` | registers a predicate that returns true while the reader is mid-task (a comment being typed, text selected); live reload waits while any does |
| `Rapport.busy()` | whether any predicate says so now |
| `Rapport.reveal(el)` | makes `el` visible: opens `<details>` ancestors and calls the `reveal` of every module around it; returns `el` |
| `Rapport.reload()` | reloads the page and returns to the same scroll position; emits `before-reload` first |
| `Rapport.api(module, path, opts)` | calls a route of the module's `server.py` and returns a Promise of the parsed answer. `opts.body` is sent as JSON; `opts.method` defaults to POST with a body, else GET. It rejects with an `Error` holding the server's message and `.status` (0: no answer, or a static page). A 403 on a write means the session token is stale: the kernel emits `session:stale` and reloads the page 0.7 s later, so drafts saved in `before-reload` survive |

`Rapport.start()` is called by the page template once all scripts are in. It is internal: don't call it.

| event | emitted by | payload |
|---|---|---|
| `ready` | the kernel, after every module's `init` and `mount` | none |
| `before-reload` | `Rapport.reload()` | none; save unsent input (to `sessionStorage`) here |
| `session:stale` | `Rapport.api()`, when a write got 403: the page reloads 0.7 s later | none; tell the reader why (feedback shows a toast) |
| `server:down` | live-reload, when the server stops answering | none |
| `server:up` | live-reload, when it answers again after being down | none |
| `<module>:changed` | live-reload, when the stored state of a module with a `server.py` changed (here, in another tab, or from another process) | the `/api/meta` response: `{build_id, errors, version, revs, dir}` |
| `feedback:changed` | the same, for the feedback module: a comment added or resolved, here or from the CLI | the `/api/meta` response |

Only served pages (`rapport serve`) emit the `server:` and `:changed` events.

Page data (`Rapport.data`, `ctx.data`) is built at render time:

| key | holds |
|---|---|
| `report` | `{title, version, served, source}`: `served` is true on a `rapport serve` page, `source` is the report file name |
| `modules` | `{name: resolved config}` for each module on the page that has a script.js |
| `present` | the names of all modules used on the page, sorted |
| `serve` | served pages only: `{token, api, build_id}`: the session token (sent as `X-Rapport-Token`), the API base path and the id of this build |

## prepare.py

```python
def prepare(node, ctx):
    rows = node.attrs.get("data", {}).get("row", [])   # body = "toml"
    return {"total": sum(r["value"] for r in rows)}     # extra template variables
```

`prepare(node, ctx)` runs each time a module renders a template, whatever its kind, and what it returns is added to the template's variables. It runs once per use of a block, element or inline module, and once per page for a layout region module, the `page`, and a feature (a feature without a template.html renders nothing, so it doesn't run). `node` depends on the kind:

| kind | `node` |
|---|---|
| block, element | the node the template gets: `params`, `body`, `children`, `text`, `attrs`, `html`, `id`, `lines`, `heading_path` |
| inline | `module`, `params`, `body` (the rendered text between the brackets), `text` (the raw text); `id` is `""` |
| layout region module (`header`, `toc`, yours) | `module` and `id` are its name, `attrs` is `{"region": "top"}` (or `side`, `bottom`, the name of an [extra region](#extra-regions)), `params` is `{}` |
| `page` | `module` is the page's name; `id` is `""`, `params` is `{}` |
| feature | `module` is its name; `id` is `""`, `params` is `{}` |

Layout, page and feature modules take no params, so their `node` only says who is asking: read the rest from `ctx` (`ctx.meta`, `ctx.module(name)`, `ctx.config`). A load error in prepare.py, or an exception in `prepare`, is a build error naming the module; the template then renders without its variables.

**Data for `script.js`.** There is no separate hook for it: render it in the template, as JSON in a `<script>` element. `tojson` escapes `<`, `>`, `&` and `'`, so the data can't end the element, and scripts are never part of a feedback quote.

```python
def prepare(node, ctx):
    return {"stats": {"rows": 12, "label": ctx.meta.get("title")}}
```

```html
<script type="application/json" class="rp-stats-data">{{ stats|tojson }}</script>
```

```js
var stats = JSON.parse(document.querySelector(".rp-stats-data").textContent);   // in init(); in mount, search inside el
```

**`css(ctx) -> str`.** Optional. CSS computed at build time, for rules that depend on a setting and so can't sit in style.css. It is called once per page, for each module on the page that defines it, and the CSS it returns goes right after that module's style.css, in the same cascade layer (`rp-modules`; `rp-theme` for the theme). Its `url()` files are inlined as in style.css, relative to the folder of the prepare.py that defines it. A `css()` that raises is a build error naming the module.

```python
def css(ctx):
    cols = ctx.module("gallery").get("columns", 3)
    return f".rp-gallery{{grid-template-columns:repeat({cols},1fr)}}"
```

The built-in `code` module uses it for its Pygments rules, from `[modules.code] light_style` and `dark_style` (`modules/element/code/prepare.py`).

**`check(config) -> list[str]`.** Optional. It validates the module's `[modules.<name>]` settings whenever config loads; each string becomes a warning in `rapport config validate`, `lint` and `brief`. The built-in `feedback` module uses it to catch two actions sharing a shortcut key.

`prepare.py` runs as Python code, so it carries the same trust as the folder it lives in. `rapport modules list` marks modules that have one.

### Extra regions

The default page has three regions, `top`, `side` and `bottom`, and `[layout]` fills them with layout modules. `[layout.regions]` adds more: a banner above everything, a right rail. The page template places each one as `{{ regions.<name> }}`, and the default page places only its own three, so an extra region needs a `page` override:

```toml
# rapport.toml
[layout.regions]
banner = ["notice"]          # the layout modules in it, in order
```

```
rapport modules new page     # a thin override: copy the built-in template.html in, then add {{ regions.banner }} where it goes
```

`rapport modules info page` shows where the built-in's template.html is.

Region names are lowercase letters, digits and hyphens, and can't be `top`, `side` or `bottom` (set those with `[layout] top = […]`). Write `regions["right-rail"]` for a name with a hyphen, and for one that is also a dict method (`items`, `keys`, `values`, `get`). Every module in a region must be a layout module; each gets `region` set to the region's name. A region the page template never mentions is still rendered, and the build warns that the page doesn't place it.

## server.py

A module can extend the review server: a sign-off button, a poll, a checklist whose ticks persist. Its `server.py` registers routes. `rapport serve` answers them under `/api/m/<name>/`, keeps the module's state, and tells the page when that state changes. `rapport modules new <name> --server` scaffolds a working example.

The built-in reader feedback is one such module: `modules/feature/feedback/server.py` (with its `script.js`) is the reference implementation. An override of `feedback` inherits both halves and replaces whichever it copies in.

```toml
# rapport-modules/signoff/module.toml
name = "signoff"
kind = "feature"
summary = "Reviewers sign off at the end of the report."
when = "serve"            # routes only exist in `rapport serve`
```

```python
# rapport-modules/signoff/server.py
def routes(r):
    r.get("/", show)
    r.post("/", sign)                 # body: {"name": "Ada"}
    r.delete("/{who}", unsign)        # /api/m/signoff/Ada → req.params["who"] == "Ada"


def show(req):
    return {"signed": req.store.read().get("signed", [])}


def sign(req):
    who = str(req.body.get("name") or "").strip()
    if not who:
        raise req.Refused("type your name first")      # → 400; the page gets the message
    with req.store.edit() as state:                   # locked; saved when the block ends
        state.setdefault("signed", []).append({"name": who, "version": req.version})
    return {"signed": state["signed"]}


def unsign(req):
    with req.store.edit() as state:
        signed = state.get("signed", [])
        if not any(s["name"] == req.params["who"] for s in signed):
            raise req.NotFound(f"{req.params['who']} has not signed off")    # → 404; nothing is saved
        state["signed"] = [s for s in signed if s["name"] != req.params["who"]]
    return {"signed": state["signed"]}


def describe(state, config):
    """What `rapport modules state signoff` prints."""
    return "\n".join(f"{s['name']} signed off on v{s['version']}" for s in state.get("signed", [])) or "nobody yet"
```

```js
// rapport-modules/signoff/script.js
Rapport.module("signoff", {
  init: function () {
    var box = document.createElement("p");
    box.setAttribute("data-rp-ui", "");               // chrome: never quoted in feedback
    box.innerHTML = '<span></span> <button type="button">Sign off</button>';
    document.querySelector(".rp-content").appendChild(box);
    var text = box.querySelector("span");
    function show(res) { text.textContent = res.signed.map(function (s) { return s.name; }).join(", ") || "Nobody yet."; }
    function fail(err) { text.textContent = err.message; }        // the server's message
    function load() { Rapport.api("signoff", "/").then(show, fail); }
    box.querySelector("button").addEventListener("click", function () {
      var name = window.prompt("Your name");
      if (name) Rapport.api("signoff", "/", { body: { name: name } }).then(show, fail);
    });
    Rapport.on("signoff:changed", load);              // changed here, in another tab, or by another process
    load();
  }
});
```

### Routes

`routes(r)` registers handlers with `r.get`, `r.post`, `r.put`, `r.patch` and `r.delete(path, fn)`. A path starts with `/`. A `{name}` segment is a path parameter: one segment, lowercase name. Literal segments win over parameters, so `/items/new` matches before `/items/{id}`. Registering a method and path twice, a bad segment, or a handler that isn't a function is an error.

A handler takes one argument, the request, and returns the answer: anything JSON can hold (`None` is `null`). The server calls handlers from several threads at once, and reloads `server.py` on every rebuild, so keep state in `req.store`, never in module globals.

| `req.` | holds |
|---|---|
| `body` | the JSON body: always an object, `{}` for GET |
| `params` | the path parameters, URL-decoded strings |
| `query` | the query string: `?since=3` gives `{"since": "3"}` |
| `store` | this module's state, below |
| `config` | this module's resolved `[modules.<name>]` table |
| `version` | the report version now (`rapport feedback resolve` bumps it) |
| `report_dir` | the report folder, a `Path` |
| `blocks` | the block index of the last successful build: `{id: {module, lines, src, heading_path, section, text}}` |
| `full_config` | that build's whole resolved config: `.get("report.source")`, `.module(name)` |
| `method` | `GET`, `POST`, `PUT`, `PATCH` or `DELETE` |

| a handler raises | the page gets |
|---|---|
| `req.Refused("…")` | 400 `{"error": "…"}`: the input was wrong |
| `req.NotFound("…")` | 404 `{"error": "…"}` |
| anything else | 500 `{"error": "server error: …"}`; the traceback goes to the server log (the terminal, or `.rapport/server.log` with `--detach`) |

An unknown module or route also answers 404, and a state file that isn't valid JSON answers 500.

### State

`req.store` is one JSON object per module, kept in `.rapport/modules/<name>.json` (feedback's, older, in `.rapport/feedback.json`).

- `req.store.read()` returns it, or `{}` before the first write.
- `with req.store.edit() as state:` locks it against other requests and processes, and saves it when the block ends. Change `state` in place. An exception inside the block (a `Refused`, say) saves nothing. Don't nest edits.
- A GET handler can't edit: GET needs no token, so any web page could call it. Change state in POST, PUT, PATCH or DELETE.
- A block module has one store, not one per use. If a block can appear twice, key its state by a param (an `id`).

The state folder ignores itself in git; `[report] commit_feedback = true` lets module state through along with the feedback.

Each poll of `/api/meta` carries `revs`: every served module's state revision, the state file's modification time as a string. When one moves, live-reload emits `<name>:changed` with the `/api/meta` response. A write from another process counts too, so listen to the event and fetch again.

On the page, `Rapport.api(module, path, opts)` calls a route (see the [kernel table](#the-client-kernel)). Encode path parameters with `encodeURIComponent`. A static file has no server: `Rapport.api` rejects at once there, so give the module `when = "serve"` or a static fallback.

### What core enforces

Before a handler runs, the server has:

- checked the `Host` header (no DNS rebinding);
- required the page's session token (`X-Rapport-Token`) for every method but GET, so another site can't post to it;
- refused a body that isn't a JSON object, or is over 1 MB;
- answered 404 for a module that is switched off, is `when = "static"`, or has no `server.py`.

Routes come from the last successful build. An edit to `server.py` applies on the next rebuild. A `server.py` that fails to load, or whose `routes(r)` fails, is a build error: in the server log and on the page, never a crash.

`server.py` runs as Python code, with the same trust as `prepare.py` and the folder it lives in. `rapport modules list` marks modules that have one, `modules info` lists their routes, and `modules check` loads the file and runs `routes(r)`.

### Reading the state as the agent

Agents never touch `.rapport/`. `rapport modules state <name> [-C <dir>]` prints the module's state: the text `describe(state, config)` returns, or the JSON if `server.py` has no `describe`. `--json` always prints the JSON and runs no module code. The CLI can't write module state.

## Heavier libraries

Output must stay a single self-contained file: never load anything from a CDN. For charts and diagrams, render server-side in `prepare.py`, e.g. emit SVG. There's no supported way yet to bundle a third-party JS library into a module; it will come with the first module that needs one (such as Mermaid).

## Checklist

- `rapport modules check <name>` passes: manifest valid, template parses, example renders with exactly one root element, style.css uses tokens, `rem`/`em` font sizes and no `!important`, its `url()` files exist, and `server.py` loads and registers its routes.
- `example.md` shows realistic use; the brief prints it to every agent.
- `summary` says what the module is *for*, in one line.
