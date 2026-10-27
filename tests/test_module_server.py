"""Modules that extend the review server: server.py, /api/m/<module>/…, module state."""

import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import write
from rapport_core.cli import main
from rapport_core.serve import make_server
from rapport_core.store import Corrupt, module_store
from test_server import req

ROOT = Path(__file__).resolve().parent.parent
TEXT = "## Notes\n\nA paragraph.\n"

MANIFEST = 'name = "notes"\nkind = "feature"\nsummary = "Notes."\nwhen = "{when}"\n\n' \
           '[config.limit]\ntype = "int"\ndefault = 3\ndoc = "Most notes."\n'

NOTES = '''
def routes(r):
    r.get("/", show)
    r.post("/items", add)
    r.get("/items/new", blank)
    r.get("/items/{id}", one)
    r.delete("/items/{id}", drop)
    r.get("/boom", boom)
    r.get("/odd", odd)
    r.get("/sneaky", sneaky)
    r.get("/context", context)


def show(req):
    return {"items": req.store.read().get("items", {}), "query": req.query}


def add(req):
    text = req.body.get("text")
    if not isinstance(text, str) or not text:
        raise req.Refused("text must be a non-empty string")
    with req.store.edit() as state:
        n = state.get("next", 1)
        state["next"] = n + 1
        state.setdefault("items", {})[str(n)] = text
    return {"id": str(n)}


def blank(req):
    return {"blank": True}


def one(req):
    items = req.store.read().get("items", {})
    if req.params["id"] not in items:
        raise req.NotFound(f"no item {req.params['id']}")
    return {"id": req.params["id"], "text": items[req.params["id"]]}


def drop(req):
    with req.store.edit() as state:
        if state.get("items", {}).pop(req.params["id"], None) is None:
            raise req.NotFound("no such item")


def boom(req):
    return 1 / 0


def odd(req):
    return {1, 2}


def sneaky(req):
    with req.store.edit() as state:
        state["x"] = 1


def context(req):
    return {"method": req.method, "config": req.config, "title": req.full_config.get("meta.title"),
            "version": req.version, "blocks": sorted(b["module"] for b in req.blocks.values()),
            "dir": str(req.report_dir)}


def describe(state, config):
    return f"{len(state.get('items', {}))} note(s), at most {config['limit']}"
'''


def module_files(name="r", server=NOTES, when="serve", module="notes"):
    base = f"reports/{name}/rapport-modules/{module}"
    return {f"{base}/module.toml": MANIFEST.replace('"notes"', f'"{module}"').format(when=when),
            f"{base}/server.py": server}


@pytest.fixture
def served(report):
    servers = []

    def start(files=None, sets=None, name="r"):
        d = report('+++\ntitle = "Notes"\n+++\n' + TEXT, module_files(name) if files is None else files, name=name)
        httpd, app, url, cfg = make_server(d, sets, port=0, pinned=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return d, app, url
    yield start
    for h in servers:
        h.shutdown()
        h.server_close()


def raw(url, method, path, body: bytes, headers: dict):
    host, port = url.split("//")[1].rstrip("/").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest(method, path)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders(body)
    resp = conn.getresponse()
    out = resp.status, json.loads(resp.read() or b"null")
    conn.close()
    return out


# ── dispatch ──────────────────────────────────────────────────────────────────── #
def test_routes_dispatch_with_path_params(served):
    d, app, url = served()
    api = url + "api/m/notes"
    assert req(api + "/") == (200, {"items": {}, "query": {}})
    assert req(api + "?a=1&b=2")[1]["query"] == {"a": "1", "b": "2"}       # no trailing slash: the root
    assert req(api + "/items", "POST", {"text": "first"}, app.token) == (200, {"id": "1"})
    assert req(api + "/items/1") == (200, {"id": "1", "text": "first"})
    assert req(api + "/items/1/") == (200, {"id": "1", "text": "first"})
    assert req(api + "/items/new") == (200, {"blank": True})                # a literal beats a {param}
    assert req(api + "/items/a%20b") == (404, {"error": "no item a b"})      # params arrive decoded
    assert req(api + "/items/1", "DELETE", None, app.token) == (200, None)
    assert req(api + "/items/1")[0] == 404
    code, ctx = req(api + "/context")
    assert code == 200 and ctx == {"method": "GET", "config": {"enabled": True, "limit": 3}, "title": "Notes",
                                   "version": 1, "blocks": ["header", "heading", "paragraph"],
                                   "dir": str(d.resolve())}


def test_module_routes_check_host_and_token(served):
    d, app, url = served()
    api = url + "api/m/notes/items"
    store = module_store(d, "notes")
    assert req(api, "POST", {"text": "x"})[0] == 403
    assert req(api, "POST", {"text": "x"}, "stale") == (403, {"error": "stale or missing token; reload the page"})
    assert req(api, "POST", {"text": "x"}, app.token, host="evil.example:80")[0] == 403
    assert req(url + "api/m/notes/", host="evil.example:80")[0] == 403
    assert not store.path.exists()                       # nothing ran
    assert req(url + "api/m/notes/")[0] == 200            # GET needs no token


def test_unknown_disabled_and_static_modules_answer_404(served):
    d, app, url = served()
    assert req(url + "api/m/nope/")[0] == 404
    assert req(url + "api/m/notes/zzz")[0] == 404
    code, body = req(url + "api/m/notes/items", "PUT", {}, app.token)
    assert code == 404 and "has no route PUT /items (that path takes POST)" in body["error"]
    d, app, url = served(sets=["modules.notes.enabled=false"], name="off")
    assert req(url + "api/m/notes/")[0] == 404 and req(url + "api/meta")[1]["revs"] == {}
    d, app, url = served(files=module_files("static", when="static"), name="static")
    assert req(url + "api/m/notes/")[0] == 404 and req(url + "api/meta")[1]["revs"] == {}


def test_bodies_are_checked_before_the_handler(served):
    d, app, url = served()
    path, token = "/api/m/notes/items", {"X-Rapport-Token": app.token}
    assert raw(url, "POST", path, b"{nope", {**token, "Content-Length": "5"}) == (400, {"error": "invalid JSON"})
    assert raw(url, "POST", path, b"[1]", {**token, "Content-Length": "3"}) == \
        (400, {"error": "expected a JSON object"})
    assert raw(url, "POST", path, b"", {**token, "Content-Length": "2000000"}) == \
        (400, {"error": "request too large"})
    assert raw(url, "POST", path, b"", {**token, "Content-Length": "abc"})[0] == 400
    assert req(url + "api/m/notes/items", "POST", {"text": ""}, app.token) == \
        (400, {"error": "text must be a non-empty string"})


def test_handler_errors_answer_500_with_a_traceback_in_the_log(served, capsys):
    d, app, url = served()
    code, body = req(url + "api/m/notes/boom")
    assert code == 500 and body["error"] == "server error: division by zero"
    log = capsys.readouterr().err
    assert "ZeroDivisionError" in log and "server.py" in log
    assert req(url + "api/m/notes/odd")[0] == 500                 # not JSON-serialisable
    code, body = req(url + "api/m/notes/sneaky")                  # a GET can't write: it needs no token
    assert code == 500 and "GET routes can't change state" in body["error"]
    assert not module_store(d, "notes").path.exists()
    module_store(d, "notes").path.parent.mkdir(parents=True)
    module_store(d, "notes").path.write_text("{broken", encoding="utf-8")
    code, body = req(url + "api/m/notes/")
    assert code == 500 and "is not valid JSON" in body["error"]


# ── state ─────────────────────────────────────────────────────────────────────── #
def test_state_revisions_show_in_meta_including_writes_from_outside(served):
    d, app, url = served()
    revs = lambda: req(url + "api/meta")[1]["revs"]      # noqa: E731
    assert revs() == {"notes": "0"}
    req(url + "api/m/notes/items", "POST", {"text": "from the page"}, app.token)
    first = revs()["notes"]
    assert first == str(module_store(d, "notes").rev()) != "0"
    script = ("import sys; from rapport_core.store import module_store\n"
              "with module_store(sys.argv[1], 'notes').edit() as s:\n"
              "    s.setdefault('items', {})['9'] = 'from another process'\n")
    subprocess.run([sys.executable, "-c", script, str(d)], cwd=ROOT, check=True, timeout=60)
    assert revs()["notes"] not in ("0", first)
    assert req(url + "api/m/notes/items/9")[1]["text"] == "from another process"


def test_store_edits_never_interleave(report):
    d = report("x\n")
    store = module_store(d, "count")

    def bump(n):
        for _ in range(n):
            with store.edit() as s:
                s["n"] = s.get("n", 0) + 1
    script = ("import sys; from rapport_core.store import module_store\n"
              "for _ in range(40):\n"
              "    with module_store(sys.argv[1], 'count').edit() as s:\n"
              "        s['n'] = s.get('n', 0) + 1\n")
    procs = [subprocess.Popen([sys.executable, "-c", script, str(d)], cwd=ROOT) for _ in range(2)]
    threads = [threading.Thread(target=bump, args=(40,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert store.read() == {"n": 240}
    with pytest.raises(RuntimeError, match="don't nest"):
        with store.edit():
            with store.edit():
                pass
    with pytest.raises(RuntimeError, match="GET routes"):
        with module_store(d, "count", readonly=True).edit():
            pass
    store.path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(Corrupt, match="must hold a JSON object"):
        store.read()


# ── server.py lifecycle ───────────────────────────────────────────────────────── #
def test_a_broken_server_py_is_reported_and_the_server_keeps_going(served):
    d, app, url = served(files=module_files(server="def routes(r):\n    r.get('/', show\n"))
    meta = req(url + "api/meta")[1]
    assert meta["errors"] == [app.errors[0]] and app.errors[0].startswith(
        "module 'notes' server.py failed to load: SyntaxError")
    assert req(url)[0] == 200 and req(url + "api/m/notes/")[0] == 404
    write(d / "rapport-modules/notes/server.py", NOTES)
    app.rebuild()
    assert app.errors == [] and req(url + "api/m/notes/")[0] == 200


def test_route_changes_apply_on_the_next_build(served):
    d, app, url = served()
    src = d / "rapport-modules/notes/server.py"
    assert src in app.files                               # the watcher watches server.py
    before = app.build_id
    write(src, 'def routes(r):\n    r.get("/v2", lambda req: {"v": 2})\n')
    assert app.watcher.tick() and app.build_id == before + 1
    assert not app.watcher.tick()                         # one edit, one rebuild: no __pycache__ in the folder
    assert not (src.parent / "__pycache__").exists()
    assert req(url + "api/m/notes/v2") == (200, {"v": 2}) and req(url + "api/m/notes/")[0] == 404
    write(src, "def routes(r):\n    r.get('items', print)\n")
    app.rebuild()                                         # registration errors are build errors…
    assert app.errors == ['module \'notes\' server.py: r.get(\'items\'): a path starts with "/", e.g. "/items/{id}"']
    assert req(url + "api/m/notes/v2")[0] == 404          # …and the module gets no routes


# ── CLI ───────────────────────────────────────────────────────────────────────── #
def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_modules_state_prints_what_describe_says(capsys, report):
    d = report(TEXT, module_files())
    assert run(capsys, "modules", "state", "notes", "-C", str(d)) == (0, "0 note(s), at most 3\n", "")
    with module_store(d, "notes").edit() as s:
        s["items"] = {"1": "first"}
    assert run(capsys, "modules", "state", "notes", str(d))[1] == "1 note(s), at most 3\n"
    code, out, err = run(capsys, "modules", "state", "notes", "--json", "-C", str(d))
    assert code == 0 and json.loads(out) == {"items": {"1": "first"}}
    write(d / "rapport-modules/notes/server.py", "def routes(r):\n    pass\n\n\ndef describe(state, config):\n"
                                                 "    return state['missing']\n")
    code, out, err = run(capsys, "modules", "state", "notes", "-C", str(d))
    assert code == 1 and json.loads(out) == {"items": {"1": "first"}}
    assert "describe() in server.py failed: KeyError" in err
    write(d / "rapport-modules/notes/server.py", "def routes(r):\n    pass\n")
    assert json.loads(run(capsys, "modules", "state", "notes", "-C", str(d))[1]) == {"items": {"1": "first"}}
    code, out, err = run(capsys, "modules", "state", "callout", "-C", str(d))
    assert code == 2 and "'callout' has no server.py, so it keeps no state" in err
    code, out, err = run(capsys, "modules", "state", "note", "-C", str(d))
    assert code == 2 and "did you mean 'notes'" in err


BROKEN = {
    "syntax": ("def routes(r)\n", "error: server.py failed to load: SyntaxError"),
    "no-routes": ("def describe(state, config):\n    return ''\n", "error: server.py has no routes(r) function"),
    "raises": ("def routes(r):\n    r.get('/', {}['x'])\n", "error: server.py: routes(r) failed: KeyError: 'x'"),
    "twice": ("def routes(r):\n    r.get('/a/{x}', print)\n    r.get('/a/{y}', print)\n",
              "error: server.py: r.get('/a/{y}'): registered twice"),
    "segment": ("def routes(r):\n    r.post('/a b', print)\n", "error: server.py: r.post('/a b'): bad segment 'a b'"),
    "handler": ("def routes(r):\n    r.get('/', 'show')\n",
                "error: server.py: r.get('/'): the handler must be a function, not str"),
}


def test_modules_check_reports_a_broken_server_py(capsys, report):
    files = {}
    for name, (src, _) in BROKEN.items():
        files.update(module_files(server=src, module=name))
    files.update(module_files(module="frozen", when="static"))
    d = report(TEXT, files)
    for name, (_, want) in BROKEN.items():
        code, out, err = run(capsys, "modules", "check", name, "-C", str(d))
        assert code == 1 and want in out, (name, out)
    code, out, err = run(capsys, "modules", "check", "frozen", "-C", str(d))
    assert code == 0 and 'warning: server.py is never used: modules with when = "static" get no routes' in out
    code, out, err = run(capsys, "modules", "info", "raises", "-C", str(d))
    assert "routes: error: server.py: routes(r) failed: KeyError: 'x'" in out


def test_server_scaffold_passes_check_and_works_end_to_end(capsys, report):
    d = report(TEXT)
    code, out, err = run(capsys, "modules", "new", "signoff", "--server", "--scope", "report", "-C", str(d))
    assert code == 0 and "with server routes" in out
    assert sorted(p.name for p in (d / "rapport-modules/signoff").iterdir()) == \
        ["module.toml", "script.js", "server.py", "style.css"]
    code, out, err = run(capsys, "modules", "check", "signoff", "-C", str(d))
    assert code == 0 and "signoff        ok" in out
    assert "[custom, runs server.py, serve only]" in run(capsys, "modules", "list", "-C", str(d))[1]
    info = run(capsys, "modules", "info", "signoff", "-C", str(d))[1]
    assert "DELETE /{who}" in info and "files: module.toml, style.css, script.js, server.py" in info
    httpd, app, url, cfg = make_server(d, port=0, pinned=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert 'Rapport.module("signoff"' in req(url)[1]
        api = url + "api/m/signoff/"
        assert req(api) == (200, {"signed": []})
        assert req(api, "POST", {"name": " "}, app.token) == (400, {"error": "type your name first"})
        code, body = req(api, "POST", {"name": "Ada"}, app.token)
        assert code == 200 and [s["name"] for s in body["signed"]] == ["Ada"]
        assert req(api + "Bob", "DELETE", None, app.token) == (404, {"error": "Bob has not signed off"})
        req(api, "POST", {"name": "Bob"}, app.token)
        assert req(api + "Bob", "DELETE", None, app.token)[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
    capsys.readouterr()                                   # the server's own log
    out = run(capsys, "modules", "state", "signoff", "-C", str(d))[1]
    assert out.startswith("Ada signed off on v1 at ") and "Bob" not in out
    assert run(capsys, "modules", "new", "poll", "--server", "--kind", "block", "-C", str(d))[0] == 2
    assert "already installed" in run(capsys, "modules", "new", "callout", "--server", "-C", str(d))[2]
