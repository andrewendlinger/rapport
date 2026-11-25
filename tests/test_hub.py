"""`rapport hub`: the macOS login agent (--install/--uninstall), starting through it, and --agent.
launchctl is faked throughout: CI must never touch the real launchd."""

import os
import plistlib
import subprocess

import pytest

from conftest import write
from rapport_core import hub, serve
from rapport_core.cli import main


class Launchd:
    """A fake launchctl: records calls; `print` answers whether the agent is loaded."""

    def __init__(self):
        self.calls, self.loaded, self.up = [], False, False

    def __call__(self, *args):
        self.calls.append(args[0])
        if args[0] == "print":
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, "", "")
        if args[0] in ("bootstrap", "kickstart"):
            self.loaded = self.loaded or args[0] == "bootstrap"
            self.up = True
        if args[0] == "bootout":
            self.loaded = self.up = False
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def mac(isolated_home, monkeypatch):
    """macOS with a fake launchd; the hub 'runs' whenever launchd started it."""
    launchd = Launchd()
    monkeypatch.setattr(hub, "macos", lambda: True)
    monkeypatch.setattr(hub, "launchctl", launchd)
    monkeypatch.setattr(serve, "running", lambda d: {"pid": 1, "port": 6969, "url": "http://127.0.0.1:6969/"}
                        if launchd.up else None)
    monkeypatch.setattr(serve, "port_taken", lambda host, port: False)
    monkeypatch.setattr(serve, "detach", lambda d, args: pytest.fail("started a hub launchd doesn't own"))
    monkeypatch.setattr(serve, "stop", lambda d: setattr(launchd, "up", False) or 0)
    monkeypatch.setenv("SHELL", "/bin/sh")
    write(isolated_home / ".config/rapport/config.toml", '[hub]\ndir = "~/hub"\nport = 6969\n')
    return isolated_home, launchd


def test_the_plist_runs_the_hub_through_the_login_shell(tmp_path):
    p = plistlib.loads(hub.plist(tmp_path / "server.log", "/bin/zsh"))
    assert p["Label"] == hub.LABEL and p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}       # a crash restarts it, --stop doesn't
    shell, flag, command = p["ProgramArguments"]
    assert (shell, flag) == ("/bin/zsh", "-lc")               # the profile supplies RAPPORT_CONFIG_DIR
    assert command.startswith("exec ") and command.endswith("rapport hub --agent")
    assert "bin/rapport" in command and "RAPPORT_CONFIG" not in str(p)    # nothing captured
    assert p["StandardOutPath"] == p["StandardErrorPath"] == str(tmp_path / "server.log")
    assert p["AbandonProcessGroup"] is True


def test_install_writes_loads_and_waits(mac, capsys):
    home, launchd = mac
    assert main(["hub", "--install", "--no-open"]) == 0
    path = home / "Library/LaunchAgents" / f"{hub.LABEL}.plist"
    assert plistlib.loads(path.read_bytes())["ProgramArguments"][0] == "/bin/sh"
    assert launchd.calls == ["print", "bootstrap"] and launchd.up
    out = capsys.readouterr().out
    assert "installed" in out and "Background item added" in out and "http://127.0.0.1:6969/" in out
    launchd.calls.clear()
    assert main(["hub", "--install", "--no-open"]) == 0       # again: a fresh plist, a fresh start
    assert launchd.calls == ["print", "bootout", "bootstrap"]


def test_install_and_uninstall_need_macos(isolated_home, monkeypatch, capsys):
    monkeypatch.setattr(hub, "macos", lambda: False)
    monkeypatch.setattr(hub, "launchctl", lambda *a: pytest.fail("launchctl off macOS"))
    assert main(["hub", "--install"]) == 2 and "needs macOS" in capsys.readouterr().err
    assert main(["hub", "--uninstall"]) == 2


def test_installed_hub_starts_through_launchd(mac, capsys):
    home, launchd = mac
    assert main(["hub", "--install", "--no-open"]) == 0
    assert main(["hub", "--stop"]) == 0 and not launchd.up
    assert "starts it again at the next login" in capsys.readouterr().out
    launchd.calls.clear()
    assert main(["hub", "--no-open"]) == 0                    # loaded but stopped: kick it
    assert launchd.calls == ["print", "kickstart"] and launchd.up
    launchd.loaded = launchd.up = False                       # plist there, launchd doesn't have it
    launchd.calls.clear()
    assert main(["hub", "--no-open"]) == 0
    assert launchd.calls == ["print", "bootstrap"] and launchd.up
    assert "through its login agent" in capsys.readouterr().out


def test_uninstall_unloads_and_removes(mac, capsys):
    home, launchd = mac
    assert main(["hub", "--uninstall"]) == 0 and "no login agent" in capsys.readouterr().out
    assert main(["hub", "--install", "--no-open"]) == 0
    assert main(["hub", "--uninstall"]) == 0
    assert "bootout" in launchd.calls and not launchd.up
    assert not (home / "Library/LaunchAgents" / f"{hub.LABEL}.plist").exists()


def test_status_tells_whether_the_agent_is_installed(mac, capsys):
    assert main(["hub", "--status"]) == 1
    assert "login agent: not installed" in capsys.readouterr().out
    assert main(["hub", "--install", "--no-open"]) == 0
    capsys.readouterr()
    assert main(["hub", "--status"]) == 0
    assert "login agent: installed" in capsys.readouterr().out


def test_agent_runs_always_on_and_takes_over(mac, monkeypatch, capsys):
    home, launchd = mac
    ran = []
    monkeypatch.setattr(serve, "run", lambda d, args: ran.append(args) or 0)
    launchd.up = True                                         # a hub started by hand
    assert main(["hub", "--agent"]) == 0
    (args,) = ran
    assert args.port == 6969 and args.no_open and args.foreground
    assert args.set[:2] == hub.AGENT_SETS
    assert "taking over the hub from pid 1" in capsys.readouterr().out


def test_agent_start_up_failure_exits_0_so_launchd_stops_retrying(mac, monkeypatch, capsys):
    monkeypatch.setattr(serve, "port_taken", lambda host, port: True)
    assert main(["hub", "--agent"]) == 0
    err = capsys.readouterr().err
    assert "port 6969 is held by another program" in err and "won't retry" in err
    assert main(["hub", "--no-open"]) == 2                    # by hand it is an error


def test_service_names_this_user(monkeypatch):
    assert hub.service() == f"gui/{os.getuid()}/{hub.LABEL}"
