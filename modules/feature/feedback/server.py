"""feedback: the reader's comments, under /api/m/feedback/ while `rapport serve` runs;
script.js calls them with Rapport.api("feedback", path, opts). The reference
implementation of a module's server.py ("server.py" in references/modules.md).

The store, the checks on what the page sends, and the agent's `rapport feedback`
are core (rapport_core/feedback.py): the CLI writes the same file under the same
lock. A copy of this module in a rapport-modules/ folder (an override) imports
them too. A refused payload answers 400, an unknown id 404, a broken
feedback.json 500.
"""

from rapport_core.changes import changes
from rapport_core.feedback import Store, check_content, make_item, update_fields


def routes(r):
    r.get("/", list_items)
    r.post("/", add_item)               # body: {"target": {…}, "actions": [ids], "comment": "…"}
    r.patch("/{id}", update_item)       # body: {"actions": [ids], "comment": "…"}, either or both
    r.delete("/{id}", delete_item)


def list_items(req):
    """The items, plus what changed since the last feedback round (the page's change bars)."""
    items = Store(req.report_dir).items()       # Store is on req.store's file: feedback.json
    return {"items": items, "changes": changes(req.report_dir, req.blocks, items) if req.blocks else None}


def add_item(req):
    """The new item; an identical open quick action answers the existing one, with "duplicate": true."""
    item = make_item(req.body, req.full_config, req.blocks, req.version)
    return Store(req.report_dir).add(item, req.blocks)


def update_item(req):
    cfg = req.full_config
    fields = update_fields(req.body, cfg)
    return Store(req.report_dir).update(req.params["id"], fields, validate=lambda it: check_content(
        it.get("actions") or [], it.get("comment") or "", cfg))


def delete_item(req):
    Store(req.report_dir).delete(req.params["id"])
    return {"ok": True}


def describe(state, config):
    """What `rapport modules state feedback` prints. The agent works through `rapport feedback`."""
    items = state.get("items", [])
    n = sum(1 for i in items if i.get("status") == "open")
    return f"{n} open of {len(items)} feedback item(s); `rapport feedback` lists the open ones and what to do"
