"""Подключить CRM к WAHA (бесплатная рассылка в WhatsApp) из командной строки.

Обычно вызывается сама из deploy/waha/install.sh. Вручную:
    WAHA_API_KEY=ключ python manage.py setup_whatsapp --url http://127.0.0.1:3000
    python manage.py setup_whatsapp --status      — подключён ли WhatsApp
Ключ лучше передавать переменной WAHA_API_KEY, а не --key: так он не виден в списке процессов.
"""
import os

from django.core.management.base import BaseCommand, CommandError

from dashboard.models import WhatsAppSettings
from dashboard.whatsapp import WhatsAppError, waha_status


class Command(BaseCommand):
    help = 'Подключить CRM к WAHA: адрес и API-ключ (ключ — из переменной WAHA_API_KEY или --key)'

    def add_arguments(self, parser):
        parser.add_argument('--url', default='http://127.0.0.1:3000', help='адрес WAHA, по умолчанию http://127.0.0.1:3000')
        parser.add_argument('--key', help='API-ключ WAHA (по умолчанию — из переменной окружения WAHA_API_KEY)')
        parser.add_argument('--status', action='store_true', help='ничего не менять, только показать состояние')

    def handle(self, *args, **options):
        settings = WhatsAppSettings.load()
        if not options['status']:
            key = (options['key'] or os.environ.get('WAHA_API_KEY', '')).strip()
            if not key:
                raise CommandError('Нужен API-ключ WAHA: переменная WAHA_API_KEY или --key')
            settings.provider = 'waha'
            settings.api_url = options['url'].strip().rstrip('/')[:200]
            settings.instance_id = ''
            settings.token = key[:200]
            settings.save()
            self.stdout.write(f'  CRM запомнила адрес WAHA ({settings.api_url}) и ключ.')
        elif settings.provider != 'waha' or not settings.is_configured:
            raise CommandError('WAHA в CRM не настроена. Запустите: sudo bash deploy/waha/install.sh')

        try:
            # Заодно запускает WhatsApp в WAHA, если он ещё не запущен — тогда в CRM появится QR-код
            state, _ = waha_status(settings)
        except WhatsAppError as e:
            raise CommandError(f'CRM не может достучаться до WAHA: {e}')
        if state == 'WORKING':
            self.stdout.write(self.style.SUCCESS('  ✔ WhatsApp подключён — рассылки уходят автоматически.'))
        else:
            self.stdout.write(self.style.WARNING(
                '  ✔ WAHA на связи. Осталось отсканировать QR-код: CRM → «Рассылки» → кнопка вверху справа.'))
