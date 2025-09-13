from unittest.mock import patch

from macbot.tools import browse_website, web_search


def test_web_search_encodes_query():
    with patch("macbot.tools.subprocess.run") as mock_run:
        result = web_search("C++ tips & tricks")
        expected_url = "https://www.google.com/search?q=C%2B%2B%20tips%20%26%20tricks"
        mock_run.assert_called_once_with([
            "open",
            "-a",
            "Safari",
            expected_url,
        ], check=True)
        assert "Opened Safari" in result


def test_browse_website_encodes_url():
    with patch("macbot.tools.subprocess.run") as mock_run:
        result = browse_website("example.com/path with spaces?q=a b#frag ment")
        expected_url = "https://example.com/path%20with%20spaces?q=a%20b#frag%20ment"
        mock_run.assert_called_once_with([
            "open",
            "-a",
            "Safari",
            expected_url,
        ], check=True)
        assert result == f"Opened {expected_url} in Safari."
