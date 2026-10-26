+++
title = "rapport → Confluence round trip (test)"
+++

::: callout About this page
Written as a local rapport report (`report.md`) and published with `rapport export confluence`. Edit anything here in Confluence; the agent pulls your edits back into the report with `rapport confluence pull`.
:::

## Text

Plain **bold**, *italic*, ~~strike~~, `code`, a [link](https://example.org) and <https://github.com>. H:sub[2]O, mc:sup[2], :underline[under].
Hard break next\
line two.

:color[Red text]{color = "bold-red"} and :highlight[marked]{color = "lime"}; :status[Done]{color = "green"} :status[Wait]{color = "red", style = "subtle"} :badge[beta]{tone = "violet"}; due :date[2026-11-01]; owner :mention[Ada Lovelace]{id = "557058:ada"} and :mention[Bob]; rate :math[T_2^*].

### Lists

- one
- two
  - nested
1. first
2. second

- [x] done task
- [ ] open task

> A quote with *style*.

```python {title = "app.py", wrap = true}
print("hi")
```

| A | B |
|---|---|
| 1 | 2 |

---

::: callout Heads up {tone = "warning"}
Body text.
:::

::: callout {tone = "custom", icon = "💡", color = "light-purple"}
Custom panel.
:::

::: collapse More
Hidden.
:::

:::: columns {layout = "two-left-sidebar", divider = true}
::: column
Left.
:::
::: column
Right.
:::
::::

:::: tabs
::: tab One
First.
:::
::: tab Two
Second.
:::
::::

::: decisions
- [x] Decided thing
- [ ] Open thing
:::

::: properties {label = "rt"}
Owner = ":mention[Ada Lovelace]{id = \"557058:ada\"}"
Status = ':status[Draft]{color = "yellow"}'
:::

::: card https://github.com/x {title = "X"}
:::

::: align center
Centred.
:::

::: grid {numbered = true, header_column = true, widths = [100, 80, 80], cells = {B2 = {bg = "light-blue", rowspan = 2}}}
| H1 | H2 | H3 |
|---|---|---|
| a | b | c |
| d |  | f |
:::

::: mermaid {caption = "Flow"}
flowchart LR
  A --> B
:::

::: plantuml
A -> B
:::

::: latex
E = mc^2
:::

::: html-embed
<b>static</b>
:::

Text with a note[^1].

[^1]: The note.
