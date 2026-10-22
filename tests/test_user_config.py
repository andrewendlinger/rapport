import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import write
from rapport_core import config as config_mod
from rapport_core import serve
from rapport_core.cli import main, with_default_action
from rapport_core.util import RapportError


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def load(d):
    return config_mod.load_for_report(d)[0]


def test_precedence(report, isolated_home, monkeypatch):
    d = report("x\n")
    write(isolated_home / ".config/rapport/config.toml", "[serve]\nport = 1000\n")
    write(isolated_home / "xdg/rapport/config.toml", "[serve]\nport = 1001\n")
    write(isolated_home / "dir/config.toml", "[serve]\nport = 1002\n")
    write(isolated_home / "file.toml", "[serve]\nport = 1003\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated_home / "xdg"))
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", str(isolated_home / "dir"))
    monkeypatch.setenv("RAPPORT_CONFIG", str(isolated_home / "file.toml"))
    for source, port, unset in [("RAPPORT_CONFIG", 1003, "RAPPORT_CONFIG"), ("RAPPORT_CONFIG_DIR", 1002, "RAPPORT_CONFIG_DIR"),
                                ("XDG_CONFIG_HOME", 1001, "XDG_CONFIG_HOME"), ("default", 1000, None)]:
        assert config_mod.user_paths().source == source
        assert load(d).get("serve.port") == port, source
        if unset:
            monkeypatch.delenv(unset)


def test_empty_values_are_ignored(report, isolated_home, monkeypatch):
    write(isolated_home / ".config/rapport/config.toml", "[serve]\nport = 1001\n")
    monkeypatch.setenv("RAPPORT_CONFIG", "")
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", "")
    assert config_mod.user_paths().source == "XDG_CONFIG_HOME"
    assert load(report("x\n")).get("serve.port") == 1001


def test_config_dir_replaces_the_whole_folder(capsys, report, isolated_home, monkeypatch):
    d = report("+++\npreset = \"mine\"\n+++\nx\n")
    dots = isolated_home / "dots"
    write(dots / "config.toml", "[serve]\nport = 1002\n")
    write(dots / "learned.toml", '[[learned]]\nrule = "dir rule"\n')
    write(dots / "presets/mine.toml", '[layout]\nwidth = "50ch"\n')
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", str(dots))
    cfg = load(d)
    assert cfg.get("serve.port") == 1002 and "dir rule" in cfg.get("writing.rules")
    assert cfg.get("layout.width") == "50ch"
    assert run(capsys, "modules", "new", "aside", "--scope", "user", "-C", str(d))[0] == 0
    assert (dots / "rapport-modules/aside/module.toml").exists()
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", str(isolated_home / "nowhere"))      # missing folder: no user config
    assert load(report("x\n", name="r2")).get("serve.port") == 4848


def test_config_file_and_its_siblings(capsys, report, isolated_home, monkeypatch):
    d = report("+++\npreset = \"mine\"\n+++\n::: aside\nhi\n:::\n")
    dots = isolated_home / "dots"
    write(dots / "my-rapport.toml", '[serve]\nport = 1003\n[[modules.feedback.actions]]\nid = "cite"\nlabel = "Cite"\n'
                                    'key = "n"\ninstruction_file = "prompts/cite.md"\n')
    write(dots / "rapport-presets/mine.toml", '[layout]\nwidth = "50ch"\n')
    write(isolated_home / ".config/rapport/config.toml", "[serve]\nport = 1000\n")     # not read any more
    monkeypatch.setenv("RAPPORT_CONFIG", str(dots / "my-rapport.toml"))
    assert run(capsys, "writing", "add", "Prefer numbers.", "-C", str(d))[0] == 0
    assert "Prefer numbers." in (dots / "rapport-learned.toml").read_text()
    assert not (isolated_home / ".config/rapport/learned.toml").exists()
    assert run(capsys, "modules", "new", "aside", "--kind", "block", "--scope", "user", "-C", str(d))[0] == 0
    cfg = load(d)
    assert cfg.get("serve.port") == 1003 and cfg.get("layout.width") == "50ch"
    assert "Prefer numbers." in cfg.get("writing.rules")
    assert cfg.registry.get("aside").dir == (dots / "rapport-modules/aside").resolve()
    acts = {a["id"]: a for a in cfg.module("feedback")["actions"]}
    assert acts["cite"]["instruction_file"] == str((dots / "prompts/cite.md").resolve())    # relative to the file
    assert [(lay.kind, lay.name) for lay in cfg.layers if lay.kind.endswith("user")] == \
        [("user", "user"), ("learned-user", "learned (user)")]
    code, out = run(capsys, "build", str(d))
    assert code == 0 and 'class="rp-m-aside' in (d / "report.html").read_text(), out


def test_dev_null_turns_user_config_off(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    write(isolated_home / ".config/rapport/config.toml", "[serve]\nport = 1000\n")
    write(isolated_home / ".config/rapport/learned.toml", '[[learned]]\nrule = "user rule"\n')
    write(isolated_home / ".config/rapport/presets/mine.toml", '[layout]\nwidth = "50ch"\n')
    write(isolated_home / "dir/config.toml", "[serve]\nport = 1002\n")
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", str(isolated_home / "dir"))
    monkeypatch.setenv("RAPPORT_CONFIG", os.devnull)
    up = config_mod.user_paths()
    assert up.config_file is up.learned_file is up.modules_dir is up.presets_dir is None
    cfg = load(d)
    assert cfg.get("serve.port") == 4848 and "user rule" not in cfg.get("writing.rules")
    assert not [lay for lay in cfg.layers if lay.kind in ("user", "learned-user")]
    assert not [r for r in cfg.registry.roots if "rapport-modules" in str(r) and ".config" in str(r)]
    with pytest.raises(RapportError, match="preset 'mine' not found"):
        load(report("+++\npreset = \"mine\"\n+++\nx\n", name="r2"))
    code, out = run(capsys, "writing", "add", "a rule", "-C", str(d))
    assert code == 2 and "no user config" in out and "--scope project" in out
    code, out = run(capsys, "modules", "new", "aside", "--scope", "user", "-C", str(d))
    assert code == 2 and "no user config" in out
    assert run(capsys, "build", str(d))[0] == 0


def test_missing_config_file_fails_every_command_but_paths(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    missing = isolated_home / "dots/nope.toml"
    monkeypatch.setenv("RAPPORT_CONFIG", str(missing))
    for argv in (["build", str(d)], ["lint", str(d)], ["config", "show", str(d)], ["init", str(d.parent / "new")]):
        code, out = run(capsys, *argv)
        assert code == 2 and f"RAPPORT_CONFIG points at {missing}, which doesn't exist" in out, argv
    assert not (d.parent / "new").exists()
    with pytest.raises(RapportError, match="doesn't exist"):
        load(d)
    code, out = run(capsys, "config", "paths", str(d))
    assert code == 0 and f"{missing}  [missing]" in out and "error: RAPPORT_CONFIG points at" in out
    code, out = run(capsys, "config", "paths", "--json", str(d))
    info = json.loads(out)
    assert code == 0 and "doesn't exist" in info["error"] and info["user"]["config"]["status"] == "missing"
    code, out = run(capsys, "serve", "--status", str(d))      # stopping a server never depends on config
    assert code == 1 and "no server running" in out
    code, out = run(capsys, "serve", "--stop", str(d))
    assert code == 0 and "no server running" in out


def test_config_pointing_at_a_folder_is_an_error(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    monkeypatch.setenv("RAPPORT_CONFIG", str(isolated_home))
    code, out = run(capsys, "build", str(d))
    assert code == 2 and "name a file, or set RAPPORT_CONFIG_DIR" in out


def test_relative_values_are_made_absolute_and_exported(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    write(isolated_home / "dots/my.toml", "")
    monkeypatch.chdir(isolated_home / "dots")
    monkeypatch.setenv("RAPPORT_CONFIG", "my.toml")
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", "~/dir")
    code, out = run(capsys, "config", "paths", "--json", str(d))
    cwd = Path(os.getcwd())
    assert code == 0
    assert os.environ["RAPPORT_CONFIG"] == str(cwd / "my.toml")
    assert os.environ["RAPPORT_CONFIG_DIR"] == str(isolated_home / "dir")
    info = json.loads(out)
    assert info["source"] == "RAPPORT_CONFIG" and info["user"]["config"] == {"path": str(cwd / "my.toml"),
                                                                             "status": "exists"}
    assert info["notes"] == ["RAPPORT_CONFIG_DIR ignored because RAPPORT_CONFIG is set"]


def test_detach_child_gets_our_cwd_and_absolute_env(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    write(isolated_home / "dots/my.toml", "")
    monkeypatch.chdir(isolated_home / "dots")
    monkeypatch.setenv("RAPPORT_CONFIG", "my.toml")
    seen = {}

    def popen(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"), env=os.environ.get("RAPPORT_CONFIG"))
        return SimpleNamespace(pid=0, poll=lambda: 1)            # "exits" at once: no real server
    monkeypatch.setattr(serve, "subprocess", SimpleNamespace(Popen=popen, STDOUT=subprocess.STDOUT,
                                                             DEVNULL=subprocess.DEVNULL))
    code, out = run(capsys, "serve", "--detach", "--no-open", "--set", "modules.paths=mods", str(d))
    assert code == 2 and "server exited during start-up" in out
    assert seen["cwd"] == os.getcwd()
    assert seen["env"] == str(Path(os.getcwd()) / "my.toml")
    assert seen["cmd"][2:5] == ["serve", str(d.resolve()), "--no-open"]
    assert seen["cmd"][-2:] == ["--set", "modules.paths=mods"]


def test_home_rapport_toml_as_user_config_loads_once(capsys, report, isolated_home, monkeypatch):
    d = report("x\n")
    write(isolated_home / "rapport.toml", '[writing]\nrules = ["home rule"]\n')
    write(isolated_home / "rapport-learned.toml", '[[learned]]\nrule = "learned rule"\n')
    monkeypatch.setenv("RAPPORT_CONFIG", "~/rapport.toml")
    code, out = run(capsys, "config", "show", "--origin", "--key", "writing.rules", "-C", str(d))
    assert code == 0
    assert '["home rule", "learned rule"]    # user + learned (user)' in out
    assert "project" not in out
    code, out = run(capsys, "config", "paths", str(d))
    assert "rapport.toml  [same file as the user config: loaded once, as user]" in out


def test_user_file_named_rapport_toml_is_still_the_user_layer(report, isolated_home, monkeypatch):
    # a user file inside the repo, outside the report's chain, ranks below the repo's
    # rapport-modules/ like any user config, despite its project-style name
    (isolated_home / "proj/.git").mkdir(parents=True)
    d = report("x\n")
    write(isolated_home / "proj/cfg/rapport.toml", "")
    for root in ("proj/rapport-modules", "proj/cfg/rapport-modules"):
        write(isolated_home / root / "aside/module.toml", 'kind = "block"\nsummary = "x"\n')
        write(isolated_home / root / "aside/template.html", "<div>{{ body }}</div>\n")
    monkeypatch.setenv("RAPPORT_CONFIG", str(isolated_home / "proj/cfg/rapport.toml"))
    cfg = load(d)
    assert [lay.kind for lay in cfg.layers if lay.path and lay.path.name == "rapport.toml"] == ["user"]
    assert cfg.registry.get("aside").dir == (isolated_home / "proj/rapport-modules/aside").resolve()


def test_config_paths_text_and_json(capsys, report, isolated_home):
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml", "")
    user = isolated_home / ".config/rapport"
    assert with_default_action(["config", "paths", str(d)]) == ["config", "paths", str(d)]
    code, out = run(capsys, "config", "paths", str(d))
    assert code == 0
    assert f"user config (chosen by XDG_CONFIG_HOME={isolated_home / '.config'})" in out
    for line in (f"config   {user / 'config.toml'}  [missing]", f"learned  {user / 'learned.toml'}  [missing]",
                 f"modules  {user / 'rapport-modules'}  [missing]", f"presets  {user / 'presets'}  [missing]",
                 f"  {(isolated_home / 'proj/rapport.toml').resolve()}\n", f"{d.resolve() / '.rapport'}  [missing]"):
        assert line in out, line
    run(capsys, "build", str(d))
    info = json.loads(run(capsys, "config", "paths", "--json", "-C", str(d))[1])
    assert info["source"] == "XDG_CONFIG_HOME" and info["error"] is None and info["notes"] == []
    assert info["user"]["learned"] == {"path": str(user / "learned.toml"), "status": "missing"}
    assert info["project"] == [{"path": str((isolated_home / "proj/rapport.toml").resolve()), "loaded_as_user": False}]
    assert info["state_dir"] == {"path": str(d.resolve() / ".rapport"), "status": "exists"}


def test_watcher_watches_the_relocated_files(report, isolated_home, monkeypatch):
    d = report("x\n")
    dots = isolated_home / "dots"
    write(dots / "my.toml", "")
    (dots / "rapport-modules").mkdir()
    monkeypatch.setenv("RAPPORT_CONFIG", str(dots / "my.toml"))
    files = serve.watched_files(d, load(d))
    for f in (dots / "my.toml", dots / "rapport-learned.toml", (dots / "rapport-modules").resolve()):
        assert f in files, f
    assert isolated_home / ".config/rapport/config.toml" not in files
