import asyncio
import logging
import uuid
from datetime import date, timedelta
from sqlalchemy import text

import httpx
import pandas as pd

from src.core.celery_app import celery_app
from src.core.config import settings
from src.core.clickhouse import insert_rows, query as ch_query
from src.core.encryption import decrypt
from src.core.database import AsyncSessionLocal
from src.collector.facebook import get_spend_data
from src.collector.keitaro import KeitaroClient, KeitaroAuthError, KeitaroUnavailableError
from src.analytics.pattern_engine import PatternEngine, FireAlarm
from src.data_gen.synthetic import generate_aggregated

logger = logging.getLogger(__name__)

CH_TABLE = "adpilot.ad_performance_merged"
CH_COLUMNS = ["tenant_id", "ad_id", "date", "spend", "impressions",
               "clicks", "conversions", "revenue", "source"]

TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _telegram_send(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    resp = httpx.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=900)
def collect_fb_task(self, tenant_id: str = TENANT_ID) -> int:
    """Hourly: загрузить FB spend за последний час, записать в ClickHouse."""
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)
        rows = get_spend_data(
            account_id="",
            date_from=yesterday,
            date_to=today,
            tenant_id=tenant_id,
        )
        if rows:
            inserted = insert_rows(CH_TABLE, rows, CH_COLUMNS)
            logger.info("collect_fb_task: inserted %d rows", inserted)
            return inserted
        return 0
    except Exception as exc:
        logger.exception("collect_fb_task failed: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=900)
def collect_keitaro_task(self, tenant_id: str = TENANT_ID) -> int:
    """Hourly: безопасно забрать клики из Keitaro API используя расшифрованные учетные данные."""

    async def get_credentials():
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                text(
                    "SELECT base_url, encrypted_api_key, api_key_iv, api_key_tag, ad_id_param "
                    "FROM keitaro_configs WHERE tenant_id = :tid AND is_active = true"
                ),
                {"tid": uuid.UUID(tenant_id)},
            )
            return result.fetchone()

    try:
        config = asyncio.run(get_credentials())

        if not config:
            logger.error("No active Keitaro config found for tenant %s", tenant_id)
            return 0

        base_url, encrypted_key, iv, tag, ad_id_param = config
        decrypted_api_key = decrypt(bytes(encrypted_key), bytes(iv), bytes(tag))

        client = KeitaroClient(base_url=base_url, api_key=decrypted_api_key, ad_id_param=ad_id_param)
        today = date.today()
        yesterday = today - timedelta(days=1)
        keitaro_rows = client.get_clicks_report(date_from=yesterday, date_to=today)

        ch_rows = [
            {
                "tenant_id": tenant_id,
                "ad_id": r["ad_id"],
                "date": r["date"],
                "spend": 0.0,
                "impressions": 0,
                "clicks": r["clicks"],
                "conversions": r["conversions"],
                "revenue": r["revenue"],
                "source": "keitaro",
            }
            for r in keitaro_rows
        ]

        if ch_rows:
            inserted = insert_rows(CH_TABLE, ch_rows, CH_COLUMNS)
            logger.info("collect_keitaro_task: inserted %d rows", inserted)
            return inserted
        return 0

    except KeitaroAuthError:
        logger.error("Keitaro auth error for tenant %s — credentials invalid", tenant_id)
        return 0
    except Exception as exc:
        logger.exception("collect_keitaro_task failed: %s", exc)
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=900)
def send_morning_report_task(self, tenant_id: str = TENANT_ID) -> None:
    """Ежедневный аудит: извлечение данных -> выявление аномалий -> отправка Copilot-отчета."""
    try:
        rows = generate_aggregated()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date

        insert_rows(CH_TABLE, rows, CH_COLUMNS)

        roi_list = PatternEngine.compute_cohort_roi(df)
        burnout_signals = PatternEngine.detect_burnout(df)
        scale_candidates = PatternEngine.detect_scale_candidates(df)

        report = _format_report(roi_list, burnout_signals, scale_candidates)

        chat_id = settings.telegram_admin_chat_id
        if chat_id:
            _telegram_send(chat_id, report)
            logger.info("Morning report sent to chat_id=%s", chat_id)
        else:
            logger.warning("TELEGRAM_ADMIN_CHAT_ID not set")

    except KeitaroUnavailableError:
        logger.error("send_morning_report_task: Keitaro unavailable for tenant %s", tenant_id)
        chat_id = settings.telegram_admin_chat_id
        if chat_id:
            _telegram_send(
                chat_id,
                "⚠️ <b>Критическое уведомление</b>\n\n"
                "Доброе утро. Сегодня аналитический отчёт не сформирован — "
                "ваш сервер Keitaro недоступен (Connection Timeout).\n\n"
                "<i>Проверьте хостинг или перезагрузите трекер, "
                "чтобы не терять клики.</i>",
            )
    except Exception as exc:
        logger.exception("send_morning_report_task failed: %s", exc)
        raise self.retry(exc=exc)


def _format_report(roi_list, burnout_signals, scale_candidates) -> str:
    """Форматирует недирективный Pain-first отчёт."""
    from datetime import date as dt_date
    today_str = dt_date.today().strftime("%d.%m.%Y")

    losers = [r for r in roi_list if r.roi_pct < -5.0]
    losers.sort(key=lambda r: r.roi_pct)

    lines = [
        "<b>📊 Утренний аудит Ad-Pilot</b>",
        f"<i>{today_str} · 08:30 UTC</i>",
        "",
    ]

    if losers:
        lines.append("<b>🔴 Слив бюджета (Упущенная выгода)</b>")
        for r in losers[:5]:
            lines.append(
                f"• Объявление: <code>{r.ad_id}</code>\n"
                f"  <b>Наблюдение:</b> Расход: ${r.spend:.0f} | Доход: ${r.revenue:.0f} | ROI: <b>{r.roi_pct:.1f}%</b>\n"
                f"  <b>Гипотеза:</b> Связка теряет эффективность или аудитория выгорела.\n"
                f"  <b>Рекомендация:</b> Проверить показатели в кабинете или временно снизить ставку."
            )
    else:
        lines.append("<b>🔴 Потерь не обнаружено</b> — все активные связки работают в плюс.")
    lines.append("")

    if burnout_signals:
        lines.append("<b>🟡 Анализ выгорания CTR</b>")
        for s in burnout_signals[:5]:
            lines.append(
                f"• Объявление: <code>{s.ad_id}</code>\n"
                f"  <b>Наблюдение:</b> CTR снизился на <b>{s.ctr_drop_pct:.0f}%</b> "
                f"(с {s.avg_baseline_ctr * 100:.2f}% до {s.avg_recent_ctr * 100:.2f}%).\n"
                f"  <b>Гипотеза:</b> Снижение вовлеченности из-за усталости от креатива.\n"
                f"  <b>Рекомендация:</b> Рассмотреть уникализацию или замену креатива."
            )
    else:
        lines.append("<b>🟡 Сигналов о выгорании CTR не обнаружено.</b>")
    lines.append("")

    if scale_candidates:
        lines.append("<b>🟢 Кандидаты на масштабирование (Эксперименты)</b>")
        for c in scale_candidates[:5]:
            lines.append(
                f"• Объявление: <code>{c.ad_id}</code>\n"
                f"  <b>Наблюдение:</b> ROI на <b>+{c.relative_roi_pct:.1f}%</b> выше среднего по кабинету | "
                f"ROI: {c.roi_pct:.0f}% | Конверсий: {c.conversions}\n"
                f"  <b>Рекомендация:</b> Рассмотреть аккуратное увеличение дневного бюджета на 15-20% в качестве теста."
            )
    else:
        lines.append("<b>🟢 Стабильных кандидатов на масштабирование не найдено.</b>")

    lines.append("")
    lines.append(f"<b>Всего активных объявлений в анализе:</b> {len(roi_list)}")
    lines.append("<i>Все рекомендации носят характер рабочих гипотез. Принимайте решения взвешенно.</i>")

    return "\n".join(lines)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def fire_alarm_check_task(self, tenant_id: str = TENANT_ID) -> int:
    """Каждые 30 мин: сканирует ClickHouse на критические аномалии, шлёт Fire Alarm в Telegram."""
    try:
        today = date.today()
        week_ago = today - timedelta(days=7)
        yesterday = today - timedelta(days=1)

        today_rows = ch_query(
            "SELECT ad_id, sum(spend) AS spend, sum(clicks) AS clicks, "
            "       sum(impressions) AS impressions, sum(conversions) AS conversions, "
            "       sum(revenue) AS revenue "
            "FROM adpilot.ad_performance_merged "
            "WHERE tenant_id = %(tenant_id)s AND date = %(today)s "
            "GROUP BY ad_id",
            {"tenant_id": tenant_id, "today": str(today)},
        )

        if not today_rows:
            return 0

        baseline_rows = ch_query(
            "SELECT avg(daily_spend) AS avg_daily_spend FROM ("
            "  SELECT date, sum(spend) AS daily_spend "
            "  FROM adpilot.ad_performance_merged "
            "  WHERE tenant_id = %(tenant_id)s "
            "    AND date >= %(week_ago)s AND date < %(today)s "
            "  GROUP BY date"
            ")",
            {"tenant_id": tenant_id, "week_ago": str(week_ago), "today": str(today)},
        )
        baseline_avg = (
            float(baseline_rows[0]["avg_daily_spend"] or 0.0) if baseline_rows else 0.0
        )

        today_df = pd.DataFrame(today_rows)
        today_df["date"] = today

        alarms = PatternEngine.detect_fire_alarms(today_df, baseline_avg_spend=baseline_avg)

        if not alarms:
            return 0

        chat_id = settings.telegram_admin_chat_id
        if chat_id:
            for alarm in alarms:
                _telegram_send(chat_id, _format_fire_alarm(alarm))
                logger.warning(
                    "Fire Alarm dispatched: type=%s ad_id=%s tenant=%s",
                    alarm.alarm_type, alarm.ad_id, tenant_id,
                )

        return len(alarms)

    except Exception as exc:
        logger.exception("fire_alarm_check_task failed: %s", exc)
        raise self.retry(exc=exc)


def _format_fire_alarm(alarm: FireAlarm) -> str:
    if alarm.alarm_type == "UTM_BREAK":
        return (
            "🚨 <b>FIRE ALARM: Нет кликов при активных расходах</b>\n\n"
            f"• Объявление: <code>{alarm.ad_id}</code>\n"
            f"  <b>Наблюдение:</b> {alarm.message}\n"
            f"  <b>Гипотеза:</b> UTM-метки сломаны или трекер не получает трафик.\n"
            f"  <b>Рекомендация:</b> Немедленно проверить ссылки в объявлении "
            f"и статус Keitaro."
        )
    if alarm.alarm_type == "BUDGET_DRAIN":
        return (
            "🚨 <b>FIRE ALARM: Аномальный рост расходов</b>\n\n"
            f"  <b>Наблюдение:</b> {alarm.message}\n"
            f"  <b>Гипотеза:</b> Ставки вышли из-под контроля или "
            f"запустилось неконтролируемое масштабирование.\n"
            f"  <b>Рекомендация:</b> Проверить лимиты бюджетов в Facebook Ads Manager."
        )
    return f"🚨 <b>FIRE ALARM [{alarm.alarm_type}]</b>\n{alarm.message}"
