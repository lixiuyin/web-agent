"""Regression tests for browser security defaults."""

from webagent.browser.controller import BrowserController
from webagent.browser.stealth import CHROME_STEALTH_ARGS


def test_stealth_flags_do_not_disable_core_browser_security() -> None:
    forbidden = {
        "--disable-web-security",
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-client-side-phishing-detection",
    }

    assert forbidden.isdisjoint(CHROME_STEALTH_ARGS)
    assert all("IsolateOrigins" not in flag for flag in CHROME_STEALTH_ARGS)


def test_controller_validates_https_by_default() -> None:
    browser = BrowserController()

    assert browser.ignore_https_errors is False
    assert browser.temporary_profile is True
    assert browser.humanize_delays is False
    assert browser.locale is None
    assert browser.timezone_id is None
    assert browser.proxy_server is None
