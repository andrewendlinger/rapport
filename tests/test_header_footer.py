import re

from conftest import GIF, write
from rapport_core.build import build
from rapport_core.config import load

PLAIN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="#0B6E4F"/></svg>'
MONO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path fill="currentColor" d="M0 0h8v8H0z"/></svg>'


def region(html: str, tag: str) -> str:
    m = re.search(rf"<{tag}\b.*?</{tag}>", html, re.S)
    return m.group(0) if m else ""


def test_logo_icons_and_entries_resolve_from_the_declaring_file(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", """
[modules.header]
logo = "brand/logo.svg"
logo_dark = "brand/logo-dark.svg"
logo_alt = "ACME"
icon = "⭐"

[[modules.header.entries]]
icon = "brand/mono.svg"
href = "https://github.com/acme/platform"
label = "Source on GitHub"

[[modules.footer.entries]]
icon = "🔒"
text = "Confidential"

[[modules.footer.entries]]
icon = "brand/logo.svg"
text = "kept in colour"

[[modules.footer.entries]]
icon = "brand/mono.svg"
text = "untinted"
tint = false

[[modules.footer.entries]]
icon = "brand/dot.gif"
text = "tinted"
tint = true
""")
    d = report("Body.\n", {"brand/logo.svg": PLAIN_SVG, "brand/logo-dark.svg": PLAIN_SVG.replace("0B6E4F", "6FD3A8"),
                           "brand/mono.svg": MONO_SVG, "brand/dot.gif": GIF})
    res = build(d)
    assert res.ok, res.messages()
    header, footer = region(res.html, "header"), region(res.html, "footer")
    assert re.search(r'<img class="rp-light-only" src="data:image/svg\+xml;base64,[^"]+" alt="ACME"', header)
    assert re.search(r'<img class="rp-dark-only" src="data:image/svg\+xml;base64,[^"]+" alt="ACME"', header)
    assert re.search(r'<h1 class="rp-title"><span class="rp-icon" aria-hidden="true" data-rp-ui>⭐</span>', header)
    # an svg drawn in currentColor is tinted; an icon-only link gets its name from label
    assert re.search(r'<a class="rp-entry" href="https://github.com/acme/platform" title="Source on GitHub" '
                     r'aria-label="Source on GitHub"><span class="rp-icon rp-icon-tint" '
                     r'style="--rp-icon:url\(data:image/svg\+xml;base64,[^)]+\)"', header)
    assert '<span class="rp-icon" aria-hidden="true" data-rp-ui>🔒</span><span>Confidential</span>' in footer
    assert re.search(r'<img class="rp-icon" src="data:image/svg\+xml;[^"]+" alt="" data-rp-ui><span>kept in colour', footer)
    assert re.search(r'<img class="rp-icon" src="data:image/svg\+xml;[^"]+" alt="" data-rp-ui><span>untinted', footer)
    assert re.search(r'rp-icon-tint" style="--rp-icon:url\(data:image/gif;base64,[^)]+\)"[^>]*></span><span>tinted',
                     footer)
    assert str(isolated_home) not in res.html                   # inlined, no local paths


def test_front_matter_icon_wins_and_is_relative_to_the_report(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", '[modules.header]\nicon = "⭐"\n')
    d = report('+++\ntitle = "T"\nicon = "assets/mark.gif"\n+++\n\nBody.\n', {"reports/r/assets/mark.gif": GIF})
    res = build(d)
    assert res.ok, res.messages()
    assert re.search(r'<h1 class="rp-title"><img class="rp-icon" src="data:image/gif;base64,[^"]+" alt="" data-rp-ui>T</h1>',
                     res.html)
    assert "⭐" not in region(res.html, "header")


def test_entries_add_up_across_layers_and_merge_by_id(report, isolated_home):
    write(isolated_home / ".config/rapport/config.toml", """
[[modules.footer.entries]]
id = "conf"
icon = "🔒"
text = "Confidential"

[[modules.footer.entries]]
text = "© ACME"
""")
    write(isolated_home / "proj/rapport.toml", """
[[modules.footer.entries]]
id = "conf"
enabled = false

[[modules.footer.entries]]
text = "Platform team"
""")
    d = report("x\n")
    footer = region(build(d).html, "footer")
    assert "Confidential" not in footer and "<span>© ACME</span>" in footer and "<span>Platform team</span>" in footer
    cfg = load(d)                       # entries without an id still say which file added them
    origin = cfg.origin("modules.footer.entries", cfg.module("footer")["entries"])
    assert "user" in origin and "rapport.toml" in origin, origin


def test_unsafe_links_missing_and_remote_icons_are_errors(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", """
[[modules.footer.entries]]
text = "click"
href = "javascript:alert(1)"

[[modules.footer.entries]]
icon = "brand/missing.svg"

[[modules.header.entries]]
icon = "https://example.com/i.svg"
""")
    res = build(report("x\n"))
    msgs = [e.message for e in res.errors]
    assert any(m.startswith("modules.footer.entries: unsafe link 'javascript:alert(1)'") for m in msgs), msgs
    assert any(m.startswith("modules.footer.entries: asset not found:") and "brand/missing.svg" in m for m in msgs), msgs
    assert any(m.startswith("modules.header.entries: remote asset 'https://example.com/i.svg'") for m in msgs), msgs
    assert "javascript:" not in res.html and "example.com" not in res.html


def test_symbols_are_text_not_paths(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", '[[modules.footer.entries]]\nicon = "v1.2"\ntext = "x"\n'
                                               '[[modules.footer.entries]]\nicon = "→"\ntext = "y"\n')
    res = build(report("x\n"))
    assert res.ok, res.messages()
    footer = region(res.html, "footer")
    assert 'data-rp-ui>v1.2</span>' in footer and 'data-rp-ui>→</span>' in footer


def test_footer_parts_can_be_switched_off(report, isolated_home):
    off = ["modules.footer.show_title=false", "modules.footer.show_version=false", "modules.footer.show_date=false"]
    res = build(report("x\n"), off)
    assert res.ok and "<footer" not in res.html                  # nothing left: no empty rule
    write(isolated_home / "proj/rapport.toml", '[[modules.footer.entries]]\ntext = "only this"\n')
    footer = region(build(report("x\n"), off).html, "footer")
    assert "only this" in footer and "built" not in footer and "<span>v1" not in footer and "<span>R</span>" not in footer


def test_logo_height_and_alignment(report, isolated_home):
    write(isolated_home / "proj/logo.svg", PLAIN_SVG)
    write(isolated_home / "proj/rapport.toml", '[modules.footer]\nlogo = "logo.svg"\nlogo_align = "right"\n'
                                               'logo_height = 40\n')
    footer = region(build(report("x\n")).html, "footer")
    assert 'class="rp-logo rp-logo-right"' in footer and 'style="height:calc(40rem/17)"' in footer
    write(isolated_home / "proj/rapport.toml", '[modules.header]\nlogo = "logo.svg"\nlogo_height = "40px"\n')
    res = build(report("x\n"))
    assert res.ok and any("logo_height" in m for m in res.messages()), res.messages()    # wrong type: warned,
    assert 'style="height:calc(32rem/17)"' in region(res.html, "header")       # and the default used


def test_default_header_and_footer_are_unchanged(report):
    html = build(report("x\n")).html
    body = html.split("</style>")[1]
    assert "rp-logo" not in body and "rp-entry" not in body and "rp-icon" not in body
