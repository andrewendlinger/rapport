"""Dev helper: full-page screenshots of a report file in light/dark."""
import os
import sys
from playwright.sync_api import sync_playwright
path, out = sys.argv[1], sys.argv[2]
schemes = sys.argv[3].split(",") if len(sys.argv) > 3 else ["light", "dark"]
with sync_playwright() as p:
    exe = os.environ.get("RP_CHROME")
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    for scheme in schemes:
        page = b.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
        errs = []
        page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
        page.goto(path if "://" in path else "file://" + os.path.abspath(path))
        page.wait_for_timeout(300)
        page.screenshot(path=f"{out}-{scheme}.png", full_page=True)
        print(scheme, "errors:", errs)
        page.close()
    b.close()
