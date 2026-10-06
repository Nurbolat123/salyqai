"""Планирование и отправка напоминаний о сроках (ТЗ 4.6). Сроки — из конфигурации года."""

from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import consents
from salyq.auth.clock import utcnow
from salyq.models import Reminder, User
from salyq.reminders.notifiers import Notifier
from salyq.tax.config import ConfigNotFound
from salyq.tax.config_store import active_config
from salyq.tax.engine import period_deadlines

OFFSETS_DAYS = (7, 3, 1, 0)
TITLES = {
    "declaration_910": "Сдать форму 910.00",
    "tax_payment": "Оплатить налог по форме 910.00",
    "social_payments": "Оплатить соцплатежи за себя (ОПВ, ОПВР, СО, ВОСМС)",
}


def upcoming(session: Session, today: date) -> list[tuple[str, date, str]]:
    """(вид, срок, за что) на ближайшие 7+ дней."""
    out = []
    for year in (today.year - 1, today.year):
        for half in (1, 2):
            try:
                dl = period_deadlines(year, half, active_config(session, year))
            except ConfigNotFound:
                continue
            for kind, due in dl.items():
                if today <= due <= today + timedelta(days=max(OFFSETS_DAYS)):
                    out.append((kind, due, f"{half} полугодие {year}"))
    try:
        sp = active_config(session, today.year).config.deadlines.social_payments
        for back in (0, 1):  # срок за прошлый и текущий месяц
            idx = today.year * 12 + today.month - 1 - back + sp.months_after
            due = date(idx // 12, idx % 12 + 1, sp.day)
            month_idx = idx - sp.months_after
            if today <= due <= today + timedelta(days=max(OFFSETS_DAYS)):
                out.append(("social_payments", due, f"{month_idx % 12 + 1:02d}.{month_idx // 12}"))
    except ConfigNotFound:
        pass
    return out


def schedule(session: Session, today: date, notifiers: Sequence[Notifier]) -> int:
    """Создаёт строки напоминаний для всех пользователей с согласием и каналом связи."""
    deadlines = upcoming(session, today)
    created = 0
    for user in session.scalars(select(User)):
        if not consents.has_consent(session, user, "pd_processing"):
            continue
        for n in notifiers:
            if not n.address(user):
                continue
            for kind, due, _ in deadlines:
                for off in OFFSETS_DAYS:
                    remind_on = due - timedelta(days=off)
                    if remind_on != today:
                        continue
                    exists = session.scalar(select(Reminder.id).where(
                        Reminder.user_id == user.id, Reminder.kind == kind, Reminder.due_date == due,
                        Reminder.remind_on == remind_on, Reminder.channel == n.channel))
                    if exists is None:
                        session.add(Reminder(user_id=user.id, kind=kind, due_date=due,
                                             remind_on=remind_on, channel=n.channel))
                        created += 1
    session.commit()
    return created


def message(kind: str, due: date, today: date) -> tuple[str, str]:
    days = (due - today).days
    when = "сегодня последний день" if days == 0 else f"осталось {days} дн."
    return f"Salyq: {TITLES[kind]}", f"{TITLES[kind]} до {due:%d.%m.%Y} — {when}. Подробности в приложении Salyq."


def send_due(session: Session, today: date, notifiers: Sequence[Notifier]) -> int:
    by_channel = {n.channel: n for n in notifiers}
    sent = 0
    rows = session.scalars(select(Reminder).where(
        Reminder.sent_at.is_(None), Reminder.remind_on <= today, Reminder.due_date >= today))
    for r in rows:
        n = by_channel.get(r.channel)
        user = session.get(User, r.user_id)
        address = n.address(user) if n else None
        if n is None or not address:
            r.error = "канал не настроен"
            continue
        subject, text = message(r.kind, r.due_date, today)
        try:
            n.send(address, subject, text)
            r.sent_at, r.error = utcnow(), None
            sent += 1
        except Exception as exc:  # сеть/провайдер — повторим при следующем запуске
            r.error = str(exc)[:500]
    session.commit()
    return sent


def run(session: Session, today: date, notifiers: Sequence[Notifier]) -> dict[str, int]:
    return {"scheduled": schedule(session, today, notifiers), "sent": send_due(session, today, notifiers)}
