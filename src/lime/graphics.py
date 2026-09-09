"""Raster headings carried by the Kitty graphics protocol supported by Ghostty."""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from PIL import Image, ImageDraw, ImageFont

from lime.terminal import Palette, Terminal

FONT_CANDIDATES = (
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 0),
)


def load_font(size: int, custom: Path | None = None) -> ImageFont.FreeTypeFont:
    if custom is not None:
        return ImageFont.truetype(str(custom), size)
    for path, index in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default(size=size)


def wrap_heading(text: str, font: ImageFont.FreeTypeFont, width: int) -> Iterator[str]:
    """Wrap using actual glyph bounds; retain even unbroken long titles."""

    def fits(value: str) -> bool:
        left, _, right, _ = font.getbbox(value)
        return max(font.getlength(value), right - left) <= width

    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if fits(candidate):
            line = candidate
            continue
        if line:
            yield line
        line = ""
        for char in word:
            if line and not fits(line + char):
                yield line
                line = ""
            line += char
    if line:
        yield line


def encode_png(png: bytes, columns: int, rows: int) -> Iterator[str]:
    encoded = base64.b64encode(png).decode("ascii")
    for offset in range(0, len(encoded), 4096):
        chunk = encoded[offset : offset + 4096]
        more = int(offset + 4096 < len(encoded))
        # q=2 suppresses replies, which otherwise leak into the shell's input.
        control = f"a=T,f=100,t=d,c={columns},r={rows},C=1,q=2," if offset == 0 else ""
        yield f"\033_G{control}m={more};{chunk}\033\\"


class Headings:
    def __init__(
        self,
        terminal: Terminal,
        width: int,
        palette: Palette,
        font: Path | None = None,
    ) -> None:
        self.terminal, self.width, self.palette, self.font = terminal, width, palette, font

    def images(self, text: str, level: int) -> Iterator[tuple[bytes, int, int]]:
        rows = 3 if level == 1 else 2
        scale = 2  # Supersampling keeps the font crisp on Retina displays.
        cw, ch = self.terminal.cell_width, self.terminal.cell_height
        height = rows * ch * scale
        width = self.width * cw * scale
        if width * height > 16_000_000:
            raise ValueError("heading image exceeds pixel budget")
        ratio = {1: 0.72, 2: 0.78, 3: 0.62}[level]
        font = load_font(max(1, int(height * ratio)), self.font)
        for line in wrap_heading(text, font, max(1, width - cw * scale)):
            left, top, right, bottom = font.getbbox(line)
            columns = min(self.width, max(1, math.ceil((right - left) / (cw * scale)) + 1))
            canvas = Image.new("RGBA", (columns * cw * scale, height), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).text(
                (-left, (height - (bottom - top)) // 2 - top),
                line,
                font=font,
                fill=(*self.palette[level - 1], 255),
            )
            buffer = io.BytesIO()
            canvas.save(buffer, format="PNG")
            yield buffer.getvalue(), columns, rows

    def write(self, stream: TextIO, text: str, level: int, margin: int) -> None:
        for png, columns, rows in self.images(text, level):
            # Reserve space *before* placement, including when at the bottom of
            # the screen. Cursor motion stays inside the newly reserved rows.
            stream.write("\n" * rows + f"\033[{rows}A\r" + " " * margin)
            stream.writelines(encode_png(png, columns, rows))
            stream.write("\r" + "\n" * rows)
            stream.flush()
