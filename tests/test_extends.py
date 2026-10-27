"""Modules that extend another: `extends` in module.toml, `modules new` (thin, --copy, --extends),
[upstream] drift, `modules diff`, and everything downstream that has to see inherited files."""

import base64
import threading
import time
import tomllib

from conftest import write
from rapport_core.build import build
from rapport_core.cli import main
from rapport_core.config import load, user_paths
from rapport_core.serve import make_server
from rapport_core.store import module_store
from test_server import req

TIP = """name = "tip"
kind = "block"
summary = "A tip."
positional = "title"

[params.title]
type = "string"
default = ""
doc = "Shown first."

[params.tone]
type = "enum"
choices = ["plain", "warm"]
default = "plain"
doc = "Colour."
"""
TIP_TEMPLATE = '<div class="rp-tip rp-tip-{{ node.params.tone }}"><b>{{ node.params.title }}</b>{{ node.body }}</div>\n'
TIP_FILES = {"module.toml": TIP, "template.html": TIP_TEMPLATE, "example.md": "::: tip Hello\nA tip.\n:::\n",
             "style.css": ".rp-tip{border:1px solid var(--rp-line)}\n.rp-tip b{color:var(--rp-accent)}\n"}


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def user_module(name: str, files: dict):
    d = user_paths().modules_dir / name
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(text) if isinstance(text, bytes) else write(d / rel, text)
    return d


def project(isolated_home, text="x\n", report=None):
    write(isolated_home / "proj/rapport.toml", "")
    return report(text), isolated_home / "proj/rapport-modules"


# ── same-name overrides ───────────────────────────────────────────────────────── #
def test_a_thin_override_replaces_its_template_and_inherits_the_rest(capsys, report, isolated_home):
    d, mods = project(isolated_home, "::: callout Hi {tone = \"tip\"}\nBody.\n:::\n", report)
    assert run(capsys, "modules", "new", "callout", "-C", str(d))[0] == 0
    write(mods / "callout/template.html", '<aside class="rp-callout mine">{{ node.body }}</aside>\n')
    res = build(d)
    assert res.ok, res.messages()
    assert 'class="rp-m-callout rp-block rp-callout mine"' in res.html
    assert ".rp-callout.rp-tone-warning{" in res.html             # the built-in's style.css still applies
    cfg = load(d)
    mod = cfg.registry.get("callout")
    assert mod.base.builtin and mod.example_path == mod.base.example_path and mod.positional == "title"
    code, out = run(capsys, "modules", "check", "callout", "-C", str(d))
    assert code == 0 and "callout        ok" in out                  # the inherited example renders
    assert "::: callout Heads up" in run(capsys, "brief", "-C", str(d))[1]


def test_an_override_adds_a_param_and_changes_a_default(capsys, report, isolated_home):
    d, mods = project(isolated_home, "::: callout Hi\nBody.\n:::\n\n::: callout {icon = \"!\"}\nB.\n:::\n", report)
    write(mods / "callout/module.toml", 'name = "callout"\nextends = "callout"\n\n[params.tone]\ndefault = "tip"\n\n'
                                        '[params.icon]\ntype = "string"\ndefault = ""\ndoc = "Shown first."\n')
    write(mods / "callout/template.html", '<aside class="rp-callout rp-tone-{{ node.params.tone }}">'
                                          '{{ node.params.icon }}{{ node.body }}</aside>\n')
    res = build(d)
    assert res.ok, res.messages()
    mod = res.config.registry.get("callout")
    assert mod.params["tone"]["choices"] == ["note", "tip", "warning", "danger", "success"]   # merged per field
    assert list(mod.params) == ["tone", "title", "icon"] and mod.body == "markdown"
    assert res.html.count("rp-tone-tip") >= 2 and ">!<p " in res.html
    assert res.config.module("callout")["tone"] == "tip"                  # the default reaches config too


def test_inherited_style_css_inlines_urls_from_its_own_folder(capsys, report, isolated_home):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    user_module("tip", {**TIP_FILES, "style.css": ".rp-tip{background:url(dot.svg)}\n", "dot.svg": svg,
                        "example.md": "::: tip Hello\n![a dot](assets/pic.svg)\n:::\n", "assets/pic.svg": svg})
    d, mods = project(isolated_home, "::: tip Hi\nBody.\n:::\n", report)
    write(mods / "tip/module.toml", 'name = "tip"\nextends = "tip"\n')
    write(mods / "tip/style.css", ".rp-tip{border-color:var(--rp-accent)}\n")
    res = build(d)
    assert res.ok, res.messages()
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    base_at, own_at = res.html.index(f'.rp-tip{{background:url("{uri}")}}'), res.html.index(".rp-tip{border-color")
    assert base_at < own_at < res.html.index("@layer rp-modules{") + len(res.html)    # base first, one layer
    code, out = run(capsys, "modules", "check", "tip", "-C", str(d))      # the example finds the base's assets/
    assert code == 0 and "tip            ok" in out
    write(user_paths().modules_dir / "tip/style.css", ".rp-tip{background:url(gone.png)}\n")
    code, out = run(capsys, "modules", "check", "tip", "-C", str(d))
    assert code == 1 and "error: style.css (inherited, in " in out and "asset not found: gone.png" in out


NOTES_SERVER = '''def routes(r):
    r.get("/", lambda req: req.store.read())
    r.post("/", add)


def add(req):
    with req.store.edit() as state:
        state.setdefault("items", []).append(req.body["text"])
    return state


def describe(state, config):
    return f"{len(state.get('items', []))} note(s)"
'''


def test_inherited_prepare_and_server_load_from_where_they_live(capsys, report, isolated_home):
    base = user_module("notes", {
        "module.toml": 'name = "notes"\nkind = "block"\nsummary = "Notes."\nbody = "text"\n',
        "template.html": '<div class="rp-notes">{{ where }} {{ node.text }}</div>\n',
        "prepare.py": "from pathlib import Path\n\n\ndef prepare(node, ctx):\n"
                      "    return {'where': Path(__file__).parents[2].name}\n",
        "server.py": NOTES_SERVER})
    d, mods = project(isolated_home, "::: notes\nhi\n:::\n", report)
    write(mods / "notes/module.toml", 'name = "notes"\nextends = "notes"\n')
    res = build(d)
    assert res.ok, res.messages()
    assert "rapport hi</div>" in res.html          # __file__ is the base's: …/rapport/rapport-modules/notes
    mod = res.config.registry.get("notes")
    assert mod.has_code and mod.has_server and mod.file("server.py") == (base / "server.py").resolve()
    assert "[custom, extends notes (custom), runs prepare.py, runs server.py]" in run(capsys, "modules", "list",
                                                                                         "-C", str(d))[1]
    with module_store(d, "notes").edit() as s:                         # the state of the name, base or override
        s["items"] = ["before"]
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert not app.errors
        assert req(url + "api/m/notes/") == (200, {"items": ["before"]})
        assert req(url + "api/m/notes/", "POST", {"text": "after"}, app.token) == (200, {"items": ["before", "after"]})
    finally:
        httpd.shutdown()
        httpd.server_close()
    capsys.readouterr()
    assert run(capsys, "modules", "state", "notes", "-C", str(d)) == (0, "2 note(s)\n")


def test_a_chain_of_overrides_merges_every_level(capsys, report, isolated_home):
    user_module("tip", TIP_FILES)
    d, mods = project(isolated_home, "::: tip Hi\nBody.\n:::\n", report)
    write(mods / "tip/module.toml", 'name = "tip"\nextends = "tip"\n[params.tone]\ndefault = "warm"\n')
    write(mods / "tip/style.css", ".rp-tip-warm{color:var(--rp-warn)}\n")
    write(d / "rapport-modules/tip/module.toml", 'name = "tip"\nextends = "tip"\nsummary = "Mine."\n')
    write(d / "rapport-modules/tip/template.html", '<p class="rp-tip rp-tip-{{ node.params.tone }}">{{ node.body }}</p>\n')
    res = build(d)
    assert res.ok, res.messages()
    mod = res.config.registry.get("tip")
    assert [m.dir for m in mod.chain] == [(d / "rapport-modules/tip").resolve(), (mods / "tip").resolve(),
                                          (user_paths().modules_dir / "tip").resolve()]
    assert mod.summary == "Mine." and mod.params["tone"]["default"] == "warm"
    assert '<p class="rp-m-tip rp-block rp-tip rp-tip-warm"' in res.html
    assert res.html.index(".rp-tip b{") < res.html.index(".rp-tip-warm{")
    code, out = run(capsys, "modules", "info", "tip", "-C", str(d))
    assert code == 0 and "chain: tip (report) extends tip (project) extends tip (user)\n" in out
    for line in ("  module.toml    report on top of project on top of user (merged)\n",
                 "  example.md     user (inherited)\n",
                 "  style.css      user, then project (each added after the one before)\n",
                 "  template.html  report, replacing user's\n"):
        assert line in out, line


def test_extends_errors_name_the_problem_and_the_page_still_builds(capsys, report, isolated_home):
    d, mods = project(isolated_home, "::: callout Hi\nBody.\n:::\n", report)
    write(mods / "a/module.toml", 'name = "a"\nextends = "b"\n')
    write(mods / "b/module.toml", 'name = "b"\nextends = "a"\n')
    write(mods / "c/module.toml", 'name = "c"\nextends = "calout"\n')
    write(mods / "d/module.toml", 'name = "d"\nextends = "d"\nkind = "block"\n')
    write(mods / "e/module.toml", 'name = "e"\nextends = "c"\n')
    write(mods / "callout/module.toml", 'name = "callout"\nkind = "inline"\nextends = "callout"\n')
    cfg = load(d)
    errors = "\n".join(cfg.registry.errors)
    assert "a/module.toml: `extends` goes round in a circle: a (" in errors and ") → a" in errors
    assert errors.count("circle") == 1 and "b/module.toml" not in errors
    assert "extends 'calout': no such module (did you mean 'callout'?)" in errors
    assert "extends 'd', but there's no other 'd' below it to extend" in errors
    assert "e/module.toml: extends 'c' (" in errors and "which has an error (see above)" in errors
    assert "kind = \"inline\", but 'callout' (built-in), which it extends, is a block; leave kind out" in errors
    assert not {"a", "b", "c", "d", "e"} & set(cfg.registry.names())
    assert cfg.registry.get("callout").builtin                          # a broken override falls back
    assert build(d).ok
    code, out = run(capsys, "modules", "check", "-C", str(d))
    assert code == 1 and "5 error(s)" in out


# ── modules with a new name ────────────────────────────────────────────────────── #
def test_a_derived_module_gets_its_own_name_and_script(capsys, report, isolated_home):
    d, mods = project(isolated_home, "::: my-collapse More\nInside.\n:::\n\n::: collapse Also\nX.\n:::\n", report)
    code, out = run(capsys, "modules", "new", "my-collapse", "--extends", "collapse", "-C", str(d))
    assert code == 0 and "script.js and example.md: copied, and renamed to 'my-collapse'" in out
    assert sorted(p.name for p in (mods / "my-collapse").iterdir()) == ["example.md", "module.toml", "script.js"]
    assert 'Rapport.module("my-collapse", {' in (mods / "my-collapse/script.js").read_text()
    assert (mods / "my-collapse/example.md").read_text().startswith("::: my-collapse ")
    assert run(capsys, "modules", "check", "my-collapse", "-C", str(d))[0] == 0
    res = build(d)
    assert res.ok, res.messages()
    assert 'data-rp-module="my-collapse"' in res.html and 'data-rp-module="collapse"' in res.html
    assert 'Rapport.module("my-collapse"' in res.html and 'Rapport.module("collapse"' in res.html
    (mods / "my-collapse/script.js").unlink()           # a script can't register under the other name
    errors = "\n".join(load(d).registry.errors)
    assert "can't inherit script.js from 'collapse': it registers itself as Rapport.module(\"collapse\", …)" in errors
    write(mods / "warn-box/module.toml", 'name = "warn-box"\nextends = "callout"\nsummary = "A warning."\n'
                                         '[params.tone]\ndefault = "warning"\n')
    cfg = load(d)
    box = cfg.registry.get("warn-box")
    assert box.template_path == box.base.template_path and box.example_path is None     # the example names callout
    assert "warning: no example.md" in run(capsys, "modules", "check", "warn-box", "-C", str(d))[1]
    code, out = run(capsys, "modules", "new", "my-code", "--extends", "code", "-C", str(d))
    assert code == 2 and "would render nothing" in out


def test_a_new_theme_extends_default_and_merges_tokens(capsys, report, isolated_home):
    d, mods = project(isolated_home, report=report)
    assert run(capsys, "modules", "new", "brand", "--kind", "theme", "-C", str(d))[0] == 0
    text = (mods / "brand/module.toml").read_text()
    write(mods / "brand/module.toml", text.replace("[tokens]\n", '[tokens]\naccent = "#C00000"\n')
          .replace("[tokens.dark]\n", '[tokens.dark]\naccent = "#FF6666"\n'))
    res = build(d, ["theme.name=brand"])
    assert res.ok, res.messages()
    theme, default = res.config.registry.get("brand"), res.config.registry.get("default")
    assert theme.tokens == {**default.tokens, "accent": "#C00000"}
    assert theme.dark_tokens == {**default.dark_tokens, "accent": "#FF6666"}
    assert "--rp-accent:#C00000;" in res.html and default.css_paths[0].read_text()[:40] in res.html


# ── modules new --copy, provenance, drift ──────────────────────────────────────── #
def test_copy_skips_pycache_and_records_where_it_came_from(capsys, report, isolated_home):
    base = user_module("tip", {**TIP_FILES, "__pycache__/x.pyc": b"\0", ".DS_Store": b"\0"})
    d, mods = project(isolated_home, report=report)
    code, out = run(capsys, "modules", "new", "tip", "--copy", "-C", str(d))
    assert code == 0 and "copied tip (user)" in out
    assert sorted(p.name for p in (mods / "tip").iterdir()) == ["example.md", "module.toml", "style.css",
                                                                "template.html"]
    data = tomllib.loads((mods / "tip/module.toml").read_text())
    assert data["upstream"]["version"] and sorted(data["upstream"]["files"]) == sorted(TIP_FILES)
    assert "extends" not in data and "upstream" not in tomllib.loads((base / "module.toml").read_text())
    assert run(capsys, "modules", "check", "tip", "-C", str(d))[0] == 0
    assert run(capsys, "modules", "new", "tip", "--copy", "--scope", "report", "-C", str(d))[0] == 0   # a copy of a copy
    assert tomllib.loads((d / "rapport-modules/tip/module.toml").read_text())["upstream"]["files"]["module.toml"] \
        != data["upstream"]["files"]["module.toml"]
    assert run(capsys, "modules", "new", "callout", "-C", str(d))[0] == 0
    code, out = run(capsys, "modules", "new", "callout", "--copy", "--scope", "report", "-C", str(d))
    assert code == 2 and "extends callout (built-in), so it isn't one folder to copy" in out
    code, out = run(capsys, "modules", "new", "tip", "--scope", "user", "-C", str(d.parent / "elsewhere"))
    assert code == 2 and "already exists" in out
    assert run(capsys, "modules", "new", "badge", "--scope", "report", "-C", str(d))[0] == 0
    code, out = run(capsys, "modules", "new", "badge", "-C", str(d))           # the report's badge would win
    assert code == 2 and "comes first in the module search" in out and "would never be used" in out
    code, out = run(capsys, "modules", "new", "calout", "--copy", "-C", str(d))
    assert code == 2 and "there's no 'calout' (did you mean 'callout'?)" in out


def test_check_warns_when_a_file_an_override_replaces_changes_upstream(capsys, report, isolated_home):
    base = user_module("tip", TIP_FILES)
    d, mods = project(isolated_home, report=report)
    assert run(capsys, "modules", "new", "tip", "-C", str(d))[0] == 0
    write(mods / "tip/template.html", TIP_TEMPLATE.replace("<b>", "<strong>").replace("</b>", "</strong>"))
    write(base / "style.css", ".rp-tip{border:2px solid var(--rp-line)}\n")      # inherited or added to: no warning
    write(base / "example.md", "::: tip Bye\nA tip.\n:::\n")
    assert "tip            ok" in run(capsys, "modules", "check", "tip", "-C", str(d))[1]
    write(base / "template.html", TIP_TEMPLATE.replace("<div", "<section").replace("</div>", "</section>"))
    code, out = run(capsys, "modules", "check", "tip", "-C", str(d))
    assert code == 0 and "warning: tip (user) has changed since this override was made (rapport " in out
    assert "): template.html; compare: `rapport modules diff tip`" in out
    assert "changed there since: template.html" in run(capsys, "modules", "info", "tip", "-C", str(d))[1]
    code, out = run(capsys, "modules", "diff", "tip", "-C", str(d))
    assert code == 0 and out.startswith("# tip (project) against tip (user)\n")
    assert f"--- {(base / 'template.html').resolve()}\n" in out and "-<section class" in out and "+<div class" in out
    assert "\n# Changed in tip (user) since this was made: template.html." in out
    write(mods / "tip/template.html", (base / "template.html").read_text())   # merged by taking theirs
    assert "tip            ok" in run(capsys, "modules", "check", "tip", "-C", str(d))[1]


def test_check_warns_when_a_copy_falls_behind_and_diff_says_how_to_catch_up(capsys, report, isolated_home):
    base = user_module("tip", TIP_FILES)
    d, mods = project(isolated_home, report=report)
    assert run(capsys, "modules", "new", "tip", "--copy", "-C", str(d))[0] == 0
    assert "tip            ok" in run(capsys, "modules", "check", "tip", "-C", str(d))[1]
    write(base / "template.html", TIP_TEMPLATE.replace("<b>", "<i>").replace("</b>", "</i>"))
    write(base / "server.py", "def routes(r):\n    pass\n")
    (base / "example.md").unlink()
    code, out = run(capsys, "modules", "check", "tip", "-C", str(d))
    assert "warning: tip (user) has changed since this copy was made (rapport " in out
    assert "): example.md (gone), server.py (new), template.html; compare: `rapport modules diff tip`" in out
    out = run(capsys, "modules", "diff", "tip", "-C", str(d))[1]
    assert "# example.md: only in tip (project)\n" in out and "# server.py: only in tip (user)\n" in out
    assert "-<div class=\"rp-tip rp-tip-{{ node.params.tone }}\"><i>" in out and "+[upstream]" in out
    record = out.split("[upstream.files]\n")[-1]
    mf = mods / "tip/module.toml"
    mf.write_text(mf.read_text().split("[upstream.files]\n")[0] + "[upstream.files]\n" + record)
    assert "tip            ok" in run(capsys, "modules", "check", "tip", "-C", str(d))[1]


def test_info_and_list_without_inheritance_are_unchanged(capsys, report):
    d = report("x\n")
    out = run(capsys, "modules", "info", "callout", "-C", str(d))[1]
    assert "files: module.toml, template.html, style.css, example.md\n" in out and "chain:" not in out
    code, out = run(capsys, "modules", "diff", "callout", "-C", str(d))
    assert code == 2 and "neither extends nor overrides another module" in out


# ── the review server ─────────────────────────────────────────────────────────── #
def test_the_watcher_rebuilds_when_a_base_file_changes(report, isolated_home):
    base = user_module("tip", TIP_FILES)
    d, mods = project(isolated_home, "::: tip Hi\nBody.\n:::\n", report)
    write(mods / "tip/module.toml", 'name = "tip"\nextends = "tip"\n')
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    try:
        assert (base / "template.html").resolve() in app.files and (base / "style.css").resolve() in app.files
        threading.Thread(target=app.watch, args=(0.05,), daemon=True).start()
        time.sleep(0.2)
        first = app.build_id
        write(base / "template.html", TIP_TEMPLATE.replace("<b>", "<b class=\"edited\">"))
        deadline = time.time() + 5
        while app.build_id == first and time.time() < deadline:
            time.sleep(0.05)
        assert app.build_id > first and b'<b class="edited">' in app.html
    finally:
        httpd.server_close()
