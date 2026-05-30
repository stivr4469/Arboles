import pytest
import pandas as pd
from datetime import date

from src.analytics.pattern_engine import PatternEngine, FireAlarm


TODAY = date(2026, 5, 30)


def make_row(ad_id, spend, clicks, impressions=2000, revenue=0.0, conversions=0):
    return {
        "tenant_id": "test-tenant",
        "ad_id": ad_id,
        "date": TODAY,
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "revenue": revenue,
        "source": "facebook",
    }


class TestFireAlarms:
    def test_empty_dataframe_returns_empty(self):
        alarms = PatternEngine.detect_fire_alarms(pd.DataFrame())
        assert alarms == []

    def test_utm_break_detected_when_spend_no_clicks(self):
        df = pd.DataFrame([make_row("ad_001", spend=50.0, clicks=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert any(a.alarm_type == "UTM_BREAK" for a in alarms)
        assert any(a.ad_id == "ad_001" for a in alarms)

    def test_utm_break_not_triggered_below_min_spend(self):
        # spend ниже порога (default 10.0) → не алертить
        df = pd.DataFrame([make_row("ad_small", spend=5.0, clicks=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert not any(a.alarm_type == "UTM_BREAK" for a in alarms)

    def test_utm_break_not_triggered_when_clicks_present(self):
        df = pd.DataFrame([make_row("ad_ok", spend=50.0, clicks=100)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert not any(a.alarm_type == "UTM_BREAK" for a in alarms)

    def test_utm_break_severity_is_critical(self):
        df = pd.DataFrame([make_row("ad_001", spend=50.0, clicks=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        utm = next(a for a in alarms if a.alarm_type == "UTM_BREAK")
        assert utm.severity == "CRITICAL"
        assert utm.message  # непустое сообщение

    def test_budget_drain_detected_at_2x_baseline(self):
        # today=300, baseline=100 → 3x > drain_multiplier(2.0)
        df = pd.DataFrame([make_row("ad_001", spend=300.0, clicks=500)])
        alarms = PatternEngine.detect_fire_alarms(df, baseline_avg_spend=100.0)
        assert any(a.alarm_type == "BUDGET_DRAIN" for a in alarms)

    def test_budget_drain_not_triggered_below_multiplier(self):
        # today=150, baseline=100 → 1.5x < drain_multiplier(2.0)
        df = pd.DataFrame([make_row("ad_001", spend=150.0, clicks=500)])
        alarms = PatternEngine.detect_fire_alarms(df, baseline_avg_spend=100.0)
        assert not any(a.alarm_type == "BUDGET_DRAIN" for a in alarms)

    def test_budget_drain_not_triggered_without_baseline(self):
        # нет исторических данных → нельзя судить о drain
        df = pd.DataFrame([make_row("ad_001", spend=999.0, clicks=500)])
        alarms = PatternEngine.detect_fire_alarms(df, baseline_avg_spend=0.0)
        assert not any(a.alarm_type == "BUDGET_DRAIN" for a in alarms)

    def test_budget_drain_ad_id_is_none(self):
        df = pd.DataFrame([make_row("ad_001", spend=300.0, clicks=500)])
        alarms = PatternEngine.detect_fire_alarms(df, baseline_avg_spend=100.0)
        drain = next(a for a in alarms if a.alarm_type == "BUDGET_DRAIN")
        assert drain.ad_id is None  # account-level alarm, не per-ad

    def test_no_alarm_on_normal_operation(self):
        df = pd.DataFrame([make_row("ad_001", spend=100.0, clicks=200, conversions=5)])
        alarms = PatternEngine.detect_fire_alarms(df, baseline_avg_spend=90.0)
        assert alarms == []

    def test_multiple_ads_utm_break_all_flagged(self):
        df = pd.DataFrame([
            make_row("ad_001", spend=50.0, clicks=0),
            make_row("ad_002", spend=30.0, clicks=0),
            make_row("ad_003", spend=80.0, clicks=150),  # нормальный
        ])
        alarms = PatternEngine.detect_fire_alarms(df)
        utm_ids = {a.ad_id for a in alarms if a.alarm_type == "UTM_BREAK"}
        assert "ad_001" in utm_ids
        assert "ad_002" in utm_ids
        assert "ad_003" not in utm_ids

    def test_drain_multiplier_is_configurable(self):
        df = pd.DataFrame([make_row("ad_001", spend=150.0, clicks=500)])
        # с multiplier=1.2 → 150 > 1.2*100 = 120 → alarm
        alarms = PatternEngine.detect_fire_alarms(
            df, baseline_avg_spend=100.0, drain_multiplier=1.2
        )
        assert any(a.alarm_type == "BUDGET_DRAIN" for a in alarms)


class TestZeroLeads:
    def test_zero_leads_detected_at_40_clicks(self):
        df = pd.DataFrame([make_row("ad_001", spend=30.0, clicks=40, conversions=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert any(a.alarm_type == "ZERO_LEADS" for a in alarms)
        assert any(a.ad_id == "ad_001" for a in alarms)

    def test_zero_leads_not_triggered_below_40_clicks(self):
        # 39 кликов — ещё статистически незначимо
        df = pd.DataFrame([make_row("ad_001", spend=30.0, clicks=39, conversions=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert not any(a.alarm_type == "ZERO_LEADS" for a in alarms)

    def test_zero_leads_not_triggered_when_conversions_exist(self):
        df = pd.DataFrame([make_row("ad_001", spend=30.0, clicks=50, conversions=1)])
        alarms = PatternEngine.detect_fire_alarms(df)
        assert not any(a.alarm_type == "ZERO_LEADS" for a in alarms)

    def test_zero_leads_severity_is_critical(self):
        df = pd.DataFrame([make_row("ad_001", spend=30.0, clicks=40, conversions=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        alarm = next(a for a in alarms if a.alarm_type == "ZERO_LEADS")
        assert alarm.severity == "CRITICAL"

    def test_zero_leads_threshold_is_configurable(self):
        # порог 20 кликов — срабатывает раньше
        df = pd.DataFrame([make_row("ad_001", spend=30.0, clicks=20, conversions=0)])
        alarms = PatternEngine.detect_fire_alarms(df, min_clicks_for_zero_leads=20)
        assert any(a.alarm_type == "ZERO_LEADS" for a in alarms)

    def test_utm_break_and_zero_leads_dont_overlap(self):
        # UTM_BREAK: clicks=0. ZERO_LEADS: clicks>=40. Не могут быть одновременно.
        df = pd.DataFrame([make_row("ad_001", spend=50.0, clicks=0, conversions=0)])
        alarms = PatternEngine.detect_fire_alarms(df)
        types = {a.alarm_type for a in alarms}
        assert "UTM_BREAK" in types
        assert "ZERO_LEADS" not in types
