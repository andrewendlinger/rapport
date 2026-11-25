"""`rapport hub`: start, stop and show the hub, the report that lists every other one, and keep
it running with a macOS login agent (`--install`).

The agent is a LaunchAgent that runs `rapport hub --agent` through the user's login shell, so
the shell's profile supplies RAPPORT_CONFIG_DIR and PATH on every start and nothing is copied
into the plist (a moved venv or a `git pull` can't leave it stale). launchd starts it at login
and again when it crashes (KeepAlive on an unsuccessful exit). `rapport hub --stop` exits 0, so
it stays stopped until `rapport hub`, which then starts it through launchd rather than as a
second, unmanaged hub. Under `--agent` a start-up failure (port taken, broken config) also
exits 0, with one line in the log: launchd would otherwise retry it every 10 s for good.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import ROOT, reports, serve
from . import config as config_mod
from .util import RapportError, ensure_state_dir

LABEL = "io.github.andrewendlinger.rapport.hub"
# an always-on hub: no idle stop, and a slower look for changes (it watches every report)
AGENT_SETS = ["serve.idle_hours=0", "serve.watch_ms=2000"]
START_TIMEOUT = 30.0


def macos() -> bool:
    return sys.platform == "darwin"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def installed() -> bool:
    return macos() and plist_path().exists()


def service() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _launchctl_ok(*args: str) -> None:
    res = launchctl(*args)
    if res.returncode:
        raise RapportError(f"launchctl {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")


def loaded() -> bool:
    """Does launchd have the agent (running or not)? A plist on disk may not be loaded yet."""
    return launchctl("print", service()).returncode == 0


def login_shell() -> str:
    shell = os.environ.get("SHELL", "")
    return shell if shell and Path(shell).is_file() else "/bin/zsh"


def agent_command() -> str:
    """What the login shell runs: bin/rapport, which picks the venv or uv itself on every start."""
    cli = ROOT / "bin" / "rapport"
    argv = [str(cli)] if cli.is_file() else [sys.executable, str(ROOT / "scripts" / "rapport.py")]
    return "exec " + shlex.join([*argv, "hub", "--agent"])


def plist(log: Path, shell: str) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [shell, "-lc", agent_command()],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "AbandonProcessGroup": True,        # report servers it started keep running when it stops
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    })


# ── the command ───────────────────────────────────────────────────────────── #
def cli(args) -> int:
    if args.agent:
        args.set = AGENT_SETS + list(args.set or [])
        args.foreground, args.no_open = True, True
    try:
        return _cli(args)
    except RapportError as e:
        if not args.agent:
            raise
        print(f"[{time.strftime('%H:%M:%S')}] rapport hub: {e}\n"
              "  The login agent won't retry: fix this, then run `rapport hub`.", file=sys.stderr, flush=True)
        return 0


def _cli(args) -> int:
    cfg = config_mod.load(Path.home(), None, args.set)     # the hub is yours: user config
    d = reports.hub_dir(cfg)
    if args.install:
        return install(cfg, d, args.no_open)
    if args.uninstall:
        return uninstall(d)
    if args.stop:
        code = serve.stop(d)
        if installed() and not code:
            print("the login agent starts it again at the next login, or with `rapport hub`")
        return code
    if args.status:
        return status(d)
    reports.ensure_hub(cfg)
    explicit = args.port is not None
    port = args.port or reports.hub_port(cfg)
    live = serve.running(d)
    if live and live.get("port") != port:  # [hub] port changed: move it there, a hub has one address
        print(f"moving the hub from port {live.get('port')} to {port}")
        live = _stop(d)
    if live and args.agent:                 # the agent owns the hub: take over from one started by hand
        print(f"taking over the hub from pid {live.get('pid')}", flush=True)
        live = _stop(d)
    if live and not args.foreground:        # it runs already: hand out the page again
        print(f"→ {live['url']}")
        if not args.no_open:
            webbrowser.open(live["url"])
        return 0
    if not live:
        _check_port(cfg, port)
    args.port = port                        # pinned: the hub never moves to the next port
    if args.foreground:
        return serve.run(d, args)
    if installed() and not explicit:
        return start_agent(d, args.no_open)
    return serve.detach(d, args)


def _stop(d: Path) -> None:
    if serve.stop(d):
        raise RapportError("the running hub didn't stop; try `rapport hub --stop` again")
    return None


def _check_port(cfg, port: int) -> None:
    if serve.port_taken(cfg.get("serve.host", "127.0.0.1"), port):
        raise RapportError(f"port {port} is held by another program (`lsof -nP -iTCP:{port} -sTCP:LISTEN` "
                           "shows which); stop it, or set another [hub] port in your user config")


def start_agent(d: Path, no_open: bool) -> int:
    """Start the installed agent: load it if launchd doesn't have it (RunAtLoad starts it), else
    kick it. Either way launchd owns the process, so it restarts after a crash."""
    if loaded():
        _launchctl_ok("kickstart", service())
    else:
        _launchctl_ok("bootstrap", f"gui/{os.getuid()}", str(plist_path()))
    print("starting the hub through its login agent", flush=True)
    return wait_until_up(d, no_open)


def wait_until_up(d: Path, no_open: bool) -> int:
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        live = serve.running(d)
        if live:
            print(f"→ {live['url']}")
            if not no_open:
                webbrowser.open(live["url"])
            return 0
        time.sleep(0.25)
    log = d / ".rapport" / serve.LOG_FILE
    tail = log.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-8:] if log.exists() else []
    raise RapportError(f"the hub didn't come up within {START_TIMEOUT:.0f} s; the end of {log}:\n  "
                       + "\n  ".join(tail))


def install(cfg, d: Path, no_open: bool) -> int:
    if not macos():
        raise RapportError("--install needs macOS (launchd). Elsewhere, start `rapport hub` at login "
                           "yourself, e.g. from your desktop's autostart")
    reports.ensure_hub(cfg)
    port = reports.hub_port(cfg)
    if loaded():                            # reinstall: a fresh plist, a fresh start
        _launchctl_ok("bootout", service())
    deadline = time.time() + 10
    while serve.running(d) and time.time() < deadline:    # bootout's hub, or one started by hand
        serve.stop(d)
        time.sleep(0.2)
    _check_port(cfg, port)
    log = ensure_state_dir(d) / serve.LOG_FILE
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist(log, login_shell()))
    _launchctl_ok("bootstrap", f"gui/{os.getuid()}", str(path))
    print(f"installed {path}: the hub starts at login and again if it stops unexpectedly.\n"
          "macOS may show a \"Background item added\" notice naming your shell; that is this agent.\n"
          "`rapport hub --stop` stops it until `rapport hub`; `rapport hub --uninstall` removes it.", flush=True)
    return wait_until_up(d, no_open)


def uninstall(d: Path) -> int:
    if not macos():
        raise RapportError("--uninstall is for the macOS login agent; there is none on this system")
    path = plist_path()
    if not path.exists() and not loaded():
        print("no login agent installed")
        return 0
    if loaded():
        _launchctl_ok("bootout", service())     # stops the hub; report servers it started keep running
    path.unlink(missing_ok=True)
    print(f"removed {path}; the hub stopped. `rapport hub` starts it by hand.")
    return 0


def status(d: Path) -> int:
    code = serve.status(d)
    if installed():
        print(f"login agent: installed ({plist_path()}){'' if loaded() else ', not loaded'}")
    elif macos():
        print("login agent: not installed (`rapport hub --install` keeps the hub running)")
    return code
