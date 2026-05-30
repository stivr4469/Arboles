import pytest
from datetime import date
from unittest.mock import patch, MagicMock
from src.collector.keitaro import KeitaroClient, KeitaroAuthError

BASE_URL = "https://tracker.example.com"
API_KEY = "test_key_123"


@pytest.fixture
def client():
    return KeitaroClient(base_url=BASE_URL, api_key=API_KEY, ad_id_param="sub3")


def make_mock_response(status_code: int, json_data: dict):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock
        )
    return mock


class TestKeitaroClient:
    def test_aggregates_approved_revenue(self, client):
        rows = [
            {"sub3": "ad_001", "status": "approved", "revenue": 10.0, "clicks": 5},
            {"sub3": "ad_001", "status": "", "revenue": 0.0, "clicks": 100},
            {"sub3": "ad_001", "status": "lead", "revenue": 5.0, "clicks": 3},
        ]
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(200, {"rows": rows})
            result = client.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

        assert len(result) == 1
        assert result[0]["ad_id"] == "ad_001"
        assert result[0]["revenue"] == pytest.approx(15.0)
        assert result[0]["conversions"] == 8  # approved(5) + lead(3)

    def test_raises_auth_error_on_401(self, client):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(401, {})
            with pytest.raises(KeitaroAuthError):
                client.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

    def test_raises_auth_error_on_403(self, client):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(403, {})
            with pytest.raises(KeitaroAuthError):
                client.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

    def test_skips_empty_ad_id(self, client):
        rows = [
            {"sub3": "", "status": "approved", "revenue": 5.0, "clicks": 2},
            {"sub3": "ad_valid", "status": "approved", "revenue": 10.0, "clicks": 3},
        ]
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(200, {"rows": rows})
            result = client.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

        assert len(result) == 1
        assert result[0]["ad_id"] == "ad_valid"

    def test_uses_correct_auth_header(self, client):
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(200, {"rows": []})
            client.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["headers"]["Api-Key"] == API_KEY

    def test_configurable_ad_id_param(self):
        client_sub2 = KeitaroClient(BASE_URL, API_KEY, ad_id_param="sub2")
        rows = [{"sub2": "ad_via_sub2", "status": "approved", "revenue": 20.0, "clicks": 10}]
        with patch("httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(200, {"rows": rows})
            result = client_sub2.get_clicks_report(date(2026, 5, 30), date(2026, 5, 30))

        assert result[0]["ad_id"] == "ad_via_sub2"
