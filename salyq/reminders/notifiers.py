"""Каналы напоминаний (ТЗ 4.6). Текст напоминаний не содержит персональных данных:
Telegram и почтовые сервисы могут находиться за пределами РК."""

import json
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Protocol

from salyq.models import User
from salyq.settings import Settings


class NotifyError(RuntimeError):
    pass


class Notifier(Protocol):
    channel: str

    def address(self, user: User) -> str | None: ...
    def send(self, address: str, subject: str, text: str) -> None: ...


class MemoryNotifier:
    """Для разработки и тестов: складывает сообщения в список."""

    def __init__(self, channel: str = "email"):
        self.channel, self.sent = channel, []

    def address(self, user: User) -> str | None:
        return {"email": user.email, "telegram": user.telegram_chat_id, "push": user.push_token}[self.channel]

    def send(self, address: str, subject: str, text: str) -> None:
        self.sent.append((address, subject, text))


class EmailNotifier:
    channel = "email"

    def __init__(self, host: str, port: int, sender: str, user: str | None, password: str | None):
        self.host, self.port, self.sender, self.user, self.password = host, port, sender, user, password

    def address(self, user: User) -> str | None:
        return user.email

    def send(self, address: str, subject: str, text: str) -> None:  # pragma: no cover — сеть
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, address, subject
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)


class TelegramNotifier:
    channel = "telegram"

    def __init__(self, bot_token: str):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def address(self, user: User) -> str | None:
        return user.telegram_chat_id

    def send(self, address: str, subject: str, text: str) -> None:  # pragma: no cover — сеть
        body = json.dumps({"chat_id": address, "text": f"{subject}\n\n{text}"}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            if resp.status != 200:
                raise NotifyError(f"Telegram ответил {resp.status}")


def build_notifiers(settings: Settings) -> list[Notifier]:
    """Только настроенные каналы. Push (FCM/APNs) подключается с мобильным приложением."""
    out: list[Notifier] = []
    if settings.smtp_host:
        out.append(EmailNotifier(settings.smtp_host, settings.smtp_port, settings.smtp_sender,
                                 settings.smtp_user, settings.smtp_password))
    if settings.telegram_bot_token:
        out.append(TelegramNotifier(settings.telegram_bot_token))
    return out
