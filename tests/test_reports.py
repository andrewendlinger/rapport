"""Finding reports: rapport list, rapport hub and the reports module."""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from conftest import write
from rapport_core import reports
from rapport_core.build import build
from rapport_core.cli import main
from rapport_core.serve import make_server, port_taken, read_server, running, stop
from test_server import req

ROOT = Path(__file__).resolve().parent.parent
HUB = "[modules.reports]\nenabled = true"


def make(home: Path, rel: str, front: str = "", body: str = "Text.\n", state: dict | None = None) -> Path:
    """A report folder under $HOME with a .rapport/ holding the given state files."""
    d = home / rel
    write(d / "report.md", (f"+++\n{front}\n+++\n" if front else "") + body)
    (d / ".rapport").mkdir(parents=True, exist_ok=True)
    for name, data in (state or {}).items():
        write(d / ".rapport" / name, json.dumps(data))
    return d


def set_mtime(d: Path, when: float) -> None:
    for p in [d / "report.md", *(d / ".rapport").rglob("*.json")]:
        os.utime(p, (when, when))


@pytest.fixture
def tree(isolated_home):
    home = isolated_home
    a = make(home, "work/alpha/reports/first", 'title = "First"\nstatus = "draft"\ntags = ["sms", "belt"]')
    b = make(home, "work/beta/reports/second", 'title = "Second"\nsubtitle = "Sub"', state={
        "state.json": {"version": 4},
        "feedback.json": {"items": [{"id": "FB-1", "status": "open"}, {"id": "FB-2", "status": "resolved"},
                                    {"id": "FB-3", "status": "open"}]},
        "server.json": {"pid": None, "url": "http://127.0.0.1:4999/", "port": 4999, "token": "t",
                        "started": "2026-01-02T10:00:00"},
        "confluence/page.json": {"page_id": "123", "version": 5, "title": "Second"},
    })
    c = make(home, "work/gamma", body="# Lifted title\n\nText.\n")
    write(home / "work/node_modules/x/.rapport/state.json", "{}")       # skipped folders
    write(home / "work/.hidden/r/.rapport/state.json", "{}")
    write(home / "work/alpha/reports/first/sub/.rapport/state.json", "{}")    # inside a report: not searched
    write(home / "work/plain/report.md", "no state folder\n")
    now = time.time()
    set_mtime(a, now - 3 * 86400)
    set_mtime(b, now - 86400)
    set_mtime(c, now)
    return home, a, b, c


def test_find_skips_hidden_tool_and_nested_folders(tree):
    home, a, b, c = tree
    assert sorted(reports.find([home / "work"])) == sorted([a, b, c])
    assert reports.find([home / "work"], exclude=[home / "work/beta"]) == [a, c]
    assert reports.find([home / "missing"]) == []
    assert reports.find([home / "work", home / "work/alpha"]).count(a) == 1


def test_info_reads_what_rapport_keeps(tree):
    home, a, b, c = tree
    first = reports.info(a)
    assert (first["title"], first["status"], first["tags"], first["project"]) == ("First", "draft", ["sms", "belt"], "alpha")
    assert first["version"] == 1 and first["open_feedback"] == 0 and first["confluence"] is None
    assert first["where"] == "~/work/alpha/reports/first" and first["serving"] is None and first["served"] is None
    second = reports.info(b, site="https://x.atlassian.net/wiki/")
    assert (second["subtitle"], second["version"], second["open_feedback"]) == ("Sub", 4, 2)
    assert second["served"] == "2026-01-02T10:00:00" and second["serving"] is None      # pid null: stopped
    assert second["confluence"]["url"] == "https://x.atlassian.net/wiki/pages/viewpage.action?pageId=123"
    assert reports.info(b)["confluence"]["url"] == ""
    assert reports.info(c)["title"] == "Lifted title"


def test_info_follows_report_source_and_survives_bad_front_matter(isolated_home):
    d = make(isolated_home, "work/r")
    (d / "report.md").unlink()
    write(d / "rapport.toml", '[report]\nsource = "README.md"\n')
    write(d / "README.md", '+++\ntitle = "From readme"\n+++\n')
    assert reports.info(d)["title"] == "From readme"
    assert reports.source_of(d) in reports.watch_files(d)
    bad = make(isolated_home, "work/bad", body="")
    write(bad / "report.md", "+++\ntitle = \n+++\n")
    row = reports.info(bad)
    assert row["title"] == "bad" and "invalid TOML" in row["error"]


def test_project_is_the_git_repo(isolated_home):
    d = make(isolated_home, "work/repo/sessions/s1/reports/r")
    (isolated_home / "work/repo/.git").mkdir()
    assert reports.info(d)["project"] == "repo"


def test_collect_most_recent_first(tree):
    home, a, b, c = tree
    assert [r["dir"] for r in reports.collect([home / "work"])] == [str(c), str(b), str(a)]


def test_list_cli(tree, capsys, monkeypatch):
    home, a, b, c = tree
    monkeypatch.chdir(home)
    assert main(["list"]) == 2
    assert "[hub] roots" in capsys.readouterr().err
    assert main(["list", str(home / "work")]) == 0
    out = capsys.readouterr().out
    assert out.index("Lifted title") < out.index("Second") < out.index("First")
    assert "[2 open]" in out and "[Confluence v5]" in out and "#sms" in out and "~/work/beta/reports/second" in out
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\nexclude = ["~/work/gamma"]\n')
    assert main(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["title"] for r in rows] == ["Second", "First"]


def test_hub_folder(isolated_home, monkeypatch):
    from rapport_core import config as config_mod
    cfg = config_mod.load(isolated_home)
    assert reports.hub_dir(cfg) == isolated_home / ".local/state/rapport/hub"
    monkeypatch.setenv("XDG_STATE_HOME", str(isolated_home / "st"))
    assert reports.hub_dir(cfg) == isolated_home / "st/rapport/hub"
    write(isolated_home / ".config/rapport/config.toml", '[hub]\ndir = "~/my-hub"\n')
    cfg = config_mod.load(isolated_home)
    d = reports.ensure_hub(cfg)
    assert d == isolated_home / "my-hub" and "::: reports" in (d / "report.md").read_text()
    assert 'title = "Rapports"' in (d / "report.md").read_text()
    write(d / "report.md", "mine\n")
    reports.ensure_hub(cfg)
    assert (d / "report.md").read_text() == "mine\n"         # yours once written


def test_module_lists_reports_and_not_its_own_page(tree):
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\n')
    hub = make(home, "work/hub", HUB, "::: reports\n:::\n")
    res = build(hub)
    assert res.ok, res.messages()
    html = (hub / "report.html").read_text()
    assert html.index("Lifted title") < html.index("Second") < html.index("First")
    assert "3 reports" in html and 'data-dir="' + str(hub) + '"' not in html
    assert "#sms" in html and "2 open</span>" in html and "v4 · " in html
    assert html.index('data-group="today"') < html.index("Lifted title") < html.index('data-group="week"') \
        < html.index("Second") < html.index("First")
    assert str(b / ".rapport" / "server.json") in {str(p) for p in res.assets}       # watched while served


def test_module_without_roots_says_how(isolated_home):
    hub = make(isolated_home, "hub", HUB, "::: reports\n:::\n")
    res = build(hub)
    assert res.ok and "Set [hub] roots" in (hub / "report.html").read_text()


def test_routes_serve_stop_and_refuse(tree):
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\n\n[serve]\nport = 0\nopen_browser = false\n')
    hub = make(home, "hub", HUB, "::: reports\n:::\n")
    httpd, app, url, cfg = make_server(hub, port=0, pinned=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        api = url + "api/m/reports/"
        assert req(api + "serve", "POST", {"dir": str(a)})[0] == 403                # no token
        code, res = req(api + "serve", "POST", {"dir": str(home)}, app.token)
        assert code == 400 and "not a report this page lists" in res["error"]
        code, res = req(api + "serve", "POST", {"dir": str(a)}, app.token)
        assert code == 200, res
        try:
            assert running(a)["url"] == res["url"]
            assert reports.info(a)["serving"] == res["url"]
        finally:
            code, res = req(api + "stop", "POST", {"dir": str(a)}, app.token)
        assert code == 200 and res["stopped"] and running(a) is None
        assert read_server(a)["pid"] is None
        before = hub.joinpath("report.md").stat().st_mtime_ns
        time.sleep(0.01)
        assert req(api + "refresh", "POST", {}, app.token)[0] == 200
        assert hub.joinpath("report.md").stat().st_mtime_ns > before
    finally:
        stop(a)
        httpd.shutdown()
        httpd.server_close()


def test_module_is_off_unless_switched_on(isolated_home):
    d = make(isolated_home, "r", body="::: reports\n:::\n")
    res = build(d)
    assert not res.ok and any("reports" in str(e) for e in res.errors)


def test_groups_serving_first_then_by_age():
    import importlib.util
    spec = importlib.util.spec_from_file_location("reports_prepare", ROOT / "modules/block/reports/prepare.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    from datetime import datetime, timedelta
    ago = lambda days: (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")     # noqa: E731
    rows = [{"active": ago(d), "serving": s, "n": n} for n, (d, s) in
            enumerate([(40, "http://x/"), (0, None), (3, None), (10, None), (100, None), (0, None)])]
    rows[5]["active"] = None
    got = {g["id"]: ([r["n"] for r in g["rows"]], g["open"]) for g in prep.groups(rows)}
    assert got == {"serving": ([0], True), "today": ([1], True), "week": ([2], True), "month": ([3], False),
                   "older": ([4, 5], False)}
    only = prep.groups([{"active": ago(100), "serving": None}])
    assert [(g["id"], g["open"]) for g in only] == [("older", True)]


def _prepare():
    import importlib.util
    spec = importlib.util.spec_from_file_location("reports_prepare", ROOT / "modules/block/reports/prepare.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    return prep


def test_first_image_skips_icons_and_dark_diagrams():
    import base64
    prep = _prepare()
    uri = lambda data, mime="image/png": f"data:{mime};base64,{base64.b64encode(data).decode()}"     # noqa: E731
    big, dark, svg = b"P" * 2000, b"D" * 2000, b"<svg>" + b" " * 2000 + b"</svg>"
    page = (f'<header><img src="{uri(big)}"></header><main class="rp-content" id="rp-content">'
            f'<img class="rp-icon" src="{uri(b"i" * 3000)}" data-rp-ui><img src="{uri(b"x" * 10)}">'
            f'<img class="rp-diagram-dark" src="{uri(dark)}"><img class="rp-diagram-light" src="{uri(svg, "image/svg+xml")}">'
            f'<img src="{uri(big)}"></main>').encode()
    assert prep.first_image(page) == ("image/svg+xml", svg)
    assert prep.first_image(b'<main id="rp-content"><p>no figures</p></main>') is None


def test_thumbnails_first_figure_front_matter_or_tile(tree):
    import base64
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\n')
    png = b"\x89PNG" + b"0" * 3000
    (a / "report.html").write_text(f'<main id="rp-content"><img src="data:image/png;base64,'
                                   f'{base64.b64encode(png).decode()}"></main>', encoding="utf-8")
    write(c / "assets/cover.svg", "<svg>" + " " * 2000 + "</svg>")
    (c / "report.md").write_text('+++\nthumbnail = "assets/cover.svg"\n+++\n# Lifted title\n', encoding="utf-8")
    hub = make(home, "hub", HUB, "::: reports\n:::\n")
    res = build(hub)
    assert res.ok, res.messages()
    html = (hub / "report.html").read_text()
    assert base64.b64encode(png).decode() in html                          # the first figure
    assert "data:image/svg+xml;base64," in html                            # the front matter's choice
    assert 'rp-reports-tile is-c' in html and ">BE</span>" in html         # beta has none: its initials
    assert "rp-reports-list is-cards" in html                              # recent groups are cards
    assert str(a / "report.html") in {str(p) for p in res.assets}          # a rebuilt report updates it


def test_thumbnail_names_a_figure_by_its_source_line(isolated_home):
    import base64
    prep = _prepare()
    d = make(isolated_home, "work/r")
    first, chosen = b"A" * 2000, b"B" * 2000
    uri = lambda data: "data:image/png;base64," + base64.b64encode(data).decode()     # noqa: E731
    html = (f'<main id="rp-content"><figure data-rp-id="image-1"><img src="{uri(first)}"></figure>'
            f'<div data-rp-id="drawio-2"><img class="rp-diagram-light" src="{uri(chosen)}"></div></main>').encode()
    write(d / ".rapport/blocks.json", json.dumps({"blocks": {
        "image-1": {"module": "image", "src": "![Setup](assets/setup.png)"},
        "drawio-2": {"module": "drawio", "src": '::: drawio assets/flow.drawio {caption = "The pipeline"}'}}}))
    assert prep.named_figure(d, "flow.drawio", html) == ("image/png", chosen)
    assert prep.named_figure(d, "The pipeline", html) == ("image/png", chosen)
    assert prep.named_figure(d, "Setup", html) == ("image/png", first)
    assert prep.named_figure(d, "nothing like it", html) is None


def test_thumbnail_that_matches_nothing_warns(tree):
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\n')
    (a / "report.md").write_text('+++\nthumbnail = "assets/gone.png"\n+++\nText.\n', encoding="utf-8")
    hub = make(home, "hub", HUB, "::: reports\n:::\n")
    res = build(hub)
    assert res.ok and any("thumbnail 'assets/gone.png' names no image file or figure" in w for w in res.warnings)


def test_age_is_never_negative():
    from datetime import datetime, timedelta
    prep = _prepare()
    now = datetime.now()
    assert prep._age((now + timedelta(days=2)).isoformat(), now) == 0
    assert prep._age(None, now) > 1000
    rows = [{"active": (now + timedelta(days=3)).isoformat(timespec="seconds"), "serving": None}]
    assert [g["id"] for g in prep.groups(rows)] == ["today"]          # not lost from every group


def test_named_figure_stays_inside_the_matched_block(isolated_home):
    import base64
    prep = _prepare()
    d = make(isolated_home, "work/r")
    uri = "data:image/png;base64," + base64.b64encode(b"C" * 2000).decode()
    html = (f'<main id="rp-content"><table data-rp-id="table-1"><tr><td>x</td></tr></table>'
            f'<figure data-rp-id="image-2"><img src="{uri}"></figure></main>').encode()
    write(d / ".rapport/blocks.json", json.dumps({"blocks": {
        "table-1": {"module": "table", "src": "| results table |", "lines": [3, 5]},
        "image-2": {"module": "image", "src": "![Later](assets/later.png)", "lines": [7, 7]}}}))
    assert prep.named_figure(d, "results table", html) is None          # not the next figure down the page
    write(d.parent / "outside.png", "x" * 3000)
    assert prep.named_figure(d, "../outside.png", html) is None         # only files in the report folder


FAKE_BROWSER = """#!/bin/sh
for a in "$@"; do case "$a" in --screenshot=*) out="${a#--screenshot=}";; esac; done
[ -n "$WRITE" ] && printf '\\211PNG fake screenshot bytes %0500d' 0 > "$out"
exec sleep 60
"""


def test_screenshot_waits_for_the_file_then_ends_the_browser(tmp_path, monkeypatch):
    prep = _prepare()
    exe = write(tmp_path / "browser", FAKE_BROWSER)
    exe.chmod(0o755)
    page = write(tmp_path / "report.html", "<p>x</p>")
    monkeypatch.setenv("WRITE", "1")
    t = time.time()
    shot = prep.screenshot(str(exe), page, timeout=10)
    assert shot and shot[0] == "image/png" and shot[1].startswith(b"\x89PNG") and time.time() - t < 5
    monkeypatch.delenv("WRITE")
    t = time.time()
    assert prep.screenshot(str(exe), page, timeout=1) is None and time.time() - t < 3


def test_hub_screenshots_a_report_without_figures(tree, monkeypatch):
    home, a, b, c = tree
    monkeypatch.delenv("RAPPORT_TOOLS", raising=False)
    monkeypatch.setenv("WRITE", "1")
    exe = write(home / "bin/browser", FAKE_BROWSER)
    exe.chmod(0o755)
    write(home / ".config/rapport/config.toml", f'[hub]\nroots = ["~/work"]\n\n[modules.reports]\nbrowser = "{exe}"\n'
                                                f'thumbnails = true\n')
    (a / "report.html").write_text('<main id="rp-content"><p>text only</p></main>', encoding="utf-8")
    hub = make(home, "hub", HUB, "::: reports\n:::\n")
    assert build(hub).ok
    html = (hub / "report.html").read_text()
    assert html.count('<img src="data:image/') == 1 and 'data-pending="1"' not in html   # a: screenshot; b, c: tiles


def test_reveal_shows_the_folder_and_routes_skip_the_page_itself(tree, monkeypatch):
    import subprocess
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~"]\n')
    hub = make(home, "hub", HUB, "::: reports\n:::\n")
    calls = []

    class Popen:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    httpd, app, url, cfg = make_server(hub, port=0, pinned=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        api = url + "api/m/reports/"
        assert req(api + "reveal", "POST", {"dir": str(a)}, app.token)[0] == 200
        assert calls and calls[-1][-1] == str(a) and (calls[-1][:2] == ["open", "-R"] or calls[-1][0] == "xdg-open")
        code, res = req(api + "stop", "POST", {"dir": str(hub)}, app.token)      # the hub can't stop itself
        assert code == 400 and "not a report this page lists" in res["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_examples_never_list_your_reports(tree, capsys, monkeypatch):
    home, a, b, c = tree
    write(home / ".config/rapport/config.toml", '[hub]\nroots = ["~/work"]\n')
    monkeypatch.chdir(home)
    assert main(["gallery", "--out", str(home / "gallery")]) == 0
    html = (home / "gallery/report.html").read_text()
    assert 'data-rp-module="reports"' in html and str(a) not in html and "No reports found" in html


def test_watch_resolves_against_the_report_folder(isolated_home):
    from rapport_core.config import load_for_report
    from rapport_core.render import Context, Renderer
    d = make(isolated_home, "r")
    cfg, _ = load_for_report(d)
    r = Renderer(cfg)
    Context(r).watch("notes/data.json")
    assert (d / "notes/data.json").resolve() in r.asset_paths


def test_hub_status_and_stop_when_not_running(isolated_home, capsys):
    write(isolated_home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\n')
    assert main(["hub", "--status"]) == 1 and "no server running" in capsys.readouterr().out
    assert main(["hub", "--stop"]) == 0


@pytest.fixture
def hub_start(isolated_home, monkeypatch):
    """`rapport hub` with the background start recorded instead of run; nothing listens."""
    from rapport_core import serve
    started = []
    monkeypatch.setattr(serve, "detach", lambda d, args: started.append(args.port) or 0)
    monkeypatch.setattr(serve, "running", lambda d: None)
    monkeypatch.setattr(serve, "port_taken", lambda host, port: False)
    write(isolated_home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\n')
    return isolated_home, started


def test_hub_takes_its_port_and_no_other(hub_start):
    home, started = hub_start
    assert main(["hub", "--no-open"]) == 0 and started == [4840]
    assert "[serve]" not in (home / "hub/report.md").read_text()      # the port is [hub] port's alone
    # an older hub page pinned 4840 in its front matter, and server.json remembers 4841
    write(home / "hub/report.md", reports.HUB_TEMPLATE.replace("+++\n\n", "\n[serve]\nport = 4840\n+++\n\n"))
    write(home / "hub/.rapport/server.json", '{"port": 4841}')
    write(home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\nport = 6969\n')
    assert main(["hub", "--no-open"]) == 0 and started[-1] == 6969
    assert main(["hub", "--no-open", "--port", "7000"]) == 0 and started[-1] == 7000


def test_hub_moves_to_a_changed_port(hub_start, monkeypatch, capsys):
    from rapport_core import serve
    home, started = hub_start
    stopped = []
    monkeypatch.setattr(serve, "running", lambda d: None if stopped else {"port": 4840, "url": "http://127.0.0.1:4840/"})
    monkeypatch.setattr(serve, "stop", lambda d: stopped.append(d) or 0)
    assert main(["hub", "--no-open", "--port", "4840"]) == 0 and started == [] and not stopped    # it runs there
    write(home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\nport = 6969\n')
    assert main(["hub", "--no-open"]) == 0 and stopped and started == [6969]
    assert "moving the hub from port 4840 to 6969" in capsys.readouterr().out


def test_hub_refuses_a_port_another_program_holds(hub_start, monkeypatch, capsys):
    import socket
    from rapport_core import serve
    home, started = hub_start
    monkeypatch.setattr(serve, "port_taken", port_taken)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        write(home / ".config/rapport/config.toml", f'[hub]\ndir = "~/hub"\nport = {port}\n')
        assert main(["hub", "--no-open"]) == 2 and started == []
    err = capsys.readouterr().err
    assert f"port {port} is held by another program" in err and f"lsof -nP -iTCP:{port}" in err
    assert not port_taken("127.0.0.1", port)          # free again once it lets go


def test_hub_port_must_be_a_port(hub_start, capsys):
    home, started = hub_start
    write(home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\nport = "6969"\n')
    assert main(["hub", "--no-open"]) == 2 and started == []
    assert "[hub] port = '6969' isn't a port number" in capsys.readouterr().err
