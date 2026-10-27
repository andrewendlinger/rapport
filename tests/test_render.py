import json
import re

from conftest import GIF, write
from rapport_core.build import build


def test_anchor_attributes_and_classes(report):
    res = build(report("## Head\n\nA paragraph.\n\n::: callout\nNote.\n:::\n"))
    assert res.ok, res.messages()
    html = res.html
    assert re.search(r'<p class="rp-m-paragraph rp-block" data-rp-id="paragraph-[0-9a-f]{6}" '
                     r'data-rp-module="paragraph" data-rp-lines="3-3">A paragraph.</p>', html)
    assert 'class="rp-m-callout rp-block rp-callout rp-tone-note"' in html
    assert res.blocks and all({"lines", "src", "module"} <= set(b) for b in res.blocks.values())


def test_gif_is_inlined(report, isolated_home):
    d = report("![Demo](assets/demo.gif)\n", {"reports/r/assets/demo.gif": GIF})
    res = build(d)
    assert res.ok, res.messages()
    assert 'src="data:image/gif;base64,' in res.html
    assert "<figcaption>Demo</figcaption>" in res.html


def test_remote_and_missing_assets_are_errors(report):
    res = build(report("![x](https://example.com/a.png)\n\n![y](assets/missing.png)\n"))
    msgs = [e.message for e in res.errors]
    assert any("remote asset" in m for m in msgs) and any("asset not found" in m for m in msgs)
    assert "https://example.com" not in res.html.split("<main")[1]


def test_inline_budget(report):
    d = report("![a](assets/a.gif)\n", {"reports/r/assets/a.gif": GIF * 1000})
    res = build(d, ["report.max_inline_mb=0.01"])
    assert any("max_inline_mb" in e.message for e in res.errors)


def test_raw_html_escaped_by_default(report):
    res = build(report("<div onclick=x>raw</div>\n"))
    assert "<div onclick=x>" not in res.html and "&lt;div onclick=x&gt;" in res.html
    res2 = build(report("<div class=k>raw</div>\n", name="r2"), ["report.raw_html=true"])
    assert "<div class=k>raw</div>" in res2.html and 'data-rp-module="html"' in res2.html


def test_css_and_js_only_for_used_modules(report):
    html = build(report("plain\n")).html
    assert "/* compare */" not in html and "/* collapse */" not in html
    html2 = build(report("::: collapse T\nx\n:::\n", name="r2")).html
    assert "/* collapse */" in html2 and "Rapport.module(\"collapse\"" in html2


def test_static_vs_served_features(report):
    res = build(report("x\n"), serve_data={"serve": {"token": "t", "api": "/api", "build_id": 1}})
    assert 'Rapport.module("feedback"' not in res.html and 'Rapport.module("static-hint"' not in res.html
    hinted = build(report("x\n", name="r2"), ["modules.static-hint.enabled=true"],
                   serve_data={"serve": {"token": "t", "api": "/api", "build_id": 1}})
    assert 'Rapport.module("static-hint"' in hinted.html and 'Rapport.module("static-hint"' not in hinted.served_html
    assert 'Rapport.module("feedback"' in res.served_html and 'Rapport.module("live-reload"' in res.served_html
    assert '"token": "t"' in res.served_html and '"token"' not in res.html


def test_feature_can_be_disabled(report):
    html = build(report("x\n"), ["modules.theme-toggle.enabled=false"]).html
    assert "rp-theme-toggle" not in html.split("</style>")[1]


def test_theme_tokens_and_dark(report):
    html = build(report("x\n"), ['theme.tokens.accent="#123456"', "theme.name=paper"]).html
    assert "--rp-accent:#123456;" in html and "--rp-body-font:var(--rp-serif)" in html
    assert ':root[data-theme="dark"]' in html


def test_layout_regions_configurable(report):
    html = build(report("## A\n\nx\n\n## B\n\ny\n\n## C\n\nz\n"), ["layout.side=[]", "layout.bottom=[]"]).html
    body = html.split("</style>")[1]
    assert '<body class="rp-page">' in body and "rp-footer" not in body and "rp-toc" not in body


def test_strict_mode_flags_multi_root_templates(report, isolated_home):
    mdir = isolated_home / "proj/reports/r/rapport-modules/two"
    write(mdir / "module.toml", 'kind = "block"\nsummary = "two roots"\n')
    write(mdir / "template.html", "<p>a</p><p>b</p>")
    res = build(report("::: two\nx\n:::\n"), strict=True)
    assert any("2 root" in w for w in res.warnings)


def test_override_builtin_by_name(report, isolated_home):
    mdir = isolated_home / "proj/rapport-modules/callout"
    write(isolated_home / "proj/rapport.toml", "")
    write(mdir / "module.toml", 'kind = "block"\nsummary = "mine"\npositional = "title"\n'
                                '[params.title]\ntype = "string"\ndefault = ""\n')
    write(mdir / "template.html", '<section class="mine">{{ node.body }}</section>')
    res = build(report("::: callout\nx\n:::\n"))
    assert res.ok, res.messages()
    assert '<section class="rp-m-callout rp-block mine"' in res.html
    assert res.config.registry.get("callout").shadows      # built-in recorded as shadowed


# ── regressions from the review ────────────────────────────────────────────── #
def test_leading_h1_is_the_title_in_both_renders(report):
    res = build(report("# My Real Title\n\nIntro paragraph.\n"), serve_data={"serve": {"token": "t"}})
    for page in (res.html, res.served_html):
        assert "<title>My Real Title</title>" in page
        assert '<h1 class="rp-title">My Real Title</h1>' in page
        assert page.count("My Real Title</h1>") == 1


def test_each_image_counts_once_toward_the_budget(report):
    d = report("![Demo](assets/demo.gif)\n", {"reports/r/assets/demo.gif": GIF})
    budget = len(GIF) * 1.5 / 1048576
    res = build(d, [f"report.max_inline_mb={budget}"])
    assert res.ok, res.messages()


def test_asset_names_with_spaces(report):
    d = report("![Demo](<assets/my demo.gif>)\n", {"reports/r/assets/my demo.gif": GIF})
    res = build(d)
    assert res.ok, res.messages()
    assert 'src="data:image/gif;base64,' in res.html


def test_switched_off_modules(report, isolated_home):
    d = report("::: callout\nNote.\n:::\n\nShipped :badge[yes].\n\n```python\nx = 1\n```\n")
    write(isolated_home / "proj/rapport.toml",
          "[modules.callout]\nenabled = false\n[modules.badge]\nenabled = false\n[modules.code]\nenabled = false\n")
    res = build(d)
    msgs = [e.message for e in res.errors]
    assert any("module 'callout' is switched off" in m for m in msgs)
    assert any("inline module 'badge' is switched off" in m for m in msgs)
    main = res.html.split("<main")[1].split("</main>")[0]
    # a switched-off element falls back to plain Markdown HTML, still commentable
    assert 'data-rp-module="code"' in main and '<code class="language-python">x = 1' in main
    assert '<figure class="rp-code">' not in main


def test_template_may_start_with_a_comment(report):
    d = report("::: boxed\nInside.\n:::\n", {
        "reports/r/rapport-modules/boxed/module.toml": 'name = "boxed"\nkind = "block"\nsummary = "A box."\n',
        "reports/r/rapport-modules/boxed/template.html": '<!-- the box -->\n<div class="box">{{ node.body }}</div>\n',
    })
    res = build(d)
    assert res.ok, res.messages()
    assert re.search(r'<div class="rp-m-boxed rp-block box" data-rp-id="boxed-', res.html)


def test_report_html_has_no_local_paths(report):
    d = report("Hello.\n")
    res = build(d)
    assert str(d) not in res.html and "/bin/rapport" not in res.html


def test_generated_labels_are_chrome(report):
    res = build(report('::: callout\nBody.\n:::\n\n::: compare {labels = ["Before", "After"]}\n'
                       "```python\nold()\n```\n```python\nnew()\n```\n:::\n\n:::: compare\n"
                       "::: pane Old approach\nLeft.\n:::\n::: pane New\nRight.\n:::\n::::\n"))
    assert res.ok, res.messages()
    assert '<p class="rp-callout-title" data-rp-ui>Note</p>' in res.html
    assert '<div class="rp-pane" data-rp-label="Before"><div class="rp-pane-label" data-rp-ui>Before</div>' in res.html
    assert 'data-rp-label="Old approach"' in res.html


def test_page_data_has_no_angle_brackets(report):
    title = "a <!-- b <script> c </script>"
    html = build(report(f'+++\ntitle = "{title}"\n+++\n\nx\n')).html
    data = html.split('<script type="application/json" id="rp-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in data and json.loads(data)["report"]["title"] == title


def test_a_prepare_py_that_fails_to_load_fails_the_build(report, isolated_home):
    mdir = isolated_home / "proj/reports/r/rapport-modules/note"
    write(mdir / "module.toml", 'kind = "block"\nsummary = "s"\n')
    write(mdir / "template.html", "<div>{{ extra }}{{ node.body }}</div>")
    write(mdir / "prepare.py", "def prepare(node, ctx:\n    return {'extra': 'x'}\n")
    res = build(report("::: note\nx\n:::\n\n::: note\ny\n:::\n"))
    assert not res.ok
    errors = [e.message for e in res.errors if "prepare.py failed to load: SyntaxError" in e.message]
    assert len(errors) == 1, res.messages()                          # once, not once per use
    assert any("modules.note: prepare.py failed to load" in w for w in res.warnings)


def test_a_containers_own_errors_are_on_its_opening_line(report, isolated_home):
    mdir = isolated_home / "proj/reports/r/rapport-modules/two"
    write(mdir / "module.toml", 'kind = "block"\nsummary = "two roots"\n')
    write(mdir / "template.html", "<p>{{ node.body }}</p><p>{{ 1 // 0 }}</p>")
    res = build(report("Intro.\n\n::: two\nfirst child\n\nsecond child\n:::\n"), strict=True)
    assert [e.line for e in res.errors if "failed to render" in e.message] == [3], res.messages()


def test_order_orders_scripts_too(report, isolated_home):
    # alphabetical order is "a-late" < "z-early"; their `order` values reverse it
    for name, order in (("a-late", 90), ("z-early", 10)):
        mdir = isolated_home / f"proj/reports/r/rapport-modules/{name}"
        write(mdir / "module.toml", f'kind = "feature"\nsummary = "s"\norder = {order}\n')
        write(mdir / "script.js", f'Rapport.module("{name}", {{}});\n')
    res = build(report("x\n"))
    assert res.ok, res.messages()
    scripts = re.findall(r"<script>/\* ([\w-]+) \*/", res.html)
    assert scripts[0] == "runtime"                                    # the kernel stays first
    assert scripts.index("z-early") < scripts.index("a-late")
