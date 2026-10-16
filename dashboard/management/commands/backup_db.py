"""Резервная копия базы: backups/db-ГГГГММДД-ЧЧММ.sqlite3, хранятся последние 30.

Копия делается средствами SQLite (backup API), поэтому она целая, даже если в этот момент
кто-то работает в CRM. Запускать раз в сутки по расписанию (cron) — см. docs/server.md:

    python manage.py backup_db
    python manage.py backup_db --keep 60
"""
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

BACKUP_DIR = Path(settings.BASE_DIR) / 'backups'


def make_backup(target):
    """Целая копия текущей базы в файл target.
    Через подключение самого Django: оно не ждёт своих же блокировок, а копия всё равно целая."""
    if connection.vendor != 'sqlite':
        raise CommandError('Копия делается только для SQLite')
    target.parent.mkdir(parents=True, exist_ok=True)
    connection.ensure_connection()
    copy = sqlite3.connect(str(target))
    try:
        connection.connection.backup(copy)
    finally:
        copy.close()
    return target


class Command(BaseCommand):
    help = 'Сделать резервную копию базы в папку backups/'

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=30, help='Сколько последних копий хранить')

    def handle(self, *args, keep=30, **options):
        name = f'db-{timezone.localtime():%Y%m%d-%H%M}.sqlite3'
        target = make_backup(BACKUP_DIR / name)
        old = sorted(BACKUP_DIR.glob('db-*.sqlite3'))[:-max(keep, 1)]
        for path in old:
            path.unlink()
        size = target.stat().st_size / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(f'Копия базы: {target} ({size:.1f} МБ), удалено старых: {len(old)}'))
