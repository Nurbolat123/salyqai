"""Фоновые задачи (Celery + Redis, ТЗ 3): напоминания и курсы НБ РК раз в день.

Запуск: celery -A salyq.worker worker -B --loglevel=info
"""

from datetime import date

from celery import Celery
from celery.schedules import crontab
from sqlalchemy.orm import Session

from salyq.db import get_engine
from salyq.settings import get_settings

app = Celery("salyq", broker=get_settings().redis_url)
app.conf.timezone = "Asia/Almaty"
app.conf.beat_schedule = {
    "fx-daily": {"task": "salyq.worker.fetch_fx", "schedule": crontab(hour=9, minute=5)},
    "reminders-daily": {"task": "salyq.worker.reminders", "schedule": crontab(hour=10, minute=0)},
}


@app.task
def fetch_fx() -> int:
    from salyq.fx import fetch_and_store

    with Session(get_engine()) as session:
        return fetch_and_store(session, date.today())


@app.task
def reminders() -> dict[str, int]:
    from salyq.reminders.notifiers import build_notifiers
    from salyq.reminders.service import run

    with Session(get_engine()) as session:
        return run(session, date.today(), build_notifiers(get_settings()))
