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
    padding: int = 12
    vertical_padding: int = 6
    line_numbers: bool = True
    headings: str = "auto"
    heading_labels: bool = True
    mermaid: str = "auto"
    images: bool = True
    interactive: str = "auto"
    zen: bool = False
    font: str | None = None


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
        ("padding", 0, 100),
        ("vertical_padding", 0, 100),
    ):
        if key in values and (
            type(values[key]) is not int or not minimum <= values[key] <= maximum
        ):
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    for key in ("line_numbers", "heading_labels", "images", "zen"):
        if key in values and type(values[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    for key, options in (
        ("headings", ("auto", "image", "text")),
        ("mermaid", ("auto", "off")),
        ("interactive", ("auto", "on", "off")),
    ):
        if key in values and values[key] not in options:
            raise ValueError(f"{key} must be one of {', '.join(options)}")
    if values.get("font") is not None:
        if not isinstance(values["font"], str):
            raise ValueError("font must be a path or null")
        font = Path(values["font"]).expanduser()
        values["font"] = str(font if font.is_absolute() else path.parent / font)
    return Settings(**values)
