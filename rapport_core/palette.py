"""Named colour palettes: Atlassian Confluence's fixed text, highlight and background
colours, so a report can use exactly the colours a Confluence page can, and an
export can write the stored values Confluence expects (`light`).

Each palette entry becomes a theme token: --rp-<prefix>-<name>, e.g.
--rp-text-bold-red, --rp-hl-yellow, --rp-bg-light-blue. A module that uses a
palette declares it in module.toml (`palettes = ["text"]`); the renderer then
adds that palette's tokens (light and dark) to the page, and a theme or
[theme.tokens] can override any of them like other tokens. Dark values follow
Atlassian's dark accent colours (approximate: Confluence maps the stored light
values to its own dark tokens).
"""

from __future__ import annotations

PREFIX = {"text": "text", "highlight": "hl", "background": "bg"}

# name: (light = the value Confluence stores, dark)
TEXT = {
    "default": ("#172b4d", "var(--rp-ink)"),
    "bold-blue": ("#0747a6", "#85b8ff"),
    "bold-teal": ("#008da6", "#9dd9ee"),
    "bold-green": ("#006644", "#7ee2b8"),
    "bold-orange": ("#ff991f", "#fec195"),
    "bold-red": ("#bf2600", "#fd9891"),
    "bold-purple": ("#403294", "#b8acf6"),
    "gray": ("#97a0af", "#8c9bab"),
    "blue": ("#4c9aff", "#579dff"),
    "teal": ("#00b8d9", "#6cc3e0"),
    "green": ("#36b37e", "#4bce97"),
    "yellow": ("#ffc400", "#f5cd47"),
    "red": ("#ff5630", "#f87168"),
    "purple": ("#6554c0", "#9f8fef"),
    "white": ("#ffffff", "#ffffff"),
    "subtle-blue": ("#b3d4ff", "#b3d4ff"),
    "subtle-teal": ("#b3f5ff", "#b3f5ff"),
    "subtle-green": ("#abf5d1", "#abf5d1"),
    "subtle-yellow": ("#fff0b3", "#fff0b3"),
    "subtle-red": ("#ffbdad", "#ffbdad"),
    "subtle-purple": ("#eae6ff", "#eae6ff"),
}

HIGHLIGHT = {
    "gray": ("#dcdfe4", "#2c333a"),
    "teal": ("#c6edfb", "#164555"),
    "lime": ("#d3f1a7", "#37471f"),
    "yellow": ("#f8e6a0", "#533f04"),
    "orange": ("#fedec8", "#5f3811"),
    "magenta": ("#fdd0ec", "#50253f"),
    "purple": ("#dfd8fd", "#352c63"),
}

BACKGROUND = {
    "white": ("#ffffff", "var(--rp-bg)"),
    "light-gray": ("#f4f5f7", "#22272b"),
    "gray": ("#b3bac5", "#454f59"),
    "light-blue": ("#deebff", "#092957"),
    "blue": ("#b3d4ff", "#09326c"),
    "dark-blue": ("#4c9aff", "#0055cc"),
    "light-teal": ("#e6fcff", "#1e3137"),
    "teal": ("#b3f5ff", "#206a83"),
    "dark-teal": ("#79e2f2", "#227d9b"),
    "light-green": ("#e3fcef", "#1c3329"),
    "green": ("#abf5d1", "#216e4e"),
    "dark-green": ("#57d9a3", "#1f845a"),
    "light-yellow": ("#fffae6", "#332e1b"),
    "yellow": ("#fff0b3", "#533f04"),
    "dark-yellow": ("#ffc400", "#7f5f01"),
    "light-red": ("#ffebe6", "#42221f"),
    "red": ("#ffbdad", "#5d1f1a"),
    "dark-red": ("#ff8f73", "#ae2e24"),
    "light-purple": ("#eae6ff", "#2b273f"),
    "purple": ("#c0b6f2", "#352c63"),
    "dark-purple": ("#998dd9", "#5e4db2"),
}

PALETTES = {"text": TEXT, "highlight": HIGHLIGHT, "background": BACKGROUND}


def tokens(names, dark: bool = False) -> dict[str, str]:
    """Theme tokens (without the --rp- prefix) for the named palettes."""
    out = {}
    for name in names:
        for key, (light, dk) in PALETTES.get(name, {}).items():
            out[f"{PREFIX[name]}-{key}"] = dk if dark else light
    return out


def all_token_names() -> set[str]:
    return set(tokens(PALETTES))


def stored(palette: str, name: str) -> str:
    """The light value Confluence stores for a palette entry ("" if unknown)."""
    return PALETTES.get(palette, {}).get(name, ("",))[0]
