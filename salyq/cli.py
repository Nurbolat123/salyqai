"""Служебные команды: python -m salyq.cli <команда>."""

import argparse
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.crypto import blind_index
from salyq.db import get_engine
from salyq.models import User


def make_expert(session: Session, iin: str) -> str:
    user = session.scalar(select(User).where(User.iin_hash == blind_index(iin, "iin")))
    if user is None:
        return "пользователь не найден: он должен хотя бы раз войти по ЭЦП"
    user.role = "expert"
    audit.record(session, "user.role_change", actor_type="system", object_type="user", object_id=user.id, role="expert")
    session.commit()
    return f"пользователь {user.id} назначен экспертом"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="salyq")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make-expert", help="назначить пользователя экспертом")
    p.add_argument("--iin", required=True)
    p = sub.add_parser("fetch-fx", help="загрузить курсы НБ РК на дату")
    p.add_argument("--date", type=date.fromisoformat, default=date.today())
    p = sub.add_parser("reminders", help="запланировать и отправить напоминания")
    p.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    with Session(get_engine()) as session:
        if args.cmd == "make-expert":
            print(make_expert(session, args.iin))
        elif args.cmd == "fetch-fx":
            from salyq.fx import fetch_and_store

            print(f"сохранено курсов: {fetch_and_store(session, args.date)}")
        elif args.cmd == "reminders":
            from salyq.reminders.notifiers import build_notifiers
            from salyq.reminders.service import run
            from salyq.settings import get_settings

            print(run(session, args.date, build_notifiers(get_settings())))


if __name__ == "__main__":
    main()
