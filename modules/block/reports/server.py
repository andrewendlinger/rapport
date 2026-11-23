"""reports: act on a listed report from the page. Only folders the page lists (under
[hub] roots) are accepted, so the token-guarded page can't be turned on anything else.
Serving and stopping write that report's server.json, which the page watches: it
rebuilds and reloads with the new state. Nothing is kept in this module's store."""
import os
import subprocess
import sys
from pathlib import Path
from rapport_core import ROOT, reports, serve


def routes(r):
    r.post("/serve", serve_report)      # body: {"dir": "/abs/report"} → {"url": …}
    r.post("/stop", stop_report)        # body: {"dir": …}
    r.post("/reveal", reveal)           # body: {"dir": …}: open the folder in the file manager
    r.post("/refresh", refresh)         # look for new reports: rebuild the page


def _report(req) -> Path:
    d = str(req.body.get("dir") or "")
    known = {str(p) for p in reports.listed(req.full_config, req.report_dir)}
    if d not in known:
        raise req.Refused(f"not a report this page lists: {d or '(none given)'}")
    return Path(d)


def serve_report(req):
    """Through the CLI rather than serve.detach(): the server must not be this process's child,
    or once stopped it would linger as a zombie that looks alive."""
    d = _report(req)
    cmd = [sys.executable, str(ROOT / "scripts" / "rapport.py"), "serve", str(d), "--detach", "--no-open"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=d, timeout=60)
    except subprocess.TimeoutExpired:
        raise req.Refused(f"the server for {d} took over a minute to start; see {d / '.rapport' / 'server.log'}") \
            from None
    if out.returncode != 0:
        raise req.Refused((out.stderr or out.stdout).strip().removeprefix("rapport: ") or "serve failed")
    info = serve.running(d)
    if not info:
        raise req.Refused(f"the server for {d} did not come up; see {d / '.rapport' / 'server.log'}")
    return {"url": info["url"]}


def stop_report(req):
    d = _report(req)
    if serve.stop(d) != 0:
        raise req.Refused(f"the server for {d} is still running; try again, or stop it with "
                          f"`rapport serve --stop {d}`")
    return {"stopped": True}


def reveal(req):
    d = _report(req)
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(d)]        # show it in Finder: plain `open` would launch a folder named X.app
    elif sys.platform.startswith("win"):
        os.startfile(str(d))        # noqa: S606 - a folder the page lists
        return {"ok": True}
    else:
        cmd = ["xdg-open", str(d)]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        raise req.Refused(f"could not open {d}: {e}") from None
    return {"ok": True}


def refresh(req):
    """Touch the page's own source: the server sees a change, rebuilds (scanning again) and reloads."""
    os.utime(req.full_config.source_path)
    return {"ok": True}
