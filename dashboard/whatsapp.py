"""Отправка сообщений в WhatsApp.

Режимы:
  * WAHA (github.com/devlikeapro/waha) — бесплатная программа на своём сервере.
    К ней по QR-коду привязывается WhatsApp центра, и CRM сама отправляет сообщения
    в личку и в групповые чаты. Адрес и API-ключ вводятся на странице «Рассылки»,
    QR-код показывается там же. Как поставить WAHA — docs/whatsapp-waha.md.
  * Green API (green-api.com) — то же самое, но платный сервис: apiUrl, idInstance, apiTokenInstance.
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


class WhatsAppNotFound(WhatsAppError):
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


class WAHA:
    """Клиент WAHA: {адрес}/api/..., ключ в заголовке X-Api-Key. В бесплатной версии одна сессия — default."""

    SESSION = 'default'

    def __init__(self, settings):
        self.api_url = (settings.api_url or 'http://localhost:3000').rstrip('/')
        self.token = settings.token.strip()

    def _call(self, path, payload=None, method=None):
        url = f'{self.api_url}/api/{path}'
        data = None
        headers = {'Accept': 'application/json', 'X-Api-Key': self.token}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        method = method or ('POST' if payload is not None else 'GET')
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode('utf-8') or 'null'
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise WhatsAppError('Неверный API-ключ WAHA')
            if e.code == 404:
                raise WhatsAppNotFound('WAHA: не найдено')
            if e.code == 422:
                raise WhatsAppError('WhatsApp не подключён в WAHA — отсканируйте QR-код на странице «Рассылки»')
            raise WhatsAppError(f'WAHA ответил ошибкой {e.code}')
        except (urllib.error.URLError, TimeoutError, OSError):
            raise WhatsAppError('Нет связи с WAHA — проверьте, что он запущен и адрес верный')
        try:
            return json.loads(body)
        except ValueError:
            raise WhatsAppError('Непонятный ответ WAHA')

    def state(self):
        """WORKING — подключён; SCAN_QR_CODE — ждёт QR; STARTING, STOPPED, FAILED; missing — сессии ещё нет"""
        try:
            data = self._call(f'sessions/{self.SESSION}')
        except WhatsAppNotFound:
            return 'missing'
        return (data or {}).get('status', 'unknown')

    def start(self):
        """Создать и запустить сессию (если уже есть — просто запустить)"""
        state = self.state()
        if state == 'missing':
            self._call('sessions', {'name': self.SESSION, 'start': True})
        elif state in ('STOPPED', 'FAILED'):
            self._call(f'sessions/{self.SESSION}/restart' if state == 'FAILED' else f'sessions/{self.SESSION}/start', {})

    def qr(self):
        """QR-код для привязки как data:-ссылка на картинку"""
        data = self._call(f'{self.SESSION}/auth/qr?format=image') or {}
        if not data.get('data'):
            raise WhatsAppError('WAHA не отдал QR-код')
        return f'data:{data.get("mimetype") or "image/png"};base64,{data["data"]}'

    def send(self, chat_id, text):
        data = self._call('sendText', {'session': self.SESSION, 'chatId': chat_id, 'text': text})
        message_id = (data or {}).get('id') if isinstance(data, dict) else None
        if isinstance(message_id, dict):
            message_id = message_id.get('_serialized') or message_id.get('id')
        if not message_id:
            raise WhatsAppError('WAHA не принял сообщение')
        return message_id

    def groups(self):
        """Групповые чаты аккаунта: [(id, название)]. Формат ответа зависит от движка WAHA — разбираем все."""
        data = self._call(f'{self.SESSION}/groups') or []
        items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
        found = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            chat_id = item.get('id') or item.get('JID') or ''
            if isinstance(chat_id, dict):
                chat_id = chat_id.get('_serialized', '')
            if isinstance(chat_id, str) and chat_id.endswith('@g.us'):
                found[chat_id] = item.get('name') or item.get('subject') or item.get('Name') or chat_id
        return sorted(found.items(), key=lambda x: x[1].lower())


def client(settings):
    """Клиент выбранного способа подключения"""
    return WAHA(settings) if settings.provider == 'waha' else GreenAPI(settings)


WAHA_LABELS = {
    'WORKING': 'WhatsApp подключён',
    'SCAN_QR_CODE': 'Отсканируйте QR-код телефоном с WhatsApp центра',
    'STARTING': 'WAHA запускается — подождите минуту',
    'STOPPED': 'WhatsApp в WAHA остановлен — откройте подключение, он запустится',
    'FAILED': 'WhatsApp в WAHA отключился — отсканируйте QR-код заново',
    'missing': 'Отсканируйте QR-код телефоном с WhatsApp центра',
}

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
    cached = None if fresh else cache.get(_state_key(settings))
    if cached:
        return tuple(cached)
    try:
        state = client(settings).state()
    except WhatsAppError as e:
        return 'manual', str(e)
    return _remember(settings, state)


def _state_key(settings):
    return f'wa-state:{settings.provider}:{settings.api_url}:{settings.instance_id}:{settings.token[-6:]}'


def _remember(settings, state):
    """Состояние → (режим, подпись), и запомнить на минуту"""
    if settings.provider == 'waha':
        result = ('api', WAHA_LABELS['WORKING']) if state == 'WORKING' else ('manual', WAHA_LABELS.get(state, f'WAHA: {state}'))
    else:
        result = ('api', STATE_LABELS['authorized']) if state == 'authorized' else ('manual', STATE_LABELS.get(state, f'Green API: {state}'))
    cache.set(_state_key(settings), list(result), 60)
    return result


def waha_status(settings):
    """Для экрана подключения WAHA: (состояние, QR-код или ''). Сессию при необходимости запускаем сами."""
    waha = WAHA(settings)
    state = waha.state()
    if state in ('missing', 'STOPPED', 'FAILED'):
        waha.start()
        state = 'STARTING'
    qr = waha.qr() if state == 'SCAN_QR_CODE' else ''
    _remember(settings, state)
    return state, qr
