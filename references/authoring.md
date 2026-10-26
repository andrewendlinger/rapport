# Writing report.md

`rapport brief` lists the modules actually installed, with params and examples. This page covers the syntax they share.

## File layout

```
reports/q3-latency/
  report.md        ← you write this
  assets/          ← images, gifs, svgs (inlined at build: the HTML stays one file)
  rapport.toml     ← optional per-report config (or use front matter)
  report.html      ← generated; never edit
  .rapport/        ← review state: feedback, version, block index, server info (ignores itself in git)
```

## Front matter

TOML between `+++` lines, at the very top:

```toml
+++
title = "Cache latency regression"
subtitle = "Why p99 tripled after 4.2, and the fix"
author = "Platform team"
date = "2026-10-20"
status = "draft"        # shown as a pill in the header
kicker = "Incident"     # small label above the title
icon = "🧪"             # before the title: an emoji, or an image in the report folder

[theme]                 # any config section works here, for this report only
name = "paper"
+++
```

Top-level keys that aren't config sections become `meta.*` (title, subtitle, author, date, status, kicker, icon, or your own, read by layout templates as `meta.x`).

Without a `title`, a leading `# Heading` becomes the title.

## Markdown

CommonMark, plus:
- pipe tables
- `~~strikethrough~~`
- footnotes (`text[^1]` … `[^1]: note`)
- task lists (`- [ ]`, `- [x]`)

Headings `##` and `###` feed the TOC. Each gets a stable id from its text, so `[see setup](#setup)` links work.

## Block modules

```
::: name optional title text {key = "value", n = 3, flag = true, list = ["a", "b"]}
body
:::
```

- **Params** are a TOML inline table: `key = value`, comma-separated, strings in double quotes, `true`/`false` in lowercase. It must be **one line**, at the end of the opening line. A mistake gives `report.md:LINE: params must be …`.
- **Title text** before the `{…}` fills the module's positional param (usually `title`). Modules without a positional param reject it.
- **Body:** most modules take Markdown, which can include other modules. Some take raw text or TOML (the brief says which).
- **Nesting:** give the outer container more colons:

  ```
  :::: compare
  ::: pane Option A
  …
  :::
  ::: pane Option B
  …
  :::
  ::::
  ```

  Same-length nesting also works, but extra colons are easier to read.
- A `:::` inside a fenced code block never closes a container, so it's safe to show module syntax in code.

## Inline modules

`:name[text]{params}`, e.g. `:badge[shipped]{tone = "ok"}`. The `{…}` is optional.

## Code

````
```python {title = "cache.py", hl = [3, 4], line_numbers = true}
…
```
````

The language comes first, then optional params: `title`, `hl` (lines to highlight), `line_numbers`, `wrap`. Pygments highlights any language it knows; others render plain.

## Images and gifs

`![Caption text](assets/flow.gif)` on its own line becomes a figure with a caption (taken from the title, else the alt text) and click-to-zoom. Inline images inside a sentence also work.

Every file is inlined as a `data:` URI. Remote URLs are a build error (download the file into `assets/` instead), and so is going over `report.max_inline_mb` in total.

## Diagrams and math

`::: mermaid`, `::: plantuml` and `::: latex` take their source as the body; `::: drawio assets/flow.drawio` points at a file; `:math[T_2^*]` is inline TeX. They render to SVG at build time with local programs (`mmdc`, `plantuml`, the draw.io CLI, Node for MathJax, which installs itself once into `~/.cache/rapport`) and are cached there, so the report stays one offline file. If a program is missing, the build warns and shows the source. Diagrams get a light and a dark rendering.

## Raw HTML

Off by default: it's escaped. If the user really wants it, set `[report] raw_html = true`. Prefer writing a module. For a styled widget with its own CSS, use `::: html-embed`: static HTML only (scripts are removed), isolated from the page's styles, local images inlined.

## Writing for feedback

Readers comment on blocks: paragraphs, whole lists, tables, code blocks, images, panes. Selecting text inside a list item still quotes just that text. Keep paragraphs focused, so one comment maps to one fix. Use `collapse` for supporting detail rather than cutting it.
