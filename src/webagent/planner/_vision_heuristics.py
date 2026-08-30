"""Heuristics for judging whether a chat model actually processed an image.

Some OpenAI-compatible endpoints accept image payloads but silently ignore
them, or return a boilerplate "I can't see images" apology. The planner probes
vision support with a known red square; these string heuristics classify the
probe/answer without a second round-trip.
"""

from __future__ import annotations

# Phrases a model emits when it received no usable image.
_NO_VISION_PHRASES = (
    "i don't see any image",
    "i don't see an image",
    "i cannot see any image",
    "i cannot see the image",
    "no image attached",
    "no image provided",
    "no image was provided",
    "i'm unable to view",
    "i am unable to view",
    "i can't view the image",
    "i cannot view the image",
    "i don't have the ability to view",
    "i do not have the ability to view",
    "i'm not able to see",
    "i am not able to see",
    "there is no image",
    "image is not visible",
    "unable to see the image",
    "cannot analyze images",
    "i cannot analyze the image",
)

# Words/phrases that suggest the model is genuinely describing an image.
_VISUAL_INDICATORS = (
    # Colors
    "red",
    "blue",
    "green",
    "yellow",
    "black",
    "white",
    "orange",
    "purple",
    "pink",
    "brown",
    "gray",
    "grey",
    "color",
    # Shapes and visual elements
    "shows",
    "shows a",
    "depicts",
    "displays",
    "illustrates",
    "presents",
    "figure",
    "chart",
    "graph",
    "image",
    "diagram",
    "plot",
    "rectangle",
    "square",
    "circle",
    "left",
    "right",
    "top",
    "bottom",
    "center",
    # Descriptive language
    "the image",
    "this figure",
    "the chart",
    "the graph",
    "the picture",
    "we can see",
    "visible",
    "appears to be",
    "see the",
    "image is",
)

# Sentence patterns that describe visual content in medium-length answers.
_VISUAL_PATTERNS = ("is a", "is an", "consists of", "contains", "made of", "solid")


def indicates_no_vision(response: str) -> bool:
    """Return True if the response suggests the model cannot see the image."""
    if not response:
        return False
    lower = response.lower()
    return any(phrase in lower for phrase in _NO_VISION_PHRASES)


def has_visual_content(response: str) -> bool:
    """Return True if the response reads like a genuine visual description."""
    if not response:
        return False

    response_lower = response.lower()
    has_indicator = any(indicator in response_lower for indicator in _VISUAL_INDICATORS)

    # Very short alphabetic answers (e.g. "Red", "Blue") are valid vision replies.
    if not has_indicator and len(response) < 10 and response.isalpha():
        return True

    # Medium-length answers that describe visual content (e.g. "It is a green bar").
    if not has_indicator and 10 <= len(response) <= 150:
        return any(pattern in response_lower for pattern in _VISUAL_PATTERNS)

    return has_indicator
