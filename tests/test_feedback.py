import threading

import pytest

from rapport_core import config as config_mod
from rapport_core.build import build
from rapport_core.feedback import Store, blocks_with_quote, describe, format_items, locate, make_item, quote_in
from rapport_core.util import RapportError, read_state

SRC = "## Summary\n\nThe cache key changed, so every request missed once.\n\nSecond paragraph here.\n"


def setup(report):
    d = report(SRC)
    res = build(d)
    para = next(k for k, v in res.blocks.items() if v["src"].startswith("The cache key"))
    return d, res.config, res.blocks, para


def test_make_item_validation(report):
    d, cfg, blocks, para = setup(report)
    t = {"kind": "text", "blocks": [para], "quote": "every request missed once"}
    item = make_item({"target": t, "actions": ["verbose"]}, cfg, blocks, 1)
    assert item["target"]["lines"] == [3, 3] and item["target"]["heading_path"] == ["Summary"]
    assert item["action_labels"]["verbose"]["label"] == "Too verbose"
    with pytest.raises(RapportError, match="unknown action"):
        make_item({"target": t, "actions": ["nope"]}, cfg, blocks, 1)
    with pytest.raises(RapportError, match="pick an action or write a comment"):
        make_item({"target": t}, cfg, blocks, 1)
    with pytest.raises(RapportError, match="block not found"):
        make_item({"target": {"kind": "text", "blocks": ["zzz"], "quote": "not in the report"}, "comment": "c"},
                  cfg, blocks, 1)
    with pytest.raises(RapportError, match="target must be a dict"):
        make_item({"target": "x", "comment": "c"}, cfg, blocks, 1)
    with pytest.raises(RapportError, match="actions must be a list"):
        make_item({"target": t, "actions": "verbose"}, cfg, blocks, 1)


def test_required_comment(report, isolated_home):
    (isolated_home / "proj").mkdir(parents=True, exist_ok=True)
    (isolated_home / "proj/rapport.toml").write_text(
        '[[modules.feedback.actions]]\nid = "why"\nlabel = "Why?"\ncomment = "required"\ninstruction = "Explain."\n')
    d, cfg, blocks, para = setup(report)
    with pytest.raises(RapportError, match="needs a comment"):
        make_item({"target": {"kind": "block", "blocks": [para]}, "actions": ["why"]}, cfg, blocks, 1)


def test_store_lifecycle_and_dedupe(report):
    d, cfg, blocks, para = setup(report)
    s = Store(d)
    fields = make_item({"target": {"kind": "text", "blocks": [para], "quote": "missed once"},
                        "actions": ["delete"]}, cfg, blocks, 1)
    a = s.add(fields)
    again = s.add(fields)
    assert again["id"] == a["id"] and again["duplicate"]
    assert len(s.items()) == 1
    s.update(a["id"], {"comment": "really"})
    _, version = s.resolve([a["id"]], "removed it")
    item = s.items()[0]
    assert item["status"] == "resolved" and item["resolution"] == {**item["resolution"], "note": "removed it",
                                                                   "version": version} and version == 2
    with pytest.raises(RapportError, match="not open: FB-1"):
        s.resolve([a["id"]], "again")
    assert read_state(d)["version"] == 2              # a refused resolve bumps nothing
    s.reopen([a["id"]])
    assert s.items()[0]["status"] == "open" and "resolution" not in s.items()[0]
    s.delete(a["id"])
    assert s.items() == []


def test_concurrent_adds_get_unique_ids(report):
    d, cfg, blocks, para = setup(report)
    s = Store(d)

    def add(i):
        s.add({"target": {"kind": "report"}, "actions": [], "comment": f"c{i}"})
    threads = [threading.Thread(target=add, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ids = [i["id"] for i in s.items()]
    assert len(ids) == 40 == len(set(ids))


def test_parallel_resolves_each_get_their_own_version(report):
    """An agent resolves items with parallel tool calls. Bumping the version outside the
    store's lock lost most of 40 bumps here; inside it, each resolve gets its own."""
    d, cfg, blocks, para = setup(report)
    s = Store(d)
    ids = [s.add({"target": {"kind": "report"}, "actions": [], "comment": f"c{i}"})["id"] for i in range(40)]
    threads = [threading.Thread(target=s.resolve, args=([i], f"fixed {i}")) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    state = read_state(d)
    assert state["version"] == 41
    assert sorted(h["note"] for h in state["history"]) == sorted(f"fixed {i}" for i in ids)
    assert sorted(i["resolution"]["version"] for i in s.items()) == list(range(2, 42))


def test_quotes_with_code_and_emphasis_marks_are_found():
    src = "Set `max_paragraph_words` to *zero*, or edit ~/.config/rapport/config.toml."
    for quote in ("max_paragraph_words to zero", "edit ~/.config/rapport/config.toml"):
        assert quote_in(quote, src), quote
        assert blocks_with_quote(quote, {"p-1": {"src": src}}) == ["p-1"], quote
    assert not quote_in("something else entirely", src)


def test_locate_statuses():
    t = {"src": "The cache key changed.", "lines": [3, 3]}
    assert locate(t, "a\n\nThe cache key changed.\n") == ([3, 3], "exact")
    assert locate(t, "new\n\nx\n\nThe cache key changed.\n") == ([5, 5], "moved")
    multi = {"src": "line one\nline two", "lines": [1, 2]}
    assert locate(multi, "x\nline   one\nline two\n")[1] == "moved"          # whitespace-insensitive
    assert locate(t, "a\n\nThe cache key has changed.\n") == ([3, 3], "fuzzy")
    assert locate(t, "something else entirely\n")[1] == "stale"


def test_cli_output_for_agents(report):
    d, cfg, blocks, para = setup(report)
    s = Store(d)
    s.add(make_item({"target": {"kind": "text", "blocks": [para], "quote": "every request missed once"},
                     "actions": ["slop"], "comment": "too generic"}, cfg, blocks, 1))
    s.add(make_item({"target": {"kind": "report"}, "comment": "add a timeline"}, cfg, blocks, 1))
    # the agent edits above the passage: the item must follow it
    (d / "report.md").write_text("## Summary\n\nInserted.\n\n" + SRC.split("\n\n", 1)[1], encoding="utf-8")
    cfg2, _ = config_mod.load_for_report(d)
    src = (d / "report.md").read_text()
    out = format_items(describe(s.items(), cfg2, src), "report.md", src, 1)
    assert "FB-1 · AI slop · Summary · report.md:5 · (moved since the comment)" in out
    assert "do: Rewrite this in plain, concise" in out
    assert "comment: too generic" in out and "learn: after fixing: distil the pattern" in out \
        and "Only on a yes" in out and "--approved" in out
    assert "│    5  The cache key changed" in out
    assert out.index("FB-2") > out.index("FB-1") and "whole report" in out


def test_multi_block_selection_is_located_per_block(report):
    d = report("Para one ends here.\n\n::: collapse Steps\n1. first\n2. second\n:::\n")
    res = build(d)
    para = next(k for k, v in res.blocks.items() if v["module"] == "paragraph")
    lst = next(k for k, v in res.blocks.items() if v["module"] == "list")
    item = make_item({"target": {"kind": "text", "blocks": [para, lst], "quote": "ends here. Steps first second"},
                      "actions": ["verbose"]}, res.config, res.blocks, 1)
    src = (d / "report.md").read_text()
    assert locate(item["target"], src) == ([1, 5], "exact")
    (d / "report.md").write_text("New first line.\n\n" + src, encoding="utf-8")
    assert locate(item["target"], (d / "report.md").read_text()) == ([3, 7], "moved")
    out = describe([{**item, "id": "FB-1", "status": "open"}], res.config, (d / "report.md").read_text())
    assert out[0]["located"]["quote_found"] is True


# ── regressions from the review ────────────────────────────────────────────── #
def test_locate_matches_whole_lines_nearest_the_old_position():
    src = "### Results\n\nintro\n\n## Results\n\nbody\n"
    assert locate({"src": "## Results", "lines": [5, 5]}, src) == ([5, 5], "exact")
    twice = "TBD\n\nmiddle\n\nTBD\n"
    assert locate({"src": "TBD", "lines": [5, 5]}, twice) == ([5, 5], "exact")
    assert locate({"src": "TBD", "lines": [5, 5]}, "new\n" + twice) == ([6, 6], "moved")


def test_locate_ignores_markup_changes():
    t = {"src": "Some **bold** claim here.", "lines": [1, 1]}
    assert locate(t, "x\n\nSome _bold_ claim here.\n") == ([3, 3], "moved")


def test_stale_block_id_is_recovered_by_quote(report):
    d, cfg, blocks, para = setup(report)
    item = make_item({"target": {"kind": "text", "blocks": ["paragraph-000000"], "quote": "every request missed"},
                      "comment": "which deploy?"}, cfg, blocks, 1)
    assert item["target"]["blocks"] == [para]


def test_region_is_kept_and_shown(report):
    d, cfg, blocks, para = setup(report)
    item = make_item({"target": {"kind": "block", "blocks": [para], "region": "After"}, "comment": "c"}, cfg, blocks, 1)
    assert item["target"]["region"] == "After"
    src = (d / "report.md").read_text()
    out = format_items(describe([{**item, "id": "FB-1", "status": "open"}], cfg, src), "report.md", src, 1)
    assert "in “After”" in out


def test_titles_in_params_count_as_quoted_text(report):
    d = report('::: callout {title = "Breaking change"}\nThe key changed.\n:::\n')
    res = build(d)
    callout = next(k for k, v in res.blocks.items() if v["module"] == "callout")
    para = next(k for k, v in res.blocks.items() if v["module"] == "paragraph")
    item = make_item({"target": {"kind": "text", "blocks": [callout, para], "quote": "Breaking change The key"},
                      "actions": ["unclear"]}, res.config, res.blocks, 1)
    out = describe([{**item, "id": "FB-1", "status": "open"}], res.config, (d / "report.md").read_text())
    assert out[0]["located"]["quote_found"] is True


def test_patch_cannot_empty_an_item(report):
    d, cfg, blocks, para = setup(report)
    from rapport_core.feedback import check_content
    s = Store(d)
    a = s.add(make_item({"target": {"kind": "report"}, "comment": "c"}, cfg, blocks, 1))
    with pytest.raises(RapportError, match="pick an action or write a comment"):
        s.update(a["id"], {"comment": "", "actions": []},
                 validate=lambda it: check_content(it["actions"], it["comment"], cfg))
    assert s.items()[0]["comment"] == "c"
