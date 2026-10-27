"""prepare.py: prepare(node, ctx) for every kind that renders a template, and the css(ctx) hook."""

import json
import re

from conftest import write
from rapport_core.build import build
from rapport_core.render import pygments_css

FONT = b"wOF2" + b"\0" * 60
# a template for anything: the variable prepare() returns, and nothing else
SEEN = '<div class="rp-seen">{{ seen }}</div>\n'


def module(isolated_home, name, kind, files, toml="", where="proj/reports/r/rapport-modules"):
    """A module in the report's own rapport-modules/ (or `where`): a module.toml of kind and toml, plus files."""
    d = isolated_home / where / name
    write(d / "module.toml", f'kind = "{kind}"\nsummary = "s"\n{toml}')
    for rel, content in files.items():
        if isinstance(content, bytes):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_bytes(content)
        else:
            write(d / rel, content)
    return d


def style(html):
    return html.split("<style>\n", 1)[1].split("\n</style>", 1)[0]


# ── prepare() reaches every kind's template ─────────────────────────────────── #
def test_inline_prepare_vars_reach_the_template(report, isolated_home):
    module(isolated_home, "loud", "inline", {
        "template.html": '<b class="rp-loud">{{ shout }}</b>\n',
        "prepare.py": "def prepare(node, ctx):\n"
                      "    return {'shout': f'{node.text.upper()}-{node.params[\"n\"]}-{node.body}'}\n"},
        '[params.n]\ntype = "int"\ndefault = 1\n')
    res = build(report("Say :loud[hello]{n = 3} now.\n"))
    assert res.ok, res.messages()
    assert ">HELLO-3-hello<" in res.html


def test_layout_region_prepare_gets_the_region_and_its_name(report, isolated_home):
    module(isolated_home, "stamp", "layout", {
        "template.html": SEEN,
        "prepare.py": "def prepare(node, ctx):\n"
                      "    return {'seen': f\"{node.module}|{node.id}|{len(node.params)}|{node.attrs['region']}\"}\n"})
    res = build(report("x\n"), ['layout.top=["stamp"]', 'layout.bottom=["stamp"]'])
    assert res.ok, res.messages()
    assert ">stamp|stamp|0|top<" in res.html and ">stamp|stamp|0|bottom<" in res.html


def test_page_prepare_vars_reach_the_page_template(report, isolated_home):
    module(isolated_home, "page", "layout", {
        "template.html": "<!doctype html><html><head><style>\n{{ css }}\n</style></head>"
                         "<body>{{ seen }}{{ content }}</body></html>\n",
        "prepare.py": "def prepare(node, ctx):\n    return {'seen': f'[{node.module}|{node.id}|{node.params}|{node.attrs}]'}\n"},
        'extends = "page"\n')
    res = build(report("x\n"))
    assert res.ok, res.messages()
    assert "<body>[page||{}|{}]" in res.html


def test_feature_prepare_vars_reach_the_template(report, isolated_home):
    module(isolated_home, "stats", "feature", {
        "template.html": SEEN,
        "prepare.py": "def prepare(node, ctx):\n    return {'seen': f'{node.module}|{node.id}|{ctx.meta[\"title\"]}'}\n"})
    res = build(report("# Title\n\nx\n"))
    assert res.ok, res.messages()
    assert ">stats||Title<" in res.html


def test_computed_data_reaches_script_js_as_json_in_the_template(report, isolated_home):
    stats = {"label": "</script><b>&'", "rows": [1, 2]}
    module(isolated_home, "stats", "feature", {
        "template.html": '<script type="application/json" class="rp-stats-data">{{ stats|tojson }}</script>\n',
        "prepare.py": f"def prepare(node, ctx):\n    return {{'stats': {stats!r}}}\n",
        "script.js": 'Rapport.module("stats", {init: function () {\n'
                     '  var data = JSON.parse(document.querySelector(".rp-stats-data").textContent);\n}});\n'})
    res = build(report("x\n"))
    assert res.ok, res.messages()
    m = re.search(r'<script type="application/json" class="rp-stats-data">(.*?)</script>', res.html)
    assert json.loads(m.group(1)) == stats          # one script element: nothing in the data ended it
    assert "<b>" not in m.group(1)


def test_a_failing_prepare_is_a_build_error_naming_the_module_in_every_kind(report, isolated_home):
    boom = "def prepare(node, ctx):\n    raise ValueError('nope')\n"
    module(isolated_home, "shout", "inline", {"template.html": "<b>x</b>\n", "prepare.py": boom})
    module(isolated_home, "stamp", "layout", {"template.html": "<div>x</div>\n", "prepare.py": boom})
    module(isolated_home, "stats", "feature", {"template.html": "<div>x</div>\n", "prepare.py": boom})
    res = build(report("A :shout[b].\n"), ['layout.top=["stamp"]'])
    msgs = [e.message for e in res.errors]
    for name in ("shout", "stamp", "stats"):
        assert f"module '{name}' prepare.py failed: nope" in msgs, res.messages()
    assert 'data-rp-module="shout"' in res.html          # the build went on without the module's variables


def test_a_prepare_py_that_fails_to_load_is_a_build_error_in_every_kind(report, isolated_home):
    broken = "def prepare(node, ctx:\n    return {}\n"
    module(isolated_home, "shout", "inline", {"template.html": "<b>x</b>\n", "prepare.py": broken})
    module(isolated_home, "stamp", "layout", {"template.html": "<div>x</div>\n", "prepare.py": broken})
    module(isolated_home, "stats", "feature", {"template.html": "<div>x</div>\n", "prepare.py": broken})
    res = build(report("A :shout[b].\n"), ['layout.top=["stamp"]'])
    msgs = [e.message for e in res.errors]
    for name in ("shout", "stamp", "stats"):
        assert any(m.startswith(f"module '{name}' prepare.py failed to load: SyntaxError") for m in msgs), res.messages()


# ── css(ctx) ───────────────────────────────────────────────────────────────── #
def test_css_hook_follows_style_css_in_the_modules_layer(report, isolated_home):
    module(isolated_home, "note", "block", {
        "template.html": "<div>{{ node.body }}</div>\n",
        "style.css": ".rp-note{margin:0}\n",
        "prepare.py": "def css(ctx):\n    return '.rp-note-' + ctx.module('note')['tone'] + '{color:var(--rp-ink)}'\n"},
        '[config.tone]\ntype = "string"\ndefault = "warm"\n')
    res = build(report("::: note\nx\n:::\n"), ["modules.note.tone=\"cool\""])
    assert res.ok, res.messages()
    css = style(res.html)
    chunk = "/* note */\n.rp-note{margin:0}\n\n.rp-note-cool{color:var(--rp-ink)}"
    assert chunk in css and css.index("@layer rp-modules{") < css.index(chunk)
    assert css.index(".rp-note-cool") < css.index("/* print */")        # its own chunk: before the next module's


def test_css_hook_runs_once_per_page_and_only_for_modules_on_it(report, isolated_home):
    module(isolated_home, "note", "block", {
        "template.html": "<div>x</div>\n",
        "prepare.py": "CALLS = []\n\ndef css(ctx):\n    CALLS.append(1)\n    return '.rp-note-hook{color:red}'\n"})
    res = build(report("::: note\na\n:::\n\n::: note\nb\n:::\n"))
    assert res.ok, res.messages()
    assert res.html.count(".rp-note-hook{") == 1
    assert ".rp-note-hook" not in build(report("plain\n", name="r2")).html


def test_a_themes_css_hook_goes_in_the_theme_layer(report, isolated_home):
    module(isolated_home, "brand", "theme", {
        "style.css": ".brand-base{margin:0}\n",
        "prepare.py": "def css(ctx):\n    return '.brand-hook{margin:1px}'\n"},
        'extends = "default"\n')
    res = build(report("x\n"), ['theme.name="brand"'])
    assert res.ok, res.messages()
    css = style(res.html)
    assert css.index(".brand-base{margin:0}\n\n.brand-hook{margin:1px}") < css.index("@layer rp-modules{")


def test_css_hook_urls_resolve_beside_the_prepare_py_that_defines_them(report, isolated_home):
    module(isolated_home, "frame", "block", {
        "template.html": "<div>{{ node.body }}</div>\n",
        "fonts/Frame.woff2": FONT,
        "prepare.py": "def css(ctx):\n    return '@font-face{font-family:Frame;src:url(fonts/Frame.woff2)}'\n"},
        where="proj/rapport-modules")
    write(isolated_home / "proj/rapport.toml", "")
    d = report("::: frame\nx\n:::\n")
    res = build(d)
    assert res.ok, res.messages()
    assert 'src:url("data:font/woff2;base64,' in style(res.html) and "fonts/Frame.woff2" not in res.html
    # an override that adds only a style.css inherits the hook, and the hook still finds its font
    module(isolated_home, "frame", "block", {"style.css": ".rp-frame{margin:0}\n"}, 'extends = "frame"\n')
    res = build(d)
    assert res.ok, res.messages()
    css = style(res.html)
    assert css.index(".rp-frame{margin:0}") < css.index("@font-face{font-family:Frame") and "fonts/Frame.woff2" not in css


def test_css_hook_problems_are_build_errors(report, isolated_home):
    module(isolated_home, "remote", "block", {
        "template.html": "<div>x</div>\n",
        "prepare.py": "def css(ctx):\n    return '.a{background:url(https://example.com/a.png)}'\n"})
    module(isolated_home, "broken", "block", {
        "template.html": "<div>x</div>\n",
        "prepare.py": "def css(ctx):\n    raise ValueError('nope')\n"})
    res = build(report("::: remote\nx\n:::\n\n::: broken\ny\n:::\n"))
    msgs = [e.message for e in res.errors]
    assert any(m.startswith("module 'remote' css():1: remote url(https://example.com/a.png)") for m in msgs), msgs
    assert "module 'broken' prepare.py css() failed: nope" in msgs
    assert "example.com" not in res.html


# ── the code module owns its Pygments CSS ───────────────────────────────────── #
def test_code_blocks_bring_their_pygments_css_with_the_code_module(report):
    res = build(report("```python\nx = 1\n```\n"))
    assert res.ok, res.messages()
    css = style(res.html)
    pg = pygments_css("friendly", "github-dark")
    assert css.count(pg) == 1 and css.index("@layer rp-modules{") < css.index("/* code */") < css.index(pg)
    assert css.index(".rp-pre{") < css.index(pg)                         # after the code module's style.css
    assert ".rp-code .k" not in build(report("plain\n", name="r2")).html          # no code, no Pygments rules


def test_code_css_follows_the_modules_styles(report):
    res = build(report("```python\nx = 1\n```\n"), ["modules.code.light_style=monokai", "modules.code.dark_style=dracula"])
    assert pygments_css("monokai", "dracula") in res.html


def test_code_css_is_there_when_only_ctx_highlight_was_used(report, isolated_home):
    module(isolated_home, "snip", "block", {
        "template.html": "<pre>{{ code }}</pre>\n",
        "prepare.py": "def prepare(node, ctx):\n    return {'code': ctx.highlight('x = 1', 'python')}\n"})
    res = build(report("::: snip\nx\n:::\n"))
    assert res.ok, res.messages()
    assert 'class="rp-line"' in res.html and pygments_css("friendly", "github-dark") in res.html


def test_code_css_is_there_when_a_non_block_module_used_ctx_highlight(report, isolated_home):
    module(isolated_home, "stats", "feature", {
        "template.html": "<pre>{{ code }}</pre>\n",
        "prepare.py": "def prepare(node, ctx):\n    return {'code': ctx.highlight('x = 1', 'python')}\n"})
    res = build(report("plain\n"))
    assert res.ok, res.messages()
    assert pygments_css("friendly", "github-dark") in res.html


def test_an_override_of_code_inherits_the_css_hook(report, isolated_home):
    module(isolated_home, "code", "element", {"style.css": ".rp-code{margin:0}\n"}, 'extends = "code"\n')
    res = build(report("```python\nx = 1\n```\n"))
    assert res.ok, res.messages()
    css = style(res.html)
    assert css.index(".rp-code{margin:0}") < css.index(pygments_css("friendly", "github-dark"))
