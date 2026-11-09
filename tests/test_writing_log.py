import json
import subprocess

from rapport_core import writinglog
from rapport_core.cli import main

TEXT = "## Summary\n\nIt's worth noting that the cache is fast.\n\nA second paragraph stays as it is.\n"


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def log_on(home, extra=""):
    cfg = home / ".config/rapport/config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f"[writing]\nlog = true\n{extra}")
    return home / ".config/rapport/writing-log"


def entries(folder):
    return [json.loads(ln) for f in sorted(folder.glob("*.jsonl")) for ln in f.read_text().splitlines()]


def test_off_by_default(capsys, report, isolated_home):
    d = report(TEXT)
    run(capsys, "feedback", "add", "plainer", "--quote", "worth noting", "--action", "slop", "-C", str(d))
    (d / "report.md").write_text(TEXT.replace("It's worth noting that the cache", "The cache"))
    run(capsys, "feedback", "resolve", "FB-1", "--note", "plain", "-C", str(d))
    assert not (isolated_home / ".config/rapport/writing-log").exists()
    assert not (d / ".rapport/writing-snapshot.json").exists()


def test_a_resolved_item_keeps_its_block_before_and_after(capsys, report, isolated_home):
    folder = log_on(isolated_home)
    d = report(TEXT)
    code, out = run(capsys, "feedback", "add", "say it plainly", "--quote", "worth noting", "--action", "slop",
                    "-C", str(d))
    assert code == 0, out
    out = run(capsys, "feedback", "-C", str(d))[1]
    assert "learn: kept in the writing log for the next digest" in out           # no per-flag proposal
    (d / "report.md").write_text(TEXT.replace("It's worth noting that the cache", "The cache"))
    run(capsys, "feedback", "resolve", "FB-1", "--note", "Plain rewrite", "-C", str(d))
    rnd, e = entries(folder)
    assert rnd["event"] == "round" and rnd["headings"] == ["Summary"] and rnd["words"] > 0
    assert e["event"] == "resolved" and e["found"] == "changed" and e["after_by"] == "agent"
    assert e["item"] == {"id": "FB-1", "via": "chat", "actions": ["slop"], "labels": ["AI slop"],
                         "comment": "say it plainly", "note": "Plain rewrite"}
    assert "It's worth noting" in e["before"] and e["after"] == "The cache is fast."
    assert e["where"]["heading_path"] == ["Summary"] and e["neighbours"]["next"].startswith("A second")
    assert len(json.dumps(e)) < 2000                                    # a block and its context, no more
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
    assert all(oct(f.stat().st_mode & 0o777) == "0o600" for f in folder.glob("*.jsonl"))


def test_a_deleted_block_has_no_after(capsys, report, isolated_home):
    folder = log_on(isolated_home)
    d = report(TEXT)
    run(capsys, "feedback", "add", "--quote", "A second paragraph", "--action", "delete", "-C", str(d))
    (d / "report.md").write_text(TEXT.replace("A second paragraph stays as it is.\n", ""))
    run(capsys, "feedback", "resolve", "FB-1", "--note", "Cut", "-C", str(d))
    e = [x for x in entries(folder) if x["event"] == "resolved"][0]
    assert e["found"] == "deleted" and e["after"] is None and e["before"].startswith("A second paragraph")


def test_the_readers_edits_between_rounds_but_not_the_agents(capsys, report, isolated_home):
    folder = log_on(isolated_home)
    d = report(TEXT)
    run(capsys, "lint", "-C", str(d))                                   # the agent's draft is done
    (d / "report.md").write_text(TEXT.replace("the cache is fast", "the cache is quick"))   # the reader edits
    run(capsys, "feedback", "-C", str(d))                               # the agent starts a round
    (e,) = [x for x in entries(folder) if x["event"] == "edited"]
    assert e["by"] == "owner?" and "is fast" in e["before"] and "is quick" in e["after"]
    (d / "report.md").write_text(TEXT.replace("the cache is fast", "the cache responds quickly"))  # the agent
    run(capsys, "feedback", "-C", str(d))                               # lists again mid-round: not the reader
    run(capsys, "lint", "-C", str(d))
    run(capsys, "feedback", "-C", str(d))                               # nothing changed since it finished
    assert len([x for x in entries(folder) if x["event"] == "edited"]) == 1


def test_a_reopened_item_keeps_the_rejected_fix(capsys, report, isolated_home):
    folder = log_on(isolated_home)
    d = report(TEXT)
    run(capsys, "feedback", "add", "plainer", "--quote", "worth noting", "--action", "slop", "-C", str(d))
    (d / "report.md").write_text(TEXT.replace("It's worth noting that the cache", "Notably, the cache"))
    run(capsys, "feedback", "resolve", "FB-1", "--note", "Shorter", "-C", str(d))
    run(capsys, "feedback", "reopen", "FB-1", "-C", str(d))
    e = [x for x in entries(folder) if x["event"] == "reopened"][0]
    assert e["item"]["note"] == "Shorter" and "Notably, the cache" in e["rejected"]


def test_names_emails_and_urls_are_blanked(capsys, report, isolated_home):
    folder = log_on(isolated_home, 'log_redact = ["Acme", "/Project [A-Z]+/"]\n')
    d = report("## Acme rollout\n\nProject FALCON for ada@acme.com, see https://acme.com/x.\n")
    run(capsys, "feedback", "add", "ask ada@acme.com about Acme", "--quote", "Project FALCON", "-C", str(d))
    run(capsys, "feedback", "resolve", "FB-1", "--note", "ok", "-C", str(d))
    text = json.dumps(entries(folder))
    assert "acme" not in text.lower() and "FALCON" not in text and "ada@" not in text
    assert "[redacted]" in text and "[email]" in text and "[url]" in text


def test_a_log_inside_a_git_repo_must_be_ignored(capsys, report, isolated_home, tmp_path, monkeypatch):
    repo = tmp_path / "setup"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "rapport").mkdir()
    (repo / "rapport/config.toml").write_text("[writing]\nlog = true\n")
    monkeypatch.setenv("RAPPORT_CONFIG_DIR", str(repo / "rapport"))
    writinglog._GIT_OK.clear()
    d = report(TEXT)
    run(capsys, "feedback", "add", "x", "--quote", "worth noting", "-C", str(d))
    out = run(capsys, "feedback", "resolve", "FB-1", "-C", str(d))[1]
    assert "not ignored" in out and not (repo / "rapport/writing-log").exists()
    assert "NOT WRITTEN" in run(capsys, "writing", "log", "-C", str(d))[1]
    (repo / ".gitignore").write_text("/rapport/writing-log/\n")
    writinglog._GIT_OK.clear()
    run(capsys, "feedback", "add", "y", "--quote", "second paragraph", "-C", str(d))
    run(capsys, "feedback", "resolve", "FB-2", "-C", str(d))
    assert entries(repo / "rapport/writing-log")


def test_no_user_config_no_log(capsys, report, isolated_home, monkeypatch):
    monkeypatch.setenv("RAPPORT_CONFIG", "/dev/null")
    d = report("+++\n[writing]\nlog = true\n+++\n" + TEXT)
    run(capsys, "feedback", "add", "x", "--quote", "worth noting", "-C", str(d))
    run(capsys, "feedback", "resolve", "FB-1", "-C", str(d))
    assert writinglog.log_dir() is None and "off" in run(capsys, "writing", "log", "-C", str(d))[1]


def test_status_digest_decisions_done_and_forget(capsys, report, isolated_home):
    folder = log_on(isolated_home)
    a, b = report(TEXT, name="a"), report(TEXT, name="b")
    for d in (a, b):
        run(capsys, "feedback", "add", "plainer", "--quote", "worth noting", "--action", "slop", "-C", str(d))
        (d / "report.md").write_text(TEXT.replace("It's worth noting that the cache", "The cache"))
        run(capsys, "feedback", "resolve", "FB-1", "--note", "Plain", "-C", str(d))
    assert run(capsys, "writing", "add", "--approved", "Lead with the result.", "-C", str(a))[0] == 0
    run(capsys, "writing", "reject", "Never use commas.", "-C", str(a))
    out = run(capsys, "writing", "log", "-C", str(a))[1]
    assert "writing log: on" in out and "from 2 report(s)" in out and "none yet" in out
    out = run(capsys, "writing", "digest", "-C", str(a))[1]
    assert "2 entries from 2 report(s)" in out and "AI slop 2" in out and "references/writing-digest.md" in out
    assert "- approved" in out and "Lead with the result." in out and "- rejected" in out
    assert "comment: plainer" in out and "after (agent): The cache is fast." in out
    assert "marked done" in run(capsys, "writing", "digest", "--done")[1]
    assert "0 entries" in run(capsys, "writing", "digest", "-C", str(a))[1]
    assert "2 entries" in run(capsys, "writing", "digest", "--all", "-C", str(a))[1]
    data = json.loads(run(capsys, "writing", "digest", "--all", "--json", "-C", str(a))[1])
    assert {e["event"] for e in data["entries"]} >= {"round", "resolved", "rule"}
    assert "forgot 2 entries" in run(capsys, "writing", "forget", "--report", str(a))[1]   # round + item
    assert {e["report"]["path"].split("/")[-1] for e in entries(folder) if e.get("report")} == {"b"}


def test_recurring_cuts_need_three_times_in_two_reports():
    def e(key, before, after):
        return {"event": "resolved", "report": {"key": key}, "before": before, "after": after}
    log = [e("a", "It's worth noting that X works.", "X works."),
           e("a", "It's worth noting that Y fails.", "Y fails."),
           e("b", "It's worth noting that Z is slow.", "Z is slow."),
           e("b", "Some other text here.", "Other text.")]
    cuts = writinglog.recurring_cuts(log)
    assert cuts[0] == ("it's worth noting that", 3, 2)
    assert all(t >= 3 and r >= 2 for _, t, r in cuts)
