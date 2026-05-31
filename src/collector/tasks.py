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
from src.collector.facebook import (
    get_spend_data,
    check_account_status,
    FBAuthError,
    FBUnavailableError,
)
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
    """Hourly: загрузить FB spend из Marketing API, записать в ClickHouse.

    Читает учётные данные из ad_accounts (fb_act_id + зашифрованный токен).
    Если аккаунты не настроены — фолбэк на sample CSV (dev-режим).
    """
    async def get_fb_accounts():
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_id}
            )
            result = await session.execute(
                text(
                    "SELECT fb_act_id, encrypted_token, token_iv, token_tag "
                    "FROM ad_accounts WHERE tenant_id = :tid AND is_active = true"
                ),
                {"tid": uuid.UUID(tenant_id)},
            )
            return result.fetchall()

    try:
        today = date.today()
        yesterday = today - timedelta(days=1)

        accounts = asyncio.run(get_fb_accounts())

        if not accounts:
            # Нет настроенных аккаунтов → CSV fallback (dev)
            logger.warning("collect_fb_task: no FB accounts configured for tenant %s, using CSV", tenant_id)
            rows = get_spend_data(account_id="", date_from=yesterday, date_to=today, tenant_id=tenant_id)
        else:
            rows = []
            for fb_act_id, enc_token, iv, tag in accounts:
                try:
                    access_token = decrypt(bytes(enc_token), bytes(iv), bytes(tag))

                    # ── проверка здоровья аккаунта ─────────────────────────
                    fb_status = check_account_status(fb_act_id, access_token)
                    asyncio.run(_update_account_status_db(tenant_id, fb_act_id, fb_status))

                    if not fb_status.is_healthy:
                        logger.warning(
                            "collect_fb_task: account %s is %s (%s), skipping insights",
                            fb_act_id, fb_status.status_label, fb_status.disable_reason_label,
                        )
                        continue

                    # ── сбор расходов ──────────────────────────────────────
                    account_rows = get_spend_data(
                        account_id=fb_act_id,
                        date_from=yesterday,
                        date_to=today,
                        tenant_id=tenant_id,
                        access_token=access_token,
                    )
                    rows.extend(account_rows)

                except FBAuthError:
                    logger.error("collect_fb_task: FB token invalid for account %s, skipping", fb_act_id)
                except FBUnavailableError as exc:
                    logger.warning("collect_fb_task: FB unavailable for %s: %s", fb_act_id, exc)
                    raise self.retry(exc=exc)

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

        # Блок здоровья аккаунтов — читаем последний известный статус из БД
        account_rows = asyncio.run(_get_all_accounts_status(tenant_id))
        health_block = _format_account_health(account_rows)

        report = _format_report(roi_list, burnout_signals, scale_candidates, health_block)

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


async def _update_account_status_db(tenant_id: str, fb_act_id: str, status) -> None:
    """Сохраняет результат check_account_status в ad_accounts."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_id}
            )
            await session.execute(
                text(
                    "UPDATE ad_accounts SET "
                    "last_status_code = :code, "
                    "last_disable_reason = :reason, "
                    "status_checked_at = NOW(), "
                    "is_active = :is_active "
                    "WHERE tenant_id = :tid AND fb_act_id = :fb_act_id"
                ),
                {
                    "code": status.status_code,
                    "reason": status.disable_reason_code,
                    "is_active": status.is_healthy,
                    "tid": uuid.UUID(tenant_id),
                    "fb_act_id": fb_act_id,
                },
            )


async def _get_all_accounts_status(tenant_id: str) -> list:
    """Читает все FB-аккаунты тенанта с последним известным статусом."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SET LOCAL app.current_tenant_id = :tid"), {"tid": tenant_id}
        )
        result = await session.execute(
            text(
                "SELECT fb_act_id, is_active, last_status_code, last_disable_reason "
                "FROM ad_accounts WHERE tenant_id = :tid "
                "ORDER BY is_active DESC, fb_act_id"
            ),
            {"tid": uuid.UUID(tenant_id)},
        )
        return result.fetchall()


def _format_account_health(accounts: list) -> str:
    """Форматирует блок «Статус аккаунтов» для утреннего отчёта."""
    if not accounts:
        return ""

    _PROBLEM_LABELS = {
        2: ("❌", "Заблокирован"),
        3: ("⚠️", "Ошибка биллинга"),
        7: ("⚠️", "На проверке (Risk Review)"),
        101: ("❌", "Закрыт"),
    }

    problems = []
    active_count = 0

    for fb_act_id, is_active, status_code, disable_reason in accounts:
        if status_code is None:
            # Статус ещё не проверялся (аккаунт только добавлен)
            if is_active:
                active_count += 1
        elif status_code == 1:
            active_count += 1
        elif status_code in _PROBLEM_LABELS:
            icon, label = _PROBLEM_LABELS[status_code]
            problems.append(f"• {icon} {label}: <code>{fb_act_id}</code>")
        else:
            problems.append(f"• ⚠️ Статус {status_code}: <code>{fb_act_id}</code>")

    if not problems and active_count == 0:
        return ""

    lines = ["🏥 <b>Статус аккаунтов:</b>"]
    lines.extend(problems)
    if active_count:
        lines.append(f"• ✅ Активны: {active_count} аккаунт(ов).")
    return "\n".join(lines)


def _format_report(roi_list, burnout_signals, scale_candidates, health_block: str = "") -> str:
    from datetime import date as dt_date
    today_str = dt_date.today().strftime("%d.%m.%Y")

    losers = sorted([r for r in roi_list if r.roi_pct < -5.0], key=lambda r: r.roi_pct)

    lines = [f"<b>📊 Отчёт за {today_str}</b>", ""]

    if health_block:
        lines.append(health_block)
        lines.append("")

    if losers:
        lines.append("🔴 <b>Минус:</b>")
        for r in losers[:5]:
            lines.append(
                f"• <code>{r.ad_id}</code> — расход ${r.spend:.0f}, "
                f"доход ${r.revenue:.0f}, ROI {r.roi_pct:.0f}%"
            )
    else:
        lines.append("🔴 Убыточных нет — всё в плюсе.")
    lines.append("")

    if burnout_signals:
        lines.append("🟡 <b>Выгорание:</b>")
        for s in burnout_signals[:5]:
            lines.append(
                f"• <code>{s.ad_id}</code> — CTR упал на {s.ctr_drop_pct:.0f}% "
                f"({s.avg_baseline_ctr * 100:.2f}% → {s.avg_recent_ctr * 100:.2f}%)"
            )
    else:
        lines.append("🟡 Выгорания нет.")
    lines.append("")

    if scale_candidates:
        lines.append("🟢 <b>Можно увеличить бюджет:</b>")
        for c in scale_candidates[:5]:
            lines.append(
                f"• <code>{c.ad_id}</code> — ROI {c.roi_pct:.0f}%, "
                f"лидов {c.conversions}, расход ${c.daily_spend:.0f}/день"
            )
    else:
        lines.append("🟢 Кандидатов для масштаба нет.")

    lines.append(f"\n<i>Объявлений в анализе: {len(roi_list)}</i>")
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
            f"🚨 <b>Деньги идут, клики не приходят</b>\n"
            f"Объявление: <code>{alarm.ad_id}</code>\n"
            f"{alarm.message}\n"
            f"→ Проверь ссылку в объявлении и статус Keitaro."
        )
    if alarm.alarm_type == "BUDGET_DRAIN":
        return (
            f"🚨 <b>Расход вырос в разы</b>\n"
            f"{alarm.message}\n"
            f"→ Проверь лимиты бюджетов в Facebook Ads Manager."
        )
    if alarm.alarm_type == "ZERO_LEADS":
        return (
            f"🚨 <b>Клики есть, лидов нет</b>\n"
            f"Объявление: <code>{alarm.ad_id}</code>\n"
            f"{alarm.message}\n"
            f"→ Проверь лендинг и форму заявки."
        )
    return f"🚨 <b>FIRE ALARM [{alarm.alarm_type}]</b>\n{alarm.message}"
