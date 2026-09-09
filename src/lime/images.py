"""Local images and shared inline graphics placement."""

from __future__ import annotations

import io
import math
import warnings
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote, urlsplit

from PIL import Image

from lime.graphics import encode_png
from lime.terminal import Terminal


def write_image(
    stream: TextIO, image: Image.Image, terminal: Terminal, width: int, margin: int
) -> None:
    pixel_width = min(image.width, width * terminal.cell_width * 2)
    image = image.resize((pixel_width, max(1, round(image.height * pixel_width / image.width))))
    columns = max(1, math.ceil(image.width / (terminal.cell_width * 2)))
    rows = max(1, math.ceil(image.height / (terminal.cell_height * 2)))
    slice_rows = max(1, terminal.rows - 2)
    pixels_per_row = terminal.cell_height * 2
    canvas = Image.new("RGBA", (columns * terminal.cell_width * 2, rows * pixels_per_row))
    canvas.paste(image)
    for start in range(0, rows, slice_rows):
        count = min(slice_rows, rows - start)
        tile = canvas.crop(
            (0, start * pixels_per_row, canvas.width, (start + count) * pixels_per_row)
        )
        buffer = io.BytesIO()
        tile.save(buffer, format="PNG")
        stream.write("\n" * count + f"\033[{count}A\r" + " " * margin)
        stream.writelines(encode_png(buffer.getvalue(), columns, count))
        stream.write("\r" + "\n" * count)
    stream.flush()


def load_image(path: Path) -> Image.Image:
    if path.stat().st_size > 20_000_000:
        raise ValueError("image exceeds 20 MB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path, formats=("PNG", "JPEG", "WEBP")) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("image exceeds 20 megapixels")
            return image.convert("RGBA")


class Images:
    def __init__(self, terminal: Terminal, width: int):
        self.terminal, self.width = terminal, width

    def write(self, stream: TextIO, uri: str, margin: int) -> bool:
        parsed = urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return False
        try:
            image = load_image(Path(unquote(parsed.path)))
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return False
        write_image(stream, image, self.terminal, self.width, margin)
        return True
