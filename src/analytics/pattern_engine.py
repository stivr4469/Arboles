import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import date


@dataclass
class CohortROI:
    ad_id: str
    spend: float
    revenue: float
    roi_pct: float
    conversions: int


@dataclass
class BurnoutSignal:
    ad_id: str
    ctr_drop_pct: float
    days_declining: int
    avg_baseline_ctr: float
    avg_recent_ctr: float


@dataclass
class ScaleCandidate:
    ad_id: str
    relative_roi_pct: float  # насколько ROI выше среднего по аккаунту
    daily_spend: float
    roi_pct: float
    conversions: int


@dataclass
class FireAlarm:
    alarm_type: str   # 'UTM_BREAK' | 'BUDGET_DRAIN' | 'ZERO_LEADS'
    ad_id: str | None
    message: str
    severity: str     # 'CRITICAL' | 'WARNING'


class PatternEngine:

    @staticmethod
    def merge_sources(fb_path: str, keitaro_path: str, tenant_id: str) -> pd.DataFrame:
        """Объединение расходов FB и кликов Keitaro по ключу fb_ads.ad_id = keitaro.sub3."""
        df_spend = pd.read_csv(fb_path)
        df_clicks = pd.read_csv(keitaro_path)

        df_spend = df_spend.rename(columns={'spend_usd': 'spend'})
        df_spend['date'] = pd.to_datetime(df_spend['date']).dt.date
        df_spend['ad_id'] = df_spend['ad_id'].astype(str)

        df_clicks = df_clicks.rename(columns={'revenue_usd': 'revenue'})
        df_clicks['click_time'] = pd.to_datetime(df_clicks['datetime'])
        df_clicks['click_date'] = df_clicks['click_time'].dt.date
        df_clicks['ad_id'] = df_clicks['sub3'].astype(str)

        clicks_aggregated = (
            df_clicks
            .groupby(['click_date', 'ad_id'])
            .agg(
                conversions=('status', lambda x: x.isin(['approved', 'lead']).sum()),
                revenue=('revenue', 'sum'),
            )
            .reset_index()
        )

        merged = pd.merge(
            df_spend,
            clicks_aggregated,
            left_on=['date', 'ad_id'],
            right_on=['click_date', 'ad_id'],
            how='left',
        )

        merged['conversions'] = merged['conversions'].fillna(0).astype(int)
        merged['revenue'] = merged['revenue'].fillna(0.0)
        merged['tenant_id'] = tenant_id
        merged['source'] = 'facebook'

        cols = ['tenant_id', 'ad_id', 'date', 'spend', 'impressions',
                'clicks', 'conversions', 'revenue', 'source']
        return merged[cols]

    @staticmethod
    def compute_cohort_roi(df: pd.DataFrame) -> list[CohortROI]:
        """Когортный ROI на основе суммарных данных. ROI = (revenue - spend) / spend * 100."""
        if df.empty or 'ad_id' not in df.columns:
            return []

        grouped = (
            df.groupby('ad_id')
            .agg(spend=('spend', 'sum'), revenue=('revenue', 'sum'), conversions=('conversions', 'sum'))
            .reset_index()
        )

        results: list[CohortROI] = []
        for _, row in grouped.iterrows():
            spend = float(row['spend'])
            revenue = float(row['revenue'])
            conversions = int(row['conversions'])
            roi_pct = ((revenue - spend) / spend * 100.0) if spend > 0 else 0.0
            results.append(CohortROI(ad_id=str(row['ad_id']), spend=spend,
                                     revenue=revenue, roi_pct=roi_pct, conversions=conversions))
        return results

    @staticmethod
    def compute_relative_roi(df: pd.DataFrame) -> dict[str, float]:
        """Насколько ROI объявления выше/ниже среднего ROI по аккаунту."""
        cohort_rois = PatternEngine.compute_cohort_roi(df)
        if not cohort_rois:
            return {}
        baseline_roi = float(np.mean([c.roi_pct for c in cohort_rois]))
        return {c.ad_id: c.roi_pct - baseline_roi for c in cohort_rois}

    @staticmethod
    def detect_burnout(
        df: pd.DataFrame,
        ctr_drop_threshold: float = 0.30,
        recent_window_days: int = 3,
        baseline_window_days: int = 7,
        min_impressions_threshold: int = 3000,
    ) -> list[BurnoutSignal]:
        """Поиск выгоревших креативов.

        Сравнивает средний CTR за последние 3 дня со стабильным средним за предыдущие 7 дней.
        min_impressions_threshold — защита от шума Пуассона на малых объёмах.
        """
        if df.empty:
            return []

        df = df.copy()
        df['date'] = pd.to_datetime(df['date']).dt.date
        max_date = df['date'].max()

        daily = (
            df.groupby(['ad_id', 'date'])
            .agg(clicks=('clicks', 'sum'), impressions=('impressions', 'sum'))
            .reset_index()
        )
        daily['ctr'] = np.where(daily['impressions'] > 0,
                                daily['clicks'] / daily['impressions'], 0.0)

        signals: list[BurnoutSignal] = []
        for ad_id, group in daily.groupby('ad_id'):
            group = group.sort_values('date')

            recent_start = (pd.Timestamp(max_date) - pd.Timedelta(days=recent_window_days)).date()
            recent = group[group['date'] > recent_start]

            baseline_start = (pd.Timestamp(max_date) - pd.Timedelta(
                days=recent_window_days + baseline_window_days)).date()
            baseline = group[(group['date'] > baseline_start) & (group['date'] <= recent_start)]

            if recent.empty or baseline.empty:
                continue

            if baseline['impressions'].sum() < min_impressions_threshold:
                continue

            avg_recent = float(recent['ctr'].mean())
            avg_baseline = float(baseline['ctr'].mean())

            if avg_baseline <= 0:
                continue

            drop_fraction = (avg_baseline - avg_recent) / avg_baseline
            if drop_fraction > ctr_drop_threshold:
                signals.append(BurnoutSignal(
                    ad_id=str(ad_id),
                    ctr_drop_pct=round(drop_fraction * 100, 2),
                    days_declining=len(recent),
                    avg_baseline_ctr=round(avg_baseline, 4),
                    avg_recent_ctr=round(avg_recent, 4),
                ))

        return signals

    @staticmethod
    def detect_scale_candidates(
        df: pd.DataFrame,
        min_relative_roi: float = 10.0,
        min_conversions: int = 5,
    ) -> list[ScaleCandidate]:
        """Поиск недоиспользованных объявлений с устойчивым объёмом конверсий.

        min_relative_roi — минимальное опережение среднего ROI по аккаунту (п.п.).
        min_conversions — защита от ложного масштабирования «случайных» лидов.
        Порог spend — динамический percentile(25), не хардкод.
        """
        if df.empty:
            return []

        daily_spend = (
            df.groupby(['ad_id', 'date'])['spend']
            .sum().reset_index()
            .groupby('ad_id')['spend'].mean().reset_index()
            .rename(columns={'spend': 'avg_daily_spend'})
        )

        if daily_spend.empty:
            return []

        spend_threshold = float(np.percentile(daily_spend['avg_daily_spend'], 25))
        relative_rois = PatternEngine.compute_relative_roi(df)
        cohort_rois = {c.ad_id: c for c in PatternEngine.compute_cohort_roi(df)}

        candidates: list[ScaleCandidate] = []
        for _, row in daily_spend.iterrows():
            ad_id = str(row['ad_id'])
            avg_daily = float(row['avg_daily_spend'])
            rel_roi = relative_rois.get(ad_id, 0.0)
            cohort = cohort_rois.get(ad_id)
            if not cohort:
                continue
            if cohort.conversions < min_conversions:
                continue
            if rel_roi > min_relative_roi and avg_daily < spend_threshold:
                candidates.append(ScaleCandidate(
                    ad_id=ad_id,
                    relative_roi_pct=round(rel_roi, 2),
                    daily_spend=round(avg_daily, 2),
                    roi_pct=round(cohort.roi_pct, 2),
                    conversions=cohort.conversions,
                ))

        return candidates

    @staticmethod
    def detect_fire_alarms(
        today_df: pd.DataFrame,
        baseline_avg_spend: float = 0.0,
        min_spend_for_alarm: float = 10.0,
        drain_multiplier: float = 2.0,
        min_clicks_for_zero_leads: int = 40,
    ) -> list[FireAlarm]:
        """Детектирует критические аномалии для немедленного Fire Alarm.

        UTM_BREAK:    spend > min_spend_for_alarm и clicks == 0 (per ad_id).
        BUDGET_DRAIN: суммарный дневной spend > drain_multiplier × baseline_avg_spend.
        ZERO_LEADS:   clicks >= min_clicks_for_zero_leads и conversions == 0.
                      Не пересекается с UTM_BREAK (clicks==0 исключён).
        """
        if today_df.empty:
            return []

        alarms: list[FireAlarm] = []

        for _, row in today_df.iterrows():
            spend = float(row.get("spend", 0))
            clicks = int(row.get("clicks", 0))
            conversions = int(row.get("conversions", 0))
            ad_id = str(row.get("ad_id", ""))

            if spend > min_spend_for_alarm and clicks == 0:
                alarms.append(FireAlarm(
                    alarm_type="UTM_BREAK",
                    ad_id=ad_id,
                    message=f"Расход ${spend:.0f}, кликов 0. Трекер не фиксирует переходы.",
                    severity="CRITICAL",
                ))
            elif clicks >= min_clicks_for_zero_leads and conversions == 0:
                alarms.append(FireAlarm(
                    alarm_type="ZERO_LEADS",
                    ad_id=ad_id,
                    message=f"{clicks} кликов, 0 лидов. Связка не конвертит.",
                    severity="CRITICAL",
                ))

        if baseline_avg_spend > 0:
            total_spend = float(today_df["spend"].sum())
            if total_spend > drain_multiplier * baseline_avg_spend:
                alarms.append(FireAlarm(
                    alarm_type="BUDGET_DRAIN",
                    ad_id=None,
                    message=(
                        f"Дневной расход ${total_spend:.0f} превысил "
                        f"{drain_multiplier:.0f}x от среднего "
                        f"(${baseline_avg_spend:.0f}/день)."
                    ),
                    severity="CRITICAL",
                ))

        return alarms
