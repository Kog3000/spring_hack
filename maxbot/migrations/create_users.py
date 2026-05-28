"""Создаёт учётки организатора и технического администратора.

Usage:
    python -m migrations.create_users \
        --org-email org@mirea.ru --org-password pass1 --org-name "Иванова И.И." \
        --admin-email admin@mirea.ru --admin-password pass2 --admin-name "Техадмин"

Или без аргументов — создаст демо-учётки (см. defaults).
"""
import argparse

from app.db import session_scope, init_db
from app.logging_setup import get_logger, setup_logging
from app.repositories.repos import AdminRepo, OrganizerRepo
from app.services.passwords import hash_password


def main():
    setup_logging()
    log = get_logger(__name__)

    parser = argparse.ArgumentParser()
    parser.add_argument("--org-email", default="organizer@mirea.ru")
    parser.add_argument("--org-password", default="organizer123")
    parser.add_argument("--org-name", default="Организатор приёмной комиссии")
    parser.add_argument("--admin-email", default="admin@mirea.ru")
    parser.add_argument("--admin-password", default="admin123")
    parser.add_argument("--admin-name", default="Технический администратор")
    args = parser.parse_args()

    # На случай первого запуска без init_db
    init_db()

    with session_scope() as s:
        org = OrganizerRepo.get_by_email(s, args.org_email)
        if org is None:
            OrganizerRepo.create(
                s,
                email=args.org_email,
                password_hash=hash_password(args.org_password),
                full_name=args.org_name,
            )
            log.info("Создан организатор: %s", args.org_email)
        else:
            log.info("Организатор уже существует: %s", args.org_email)

        adm = AdminRepo.get_by_email(s, args.admin_email)
        if adm is None:
            AdminRepo.create(
                s,
                email=args.admin_email,
                password_hash=hash_password(args.admin_password),
                display_name=args.admin_name,
            )
            log.info("Создан техадмин: %s", args.admin_email)
        else:
            log.info("Техадмин уже существует: %s", args.admin_email)


if __name__ == "__main__":
    main()
