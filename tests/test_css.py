"""Styling: cascade layers, [theme] css, url() inlining, theme checks."""

import base64
import threading
import time

from conftest import GIF, write
from rapport_core import config as config_mod
from rapport_core.build import build
from rapport_core.cli import main
from rapport_core.render import _script_safe

FONT = b"wOF2" + b"\0" * 60
BRAND = '+++\n[theme]\ncss = ["brand.css"]\n+++\n\nx\n'


def style(html):
    return html.split("<style>\n", 1)[1].split("\n</style>", 1)[0]


def themed(report, css, files=None, sets=None):
    """Build a report whose front matter lists brand.css; returns (result, brand.css as inlined)."""
    res = build(report(BRAND, {"reports/r/brand.css": css, **(files or {})}), sets)
    return res, style(res.html).split("/* theme.css: brand.css */\n", 1)[1]


def test_rapport_css_is_layered_and_theme_css_follows_unlayered(report):
    d = report('+++\n[theme]\ncss = ["brand.css"]\n+++\n\n::: callout\nx\n:::\n',
               {"reports/r/brand.css": "h2{color:rgb(1,2,3)}\n"})
    res = build(d)
    assert res.ok, res.messages()
    css = style(res.html)
    assert css.startswith("@layer rp-theme,rp-modules;\n@layer rp-theme{")
    assert css.index("--rp-accent:") < css.index("@layer rp-modules{") < css.index("/* callout */")
    i = css.index("/* theme.css: brand.css */")
    assert css[:i].count("{") == css[:i].count("}")         # outside every layer block
    assert "h2{color:rgb(1,2,3)}" in css[i:] and "@layer" not in css[i:]


def test_theme_css_appends_nearest_last_relative_to_each_file(report, isolated_home):
    home = isolated_home.resolve()
    write(home / ".config/rapport/config.toml", '[theme]\ncss = ["u.css"]\n')
    write(home / "proj/rapport.toml", '[theme]\ncss = ["p.css"]\n')
    d = report('+++\n[theme]\ncss = ["r.css"]\n+++\n\nx\n',
               {"reports/r/r.css": ".r{}", "p.css": ".p{}", str(home / ".config/rapport/u.css"): ".u{}"})
    cfg, _ = config_mod.load_for_report(d)
    assert cfg.get("theme.css") == [str(home / ".config/rapport/u.css"), str(home / "proj/p.css"),
                                    str(home / "proj/reports/r/r.css")]
    css = style(build(d).html)
    assert css.index(".u{}") < css.index(".p{}") < css.index(".r{}")


def test_same_file_from_two_layers_appears_once_at_its_first_position(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", '[theme]\ncss = ["shared.css", "p.css"]\n')
    d = report('+++\n[theme]\ncss = ["../../shared.css"]\n+++\n\nx\n', {"shared.css": ".s{}", "p.css": ".p{}"})
    cfg, _ = config_mod.load_for_report(d)
    proj = isolated_home.resolve() / "proj"
    assert cfg.get("theme.css") == [str(proj / "shared.css"), str(proj / "p.css")]
    assert style(build(d).html).count("/* theme.css: shared.css */") == 1


def test_empty_entry_resets_append_lists(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", '[theme]\ncss = ["p.css"]\n[writing]\navoid = ["synergy"]\n')
    d = report('+++\n[theme]\ncss = ["", "r.css"]\n[writing]\navoid = ["", "very"]\n+++\n\nx\n',
               {"reports/r/r.css": ".r{}", "p.css": ".p{}"})
    cfg, _ = config_mod.load_for_report(d)
    assert cfg.get("theme.css") == [str(d.resolve() / "r.css")] and cfg.origins["theme.css"] == "front matter"
    assert cfg.get("writing.avoid") == ["very"]            # built-in defaults dropped too
    assert ".p{}" not in style(build(d).html)


def test_set_theme_css_takes_a_plain_string(report, monkeypatch):
    d = report("x\n", {"reports/r/x.css": ".x{}"}).resolve()
    monkeypatch.chdir(d)                                    # --set paths are relative to the cwd
    res = build(d, ["theme.css=x.css"])
    assert res.ok, res.messages()
    assert res.config.get("theme.css") == [str(d / "x.css")] and ".x{}" in style(res.html)


def test_theme_css_must_be_an_existing_css_file(report):
    d = report('+++\n[theme]\ncss = ["notes.txt", "gone.css"]\n+++\n\nx\n', {"reports/r/notes.txt": "p{}"}).resolve()
    msgs = build(d).messages()
    assert f"error: report.md: theme.css: {d / 'notes.txt'} isn't a .css file" in msgs
    assert f"error: report.md: theme.css: file not found: {d / 'gone.css'}" in msgs


def test_url_inlines_fonts_and_images(report):
    res, css = themed(report, '@font-face{font-family:Brand;src:url(fonts/Brand.woff2) format("woff2")}\n'
                              ".a{background:URL( 'img/dot.gif' )}\n.b{background:url(\"img/my%20dot.png\")}\n",
                      {"reports/r/fonts/Brand.woff2": FONT, "reports/r/img/dot.gif": GIF,
                       "reports/r/img/my dot.png": GIF})
    assert res.ok, res.messages()
    assert f'src:url("data:font/woff2;base64,{base64.b64encode(FONT).decode()}") format("woff2")' in css
    assert '.a{background:url("data:image/gif;base64,' in css and '.b{background:url("data:image/png;base64,' in css
    assert {str(res.config.report_dir / "fonts/Brand.woff2")} <= set(map(str, res.assets))


def test_url_leaves_strings_comments_data_and_ids_alone(report):
    keep = ('/* url(gone.png) */\n.a{content:"url(gone.png)"}\n'
            '.b{background:url("data:image/svg+xml,<svg fill=\'%23fff\'>)</svg>")}\n'
            '.c{mask:url(#m)}\n@font-face{font-family:B;src:local("Brand"),local(Brand-Bold)}\n')
    res, css = themed(report, keep)
    assert res.ok, res.messages()
    assert css.strip() == keep.strip()


def test_url_query_fragment_and_image_set(report):
    res, css = themed(report, '@font-face{font-family:B;src:url(fonts/b.eot?#iefix) format("embedded-opentype"),'
                              'url(fonts/b.woff2?v=3)}\n.i{mask:url(img/icons.svg#star)}\n'
                              '.s{background:image-set("img/dot.gif" type("image/gif") 1x, url(img/dot.gif) 2x)}\n'
                              ".t{background:-webkit-image-set('img/dot.gif' 1x)}\n",
                      {"reports/r/fonts/b.eot": FONT, "reports/r/fonts/b.woff2": FONT,
                       "reports/r/img/icons.svg": "<svg/>", "reports/r/img/dot.gif": GIF})
    assert res.ok, res.messages()
    assert 'url("data:application/vnd.ms-fontobject;base64,' in css and '#iefix")' in css
    assert "?v=3" not in css and 'url("data:image/svg+xml;base64,PHN2Zy8+#star")' in css
    gif = f'"data:image/gif;base64,{base64.b64encode(GIF).decode()}"'
    assert f'image-set({gif} type("image/gif") 1x, url({gif}) 2x)' in css
    assert f"-webkit-image-set({gif} 1x)" in css


def test_css_errors_name_the_css_file_and_line(report):
    res, css = themed(report, '@import "a.css";\n@IMPORT url(b.css);\n.a{background:url(/abs.png)}\n'
                              ".b{background:url(//cdn.example/x.png)}\n.c{background:url(https://cdn.example/y.png)}\n"
                              ".d{background:url(img/missing.png)}\n.e{background:url(notes.txt)}\n")
    msgs = res.messages()
    for n, text in [(1, "@import isn't supported; list the file in theme.css instead"),
                    (2, "@import isn't supported"), (3, "root-relative"), (4, "remote url(//cdn.example/x.png)"),
                    (5, "remote url(https://cdn.example/y.png)"), (6, "asset not found: img/missing.png"),
                    (7, "can't inline .txt files")]:
        assert any(m.startswith(f"error: report.md: brand.css:{n}: ") and text in m for m in msgs), (n, msgs)
    assert all(e.line is None for e in res.errors) and len(res.errors) == 7
    assert "cdn.example" not in css


# escaped names, a comment or modifier after a quoted url, an escaped image-set
HIDDEN = ["u\\72 l({})", '\\75rl("{}")', 'url("{}"/**/)', "url('{}' foo)", 'IMAGE-\\73 et("{}" 1x)']


def test_escapes_and_url_modifiers_hide_no_remote_url(report):
    res, css = themed(report, "".join(f".a{{background:{h.format('https://x.example/a.png')}}}\n" for h in HIDDEN))
    msgs = res.messages()
    for n in range(1, len(HIDDEN) + 1):
        assert any(m.startswith(f"error: report.md: brand.css:{n}: remote url(https://x.example/a.png)")
                   for m in msgs), (n, msgs)
    assert "x.example" not in res.html


def test_url_cut_off_by_the_end_of_the_css_is_still_checked(report):
    res, css = themed(report, ".a{background:url(https://x.example/a.png")
    assert any("brand.css:1: remote url(https://x.example/a.png)" in m for m in res.messages())
    assert "x.example" not in res.html


def test_scan_stays_linear_on_unclosed_urls():
    from rapport_core import css as css_mod
    start = time.time()
    css_mod.scan("url(" * 20000 + "url(a" * 20000)
    assert time.time() - start < 2


def test_escapes_and_url_modifiers_still_inline(report):
    res, css = themed(report, "".join(f".a{{background:{h.format('dot.gif')}}}\n" for h in HIDDEN),
                      {"reports/r/dot.gif": GIF})
    assert res.ok, res.messages()
    gif = f'"data:image/gif;base64,{base64.b64encode(GIF).decode()}"'
    assert css.splitlines() == [f".a{{background:url({gif})}}", f".a{{background:url({gif})}}",
                                f".a{{background:url({gif}/**/)}}", f".a{{background:url({gif} foo)}}",
                                f".a{{background:IMAGE-\\73 et({gif} 1x)}}"]


def test_var_inside_image_set_is_an_error(report):
    res, css = themed(report, '.a{--u:"https://x.example/a.png";background-image:image-set(var(--u) 1x)}\n'
                              ".b{background:-webkit-image-set(var(--v) 1x, url(dot.gif) 2x)}\n"
                              ".c{background:url(dot.gif) var(--pos)}\n", {"reports/r/dot.gif": GIF})
    assert [m.removeprefix("error: report.md: ") for m in res.messages()] == [
        f"brand.css:{n}: var() inside {f}(): a url can't be checked or inlined through a variable; write it out"
        for n, f in ((1, "image-set"), (2, "-webkit-image-set"))]
    assert "var(--pos)" in css


def test_import_is_dropped_from_the_page(report):
    res, css = themed(report, '@import "https://x.example/a.css";\n@IMPORT url(https://x.example/b.css) screen;\n'
                              '@\\69mport "https://x.example/c.css" layer(x);\n.a{color:red}\n@import "d.css"')
    assert [e.message.split(": @import")[0] for e in res.errors] == [f"brand.css:{n}" for n in (1, 2, 3, 5)]
    assert css == "\n\n\n.a{color:red}\n" and "x.example" not in res.html


def test_byte_order_mark_is_not_part_of_the_first_rule(report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/boxed"
    write(mod / "module.toml", 'name = "boxed"\nkind = "feature"\nsummary = "B."\n')
    write(mod / "style.css", "\ufeff.rp-boxed{color:var(--rp-ink)}\n")
    write(isolated_home / "proj/rapport.toml", "")
    res, css = themed(report, "\ufeffh2{color:red}\n")
    assert css.startswith("h2{color:red}") and "/* boxed */\n.rp-boxed{" in style(res.html)
    assert "\ufeff" not in res.html


def test_style_and_script_end_tags_are_escaped(report):
    res, css = themed(report, '.x{content:"</STYLE><script>alert(1)</script>"}\n')
    assert res.html.lower().count("</style") == 1 and '"<\\/STYLE><script>' in css
    assert _script_safe('s = "</SCRIPT>"; t = "</script>"') == 's = "<\\/SCRIPT>"; t = "<\\/script>"'


def test_css_assets_count_toward_the_inline_budget(report):
    res, _ = themed(report, ".a{background:url(big.gif)}\n.b{background:url(big.gif)}\n",
                    {"reports/r/big.gif": GIF * 200}, ["report.max_inline_mb=0.01"])
    assert any(m.startswith("brand.css:2: inlined assets exceed report.max_inline_mb") for m in (e.message for e in res.errors))


def test_page_never_shows_local_paths(report, isolated_home):
    write(isolated_home / "proj/rapport.toml", '[theme]\ncss = ["style/brand.css"]\n')
    d = report("x\n", {"style/brand.css": "@font-face{font-family:B;src:url(fonts/b.woff2)}\n",
                       "style/fonts/b.woff2": FONT})
    res = build(d)
    assert res.ok, res.messages()
    for p in (isolated_home, isolated_home.resolve(), d):
        assert str(p) not in res.html
    assert "/* theme.css: brand.css */" in res.html


def test_module_css_url_is_relative_to_its_folder_and_checks_clean(capsys, report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/brand"
    write(mod / "module.toml", 'name = "brand"\nkind = "theme"\nsummary = "Brand."\n[tokens]\nink = "#111111"\n'
                               'bg = "#ffffff"\nwidth = "60ch"\nbody-size = "18px"\n[tokens.dark]\nink = "#eeeeee"\n')
    write(mod / "style.css", '@font-face{font-family:Brand;src:url(fonts/Brand.woff2) format("woff2")}\n'
                             "body{color:var(--rp-ink);background:url(\"data:image/svg+xml,<svg fill='#fff'/>\")}\n"
                             "@media (prefers-reduced-motion:reduce){*{animation:none!important}}\n")
    (mod / "fonts").mkdir()
    (mod / "fonts/Brand.woff2").write_bytes(FONT)
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml", '[theme]\nname = "brand"\n')
    res = build(d)
    assert res.ok, res.messages()
    assert 'src:url("data:font/woff2;base64,' in style(res.html).split("@layer rp-modules{")[0]
    assert main(["modules", "check", "brand", "-C", str(d)]) == 0
    assert "brand          ok" in capsys.readouterr().out
    assert main(["modules", "info", "brand", "-C", str(d)]) == 0
    out = capsys.readouterr().out
    assert "  ink        #111111    dark: #eeeeee" in out and "  bg         #ffffff\n" in out


def test_check_flags_important_and_bad_urls(capsys, report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/loud"
    write(mod / "module.toml", 'name = "loud"\nkind = "layout"\nsummary = "Loud."\n')
    write(mod / "template.html", '<div class="rp-loud">x</div>\n')
    write(mod / "style.css", ".rp-loud{color:var(--rp-ink)!important}\n.rp-loud b{background:url(gone.png)}\n")
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml", "")
    assert main(["modules", "check", "loud", "-C", str(d)]) == 1
    out = capsys.readouterr().out
    assert "error: style.css:2: asset not found: gone.png" in out
    assert "warning: !important in style.css can't be overridden by theme.css" in out


def test_layout_width_defaults_to_the_theme_token(report):
    d = report("x\n")
    assert "--rp-width:64ch;" in style(build(d, ["theme.name=paper"]).html)
    assert "--rp-width:50ch;" in style(build(d, ["theme.name=paper", 'theme.tokens.width="50ch"']).html)
    assert "--rp-width:80ch;" in style(build(d, ["layout.width=80ch"]).html)


def test_theme_without_width_or_body_size_gets_rapports_own(capsys, report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/bare"
    write(mod / "module.toml", 'name = "bare"\nkind = "theme"\nsummary = "Bare."\n[tokens]\nink = "#111111"\n'
                               '[tokens.dark]\nink = "#eeeeee"\n')
    write(isolated_home / "proj/rapport.toml", '[theme]\nname = "bare"\n')
    d = report("x\n")
    root = style(build(d).html).split("\n")[2]
    assert root == (":root{color-scheme:light dark;--rp-ink:#111111;--rp-width:70ch;--rp-body-size:17px;"
                    "--rp-on-light:block;--rp-on-dark:none;}")
    assert "--rp-width:80ch;" in style(build(d, ["layout.width=80ch"]).html)
    for name, size in (("default", "16px"), ("classic", "17px"), ("paper", "18px")):       # a theme's own, once
        css = style(build(d, [f"theme.name={name}"]).html)
        assert css.count("--rp-width:") == css.count("--rp-body-size:") == 1 and f"--rp-body-size:{size};" in css
    assert main(["modules", "check", "bare", "-C", str(d)]) == 0
    assert "bare           warning: no width or body-size token; the page falls back to width = 70ch, " \
           "body-size = 17px" in capsys.readouterr().out


def test_watch_covers_css_files_and_what_they_reference(report):
    from rapport_core.serve import make_server
    d = report(BRAND, {"reports/r/brand.css": "@font-face{font-family:B;src:url(b.woff2)}\n",
                       "reports/r/b.woff2": FONT})
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    try:
        assert d.resolve() / "brand.css" in app.files and d.resolve() / "b.woff2" in app.files
        threading.Thread(target=app.watch, args=(0.05,), daemon=True).start()
        time.sleep(0.2)
        first = app.build_id
        (d / "b.woff2").write_bytes(FONT + b"more")
        deadline = time.time() + 5
        while app.build_id == first and time.time() < deadline:
            time.sleep(0.05)
        assert app.build_id > first and base64.b64encode(FONT + b"more") in app.html
    finally:
        httpd.server_close()


def test_served_page_assets_are_watched(report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/served-only"
    write(mod / "module.toml", 'name = "served-only"\nkind = "feature"\nsummary = "S."\nwhen = "serve"\n')
    write(mod / "style.css", ".rp-so{background:url(dot.gif)}\n")
    (mod / "dot.gif").write_bytes(GIF)
    write(isolated_home / "proj/rapport.toml", "")
    res = build(report("x\n"), serve_data={"serve": {"token": "t", "api": "/api", "build_id": 1}})
    assert res.ok, res.messages()
    assert mod.resolve() / "dot.gif" in res.assets and "data:image/gif" not in res.html


def test_body_size_is_the_rem_of_every_theme(report, isolated_home):
    mod = isolated_home / "proj/rapport-modules/bare"
    write(mod / "module.toml", 'name = "bare"\nkind = "theme"\nsummary = "Bare."\n[tokens]\nbody-size = "19px"\n')
    write(mod / "style.css", "body{margin:0}\n")
    write(isolated_home / "proj/rapport.toml", "")
    d = report("x\n")
    theme = style(build(d, ["theme.name=bare"]).html).split("@layer rp-modules{")[0]
    assert "html{font-size:var(--rp-body-size)}" in theme and "--rp-body-size:19px;" in theme
    # the body is 1rem, so a rem/em body-size applies once instead of compounding
    for name, body in (("default", "font:1rem/1.65 "), ("classic", "font:1rem/1.65 "), ("paper", "font:1rem/1.7 ")):
        css = style(build(d, [f"theme.name={name}", 'theme.tokens.body-size="1.1rem"']).html)
        assert "--rp-body-size:1.1rem;" in css and body in css and "font:var(--rp-body-size)" not in css


def test_check_warns_about_px_font_sizes_except_in_feature_ui(capsys, report, isolated_home):
    write(isolated_home / "proj/rapport.toml", "")
    d = report("x\n")
    for name, kind in (("note", "block"), ("tag", "inline"), ("strip", "layout"), ("pop", "feature"), ("brand", "theme")):
        assert main(["modules", "new", name, "--kind", kind, "-C", str(d)]) == 0
        capsys.readouterr()
        assert main(["modules", "check", name, "-C", str(d)]) == 0
        assert f"{name:<14} ok" in capsys.readouterr().out    # scaffolds and the theme they extend use rem
    mods = isolated_home / "proj/rapport-modules"

    def check(name, css):
        p = mods / name / "style.css"          # a new theme extends default: its style.css starts empty
        p.write_text((p.read_text() if p.exists() else "") + css)
        main(["modules", "check", name, "-C", str(d)])
        return capsys.readouterr().out
    warning = "warning: font-size in px won't scale with body-size; use rem or em"
    assert warning not in check("note", "@media (max-width:640px){.rp-note{padding:8px;font:1rem/20px x}}\n")
    assert warning in check("note", ".rp-note-title{font:600 13px var(--rp-sans)}\n")
    assert warning in check("brand", ".rp-content h2{font-size:22px}\n")
    assert warning not in check("pop", ".rp-pop{font:12px var(--rp-sans)}\n")
