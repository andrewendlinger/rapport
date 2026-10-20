"""rapport core: config, module registry, parser, renderer, server, feedback."""

from pathlib import Path

__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parent.parent
BUILTIN_MODULES = ROOT / "modules"
BUILTIN_PRESETS = ROOT / "presets"
SCHEMA_FILE = ROOT / "config" / "schema.toml"
