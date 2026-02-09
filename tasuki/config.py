"""Configuration file and prompt discovery.

Search order:
1. Explicitly provided path
2. config/ in the current directory
3. ~/.config/tasuki/
4. Package-bundled defaults (tasuki/_defaults/)
"""

import shutil
from pathlib import Path

import yaml

# Package-bundled default configuration directory
_DEFAULTS_DIR = Path(__file__).resolve().parent / "_defaults"

# User global configuration
_USER_CONFIG_DIR = Path.home() / ".config" / "tasuki"


def _search_paths() -> list[Path]:
    """List of search paths for configuration files (in priority order)."""
    return [
        Path.cwd() / "config",
        _USER_CONFIG_DIR,
        _DEFAULTS_DIR,
    ]


def find_config(config_path: Path | None = None) -> Path | None:
    """Find tasuki.yaml."""
    if config_path:
        return config_path if config_path.exists() else None
    for base in _search_paths():
        p = base / "tasuki.yaml"
        if p.exists():
            return p
    return None


def find_prompt(name: str) -> Path | None:
    """Find prompts/<name>."""
    for base in _search_paths():
        p = base / "prompts" / name
        if p.exists():
            return p
    return None


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration. Returns an empty dict if not found."""
    path = find_config(config_path)
    if not path:
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_prompt(name: str) -> str:
    """Load prompt text. Returns an empty string if not found."""
    path = find_prompt(name)
    if not path:
        return ""
    return path.read_text(encoding="utf-8")


def init_project(target: Path | None = None) -> Path:
    """Copy config/ to the current directory (or target) to initialize."""
    dest = (target or Path.cwd()) / "config"
    if dest.exists():
        raise FileExistsError(f"{dest} already exists. Aborted to avoid overwriting.")
    shutil.copytree(_DEFAULTS_DIR, dest)
    return dest
