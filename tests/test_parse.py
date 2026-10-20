from conftest import write
from rapport_core import config as config_mod
from rapport_core.parse import parse, split_params


def doc_of(d):
    cfg, body = config_mod.load_for_report(d)
    return parse(body, cfg.registry, cfg)


def flat(nodes):
    for n in nodes:
        yield n
        yield from flat(n.children)


def test_split_params():
    assert split_params('Heads up {tone = "warning"}') == ("Heads up", {"tone": "warning"}, None)
    assert split_params("Use {x} carefully {open = true}") == ("Use {x} carefully", {"open": True}, None)
    assert split_params("Just a title") == ("Just a title", {}, None)
    assert split_params("{tone: 'x'}")[2] is not None


def test_directive_positional_and_params(report):
    d = report('::: callout Heads up {tone = "warning"}\nBody **text**.\n:::\n')
    doc = doc_of(d)
    assert not doc.errors
    (n,) = doc.nodes
    assert n.module == "callout" and n.params == {"tone": "warning", "title": "Heads up"}
    assert n.lines == (1, 3) and n.children[0].module == "paragraph"


def test_nesting_more_colons_and_same_length(report):
    d = report(":::: compare\n::: pane A\none\n:::\n::: pane B\ntwo\n:::\n::::\n\n"
               "::: collapse Outer\n::: callout\ninner\n:::\nafter inner\n:::\n")
    doc = doc_of(d)
    assert not doc.errors
    cmp, col = doc.nodes
    assert [c.module for c in cmp.children] == ["pane", "pane"]
    assert [c.params["title"] for c in cmp.children] == ["A", "B"]
    assert [c.module for c in col.children] == ["callout", "paragraph"]
    assert col.lines == (10, 15)


def test_fence_inside_container_does_not_close_it(report):
    d = report("::: collapse Code\n```markdown\n::: callout\nshown, not parsed\n:::\n```\nstill inside\n:::\n\nafter\n")
    doc = doc_of(d)
    assert not doc.errors
    col, after = doc.nodes
    assert [c.module for c in col.children] == ["code", "paragraph"]
    assert ":::" in col.children[0].text
    assert after.module == "paragraph" and after.lines == (10, 10)


def test_errors_carry_lines_and_suggestions(report):
    d = report("intro\n\n::: comprae\nx\n:::\n\n::: collapse T {open = yes}\ny\n:::\n\n"
               "::: callout {tone = \"loud\"}\nz\n:::\n\n::: collapse T {nope = 1}\nw\n:::\n")
    msgs = {(e.line, e.message) for e in doc_of(d).errors}
    assert (3, "unknown module 'comprae' (did you mean 'compare'?)") in msgs
    assert any(line == 7 and "TOML inline table" in m for line, m in msgs)
    assert any(line == 11 and "'loud' is not one of" in m for line, m in msgs)
    assert any(line == 15 and "has no param 'nope'" in m for line, m in msgs)


def test_front_matter_keeps_line_numbers(report):
    d = report('+++\ntitle = "T"\nsubtitle = "S"\n+++\n\n## Head\n\nPara on line 8.\n')
    doc = doc_of(d)
    head, para = doc.nodes
    assert head.lines == (6, 6) and para.lines == (8, 8)
    assert para.src == "Para on line 8."


def test_inline_module_and_code_params(report):
    d = report('Status :badge[shipped]{tone = "ok"} now.\n\n```python {title = "a.py", hl = [2]}\nx = 1\ny = 2\n```\n')
    doc = doc_of(d)
    p, code = doc.nodes
    assert any(c.type == "rp_inline" and c.meta["params"] == {"tone": "ok"} for c in p.inline.children)
    assert code.module == "code" and code.attrs["lang"] == "python"
    assert code.params["title"] == "a.py" and code.params["hl"] == [2] and code.text == "x = 1\ny = 2"


def test_image_paragraph_becomes_image_node(report):
    d = report("![Hover state](assets/a.gif \"Caption\")\n\nText with ![inline](assets/b.png) image.\n")
    img, para = doc_of(d).nodes
    assert img.module == "image" and img.attrs == {"src": "assets/a.gif", "alt": "Hover state", "title": "Caption"}
    assert para.module == "paragraph"


def test_unclosed_container_warns(report):
    doc = doc_of(report("::: collapse T\nnever closed\n"))
    assert any("never closed" in w.message for w in doc.warnings)


def test_toml_body_module(report, isolated_home):
    write(isolated_home / "proj/reports/r/rapport-modules/kv/module.toml",
          'kind = "block"\nsummary = "k/v"\nbody = "toml"\n')
    write(isolated_home / "proj/reports/r/rapport-modules/kv/template.html",
          '<dl>{% for k, v in node.attrs.data.items() %}<dt>{{ k }}</dt><dd>{{ v }}</dd>{% endfor %}</dl>')
    d = report('::: kv\nlatency = "40 ms"\nerrors = 0\n:::\n')
    (n,) = doc_of(d).nodes
    assert n.attrs["data"] == {"latency": "40 ms", "errors": 0}


def test_block_ids_are_content_derived(report):
    a = doc_of(report("## A\n\nfirst\n\nsecond\n", name="a"))
    b = doc_of(report("## A\n\nnew paragraph\n\nfirst\n\nsecond\n", name="b"))
    ids_a = {n.src: n.id for n in flat(a.nodes)}
    ids_b = {n.src: n.id for n in flat(b.nodes)}
    assert ids_a["first"] == ids_b["first"] and ids_a["second"] == ids_b["second"]


def test_duplicate_blocks_get_distinct_ids(report):
    doc = doc_of(report("same\n\nsame\n"))
    assert len({n.id for n in doc.nodes}) == 2
