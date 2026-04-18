"""Unit tests for workflows.car_scout.title_vdp."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.scraping import BrightDataFetchError
from workflows.car_scout.title_vdp import (
    scan_html_for_title,
    verify_title_via_vdp,
)


class TestScanHtmlForTitle:
    def test_clean_html_with_positive_language(self):
        html = "<html><body><p>Clean title, one owner</p></body></html>"
        r = scan_html_for_title(html)
        assert r.verdict == "clean"
        assert r.matched_clean_phrase is not None
        assert "clean title" in r.matched_clean_phrase.lower()

    def test_rebuilt_title_phrase_rejected(self):
        html = "<html><body><p>Rebuilt Title — still runs great!</p></body></html>"
        r = scan_html_for_title(html)
        assert r.verdict == "branded"
        assert "rebuilt" in r.matched_branded_phrase.lower()

    def test_salvage_title_phrase_rejected(self):
        html = "<html><body>This vehicle has a SALVAGE TITLE.</body></html>"
        r = scan_html_for_title(html)
        assert r.verdict == "branded"

    def test_reconstructed_rejected(self):
        html = "<body>Reconstructed title - fully repaired.</body>"
        assert scan_html_for_title(html).verdict == "branded"

    def test_standalone_rebuilt_rejected(self):
        html = "<body>Rebuilt. Runs well.</body>"
        assert scan_html_for_title(html).verdict == "branded"

    def test_branded_wins_over_clean_when_both_present(self):
        """Dealers who have a rebuilt-title disclaimer AND say 'clean CARFAX' must be rejected."""
        html = """
        <body>
          <p>Clean CARFAX report available!</p>
          <p>Rebuilt Title - reconstructed from salvage.</p>
        </body>
        """
        r = scan_html_for_title(html)
        assert r.verdict == "branded"

    def test_script_tag_rebuilt_word_ignored(self):
        """JS code using 'rebuild' shouldn't false-positive."""
        html = """
        <html>
          <body><p>Clean title confirmed</p></body>
          <script>
            function rebuildIndex() { console.log('rebuilt'); }
          </script>
        </html>
        """
        r = scan_html_for_title(html)
        assert r.verdict == "clean"

    def test_unknown_when_neither_phrase_found(self):
        html = "<body><p>2020 Subaru Crosstrek Premium, great condition.</p></body>"
        r = scan_html_for_title(html)
        assert r.verdict == "unknown"

    def test_empty_html(self):
        assert scan_html_for_title("").verdict == "unknown"

    def test_no_accidents_reported_is_clean(self):
        html = "<body>Carfax: no accidents reported.</body>"
        assert scan_html_for_title(html).verdict == "clean"

    def test_one_owner_phrasing_variants(self):
        for phrase in ("Carfax one-owner", "CARFAX One Owner"):
            html = f"<body>{phrase}</body>"
            assert scan_html_for_title(html).verdict == "clean", phrase

    def test_case_insensitive(self):
        html = "<body>REBUILT TITLE</body>"
        assert scan_html_for_title(html).verdict == "branded"

    def test_no_false_positive_on_trim_name(self):
        """'Base trim' must not trigger — only 'rebuilt', 'salvage', etc. do."""
        html = "<body>2018 Subaru Crosstrek Base trim, pristine condition.</body>"
        assert scan_html_for_title(html).verdict == "unknown"


class TestVerifyTitleViaVdp:
    def test_success_returns_scanned_verdict(self):
        mock_client = MagicMock()
        mock_client.fetch.return_value = "<body>Clean title confirmed.</body>"
        r = verify_title_via_vdp("https://x.example/a", mock_client)
        assert r.verdict == "clean"
        assert r.fetch_error is None
        mock_client.fetch.assert_called_once_with("https://x.example/a")

    def test_fetch_error_returns_unknown(self):
        mock_client = MagicMock()
        mock_client.fetch.side_effect = BrightDataFetchError("502 Bad Gateway")
        r = verify_title_via_vdp("https://x.example/a", mock_client)
        assert r.verdict == "unknown"
        assert r.fetch_error is not None
        assert "502" in r.fetch_error

    def test_branded_page_captured(self):
        mock_client = MagicMock()
        mock_client.fetch.return_value = "<body>Rebuilt Title - disclosure</body>"
        r = verify_title_via_vdp("https://x.example/a", mock_client)
        assert r.verdict == "branded"
        assert r.matched_branded_phrase is not None
