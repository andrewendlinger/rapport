# Changing rapport

rapport is an Agent Skill plus a Python CLI, and this folder is both. `SKILL.md` tells an agent how to *use* rapport; this file is for changing rapport itself. Runtime: Python 3.11+, markdown-it-py, mdit-py-plugins, Jinja2, Pygments, and nothing else.

## Where things are

- `rapport_core/`, the engine:
  - `cli.py` the commands, one `cmd_*` each · `config.py` the config layers and module search roots · `schema.py` types, defaults, merging
  - `registry.py` module discovery, manifests, `extends`, loading `prepare.py`/`server.py` · `parse.py` Markdown and `:::` into a `Document` with line maps
  - `render.py` nodes into HTML through module templates; assets, CSS/JS, the block index · `css.py` `url()` inlining, cascade layers · `build.py` parse, render, write
  - `serve.py` the review server · `routes.py` the routes of a module's `server.py` · `store.py` state files in `.rapport/`
  - `feedback.py` the feedback store and `rapport feedback` · `changes.py` the change bars · `brief.py`, `lint.py`, `modules_cmd.py` those commands · `util.py` errors, `did_you_mean`, `atomic_write`
  - `confluence.py` the export (`confluence.html` templates) · `confluence_sync.py`, `confluence_cli.py` the page outline and `rapport confluence` · `dom.py` a small HTML tree for both
  - `palette.py` the named colour palettes · `external.py` local tools (diagrams), with a cache · `mathjax.py`/`mathjax.cjs` math to SVG
- `modules/<kind>/<name>/`: every built-in part of a page. `layout` (page, header, toc, footer), `theme`, `element` (heading, code…), `block` (`:::`), `inline`, `feature` (runtime kernel, feedback, live-reload…).
- `config/schema.toml` core settings (type, default, doc, merge rule); a module's own live in its `module.toml`. `presets/` named bundles of settings.
- `references/`: `modules.md` is THE module contract and the only place it is written down; `config.md`, `authoring.md`, `state.md`.
- `scripts/rapport.py` is the entry point with its dependency header; `bin/rapport` picks `.venv`, then `uv run --script`, then `python3`.
- `examples/primer/`: the tutorial report. Its `report.html` is tracked and generated, so never build the primer in place while testing.
- `tests/` unit suite (fixtures in `conftest.py`: `report(text, files)`, and an autouse `isolated_home` so no real user config leaks in); `tests/e2e/` Playwright.

## Prefer a module to core code

A new page element, setting or server behaviour should be a module, or a generic hook every module can use. Customisations go in module folders (`rapport modules new`). Core code must not name a particular module, except the few listed under "Names core relies on" in `references/modules.md`. The hooks, all documented there:

- template variables and `ctx` (`asset`, `icon`, `highlight`, `module()`…); `prepare(node, ctx)` for extra variables, `css(ctx)` for build-time CSS, `check(config)` for warnings
- `server.py`: `routes(r)` under `/api/m/<name>/`, with the module's state in `req.store`
- the client kernel: `Rapport.module/on/emit/api/busy/reveal/reload`, `init`/`mount`/`reveal`, the events
- `[layout.regions]` and `extends` (thin overrides)

A new hook gets a section in `modules.md` and a built-in module that uses it, as `css()` has `code` and `server.py` has `feedback`.

## Invariants

- **Self-contained output.** No remote URL in a page. Assets become `data:` URIs (`Renderer.asset` in `render.py`, `css.py`); a remote `src`, `@import` or `url()` is a build error; the total is capped by `report.max_inline_mb`.
- **Reader text versus chrome.** UI the reader didn't write carries `data-rp-ui`, so it never lands in a quote and every quote can be found in the source. `render.text_of` stores the text a reader sees per block (`blocks.json`); the change bars diff it and place the added words with the feedback script's text model (`isUI`/`textIndex` in `modules/feature/feedback/script.js`). The two must agree: change one, change both, run e2e.
- **One parsed `Document`.** `build.build` parses once and renders twice, static and served. Rendering must never mutate the `Document` or its nodes.
- **`build_id`** (`serve.App`) moves only on a successful build (a failed one that bumped it would reload the page forever) and counts from the server's start time, so a page open across a restart reloads.
- **Security** (`serve.py`, `routes.py`). `_host_ok` on every request (DNS rebinding), `_token_ok` on every write (CSRF). All writes are module routes, never a handler added to `serve.py`. A GET handler gets a read-only store: GET needs no token.
- **State files** go through `store.StateFile`: thread lock, flock and atomic write. Edit with `with store.edit() as state:`, never nest edits, never write `.rapport/` files directly.
- **Module Python** loads through `registry.load_code`, which writes no `__pycache__`: a new folder in a module looks like an edit to the watcher and rebuilds a second time.
- **CSS layers** (`css.layered`): theme in `rp-theme`, modules in `rp-modules`, the user's `[theme] css` unlayered after both so it wins. Module CSS uses theme tokens and no `!important` (`rapport modules check` warns).
- **Module JS is ES5**: `var` and `function`, no arrows, `let`/`const` or template literals, no build step, no npm.
- **No new runtime dependencies.** The lists in the `scripts/rapport.py` header, `requirements.txt` and `pyproject.toml` must be identical; `tests/test_project.py` checks.

## Workflow

```sh
uv run pytest                          # unit suite, about 25 s; the first run creates .venv/
uv run --group e2e pytest tests/e2e    # browser suite, about 40 s
uvx ruff check                         # lint; rules and line length in pyproject.toml
```

- Run e2e after any change to a `script.js`, `style.css` or template, or to the server; unit tests can't see them. It needs a Chromium (`uvx playwright install chromium`, or set `RP_CHROME`). Without Playwright it skips itself, which is not a pass.
- `uv run` makes `.venv/` here (git-ignored), and `bin/rapport` prefers it. Run `uv run pytest` once after changing a dependency to refresh it. `uv.lock` is git-ignored on purpose: users resolve fresh dependencies, so the tests should too.
- Add tests next to similar ones. CLI tests call `main([...])` with `capsys`; server tests use `make_server(d, port=0, pinned=True)`.
- To try a change on a real report, use a copy in `mktemp -d`, or `rapport gallery --out "$(mktemp -d)"`.
- A user-visible change gets a bullet under `## Unreleased` in `CHANGELOG.md`: a bold lead phrase, then one or two plain sentences.
- Keep the docs in step: `references/modules.md` for the module contract, `references/config.md` and the `doc` fields in `config/schema.toml` for settings, README for user-facing basics. Touch `SKILL.md` only when an agent's workflow changes.

## Release

1. Set `__version__` in `rapport_core/__init__.py`.
2. In `CHANGELOG.md`, turn `## Unreleased` into `## X.Y.Z (YYYY-MM-DD)`.
3. Regenerate the primer from a clean copy. Building in place would put this machine's review version (`examples/primer/.rapport/`) and user config into the tracked file.

   ```sh
   d=$(mktemp -d) && cp -R examples/primer "$d/primer" && rm -rf "$d/primer/.rapport" \
     && RAPPORT_CONFIG=/dev/null bin/rapport build "$d/primer" && cp "$d/primer/report.html" examples/primer/report.html
   ```
4. Run the three commands above, then commit everything as `rapport X.Y.Z`.

## Style

- Match the surrounding code: small functions, comments and docstrings that say why, not what. A broad `except Exception` carries `# noqa: BLE001 - <reason>`.
- Lines run to about 120; ruff stops them at 140.
- Error messages are short and say what to do. Offer `did_you_mean(word, options)` wherever a name can be mistyped.
- Docs use plain, short sentences.
