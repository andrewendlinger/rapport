"""Only ::: tab children make sense inside tabs."""


def prepare(node, ctx):
    if not node.children or any(c.module != "tab" for c in node.children):
        raise ValueError("tabs holds only ::: tab blocks (give tabs more colons: :::: tabs)")
    return {}
