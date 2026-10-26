"""Confluence-parity modules: palettes, inline marks, layouts, tabs, decisions,
properties, cards, alignment, grid tables, and Confluence panel tones."""

import re

from rapport_core.build import build


def ok(report, text, name="r", sets=None):
    res = build(report(text, name=name), sets or [])
    assert res.ok, res.messages()
    return res.html.split("<main", 1)[1], res.html


def errors(report, text, name="r"):
    return [e.message for e in build(report(text, name=name)).errors]


def test_status_colours_and_styles(report):
    main, _ = ok(report, ':status[Done]{color = "green"} :status[Wait]{color = "red", style = "subtle"} :status[x]\n')
    assert re.search(r'rp-status rp-status-green rp-status-bold" data-rp-module="status">Done', main)
    assert re.search(r'rp-status-red rp-status-subtle" data-rp-module="status">Wait', main)
    assert re.search(r'rp-status-neutral rp-status-bold" data-rp-module="status">x', main)


def test_palette_tokens_only_when_used(report):
    _, page = ok(report, "plain\n")
    assert "--rp-text-bold-red" not in page and "--rp-hl-yellow" not in page
    _, page = ok(report, ':color[a]{color = "bold-red"} :highlight[b]\n', name="r2")
    assert "--rp-text-bold-red:#bf2600" in page and "--rp-hl-yellow:#f8e6a0" in page
    assert "--rp-text-bold-red:#fd9891" in page             # dark value in the dark block
    assert 'style="color:var(--rp-text-bold-red)"' in page and 'style="background:var(--rp-hl-yellow)"' in page
    assert "--rp-bg-light-blue" not in page                 # background palette not needed


def test_palette_token_override(report):
    _, page = ok(report, ":highlight[b]\n", sets=['theme.tokens={"hl-yellow" = "#ffff00"}'])
    assert "--rp-hl-yellow:#ffff00" in page


def test_unknown_palette_colour_is_an_error(report):
    assert any("color" in m for m in errors(report, ':color[a]{color = "neon"}\n'))


def test_sub_sup_attach_to_words_other_inline_modules_do_not(report):
    main, _ = ok(report, "H:sub[2]O and mc:sup[2], but ab:status[x] stays text.\n")
    assert 'H<sub class="rp-m-sub rp-sub" data-rp-module="sub">2</sub>O' in main
    assert "mc<sup" in main and "ab:status[x]" in main


def test_underline(report):
    main, _ = ok(report, "a :underline[b] c\n")
    assert '<u class="rp-m-underline rp-underline" data-rp-module="underline">b</u>' in main


def test_date_formats_and_rejects_non_iso(report):
    main, _ = ok(report, "Due :date[2026-11-01].\n")
    assert '<time class="rp-m-date rp-date" datetime="2026-11-01" data-rp-module="date">Nov 1, 2026</time>' in main
    assert any("ISO date" in m for m in errors(report, "Due :date[next week].\n", name="r2"))


def test_mention(report):
    main, _ = ok(report, ':mention[Ada]{id = "5570:1"}\n')
    assert ">@Ada</span>" in main


def test_columns_layout_and_count(report):
    main, _ = ok(report, '::::columns {layout = "three-equal", divider = true}\n:::column\na\n:::\n'
                         ":::column\nb\n:::\n:::column\nc\n:::\n::::\n")
    assert "rp-columns rp-layout-three-equal rp-columns-divider" in main
    assert main.count('data-rp-module="column"') == 3
    assert any("needs 2 columns" in m for m in errors(report, "::::columns\n:::column\na\n:::\n::::\n", name="r2"))
    assert any("only ::: column" in m for m in errors(report, "::::columns\nloose\n::::\n", name="r3"))


def test_tabs(report):
    main, page = ok(report, "::::tabs\n:::tab One\nfirst\n:::\n:::tab Two\nsecond\n:::\n::::\n")
    assert re.search(r'<button type="button" role="tab" class="rp-tabs-btn rp-on">One</button>'
                     r'<button type="button" role="tab" class="rp-tabs-btn">Two</button>', main)
    assert 'data-rp-label="Two"' in main and 'Rapport.module("tabs"' in page
    assert any("only ::: tab" in m for m in errors(report, "::::tabs\nloose\n::::\n", name="r2"))
    assert any("needs param 'title'" in m for m in errors(report, "::::tabs\n:::tab\nx\n:::\n::::\n", name="r3"))


def test_decisions(report):
    main, _ = ok(report, "::: decisions\n- [x] Ship **it**\n- [ ] Pick a name\n:::\n")
    assert '<li class="rp-decision rp-decided">' in main and "<strong>it</strong>" in main
    assert main.count("rp-decision-icon") == 2
    assert any("decisions line 1" in m for m in errors(report, "::: decisions\njust text\n:::\n", name="r2"))


def test_properties(report):
    main, _ = ok(report, '::: properties {label = "x"}\nOwner = ":mention[Ada]"\nVersion = 3\n:::\n')
    assert '<th scope="row">Owner</th><td><span class="rp-m-mention rp-mention"' in main
    assert '<th scope="row">Version</th><td>3</td>' in main


def test_card(report):
    main, _ = ok(report, '::: card https://www.example.com/a {title = "Ex"}\n:::\n')
    assert 'class="rp-m-card rp-block rp-card rp-card-block" href="https://www.example.com/a"' in main
    assert ">example.com<" in main and ">Ex<" in main
    assert any("http(s) URL" in m for m in errors(report, "::: card ftp://x\n:::\n", name="r2"))


def test_align(report):
    main, _ = ok(report, "::: align right\ntext\n:::\n")
    assert "rp-align rp-align-right" in main
    assert errors(report, "::: align middle\ntext\n:::\n", name="r2")


TABLE = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 |  | 6 |\n"


def test_grid_spans_colours_headers_numbers_widths(report):
    main, page = ok(report, '::: grid {numbered = true, header_column = true, widths = [10, 20, 30], '
                            'cells = {B2 = {rowspan = 2, bg = "light-blue", valign = "middle"}, A1 = {colspan = 2}}}\n'
                            + TABLE + ":::\n")
    assert '<th colspan="2">A</th><th>C</th>' in main                   # B1 covered by A1's colspan
    assert '<td rowspan="2" style="background:var(--rp-bg-light-blue);vertical-align:middle" ' \
           'data-rp-bg="light-blue">2</td>' in main
    assert '<tr><td class="rp-grid-num" data-rp-ui>2</td><th>4</th><td>6</td></tr>' in main   # B3 covered
    assert '<col class="rp-grid-num-col"><col style="width:10px">' in main
    assert "--rp-bg-light-blue:#deebff" in page


def test_grid_without_header_row(report):
    main, _ = ok(report, "::: grid {header_row = false}\n" + TABLE + ":::\n")
    assert "<thead>" not in main and "<td>A</td>" in main


def test_grid_errors(report):
    cases = {
        "cells = {Z9 = {bg = \"blue\"}}": "rows and",
        "cells = {B2 = {bg = \"neon\"}}": "background palette",
        "cells = {A1 = {rowspan = 2}}": "header row",
        "cells = {C2 = {colspan = 2}}": "runs past",
        "widths = [1]": "widths",
        "cells = {B2 = {size = 3}}": "unknown settings",
    }
    for i, (params, msg) in enumerate(cases.items()):
        errs = errors(report, "::: grid {" + params + "}\n" + TABLE + ":::\n", name=f"g{i}")
        assert any(msg in m for m in errs), (params, errs)
    assert any("exactly one" in m for m in errors(report, "::: grid\ntext\n:::\n", name="gx"))


def test_callout_confluence_tones_and_custom(report):
    main, page = ok(report, '::: callout {tone = "error"}\nx\n:::\n\n'
                            '::: callout {tone = "custom", icon = "💡", color = "light-purple"}\ny\n:::\n')
    assert "rp-callout rp-tone-error" in main
    assert 'rp-tone-custom" style="--c-soft:var(--rp-bg-light-purple)"' in main
    assert 'rp-callout-icon" data-rp-ui aria-hidden="true">💡' in main
    assert main.count("rp-callout-title") == 1                          # custom has no tone label
    assert "--rp-bg-light-purple:#eae6ff" in page


def test_light_only_theme_keeps_light_palette(report):
    d = report(":highlight[b]\n", {"reports/r/rapport-modules/flat/module.toml":
                                   'kind = "theme"\n[tokens]\nwidth = "70ch"\nbody-size = "16px"\n'})
    res = build(d, ['theme.name="flat"'])
    assert res.ok, res.messages()
    assert "--rp-hl-yellow:#f8e6a0" in res.html and "#533f04" not in res.html


def test_two_modules_with_one_name_in_one_folder_is_an_error(tmp_path):
    from rapport_core.registry import Registry
    for kind, sub in (("block", "a"), ("theme", "b")):
        d = tmp_path / sub / "same"
        d.mkdir(parents=True)
        (d / "module.toml").write_text(f'name = "same"\nkind = "{kind}"\n')
    reg = Registry([tmp_path])
    assert any("already named 'same'" in e for e in reg.errors)
