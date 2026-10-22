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
    s.resolve([b["id"]], "Added a timeline section", 2)
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


def test_static_file_is_read_only_with_hint(report, page):
    d = report(REPORT, {"reports/r/assets/demo.gif": GIF})
    build(d)
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
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--rp-accent')") == "#2050C8"
    page.emulate_media(color_scheme="dark")
    assert get(".rp-callout", "borderLeftColor") == "rgb(200, 210, 220)"
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    assert get(".rp-callout", "borderLeftColor") == "rgb(10, 20, 30)"


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
