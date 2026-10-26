"""Confluence export and sync: the markup the export writes, the page outline
(Confluence's stored HTML of a published report reads the same as the export),
and diff / check / status against a live page."""

import json
import re
from pathlib import Path

import pytest

from conftest import GIF
from rapport_core import confluence_sync as sync
from rapport_core.confluence import export
from rapport_core.confluence_sync import comments, outline, render_diff

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "confluence-showcase.html"
APPS = ['confluence.apps=["macro-pack", "drawio"]']
FLOW = (Path(__file__).resolve().parent.parent / "modules/block/drawio/assets/flow.drawio").read_text()

REPORT = """+++
title = "Round trip"
+++

## Text

Plain **bold**, *italic*, ~~strike~~, `code`, a [link](https://example.org) and <https://github.com>. H:sub[2]O, mc:sup[2], :underline[under].
Hard break next\\
line two.

:color[Red text]{color = "bold-red"} and :highlight[marked]{color = "lime"}; :status[Done]{color = "green"} :status[Wait]{color = "red", style = "subtle"}; due :date[2026-11-01]; owner :mention[Ada]{id = "557058:1"}; rate :math[T_2^*]. Prices like $5 and snake_case_names stay as they are.

- one
- two
  - nested

1. first
2. second

- [x] done task
- [ ] open task
  - [ ] nested task

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
Owner = ":mention[Ada]{id = \\"557058:1\\"}"
:::

::: card https://github.com/x
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
@startuml
A -> B
@enduml
:::

::: latex
E = mc^2
:::

::: drawio assets/flow.drawio
:::

::: html-embed
<b>static</b>
:::

![A dot](assets/dot.gif)
"""


def files(name="r"):
    return {f"reports/{name}/assets/dot.gif": GIF, f"reports/{name}/assets/flow.drawio": FLOW}


# ── export ──────────────────────────────────────────────────────────────────────── #
def test_export_markup(report):
    res = export(report(REPORT, files()), APPS)
    assert res.ok, [e.message for e in res.errors]
    b = res.body
    assert res.title == "Round trip"
    assert '<span style="background-color: #d3f1a7"><span style="color: #172b4d">marked</span></span>' in b
    assert 'data-status-style="bold">Done' in b and 'data-status-style="subtle">Wait' in b
    assert '<a href="https://github.com" data-card-appearance="inline"></a>' in b
    assert '<ul data-type="task-list"><li data-type="task-item"><input type="checkbox" checked> done task</li>' in b
    assert re.search(r'open task</li><ul data-type="task-list"><li data-type="task-item"><input type="checkbox"> '
                     r'nested task</li></ul></ul>', b)                  # a nested task list follows its item
    assert '<div data-type="panel-warning"><p><strong>Heads up</strong></p>' in b
    assert 'data-type="panel-custom" data-icon=":bulb:" data-icon-text="💡" data-color="#EAE6FF"' in b
    assert '<section data-type="layout-two-left-sidebar" data-column-rule-style="solid">' in b
    assert 'data-extension-key="native-tabs"' in b and '"title":"Two"' in b
    assert '<li data-type="decision-item" data-state="UNDECIDED">Open thing</li>' in b
    assert '<td rowspan="2" data-colwidth="80" data-background="#DEEBFF">b</td>' in b
    assert '<table data-number-column="true" data-display-mode="fixed">' in b
    assert "\"input\":\"mermaid\"" in b and "data-parameters='{" in b and "&quot;" not in b
    assert '<p style="text-align: center"><em>Flow</em></p>' in b
    assert 'data-rapport-src=\\"assets/dot.gif\\"' in b or 'data-rapport-src="assets/dot.gif"' in b.replace('\\"', '"')
    assert '<code>\\(T_2^*\\)</code>' in b
    assert "macroParams" in b and "Page Properties" in b


def test_export_without_apps_falls_back_and_hands_over(report):
    res = export(report(REPORT, files()))
    msgs = " ".join(str(w) for w in res.warnings)
    assert 'class="language-mermaid"' in res.body and "no Macro Pack" in msgs
    assert "rapport image: assets/dot.gif" in res.body
    assert any("upload assets/dot.gif" in h for h in res.handovers)


def test_drawio_is_always_native(report):
    # not inserted yet: a placeholder and a step for the user (never Macro Pack's draw.io XML)
    res = export(report('::: drawio assets/flow.drawio\n:::\n', files("r2"), name="r2"), APPS)
    assert res.ok and "macro-pack" not in res.body and "rapport draw.io: assets/flow.drawio" in res.body
    assert any("insert assets/flow.drawio" in h and "native draw.io diagram" in h for h in res.handovers)
    # inserted on another page: an embed
    d = report('::: drawio {confluence_page = "7", confluence_name = "x.drawio"}\n:::\n')
    res = export(d, APPS)
    assert res.ok and "static/inc-drawio" in res.body and '"diagramName":"x.drawio"' in res.body
    # inserted on the report's own page: the native macro, revision and size as Confluence stores them
    sync.record(d, {"body": "", "id": "7", "version": 3}, quiet=True)
    (d / "report.md").write_text('::: drawio assets/flow.drawio {confluence_page = "7", confluence_name = "flow.drawio",'
                                 ' confluence_revision = 2, confluence_width = 772, confluence_height = 151}\n:::\n')
    (d / "assets").mkdir()
    (d / "assets/flow.drawio").write_text(FLOW)
    res = export(d, APPS)
    assert res.ok and "static/drawio" in res.body and "inc-drawio" not in res.body
    assert '"revision":2' in res.body and '"width":772,"height":151' in res.body


def test_module_without_confluence_template_is_an_error(report):
    d = report("::: thing\nx\n:::\n", {"reports/r/rapport-modules/thing/module.toml": 'kind = "block"\n',
                                      "reports/r/rapport-modules/thing/template.html": "<div>{{ node.body }}</div>"})
    res = export(d)
    assert any("can't be exported to Confluence" in e.message for e in res.errors)


def test_passthrough_blocks_are_written_verbatim(report):
    jira = ('<div data-type="extension" data-extension-key="jira" data-extension-type="com.atlassian.confluence.macro'
            '.core" data-parameters=\'{"macroParams":{"jqlQuery":{"value":"x"}}}\'></div>')
    anchor = '<span class="annotation" data-annotation-id="a1">kept</span>'
    res = export(report(f"::: confluence Jira\n{jira}\n:::\n\nAn :confluence-inline[kept]{{html = '{anchor}'}}.\n"))
    assert res.ok and jira in res.body and anchor in res.body
    # fetched HTML's highlight nesting (invisible once sent back) is repaired
    hl = '<p><span style="color: #172b4d"><span style="background-color: #d3f1a7">x</span></span></p>'
    res = export(report(f"::: confluence Fetched\n{hl}\n:::\n", name="hl"))
    assert '<span style="background-color: #d3f1a7"><span style="color: #172b4d">x</span></span>' in res.body


# ── outline ─────────────────────────────────────────────────────────────────────── #
def test_published_page_reads_like_its_export(report, tmp_path):
    """The page as Confluence stored it (fetched back after publishing) and the
    export it was made from give the same outline: Confluence's rewrites
    (local IDs, macro IDs, <p> in items and cells, reordered colour spans, block
    cards as divs, card and date texts, thead rows) are noise."""
    live = (FIXTURES / "confluence-live/live.html").read_text(encoding="utf-8")
    d = report((FIXTURES / "confluence-live/report.md").read_text(encoding="utf-8"), name="live")
    res = export(d, APPS)
    assert res.ok, [e.message for e in res.errors]
    assert res.body != live
    assert outline(res.body) == outline(live), render_diff(outline(res.body), outline(live))


def test_outline_lines(report):
    o = outline(export(report(REPORT, files()), APPS).body)
    for line in ["## Text", "{background-color: #d3f1a7; color: #172b4d | marked}; [status green bold: Done] "
                 "[status red subtle: Wait]; due [date 2026-11-01]; owner [@557058:1]; rate `\\(T_2^*\\)`. Prices "
                 "like $5 and snake_case_names stay as they are.", "[x] done task", "  [ ] nested task",
                 "[panel-custom icon=:bulb: color=#eae6ff]", "  Custom panel.", "[card block https://github.com/x]",
                 "| {th w=100} a | {rowspan=2 bg=#deebff w=80} b | {w=80} c |", "[macro-pack plantuml]",
                 "    A -> B", "[image assets/dot.gif"]:
        assert any(line in x for x in o), line
    assert "[panel-note]" in o and "  📐 rapport draw.io: assets/flow.drawio (insert the diagram here)" in o


def test_outline_of_a_real_page(report):
    html = FIXTURE.read_text(encoding="utf-8")
    o = outline(html)
    assert outline("".join(dom_roundtrip(html))) == o
    joined = "\n".join(o)
    for snippet in ["[panel-custom icon=:magnet: color=#e3fcef]", "[extension jira", '[extension drawio-embed {"guestParams":{"diagramDisplayName":"tester.drawio"', "labels: Ada | pi agent writes HTML | MCP gateway HTML to ADF",
                    "[macro-pack mermaid {", "    sequenceDiagram", "[bodied-extension details", "[card embed https://www.youtube.com/watch?v=jNQXAC9IVRw]"]:
        assert snippet in joined, snippet
    assert comments(html) == [("0ded0051-43a1-4c25-969a-0ae89d6b42a0",
                               "Everything should be made as simple as possible, but not simpler.")]
    assert len(joined) < len(html) / 2                       # compact enough to read


def dom_roundtrip(html):
    from rapport_core import dom
    yield dom.serialize(dom.parse(html))


def test_color_around_highlight_survives(report):
    """Confluence uses the innermost text colour, and a highlight carries one (the
    converter workaround): an outer :color must replace it."""
    res = export(report(':color[:highlight[**x**]{color = "yellow"}]{color = "bold-red"}\n'))
    assert '<span style="background-color: #f8e6a0"><span style="color: #bf2600"><strong>x</strong>' in res.body
    assert outline(res.body) == ["{background-color: #f8e6a0; color: #bf2600 | **x**}"]


def test_outline_ignores_mark_order_and_resave_params():
    """Confluence reorders nested marks and colour spans and adds _parentId to
    macros when a page is re-saved; none of that is a change."""
    a = ('<p><u><em><strong>x</strong></em></u> <span style="color: #bf2600"><span style="background-color: #f8e6a0">'
         '<strong>y</strong></span></span></p><div data-type="extension" data-extension-key="toc" '
         'data-parameters=\'{"macroParams":{"maxLevel":{"value":"2"}}}\'></div>')
    b = ('<p><strong><em><u>x</u></em></strong> <strong><span style="background-color: #f8e6a0"><span style="color: '
         '#bf2600">y</span></span></strong></p><div data-type="extension" data-extension-key="toc" data-parameters=\''
         '{"macroParams":{"_parentId":{"value":"1"},"maxLevel":{"value":"2"}}}\'></div>')
    assert outline(a) == outline(b)
    assert outline(a)[0] == "***<u>x</u>*** {background-color: #f8e6a0; color: #bf2600 | **y**}"
    # but a highlight that lost its text colour (invisible text) is a change
    c = a.replace('<span style="color: #bf2600">', '<span style="color: #f8e6a0">')
    assert outline(c) != outline(a)


def test_render_diff_is_word_level():
    a = ["## A", "First paragraph with some words.", "[ ] task", "Last."]
    b = ["## A", "First paragraph with other words.", "[x] task", "Last.", "New one."]
    d = render_diff(a, b)
    assert "@@ ## A @@" in d
    assert "~ First paragraph with [-some-]{+other+} words." in d and "~ [-[ ]-]{+[x]+} task" in d
    assert "+ New one." in d


# ── sync ────────────────────────────────────────────────────────────────────────── #
def published(report, name="r"):
    d = report(REPORT, files(name), name=name)
    body = export(d, APPS).body
    sync.record(d, {"body": body, "id": "42", "version": 1, "title": "Round trip"}, quiet=True)
    return d, body


def edited(body):
    return (body.replace("<p>Body text.</p>", "<p>Body text, <strong>edited in Confluence</strong>.</p>")
            .replace('<input type="checkbox"> open task', '<input type="checkbox" checked> open task')
            .replace("<hr>", "<hr><p>Added in Confluence.</p>", 1))


def test_diff_shows_confluence_edits(report, capsys):
    d, body = published(report)
    sync.diff(d, {"body": body})
    assert "no changes" in capsys.readouterr().out
    sync.diff(d, {"body": edited(body)})
    out = capsys.readouterr().out
    assert "~   Body text{+, **edited in Confluence**+}." in out
    assert "{+[x]+} open task" in out and "+ Added in Confluence." in out
    sync.diff(d, {"body": body}, local=True, sets=APPS)
    assert "report.md: no changes" in capsys.readouterr().out


def test_check_until_the_edits_are_applied(report, capsys):
    d, body = published(report, name="c")
    live = {"body": edited(body)}
    assert sync.check(d, live, APPS) == 1
    out = capsys.readouterr().out
    assert "missing in report.md: Added in Confluence." in out
    p = d / "report.md"
    text = p.read_text()
    text = (text.replace("Body text.", "Body text, **edited in Confluence**.").replace("- [ ] open task", "- [x] open task")
            .replace("---\n", "---\n\nAdded in Confluence.\n", 1)
            .replace("Plain **bold**", "Plain **strong**"))         # an unrelated local edit doesn't matter
    p.write_text(text)
    assert sync.check(d, live, APPS) == 0, capsys.readouterr().out
    # a block removed in Confluence must go locally too
    gone = {"body": body.replace("<p>Hidden.</p>", "")}
    assert sync.check(d, gone, APPS) == 1
    assert "removed in Confluence, still in report.md: Hidden." in capsys.readouterr().out


def anchored(body, text="Body text.", cid="c1"):
    return body.replace(f"<p>{text}</p>", f'<p><span class="annotation" data-annotation-id="{cid}" '
                        f'data-annotation-type="inlineComment">{text}</span></p>')


def test_prepare_refuses_until_confluence_edits_are_in(report, capsys):
    d, body = published(report, name="p")
    live = {"body": edited(body), "id": "42", "version": 2, "title": "Round trip"}
    assert sync.prepare(d, live, APPS) == 1
    out = capsys.readouterr().out
    assert "not publishing" in out and "missing in report.md: Added in Confluence." in out
    assert not (d / ".rapport/confluence/publish.json").exists()
    p = d / "report.md"
    p.write_text(p.read_text().replace("Body text.", "Body text, **edited in Confluence**.")
                 .replace("- [ ] open task", "- [x] open task").replace("---\n", "---\n\nAdded in Confluence.\n", 1))
    assert sync.prepare(d, live, APPS) == 0
    pub = json.loads((d / ".rapport/confluence/publish.json").read_text())
    assert pub["expected_version"] == 2 and pub["page_id"] == "42" and pub["title"] == "Round trip"


def test_prepare_refuses_a_title_changed_in_confluence(report, capsys):
    d, body = published(report, name="t")
    assert sync.prepare(d, {"body": body, "id": "42", "version": 2, "title": "Renamed"}, APPS) == 1
    assert "title changed in Confluence to “Renamed”" in capsys.readouterr().out


def test_prepare_keeps_inline_comments(report, capsys):
    d, body = published(report, name="k")
    live = {"body": anchored(body), "id": "42", "version": 2, "title": "Round trip"}
    assert sync.prepare(d, live, APPS) == 0
    sent = (d / ".rapport/confluence/publish.html").read_text()
    assert ('<p><span class="annotation" data-annotation-id="c1" data-annotation-type="inlineComment">Body text.'
            '</span></p>') in sent
    assert outline(sent) == outline(export(d, APPS).body)          # anchors aren't content
    # the commented text changed locally: refuse, unless the user agreed to let it go
    p = d / "report.md"
    p.write_text(p.read_text().replace("Body text.", "Body copy."))
    capsys.readouterr()
    assert sync.prepare(d, live, APPS) == 1
    assert "c1 on “Body text.”" in capsys.readouterr().out
    assert sync.prepare(d, live, APPS, detach=["c1"]) == 0


def test_carry_comments_matches_by_context():
    live = ('<p>First the model runs.</p><p>Later <span class="annotation" data-annotation-id="c2">the model</span>'
            ' again.</p>')
    # the same words added elsewhere locally don't attract the anchor
    body, lost = sync.carry_comments("<p>Intro: the model is new.</p><p>First the model runs.</p>"
                                     "<p>Later the model again.</p>", live)
    assert not lost and '<p>Later <span class="annotation" data-annotation-id="c2">the model</span> again.</p>' in body
    # two places that fit equally well: don't guess
    live2 = '<p>x one</p><p>x <span data-annotation-id="a" class="annotation">one</span></p>'
    assert sync.carry_comments("<p>x one</p>\n<p>x one</p>", live2)[1] == [("a", "one")]
    # gone, or inside another word: lost
    assert sync.carry_comments("<p>none</p>", live2)[1] == [("a", "one")]
    # already in the body (e.g. copied in a passthrough block): left alone
    kept = '<p>x <span class="annotation" data-annotation-id="a">one</span></p>'
    assert sync.carry_comments(kept, live2) == (kept, [])
    # a non-breaking space on the page matches a plain one
    body, lost = sync.carry_comments("<p>a b</p>", '<p><span data-annotation-id="n">a\xa0b</span></p>')
    assert not lost and 'data-annotation-id="n">a b</span>' in body


def test_carry_comments_same_run_and_real_page():
    live = '<p>one and <span class="annotation" data-annotation-id="b">one</span> more</p>'
    body, lost = sync.carry_comments("<p>one and one more</p>", live)
    assert body == live and not lost
    real = FIXTURE.read_text(encoding="utf-8")
    stripped = re.sub(r'<span class="annotation"[^>]*>(.*?)</span>', r"\1", real)
    assert stripped != real
    body, lost = sync.carry_comments(stripped, real)
    assert body == real and not lost


def test_record_confirms_the_publish(report, capsys):
    d, body = published(report, name="r")
    live = {"body": body, "id": "42", "version": 2, "title": "Round trip"}
    assert sync.prepare(d, live, APPS) == 0
    sent = (d / ".rapport/confluence/publish.html").read_text()
    # someone saved between prepare and publish: version jumped by two
    assert sync.record(d, {"body": sent, "id": "42", "version": 4, "title": "Round trip"}) == 1
    assert "someone saved between `prepare` and the publish" in capsys.readouterr().out
    assert not (d / ".rapport/confluence/publish.json").exists()
    # Confluence stored something else than was sent
    assert sync.prepare(d, {"body": body, "id": "42", "version": 4, "title": "Round trip"}, APPS) == 0
    assert sync.record(d, {"body": body.replace("Hidden.", "Gone."), "id": "42", "version": 5}) == 1
    assert "stored the page differently" in capsys.readouterr().out
    # a clean publish
    (d / "report.md").write_text((d / "report.md").read_text().replace("Hidden.", "Gone."))
    live = {"body": body.replace("Hidden.", "Gone."), "id": "42", "version": 5, "title": "Round trip"}
    assert sync.prepare(d, live, APPS) == 0
    sent = (d / ".rapport/confluence/publish.html").read_text()
    capsys.readouterr()
    assert sync.record(d, {"body": sent, "id": "42", "version": 6, "title": "Round trip"}) == 0
    assert "warning" not in capsys.readouterr().out


def test_diff_shows_versions_and_title(report, capsys):
    d, body = published(report, name="s")
    sync.diff(d, {"body": edited(body), "id": "42", "version": 2, "title": "Renamed"})
    out = capsys.readouterr().out
    assert "last publish version 1, live version 2" in out and "title changed: “Round trip” → “Renamed”" in out


def test_without_a_base_points_to_taking_over(report):
    from rapport_core.util import RapportError
    d = report("x\n")
    with pytest.raises(RapportError, match="Taking over"):
        sync.prepare(d, {"body": "<p>x</p>", "id": "1", "version": 3})


def test_moves_in_confluence_must_be_matched(report, capsys):
    d, body = published(report, name="m")
    swapped = body.replace("<p>Left.</p>", "<p>@@</p>").replace("<p>Right.</p>", "<p>Left.</p>").replace("<p>@@</p>",
                                                                                                         "<p>Right.</p>")
    live = {"body": swapped, "id": "42", "version": 2, "title": "Round trip"}
    assert sync.check(d, live, APPS) == 1
    assert "moved in Confluence: “Right.” now comes before “Left.”" in capsys.readouterr().out
    assert sync.prepare(d, live, APPS) == 1
    p = d / "report.md"
    p.write_text(p.read_text().replace("Left.\n:::\n::: column\nRight.", "Right.\n:::\n::: column\nLeft."))
    assert sync.check(d, live, APPS) == 0


def test_record_guards_the_base(report, capsys):
    from rapport_core.util import RapportError
    d, body = published(report, name="g")
    # another page
    with pytest.raises(RapportError, match="published to page 42, not 99"):
        sync.record(d, {"body": body, "id": "99", "version": 1, "title": "Round trip"}, sets=APPS)
    # edits report.md lacks: recording would bless them
    with pytest.raises(RapportError, match="not recording"):
        sync.record(d, {"body": edited(body), "id": "42", "version": 2, "title": "Round trip"}, sets=APPS)
    assert sync.record(d, {"body": edited(body), "id": "42", "version": 2, "title": "Round trip"}, force=True) == 0
    # a prepared publish that hasn't happened yet
    p = d / "report.md"
    p.write_text(p.read_text().replace("Body text.", "Body text, **edited in Confluence**.")
                 .replace("- [ ] open task", "- [x] open task").replace("---\n", "---\n\nAdded in Confluence.\n", 1))
    live = {"body": edited(body), "id": "42", "version": 2, "title": "Round trip"}
    assert sync.prepare(d, live, APPS) == 0
    with pytest.raises(RapportError, match="still at version 2"):
        sync.record(d, live)


def test_first_publish_is_compared_with_the_export(report, capsys):
    d = report(REPORT, files("f"), name="f")
    body = export(d, APPS).body
    (d / ".rapport/confluence").mkdir(parents=True, exist_ok=True)
    (d / ".rapport/confluence/export.html").write_text(body)
    assert sync.record(d, {"body": body.replace("Hidden.", "Lost."), "id": "7", "version": 1, "title": "T"}) == 1
    assert "stored the page differently from the export" in capsys.readouterr().out


def test_prepare_needs_id_and_version(report):
    from rapport_core.util import RapportError
    d, body = published(report, name="v")
    with pytest.raises(RapportError, match="id and version"):
        sync.prepare(d, {"body": body, "id": "42", "version": None}, APPS)


def test_cli_takes_page_json_only(report, tmp_path, capsys):
    from rapport_core.cli import main
    d = report(REPORT, files("j"), name="j")
    assert main(["export", "confluence", str(d), "--json", "--set", APPS[0]]) == 0
    body = (d / ".rapport/confluence/export.html").read_text()
    page = tmp_path / "page.json"
    page.write_text(json.dumps({"id": "42", "title": "Round trip", "version": {"number": 1}, "body": body}))
    assert main(["confluence", "record", str(page), str(d), "--set", APPS[0]]) == 0
    page.write_text(json.dumps({"id": "42", "title": "Round trip", "version": {"number": 3},
                                "body": body.replace("Body text.", "Edited.")}))
    capsys.readouterr()
    assert main(["confluence", "diff", str(page), str(d)]) == 0
    assert "-   Body text.\n+   Edited." in capsys.readouterr().out
    assert main(["confluence", "check", str(page), str(d), "--set", APPS[0]]) == 1
    (d / "report.md").write_text((d / "report.md").read_text().replace("Body text.", "Edited."))
    assert main(["confluence", "check", str(page), str(d), "--set", APPS[0]]) == 0
    html = tmp_path / "page.html"
    html.write_text(body)
    assert main(["confluence", "diff", str(html), str(d)]) != 0               # plain HTML: refused
    cap = capsys.readouterr()
    assert "save the JSON getConfluencePage returns" in cap.err + cap.out
