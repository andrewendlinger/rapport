import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own $HOME and XDG config dir: no real user config leaks in."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


@pytest.fixture
def report(isolated_home):
    """Factory: report(text, **files) → report dir under $HOME/proj/reports/r."""
    def make(text: str, files: dict | None = None, name: str = "r") -> Path:
        d = isolated_home / "proj" / "reports" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text(text, encoding="utf-8")
        for rel, content in (files or {}).items():
            p = isolated_home / "proj" / rel if not rel.startswith("/") else Path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")
        return d
    return make


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# a 1×1 animated-gif header is enough to prove gifs are inlined with the right type
GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x00\x00\x00\x00\x00"
       b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
