"""The runtime dependencies are declared three times: the script header that bin/rapport runs
(PEP 723), requirements.txt (pip), and pyproject.toml (the dev tooling's `uv run`). They must agree."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def script_header() -> dict:
    """The `# /// script` block of scripts/rapport.py as a table (the PEP 723 reading rule)."""
    text = (ROOT / "scripts" / "rapport.py").read_text(encoding="utf-8")
    m = re.search(r"(?m)^# /// script$\s(?P<body>(?:^#(?: .*)?$\s)+)^# ///$", text)
    assert m, "scripts/rapport.py has no PEP 723 header"
    return tomllib.loads("\n".join(line[2:] for line in m.group("body").splitlines()))


def pinned(requirements) -> set[str]:
    """Requirement strings in one spelling: no spaces, lower case, - for _ in the name."""
    def one(req: str) -> str:
        name, rest = re.match(r"([A-Za-z0-9._-]+)(.*)", req.strip()).groups()
        return re.sub(r"[-_.]+", "-", name).lower() + rest.replace(" ", "")
    return {one(r) for r in requirements}


def requirements_txt() -> list[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [ln for ln in (ln.split("#")[0].strip() for ln in lines) if ln]


def test_the_three_dependency_lists_agree():
    header = pinned(script_header()["dependencies"])
    assert len(header) >= 4, header
    project = pinned(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"])
    assert pinned(requirements_txt()) == header, "requirements.txt differs from the header in scripts/rapport.py"
    assert project == header, "pyproject.toml differs from the header in scripts/rapport.py"


def test_the_python_requirement_agrees():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]
    assert script_header()["requires-python"] == project
