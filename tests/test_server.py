import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rapport_core.feedback import Store
from rapport_core import serve
from rapport_core.serve import idle_stop, make_server, read_server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server(report):
    d = report("## Summary\n\nA paragraph to comment on.\n")
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield d, app, url
    httpd.shutdown()
    httpd.server_close()


def req(url, method="GET", body=None, token=None, host=None):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-Rapport-Token", token)
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}") if "json" in resp.headers.get("Content-Type", "") \
                else resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_serves_page_with_feedback_layer(server):
    d, app, url = server
    code, body = req(url)
    assert code == 200 and 'Rapport.module("feedback"' in body and app.token in body


def test_writes_need_token_and_host(server):
    d, app, url = server
    para = next(k for k, v in app.blocks.items() if v["module"] == "paragraph")
    payload = {"target": {"kind": "text", "blocks": [para], "quote": "A paragraph"}, "actions": ["verbose"]}
    assert req(url + "api/m/feedback/", "POST", payload)[0] == 403
    assert req(url + "api/meta", host="evil.example:80")[0] == 403
    code, item = req(url + "api/m/feedback/", "POST", payload, app.token)
    assert code == 200 and item["id"] == "FB-1" and item["target"]["lines"] == [3, 3]
    code, item = req(url + "api/m/feedback/FB-1", "PATCH", {"comment": "shorter"}, app.token)
    assert code == 200 and item["comment"] == "shorter"
    assert req(url + "api/m/feedback/", "POST", {"target": {"kind": "report"}}, app.token)[0] == 400
    assert req(url + "api/m/feedback/FB-1", "DELETE", None, app.token)[0] == 200
    code, body = req(url + "api/m/feedback/")
    assert code == 200 and body["items"] == [] and sorted(body) == ["changes", "items"]


def test_feedback_answer_carries_the_changes_of_the_last_round(server):
    d, app, url = server
    para = next(k for k, v in app.blocks.items() if v["module"] == "paragraph")
    payload = {"target": {"kind": "text", "blocks": [para], "quote": "A paragraph"}, "actions": ["verbose"]}
    code, item = req(url + "api/m/feedback/", "POST", payload, app.token)
    assert code == 200 and item["round"] == 1
    assert req(url + "api/m/feedback/")[1]["changes"] is None
    (d / "report.md").write_text("## Summary\n\nShorter.\n", encoding="utf-8")
    app.rebuild()
    Store(d).resolve([item["id"]], "cut it")
    ch = req(url + "api/m/feedback/")[1]["changes"]
    assert [c["items"] for c in ch["changed"]] == [[item["id"]]]
    assert app.blocks[ch["changed"][0]["id"]]["src"] == "Shorter."


def test_feedback_is_a_module_and_keeps_its_file(server):
    d, app, url = server
    with pytest.raises(urllib.error.HTTPError, match="404"):                    # moved to /api/m/feedback/
        urllib.request.urlopen(url + "api/feedback", timeout=5)
    meta = req(url + "api/meta")[1]
    assert "feedback_rev" not in meta and meta["revs"]["feedback"] == "0"
    code, item = req(url + "api/m/feedback/", "POST", {"target": {"kind": "report"}, "comment": "c"}, app.token)
    first = req(url + "api/meta")[1]["revs"]["feedback"]
    assert code == 200 and first == str((d / ".rapport" / "feedback.json").stat().st_mtime_ns)
    Store(d).resolve([item["id"]], "done")                                      # the agent, from the CLI
    assert req(url + "api/meta")[1]["revs"]["feedback"] != first


def test_files_named_by_path_settings_are_watched(report):
    from conftest import write
    d = report("x\n", {"reports/r/rapport-modules/notes/module.toml":
                       'name = "notes"\nkind = "feature"\nsummary = "Notes."\n\n[config.data]\ntype = "string"\n'
                       'default = ""\npath = true\ndoc = "A file the module reads."\n'})
    write(d / "rapport.toml", '[modules.notes]\ndata = "notes.txt"\n\n'
                              '[[modules.feedback.actions]]\nid = "cite"\nlabel = "Cite"\ninstruction_file = "cite.md"\n')
    write(d / "notes.txt", "one\n")
    write(d / "cite.md", "Add a source.\n")
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    httpd.server_close()
    assert not app.errors and {d.resolve() / "notes.txt", d.resolve() / "cite.md"} <= set(app.files)
    for name in ("cite.md", "notes.txt"):
        before = app.build_id
        write(d / name, "changed, and longer\n")
        assert app.watcher.tick() and app.build_id == before + 1, name


def test_rebuilds_on_change(server):
    d, app, url = server
    before = req(url + "api/meta")[1]["build_id"]
    th = threading.Thread(target=app.watch, args=(0.1,), daemon=True)
    th.start()
    time.sleep(0.3)
    (d / "report.md").write_text("## Summary\n\nChanged text.\n", encoding="utf-8")
    for _ in range(50):
        meta = req(url + "api/meta")[1]
        if meta["build_id"] > before:
            break
        time.sleep(0.1)
    assert meta["build_id"] > before and "Changed text." in req(url)[1]


def test_failed_build_keeps_build_id(server):
    d, app, url = server
    before = app.build_id
    (d / "report.md").write_text("+++\nbroken = \n+++\n", encoding="utf-8")
    app.rebuild()
    assert app.build_id == before and app.errors and "front matter" in app.errors[0]


def test_token_survives_restart(report):
    d = report("x\n")
    h1, app1, _, _ = make_server(d, port=0, pinned=True)
    h1.server_close()
    h2, app2, _, _ = make_server(d, port=0, pinned=True)
    h2.server_close()
    assert app1.token == app2.token == read_server(d)["token"]


def test_idle_server_stops_itself_but_not_while_used(report):
    d = report("x\n")
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        threading.Thread(target=idle_stop, args=(httpd, app, 0.6, 0.05), daemon=True).start()
        for _ in range(4):                      # page loads count as use: 1.2 s, still up
            time.sleep(0.3)
            assert req(url)[0] == 200
        deadline = time.time() + 3
        while t.is_alive() and time.time() < deadline:
            try:                                # an open page's polling doesn't
                urllib.request.urlopen(url + "api/meta", timeout=0.5).close()
            except OSError:                     # stopped: the socket stays open until server_close
                pass
            time.sleep(0.1)
        assert not t.is_alive()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert cfg.get("serve.idle_hours") == 3


def test_rerunning_serve_resets_the_idle_clock(server, capsys):
    d, app, url = server
    app.active = 0
    assert serve.run(d, None) == 0
    assert "already serving" in capsys.readouterr().out and app.active > 0


def test_detach_status_stop(report, isolated_home):
    d = report("x\n")
    env = {**os.environ, "HOME": str(isolated_home), "XDG_CONFIG_HOME": str(isolated_home / ".config")}
    cli = [str(ROOT / "bin" / "rapport")]
    out = subprocess.run(cli + ["serve", "--detach", "--no-open", "--port", "0", str(d)],
                         capture_output=True, text=True, env=env, timeout=60)
    try:
        assert out.returncode == 0, out.stderr
        assert "http://127.0.0.1:" in out.stdout
        again = subprocess.run(cli + ["serve", "--detach", "--no-open", str(d)], capture_output=True, text=True,
                               env=env, timeout=60)
        assert "already serving" in again.stdout
        st = subprocess.run(cli + ["serve", "--status", str(d)], capture_output=True, text=True, env=env, timeout=60)
        assert st.returncode == 0 and "serving" in st.stdout
    finally:
        stop = subprocess.run(cli + ["serve", "--stop", str(d)], capture_output=True, text=True, env=env, timeout=60)
    assert "stopped" in stop.stdout
    st = subprocess.run(cli + ["serve", "--status", str(d)], capture_output=True, text=True, env=env, timeout=60)
    assert st.returncode == 1


# ── regressions from the review ────────────────────────────────────────────── #
def test_input_validation(server):
    import http.client
    d, app, url = server
    para = next(k for k, v in app.blocks.items() if v["module"] == "paragraph")
    assert req(url + "api/m/feedback/", "POST", {"target": "x", "comment": "c"}, app.token)[0] == 400
    host, port = url.split("//")[1].rstrip("/").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/api/m/feedback/")
    conn.putheader("Content-Length", "abc")
    conn.putheader("X-Rapport-Token", app.token)
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()
    payload = {"target": {"kind": "text", "blocks": [para], "quote": "A paragraph"}, "actions": ["verbose"]}
    assert req(url + "api/m/feedback/", "POST", payload, app.token)[0] == 200
    assert req(url + "api/m/feedback/FB-1", "PATCH", {"comment": "", "actions": []}, app.token)[0] == 400
    assert req(url + "api/m/feedback/FB-99", "PATCH", {"comment": "x"}, app.token)[0] == 404
    assert req(url + "api/m/feedback/FB-99", "DELETE", None, app.token)[0] == 404


def test_stop_never_signals_an_unconfirmed_pid(report):
    from rapport_core.serve import server_file, stop
    d = report("x\n")
    bystander = subprocess.Popen(["sleep", "30"])
    try:
        server_file(d).parent.mkdir(parents=True, exist_ok=True)
        server_file(d).write_text(json.dumps({"pid": bystander.pid, "url": "http://127.0.0.1:9/", "token": "t"}))
        assert stop(d) == 0
        time.sleep(0.2)
        assert bystander.poll() is None, "stop killed an unrelated process"
        assert read_server(d)["pid"] is None and read_server(d)["token"] == "t"
    finally:
        bystander.kill()


def test_an_edit_during_a_build_is_not_lost(tmp_path):
    from rapport_core.serve import settle, signature
    f = tmp_path / "report.md"
    f.write_text("one")
    before = signature([f])
    time.sleep(0.01)
    f.write_text("two, written while the build ran")
    assert signature([f]) != settle(before, [f])      # → the next tick rebuilds


def test_watcher_sees_a_newly_created_asset(report):
    from rapport_core.build import build as build_
    from conftest import GIF
    d = report("![Demo](assets/demo.gif)\n")
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    try:
        assert app.errors and "asset not found" in app.errors[0]
        threading.Thread(target=app.watch, args=(0.05,), daemon=True).start()
        time.sleep(0.2)
        (d / "assets").mkdir(exist_ok=True)
        (d / "assets" / "demo.gif").write_bytes(GIF)
        deadline = time.time() + 5
        while app.errors and time.time() < deadline:
            time.sleep(0.05)
        assert not app.errors
        assert build_(d).ok
    finally:
        httpd.server_close()


def test_build_output_is_never_watched(report, isolated_home):
    from conftest import write
    from rapport_core.config import load_for_report
    from rapport_core.serve import watched_files
    d = report("x\n")
    write(d / "rapport.toml", '[modules]\npaths = ["."]\n')
    cfg, _ = load_for_report(d)
    files = watched_files(d, cfg)
    assert cfg.output_path not in files and d / ".rapport" / "blocks.json" not in files
    assert d / ".rapport" / "state.json" in files and d / "report.md" in files


def test_warnings_print_when_they_change(report, capsys):
    d = report("::: collapse More\nNever closed.\n")
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    httpd.server_close()
    assert "warning: report.md:1: ':::collapse' is never closed" in capsys.readouterr().out
    app.rebuild()
    assert "warning" not in capsys.readouterr().out
    (d / "report.md").write_text("::: collapse More\nClosed.\n:::\n", encoding="utf-8")
    app.rebuild()
    assert "warnings cleared" in capsys.readouterr().out


def _main_thread_sleep(monkeypatch, fake):
    """Swap time.sleep for this test's own loop only: earlier tests left watcher threads running."""
    real = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: fake(s) if threading.current_thread() is threading.main_thread()
                        else real(s))


def test_a_broken_feedback_file_gets_an_error_answer(server):
    d, app, url = server
    (d / ".rapport" / "feedback.json").write_text("{not json", encoding="utf-8")
    code, body = req(url + "api/m/feedback/")
    assert code == 500 and "is not valid JSON" in body["error"]


def test_stop_says_so_when_the_server_survives(report, monkeypatch, capsys):
    from rapport_core import serve
    d = report("x\n")
    serve.write_server(d, {"pid": 4242, "url": "http://127.0.0.1:9/", "token": "t"})
    monkeypatch.setattr(serve, "running", lambda _d: {"pid": 4242})
    monkeypatch.setattr(serve, "pid_alive", lambda _p: True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    _main_thread_sleep(monkeypatch, lambda s: None)
    assert serve.stop(d) == 1
    assert "still running" in capsys.readouterr().err


def test_build_watch_loads_config_only_when_it_rebuilds(report, monkeypatch, capsys):
    from rapport_core import config as config_mod
    from rapport_core import serve
    d = report("x\n")
    loads, ticks = [], []
    real_load = config_mod.load_for_report
    monkeypatch.setattr(config_mod, "load_for_report", lambda *a, **k: loads.append(1) or real_load(*a, **k))

    def sleep(_s):
        ticks.append(1)
        if len(ticks) == 3:
            (d / "report.md").write_text("y, changed\n", encoding="utf-8")
        if len(ticks) == 6:
            raise KeyboardInterrupt
    _main_thread_sleep(monkeypatch, sleep)
    assert serve.watch_loop(d, None) == 0
    assert capsys.readouterr().out.count("built report.html") == 2      # the first build, then the edit
    assert len(loads) == 2                                              # not once per tick
