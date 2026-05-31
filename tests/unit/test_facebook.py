import json
import pytest
from datetime import date
from unittest.mock import patch, MagicMock, Mock

import httpx

from src.collector.facebook import (
    get_spend_data,
    check_account_status,
    FBAccountStatus,
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


class TestCheckAccountStatus:
    def _run(self, body: dict, http_status: int = 200) -> FBAccountStatus:
        mock_get = Mock(return_value=_mock_resp(body, http_status))
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get = mock_get
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            return check_account_status("act_123456789", TOKEN)

    def test_active_account_is_healthy(self):
        status = self._run({"account_status": 1, "disable_reason": 0})
        assert status.is_healthy is True
        assert status.status_label == "ACTIVE"
        assert status.disable_reason_label == ""

    def test_disabled_returns_not_healthy(self):
        status = self._run({"account_status": 2, "disable_reason": 1})
        assert status.is_healthy is False
        assert status.status_label == "DISABLED"

    def test_unsettled_returns_not_healthy(self):
        status = self._run({"account_status": 3, "disable_reason": 3})
        assert status.is_healthy is False
        assert status.status_label == "UNSETTLED"

    def test_pending_risk_review_returns_not_healthy(self):
        status = self._run({"account_status": 7, "disable_reason": 0})
        assert status.is_healthy is False
        assert status.status_label == "PENDING_RISK_REVIEW"

    def test_closed_returns_not_healthy(self):
        status = self._run({"account_status": 101, "disable_reason": 0})
        assert status.is_healthy is False
        assert status.status_label == "CLOSED"

    def test_unknown_status_code_returns_not_healthy(self):
        status = self._run({"account_status": 999, "disable_reason": 0})
        assert status.is_healthy is False
        assert "999" in status.status_label

    def test_policy_violation_disable_reason_label(self):
        status = self._run({"account_status": 2, "disable_reason": 1})
        assert status.disable_reason_label == "Policy Violation"

    def test_billing_disable_reason_label(self):
        status = self._run({"account_status": 3, "disable_reason": 3})
        assert status.disable_reason_label == "Billing Issue"

    def test_account_id_stored_on_result(self):
        status = self._run({"account_status": 1, "disable_reason": 0})
        assert status.account_id == "act_123456789"

    def test_act_prefix_added_if_missing(self):
        mock_get = Mock(return_value=_mock_resp({"account_status": 1, "disable_reason": 0}))
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get = mock_get
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            check_account_status("123456789", TOKEN)
        assert "act_123456789" in mock_get.call_args[0][0]

    def test_auth_error_propagated(self):
        with pytest.raises(FBAuthError):
            self._run({"error": {"code": 190, "message": "Invalid token"}}, http_status=400)

    def test_network_timeout_propagated(self):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value.get.side_effect = httpx.TimeoutException("")
        with patch("src.collector.facebook.httpx.Client", return_value=mock_ctx):
            with pytest.raises(FBUnavailableError):
                check_account_status("act_123", TOKEN)
