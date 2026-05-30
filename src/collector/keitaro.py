from datetime import date
import httpx


class KeitaroAuthError(Exception):
    """401/403 — не делать Celery retry."""


class KeitaroUnavailableError(Exception):
    """Сервер Keitaro недоступен (таймаут, connection refused)."""


class KeitaroClient:
    def __init__(self, base_url: str, api_key: str, ad_id_param: str = "sub3"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ad_id_param = ad_id_param  # sub1/sub2/sub3 из keitaro_configs

    def get_clicks_report(
        self,
        date_from: date,
        date_to: date,
        source_filter: str = "facebook",
    ) -> list[dict]:
        """
        POST /api/v1/report/build — возвращает агрегированный отчёт.
        Каждая запись: {ad_id, conversions, revenue, clicks}.
        Raises KeitaroAuthError при 401/403.
        Raises httpx.HTTPError при 5xx/timeout (Celery делает retry).
        """
        url = f"{self.base_url}/api/v1/report/build"
        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "range": {
                "from": f"{date_from} 00:00:00",
                "to": f"{date_to} 23:59:59",
            },
            "grouping": [self.ad_id_param, "status"],
            "filters": [
                {"name": "sub1", "operator": "==", "expression": source_filter},
                {"name": self.ad_id_param, "operator": "!=", "expression": ""},
            ],
            "metrics": ["clicks", "revenue"],
            "timezone": "UTC",
        }

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=15)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise KeitaroUnavailableError(f"Keitaro server is unreachable: {exc}") from exc

        if resp.status_code in (401, 403):
            raise KeitaroAuthError(f"Keitaro auth failed: {resp.status_code}")

        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])

        return self._aggregate_by_ad_id(rows, date_from)

    def _aggregate_by_ad_id(self, rows: list[dict], click_date: date) -> list[dict]:
        """
        Сворачиваем строки по ad_id.
        status IN ('approved', 'lead') → conversions.
        Пустой status — не считается конверсией.
        """
        aggregated: dict[str, dict] = {}

        for row in rows:
            ad_id = str(row.get(self.ad_id_param, "")).strip()
            if not ad_id:
                continue

            status = str(row.get("status", "")).strip()
            revenue = float(row.get("revenue", 0) or 0)
            clicks = int(row.get("clicks", 0) or 0)
            is_conversion = status in ("approved", "lead")

            if ad_id not in aggregated:
                aggregated[ad_id] = {
                    "ad_id": ad_id,
                    "date": click_date,
                    "clicks": 0,
                    "conversions": 0,
                    "revenue": 0.0,
                }

            aggregated[ad_id]["clicks"] += clicks
            aggregated[ad_id]["revenue"] += revenue
            if is_conversion:
                aggregated[ad_id]["conversions"] += clicks

        return list(aggregated.values())
