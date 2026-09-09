"""Optional Mermaid CLI integration, rendered locally and displayed by Ghostty."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TextIO

from PIL import Image

from lime.images import write_image
from lime.terminal import Terminal, TerminalTheme


def hex_color(rgb):
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


class Mermaid:
    def __init__(self, terminal: Terminal, width: int, theme: TerminalTheme):
        self.terminal, self.width, self.theme = terminal, width, theme

    def png(self, source: str) -> bytes:
        command = shutil.which("mmdc")
        if command is None:
            raise ValueError("install @mermaid-js/mermaid-cli for diagrams; showing source")
        fg, bg = hex_color(self.theme.foreground), hex_color(self.theme.background)
        accent = hex_color(self.theme.palette[1])
        config = {
            "securityLevel": "strict",
            "theme": "base",
            "htmlLabels": False,
            "themeVariables": {
                "darkMode": sum(self.theme.background) < sum(self.theme.foreground),
                "background": bg,
                "primaryColor": bg,
                "secondaryColor": bg,
                "tertiaryColor": bg,
                "mainBkg": bg,
                "nodeBorder": accent,
                "primaryTextColor": fg,
                "secondaryTextColor": fg,
                "tertiaryTextColor": fg,
                "primaryBorderColor": accent,
                "secondaryBorderColor": accent,
                "tertiaryBorderColor": accent,
                "lineColor": fg,
                "textColor": fg,
                "edgeLabelBackground": bg,
                "actorBkg": bg,
                "actorTextColor": fg,
                "actorBorder": accent,
                "signalColor": fg,
                "signalTextColor": fg,
                "noteBkgColor": bg,
                "noteTextColor": fg,
                "noteBorderColor": accent,
            },
        }
        with tempfile.TemporaryDirectory(prefix="lime-mermaid-") as temporary:
            directory = Path(temporary)
            diagram, image, configuration = (
                directory / name for name in ("diagram.mmd", "diagram.png", "config.json")
            )
            diagram.write_text(source, encoding="utf-8")
            configuration.write_text(json.dumps(config), encoding="utf-8")
            try:
                subprocess.run(
                    [
                        command,
                        "-i",
                        str(diagram),
                        "-o",
                        str(image),
                        "-c",
                        str(configuration),
                        "-b",
                        "transparent",
                        "-s",
                        "2",
                        "-q",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("renderer timed out; showing source") from None
            except subprocess.CalledProcessError:
                raise ValueError(
                    "could not render diagram; showing source (check syntax and mmdc)"
                ) from None
            return image.read_bytes()

    def write(self, stream: TextIO, source: str, margin: int) -> None:
        with Image.open(io.BytesIO(self.png(source))) as source_image:
            if source_image.width * source_image.height > 20_000_000:
                raise ValueError("diagram exceeds 20 megapixels; showing source")
            image = source_image.convert("RGBA")
        write_image(stream, image, self.terminal, self.width, margin)
