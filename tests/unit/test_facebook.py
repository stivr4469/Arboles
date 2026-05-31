import json
import pytest
from datetime import date
from unittest.mock import patch, MagicMock, Mock

import httpx

from src.collector.facebook import (
    get_spend_data,
    FBAuthError,
    FBRateLimitError,
    FBUnavailableError,
)

TODAY = date(2026, 5, 31)
YESTERDAY = date(2026, 5, 30)
ACCOUNT_ID = "act_123456789"
TOKEN = "fake-access-token"


def _mock_resp(data: dict, status_code: int = 200) -> Mock:
    resp = Mock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


def _api_page(items: list[dict], next_url: str | None = None) -> dict:
    body: dict = {"data": items}
    if next_url:
        body["paging"] = {"next": next_url}
    return body


class TestFBFallback:
    def test_no_token_falls_back_to_csv(self):
        rows = get_spend_data("act_123", YESTERDAY, TODAY, "t1", access_token="")
        assert isinstance(rows, list)
        for row in rows:
            assert "ad_id" in row
            assert "spend" in row

    def test_no_account_id_falls_back_to_csv(self):
        rows = get_spend_data("", YESTERDAY, TODAY, "t1", access_token=TOKEN)
        assert isinstance(rows, list)

    def test_fallback_rows_have_required_keys(self):
        rows = get_spend_data("", YESTERDAY, TODAY, "tenant-1", access_token="")
        if rows:
            required = {"tenant_id", "ad_id", "date", "spend",
                        "impressions", "clicks", "conversions", "revenue", "source"}
            assert required.issubset(rows[0].keys())


class TestFBApiSuccess:
    def _run(self, pages: list[dict]) -> list[dict]:
        """Helper: patch httpx.Client to return pages in sequence."""
        mock_get = Mock(side_effect=[_mock_resp(p) for p in pages])
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get = mock_get
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            return get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_single_page_parsed(self):
        items = [
            {"ad_id": "ad_001", "date_start": "2026-05-30",
             "spend": "45.5", "impressions": "4200", "clicks": "65"},
        ]
        rows = self._run([_api_page(items)])
        assert len(rows) == 1
        assert rows[0]["ad_id"] == "ad_001"
        assert rows[0]["spend"] == 45.5
        assert rows[0]["impressions"] == 4200
        assert rows[0]["clicks"] == 65
        assert rows[0]["date"] == date(2026, 5, 30)
        assert rows[0]["source"] == "facebook"
        assert rows[0]["conversions"] == 0
        assert rows[0]["revenue"] == 0.0

    def test_pagination_followed(self):
        page1 = _api_page(
            [{"ad_id": "ad_001", "date_start": "2026-05-30",
              "spend": "10", "impressions": "100", "clicks": "5"}],
            next_url="https://graph.facebook.com/next?cursor=abc",
        )
        page2 = _api_page(
            [{"ad_id": "ad_002", "date_start": "2026-05-30",
              "spend": "20", "impressions": "200", "clicks": "10"}],
        )
        rows = self._run([page1, page2])
        assert len(rows) == 2
        assert {r["ad_id"] for r in rows} == {"ad_001", "ad_002"}

    def test_empty_page_returns_no_rows(self):
        rows = self._run([_api_page([])])
        assert rows == []

    def test_missing_optional_fields_default_to_zero(self):
        items = [{"ad_id": "ad_x", "date_start": "2026-05-30"}]
        rows = self._run([_api_page(items)])
        assert rows[0]["spend"] == 0.0
        assert rows[0]["impressions"] == 0
        assert rows[0]["clicks"] == 0

    def test_act_prefix_added_if_missing(self):
        """account_id without 'act_' prefix should still work."""
        mock_get = Mock(return_value=_mock_resp(_api_page([])))
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get = mock_get
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            get_spend_data("123456789", YESTERDAY, TODAY, "t1", TOKEN)
        call_url = mock_get.call_args[0][0]
        assert "act_123456789" in call_url


class TestFBApiErrors:
    def _mock_error_response(self, status: int, code: int = 0):
        error_body = {"error": {"code": code, "message": "Error"}}
        mock_get = Mock(return_value=_mock_resp(error_body, status))
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get = mock_get
        return mock_ctx

    def test_401_raises_fb_auth_error(self):
        with patch("src.collector.facebook.httpx.Client",
                   return_value=self._mock_error_response(401)):
            with pytest.raises(FBAuthError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_error_code_190_raises_fb_auth_error(self):
        with patch("src.collector.facebook.httpx.Client",
                   return_value=self._mock_error_response(400, code=190)):
            with pytest.raises(FBAuthError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_rate_limit_code_17_raises_fb_rate_limit(self):
        with patch("src.collector.facebook.httpx.Client",
                   return_value=self._mock_error_response(400, code=17)):
            with pytest.raises(FBRateLimitError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_rate_limit_code_4_raises_fb_rate_limit(self):
        with patch("src.collector.facebook.httpx.Client",
                   return_value=self._mock_error_response(400, code=4)):
            with pytest.raises(FBRateLimitError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_server_500_raises_fb_unavailable(self):
        with patch("src.collector.facebook.httpx.Client",
                   return_value=self._mock_error_response(500)):
            with pytest.raises(FBUnavailableError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_network_timeout_raises_fb_unavailable(self):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get.side_effect = httpx.TimeoutException("")
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            with pytest.raises(FBUnavailableError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)

    def test_connect_error_raises_fb_unavailable(self):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get.side_effect = httpx.ConnectError("")
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            with pytest.raises(FBUnavailableError):
                get_spend_data(ACCOUNT_ID, YESTERDAY, TODAY, "t1", TOKEN)
