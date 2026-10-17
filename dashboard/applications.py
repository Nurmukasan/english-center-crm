"""Заявки на обучение с лендинга: «Английский» и «IELTS» — две формы с общей основой.

Посетитель оставляет имя, возраст, примерный уровень, сколько планирует заниматься, когда учится
(утром или вечером) и телефон — свой и/или родителя; у IELTS ещё нужный балл и когда экзамен.
Заявки попадают во вкладку «Сообщения» → «Заявки» у админа и менеджеров: новые, в работе, закрытые.
Из заявки можно сразу создать ученика (форма «Добавить ученика» заполнится сама).

Защита от спама без капчи: скрытое поле-ловушка для ботов, подпись времени открытия страницы
(бот отправляет форму мгновенно), не больше APPLY_LIMIT заявок с одного IP за час, повтор с того же
телефона не создаёт новую заявку.
"""
import re
from datetime import timedelta

from django.core import signing
from django.core.cache import cache
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .inbox import INBOX_ROLES, when_label
from .models import Application
from .security import client_ip
from .views import crm_view, get_user_role, ru_plural, to_int
from .whatsapp import format_phone, normalize_phone

APPLY_LIMIT = 20            # заявок с одного IP за час (у мобильных операторов один IP на многих)
MIN_SECONDS = 3             # быстрее человек форму не заполнит
STARTED_SALT = 'apply-started'
DUPLICATE_WINDOW = timedelta(days=1)
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’\-][^\W\d_]+)*$")
SCORE_RE = re.compile(r'^\d(?:[.,]\d)?$')
KINDS = dict(Application.KIND_CHOICES)


def started_token():
    """Подпись времени, когда открыли страницу с формой (проверяется при отправке)"""
    return signing.dumps(int(timezone.now().timestamp()), salt=STARTED_SALT)


def options():
    """Варианты для чипов в формах лендинга"""
    return {
        'levels': Application.LEVEL_CHOICES,
        'durations': Application.DURATION_CHOICES,
        'shifts': Application.SHIFT_CHOICES,
        'targets': Application.TARGET_CHOICES,
        'exams': Application.EXAM_CHOICES,
    }


def clean_name(value, errors, field, label):
    value = re.sub(r'\s+', ' ', (value or '').strip())[:60]
    if not value:
        errors[field] = f'Напишите {label}'
    elif not NAME_RE.match(value):
        errors[field] = 'Только буквы, без цифр и значков'
    return value[:1].upper() + value[1:]


def validate(data):
    """Проверить поля формы → (поля заявки, ошибки по полям)"""
    errors = {}
    kind = data.get('kind') if data.get('kind') in KINDS else 'english'
    fields = {'kind': kind}
    fields['first_name'] = clean_name(data.get('first_name'), errors, 'first_name', 'имя')
    fields['last_name'] = clean_name(data.get('last_name'), errors, 'last_name', 'фамилию')
    age = to_int(data.get('age'))
    if age is None or not 3 <= age <= 90:
        errors['age'] = 'Укажите возраст числом'
    fields['age'] = age

    def choice(field, choices, message):
        value = data.get(field, '')
        if value not in dict(choices):
            errors[field] = message
        fields[field] = value
    choice('level', Application.LEVEL_CHOICES, 'Выберите примерный уровень')
    choice('duration', Application.DURATION_CHOICES, 'Выберите, сколько планируете заниматься')
    choice('shift', Application.SHIFT_CHOICES, 'Выберите, когда учится')

    phones = {}
    for field in ('phone', 'parent_phone'):
        raw = (data.get(field) or '').strip()
        number = normalize_phone(raw)
        if raw and not number:
            errors[field] = 'Проверьте номер: +7 и 10 цифр'
        phones[field] = number
        fields[field] = format_phone(number) if number else ''
    if not any(phones.values()) and not any(f in errors for f in phones):
        errors['phone'] = 'Нужен хотя бы один номер — свой или родителя'
    fields['_phones'] = [p for p in phones.values() if p]

    if kind == 'ielts':
        choice('target_score', Application.TARGET_CHOICES, 'Выберите нужный балл')
        choice('exam_when', Application.EXAM_CHOICES, 'Выберите, когда экзамен')
        previous = (data.get('previous_score') or '').strip().replace(',', '.')
        if previous and (not SCORE_RE.match(previous) or float(previous) > 9):
            errors['previous_score'] = 'Балл от 0 до 9, например 5.5'
        fields['previous_score'] = previous
    comment = (data.get('comment') or '').strip()
    if len(comment) > 1000:
        errors['comment'] = 'Слишком длинно — до 1000 символов'
    fields['comment'] = comment[:1000]
    return fields, errors


@require_POST
def apply(request):
    """Заявка с лендинга (без входа). JSON для формы на странице; без JS — возврат на лендинг"""
    wants_json = 'application/json' in request.headers.get('accept', '')

    def answer(ok, message='', errors=None, status=200, kind='english'):
        if wants_json:
            return JsonResponse({'ok': ok, 'message': message, 'errors': errors or {}}, status=status)
        return redirect(f'/?applied={kind}#apply' if ok else '/?apply_error=1#apply')

    done = 'Заявка отправлена! Мы позвоним в ближайшее время.'
    # Ловушка для ботов: человек это поле не видит
    if request.POST.get('ee_hp'):
        return answer(True, done)
    try:
        opened = signing.loads(request.POST.get('started', ''), salt=STARTED_SALT, max_age=2 * 86400)
    except signing.BadSignature:
        return answer(False, 'Страница устарела — обновите её и отправьте ещё раз', status=400)
    if timezone.now().timestamp() - opened < MIN_SECONDS:
        return answer(True, done)

    fields, errors = validate(request.POST)
    if errors:
        return answer(False, 'Проверьте отмеченные поля', errors, status=400)

    phones = fields.pop('_phones')
    recent = Application.objects.filter(kind=fields['kind'], created_at__gte=timezone.now() - DUPLICATE_WINDOW)
    if any(normalize_phone(a.phone) in phones or normalize_phone(a.parent_phone) in phones for a in recent):
        return answer(True, done, kind=fields['kind'])        # уже есть — второй раз не дублируем

    ip = client_ip(request)
    key = f'apply:{ip}'
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)
        count = 1
    if count > APPLY_LIMIT:
        return answer(False, 'Слишком много заявок с этого устройства — позвоните нам или попробуйте через час', status=429)

    Application.objects.create(**fields, ip=ip or None)
    return answer(True, done, kind=fields['kind'])


# ---------------- Вкладка «Заявки» в «Сообщениях» ----------------

STATUS_ORDER = ['new', 'contacted', 'enrolled', 'declined']


def new_count():
    return Application.objects.filter(status='new').count()


def inbox_context(request, now):
    """Заявки для вкладки: фильтр по форме, разделы «Новые», «Связались», «Закрытые»"""
    kind = request.GET.get('kind') if request.GET.get('kind') in KINDS else ''
    items = Application.objects.select_related('status_by', 'student')
    if kind:
        items = items.filter(kind=kind)
    by_kind = {row['kind']: row['n'] for row in Application.objects.values('kind').annotate(n=Count('id'))}
    new_by_kind = {row['kind']: row['n'] for row in Application.objects.filter(status='new').values('kind').annotate(n=Count('id'))}

    def decorate(apps):
        for a in apps:
            a.age_label = f'{a.age} {ru_plural(a.age, "год", "года", "лет")}'
            a.when = when_label(a.created_at, now)
            a.status_when = when_label(a.status_at, now) if a.status_at else ''
            a.phones = [(label, number, normalize_phone(number)) for label, number in
                        (('ученик', a.phone), ('родитель', a.parent_phone)) if number]
        return apps
    return {
        'app_kind': kind,
        'apps_new': decorate(list(items.filter(status='new').order_by('-created_at'))),
        'apps_contacted': decorate(list(items.filter(status='contacted').order_by('-status_at', '-created_at'))),
        'apps_closed': decorate(list(items.filter(status__in=['enrolled', 'declined']).order_by('-status_at', '-created_at')[:60])),
        'apps_total': sum(by_kind.values()),
        'apps_by_kind': {k: by_kind.get(k, 0) for k in KINDS},
        'apps_new_by_kind': {k: new_by_kind.get(k, 0) for k in KINDS},
    }


@crm_view
@require_POST
def set_status(request, application_id):
    """Статус заявки: связались, записан, отказ или снова «новая»"""
    if get_user_role(request.user) not in INBOX_ROLES:
        return JsonResponse({'ok': False, 'error': 'Нет доступа'}, status=403)
    application = get_object_or_404(Application, id=application_id)
    status = request.POST.get('status')
    if status in dict(Application.STATUS_CHOICES):
        application.status = status
        application.status_by = request.user if status != 'new' else None
        application.status_at = timezone.now() if status != 'new' else None
        application.save(update_fields=['status', 'status_by', 'status_at'])
    url = request.POST.get('next', '')
    return redirect(url if url.startswith('/messages/') else '/messages/?cat=applications')


def for_new_student(request):
    """«Добавить ученика» из заявки: поля формы и id заявки (только админ и менеджеры)"""
    application = Application.objects.filter(id=to_int(request.GET.get('application') or request.POST.get('application'), 0)).first()
    if not application or get_user_role(request.user) not in INBOX_ROLES:
        return None, {}
    return application, {
        'name': application.full_name, 'age': application.age,
        'phone': application.phone, 'parent_phone': application.parent_phone,
    }


def mark_enrolled(application, student, user):
    application.status, application.student = 'enrolled', student
    application.status_by, application.status_at = user, timezone.now()
    application.save(update_fields=['status', 'student', 'status_by', 'status_at'])
