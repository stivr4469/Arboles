import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

TENANT_ID = "00000000-0000-0000-0000-000000000001"  # фиксированный для тестов
TODAY = date(2026, 5, 30)
DAYS = 7

AD_PATTERNS: dict[str, Literal['burnout', 'scale', 'normal']] = {
    'burnout_001': 'burnout',
    'burnout_002': 'burnout',
    'burnout_003': 'burnout',
    'scale_001':   'scale',
    'scale_002':   'scale',
    'scale_003':   'scale',
    **{f'normal_{i:03d}': 'normal' for i in range(1, 15)}
}


def _make_row(ad_id: str, dt: date, pattern: str, day_idx: int) -> dict:
    """Generate one merged row for (ad_id, date)."""
    rng = random.Random(f"{ad_id}_{dt}")  # детерминированный seed

    if pattern == 'burnout':
        base_spend = rng.uniform(80, 120)
        # CTR деградирует в последние 3 дня (day_idx 0=oldest, 6=today)
        if day_idx >= 4:  # последние 3 дня
            ctr_decay = 1.0 - (day_idx - 3) * 0.22  # -22% каждый день
        else:
            ctr_decay = 1.0
        base_ctr = max(0.008, 0.025 * ctr_decay)
        roi_factor = rng.uniform(0.35, 0.55)

    elif pattern == 'scale':
        base_spend = rng.uniform(15, 40)   # намеренно низкий
        base_ctr = rng.uniform(0.030, 0.045)  # стабильно высокий
        roi_factor = rng.uniform(0.85, 1.40)  # ROI 85–140%

    else:  # normal
        base_spend = rng.uniform(20, 150)
        base_ctr = rng.uniform(0.015, 0.025)
        roi_factor = rng.uniform(0.20, 0.70)

    impressions = int(base_spend * rng.uniform(80, 120))  # ~100 impr/$
    clicks = max(1, int(impressions * base_ctr))
    conversions = max(0, int(clicks * rng.uniform(0.02, 0.08)))
    revenue = round(base_spend * (1 + roi_factor), 2)

    return {
        'tenant_id': TENANT_ID,
        'ad_id': ad_id,
        'date': dt,
        'spend': round(base_spend, 2),
        'impressions': impressions,
        'clicks': clicks,
        'conversions': conversions,
        'revenue': revenue,
        'source': 'synthetic',
    }


def generate_aggregated() -> list[dict]:
    """7 days × 20 ad_ids = 140 merged rows. For PatternEngine."""
    rows = []
    for ad_id, pattern in AD_PATTERNS.items():
        for day_idx in range(DAYS):
            dt = TODAY - timedelta(days=DAYS - 1 - day_idx)
            rows.append(_make_row(ad_id, dt, pattern, day_idx))
    return rows


def generate_raw_events(n: int = 10_000) -> list[dict]:
    """Generate N raw click-level events. For T-03 done condition (INSERT 10k)."""
    events = []
    ad_ids = list(AD_PATTERNS.keys())
    rng = random.Random(42)
    for i in range(n):
        ad_id = rng.choice(ad_ids)
        day_offset = rng.randint(0, DAYS - 1)
        dt = TODAY - timedelta(days=day_offset)
        status = rng.choices(['sale', 'rejected', ''], weights=[0.05, 0.10, 0.85])[0]
        revenue = round(rng.uniform(10, 50), 2) if status == 'sale' else 0.0
        events.append({
            'tenant_id': TENANT_ID,
            'ad_id': ad_id,
            'date': dt,
            'spend': 0.0,       # raw events не имеют spend
            'impressions': 0,
            'clicks': 1,
            'conversions': 1 if status == 'sale' else 0,
            'revenue': revenue,
            'source': 'keitaro_raw',
        })
    return events
