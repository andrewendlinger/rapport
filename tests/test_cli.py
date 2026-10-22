import os
import subprocess
from pathlib import Path

import pytest
from conftest import write
from rapport_core.cli import main

ROOT = Path(__file__).resolve().parent.parent


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_init_build_lint(capsys, isolated_home):
    d = isolated_home / "proj/reports/demo"
    assert run(capsys, "init", str(d), "--title", "Demo")[0] == 0
    assert (d / "report.md").exists() and (d / "assets").is_dir()
    (d / "report.md").write_text('+++\ntitle = "Demo"\n+++\n\n## A\n\nWe delve into it.\n', encoding="utf-8")
    code, out = run(capsys, "build", str(d))
    assert code == 0 and (d / "report.html").exists()
    code, out = run(capsys, "lint", "-C", str(d))
    assert code == 1 and 'report.md:7: avoid "delve"' in out


def test_brief_lists_modules_and_rules(capsys, report):
    d = report("x\n")
    code, out = run(capsys, "brief", "-C", str(d))
    assert code == 0
    for needle in ("## Writing rules", "never use:", "### compare", "### collapse", "syntax: `::: callout <title> {params}`",
                   "AI slop [slop]: Rewrite this in plain"):
        assert needle in out, needle


def test_config_show_origin_and_defaults(capsys, report, isolated_home):
    d = report("x\n")
    write(isolated_home / "proj/rapport.toml", '[theme]\nname = "paper"\n')
    code, out = run(capsys, "config", "show", "--origin", "--key", "theme.name", "-C", str(d))
    assert 'theme.name = "paper"    # project' in out
    code, out = run(capsys, "config", "defaults")
    assert "[writing]" in out and "appends across layers" in out


def test_modules_new_check_and_use(capsys, report, isolated_home):
    d = report("::: side-note Aside\nhello\n:::\n")
    write(isolated_home / "proj/rapport.toml", "")
    code, out = run(capsys, "modules", "new", "side-note", "--kind", "block", "-C", str(d))
    assert code == 0 and (isolated_home / "proj/rapport-modules/side-note/template.html").exists()
    code, out = run(capsys, "modules", "check", "side-note", "-C", str(d))
    assert code == 0 and "side-note      ok" in out
    code, out = run(capsys, "build", str(d))
    assert code == 0 and 'class="rp-m-side-note rp-block rp-side-note"' in (d / "report.html").read_text()
    code, out = run(capsys, "brief", "-C", str(d))
    assert "### side-note  (custom:" in out


def test_modules_new_copies_existing_as_override(capsys, report, isolated_home):
    d = report("x\n")
    code, out = run(capsys, "modules", "new", "callout", "--scope", "report", "-C", str(d))
    assert code == 0 and (d / "rapport-modules/callout/style.css").exists()
    code, out = run(capsys, "modules", "list", "-C", str(d))
    assert "overrides built-in" in out


def test_all_builtin_modules_check_clean(capsys, report):
    code, out = run(capsys, "modules", "check", "-C", str(report("x\n")))
    assert code == 0, out
    assert "warning" not in out, out


def test_writing_add_scopes(capsys, report, isolated_home):
    d = report("x\n")
    (isolated_home / "proj/.git").mkdir(parents=True)
    run(capsys, "writing", "add", "Say 'we', not 'the team'", "--scope", "project", "-C", str(d))
    run(capsys, "writing", "add", "synergy", "--avoid", "-C", str(d))
    assert (isolated_home / "proj/rapport-learned.toml").exists()
    code, out = run(capsys, "brief", "-C", str(d))
    assert "rule: Say 'we', not 'the team'" in out and '"synergy"' in out


def test_feedback_cli_add_and_resolve(capsys, report):
    d = report("## A\n\nSome claim here.\n")
    run(capsys, "build", str(d))
    code, out = run(capsys, "feedback", "add", "source?", "--quote", "Some claim", "-C", str(d))
    assert code == 0 and "added FB-1" in out
    code, out = run(capsys, "feedback", "-C", str(d))
    assert "FB-1 · Comment · A · report.md:3" in out
    code, out = run(capsys, "feedback", "resolve", "FB-1", "--note", "cited", "-C", str(d))
    assert "resolved FB-1 → v2" in out
    assert run(capsys, "feedback", "resolve", "FB-1", "-C", str(d))[0] == 2      # not open any more


def test_gallery(capsys, isolated_home, monkeypatch):
    out_dir = isolated_home / "gallery"
    monkeypatch.chdir(isolated_home)
    code, out = run(capsys, "gallery", "--out", str(out_dir))
    assert code == 0, out
    html = (out_dir / "report.html").read_text()
    from rapport_core.config import load
    shown = [m.name for m in load(isolated_home).registry.modules.values()
             if m.kind in ("block", "inline", "element") and m.example_path]
    assert len(shown) >= 10
    for m in shown:                     # every module with an example renders in the gallery
        assert f'data-rp-module="{m}"' in html, m


def test_bin_wrapper_through_symlink(tmp_path, isolated_home):
    link = tmp_path / "linkdir"
    link.mkdir()
    os.symlink(ROOT / "bin" / "rapport", link / "rapport")
    out = subprocess.run([str(link / "rapport"), "--version"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.startswith("rapport ")


def test_install_script(tmp_path, isolated_home):
    env = {**os.environ, "HOME": str(isolated_home)}
    target = tmp_path / "skills"
    dry = subprocess.run(["sh", str(ROOT / "install.sh"), "--only", str(target), "--dry-run"],
                         capture_output=True, text=True, env=env, timeout=120)
    assert dry.returncode == 0 and not (target / "rapport").exists()
    real = subprocess.run(["sh", str(ROOT / "install.sh"), "--only", str(target)],
                          capture_output=True, text=True, env=env, timeout=120)
    assert real.returncode == 0, real.stderr
    assert (target / "rapport").is_symlink() and (target / "rapport" / "SKILL.md").exists()
    again = subprocess.run(["sh", str(ROOT / "install.sh"), "--only", str(target)],
                           capture_output=True, text=True, env=env, timeout=120)
    assert "already linked" in again.stdout
    (tmp_path / "skills2" / "rapport").mkdir(parents=True)          # someone else's folder
    foreign = subprocess.run(["sh", str(ROOT / "install.sh"), "--only", str(tmp_path / "skills2")],
                             capture_output=True, text=True, env=env, timeout=120)
    assert "not ours" in foreign.stderr
    un = subprocess.run(["sh", str(ROOT / "install.sh"), "--only", str(target), "--uninstall"],
                        capture_output=True, text=True, env=env, timeout=120)
    assert un.returncode == 0 and not (target / "rapport").exists()
    assert (tmp_path / "skills2" / "rapport").exists()


# ── regressions from the review ────────────────────────────────────────────── #
def test_feedback_flags_before_list(capsys, report):
    import json as json_
    d = report("A paragraph.\n")
    assert run(capsys, "feedback", "add", "first", "-C", str(d))[0] == 0
    assert run(capsys, "feedback", "add", "second", "-C", str(d))[0] == 0
    assert run(capsys, "feedback", "resolve", "FB-1", "--note", "done", "-C", str(d))[0] == 0
    code, out = run(capsys, "feedback", "--json", "list", "-C", str(d))
    assert code == 0 and [i["id"] for i in json_.loads(out)["items"]] == ["FB-2"]
    code, out = run(capsys, "feedback", "--all", "list", "-C", str(d))
    assert "FB-1" in out and "FB-2" in out


def test_report_folder_as_a_plain_argument(capsys, report):
    d = report("A paragraph.\n")
    assert run(capsys, "feedback", "add", "a comment", str(d))[0] == 0
    code, out = run(capsys, "feedback", str(d))
    assert code == 0 and "FB-1" in out
    code, out = run(capsys, "feedback", "--all", str(d))
    assert code == 0 and "FB-1" in out
    code, out = run(capsys, "config", "--key", "theme.name", str(d))
    assert code == 0 and 'theme.name = "default"' in out
    assert run(capsys, "feedback", "resolve", "FB-1", str(d), "--note", "ok")[0] == 0
    for comment in ("b", "c", "d"):
        assert run(capsys, "feedback", "add", comment, str(d))[0] == 0
    # ids and the folder may also follow --note
    code, out = run(capsys, "feedback", "resolve", "FB-2", "--note", "ok", str(d))
    assert code == 0 and "resolved FB-2 → v3" in out
    code, out = run(capsys, "feedback", "resolve", "FB-3", "--note", "ok", "FB-4", str(d))
    assert code == 0 and "resolved FB-3, FB-4 → v4" in out
    assert run(capsys, "feedback", "reopen", "FB-2", "-C", str(d), "FB-3")[0] == 0
    with pytest.raises(SystemExit):                                # other commands still reject extras
        main(["lint", str(d), "extra"])
    with pytest.raises(SystemExit):
        main(["feedback", "resolve", "FB-2", "--note", "ok", str(d), "--bogus"])
    code, out = run(capsys, "config", str(d), "--key", "theme.name")
    assert code == 0 and 'theme.name = "default"' in out
    code, out = run(capsys, "modules", "list", str(d))
    assert code == 0 and "callout" in out
    code, out = run(capsys, "modules", "info", "callout", str(d))
    assert code == 0 and "callout" in out
    assert run(capsys, "gallery", str(d), "--out", str(d.parent / "g"))[0] == 0
    assert run(capsys, "writing", "add", "Prefer numbers.", str(d), "--scope", "project")[0] == 0


def test_init_quoting_scaffold_and_dash_c(capsys, isolated_home):
    d = isolated_home / "proj/reports/q"
    assert run(capsys, "init", "-C", str(d), "--title", 'Q3 \\ Q4 "final"')[0] == 0
    code, out = run(capsys, "lint", str(d))
    assert code == 0, out
    assert run(capsys, "build", str(d))[0] == 0
    html = (d / "report.html").read_text()
    assert '<title>Q3 \\ Q4 &#34;final&#34;</title>' in html or "<title>Q3 \\ Q4 &quot;final&quot;</title>" in html
    assert "&lt;!--" not in html


def test_config_json_with_a_date(capsys, report):
    d = report("+++\ndate = 2026-10-20\n+++\n\nx\n")
    code, out = run(capsys, "config", "show", "--json", "-C", str(d))
    assert code == 0 and '"date": "2026-10-20"' in out


def test_project_module_in_a_git_repo_without_rapport_toml(capsys, isolated_home):
    repo = isolated_home / "repo"
    (repo / ".git").mkdir(parents=True)
    d = repo / "reports/r1"
    assert run(capsys, "init", str(d))[0] == 0
    code, out = run(capsys, "modules", "new", "tabs", "--kind", "block", "-C", str(d))
    assert code == 0 and (repo / "rapport-modules/tabs/module.toml").exists(), out
    assert run(capsys, "modules", "check", "tabs", "-C", str(d))[0] == 0
    (d / "report.md").write_text("::: tabs\nInside.\n:::\n", encoding="utf-8")
    code, out = run(capsys, "build", str(d))
    assert code == 0, out


def test_lint_flags_raw_html(capsys, report):
    d = report("Intro.\n\n<!-- a note to self -->\n\n<https://example.com> is an autolink.\n")
    code, out = run(capsys, "lint", "-C", str(d))
    assert code == 1 and "report.md:3: raw HTML shows up as literal text" in out and "report.md:5" not in out


def test_lint_prints_build_warnings(capsys, report):
    d = report("::: collapse More\nNever closed.\n")
    code, out = run(capsys, "lint", "-C", str(d))
    assert code == 1 and "warning: report.md:1: ':::collapse' is never closed" in out


def test_feedback_add_honours_set(capsys, report):
    d = report("unused\n")
    (d / "report.md").unlink()
    write(d / "notes.md", "## A\n\nSome claim here.\n")
    code, out = run(capsys, "feedback", "--set", "report.source=notes.md", "add", "source?", "--quote", "Some claim",
                    "-C", str(d))
    assert code == 0 and "added FB-1" in out, out


def test_feedback_add_sees_edits_since_the_last_build(capsys, report):
    d = report("First paragraph.\n")
    run(capsys, "build", str(d))
    (d / "report.md").write_text("First paragraph.\n\nSet `max_paragraph_words` to zero.\n", encoding="utf-8")
    code, out = run(capsys, "feedback", "add", "why?", "--quote", "max_paragraph_words to zero", "-C", str(d))
    assert code == 0 and "added FB-1" in out, out
    code, out = run(capsys, "feedback", "-C", str(d))
    assert "report.md:3" in out and "no longer in that passage" not in out
    code, out = run(capsys, "feedback", "add", "x", "--block", "paragraph-nope", "-C", str(d))
    assert code == 2 and "no block 'paragraph-nope' in the current report" in out
