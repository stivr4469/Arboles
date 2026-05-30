from celery import Celery
from celery.schedules import crontab
from src.core.config import settings

celery_app = Celery(
    "adpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["src.collector.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.tenant_timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

celery_app.conf.beat_schedule = {
    "morning-report-daily-0830": {
        "task": "src.collector.tasks.send_morning_report_task",
        "schedule": crontab(hour=8, minute=30),
    },
    "collect-fb-hourly": {
        "task": "src.collector.tasks.collect_fb_task",
        "schedule": crontab(minute=0),
    },
    "collect-keitaro-hourly": {
        "task": "src.collector.tasks.collect_keitaro_task",
        "schedule": crontab(minute=0),
    },
    "fire-alarm-check-every-30min": {
        "task": "src.collector.tasks.fire_alarm_check_task",
        "schedule": crontab(minute="*/30"),
    },
}
