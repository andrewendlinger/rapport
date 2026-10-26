"""The layout fixes the column count: check the ::: column children match it."""

COUNT = {"two-equal": 2, "two-left-sidebar": 2, "two-right-sidebar": 2, "three-equal": 3, "three-with-sidebars": 3}


def prepare(node, ctx):
    cols = [c for c in node.children if c.module == "column"]
    if len(cols) != len(node.children):
        raise ValueError("columns holds only ::: column blocks (give columns more colons: :::: columns)")
    want = COUNT[node.params["layout"]]
    if len(cols) != want:
        raise ValueError(f"layout {node.params['layout']!r} needs {want} columns, got {len(cols)}")
    return {}
