from datetime import date, timedelta
from src.data_gen.synthetic import generate_aggregated, generate_raw_events, AD_PATTERNS, TODAY, DAYS


class TestGenerateAggregated:
    def test_correct_row_count(self):
        rows = generate_aggregated()
        assert len(rows) == len(AD_PATTERNS) * DAYS  # 20 × 7 = 140

    def test_all_fields_present(self):
        rows = generate_aggregated()
        required = {'tenant_id', 'ad_id', 'date', 'spend', 'impressions', 'clicks', 'conversions', 'revenue', 'source'}
        assert all(required.issubset(r.keys()) for r in rows)

    def test_no_zero_impressions(self):
        """Деление на ноль в CTR расчёте невозможно."""
        rows = generate_aggregated()
        assert all(r['impressions'] > 0 for r in rows)

    def test_burnout_ctr_drops_in_last_3_days(self):
        rows = generate_aggregated()
        burnout_rows = [r for r in rows if r['ad_id'] == 'burnout_001']
        # Сортируем по дате
        burnout_rows.sort(key=lambda r: r['date'])
        early_ctr = burnout_rows[0]['clicks'] / burnout_rows[0]['impressions']
        late_ctr = burnout_rows[-1]['clicks'] / burnout_rows[-1]['impressions']
        drop_pct = (early_ctr - late_ctr) / early_ctr
        assert drop_pct > 0.30, f"Expected >30% CTR drop, got {drop_pct:.1%}"

    def test_scale_spend_below_threshold(self):
        rows = generate_aggregated()
        scale_rows = [r for r in rows if r['ad_id'].startswith('scale_')]
        assert all(r['spend'] < 50 for r in scale_rows)

    def test_deterministic_output(self):
        """Два вызова дают одинаковый результат."""
        rows1 = generate_aggregated()
        rows2 = generate_aggregated()
        assert rows1 == rows2


class TestGenerateRawEvents:
    def test_generates_correct_count(self):
        events = generate_raw_events(10_000)
        assert len(events) == 10_000

    def test_all_required_fields(self):
        events = generate_raw_events(100)
        required = {'tenant_id', 'ad_id', 'date', 'spend', 'impressions', 'clicks', 'conversions', 'revenue', 'source'}
        assert all(required.issubset(e.keys()) for e in events)
