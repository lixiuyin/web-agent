"""Tests for low-level PDF utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from webagent.utils.pdf import (
    extract_figure_captions,
    extract_images,
    extract_text,
)


def _pdf_with_text(tmp_path: Path, name: str, text: str) -> Path:
    import fitz

    pdf = tmp_path / name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(pdf))
    doc.close()
    return pdf


def _pdf_with_image(tmp_path: Path, name: str, caption: str) -> Path:
    import fitz
    from PIL import Image

    img_path = tmp_path / "big.png"
    Image.new("RGB", (400, 400), color=(10, 120, 200)).save(img_path)

    pdf = tmp_path / name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(72, 200, 472, 600), filename=str(img_path))
    page.insert_text((72, 180), caption)
    doc.save(str(pdf))
    doc.close()
    return pdf


def test_extract_text_reads_content(tmp_path):
    pdf = _pdf_with_text(tmp_path, "text.pdf", "Hello structured world")
    assert "Hello structured world" in extract_text(str(pdf))


def test_extract_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_text(str(tmp_path / "nope.pdf"))


def test_extract_figure_captions_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_figure_captions(str(tmp_path / "nope.pdf"))


def test_extract_images_saves_and_flags_figures(tmp_path):
    pdf = _pdf_with_image(
        tmp_path, "figpaper.pdf", "Figure 1: A large diagram of the pipeline architecture."
    )
    out_dir = tmp_path / "out"
    results = extract_images(str(pdf), out_dir)

    assert results, "expected at least one extracted image"
    first = results[0]
    assert Path(first["path"]).exists()
    assert first["page"] == 1
    assert first["likely_figure"] is True
    assert first["figure_caption"].startswith("A large diagram")
    assert first["figure_number"] == 1


def test_extract_images_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_images(str(tmp_path / "nope.pdf"), tmp_path / "out")


def test_extract_figure_captions_joins_wrapped_lines(tmp_path):
    import fitz

    pdf = tmp_path / "wrapped_caption.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Figure 1: This figure shows a unified model capable of processing multiple modalities, such\n"
        "as text, audio, image and video, and generating real-time text or speech response. Based on these\n"
        "features, the model supports voice dialogue, video dialogue, and audio-visual tool use.\n"
        "Unrelated body text should not be required for the first line.",
    )
    doc.save(str(pdf))
    doc.close()

    captions = extract_figure_captions(str(pdf))

    assert captions["figure 1"].endswith("audio-visual tool use.")
    assert "such as text" in captions["figure 1"]
