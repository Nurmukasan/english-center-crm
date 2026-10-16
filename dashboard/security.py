"""Безопасность CRM.

  * client_ip — настоящий IP посетителя (подделать через заголовок нельзя);
  * защита от подбора пароля: после серии неудачных входов — пауза 15 минут
    (и на главной, и на /admin/); счётчики общие для всех процессов сервера;
  * журнал входов (LoginEvent): кто, когда, откуда, с какого устройства, удачно ли;
  * сеансы: на скольких устройствах вошёл пользователь, «выйти на других устройствах»;
  * SecurityHeadersMiddleware — заголовки, которые запрещают браузеру опасное,
    и флаг Secure у cookie, когда сайт открыт по HTTPS;
  * media — фото и PDF только для вошедших (кроме логотипа на главной);
  * password_problems — проверка нового пароля понятными словами.
"""
import ipaddress
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.views.static import serve

# ---------------- IP ----------------

def _is_local(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def client_ip(request):
    """IP посетителя. Заголовкам X-Real-IP / X-Forwarded-For верим, только если запрос пришёл от своего
    nginx на этом же сервере: иначе любой прислал бы «X-Forwarded-For: что угодно» и обошёл ограничения."""
    remote = request.META.get('REMOTE_ADDR', '')
    if _is_local(remote):
        real = request.META.get('HTTP_X_REAL_IP', '').strip()
        if real:
            return real[:45]
        forwarded = [part.strip() for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if part.strip()]
        if forwarded:
            return forwarded[-1][:45]   # последний адрес дописал наш nginx
    return remote[:45]


def is_https(request):
    if request.is_secure():
        return True
    return _is_local(request.META.get('REMOTE_ADDR', '')) and \
        request.META.get('HTTP_X_FORWARDED_PROTO', '').split(',')[0].strip().lower() == 'https'


# ---------------- Подбор пароля ----------------

LOGIN_LIMITS = (('user', 10), ('ip', 30))   # неудачных попыток за LOGIN_WINDOW
LOGIN_WINDOW = 15 * 60


def login_keys(request, username):
    return {'user': f'login-fail:u:{(username or "").strip().lower()[:150]}', 'ip': f'login-fail:ip:{client_ip(request)}'}


def login_blocked(request, username):
    keys = login_keys(request, username)
    return any(cache.get(keys[kind], 0) >= limit for kind, limit in LOGIN_LIMITS)


def login_failed(request, username):
    for key in login_keys(request, username).values():
        cache.add(key, 0, LOGIN_WINDOW)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOGIN_WINDOW)


def login_succeeded(request, username):
    cache.delete(login_keys(request, username)['user'])


# ---------------- Журнал входов ----------------

KEEP_EVENTS_DAYS = 90


def device_label(user_agent):
    """«Chrome · Windows», «Safari · iPhone» — чтобы в журнале было понятно, откуда вход"""
    ua = (user_agent or '').lower()
    if not ua:
        return 'Неизвестное устройство'
    for marker, name in (('edg/', 'Edge'), ('opr/', 'Opera'), ('yabrowser', 'Яндекс Браузер'), ('samsungbrowser', 'Samsung Internet'),
                         ('firefox', 'Firefox'), ('chrome', 'Chrome'), ('crios', 'Chrome'), ('safari', 'Safari')):
        if marker in ua:
            browser = name
            break
    else:
        browser = 'Браузер'
    for marker, name in (('iphone', 'iPhone'), ('ipad', 'iPad'), ('android', 'Android'), ('windows', 'Windows'),
                         ('mac os', 'Mac'), ('linux', 'Linux')):
        if marker in ua:
            return f'{browser} · {name}'
    return browser


def record_login(request, username, user=None, success=False, note=''):
    from .models import LoginEvent
    LoginEvent.objects.create(
        user=user, username=(username or '')[:150], success=success, note=note[:20],
        ip=client_ip(request), user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
    )
    LoginEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=KEEP_EVENTS_DAYS)).delete()


def throttled_admin_login(login_view):
    """Вход в /admin/ — с теми же ограничениями и записью в журнал, что и основной вход"""
    @wraps(login_view)
    def wrapper(request, extra_context=None):
        if request.method != 'POST':
            return login_view(request, extra_context)
        username = request.POST.get('username', '')
        if login_blocked(request, username):
            record_login(request, username, note='blocked')
            return HttpResponse('Слишком много неудачных попыток — подождите 15 минут', status=429,
                                content_type='text/plain; charset=utf-8')
        response = login_view(request, extra_context)
        if request.user.is_authenticated:
            login_succeeded(request, username)
            record_login(request, username, user=request.user, success=True, note='admin')
        else:
            login_failed(request, username)
            record_login(request, username, note='admin')
        return response
    return wrapper


# ---------------- Сеансы ----------------

def user_sessions(user):
    """Действующие сеансы пользователя (на скольких устройствах он вошёл)"""
    from django.contrib.sessions.models import Session
    found = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get('_auth_user_id') == str(user.pk):
            found.append(session)
    return found


def end_other_sessions(request):
    """Выйти на всех устройствах, кроме этого. Сколько сеансов закрыто"""
    current = request.session.session_key
    others = [s for s in user_sessions(request.user) if s.session_key != current]
    for session in others:
        session.delete()
    return len(others)


# ---------------- Заголовки ----------------

# Минимум, который ничего не ломает: никаких плагинов и <base>, страницу нельзя вставить в чужой сайт,
# формы отправляются только на этот сайт. Скрипты с CDN и встроенные скрипты страниц работают как раньше.
CSP = "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
PERMISSIONS = 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'
HSTS_SECONDS = 30 * 24 * 3600


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault('Content-Security-Policy', CSP)
        response.headers.setdefault('Permissions-Policy', PERMISSIONS)
        if is_https(request):
            # Сайт открыт по HTTPS: браузер больше не пойдёт на http://, а cookie входа не уйдут по незащищённому каналу
            response.headers.setdefault('Strict-Transport-Security', f'max-age={HSTS_SECONDS}')
            for cookie in response.cookies.values():
                cookie['secure'] = True
        return response


# ---------------- Файлы ----------------

PUBLIC_MEDIA = ('logo/',)   # видно всем — логотип на главной и в окне входа


def media(request, path):
    """Фото учеников и сотрудников, обложки и PDF книг — только для вошедших в CRM"""
    public = path.startswith(PUBLIC_MEDIA)
    if not public and not request.user.is_authenticated:
        raise Http404
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if not public:
        response.headers['Cache-Control'] = 'private, max-age=3600'
    return response


# ---------------- Пароли ----------------

PASSWORD_MESSAGES = (
    (password_validation.MinimumLengthValidator, 'Пароль — не короче 8 символов'),
    (password_validation.NumericPasswordValidator, 'Пароль не может состоять только из цифр'),
    (password_validation.CommonPasswordValidator, 'Слишком простой пароль — его легко угадать'),
    (password_validation.UserAttributeSimilarityValidator, 'Пароль слишком похож на логин или имя'),
)


def password_problems(password, user=None):
    """Что не так с паролем — список понятных фраз (пустой — пароль подходит)"""
    problems = []
    for validator_class, text in PASSWORD_MESSAGES:
        try:
            validator_class().validate(password or '', user)
        except ValidationError:
            problems.append(text)
    return problems
