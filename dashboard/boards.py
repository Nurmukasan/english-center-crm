"""Интерактивные доски в кабинетах: показать на доске экран ноутбука, ссылку или файл прямо из CRM.

Как устроено:
  * На каждой доске (Windows + Chrome) открыта страница /board/<token>/ — «экран доски». Она раз в пару
    секунд спрашивает сервер, нет ли для неё новостей, и показывает заставку, экран учителя, ссылку или файл.
  * Во вкладке «Доски» учитель видит кабинеты: свободна, занята (кем) или выключена. «Подключиться»
    закрепляет доску за ним — другие подключиться не могут, пока он не отключится (или полторы минуты нет
    связи с его ноутбуком — закрыл крышку; админ и менеджер могут отключить кого угодно).
  * Экран идёт напрямую с ноутбука на доску по Wi-Fi центра (WebRTC, шифруется), сервер только передаёт
    приглашение и ответ (offer/answer). Ссылки и файлы доска открывает сама.

Ссылки на доски — секретные (token): кто знает ссылку, видит то, что показывают на доске, но управлять
ей не может. Ссылку можно заменить на новую в настройках доски.
"""
import json
import os
import re
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Board, BoardFile, BoardMessage
from .views import MANAGE_ROLES, crm_view, get_user_role, to_int

BOARD_ROLES = ('teacher', 'admin', 'developer', 'manager')
HOLD_TIMEOUT = timedelta(seconds=90)      # нет связи с ноутбуком дольше — доска свободна (вкладку в фоне Chrome «будит» редко)
ONLINE_TIMEOUT = timedelta(seconds=15)    # страница доски молчит дольше — «выключена»
TOUCH_EVERY = timedelta(seconds=4)        # отметку «на связи» пишем не чаще (меньше записей в базу)
FILES_KEEP = timedelta(days=3)
MAX_FILE_MB = 100
# Что доска умеет показать сама: картинки, видео, звук, PDF. PowerPoint и Word — через «Показать экран»
FILE_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp',
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/mp4', '.m4v': 'video/mp4',
    '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
    '.pdf': 'application/pdf',
}
SDP_LIMIT = 20000

# Надписи страниц досок — здесь, а не в JS: английская версия переведёт их сама
TEXTS = {
    'free': 'Свободна', 'busy': 'Занята', 'off': 'Выключена', 'mine': 'Вы подключены',
    'connecting': 'Подключаемся…', 'sharing': 'На доске ваш экран', 'waiting_board': 'Ждём ответа доски…',
    'showing_url': 'На доске ссылка', 'showing_file': 'На доске файл', 'nothing': 'На доске пока ничего',
    'kicked': 'Вас отключили от доски', 'lost': 'Нет связи с доской — проверьте, что на ней открыта страница доски',
    'uploading': 'Загружаем файл…', 'too_big': f'Файл больше {MAX_FILE_MB} МБ',
    'server_limit': 'Сервер не принял файл: он слишком большой для настроек сервера (см. docs/server.md)',
    'no_screen': 'Браузер не дал показать экран', 'offline': 'Нет связи с сервером',
    'board_free': 'Доска свободна', 'board_held': 'Подключено', 'tap_to_start': 'Нажмите на экран, чтобы включить звук и полный экран',
    'unmute': 'Включить звук', 'open_link': 'Открыть ссылку', 'popup_blocked': 'Браузер доски не дал открыть сайт сам — нажмите кнопку',
    'since': 'с', 'copy': 'Скопировано', 'connect': 'Подключиться', 'kick': 'Отключить', 'setup': 'Настроить',
    'shows_screen': 'показывает экран', 'shows_url': 'показывает ссылку', 'shows_file': 'показывает файл',
    'confirm_kick': 'Отключить этого человека от доски?', 'saved': 'Сохранено', 'error': 'Не получилось — попробуйте ещё раз',
    'confirm_delete': 'Удалить доску? Ссылка на ней перестанет работать.',
    'confirm_token': 'Сделать новую ссылку? Старая перестанет работать — на доске нужно будет запустить новый ярлык.',
    'no_screen_here': 'С этого устройства экран показать нельзя — отправляйте ссылки и файлы',
    'stopped': 'Показ экрана остановлен', 'sent_url': 'Ссылка открыта на доске', 'sent_file': 'Файл открыт на доске',
    'cleared': 'Доска очищена', 'connected': 'Подключено', 'stop_share': 'Остановить показ', 'new_board': 'Новая доска',
    'link_failed': 'Не удалось показать экран: ноутбук и доска не видят друг друга по Wi-Fi',
    'mine_other': 'Вы подключены в другой вкладке', 'off_hint': 'Включите доску — страница доски откроется на ней сама',
}


# ---------------- Общее ----------------

def now():
    return timezone.now()


def board_online(board, at=None):
    return bool(board.seen_at and board.seen_at >= (at or now()) - ONLINE_TIMEOUT)


def holder_active(board, at=None):
    return bool(board.holder_id and board.holder_seen and board.holder_seen >= (at or now()) - HOLD_TIMEOUT)


def display_name(user):
    return (user.get_full_name() or user.username) if user else ''


def post(board, session, kind, to_board=True, **data):
    return BoardMessage.objects.create(board=board, session=session, to_board=to_board, kind=kind,
                                       payload=json.dumps(data, ensure_ascii=False))


def messages_since(board, session, to_board, since):
    rows = BoardMessage.objects.filter(board=board, session=session, to_board=to_board, id__gt=since or 0)[:50]
    return [{'id': m.id, 'kind': m.kind, **json.loads(m.payload or '{}')} for m in rows]


def release(board):
    """Освободить доску: экран доски заметит, что подключения больше нет, и вернётся к заставке.
    Только то подключение, что было прочитано: если тем временем подключился другой — его не трогаем"""
    Board.objects.filter(pk=board.pk, session=board.session).update(
        holder=None, session='', holder_since=None, holder_seen=None, showing='', showing_label='')


def ice_servers():
    """STUN для подсказки адресов; TURN — запасной путь через сервер, если Wi-Fi не пускает ноутбук к доске напрямую"""
    servers = [{'urls': 'stun:stun.l.google.com:19302'}]
    turn = getattr(settings, 'BOARD_TURN', None) or {}
    if turn.get('urls'):
        servers.append({'urls': turn['urls'], 'username': turn.get('username', ''), 'credential': turn.get('credential', '')})
    return servers


def state(board, user, at):
    online, active = board_online(board, at), holder_active(board, at)
    return {
        'id': board.id, 'name': board.name, 'online': online,
        'busy': active, 'mine': active and board.holder_id == user.id,
        'holder': display_name(board.holder) if active else '',
        'since': timezone.localtime(board.holder_since).strftime('%H:%M') if active and board.holder_since else '',
        'showing': board.showing if active else '', 'showing_label': board.showing_label if active else '',
    }


def can_use(request):
    return get_user_role(request.user) in BOARD_ROLES


def can_manage(request):
    return get_user_role(request.user) in MANAGE_ROLES


def denied():
    return JsonResponse({'ok': False, 'error': 'Нет доступа'}, status=403)


def own_board(request, board_id):
    """Доска, к которой подключён этот ноутбук (session из запроса), иначе None"""
    board = get_object_or_404(Board.objects.select_related('holder'), id=board_id)
    session = request.POST.get('session', '')
    if session and board.session == session and board.holder_id == request.user.id and holder_active(board):
        return board
    return None


# ---------------- Вкладка «Доски» (учитель, админ, менеджер) ----------------

@crm_view
def boards_page(request):
    if not can_use(request):
        from django.contrib import messages
        from django.shortcuts import redirect
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    at = now()
    boards = list(Board.objects.select_related('holder'))
    return render(request, 'dashboard/boards.html', {
        'boards': [state(b, request.user, at) for b in boards],
        'can_manage': can_manage(request),
        'setup': [{'id': b.id, 'name': b.name, 'url': request.build_absolute_uri(reverse('board_screen', args=[b.token]))}
                  for b in boards] if can_manage(request) else [],
        'board_texts': TEXTS, 'ice': ice_servers(), 'max_file_mb': MAX_FILE_MB,
        'accept': ','.join(sorted(FILE_TYPES)),
    })


@crm_view
def boards_state(request):
    if not can_use(request):
        return denied()
    at = now()
    return JsonResponse({'ok': True, 'boards': [state(b, request.user, at) for b in Board.objects.select_related('holder')]})


@crm_view
@require_POST
def board_connect(request, board_id):
    """Подключиться: доска закрепляется за этим ноутбуком, остальным — «Занята»"""
    if not can_use(request):
        return denied()
    at = now()
    board = get_object_or_404(Board, id=board_id)
    if not board_online(board, at):
        return JsonResponse({'ok': False, 'error': 'Доска выключена — откройте на ней страницу доски'})
    session = uuid.uuid4().hex
    with transaction.atomic():
        # Свободна, или прошлый учитель пропал, или это тот же человек (переподключение с другой вкладки)
        free = Q(holder__isnull=True) | Q(holder_seen__lt=at - HOLD_TIMEOUT) | Q(holder=request.user)
        taken = Board.objects.filter(free, pk=board.pk).update(
            holder=request.user, session=session, holder_since=at, holder_seen=at, showing='', showing_label='')
    if not taken:
        board.refresh_from_db()
        return JsonResponse({'ok': False, 'error': f'Доску уже занял(а) {display_name(board.holder)}', 'busy': True})
    # Сообщения прошлых подключений больше не нужны (экран доски сам заметит новое подключение)
    BoardMessage.objects.filter(board=board).exclude(session=session).delete()
    BoardMessage.objects.filter(created_at__lt=at - timedelta(days=1)).delete()
    post(board, session, 'hello', holder=display_name(request.user))
    return JsonResponse({'ok': True, 'session': session})


@crm_view
@require_POST
def board_poll(request, board_id):
    """Ноутбук на связи: продлить подключение и забрать ответы доски"""
    if not can_use(request):
        return denied()
    board = own_board(request, board_id)
    if not board:
        return JsonResponse({'ok': True, 'active': False})
    at = now()
    if at - board.holder_seen >= TOUCH_EVERY:
        Board.objects.filter(pk=board.pk, session=board.session).update(holder_seen=at)
    since = to_int(request.POST.get('since'), 0)
    return JsonResponse({'ok': True, 'active': True, 'online': board_online(board, at),
                         'messages': messages_since(board, board.session, False, since)})


SCHEME_RE = re.compile(r'^[a-z][a-z0-9+.\-]*:', re.I)


def clean_url(raw):
    """Только сайты (http/https): «example.com» → https://example.com; javascript:, file: и прочее — нет"""
    url = (raw or '').strip()
    if not url or len(url) > 2000 or any(c.isspace() for c in url):
        return None
    if not url.lower().startswith(('http://', 'https://')):
        if SCHEME_RE.match(url) and not re.match(r'^[^:/]+:\d+(?:/|$)', url):   # своя схема, а не «сайт:порт»
            return None
        url = 'https://' + url
    try:
        parts = urlparse(url)
        parts.port
    except ValueError:
        return None
    host = parts.hostname or ''
    if parts.scheme not in ('http', 'https') or not re.match(r'^[a-z0-9.\-]+$', host, re.I) or '.' not in host.strip('.'):
        return None
    return url


@crm_view
@require_POST
def board_send(request, board_id):
    """Отправить на доску: приглашение показать экран (offer), ссылку или «убрать всё»"""
    if not can_use(request):
        return denied()
    board = own_board(request, board_id)
    if not board:
        return JsonResponse({'ok': False, 'error': 'Вы уже не подключены к этой доске', 'active': False}, status=409)
    kind = request.POST.get('kind')
    if kind == 'offer':
        sdp = request.POST.get('sdp', '')
        if not sdp or len(sdp) > SDP_LIMIT:
            return JsonResponse({'ok': False, 'error': 'Неверные данные'}, status=400)
        message = post(board, board.session, 'offer', sdp=sdp)
        showing, label = 'screen', ''
    elif kind == 'url':
        url = clean_url(request.POST.get('url'))
        if not url:
            return JsonResponse({'ok': False, 'error': 'Похоже, это не ссылка на сайт'}, status=400)
        message = post(board, board.session, 'url', url=url)
        showing, label = 'url', url
    elif kind == 'clear':
        message = post(board, board.session, 'clear')
        showing, label = '', ''
    else:
        return JsonResponse({'ok': False, 'error': 'Неверные данные'}, status=400)
    Board.objects.filter(pk=board.pk).update(showing=showing, showing_label=label[:200], holder_seen=now())
    return JsonResponse({'ok': True, 'id': message.id})


@crm_view
@require_POST
def board_upload(request, board_id):
    """Файл на доску: картинка, видео, звук или PDF"""
    if not can_use(request):
        return denied()
    board = own_board(request, board_id)
    if not board:
        return JsonResponse({'ok': False, 'error': 'Вы уже не подключены к этой доске', 'active': False}, status=409)
    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'ok': False, 'error': 'Выберите файл'}, status=400)
    ext = os.path.splitext(upload.name)[1].lower()
    mime = FILE_TYPES.get(ext)
    if not mime:
        return JsonResponse({'ok': False, 'error': 'Такой файл доска не откроет: подойдут картинки, видео, звук и PDF. '
                                                   'Презентацию покажите через «Показать экран»'}, status=400)
    if upload.size > MAX_FILE_MB * 1024 * 1024:
        return JsonResponse({'ok': False, 'error': f'Файл больше {MAX_FILE_MB} МБ'}, status=400)
    # Старые файлы (и прошлый файл этой доски) больше не нужны
    for old in BoardFile.objects.filter(Q(created_at__lt=now() - FILES_KEEP) | Q(board=board)):
        old.file.delete(save=False)
        old.delete()
    name = re.sub(r'[^\w.\- ]+', '_', os.path.basename(upload.name))[:120] or f'file{ext}'
    item = BoardFile.objects.create(board=board, file=upload, name=name, mime=mime, size=upload.size, uploaded_by=request.user)
    kind = mime.split('/')[0] if mime != 'application/pdf' else 'pdf'
    message = post(board, board.session, 'file', url=reverse('board_file', args=[board.token, item.id]),
                   name=name, type=kind)
    Board.objects.filter(pk=board.pk).update(showing='file', showing_label=name, holder_seen=now())
    return JsonResponse({'ok': True, 'id': message.id, 'name': name})


@crm_view
@require_POST
def board_release(request, board_id):
    """Отключиться (кнопка или закрыли вкладку)"""
    if not can_use(request):
        return denied()
    board = own_board(request, board_id)
    if board:
        release(board)
    return JsonResponse({'ok': True})


@crm_view
@require_POST
def board_kick(request, board_id):
    """Админ и менеджер: отключить того, кто занял доску"""
    if not can_manage(request):
        return denied()
    board = get_object_or_404(Board, id=board_id)
    release(board)
    return JsonResponse({'ok': True})


# ---------------- Настройка досок (админ, разработчик, менеджер) ----------------

@crm_view
@require_POST
def board_save(request):
    """Добавить доску или переименовать / новая ссылка / удалить"""
    if not can_manage(request):
        return denied()
    action = request.POST.get('action', 'save')
    board = Board.objects.filter(id=request.POST.get('id') or 0).first()
    if action == 'delete' and board:
        for f in board.files.all():
            f.file.delete(save=False)
        board.delete()
        return JsonResponse({'ok': True})
    if action == 'token' and board:
        from .models import board_token
        release(board)
        board.token = board_token()
        board.seen_at = None
        board.save(update_fields=['token', 'seen_at'])
        return JsonResponse({'ok': True, 'url': request.build_absolute_uri(reverse('board_screen', args=[board.token]))})
    name = re.sub(r'\s+', ' ', request.POST.get('name', '')).strip()[:60]
    if not name:
        return JsonResponse({'ok': False, 'error': 'Напишите название кабинета'}, status=400)
    if board:
        board.name = name
        board.save(update_fields=['name'])
    else:
        board = Board.objects.create(name=name, order=Board.objects.count())
    return JsonResponse({'ok': True, 'id': board.id,
                         'url': request.build_absolute_uri(reverse('board_screen', args=[board.token]))})


# ---------------- Экран доски (без входа, по секретной ссылке) ----------------

def board_by_token(token):
    board = Board.objects.select_related('holder').filter(token=token).first()
    if not board:
        raise Http404
    return board


def board_screen(request, token):
    board = board_by_token(token)
    response = render(request, 'dashboard/board_screen.html', {
        'board': board, 'board_texts': TEXTS, 'ice': ice_servers(),
    })
    # Сайт открывается отдельным окном: без этого окно, ушедшее на чужой сайт, «отрывается»
    # от страницы доски, и она не может его закрыть, когда учитель показывает следующее
    response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    return response


@require_POST
def board_screen_poll(request, token):
    """Доска на связи: отметиться и забрать то, что прислал подключённый учитель"""
    board = board_by_token(token)
    at = now()
    if not board.seen_at or at - board.seen_at >= TOUCH_EVERY:
        Board.objects.filter(pk=board.pk).update(seen_at=at)
    active = holder_active(board, at)
    if board.session and not active:
        release(board)                        # учитель пропал — доска свободна
    session = board.session if active else ''
    since = to_int(request.POST.get('since'), 0) if request.POST.get('session') == session else 0
    return JsonResponse({
        'ok': True, 'session': session, 'holder': display_name(board.holder) if active else '',
        'messages': messages_since(board, session, True, since) if session else [],
    })


@require_POST
def board_screen_send(request, token):
    """Ответ доски учителю (answer на приглашение показать экран)"""
    board = board_by_token(token)
    session = request.POST.get('session', '')
    sdp = request.POST.get('sdp', '')
    if not session or session != board.session or request.POST.get('kind') != 'answer' or not sdp or len(sdp) > SDP_LIMIT:
        return JsonResponse({'ok': False}, status=400)
    post(board, session, 'answer', to_board=False, sdp=sdp, offer=to_int(request.POST.get('offer'), 0))
    return JsonResponse({'ok': True})


RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')


def board_file(request, token, file_id):
    """Файл для доски. Видео и звук отдаём частями (Range) — иначе на доске не перемотать"""
    board = board_by_token(token)
    item = get_object_or_404(BoardFile, id=file_id, board=board)
    f = item.file.open('rb')
    size = item.file.size
    match = RANGE_RE.match(request.headers.get('Range', '').strip())
    start, end = 0, size - 1
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))     # bytes=-500: последние 500 байт
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            f.close()
            return response
        f.seek(start)
        response = StreamingHttpResponse(read_part(f, end - start + 1), status=206, content_type=item.mime)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(end - start + 1)
    else:
        response = FileResponse(f, content_type=item.mime)
    response['Accept-Ranges'] = 'bytes'
    response['Content-Disposition'] = 'inline'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, max-age=3600'
    return response


def read_part(f, length, chunk=64 * 1024):
    try:
        while length > 0:
            data = f.read(min(chunk, length))
            if not data:
                break
            length -= len(data)
            yield data
    finally:
        f.close()

