import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
_GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
_SAMPLE_PATH = Path(__file__).parent.parent.parent / "data" / "sample" / "fb_ads_spend.csv"

# Fields returned by the Insights API (revenue/conversions come from Keitaro)
_FIELDS = "ad_id,spend,impressions,clicks,date_start"


class FBAuthError(Exception):
    """Token invalid or expired (API code 190 / HTTP 401). No retry."""


class FBRateLimitError(Exception):
    """API rate limit hit (codes 4, 17, 32). Retry after delay."""


class FBUnavailableError(Exception):
    """FB API unreachable or returned 5xx. Retry."""


def get_spend_data(
    account_id: str,
    date_from: date,
    date_to: date,
    tenant_id: str = "",
    access_token: str = "",
) -> list[dict]:
    """
    Fetch ad-level spend from Facebook Marketing API.
    Falls back to sample CSV when access_token or account_id is empty.

    Returns rows compatible with ClickHouse ad_performance_merged schema.
    Revenue and conversions are left at 0 — filled later by the Keitaro collector.
    """
    if not access_token or not account_id:
        logger.debug("FB API: no token/account_id — using CSV fallback")
        return _load_from_csv(date_from, date_to, account_id, tenant_id)
    return _fetch_from_api(account_id, date_from, date_to, tenant_id, access_token)


# ── internal ──────────────────────────────────────────────────────────────────

def _fetch_from_api(
    account_id: str,
    date_from: date,
    date_to: date,
    tenant_id: str,
    access_token: str,
) -> list[dict]:
    act_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
    url = f"{_GRAPH_BASE}/{act_id}/insights"
    params = {
        "level": "ad",
        "fields": _FIELDS,
        "time_range": json.dumps({"since": str(date_from), "until": str(date_to)}),
        "time_increment": 1,
        "access_token": access_token,
        "limit": 500,
    }

    rows: list[dict] = []
    with httpx.Client(timeout=30.0) as client:
        while url:
            try:
                resp = client.get(url, params=params)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise FBUnavailableError(f"FB API unreachable: {exc}") from exc

            _raise_for_error(resp)
            body = resp.json()

            for item in body.get("data", []):
                rows.append({
                    "tenant_id": tenant_id,
                    "ad_id": str(item["ad_id"]),
                    "date": date.fromisoformat(item["date_start"]),
                    "spend": float(item.get("spend") or 0),
                    "impressions": int(item.get("impressions") or 0),
                    "clicks": int(item.get("clicks") or 0),
                    "conversions": 0,
                    "revenue": 0.0,
                    "source": "facebook",
                })

            url = body.get("paging", {}).get("next")
            params = {}  # next URL already contains all params

    logger.info("FB API: fetched %d rows for %s (%s – %s)", len(rows), act_id, date_from, date_to)
    return rows


def _raise_for_error(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    try:
        code = resp.json().get("error", {}).get("code", 0)
    except Exception:
        code = 0

    if resp.status_code in (401, 403) or code == 190:
        raise FBAuthError(f"FB token invalid or expired (HTTP {resp.status_code}, code={code})")
    if code in (4, 17, 32):
        raise FBRateLimitError(f"FB API rate limit hit (code={code})")
    raise FBUnavailableError(f"FB API error HTTP {resp.status_code}: {resp.text[:200]}")


def _load_from_csv(
    date_from: date,
    date_to: date,
    account_id: str,
    tenant_id: str,
) -> list[dict]:
    df = pd.read_csv(_SAMPLE_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    mask = (df["date"] >= date_from) & (df["date"] <= date_to)
    if account_id:
        mask &= df["account_id"] == account_id
    subset = df[mask].rename(columns={"spend_usd": "spend", "conversion_value_usd": "revenue"})
    return [
        {
            "tenant_id": tenant_id,
            "ad_id": str(row["ad_id"]),
            "date": row["date"],
            "spend": float(row["spend"]),
            "impressions": int(row["impressions"]),
            "clicks": int(row["clicks"]),
            "conversions": int(row.get("conversions", 0)),
            "revenue": float(row["revenue"]),
            "source": "facebook",
        }
        for _, row in subset.iterrows()
    ]
