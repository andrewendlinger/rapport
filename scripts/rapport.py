#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "markdown-it-py>=3.0",
#   "mdit-py-plugins>=0.4",
#   "jinja2>=3.1",
#   "pygments>=2.17",
# ]
# ///
"""rapport — modular HTML reports with in-page reader feedback.

Entry point. Run through `bin/rapport`, which picks the repo .venv, `uv run
--script` (deps from the header above), or plain python3, in that order.
"""

import sys
from pathlib import Path

# `uv run --script` puts scripts/ first on sys.path; the package lives one up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from rapport_core.cli import main
except ModuleNotFoundError as e:  # a dependency, not our own package
    if e.name and not e.name.startswith("rapport_core"):
        sys.exit(
            f"rapport: missing dependency '{e.name}'.\n"
            "  Install uv (https://docs.astral.sh/uv/) so bin/rapport can run with\n"
            "  inline dependencies, or run `install.sh --venv`, or\n"
            f"  `python3 -m pip install -r {ROOT / 'requirements.txt'}`."
        )
    raise

if __name__ == "__main__":
    sys.exit(main())
