"""Отправка сообщений в WhatsApp.

Два режима:
  * Green API (green-api.com) — к сервису привязывается WhatsApp центра по QR-коду,
    и CRM сама отправляет сообщения в личку и в групповые чаты.
    Данные подключения (apiUrl, idInstance, apiTokenInstance) вводятся на странице «Рассылки».
  * Вручную — без сервиса: для каждого получателя ссылка wa.me с готовым текстом,
    одно нажатие открывает WhatsApp, остаётся нажать «Отправить».
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import cache

TIMEOUT = 15


class WhatsAppError(Exception):
    pass


def normalize_phone(raw):
    """«+7 (700) 123-45-67», «8 700 123 45 67», «7001234567» → «77001234567»; не номер — None"""
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 11 and digits[0] == '8':
        digits = '7' + digits[1:]
    elif len(digits) == 10 and digits[0] == '7':
        digits = '7' + digits
    if 11 <= len(digits) <= 15:
        return digits
    return None


def chat_id_for_phone(phone):
    return f'{phone}@c.us'


def wa_link(phone, text):
    """Ссылка, которая открывает чат с номером и готовым текстом"""
    return f'https://wa.me/{phone}?text={urllib.parse.quote(text)}'


def format_phone(phone):
    """77001234567 → +7 700 123 45 67"""
    if len(phone or '') == 11 and phone.startswith('7'):
        return f'+7 {phone[1:4]} {phone[4:7]} {phone[7:9]} {phone[9:]}'
    return f'+{phone}' if phone else ''


class GreenAPI:
    """Клиент Green API: {apiUrl}/waInstance{idInstance}/{метод}/{apiTokenInstance}"""

    def __init__(self, settings):
        self.api_url = (settings.api_url or 'https://api.green-api.com').rstrip('/')
        self.instance_id = settings.instance_id.strip()
        self.token = settings.token.strip()

    def _call(self, method, payload=None):
        url = f'{self.api_url}/waInstance{self.instance_id}/{method}/{self.token}'
        data = None
        headers = {'Accept': 'application/json'}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method='POST' if payload is not None else 'GET')
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode('utf-8') or 'null'
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise WhatsAppError('Неверный idInstance или apiTokenInstance')
            if e.code == 466:
                raise WhatsAppError('Лимит бесплатного тарифа Green API — только 3 чата. Нужен тариф Business')
            if e.code == 429:
                raise WhatsAppError('Слишком часто — Green API просит подождать')
            raise WhatsAppError(f'Green API ответил ошибкой {e.code}')
        except (urllib.error.URLError, TimeoutError, OSError):
            raise WhatsAppError('Нет связи с Green API')
        try:
            return json.loads(body)
        except ValueError:
            raise WhatsAppError('Непонятный ответ Green API')

    def state(self):
        """authorized — WhatsApp подключён; notAuthorized — нужно отсканировать QR; и т.п."""
        data = self._call('getStateInstance')
        return (data or {}).get('stateInstance', 'unknown')

    def send(self, chat_id, text):
        data = self._call('sendMessage', {'chatId': chat_id, 'message': text})
        message_id = (data or {}).get('idMessage')
        if not message_id:
            raise WhatsAppError('Green API не принял сообщение')
        return message_id

    def groups(self):
        """Групповые чаты аккаунта: [(id, название)]"""
        data = self._call('getContacts') or []
        found = {}
        for item in data if isinstance(data, list) else []:
            chat_id = item.get('id', '')
            if item.get('type') == 'group' or chat_id.endswith('@g.us'):
                found[chat_id] = item.get('name') or item.get('contactName') or chat_id
        return sorted(found.items(), key=lambda x: x[1].lower())


STATE_LABELS = {
    'authorized': 'WhatsApp подключён',
    'notAuthorized': 'Нужно отсканировать QR-код в кабинете Green API',
    'blocked': 'WhatsApp заблокировал номер',
    'suspended': 'Отправка приостановлена WhatsApp (похоже на спам)',
    'yellowCard': 'Отправка приостановлена WhatsApp (похоже на спам)',
    'sleepMode': 'Телефон не в сети',
    'starting': 'Green API запускается — подождите минуту',
}


def connection_status(settings, fresh=False):
    """(режим, подпись): режим 'api' — можно отправлять автоматически, 'manual' — только по ссылкам.
    Ответ Green API запоминается на минуту, чтобы не спрашивать его при каждом открытии страницы."""
    if not settings.is_configured:
        return 'manual', 'Ручной режим: WhatsApp не подключён'
    key = f'wa-state:{settings.api_url}:{settings.instance_id}:{settings.token[-6:]}'
    cached = None if fresh else cache.get(key)
    if cached:
        return tuple(cached)
    try:
        state = GreenAPI(settings).state()
    except WhatsAppError as e:
        return 'manual', str(e)
    result = ('api', STATE_LABELS['authorized']) if state == 'authorized' else ('manual', STATE_LABELS.get(state, f'Green API: {state}'))
    cache.set(key, list(result), 60)
    return result
