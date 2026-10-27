import os
import shlex
import shutil
import subprocess

import pytest

from conftest import write
from rapport_core import build as build_mod
from rapport_core import serve
from rapport_core.build import build
from rapport_core.cli import main
from rapport_core.feedback import Store
from rapport_core.util import OLD_GITIGNORE, RapportError, bump_version, state_dir, state_gitignore

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
COMMIT = ["report.commit_feedback=true"]
COMMENT = {"target": {"kind": "report"}, "actions": [], "comment": "c"}


def gitignore(d):
    return (state_dir(d) / ".gitignore").read_text(encoding="utf-8")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=30).stdout


@pytest.fixture
def repo(isolated_home, monkeypatch):
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(k, raising=False)
    proj = isolated_home / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    git(proj, "init", "-q")
    monkeypatch.chdir(proj)
    return proj


# ── the managed .gitignore ─────────────────────────────────────────────────── #
def test_gitignore_follows_commit_feedback(report):
    d = report("x\n")
    build(d)
    assert gitignore(d) == state_gitignore()
    assert gitignore(d).startswith("# managed by rapport: ") and gitignore(d).splitlines()[1:] == ["*"]
    build(d, COMMIT)
    assert gitignore(d).splitlines()[1:] == ["*", "!.gitignore", "!feedback.json", "!state.json", "!rounds.json"]
    build(d)
    assert gitignore(d) == state_gitignore()


def test_old_gitignore_upgraded_and_user_file_left_alone(report):
    d = report("x\n")
    write(state_dir(d) / ".gitignore", OLD_GITIGNORE)
    build(d)
    assert gitignore(d) == state_gitignore()
    write(state_dir(d) / ".gitignore", "server.json\n# mine\n")
    build(d)
    build(d, COMMIT)
    assert gitignore(d) == "server.json\n# mine\n"


def test_header_fits_the_setting_and_older_headers_are_rewritten(report):
    assert "set [report] commit_feedback = true" in state_gitignore()
    assert "set [report] commit_feedback = true" not in state_gitignore(True)
    assert all(state_gitignore(c).startswith("# managed by rapport: ") for c in (False, True))
    d = report("x\n")
    old = "# managed by rapport: set [report] commit_feedback = true to commit feedback; edits are overwritten\n*\n"
    write(state_dir(d) / ".gitignore", old + "!.gitignore\n!feedback.json\n!state.json\n")
    build(d, COMMIT)
    assert gitignore(d) == state_gitignore(True)


def test_unchanged_gitignore_is_not_rewritten(report):
    d = report("x\n")
    build(d)
    gi = state_dir(d) / ".gitignore"
    os.utime(gi, ns=(10**9, 10**9))
    build(d)
    assert gi.stat().st_mtime_ns == 10**9


def test_state_folder_is_ignored_without_a_successful_build(report, monkeypatch):
    d = report("+++\nbroken = \n+++\n", name="a")
    with pytest.raises(RapportError, match="front matter"):
        build(d)
    assert gitignore(d) == state_gitignore() and not (state_dir(d) / "blocks.json").exists()
    d = report("x\n", name="b")
    Store(d).add(COMMENT)
    assert gitignore(d) == state_gitignore()
    d = report("x\n", name="c")
    bump_version(d, "note")
    assert gitignore(d) == state_gitignore()

    def broken(*a, **k):
        raise RapportError("boom")
    monkeypatch.setattr(serve, "build", broken)
    d = report("x\n", name="d")
    httpd, app, url, cfg = serve.make_server(d, port=0, pinned=True)
    httpd.server_close()
    assert app.errors == ["boom"] and serve.read_server(d)["url"] == url
    assert gitignore(d) == state_gitignore() and not (state_dir(d) / "blocks.json").exists()


# ── git ─────────────────────────────────────────────────────────────────────── #
@needs_git
def test_state_folder_stays_out_of_git_status(report, repo):
    d = report("x\n")
    build(d)
    Store(d).add(COMMENT)
    bump_version(d, "note")
    assert ".rapport" not in git(repo, "status", "--porcelain", "-uall")
    build(d, COMMIT)
    status = git(repo, "status", "--porcelain", "-uall")
    for f in (".gitignore", "feedback.json", "state.json"):
        assert f"reports/r/.rapport/{f}" in status
    assert "blocks.json" not in status and "feedback.lock" not in status


@needs_git
def test_tracked_state_files_are_reported_on_every_build(report, repo, capsys, monkeypatch):
    d = report("x\n")
    build(d)
    Store(d).add(COMMENT)
    git(repo, "add", "-f", "reports/r/.rapport/feedback.json", "reports/r/.rapport/blocks.json")
    for _ in range(2):
        res = build(d)
        assert res.notices == [
            ".rapport/ is git-ignored now, but these files in it are still tracked: blocks.json, feedback.json; "
            "untrack them with: git rm --cached reports/r/.rapport/blocks.json reports/r/.rapport/feedback.json"]
        assert not res.warnings
    capsys.readouterr()
    assert main(["lint", str(d)]) == 0                   # the user's call: lint stays clean
    assert capsys.readouterr().out == f"note for the user: {res.notices[0]}\nlint: clean\n"
    httpd, app, url, cfg = serve.make_server(d, port=0, pinned=True)
    httpd.server_close()
    assert f"  note for the user: {res.notices[0]}\n" in capsys.readouterr().out
    monkeypatch.chdir(repo.parent)                       # outside the repo: say where to run it
    want = f"git -C {shlex.quote(str(repo.resolve()))} rm --cached reports/r/.rapport/blocks.json"
    assert any(want in w for w in build(d).notices)
    git(repo, "rm", "-q", "--cached", "reports/r/.rapport/blocks.json", "reports/r/.rapport/feedback.json")
    assert not build(d).notices


@needs_git
def test_commit_feedback_under_an_outer_ignore_warns(report, repo):
    d = report("x\n")
    assert not build(d, COMMIT).notices
    write(repo / ".gitignore", ".rapport/\n")
    assert build(d, COMMIT).notices == [
        "commit_feedback = true, but an outer .gitignore still ignores .rapport/ "
        "(git check-ignore -v reports/r/.rapport/feedback.json shows which)"]
    assert not build(d).notices


@needs_git
def test_git_never_runs_the_repos_fsmonitor_command(report, repo):
    d = report("x\n")
    build(d)
    ran = repo.parent / "fsmonitor-ran"
    hook = write(repo.parent / "fsmonitor.sh", f"#!/bin/sh\ntouch {shlex.quote(str(ran))}\n")
    hook.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(hook))
    build(d, COMMIT)
    assert not ran.exists()
    main(["lint", "--set", "report.commit_feedback=true", str(d)])
    assert not ran.exists()


def test_git_is_never_run_outside_a_repo(report, monkeypatch):
    d = report("x\n")
    if any((p / ".git").exists() for p in d.parents):
        pytest.skip("the temp folder is inside a git work tree")

    def forbidden(*a, **k):
        raise AssertionError("git invoked")
    monkeypatch.setattr(build_mod.shutil, "which", forbidden)
    monkeypatch.setattr(build_mod.subprocess, "run", forbidden)
    res = build(d, COMMIT)
    assert res.ok and not res.warnings and not res.notices
