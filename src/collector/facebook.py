from datetime import date
from pathlib import Path
import pandas as pd

_SAMPLE_PATH = Path(__file__).parent.parent.parent / "data" / "sample" / "fb_ads_spend.csv"


def get_spend_data(
    account_id: str,
    date_from: date,
    date_to: date,
    tenant_id: str = "",
) -> list[dict]:
    """
    FB Stub Client. Интерфейс совместим с реальным FB Marketing API.
    Читает data/sample/fb_ads_spend.csv, фильтрует по датам и account_id.

    Реальный клиент будет вызывать Graph API v18+ с той же сигнатурой.
    """
    df = pd.read_csv(_SAMPLE_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    mask = (df["date"] >= date_from) & (df["date"] <= date_to)
    if account_id:
        mask &= df["account_id"] == account_id

    subset = df[mask].copy()
    subset = subset.rename(columns={
        "spend_usd": "spend",
        "conversion_value_usd": "revenue",
    })

    result = []
    for _, row in subset.iterrows():
        result.append({
            "tenant_id": tenant_id,
            "ad_id": str(row["ad_id"]),
            "date": row["date"],
            "spend": float(row["spend"]),
            "impressions": int(row["impressions"]),
            "clicks": int(row["clicks"]),
            "conversions": int(row.get("conversions", 0)),
            "revenue": float(row["revenue"]),
            "source": "facebook",
        })

    return result
