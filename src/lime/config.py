"""Small, validated user configuration; document directories cannot override it."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


@dataclass(frozen=True)
class Settings:
    width: int = 88
    vertical_padding: int = 3


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lime/config.yaml"


def load_settings(path: Path | None = None) -> Settings:
    explicit = path is not None
    path = path.expanduser() if path else config_path()
    if not explicit and not path.exists():
        return Settings()
    try:
        with path.open(encoding="utf-8") as stream:
            values = YAML(typ="safe", pure=True).load(stream)
    except (OSError, UnicodeError, YAMLError) as error:
        raise ValueError(f"cannot read config {path}: {error}") from error
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError(f"config {path} must contain a YAML mapping")  # noqa: TRY004
    known = {field.name for field in fields(Settings)}
    if any(key not in known for key in values):
        raise ValueError(
            f"unknown config keys: {', '.join(str(key) for key in values if key not in known)}"
        )
    for key, minimum, maximum in (
        ("width", 12, 1000),
        ("vertical_padding", 0, 100),
    ):
        if key in values and (
            type(values[key]) is not int or not minimum <= values[key] <= maximum
        ):
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    return Settings(**values)
