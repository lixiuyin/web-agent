"""Image helpers shared by agent output and planner prompts."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageStat


def is_blank_image(image: Image.Image) -> bool:
    """Return True for near-solid white images such as an untouched blank page."""
    rgb = image.convert("RGB")
    stat = ImageStat.Stat(rgb)
    return max(stat.stddev) < 1.0 and min(stat.mean) > 250.0


def image_to_jpeg_b64(image: Image.Image, quality: int = 70) -> str:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")
