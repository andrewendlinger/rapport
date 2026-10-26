"""Apply Confluence table features to the pipe table in the body.

The body's one table is markdown-it's HTML; rows and cells are re-emitted with
spans, header cells, colours and alignment. Cells covered by a span are dropped.
"""
import re

from markupsafe import Markup

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<(th|td)((?:\s[^>]*)?)>(.*?)</\1>", re.S)
TABLE_RE = re.compile(r"<table>.*?</table>", re.S)
ADDR_RE = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")
CELL_KEYS = {"bg", "rowspan", "colspan", "valign", "header"}
VALIGN = {"top", "middle", "bottom"}
# background palette names (rapport_core.palette.BACKGROUND)
BG = {"white", "light-gray", "gray", "light-blue", "blue", "dark-blue", "light-teal", "teal", "dark-teal",
      "light-green", "green", "dark-green", "light-yellow", "yellow", "dark-yellow", "light-red", "red",
      "dark-red", "light-purple", "purple", "dark-purple"}


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_cells(cells: dict, nrows: int, ncols: int) -> dict:
    out = {}
    for addr, spec in cells.items():
        m = ADDR_RE.match(str(addr))
        if not m:
            raise ValueError(f"grid cell {addr!r}: use an A1-style address like B2")
        r, c = int(m[2]) - 1, col_index(m[1])
        if r >= nrows or c >= ncols:
            raise ValueError(f"grid cell {addr}: the table has {nrows} rows and {ncols} columns")
        if not isinstance(spec, dict):
            raise ValueError(f"grid cell {addr}: settings must be a table, e.g. {{bg = \"light-blue\"}}")
        if bad := set(spec) - CELL_KEYS:
            raise ValueError(f"grid cell {addr}: unknown settings {sorted(bad)} (known: {sorted(CELL_KEYS)})")
        if "bg" in spec and spec["bg"] not in BG:
            raise ValueError(f"grid cell {addr}: bg {spec['bg']!r} isn't a background palette name")
        if "valign" in spec and spec["valign"] not in VALIGN:
            raise ValueError(f"grid cell {addr}: valign must be top, middle or bottom")
        for k in ("rowspan", "colspan"):
            if k in spec and (not isinstance(spec[k], int) or spec[k] < 1):
                raise ValueError(f"grid cell {addr}: {k} must be a whole number ≥ 1")
        out[(r, c)] = spec
    return out


def prepare(node, ctx):
    tables = [c for c in node.children if c.module == "table"]
    if len(tables) != 1 or len(node.children) != 1:
        raise ValueError("grid holds exactly one Markdown pipe table and nothing else")
    child = str(tables[0].html)
    tm = TABLE_RE.search(child)
    rows = [[(t, a, inner) for t, a, inner in CELL_RE.findall(r)] for r in ROW_RE.findall(tm[0])]
    nrows, ncols = len(rows), max(len(r) for r in rows)
    p = node.params
    if p["widths"] and len(p["widths"]) != ncols:
        raise ValueError(f"grid widths: {len(p['widths'])} given for {ncols} columns")
    specs = parse_cells(p["cells"], nrows, ncols)
    head = 1 if p["header_row"] else 0

    covered = set()
    for (r, c), s in specs.items():
        rs, cs = s.get("rowspan", 1), s.get("colspan", 1)
        if r + rs > nrows or c + cs > ncols:
            raise ValueError(f"grid cell {chr(65 + c)}{r + 1}: span runs past the table")
        if head and r < head <= r + rs - 1:
            raise ValueError(f"grid cell {chr(65 + c)}{r + 1}: a rowspan can't cross from the header row into the body")
        covered |= {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)} - {(r, c)}

    def cell(r, c, attrs, inner):
        s = specs.get((r, c), {})
        is_head = (r < head) or (p["header_column"] and c == 0) or s.get("header", False)
        tag = "th" if is_head else "td"
        style = re.search(r'style="([^"]*)"', attrs)          # markdown-it's column alignment
        css = [style[1].rstrip(";")] if style else []
        if "bg" in s:
            css.append(f"background:var(--rp-bg-{s['bg']})")
        if "valign" in s:
            css.append(f"vertical-align:{s['valign']}")
        extra = "".join(f' {k}="{s[k]}"' for k in ("rowspan", "colspan") if s.get(k, 1) > 1)
        if css:
            extra += f' style="{";".join(css)}"'
        if "bg" in s:
            extra += f' data-rp-bg="{s["bg"]}"'
        return f"<{tag}{extra}>{inner}</{tag}>"

    def row_html(r, cells, number):
        parts = []
        if p["numbered"]:
            parts.append('<th class="rp-grid-num" data-rp-ui></th>' if r < head
                         else f'<td class="rp-grid-num" data-rp-ui>{number}</td>')
        parts += [cell(r, c, a, inner) for c, (t, a, inner) in enumerate(cells) if (r, c) not in covered]
        return "<tr>" + "".join(parts) + "</tr>"

    out = ["<table>"]
    if p["widths"]:
        cols = (['<col class="rp-grid-num-col">'] if p["numbered"] else []) + \
               [f'<col style="width:{w}px">' for w in p["widths"]]
        out.append("<colgroup>" + "".join(cols) + "</colgroup>")
    if head:
        out.append("<thead>" + row_html(0, rows[0], 0) + "</thead>")
    out.append("<tbody>" + "".join(row_html(r, rows[r], r - head + 1) for r in range(head, nrows)) + "</tbody>")
    out.append("</table>")
    return {"table_html": Markup(child[:tm.start()] + "".join(out) + child[tm.end():])}


def confluence(node, ctx, cf):
    """The same table as Confluence markup: spans, header cells, cell colours (stored
    palette values), vertical alignment, column widths, numbered rows, width layout."""
    from rapport_core import palette
    tables = [c for c in node.children if c.module == "table"]
    if len(tables) != 1 or len(node.children) != 1:
        raise ValueError("grid holds exactly one Markdown pipe table and nothing else")
    tm = TABLE_RE.search(str(tables[0].html))
    rows = [[(t, a, inner) for t, a, inner in CELL_RE.findall(r)] for r in ROW_RE.findall(tm[0])]
    nrows, ncols = len(rows), max(len(r) for r in rows)
    p = node.params
    if p["widths"] and len(p["widths"]) != ncols:
        raise ValueError(f"grid widths: {len(p['widths'])} given for {ncols} columns")
    specs = parse_cells(p["cells"], nrows, ncols)
    head = 1 if p["header_row"] else 0
    covered = set()
    for (r, c), s in specs.items():
        rs, cs = s.get("rowspan", 1), s.get("colspan", 1)
        covered |= {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)} - {(r, c)}
    widths = p["widths"]

    def cell(r, c, inner):
        s = specs.get((r, c), {})
        tag = "th" if (r < head or (p["header_column"] and c == 0) or s.get("header")) else "td"
        attrs = []
        for k in ("rowspan", "colspan"):
            if s.get(k, 1) > 1:
                attrs.append(f'{k}="{s[k]}"')
        if widths:
            span = widths[c:c + s.get("colspan", 1)]
            attrs.append(f'data-colwidth="{",".join(str(w) for w in span)}"')
        if "bg" in s:
            attrs.append(f'data-background="{palette.stored("background", s["bg"]).upper()}"')
        if "valign" in s:
            attrs.append(f'data-valign="{s["valign"]}"')
        return f"<{tag}{''.join(' ' + a for a in attrs)}>{inner}</{tag}>"

    out = []
    for r, cells in enumerate(rows):
        out.append("<tr>" + "".join(cell(r, c, inner) for c, (t, a, inner) in enumerate(cells)
                                    if (r, c) not in covered) + "</tr>")
    attrs = []
    if p["numbered"]:
        attrs.append('data-number-column="true"')
    if widths:
        attrs.append('data-display-mode="fixed"')
    if p["layout"] != "default":
        attrs.append(f'data-layout="{p["layout"]}"')
    body = (f"<thead>{out[0]}</thead><tbody>{''.join(out[1:])}</tbody>" if head else f"<tbody>{''.join(out)}</tbody>")
    return {"cf_table": Markup(f"<table{''.join(' ' + a for a in attrs)}>{body}</table>")}
