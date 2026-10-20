"""feedback: validate the quick actions' shortcut keys."""

import re

COMMENT_KEY = "c"   # bar mode: C expands the bar into the comment window


def check(config: dict) -> list[str]:
    out, owner = [], {}
    for a in config.get("actions") or []:
        if not isinstance(a, dict) or a.get("enabled") is False or not a.get("key"):
            continue
        key, aid = str(a["key"]).lower(), a.get("id", "?")
        if not re.fullmatch(r"[a-z0-9]", key):
            out.append(f"action '{aid}': key must be one letter or digit, got {a['key']!r}")
        elif key == COMMENT_KEY:
            out.append(f"action '{aid}': key '{key}' is taken by Comment in the bar; pick another")
        elif key in owner:
            out.append(f"actions '{owner[key]}' and '{aid}' share the key '{key}'")
        else:
            owner[key] = aid
    return out
