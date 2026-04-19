"""Global user config at ~/.config/cws/config.toml (XDG_CONFIG_HOME respected)."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

# Known schema: section.key -> expected type (all stored as strings for simplicity)
KNOWN_KEYS: set[str] = {
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.domain",
    "feishu.allowed_users",
    "agent.default",
    "codex.model",
    "codex.approval_policy",
    "codex.command",
    "codex.sandbox",
    "codex.service_tier",
}

_INIT_TEMPLATE = """\
[feishu]
app_id = ""
app_secret = ""
# domain = "https://open.feishu.cn"
# allowed_users = ["open_id_1", "open_id_2"]

[agent]
default = "claude-code"

# [codex]
# model = "gpt-5.4"
# approval_policy = "on-request"
"""


def get_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "cws" / "config.toml"


def load() -> dict:
    path = get_path()
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"warning: could not parse {path}: {e}", file=sys.stderr)
        return {}


def _format_value(v: Any) -> str:
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # string
    return f'"{v}"'


def format_for_display(data: dict) -> str:
    lines: list[str] = []
    for section, values in data.items():
        if isinstance(values, dict):
            lines.append(f"[{section}]")
            for k, v in values.items():
                lines.append(f"{k} = {_format_value(v)}")
            lines.append("")
        else:
            lines.append(f"{section} = {_format_value(values)}")
    return "\n".join(lines).rstrip() + "\n"


def save(data: dict) -> None:
    path = get_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_for_display(data), encoding="utf-8")


def set_value(key: str, value: str) -> None:
    if key not in KNOWN_KEYS:
        known = ", ".join(sorted(KNOWN_KEYS))
        raise ValueError(f"unknown key {key!r}. Known keys: {known}")
    if "." not in key:
        raise ValueError(f"key must be section.name, got {key!r}")
    section, name = key.split(".", 1)
    data = load()
    if section not in data:
        data[section] = {}
    data[section][name] = value
    save(data)


def unset_value(key: str) -> None:
    if "." not in key:
        raise ValueError(f"key must be section.name, got {key!r}")
    section, name = key.split(".", 1)
    data = load()
    if section in data and name in data[section]:
        del data[section][name]
        if not data[section]:
            del data[section]
        save(data)
    # silently succeed if not present


def get_value(key: str) -> str | None:
    if "." not in key:
        return None
    section, name = key.split(".", 1)
    data = load()
    val = data.get(section, {}).get(name)
    if val is None:
        return None
    return str(val)


def write_init_template() -> bool:
    """Write the init template if the config file doesn't exist yet. Returns True if written."""
    path = get_path()
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    return True
