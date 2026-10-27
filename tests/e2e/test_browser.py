"""Browser tests for the reader-feedback layer (dev only).

Needs the `playwright` Python package and a Chromium. Uses RP_CHROME if set, else
Playwright's own browser, else the newest cached headless shell in
~/Library/Caches/ms-playwright (or ~/.cache/ms-playwright).
"""

import glob
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import GIF  # noqa: E402

playwright_sync = pytest.importorskip("playwright.sync_api")
from rapport_core.build import build  # noqa: E402
from rapport_core.feedback import Store  # noqa: E402
from rapport_core.serve import make_server  # noqa: E402

MOD = "Meta" if sys.platform == "darwin" else "Control"

REPORT = """+++
title = "Browser test"
+++

## Summary

The cache key changed, so every request missed the cache once after the deploy.

A second paragraph with different words entirely.

::: collapse More detail
Hidden sentence inside the collapse.
:::

![A demo gif](assets/demo.gif)
"""


def _cached_shell():
    for base in ("~/Library/Caches/ms-playwright", "~/.cache/ms-playwright"):
        hits = sorted(glob.glob(os.path.expanduser(base) + "/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
        if hits:
            return hits[-1]
    return None


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        attempts = []
        for a in (os.environ.get("RP_CHROME"), None, _cached_shell()):
            if a not in attempts:
                attempts.append(a)
        b, last = None, None
        for exe in attempts:
            try:
                b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
                break
            except Exception as e:  # noqa: BLE001
                last = e
        if b is None:
            pytest.skip(f"no usable Chromium: {last}")
        yield b
        b.close()


@pytest.fixture
def served(report):
    servers = []

    def start(text=REPORT, sets=None):
        d = report(text, {"reports/r/assets/demo.gif": GIF})
        httpd, app, url, cfg = make_server(d, sets, port=0, pinned=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        threading.Thread(target=app.watch, args=(0.1,), daemon=True).start()
        servers.append(httpd)
        return d, app, url
    yield start
    for h in servers:
        h.shutdown()
        h.server_close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    yield pg
    ctx.close()
    assert not errors, errors


FIND = """([text]) => {
  const w = document.createTreeWalker(document.querySelector('.rp-shell'), NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const i = n.nodeValue.indexOf(text);
    if (i >= 0) {
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + text.length);
      n.parentElement.scrollIntoView({block: 'center', behavior: 'instant'});
      const rs = r.getClientRects(), a = rs[0], b = rs[rs.length - 1];
      return {x1: a.left + 1, y: a.top + a.height / 2, x2: b.right - 1, y2: b.top + b.height / 2};
    }
  }
  return null;
}"""


def select(page, text):
    page.evaluate(FIND, [text])
    box = page.evaluate(FIND, [text])
    assert box, f"text not found: {text}"
    page.mouse.move(box["x1"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x2"], box["y2"], steps=6)
    page.mouse.up()
    page.wait_for_timeout(150)
    return box


def items(d):
    return Store(d).items()


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def open_page(page, url):
    page.goto(url)
    page.wait_for_selector(".rp-fb-chipbtn")


def test_select_type_enter(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "every request missed the cache once")
    assert page.is_visible(".rp-fb-win")
    # the cursor is already in the box; the passage stays marked
    assert page.evaluate("document.activeElement.tagName") == "TEXTAREA"
    assert page.evaluate("CSS.highlights.has('rp-fb-pending')")
    page.keyboard.type("Which deploy?")
    assert page.input_value(".rp-fb-win textarea") == "Which deploy?"
    page.keyboard.press("Enter")
    it = wait_for(lambda: items(d))
    assert it[0]["comment"] == "Which deploy?" and it[0]["actions"] == []
    t = it[0]["target"]
    assert t["kind"] == "text" and t["quote"] == "every request missed the cache once" and t["lines"] == [7, 7]
    page.wait_for_selector(".rp-fb-win", state="hidden")     # the file is written before the reply lands


def test_quick_action_one_click_then_undo(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "different words entirely")
    page.click(".rp-fb-win .rp-fb-chip[data-a=delete]")
    it = wait_for(lambda: items(d))
    assert it and it[0]["actions"] == ["delete"]
    page.wait_for_selector(".rp-fb-toast.rp-show")
    assert "Delete saved" in page.text_content(".rp-fb-toast")
    page.wait_for_function("CSS.highlights.has('rp-fb-strike')")
    page.click(".rp-fb-toast button")
    assert wait_for(lambda: not items(d))


def test_alt_shortcut_saves_action(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.press("Alt+KeyV")
    it = wait_for(lambda: items(d))
    assert it and it[0]["actions"] == ["verbose"]


def test_action_plus_comment_combine(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.type("cut")
    page.click(".rp-fb-win .rp-fb-chip[data-a=slop]")          # with text typed, it's a tag
    assert page.get_attribute(".rp-fb-win .rp-fb-chip[data-a=slop]", "aria-pressed") == "true"
    assert not items(d)
    page.click(".rp-fb-win .rp-primary")
    it = wait_for(lambda: items(d))
    assert it[0]["actions"] == ["slop"] and it[0]["comment"] == "cut"


def test_block_comment_on_gif(served, page):
    d, app, url = served()
    open_page(page, url)
    img = page.locator(".rp-m-image img")
    img.scroll_into_view_if_needed()
    img.hover()
    page.wait_for_selector(".rp-fb-handle.rp-show")
    page.click(".rp-fb-handle")
    assert page.is_visible(".rp-fb-win") and "Whole image" in page.text_content(".rp-fb-what")
    page.keyboard.type("Too fast; slow the animation down")
    page.keyboard.press("Enter")
    it = wait_for(lambda: items(d))
    assert it[0]["target"]["kind"] == "block" and it[0]["target"]["module"] == "image"


def test_click_highlight_opens_item_and_edit(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "different words entirely")
    page.click(".rp-fb-win .rp-fb-chip[data-a=verbose]")
    wait_for(lambda: items(d))
    page.reload()
    page.wait_for_function("CSS.highlights.has('rp-fb-accent')")
    box = page.evaluate(FIND, ["words entirely"])
    page.mouse.click((box["x1"] + box["x2"]) / 2, box["y"])
    page.wait_for_selector(".rp-fb-win:not([hidden])")
    assert page.text_content(".rp-fb-id") == "FB-1"
    page.fill(".rp-fb-win textarea", "half the length")
    page.click(".rp-fb-win .rp-primary")
    assert wait_for(lambda: items(d)[0]["comment"] == "half the length")
    assert page.text_content(".rp-fb-marker") == "1"


def test_reload_waits_while_composing_and_keeps_draft(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.type("draft in progress")
    (d / "report.md").write_text(REPORT.replace("## Summary", "## Summary\n\nA new line from the agent."),
                                 encoding="utf-8")
    page.wait_for_selector(".rp-lr-pill", timeout=8000)
    assert page.input_value(".rp-fb-win textarea") == "draft in progress"
    page.click(".rp-lr-pill button")
    page.wait_for_selector("text=A new line from the agent.")
    page.wait_for_selector(".rp-fb-win:not([hidden])")
    assert page.input_value(".rp-fb-win textarea") == "draft in progress"


def test_idle_page_reloads_on_rebuild(served, page):
    d, app, url = served()
    open_page(page, url)
    (d / "report.md").write_text(REPORT.replace("A second paragraph", "An edited paragraph"), encoding="utf-8")
    page.wait_for_selector("text=An edited paragraph", timeout=8000)


def test_bar_mode(served, page):
    d, app, url = served(sets=['modules.feedback.on_select="bar"'])
    open_page(page, url)
    select(page, "A second paragraph")
    assert page.is_visible(".rp-fb-bar") and page.is_hidden(".rp-fb-win")
    page.keyboard.press("v")
    it = wait_for(lambda: items(d))
    assert it and it[0]["actions"] == ["verbose"]
    select(page, "different words")
    page.keyboard.press("c")
    assert page.is_visible(".rp-fb-win")


def test_panel_reveals_collapsed_item_and_shows_resolution(served, page):
    d, app, url = served()
    block = next(k for k, v in app.blocks.items() if v["src"].startswith("Hidden sentence"))
    s = Store(d)
    a = s.add({"target": {"kind": "text", "blocks": [block], "quote": "Hidden sentence", "lines": [12, 12]},
               "actions": ["unclear"], "comment": ""})
    b = s.add({"target": {"kind": "report"}, "actions": [], "comment": "Add a timeline"})
    first = app.build_id
    s.resolve([b["id"]], "Added a timeline section")
    deadline = time.time() + 5
    while app.build_id == first and time.time() < deadline:     # the version bump rebuilds the page
        time.sleep(0.05)
    open_page(page, url)
    assert page.text_content(".rp-fb-chipbtn .rp-n") == "1"
    page.click(".rp-fb-chipbtn")
    page.click(f".rp-fb-item:has-text('{a['id']}')")
    page.wait_for_function("document.querySelector('.rp-collapse').open")
    page.click(".rp-fb-filters >> text=Resolved")
    assert "Added a timeline section" in page.text_content(".rp-fb-list")


def test_general_comment_from_panel(served, page):
    d, app, url = served()
    open_page(page, url)
    page.click(".rp-fb-chipbtn")
    page.fill(".rp-fb-general textarea", "Overall: lead with the fix")
    page.click(".rp-fb-general button")
    it = wait_for(lambda: items(d))
    assert it[0]["target"]["kind"] == "report"


def test_static_file_is_a_clean_report(report, page):
    d = report(REPORT, {"reports/r/assets/demo.gif": GIF})
    build(d)
    page.goto((d / "report.html").as_uri())
    page.wait_for_selector(".rp-content")
    assert page.query_selector("[data-rp-ui].rp-fb, .rp-hint, .rp-fb-change") is None


def test_static_hint_when_switched_on(report, page):
    d = report(REPORT, {"reports/r/assets/demo.gif": GIF})
    build(d, ["modules.static-hint.enabled=true"])
    page.goto((d / "report.html").as_uri())
    page.wait_for_selector(".rp-hint")
    hint = page.text_content(".rp-hint")
    assert "rapport serve '" in hint and d.name in hint          # folder from the page's own location
    assert page.query_selector(".rp-fb-chipbtn") is None


def test_theme_css_wins_over_theme_and_module_rules(report, page):
    css = ("h2{color:rgb(1, 2, 3)}\np{font-size:21px}\n"
           ".rp-m-callout{--rp-accent:light-dark(rgb(10, 20, 30), rgb(200, 210, 220))}\n")
    d = report('+++\n[theme]\ncss = ["brand.css"]\n+++\n\n## Head\n\n::: callout Note\nBody.\n:::\n',
               {"reports/r/brand.css": css})
    assert build(d).ok
    page.goto((d / "report.html").as_uri())

    def get(sel, prop):
        return page.eval_on_selector(sel, f"e => getComputedStyle(e).{prop}")
    assert get(".rp-content h2", "color") == "rgb(1, 2, 3)"            # theme: .rp-content h2
    assert get(".rp-callout-title", "fontSize") == "21px"              # module: .rp-callout-title
    assert get(".rp-content h2", "fontSize") != "21px"
    # a token re-scoped to one module, with a dark value: follows the OS and the toggle
    assert get(".rp-callout", "borderLeftColor") == "rgb(10, 20, 30)"
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--rp-accent')") == "#2563EB"
    page.emulate_media(color_scheme="dark")
    assert get(".rp-callout", "borderLeftColor") == "rgb(200, 210, 220)"
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    assert get(".rp-callout", "borderLeftColor") == "rgb(10, 20, 30)"


SIZED = ("h2", ".rp-title", ".rp-pre", ".rp-callout-title", ".rp-toc a", ".rp-content>p")


def test_sizes_scale_with_body_size(report, page):
    d = report('## One\n\n::: callout Note\nBody.\n:::\n\n## Two\n\n```py\nx = 1\n```\n\n## Three\n\nText.\n')

    def sizes(*sets):
        assert build(d, list(sets)).ok
        page.goto((d / "report.html").as_uri())
        return {s: float(page.eval_on_selector(s, "e => getComputedStyle(e).fontSize")[:-2]) for s in SIZED}
    assert sizes()[".rp-content>p"] == 16                           # the default theme's body size
    base = sizes("theme.tokens.body-size=17px")
    assert base["h2"] == 22 and base[".rp-title"] == 36 and base[".rp-pre"] == 13.5 and base[".rp-content>p"] == 17
    big = sizes("theme.tokens.body-size=20px")
    for s in SIZED:
        assert abs(big[s] - base[s] * 20 / 17) <= .5, (s, base[s], big[s])
    assert sizes("theme.name=paper")["h2"] == 24                    # a theme's own sizes keep their pixels
    assert sizes('theme.tokens.body-size="1.1rem"')[".rp-content>p"] == 17.6     # 1.1 × 16, not 1.1²


def test_feedback_ui_keeps_its_size_at_a_larger_body_size(served, page):
    d, app, url = served(sets=["theme.tokens.body-size=20px"])
    open_page(page, url)
    select(page, "every request missed the cache once")

    def size(sel):
        return page.eval_on_selector(sel, "e => getComputedStyle(e).fontSize")
    assert size(".rp-content h2") != "22px"
    assert (size(".rp-fb-win textarea"), size(".rp-fb-head"), size(".rp-fb-chipbtn")) == ("14.5px", "12px", "12.5px")


def test_stale_token_reloads(served, page):
    d, app, url = served()
    open_page(page, url)
    app.token = "rotated"
    select(page, "A second paragraph")
    with page.expect_navigation(timeout=5000):
        page.click(".rp-fb-win .rp-fb-chip[data-a=verbose]")


# ── regressions from the review ────────────────────────────────────────────── #
def test_general_comment_survives_a_rebuild(served, page):
    d, app, url = served()
    open_page(page, url)
    page.click(".rp-fb-chipbtn")
    page.fill(".rp-fb-general textarea", "half-written overall note")
    (d / "report.md").write_text(REPORT.replace("A second paragraph", "An edited paragraph"), encoding="utf-8")
    page.wait_for_selector(".rp-lr-pill", timeout=8000)          # deferred, not reloaded
    assert page.input_value(".rp-fb-general textarea") == "half-written overall note"
    page.click(".rp-lr-pill button")
    page.wait_for_selector("text=An edited paragraph")
    page.wait_for_selector(".rp-fb-panel:not([hidden])")
    assert page.input_value(".rp-fb-general textarea") == "half-written overall note"


def test_esc_discards_with_undo_and_never_reopens(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.type("half-written thought")
    page.keyboard.press("Escape")
    assert page.is_hidden(".rp-fb-win")
    page.wait_for_selector(".rp-fb-toast.rp-show >> text=Draft discarded")
    page.click(".rp-fb-toast button")                              # Undo
    assert page.input_value(".rp-fb-win textarea") == "half-written thought"
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_selector(".rp-fb-chipbtn")
    page.wait_for_timeout(300)
    assert page.is_hidden(".rp-fb-win")


def test_option_letters_type_in_the_comment_box(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.type("Gr")
    page.keyboard.press("Alt+KeyU")
    page.keyboard.press("Alt+KeyS")
    assert page.get_attribute(".rp-fb-win .rp-fb-chip[data-a=unclear]", "aria-pressed") == "false"
    assert page.get_attribute(".rp-fb-win .rp-fb-chip[data-a=slop]", "aria-pressed") == "false"
    assert page.input_value(".rp-fb-win textarea").startswith("Gr")


def test_items_in_a_closed_section_mark_its_summary(served, page):
    d, app, url = served()
    block = next(k for k, v in app.blocks.items() if v["src"].startswith("Hidden sentence"))
    Store(d).add({"target": {"kind": "text", "blocks": [block], "quote": "Hidden sentence", "lines": [12, 12]},
                  "actions": ["unclear"], "comment": ""})
    open_page(page, url)
    page.wait_for_selector(".rp-fb-marker")
    marker = page.locator(".rp-fb-marker").bounding_box()
    summary = page.locator(".rp-collapse > summary").bounding_box()
    assert abs(marker["y"] - summary["y"]) < 4                    # counted on the summary line
    # clicking where the hidden text would be does not open it
    page.mouse.click(summary["x"] + 40, summary["y"] + summary["height"] + 12)
    page.wait_for_timeout(200)
    assert page.is_hidden(".rp-fb-win")
    page.click(".rp-fb-marker")                                    # one item: reveal and open it
    page.wait_for_function("document.querySelector('.rp-collapse').open")
    page.wait_for_selector(".rp-fb-win:not([hidden])")


def test_pane_labels_stay_out_of_quotes(served, page):
    text = REPORT + '\n::: compare {labels = ["Before", "After"]}\n```python\nold_value = 1\n```\n' \
                    '```python\nnew_value = 2\n```\n:::\n'
    d, app, url = served(text)
    open_page(page, url)
    a = page.evaluate(FIND, ["Before"])
    b = page.evaluate(FIND, ["old_value"])
    page.mouse.move(a["x1"], a["y"])
    page.mouse.down()
    page.mouse.move(b["x2"], b["y2"], steps=6)
    page.mouse.up()
    page.wait_for_timeout(150)
    page.keyboard.type("rename it")
    page.keyboard.press("Enter")
    it = wait_for(lambda: items(d))
    t = it[0]["target"]
    assert "Before" not in t["quote"] and t["quote"].startswith("old_value") and t["region"] == "Before"


def test_copy_still_works_with_the_window_open(served, browser):
    d, app, url = served()
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    try:
        open_page(pg, url)
        select(pg, "every request missed the cache once")
        assert pg.is_visible(".rp-fb-win")
        pg.keyboard.press(f"{MOD}+KeyC")
        clip = pg.evaluate("navigator.clipboard.readText()")
        assert clip == "every request missed the cache once"
        assert pg.input_value(".rp-fb-win textarea") == ""
    finally:
        ctx.close()


def test_shift_or_cmd_enter_is_a_new_line(served, page):
    d, app, url = served()
    open_page(page, url)
    select(page, "A second paragraph")
    page.keyboard.type("one")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("two")
    page.keyboard.press(f"{MOD}+Enter")
    page.keyboard.type("three")
    assert page.input_value(".rp-fb-win textarea") == "one\ntwo\nthree"
    assert not items(d)
    page.keyboard.press("Enter")
    it = wait_for(lambda: items(d))
    assert it[0]["comment"] == "one\ntwo\nthree"


def test_double_click_a_word_then_type(served, page):
    d, app, url = served()
    open_page(page, url)
    box = page.evaluate(FIND, ["entirely"])
    page.mouse.dblclick((box["x1"] + box["x2"]) / 2, box["y"])
    page.wait_for_selector(".rp-fb-win:not([hidden])")
    page.keyboard.type("drop this word")
    page.keyboard.press("Enter")
    it = wait_for(lambda: items(d))
    assert it[0]["target"]["quote"] == "entirely" and it[0]["comment"] == "drop this word"


def test_focus_and_enter_behaviour_can_be_switched_off(served, page):
    d, app, url = served(sets=["modules.feedback.autofocus=false", "modules.feedback.enter_saves=false"])
    open_page(page, url)
    select(page, "A second paragraph")
    assert page.evaluate("document.activeElement.tagName") != "TEXTAREA"
    assert page.evaluate("getSelection().toString()") == "A second paragraph"
    page.keyboard.type("x")                                   # the first key moves into the box
    page.keyboard.press("Enter")
    assert page.input_value(".rp-fb-win textarea") == "x\n" and not items(d)
    page.keyboard.press(f"{MOD}+Enter")
    it = wait_for(lambda: items(d))
    assert it[0]["comment"] == "x"


def test_logo_follows_the_theme_and_tinted_icons_take_the_text_colour(report, page):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path fill="{}" d="M0 0h8v8H0z"/></svg>'
    d = report('+++\n[modules.header]\nlogo = "light.svg"\nlogo_dark = "dark.svg"\n'
               '[[modules.footer.entries]]\nicon = "mono.svg"\ntext = "mono"\n+++\n\nBody.\n',
               {"reports/r/light.svg": svg.format("#000"), "reports/r/dark.svg": svg.format("#fff"),
                "reports/r/mono.svg": svg.format("currentColor")})
    assert build(d).ok
    page.goto((d / "report.html").as_uri())

    def shown():
        return [c for c in ("rp-light-only", "rp-dark-only")
                if page.eval_on_selector(f".rp-header .{c}", "e => getComputedStyle(e).display") != "none"]
    assert shown() == ["rp-light-only"]
    page.emulate_media(color_scheme="dark")
    assert shown() == ["rp-dark-only"]
    page.evaluate("document.documentElement.dataset.theme = 'light'")      # the toggle beats the OS
    assert shown() == ["rp-light-only"]
    page.emulate_media(color_scheme="light")
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    assert shown() == ["rp-dark-only"]
    icon = page.eval_on_selector(".rp-footer .rp-icon-tint", """e => {const s = getComputedStyle(e);
      return [s.backgroundColor, getComputedStyle(e.parentElement).color, s.maskImage || s.webkitMaskImage]}""")
    assert icon[0] == icon[1] and icon[2].startswith('url("data:image/svg+xml;base64,')


def test_toc_marks_the_section_in_view(served, page):
    filler = "\n\n".join("Filler paragraph number %d with enough words to take up a line." % i for i in range(40))
    d, app, url = served("".join(f"## Part {n}\n\n{filler}\n\n" for n in ("one", "two", "three")))
    open_page(page, url)
    active = "document.querySelector('.rp-toc a.rp-active')?.textContent"
    assert page.evaluate(active) == "Part one"
    page.evaluate("document.getElementById('part-three').scrollIntoView({block: 'start', behavior: 'instant'})")
    page.wait_for_function(f"{active} === 'Part three'")


def test_a_broken_feedback_file_is_shown_not_taken_for_no_feedback(served, page):
    d, app, url = served()
    (d / ".rapport" / "feedback.json").write_text("{not json", encoding="utf-8")
    open_page(page, url)
    page.wait_for_selector(".rp-fb-toast.rp-show")
    assert "is not valid JSON" in page.text_content(".rp-fb-toast")
    assert page.is_hidden(".rp-fb-banner")              # the server is up: not the offline banner


def _round(d, app, edit, comments):
    """Comment on the served report, apply `edit` to report.md, resolve: one feedback round."""
    from rapport_core.feedback import make_item
    s, blocks = Store(d), dict(app.blocks)
    made = []
    for start, payload in comments:
        block = next(k for k, v in blocks.items() if v["src"].startswith(start))
        made.append(s.add(make_item({"target": {"kind": "text", "blocks": [block], "quote": start}, **payload},
                                    app.cfg, blocks, 1), blocks))
    first = app.build_id
    (d / "report.md").write_text(edit((d / "report.md").read_text()))
    assert wait_for(lambda: app.build_id > first)
    first = app.build_id
    for it in made:
        s.resolve([it["id"]], "fixed " + it["id"])
    assert wait_for(lambda: app.build_id > first)          # the version bump rebuilds the page
    return made


def test_change_bars_after_a_round(served, page):
    d, app, url = served()
    a, b = _round(d, app, lambda t: t.replace("so every request missed the cache once after the deploy",
                                              "so each request missed once").replace(
                                  "A second paragraph with different words entirely.\n\n", ""),
                  [("The cache key", {"actions": ["verbose"], "comment": "say it shorter"}),
                   ("A second paragraph", {"actions": ["delete"]})])
    open_page(page, url)
    page.wait_for_selector(".rp-fb-change")
    bar, cut = page.query_selector(".rp-fb-change.rp-bar"), page.query_selector(".rp-fb-change.rp-cut")
    assert len(page.query_selector_all(".rp-fb-change")) == 2 and bar and cut
    content = page.evaluate("document.querySelector('.rp-content').getBoundingClientRect().right")
    para = page.evaluate("""[...document.querySelectorAll('[data-rp-id]')]
        .find(e => e.textContent.includes('each request missed once')).getBoundingClientRect().toJSON()""")
    box = bar.bounding_box()
    assert box["x"] > content - 1                                   # in the right margin
    assert abs(box["y"] - para["top"]) < 2 and abs(box["height"] - para["height"]) < 2

    assert "rp-edited" in bar.get_attribute("class") and "rp-removed" in cut.get_attribute("class")
    assert page.evaluate("!CSS.highlights.has('rp-fb-added')")      # only while hovering

    bar.hover()
    page.wait_for_selector(".rp-fb-card", state="visible")
    text = page.text_content(".rp-fb-card")
    assert f"fixed {a['id']}" in text and "say it shorter" in text and "Too verbose" in text
    assert page.evaluate("[...CSS.highlights.get('rp-fb-added')].map(r => r.toString())") == ["each", "once."]
    assert "every" in page.text_content(".rp-fb-card-del del")      # removed words, struck through
    cut.hover()
    page.wait_for_function("document.querySelector('.rp-fb-card').textContent.includes('Removed')")
    assert f"fixed {b['id']}" in page.text_content(".rp-fb-card")
    assert page.text_content(".rp-fb-card-del") == "A second paragraph with different words entirely."
    assert page.evaluate("!CSS.highlights.has('rp-fb-added')")      # nothing left on the page to mark

    cut.click()                                                     # lists its items in the panel
    page.wait_for_selector(".rp-fb-panel", state="visible")
    assert "These changes" in page.text_content(".rp-fb-filters")
    assert b["id"] in page.text_content(".rp-fb-list") and a["id"] not in page.text_content(".rp-fb-list")

    page.click(".rp-fb-chgbtn")                                     # off, and it stays off on reload
    assert page.get_attribute(".rp-fb-chgbtn", "aria-pressed") == "false"
    assert page.query_selector(".rp-fb-change") is None
    open_page(page, url)
    page.wait_for_selector(".rp-fb-chgbtn", state="visible")
    assert page.query_selector(".rp-fb-change") is None
    page.click(".rp-fb-chgbtn")
    page.wait_for_selector(".rp-fb-change")


def test_an_added_block_is_green_and_lights_up_whole(served, page):
    d, app, url = served()
    _round(d, app, lambda t: t.replace("A second paragraph", "A new example, as asked.\n\nA second paragraph"),
           [("The cache key", {"comment": "add an example"})])
    open_page(page, url)
    page.wait_for_selector(".rp-fb-change")
    bar = page.query_selector(".rp-fb-change.rp-bar")
    assert "rp-added" in bar.get_attribute("class")
    bar.hover()
    page.wait_for_selector(".rp-fb-card", state="visible")
    assert page.text_content(".rp-fb-card-what") == "Added since v1"
    assert page.evaluate("[...CSS.highlights.get('rp-fb-added')].map(r => r.toString()).join('')") == \
        "A new example, as asked."
    page.mouse.move(5, 5)
    page.wait_for_function("!CSS.highlights.has('rp-fb-added')")


def test_change_bars_follow_config_and_a_closed_section(served, page):
    d, app, url = served(sets=["modules.feedback.changes=false"])
    _round(d, app, lambda t: t.replace("Hidden sentence inside", "Clearer sentence inside"),
           [("Hidden sentence", {"actions": ["unclear"]})])
    open_page(page, url)
    page.wait_for_selector(".rp-fb-chgbtn", state="visible")
    assert page.query_selector(".rp-fb-change") is None             # off by config until switched on
    page.click(".rp-fb-chgbtn")
    page.wait_for_selector(".rp-fb-change")
    summary = page.evaluate("document.querySelector('.rp-collapse summary').getBoundingClientRect().toJSON()")
    box = page.query_selector(".rp-fb-change").bounding_box()       # the block is hidden: its summary carries it
    assert abs(box["y"] - summary["top"]) < 2 and abs(box["height"] - summary["height"]) < 2
