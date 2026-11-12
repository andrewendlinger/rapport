# Confluence: publish and sync (playbook)

A report can be published as a Confluence page, edited there by people, and those edits brought back into `report.md`. `report.md` stays the source; the page is a published copy. rapport never talks to Confluence: you fetch and publish pages with whatever Confluence tools you have (e.g. the Atlassian MCP tools `getConfluencePage` / `createConfluencePage` / `updateConfluencePage`, HTML format), and rapport does the mechanical parts:

| Command | What it does |
|---|---|
| `rapport export confluence [dir] [--json]` | writes the page body to `.rapport/confluence/export.html`; prints the title, warnings, and steps for the user (`for the user: …`) |
| `rapport confluence diff PAGE [dir]` | versions, and what changed in Confluence since the last publish, as an outline diff (below). `--local` (no `PAGE`): what changed in report.md instead |
| `rapport confluence check PAGE [dir]` | are all of Confluence's changes (edits, moves, a renamed title) in report.md now? Lists what's missing; exit 1 if anything is |
| `rapport confluence prepare PAGE [dir]` | the body to publish over the live page (`.rapport/confluence/publish.html` + `publish.json`), or a refusal: Confluence has changes report.md lacks, or an inline comment would come loose |
| `rapport confluence record PAGE [dir]` | after publishing: store the page as published (the base) and confirm the publish went as intended. Refuses to replace a base with another page, or with a page whose edits report.md lacks (`--force` only when the user wants those edits dropped) |

`PAGE` is a file holding exactly what a `getConfluencePage` call returns (JSON with `id`, `version`, `title` and an HTML `body`): the guards rely on the ID and version. Move page bodies between your Confluence tools and files without copying them through the chat: if your harness can run a script that calls tools (pi's `codemode`), fetch into a file and publish from a file that way, and make sure the script reads the whole file (`publish.json` has its `length`).

## The outline

`diff`, `check`, `prepare` and `record` compare pages as an *outline*: one line per block, inline marks as compact text, nesting by indentation. Confluence rewrites every page it stores (IDs, attribute order, `<p>` in list items, colour spans), so raw HTML never compares equal; the outline drops that noise. Big payloads are collapsed: inlined images show `[image assets/x.png #hash]`, Macro Pack sources over 600 characters a hash and their start.

```text
@@ ## Results @@
~ The run took [-12-]{+14+} minutes ⏎ on the cluster.
- [ ] check the logs
+ [x] check the logs
+ A paragraph someone added.
```

`~` lines are edits within a block (`[-removed-]{+added+}`); `-` / `+` are whole blocks. `@@` names the section. Marks: `**bold**`, `*italic*`, `` `code` ``, `[text](url)`, `<card url>` (smart link), `[status green bold: Done]`, `[date 2026-11-01]`, `[@account-id]`, `{color: #bf2600 | text}`, `⏎` (line break). Blocks: `[panel-info]`, `[expand: Title]`, `[table …]` + `| cells |`, `[macro-pack mermaid]` + source, `[extension jira {…}]`, …

## Publish (first time)

1. `rapport export confluence` (a new page has nothing to lose, so no `prepare`). Pass on every `for the user:` line (uploads, native draw.io inserts) and say what the warnings mean.
2. Create the page with the body file's content and the printed title.
3. Fetch it back and `rapport confluence record <fetched page>`; it warns if Confluence stored something other than the export.

## Bring Confluence edits back

Do this before every update; an update replaces the whole page, so skipping it deletes people's edits.

1. Fetch the live page and `rapport confluence diff <page>`. "no changes": go to *Update*.
2. Apply each change to `report.md` as a surgical edit, like reader feedback: find the passage, change it in the report's own form (a `:badge` stays a badge, a hard-wrapped paragraph stays wrapped). Map Confluence elements to modules with the table below. Content with no module goes in passthrough blocks (below).
3. If report.md also changed the same passage since the last publish (`diff --local`), don't pick a side silently: show the user both versions and ask.
4. `rapport confluence check <page>` until it says ok, then `rapport build` and look.

## Update

Nothing on the page may be lost by accident: not other people's edits, not their comments, not a save that lands while you work. So an update always goes through `prepare`, never straight from `export`:

1. Fetch the live page and `rapport confluence prepare <page>`. If it refuses, do what it says: bring the listed Confluence changes back (above), or deal with the comments it names (below), then run it again.
2. Publish `publish.html` with the title from `publish.json`, but only if the page is still at `expected_version` (fetch it right before; the update call has no version lock).
3. Fetch it back and `rapport confluence record <fetched page>`. It confirms the page went up exactly one version and Confluence stored what was sent. Pass any warning on; a version jump means someone's save in between was overwritten, and the user restores it from the page history.

Ask the user before an update that removes content someone else wrote, even when `prepare` allows it (e.g. you applied their deletion of someone else's paragraph).

## Comments made in Confluence

Inline and page comments are feedback, not content. `diff` lists inline-comment anchors (`inline comment <id> on: "…"`); read the comments themselves (inline and page comments) with your Confluence tools. Record each with `rapport feedback add "<comment>" --quote "<anchored text>"` and work through them with the normal feedback loop; reply to the comment in Confluence with what you changed.

An inline comment hangs on an anchor in the page body. `prepare` copies every anchor from the live page into the new body, around the same text, so comments survive updates. If the commented text changed in report.md, the anchor has nowhere to go and `prepare` refuses, naming the comment: resolve it in Confluence first, keep its text, or, once the user agrees to let it come loose, pass `--detach <id>`. Page comments (below the page) aren't in the body and are never touched.

## Content rapport has no module for

`::: confluence <label>` (block) and `:confluence-inline[label]{html = '…'}` hold Confluence HTML verbatim: the export writes it back unchanged, the report shows a placeholder. Use them for Jira and other live macros, uploaded images and files, child-page lists, anchors, and anything else without a module. Copy the element's HTML from the fetched page exactly; don't edit it (the export repairs fetched highlight nesting, which Confluence would otherwise turn invisible).

## Taking over an existing page

Rare: normally the report comes first. Write `report.md` from the page by hand: headings and text as Markdown, each Confluence element as its module (table below), the rest as passthrough blocks. To check it, record the fetched page as the base (`rapport confluence record <fetched page>`; there's no base yet, so it is accepted); `rapport confluence diff --local` then shows where the report's export differs from the page, which should be nothing beyond Confluence-only details you chose to drop. From then on it's a published report like any other: update through `prepare`. Ask before republishing a page you didn't create.

## How modules map

| rapport | Confluence |
|---|---|
| Markdown text, headings, lists, task lists, quotes, code, pipe tables, rules | the native elements (code titles become a bold line above the block) |
| `:status`, `:date`, `:mention` (with `id`), `:color`, `:highlight`, `:underline`, `:sub`, `:sup` | status lozenges, date pills, mentions, palette colours |
| autolinks `<https://…>` · `::: card` | inline smart links · block / embed cards |
| `::: callout` (`info`, `note`, `success`, `warning`, `error`, `custom`) | panels (custom: emoji icon, palette colour) |
| `::: collapse` · `::: columns` · `::: tabs` | expand · layouts · native tabs |
| `::: decisions` · `::: properties` · `::: grid` · `::: align` | decision list · Page Properties · table with spans, colours, widths, numbering · paragraph alignment |
| `::: mermaid`, `::: plantuml`, `::: latex`, `::: html-embed` | Macro Pack, from source (captions as a centred italic line) |
| `::: drawio` | a native draw.io diagram (never Macro Pack's draw.io XML) |
| images | Macro Pack HTML with the image inlined (up to `image_max_kb`), else a placeholder to upload |
| `::: confluence`, `:confluence-inline` | the HTML they hold, verbatim |

**Lost on the way to Confluence** (it has no equivalent): code `hl`, footnotes (become a numbered list), `collapse open`, `:badge` (becomes a subtle status), card titles, `compare` (becomes columns), diagram width, `@mentions` without an `id`. The export warns about each. When bringing edits back, keep the report's richer form: an edited status that started as a `:badge` stays a badge.

**Not seen by the outline** (so not protected): resizing an image or other media block, and the editor's own "wide" block width. Such edits made in Confluence are lost on the next update; if people make them, ask them to tell you, or keep the block as passthrough.

**Diagram size.** Macro Pack has no width option: Mermaid and PlantUML diagrams fill the page width. For a small diagram that should look tight, use `::: drawio`, or tell the user it's a Macro Pack limit.

**draw.io.** Always a native draw.io diagram; Macro Pack's draw.io XML is never used. Confluence's draw.io app stores a diagram as page attachments that only a person (or an attachment-capable token) can create, so:

1. First export: a placeholder panel (`rapport draw.io: assets/x.drawio`) and a step for the user: insert the file there as a draw.io diagram (/draw.io Diagram, Extras > Edit Diagram, paste the file's XML).
2. The next `diff` shows the placeholder replaced by `[extension drawio {"guestParams":{"diagramName":…,"pageId":…,"revision":…,"width":…,"height":…}}]`. Copy those onto the block: `confluence_page`, `confluence_name`, `confluence_revision`, `confluence_width`, `confluence_height`. Keep `src` for the local render.
3. From then on the export writes the native macro on the report's own page, and an embed (`drawio-embed`) when the block names another page.

Someone editing the diagram in Confluence shows up as a higher `revision`: update `confluence_revision`, and tell the user the local `.drawio` file is now behind (the agent can't read attachments; they can export it from Confluence).

## Config

`preset = "confluence"` sets up a Confluence-bound report in one line: the `atlassian` theme, both apps below, and a writing rule plus a `lint` check against inline math, which Confluence can't show. The settings, with every default:

```toml
[confluence]
apps = ["macro-pack", "drawio"]   # apps the site has; without them diagrams fall back to code blocks
toc = true                        # start with Confluence's TOC macro when the report has a TOC
images = "macro-pack"             # or "placeholder": a panel naming the file to upload
image_max_kb = 300
```

## Writing `confluence.html` for a module

Next to `template.html`, same variables (`node`, `ctx`, `cfg`, `meta`), plus `cf`:

| `cf.` | |
|---|---|
| `has(app)`, `setting(key)` | target site: `[confluence] apps`, other `[confluence]` keys |
| `macro(key, title, params, kind=…, body=…)` | a native macro (`toc`, `details`, …); `kind` = `extension`, `bodied-extension`, `inline-extension` |
| `extension(key, params, …)`, `macro_pack(input, text, options)`, `drawio_macro(page, name, revision)`, `drawio_embed(page, name)`, `page_id()` | app macros; the report's recorded page |
| `color(palette, name)` | the hex Confluence stores for a palette name |
| `tasks(html)`, `cards(html)`, `align(html, to)`, `strip_styles(html)` | reshape markdown-it HTML |
| `image(src, alt, caption)` | a local image, embedded or handed over |
| `warn(msg)`, `handover(msg)` | a warning; a step for the user |

`prepare.py` may define `confluence(node, ctx, cf) -> dict` for extra template variables (`prepare()` is for the page and isn't called on export). Without `confluence.html`, export reports the module as unexportable, and `modules check` warns.
