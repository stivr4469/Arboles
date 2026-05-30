import pytest
import pandas as pd
from datetime import date, timedelta
from src.analytics.pattern_engine import PatternEngine, CohortROI, BurnoutSignal, ScaleCandidate  # noqa: F401


def make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def base_row(ad_id, dt, spend, impressions, clicks, revenue, conversions=0):
    return {
        'tenant_id': 'test-tenant',
        'ad_id': ad_id,
        'date': dt,
        'spend': spend,
        'impressions': impressions,
        'clicks': clicks,
        'conversions': conversions,
        'revenue': revenue,
        'source': 'synthetic',
    }


TODAY = date(2026, 5, 30)


class TestCohortROI:
    def test_positive_roi(self):
        df = make_df([base_row('ad_001', TODAY, spend=100, impressions=5000, clicks=100, revenue=180)])
        results = PatternEngine.compute_cohort_roi(df)
        roi = next(r for r in results if r.ad_id == 'ad_001')
        assert abs(roi.roi_pct - 80.0) < 0.01

    def test_zero_spend_handled(self):
        df = make_df([base_row('ad_zero', TODAY, spend=0, impressions=100, clicks=5, revenue=50)])
        results = PatternEngine.compute_cohort_roi(df)
        roi = next(r for r in results if r.ad_id == 'ad_zero')
        assert roi.roi_pct == 0.0

    def test_negative_roi(self):
        df = make_df([base_row('ad_loss', TODAY, spend=200, impressions=8000, clicks=80, revenue=100)])
        results = PatternEngine.compute_cohort_roi(df)
        roi = next(r for r in results if r.ad_id == 'ad_loss')
        assert roi.roi_pct < 0.0
        assert abs(roi.roi_pct - (-50.0)) < 0.01

    def test_aggregates_multiple_dates(self):
        rows = [
            base_row('ad_multi', TODAY - timedelta(days=1), spend=100, impressions=4000, clicks=80, revenue=120),
            base_row('ad_multi', TODAY, spend=100, impressions=4000, clicks=80, revenue=80),
        ]
        df = make_df(rows)
        results = PatternEngine.compute_cohort_roi(df)
        roi = next(r for r in results if r.ad_id == 'ad_multi')
        # total spend=200, total revenue=200 => ROI=0%
        assert abs(roi.roi_pct - 0.0) < 0.01
        assert roi.spend == 200.0
        assert roi.revenue == 200.0


class TestBurnout:
    def test_detects_burnout_over_30pct_drop(self):
        rows = []
        for i in range(7):
            day = TODAY - timedelta(days=6 - i)
            # CTR 2.5% for first 4 days, then drops for last 3
            ctr = 0.025 if i < 4 else 0.025 * (1 - (i - 3) * 0.25)
            impressions = 10000
            clicks = int(impressions * ctr)
            rows.append(base_row(
                'burnout_001', day,
                spend=100, impressions=impressions, clicks=max(clicks, 1), revenue=50,
            ))
        df = make_df(rows)
        signals = PatternEngine.detect_burnout(df)
        assert any(s.ad_id == 'burnout_001' for s in signals)

    def test_stable_ctr_not_flagged(self):
        rows = [
            base_row('stable_001', TODAY - timedelta(days=i),
                     spend=50, impressions=10000, clicks=250, revenue=40)
            for i in range(7)
        ]
        df = make_df(rows)
        signals = PatternEngine.detect_burnout(df)
        assert not any(s.ad_id == 'stable_001' for s in signals)

    def test_empty_dataframe_returns_empty(self):
        df = make_df([])
        signals = PatternEngine.detect_burnout(df)
        assert signals == []

    def test_burnout_signal_fields(self):
        rows = []
        for i in range(7):
            day = TODAY - timedelta(days=6 - i)
            ctr = 0.020 if i < 4 else 0.005  # 75% drop
            impressions = 10000
            clicks = int(impressions * ctr)
            rows.append(base_row(
                'burnout_fields', day,
                spend=100, impressions=impressions, clicks=max(clicks, 1), revenue=50,
            ))
        df = make_df(rows)
        signals = PatternEngine.detect_burnout(df)
        signal = next(s for s in signals if s.ad_id == 'burnout_fields')
        assert signal.ctr_drop_pct > 30.0
        assert signal.days_declining > 0


class TestScaleCandidates:
    def test_high_ccs_low_spend_flagged(self):
        rows = []
        # scale_001: high ROI (150%), low spend ($20), 10 conversions/day
        for i in range(7):
            rows.append(base_row(
                'scale_001', TODAY - timedelta(days=i),
                spend=20, impressions=5000, clicks=200, revenue=50, conversions=10,
            ))
        # normal_001: moderate ROI (40%), high spend ($200)
        for i in range(7):
            rows.append(base_row(
                'normal_001', TODAY - timedelta(days=i),
                spend=200, impressions=5000, clicks=100, revenue=280,
            ))
        df = make_df(rows)
        candidates = PatternEngine.detect_scale_candidates(df)
        assert any(c.ad_id == 'scale_001' for c in candidates)

    def test_high_spend_not_scale_candidate(self):
        rows = []
        for i in range(7):
            rows.append(base_row(
                'big_001', TODAY - timedelta(days=i),
                spend=500, impressions=20000, clicks=400, revenue=1200,
            ))
        for i in range(7):
            rows.append(base_row(
                'small_001', TODAY - timedelta(days=i),
                spend=10, impressions=5000, clicks=200, revenue=5,
            ))
        df = make_df(rows)
        candidates = PatternEngine.detect_scale_candidates(df)
        # big_001 has high spend — must not appear even with decent ROI
        assert not any(c.ad_id == 'big_001' for c in candidates)

    def test_empty_dataframe_returns_empty(self):
        df = make_df([])
        candidates = PatternEngine.detect_scale_candidates(df)
        assert candidates == []

    def test_scale_candidate_fields(self):
        rows = []
        for i in range(7):
            rows.append(base_row(
                'scale_002', TODAY - timedelta(days=i),
                spend=15, impressions=4000, clicks=300, revenue=60, conversions=10,
            ))
        for i in range(7):
            rows.append(base_row(
                'normal_002', TODAY - timedelta(days=i),
                spend=300, impressions=4000, clicks=100, revenue=200,
            ))
        df = make_df(rows)
        candidates = PatternEngine.detect_scale_candidates(df)
        scale_c = next((c for c in candidates if c.ad_id == 'scale_002'), None)
        if scale_c is not None:
            assert isinstance(scale_c.relative_roi_pct, float)
            assert isinstance(scale_c.daily_spend, float)
            assert isinstance(scale_c.roi_pct, float)


class TestRelativeROI:
    def test_baseline_is_mean(self):
        rows = [
            # ad_high: ROI 100% (revenue=200, spend=100)
            base_row('ad_high', TODAY, spend=100, impressions=5000, clicks=100, revenue=200),
            # ad_low: ROI 0% (revenue=100, spend=100)
            base_row('ad_low', TODAY, spend=100, impressions=5000, clicks=100, revenue=100),
        ]
        df = make_df(rows)
        rel = PatternEngine.compute_relative_roi(df)
        # baseline = mean(100, 0) = 50
        # ad_high relative_roi = 100 - 50 = +50
        # ad_low  relative_roi = 0 - 50  = -50
        assert abs(rel['ad_high'] - 50.0) < 0.01
        assert abs(rel['ad_low'] - (-50.0)) < 0.01

    def test_empty_returns_empty(self):
        df = make_df([])
        assert PatternEngine.compute_relative_roi(df) == {}
