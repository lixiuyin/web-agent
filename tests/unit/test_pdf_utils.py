"""Tests for low-level PDF utilities."""

from __future__ import annotations

from webagent.utils.pdf import extract_figure_captions


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
