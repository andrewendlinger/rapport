"""The rows come from rapport_core.reports, the same as `rapport list`, grouped by when each
report was last active. A served page watches every listed report's files, so it rebuilds
(and reloads) when one is edited, served, stopped, commented on or published.

Thumbnails, in order: the figure the report's `thumbnail` front matter names (an image file, or
text from the figure's source line), the first figure in its report.html (every image there is
already a data: URI), a screenshot of the page's top (headless Chrome or Chromium), a tile
with the project's initials. Shrunk with a local tool (sips on macOS, ImageMagick elsewhere)
and cached on the files' mtimes and sizes, so only a changed report is looked at again.
Screenshots run a few at once within a time budget per build, so the first build of a hub
stays quick; reports left over show their tile until a later build."""
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from rapport_core import reports
from rapport_core.util import BLOCKS_FILE, deep_get, state_dir

# (id, label, newer than this many days, open at first)
GROUPS = [("today", "Today", 1, True), ("week", "This week", 7, True), ("month", "This month", 31, False),
          ("older", "Older", None, False)]


def _when(iso, now):
    """Short, since the group already says roughly when: 15:03, yesterday, Mon, 12 Nov, 12 Nov 2025."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return str(iso)
    days = (now.date() - t.date()).days
    if days <= 0:
        return t.strftime("%H:%M")
    if days == 1:
        return "yesterday"
    if days < 7:
        return t.strftime("%a")
    return t.strftime("%-d %b") if t.year == now.year else t.strftime("%-d %b %Y")


def _age(iso, now):
    """Whole days since `iso`; never negative, so a clock ahead of this one still lands in Today."""
    try:
        return max(0, (now.date() - datetime.fromisoformat(iso).date()).days)
    except (TypeError, ValueError):
        return 10 ** 6


IMG_RE = re.compile(rb'<img\b([^>]*?)\bsrc="data:(image/(?:png|jpeg|gif|webp|svg\+xml));base64,([A-Za-z0-9+/=]+)"')
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp", ".svg": "image/svg+xml"}
MIN_BYTES = 1500            # smaller: an icon, not a figure
RAW_MAX = 160_000           # a raster image used as it is when no tool can shrink it
SVG_MAX = 400_000
TILES = 5                   # tile colours (style.css: .is-c0 … .is-c4)
# for a page without figures: a screenshot of its top, taller than a card shows (it crops from the top)
BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]
SHOT_ARGS = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
             "--virtual-time-budget=1500", "--window-size=800,660", "--no-first-run", "--disable-extensions",
             "--use-mock-keychain", "--password-store=basic",       # never the macOS keychain
             "--host-resolver-rules=MAP * ~NOTFOUND"]                # reports are self-contained: no network
SHOT_TIMEOUT = 15           # seconds for one screenshot
SHOT_BUDGET = 10            # seconds of screenshots per build (in parallel); the rest come on later builds
WORKERS = 4


class NotYet(Exception):
    """No thumbnail this build, and nothing to cache: a screenshot that waits for a later build."""


def first_image(html: bytes, start: int | None = None, end: int | None = None):
    """(mime, bytes) of the first figure in the content of a built report (or between `start`
    and `end`), or None. The dark variant of a diagram, icons and other chrome (data-rp-ui) are skipped."""
    if start is None:
        start = max(html.find(b'id="rp-content"'), 0)
    for m in IMG_RE.finditer(html, start, len(html) if end is None else end):
        attrs = m.group(1)
        if b"rp-diagram-dark" in attrs or b"rp-icon" in attrs or b"data-rp-ui" in attrs:
            continue
        data = base64.b64decode(m.group(3))
        if len(data) >= MIN_BYTES:
            return m.group(2).decode(), data
    return None


def named_figure(d: Path, value: str, html: bytes | None):
    """The figure the front matter's `thumbnail` names: an image file in the report folder, or
    a figure inside the first block whose source line mentions it (a file name, a caption:
    blocks.json holds each block's source and lines). None if nothing matches."""
    p = (d / value).resolve()
    if TYPES.get(p.suffix.lower()) and p.is_relative_to(d.resolve()) and p.is_file():
        return TYPES[p.suffix.lower()], p.read_bytes()
    if html is None:
        return None
    try:
        blocks = json.loads((state_dir(d) / BLOCKS_FILE).read_text(encoding="utf-8")).get("blocks", {})
    except (OSError, ValueError):
        return None
    items = [(bid, b) for bid, b in blocks.items() if isinstance(b, dict)]
    for i, (bid, b) in enumerate(items):
        if value not in str(b.get("src", "")):
            continue
        at = html.find(f'data-rp-id="{bid}"'.encode())
        if at < 0:
            continue
        last = (b.get("lines") or [0, 0])[-1]
        after = next((n for n, nb in items[i + 1:] if (nb.get("lines") or [0])[0] > last), None)  # not nested
        end = html.find(f'data-rp-id="{after}"'.encode(), at) if after else -1
        found = first_image(html, at, end if end > 0 else None)
        if found:
            return found
    return None


def browser(ctx):
    configured = ctx.module("reports").get("browser")
    return next((p for p in map(ctx.tool, [configured] if configured else BROWSERS) if p), None)


def screenshot(exe: str, page: Path, timeout: float = SHOT_TIMEOUT):
    """("image/png", bytes) of the top of the page, or None."""
    with tempfile.TemporaryDirectory(prefix="rapport-shot-", ignore_cleanup_errors=True) as tmp:
        out = Path(tmp) / "shot.png"
        # headless Chrome started from a server may not exit after writing the screenshot,
        # so wait for the file instead, then end its whole process group
        try:
            proc = subprocess.Popen([exe, *SHOT_ARGS, f"--user-data-dir={tmp}/profile", f"--screenshot={out}",
                                     page.as_uri()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return None
        try:
            deadline, size = time.time() + timeout, -1
            while time.time() < deadline:
                now = out.stat().st_size if out.is_file() else -1
                if now > 0 and now == size:         # written, and no longer growing
                    break
                if proc.poll() is not None and now <= 0:
                    break
                size = now
                time.sleep(0.2)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
        try:
            data = out.read_bytes()
        except OSError:
            return None
        return ("image/png", data) if data else None


def shrink(ctx, mime: str, data: bytes, px: int):
    """A smaller PNG, the image as it is (an SVG, or a small raster), or None."""
    if mime == "image/svg+xml":
        return (mime, data) if len(data) <= SVG_MAX else None
    sips, magick = ctx.tool("sips"), ctx.tool("magick") or ctx.tool("convert")
    if sips or magick:
        with tempfile.TemporaryDirectory(prefix="rapport-thumb-", ignore_cleanup_errors=True) as tmp:
            src, out = Path(tmp) / ("in" + next((k for k, v in TYPES.items() if v == mime), "")), Path(tmp) / "out.png"
            src.write_bytes(data)
            try:
                if sips:
                    ctx.run([sips, "-Z", str(px), "-s", "format", "png", str(src), "--out", str(out)])
                else:
                    ctx.run([magick, f"{src}[0]", "-thumbnail", f"{px}x{px}>", str(out)])
                return "image/png", out.read_bytes()
            except (ValueError, OSError):
                pass
    return (mime, data) if len(data) <= RAW_MAX else None


def _stamp(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "-"


def thumbnail(ctx, r: dict, deadline: float) -> tuple[str, str, bool]:
    """(data: URI or "" for the initials tile, a problem with the front matter's `thumbnail`,
    whether a later build may still find one).
    Cached on the files it comes from. A screenshot is taken only before `deadline`; one that
    failed is retried the next day, not on every build."""
    cfg = ctx.module("reports")
    d = Path(r["dir"])
    page = d / reports.OUTPUT
    value, px = r["thumbnail"], cfg.get("thumbnail_px", 480)
    exe = browser(ctx) if cfg.get("screenshots", True) else None
    tools = "|".join(str(ctx.tool(t)) for t in ("sips", "magick", "convert")) + f"|{exe}"
    key = "\0".join(["reports-thumb-3", str(d), value, _stamp(page), _stamp(state_dir(d) / BLOCKS_FILE),
                     _stamp(d / value) if value else "", str(px), tools])

    def shot() -> bytes:
        if time.time() > deadline:
            raise NotYet
        found = screenshot(exe, page)
        return found[1] if found else b""

    def produce() -> bytes:
        try:
            html = page.read_bytes()
        except OSError:
            html = None
        note, found = "", None
        if value:
            found = named_figure(d, value, html)
            if not found:
                note = f"thumbnail {value!r} names no image file or figure in it"
        if not found and html is not None:
            found = first_image(html)
        if not found and html is not None and exe:
            png = ctx.cached(f"{key}\0shot\0{date.today()}", shot)     # a failure (b"") is kept for the day
            if not png:
                raise NotYet
            found = ("image/png", png)
        small = shrink(ctx, *found, px) if found else None
        head = f"{small[0] if small else ''}\n{note}\n".encode()
        return head + (small[1] if small else b"")

    try:
        mime, note, data = ctx.cached(key, produce).split(b"\n", 2)
    except NotYet:
        return "", "", time.time() > deadline
    uri = f"data:{mime.decode()};base64,{base64.b64encode(data).decode()}" if mime else ""
    return uri, note.decode(), False


def initials(project: str) -> str:
    words = [w for w in re.split(r"[-_ .]+", project) if w]
    if len(words) == 1:
        return words[0][:2].upper()
    return "".join(w[0] for w in words[:2]).upper() or "?"


def rows(ctx, watch=True, thumbs=True):
    cfg = ctx.config
    mod = ctx.module("reports")
    roots = [r for r in deep_get(cfg, "hub.roots", []) or [] if isinstance(r, str) and r]
    exclude = [r for r in deep_get(cfg, "hub.exclude", []) or [] if isinstance(r, str) and r]
    quiet = {s.lower() for s in mod.get("quiet_status", [])}
    now = datetime.now()
    found = reports.collect(roots, exclude + [str(ctx.report_dir)], deep_get(cfg, "confluence.site", "") or "",
                            cache=True)
    thumbs = thumbs and mod.get("thumbnails", True)
    deadline = time.time() + SHOT_BUDGET
    with ThreadPoolExecutor(WORKERS) as pool:       # screenshots take seconds each: a few at once
        pics = list(pool.map(lambda r: thumbnail(ctx, r, deadline), found)) if thumbs else [("", "", False)] * len(found)
    for r, (pic, note, pending) in zip(found, pics):
        r["thumb"], r["thumb_pending"] = pic, pending
        if note:
            ctx.warn(f"{r['where']}: {note}; showing another picture")
        r["when"] = _when(r["active"], now)
        r["status_shown"] = "" if r["status"].lower() in quiet else r["status"]
        r["info"] = " · ".join(x for x in (
            r["status_shown"], f"v{r['version']}", r["edited"] and f"edited {r['edited'].replace('T', ' ')}",
            r["served"] and f"served {r['served'].replace('T', ' ')}") if x)
        r["initials"] = initials(r["project"])
        r["tile"] = int(hashlib.md5(r["project"].encode()).hexdigest(), 16) % TILES
        r["search"] = " ".join([r["title"], r["subtitle"], r["project"], r["where"], r["status"], *r["tags"]]).lower()
        for f in reports.watch_files(r["dir"]) if watch else ():
            ctx.watch(f)
    return found


def groups(found, cards=()):
    """Serving first, then by last activity. Each group: id, label, rows, open, cards."""
    now = datetime.now()
    out = [{"id": "serving", "label": "Serving", "rows": [r for r in found if r["serving"]], "open": True}]
    rest = [r for r in found if not r["serving"]]
    newer = 0
    for gid, label, days, is_open in GROUPS:
        mine = [r for r in rest if newer <= _age(r["active"], now) and (days is None or _age(r["active"], now) < days)]
        out.append({"id": gid, "label": label, "rows": mine, "open": is_open})
        newer = days or newer
    out = [g for g in out if g["rows"]]
    for g in out:
        g["cards"] = g["id"] in cards
    if len(out) == 1:
        out[0]["open"] = True
    return out


def keep_hint(ctx):
    """On the hub's own page: how to keep it at hand (an installed web app, a bookmark) and, on
    macOS without the login agent, how to keep it running. The script shows it only on a served
    page in a browser tab, until dismissed; an installed app window never shows it."""
    hub_dir_from = getattr(reports, "hub_dir_from", None)   # a hub running since before an upgrade: no hint
    if not hub_dir_from or Path(ctx.report_dir).resolve() != hub_dir_from(deep_get(ctx.config, "hub.dir", "") or "").resolve():
        return None
    from rapport_core import hub
    return {"install": hub.macos() and not hub.installed()}


def prepare(node, ctx):
    found = rows(ctx)
    return {"rows": found, "groups": groups(found, ctx.module("reports").get("cards", [])),
            "pending": any(r["thumb_pending"] for r in found), "keep": keep_hint(ctx)}


def confluence(node, ctx, cf):
    return {"rows": rows(ctx, watch=False, thumbs=False)}
