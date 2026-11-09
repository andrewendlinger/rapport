"""Phase 2 modules: diagrams and math rendered with local tools, static HTML embeds,
and the helpers behind them (render cache, tool lookup, light/dark display tokens).
Tool-backed tests skip when the tool isn't installed; they share the user's render
cache, so only the first run renders."""

import os
import re
import shutil
from pathlib import Path

import pytest

from rapport_core import external
from rapport_core.build import build

# captured before conftest isolates $HOME: the renderers (draw.io is an Electron app, mmdc drives
# Chrome) need the real home, or macOS asks to create a keychain for them
REAL_HOME = os.path.expanduser("~")
REAL_CACHE = os.environ.get("XDG_CACHE_HOME") or os.path.join(REAL_HOME, ".cache")
HAVE_MMDC = shutil.which("mmdc") is not None
HAVE_DRAWIO = shutil.which("drawio") is not None
HAVE_PLANTUML = shutil.which("plantuml") is not None
HAVE_NODE = shutil.which("node") is not None and shutil.which("npm") is not None
FLOW = (Path(__file__).resolve().parent.parent / "modules/block/drawio/assets/flow.drawio").read_text()


@pytest.fixture
def real_cache(monkeypatch):
    """The user's render cache and home for tool-backed tests (config stays isolated via XDG_CONFIG_HOME)."""
    monkeypatch.setenv("XDG_CACHE_HOME", REAL_CACHE)
    monkeypatch.setenv("HOME", REAL_HOME)
    monkeypatch.delenv("RAPPORT_TOOLS", raising=False)


def test_cached_produces_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    calls = []
    produce = lambda: calls.append(1) or b"svg"          # noqa: E731
    assert external.cached("k", produce) == b"svg" and external.cached("k", produce) == b"svg"
    assert len(calls) == 1 and list((tmp_path / "rapport" / "render").rglob("*"))


def test_tools_off_counts_as_missing(monkeypatch):
    assert external.tool("sh") is None                    # conftest sets RAPPORT_TOOLS=off
    monkeypatch.delenv("RAPPORT_TOOLS")
    assert external.tool("sh")


def test_math_falls_back_to_source_without_tools(report):
    res = build(report("::: latex\nE = mc^2\n:::\n\nInline :math[x^2].\n"))
    assert res.ok, res.messages()
    main = res.html.split("<main", 1)[1]
    assert "E = mc" in main and "<code>x^2</code>" in main
    assert sum("showing the TeX source" in w for w in res.warnings) == 2


def test_run_reports_failures(monkeypatch):
    monkeypatch.delenv("RAPPORT_TOOLS")
    with pytest.raises(external.ToolError, match="exit 3"):
        external.run(["sh", "-c", "echo boom >&2; exit 3"])
    assert external.tool("definitely-not-a-command-xyz") is None and external.tool("sh")


def test_display_tokens_flip_only_with_a_dark_theme(report):
    res = build(report("x\n"))
    assert "--rp-on-light:block;--rp-on-dark:none" in res.html
    assert "--rp-on-light:none;--rp-on-dark:block" in res.html             # default theme has dark mode
    d = report("x\n", {"reports/r2/rapport-modules/flat/module.toml":
                       'kind = "theme"\n[tokens]\nwidth = "70ch"\nbody-size = "16px"\n'}, name="r2")
    html = build(d, ['theme.name="flat"']).html
    assert "--rp-on-dark:none" in html and "--rp-on-dark:block" not in html


def test_missing_renderer_shows_the_source_with_a_warning(report):
    res = build(report("::: plantuml {caption = \"c\"}\nA -> B\n:::\n"),
                ['modules.plantuml.command="no-such-plantuml"'])
    assert res.ok, res.messages()
    assert any("isn't installed" in w for w in res.warnings)
    main = res.html.split("<main", 1)[1]
    assert "rp-diagram-source" in main and "@startuml" in main and "<figcaption>c</figcaption>" in main


def test_an_extends_under_another_name_reads_its_own_config(report):
    d = report("::: flow\nA -> B\n:::\n",
               {"reports/r/rapport-modules/flow/module.toml": 'name = "flow"\nextends = "plantuml"\n'})
    res = build(d, ['modules.flow.command="flow-tool"', 'modules.plantuml.command="plantuml-tool"'])
    assert res.ok, res.messages()
    missing = [str(w) for w in res.warnings if "isn't installed" in str(w)]
    assert len(missing) == 1 and "flow-tool isn't installed" in missing[0]


def test_drawio_file_errors(report):
    assert any("file not found" in e.message for e in build(report("::: drawio assets/nope.drawio\n:::\n")).errors)
    res = build(report("::: drawio https://x.org/a.drawio\n:::\n", name="r2"))
    assert any("self-contained" in e.message for e in res.errors)


@pytest.mark.skipif(not HAVE_DRAWIO, reason="draw.io CLI not installed")
def test_drawio_renders_light_and_dark(report, real_cache):
    res = build(report("::: drawio assets/flow.drawio {caption = \"Flow\", width = 60}\n:::\n",
                       {"reports/r/assets/flow.drawio": FLOW}))
    assert res.ok, res.messages()
    main = res.html.split("<main", 1)[1]
    imgs = re.findall(r'<img class="rp-diagram-(light|dark)" src="data:image/svg\+xml;base64,', main)
    assert imgs == ["light", "dark"] and 'style="--dia-w:60%"' in main


@pytest.mark.skipif(not HAVE_MMDC, reason="mmdc not installed")
def test_mermaid_renders_light_and_dark(report, real_cache):
    res = build(report("::: mermaid\nflowchart LR\n  A --> B\n:::\n"))
    assert res.ok, res.messages()
    assert len(re.findall(r'<img class="rp-diagram-(?:light|dark)" src="data:image/svg\+xml', res.html)) == 2


@pytest.mark.skipif(not HAVE_PLANTUML, reason="plantuml not installed")
def test_plantuml_renders_light_and_dark_transparent(report, real_cache):
    res = build(report("::: plantuml\nAlice -> Bob: hi\n:::\n"))        # @startuml added when missing
    assert res.ok, res.messages()
    srcs = re.findall(r'<img class="rp-diagram-(?:light|dark)" src="data:image/svg\+xml;base64,([^"]+)"', res.html)
    assert len(srcs) == 2
    import base64
    assert all("background:#" not in base64.b64decode(s).decode() for s in srcs)


@pytest.mark.skipif(not HAVE_NODE, reason="node/npm not installed")
def test_latex_and_inline_math(report, real_cache):
    res = build(report("::: latex {caption = \"Signal\"}\n$$E = mc^2$$\n:::\n\nRate :math[T_2^*] here.\n"))
    assert res.ok, res.messages()
    main = res.html.split("<main", 1)[1]
    assert re.search(r'<figure class="rp-m-latex rp-block rp-latex" title="E = mc\^2"[^>]*>\s*'
                     r'<div class="rp-latex-math"><svg', main)
    assert re.search(r'<span class="rp-m-math rp-math" title="T_2\^\*" data-rp-module="math"><svg', main)
    assert 'fill="currentColor"' in main or "currentColor" in main


@pytest.mark.skipif(not HAVE_NODE, reason="node/npm not installed")
def test_latex_error_is_a_build_error(report, real_cache):
    res = build(report("::: latex\n\\frac{a\n:::\n"))
    assert any("TeX error" in e.message or "latex" in e.message for e in res.errors)


def test_html_embed_is_static_isolated_and_self_contained(report):
    from conftest import GIF
    text = ("::: html-embed\n<style>.k{color:red}</style>\n<div class=\"k\" onclick=\"x()\">Hi</div>\n"
            "<script>alert(1)</script><a href=\"javascript:x()\">l</a><img src=\"assets/a.gif\">\n:::\n")
    res = build(report(text, {"reports/r/assets/a.gif": GIF}))
    assert res.ok, res.messages()
    main = res.html.split("<main", 1)[1]
    assert '<template shadowrootmode="open"><style>.k{color:red}</style>' in main
    assert "onclick" not in main and "alert(1)" not in main and "javascript:" not in main
    assert 'src="data:image/gif;base64,' in main
    assert any("removed 3" in w for w in res.warnings)
    res2 = build(report("::: html-embed\n<img src=\"https://x.org/a.png\">\n:::\n", name="r2"))
    assert any("remote resource" in e.message for e in res2.errors)


@pytest.mark.parametrize("html", [
    "<img src=https://x.org/a.png>",
    '<img src="assets/a.gif" srcset="assets/a.gif 1x, https://x.org/b.png 2x">',
    '<video poster="//x.org/p.png"></video>',
    '<svg><image href="https://x.org/i.png"/></svg>',
    "<svg><use xlink:href='https://x.org/s.svg#i'/></svg>",
    '<style>@import "https://x.org/a.css";</style>',
])
def test_html_embed_refuses_every_remote_load(report, html):
    from conftest import GIF
    res = build(report(f"::: html-embed\n{html}\n:::\n", {"reports/r/assets/a.gif": GIF}))
    assert not res.ok and "x.org" not in res.html.split("<main", 1)[1]


def test_html_embed_inlines_srcset_and_svg_images_but_keeps_links(report):
    from conftest import GIF
    res = build(report('::: html-embed\n<img srcset="assets/a.gif 1x, assets/a.gif 2x">'
                       '<svg><image href="assets/a.gif"/></svg><a href="https://x.org/">x</a>\n:::\n',
                       {"reports/r/assets/a.gif": GIF}))
    assert res.ok, res.messages()
    main = res.html.split("<main", 1)[1]
    assert main.count("data:image/gif;base64,") == 3 and 'href="https://x.org/"' in main
