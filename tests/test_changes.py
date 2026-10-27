import json

from rapport_core.build import build
from rapport_core.changes import changes, load_rounds, outline, word_diff
from rapport_core.render import text_of
from rapport_core.feedback import Store, make_item
from rapport_core.util import state_dir

SRC = """## Summary

Alpha stays as it is.

Beta is far too long and wordy for what it says.

Gamma should go away entirely.

::: callout Note
Inside the callout.
:::

Delta stays as well.
"""


def bid(blocks, start):
    return next(k for k, v in blocks.items() if v["src"].startswith(start))


def comment(store, cfg, blocks, start, **kw):
    target = {"kind": "text", "blocks": [bid(blocks, start)], "quote": start}
    payload = {"target": target, "actions": kw.get("actions", []), "comment": kw.get("comment", "")}
    return store.add(make_item(payload, cfg, blocks, 1), blocks)


def round_one(report, edited):
    """Comment on SRC, edit it to `edited`, resolve everything: what the page shows next."""
    d = report(SRC)
    res = build(d)
    s = Store(d)
    items = [comment(s, res.config, res.blocks, "Beta", actions=["verbose"]),
             comment(s, res.config, res.blocks, "Gamma", actions=["delete"]),
             comment(s, res.config, res.blocks, "Inside", comment="say more"),
             s.add(make_item({"target": {"kind": "report"}, "comment": "times in UTC"}, res.config, res.blocks, 1),
                   res.blocks)]
    (d / "report.md").write_text(edited)
    new = build(d)
    assert changes(d, new.blocks, s.items()) is None          # nothing resolved yet: no bars
    for it, note in zip(items, ["cut it", "removed it", "said more", "UTC"]):
        s.resolve([it["id"]], note)
    return d, s, new, [i["id"] for i in items]


def test_a_round_marks_edits_and_removals_with_their_items(report):
    edited = SRC.replace("Beta is far too long and wordy for what it says.", "Beta is short.") \
        .replace("Gamma should go away entirely.\n\n", "").replace("Inside the callout.", "Inside the callout, more.")
    d, s, new, (fb_beta, fb_gamma, fb_inside, fb_report) = round_one(report, edited)
    ch = changes(d, new.blocks, s.items())
    bars = {new.blocks[c["id"]]["src"].split("\n")[0]: c["items"] for c in ch["changed"]}
    # the callout's own text is unchanged, so its paragraph is marked, not the callout
    assert bars == {"Beta is short.": [fb_beta], "Inside the callout, more.": [fb_inside]}
    [cut] = ch["removed"]
    assert cut["items"] == [fb_gamma] and cut["text"] == "Gamma should go away entirely." and cut["count"] == 1
    assert new.blocks[cut["before"]]["module"] == "callout"
    assert ch["general"] == [fb_report] and ch["since"] == 1 and ch["round"] == 1
    beta = next(c for c in ch["changed"] if fb_beta in c["items"])
    assert beta["kind"] == "edited" and [r["quote"] for r in beta["added"]] == ["short."]
    assert beta["removed"] == ["far too long and wordy for what it says."]
    assert beta["added"][0]["prefix"] == "Beta is" and beta["added"][0]["suffix"] == ""


def test_word_diff_runs_carry_their_context():
    added, removed = word_diff("The deploy went out at 14:02.", "All times UTC. The deploy went out at 14:05.")
    assert added == [{"quote": "All times UTC.", "prefix": "", "suffix": "The deploy went out at 14:05."},
                     {"quote": "14:05.", "prefix": "All times UTC. The deploy went out at", "suffix": ""}]
    assert removed == ["14:02."]


def test_block_text_is_what_the_reader_sees():
    html = ('<div><p>Use <code>snake_case</code> &amp; <b>bold</b></p><button data-rp-ui>Copy</button>'
            '<script>var a = "<p>x</p>";</script><img src=x alt=y><span>  end </span></div>')
    assert text_of(html) == "Use snake_case & bold end"


def test_renaming_a_heading_marks_the_heading_not_its_section(report):
    d, s, new, _ = round_one(report, SRC.replace("## Summary", "## Summary of results"))
    ch = changes(d, new.blocks, s.items())
    assert [new.blocks[c["id"]]["module"] for c in ch["changed"]] == ["heading"]
    assert ch["changed"][0]["items"] == [] and not ch["removed"]       # not tied to an item


def test_a_new_container_is_one_bar_and_an_insertion_belongs_to_the_block_before_it(report):
    edited = SRC.replace("Beta is far too long and wordy for what it says.\n",
                         "Beta is far too long and wordy for what it says.\n\n"
                         "::: collapse Example\nAn example, as asked.\n:::\n")
    d, s, new, (fb_beta, *_) = round_one(report, edited)
    ch = changes(d, new.blocks, s.items())
    assert [(new.blocks[c["id"]]["module"], c["items"], c["kind"]) for c in ch["changed"]] == \
        [("collapse", [fb_beta], "added")]


def test_rounds_start_when_nothing_is_open_and_keep_only_what_is_needed(report):
    d = report(SRC)
    res = build(d)
    s = Store(d)
    a = comment(s, res.config, res.blocks, "Alpha", comment="one")
    b = comment(s, res.config, res.blocks, "Beta", comment="two")
    assert a["round"] == b["round"] == 1 and list(load_rounds(d)) == ["1"]
    assert [e["id"] for e in load_rounds(d)["1"]["blocks"]] == [e["id"] for e in outline(res.blocks)]
    s.resolve([a["id"]], "done")
    assert comment(s, res.config, res.blocks, "Gamma", comment="three")["round"] == 1    # b is still open
    s.resolve([b["id"]], "done")
    s.resolve(["FB-3"], "done")
    c = comment(s, res.config, res.blocks, "Delta", comment="next round")
    assert c["round"] == 2 and sorted(load_rounds(d)) == ["1", "2"]     # 1 is still the one shown
    s.resolve([c["id"]], "done")
    assert comment(s, res.config, res.blocks, "Alpha", comment="third")["round"] == 3
    assert sorted(load_rounds(d)) == ["2", "3"]
    assert json.loads((state_dir(d) / "rounds.json").read_text())["format"] == 1


def test_items_without_a_round_and_a_missing_baseline_show_nothing(report):
    d = report(SRC)
    res = build(d)
    s = Store(d)
    old = s.add(make_item({"target": {"kind": "report"}, "comment": "legacy"}, res.config, res.blocks, 1))
    assert "round" not in old
    s.resolve([old["id"]], "done")
    assert changes(d, res.blocks, s.items()) is None
    new = comment(s, res.config, res.blocks, "Alpha", comment="x")
    (state_dir(d) / "rounds.json").unlink()
    s.resolve([new["id"]], "done")
    assert changes(d, res.blocks, s.items()) is None


def test_cli_add_joins_the_round(report, capsys):
    from rapport_core.cli import main
    d = report(SRC)
    build(d)
    assert main(["feedback", "add", "tighten this", "--quote", "far too long", "-C", str(d)]) == 0
    assert Store(d).items()[0]["round"] == 1 and "1" in load_rounds(d)
