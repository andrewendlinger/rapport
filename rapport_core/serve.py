"""The review server: serves the report with the serve-only modules (feedback,
live reload), rebuilds when sources change, and stores reader feedback.

Security: binds 127.0.0.1 by default, checks the Host header on every request
(DNS rebinding), and requires a per-report token on every write (CSRF). The
token persists in server.json in the state folder (.rapport/, git-ignored), so
a page left open across a restart keeps working; a page holding a stale token
gets 403 and reloads itself.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ROOT
from . import config as config_mod
from .build import build
from .changes import changes
from .feedback import NotFound, Store, check_content, make_item, update_fields
from .util import STATE_FILE, RapportError, atomic_write, ensure_state_dir, read_state, state_dir, unique

SERVER_FILE = "server.json"
LOG_FILE = "server.log"
PORT_TRIES = 10


# ── watching ──────────────────────────────────────────────────────────────── #
MODULE_SUFFIXES = (".toml", ".html", ".css", ".js", ".py", ".md")


def config_files(report_dir: Path) -> list[Path]:
    """The config files every build reads, whether they exist yet or not."""
    user = config_mod.user_paths()
    files = [p for p in (user.config_file, user.learned_file) if p]
    for d in config_mod.project_dirs_for(report_dir):
        files += [d / config_mod.PROJECT_FILE, d / config_mod.LEARNED_PROJECT]
    return files


def fallback_files(report_dir: Path) -> list[Path]:
    """What to watch before a build has worked out the full list."""
    return [report_dir / "report.md"] + config_files(report_dir)


def watched_files(report_dir: Path, cfg, assets=()) -> list[Path]:
    """Everything a rebuild depends on. Missing files are listed too (they
    show up as changed once created), and so are the module folders, whose
    mtime moves when a module or file is added."""
    report_dir = report_dir.resolve()
    state = state_dir(report_dir)
    files = [cfg.source_path, state / STATE_FILE] + config_files(report_dir)
    for root in cfg.registry.roots:
        files.append(root)
        if root.is_dir():
            files += [p for p in root.glob("*") if p.is_dir()] + [p for p in root.glob("*/*") if p.is_dir()]
            files += [p for p in root.rglob("*") if p.is_file() and p.suffix in MODULE_SUFFIXES]
    asset_dir = report_dir / "assets"
    files.append(asset_dir)
    if asset_dir.is_dir():
        files += [p for p in asset_dir.rglob("*")]
    files += [Path(a) for a in assets]
    for layer in cfg.layers:
        if layer.path and layer.path.suffix == ".toml":
            files.append(layer.path)
    for a in cfg.module("feedback").get("actions") or []:
        if isinstance(a, dict) and a.get("instruction_file"):
            files.append(Path(a["instruction_file"]))
    # the build's own output must never trigger a build
    output = cfg.output_path.resolve()
    return unique(f for f in map(Path, files)
                  if f != output and not (f.is_relative_to(state) and f.name != STATE_FILE))


def signature(files: list[Path]) -> dict:
    sig = {}
    for f in files:
        try:
            st = f.stat()
            sig[str(f)] = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig[str(f)] = None
    return sig


def settle(before: dict, files: list[Path]) -> dict:
    """The signature to compare against after a build: stamps taken *before*
    the build for files we already watched (an edit during the build then
    still counts as a change), fresh stamps for files the build added."""
    after = signature(files)
    return {k: before[k] if k in before else v for k, v in after.items()}


class Watcher:
    """Calls rebuild() when a watched file changes. A tick only stats the files it
    knows; the list itself comes back from rebuild(), since working it out walks
    every module folder. sig=None: the first tick rebuilds."""

    def __init__(self, rebuild, files: list[Path], sig: dict | None = None):
        self.rebuild, self.files, self.sig = rebuild, files, sig

    def tick(self) -> bool:
        sig = signature(self.files)
        if sig == self.sig:
            return False
        self.sig = sig                  # if rebuild() raises, the next tick doesn't retry the same change
        self.files = self.rebuild() or self.files
        self.sig = settle(sig, self.files)
        return True


# ── app state ───────────────────────────────────────────────────────────────── #
class App:
    def __init__(self, report_dir: Path, sets, token: str):
        self.report_dir = report_dir
        self.sets = sets
        self.token = token
        self.store = Store(report_dir)
        self.lock = threading.Lock()
        self.html = b""
        self.build_id = 0
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.cfg = None
        self.blocks: dict = {}
        self.files: list[Path] = []
        self.watcher = Watcher(self._rebuild_files, fallback_files(report_dir))
        self.allowed_hosts: set[str] = set()

    def rebuild(self) -> None:
        try:
            res = build(self.report_dir, self.sets,
                        serve_data={"serve": {"token": self.token, "api": "/api", "build_id": self.build_id + 1}})
            with self.lock:
                self.build_id += 1
                self.html = res.served_html.encode("utf-8")
                self.errors = [f"{d.format(res.config.get('report.source'))}" for d in res.errors]
                old, self.warnings = self.warnings, res.warnings + res.notes()
                self.cfg = res.config
                self.blocks = res.blocks
                self.files = watched_files(self.report_dir, res.config, res.assets)
            stamp = time.strftime("%H:%M:%S")
            msg = f"[{stamp}] built v{read_state(self.report_dir).get('version', 1)}"
            print(msg + (f" with {len(self.errors)} error(s)" if self.errors else ""), flush=True)
            for e in self.errors:
                print(f"  error: {e}", flush=True)
            if set(self.warnings) != set(old):      # warnings show when they change, not on every rebuild
                for w in res.warnings:
                    print(f"  warning: {w}", flush=True)
                for n in res.notes():
                    print(f"  {n}", flush=True)
                if not self.warnings:
                    print("  warnings cleared", flush=True)
        except RapportError as e:
            self._fail(str(e))
        except Exception:  # noqa: BLE001 - keep serving the last good page
            self._fail("internal error during build:\n" + traceback.format_exc(limit=6))

    def _fail(self, message: str) -> None:
        # build_id only moves on a successful build: the served page still
        # carries the old id, and bumping it here would make it reload forever
        with self.lock:
            self.errors = [message]
            if not self.html:
                self.html = _error_page(message).encode("utf-8")
        print(f"build failed: {message}", flush=True)

    def _rebuild_files(self) -> list[Path]:
        self.rebuild()
        return self.files

    def watch(self, interval: float) -> None:
        while True:
            time.sleep(interval)
            try:
                self.watcher.tick()
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def feedback(self) -> dict:
        """The items, plus what changed since the last feedback round (the page's change bars)."""
        rev, items = self.store.rev(), self.store.items()
        with self.lock:
            blocks = self.blocks
        return {"items": items, "rev": rev, "changes": changes(self.report_dir, blocks, items) if blocks else None}

    def meta(self) -> dict:
        with self.lock:
            return {"build_id": self.build_id, "errors": self.errors,
                    "version": read_state(self.report_dir).get("version", 1),
                    "feedback_rev": self.store.rev(), "dir": str(self.report_dir)}


def _error_page(message: str) -> str:
    import html
    return ("<!doctype html><meta charset=utf-8><title>rapport: build error</title>"
            "<body style='font:14px/1.5 ui-monospace,monospace;padding:32px;white-space:pre-wrap'>"
            f"<b>rapport could not build this report</b>\n\n{html.escape(message)}\n\n"
            "Fix the source; this page reloads on the next successful build.</body>"
            "<script>setInterval(function(){fetch('/api/meta').then(function(r){return r.json()})"
            ".then(function(m){if(!m.errors.length)location.reload()})},1000)</script>")


# ── HTTP ─────────────────────────────────────────────────────────────────────── #
class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "rapport"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host in self.app.allowed_hosts:
            return True
        self._json({"error": "bad host"}, 403)
        return False

    def _token_ok(self) -> bool:
        if secrets.compare_digest(self.headers.get("X-Rapport-Token", ""), self.app.token):
            return True
        self._json({"error": "stale or missing token; reload the page"}, 403)
        return False

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise RapportError("bad Content-Length") from None
        if n < 0 or n > 1_000_000:
            raise RapportError("request too large")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise RapportError("invalid JSON") from None
        if not isinstance(data, dict):
            raise RapportError("expected a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with self.app.lock:
                body = self.app.html
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/meta":
            self._json(self.app.meta())
        elif path == "/api/feedback":      # fails only on a broken feedback.json: the server's problem
            self._answer(self.app.feedback, 500)
        else:
            self._send(404, b"not found", "text/plain")

    def _answer(self, fn, refused: int = 400) -> None:
        """fn()'s result as JSON; its errors as {"error": …}, a RapportError with status `refused`."""
        try:
            self._json(fn())
        except NotFound as e:
            self._json({"error": str(e)}, 404)
        except RapportError as e:
            self._json({"error": str(e)}, refused)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"server error: {e}"}, 500)

    def _mutate(self, fn) -> None:
        if self._host_ok() and self._token_ok():
            self._answer(fn)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/api/feedback":
            self._send(404, b"not found", "text/plain")
            return

        def create():
            payload = self._body()
            with self.app.lock:
                cfg, blocks = self.app.cfg, self.app.blocks
            if cfg is None:
                raise RapportError("the report has not built yet")
            version = read_state(self.app.report_dir).get("version", 1)
            return self.app.store.add(make_item(payload, cfg, blocks, version), blocks)
        self._mutate(create)

    def _item_id(self) -> str:
        parts = self.path.split("?")[0].rstrip("/").split("/")
        if len(parts) != 4 or parts[1:3] != ["api", "feedback"]:
            raise NotFound("not found")
        return parts[3]

    def do_PATCH(self) -> None:  # noqa: N802
        def patch():
            cfg = self.app.cfg
            if cfg is None:
                raise RapportError("the report has not built yet")
            item_id = self._item_id()
            fields = update_fields(self._body(), cfg)
            return self.app.store.update(item_id, fields, validate=lambda it: check_content(
                it.get("actions") or [], it.get("comment") or "", cfg))
        self._mutate(patch)

    def do_DELETE(self) -> None:  # noqa: N802
        def delete():
            self.app.store.delete(self._item_id())
            return {"ok": True}
        self._mutate(delete)


# ── server lifecycle ─────────────────────────────────────────────────────────── #
def server_file(report_dir: Path) -> Path:
    return state_dir(report_dir) / SERVER_FILE


def write_server(report_dir: Path, info: dict) -> None:
    atomic_write(ensure_state_dir(report_dir) / SERVER_FILE, json.dumps(info, indent=2) + "\n")


def read_server(report_dir: Path) -> dict:
    try:
        return json.loads(server_file(report_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def running(report_dir: Path) -> dict | None:
    """The live server for this report, verified over HTTP (stale pid files lie)."""
    info = read_server(report_dir)
    if not info.get("url") or not pid_alive(info.get("pid")):
        return None
    try:
        with urllib.request.urlopen(info["url"] + "api/meta", timeout=1.5) as r:
            meta = json.loads(r.read())
        if Path(meta.get("dir", "")).resolve() == report_dir.resolve():
            return info
    except (OSError, ValueError):
        pass
    return None


def bind(host: str, port: int, pinned: bool, report_dir: Path, handler=None) -> ThreadingHTTPServer:
    """Preferred port, then the next few, then an OS-assigned one (unless pinned)."""
    tries = [port] if pinned else list(range(port, port + PORT_TRIES)) + [0]
    last = None
    for p in tries:
        try:
            return ThreadingHTTPServer((host, p), handler or Handler)
        except OSError as e:
            last = e
            if e.errno == errno.EPERM or (e.errno == errno.EACCES and p == 0):
                raise RapportError(
                    f"not allowed to open a local port ({e.strerror}); this environment looks sandboxed. "
                    f"Ask the user to run, in their own terminal:\n  {ROOT / 'bin' / 'rapport'} serve '{report_dir}'"
                ) from None
            if pinned:
                raise RapportError(f"port {p} unavailable ({e.strerror}); pick another or omit --port") from None
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    raise RapportError(f"could not bind any port: {last}")


def make_server(report_dir: Path, sets=None, port: int | None = None, pinned: bool = False):
    """Bind, build once, and write server.json. Returns (httpd, app, url, cfg)."""
    report_dir = report_dir.resolve()
    cfg, _ = config_mod.load_for_report(report_dir, sets)
    host = cfg.get("serve.host", "127.0.0.1")
    port = int(cfg.get("serve.port", 4848)) if port is None else port
    token = read_server(report_dir).get("token") or secrets.token_urlsafe(18)
    app = App(report_dir, sets, token)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = bind(host, port, pinned, report_dir, handler)
    port = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown}:{port}/"
    app.allowed_hosts = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]", shown)}
    app.rebuild()
    app.watcher.files = app.files or app.watcher.files
    app.watcher.sig = signature(app.watcher.files)
    write_server(report_dir, {"pid": os.getpid(), "url": url, "port": port, "token": token, "dir": str(report_dir),
                              "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return httpd, app, url, cfg


def run(report_dir: Path, args) -> int:
    report_dir = report_dir.resolve()
    existing = running(report_dir)
    if existing:
        print(f"already serving {report_dir}\n→ {existing['url']}")
        return 0
    sets = getattr(args, "set", None)
    httpd, app, url, cfg = make_server(report_dir, sets, args.port, bool(args.port))
    wanted = args.port or int(cfg.get("serve.port", 4848))
    interval = max(0.1, int(cfg.get("serve.watch_ms", 400)) / 1000)
    threading.Thread(target=app.watch, args=(interval,), daemon=True).start()
    if httpd.server_address[1] != wanted:
        print(f"port {wanted} busy → using {httpd.server_address[1]}")
    print(f"rapport review server · {cfg.source_path}\n→ {url}   (Ctrl-C to stop)", flush=True)
    if cfg.get("serve.open_browser", True) and not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        info = read_server(report_dir)
        if info.get("pid") == os.getpid():
            info["pid"] = None  # keep url/token: open pages keep their token across restarts
            write_server(report_dir, info)
        print("stopped", flush=True)
    return 0


def detach(report_dir: Path, args) -> int:
    report_dir = report_dir.resolve()
    existing = running(report_dir)
    if existing:
        print(f"already serving {report_dir}\n→ {existing['url']}")
        return 0
    cfg, _ = config_mod.load_for_report(report_dir, getattr(args, "set", None))
    log = ensure_state_dir(report_dir) / LOG_FILE
    cmd = [sys.executable, str(ROOT / "scripts" / "rapport.py"), "serve", str(report_dir), "--no-open"]
    if args.port:
        cmd += ["--port", str(args.port)]
    for s in getattr(args, "set", None) or []:
        cmd += ["--set", s]
    with open(log, "ab") as fh:     # our cwd: relative --set paths resolve as they would in the foreground
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, cwd=os.getcwd())
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-8:]
            raise RapportError("server exited during start-up:\n  " + "\n  ".join(tail))
        info = read_server(report_dir)
        if info.get("pid") == proc.pid and running(report_dir):
            print(f"serving {report_dir} in the background (pid {proc.pid}, log {log})\n→ {info['url']}")
            if cfg.get("serve.open_browser", True) and not args.no_open:
                webbrowser.open(info["url"])
            return 0
        time.sleep(0.15)
    raise RapportError(f"server did not come up within 20 s; see {log}")


def _is_rapport_server(pid) -> bool:
    """Does this pid run `rapport … serve`? (A hung server doesn't answer HTTP.)"""
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True,
                             text=True, timeout=3).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return "rapport" in out and " serve" in out


def stop(report_dir: Path) -> int:
    report_dir = report_dir.resolve()
    info = read_server(report_dir)
    pid = info.get("pid")
    live = running(report_dir)
    if not live and not (pid_alive(pid) and _is_rapport_server(pid)):
        # never signal a pid we can't confirm: after a crash or reboot the
        # number may belong to an unrelated process
        if pid:
            info["pid"] = None
            write_server(report_dir, info)
            print(f"no server running for {report_dir} (cleared stale pid {pid})")
        else:
            print(f"no server running for {report_dir}")
        return 0
    pid = (live or info)["pid"]
    os.kill(int(pid), signal.SIGTERM)
    for _ in range(40):
        if not pid_alive(pid):
            print(f"stopped server (pid {pid})")
            return 0
        time.sleep(0.1)
    print(f"server (pid {pid}) is still running 4 s after SIGTERM; run --stop again, or kill {pid}", file=sys.stderr)
    return 1


def status(report_dir: Path) -> int:
    report_dir = report_dir.resolve()
    info = running(report_dir)
    if info:
        print(f"serving {report_dir}\n→ {info['url']}  (pid {info['pid']})")
        return 0
    print(f"no server running for {report_dir}")
    return 1


def watch_loop(report_dir: Path, sets) -> int:
    """`rapport build --watch`: rebuild on change, print problems."""
    report_dir = report_dir.resolve()
    interval = 0.4

    def rebuild() -> list[Path]:
        nonlocal interval
        try:
            res = build(report_dir, sets)
        except RapportError as e:       # reported once; config files stay watched for the fix
            print(f"  error: {e}", flush=True)
            return fallback_files(report_dir)
        interval = max(0.1, int(res.config.get("serve.watch_ms", 400)) / 1000)
        print(f"[{time.strftime('%H:%M:%S')}] built {res.config.output_path.name}"
              + (f" with {len(res.errors)} error(s)" if res.errors else ""), flush=True)
        for m in res.messages():
            print("  " + m, flush=True)
        return watched_files(report_dir, res.config, res.assets)

    print(f"watching {report_dir} (Ctrl-C to stop)")
    watcher = Watcher(rebuild, fallback_files(report_dir))
    try:
        while True:
            try:
                watcher.tick()
            except RapportError as e:   # e.g. the user config moved away under us
                print(f"  error: {e}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


__all__ = ["run", "detach", "stop", "status", "watch_loop"]
