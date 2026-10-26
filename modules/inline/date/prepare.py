"""Validate the ISO date and format its label."""
from datetime import date


def prepare(node, ctx):
    raw = node.text.strip()
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"date needs an ISO date like 2026-10-23, got {raw!r}") from None
    return {"iso": d.isoformat(), "label": f"{d:%b} {d.day}, {d.year}"}


def confluence(node, ctx, cf):
    return prepare(node, ctx)
