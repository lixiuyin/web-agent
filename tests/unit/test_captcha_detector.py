"""Unit tests for captcha detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from webagent.browser.captcha_detector import CaptchaDetector, check_captcha


@pytest.fixture
def mock_page():
    """Create a mock Playwright page."""
    page = MagicMock()
    page.url = "https://example.com"
    return page


class TestCaptchaDetector:
    """Tests for CaptchaDetector class."""

    def test_init(self):
        """Test detector initialization."""
        detector = CaptchaDetector()
        assert detector.detected_type is None
        assert detector.detected_confidence == 0.0

    @pytest.mark.asyncio
    async def test_no_captcha(self, mock_page):
        """Test detection when no captcha is present."""
        mock_page.title = AsyncMock(return_value="Welcome Page")
        mock_page.query_selector = AsyncMock(return_value=None)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is False
        assert result["type"] is None
        assert result["confidence"] == 0.0
        assert result["selectors"] == []

    @pytest.mark.asyncio
    async def test_recaptcha_detection_by_dom(self, mock_page):
        """Test reCAPTCHA detection via DOM patterns."""
        mock_page.title = AsyncMock(return_value="Login Page")
        mock_page.url = "https://example.com/login"

        # Mock finding reCAPTCHA iframe
        mock_element = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is True
        assert result["type"] == "recaptcha"
        assert result["confidence"] == 0.9
        assert len(result["selectors"]) > 0

    @pytest.mark.asyncio
    async def test_hcaptcha_detection(self, mock_page):
        """Test hCaptcha detection."""
        mock_page.title = AsyncMock(return_value="Verify You Are Human")
        mock_page.url = "https://example.com"

        # Mock finding hCaptcha element
        mock_element = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is True
        assert result["type"] in ["hcaptcha", "recaptcha"]  # Could match either

    @pytest.mark.asyncio
    async def test_cloudflare_detection(self, mock_page):
        """Test Cloudflare challenge detection."""
        mock_page.title = AsyncMock(return_value="Just a moment...")
        mock_page.url = "https://example.com"

        # Mock finding Cloudflare element
        mock_element = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is True

    @pytest.mark.asyncio
    async def test_keyword_detection(self, mock_page):
        """Test captcha detection via keywords in title/URL."""
        mock_page.title = AsyncMock(return_value="CAPTCHA: Verify You Are Human")
        mock_page.url = "https://example.com/challenge"
        mock_page.query_selector = AsyncMock(return_value=None)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is True
        assert result["type"] == "unknown"
        assert result["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_url_keyword_detection(self, mock_page):
        """Test captcha detection via URL keywords."""
        mock_page.title = AsyncMock(return_value="Page Title")
        mock_page.url = "https://example.com/captcha-verification"
        mock_page.query_selector = AsyncMock(return_value=None)

        detector = CaptchaDetector()
        result = await detector.detect_captcha(mock_page)

        assert result["detected"] is True
        assert result["type"] == "unknown"

    @pytest.mark.asyncio
    async def test_detector_state_updates(self, mock_page):
        """Test that detector state updates after detection."""
        mock_page.title = AsyncMock(return_value="Login")
        mock_element = MagicMock()
        mock_page.query_selector = AsyncMock(return_value=mock_element)

        detector = CaptchaDetector()
        assert detector.detected_type is None

        await detector.detect_captcha(mock_page)

        assert detector.detected_type is not None
        assert detector.detected_confidence > 0


@pytest.mark.asyncio
async def test_check_captcha_convenience_function(mock_page):
    """Test the convenience function."""
    mock_page.title = AsyncMock(return_value="Page")
    mock_page.query_selector = AsyncMock(return_value=None)

    result = await check_captcha(mock_page)

    assert isinstance(result, dict)
    assert "detected" in result
    assert "type" in result
