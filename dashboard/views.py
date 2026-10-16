from django.contrib.auth.models import User
from django.db.models import Q, Sum, Count, Min
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from functools import wraps
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime, timedelta, date
import json
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book, ScheduleSlot, LevelCalibration, InboxMessage
from users.models import Profile
from decimal import Decimal
import re


def count_lessons_in_period(group, period_start, period_end):
    """Сколько уроков у группы попадёт в период по расписанию"""
    lesson_day_of_weeks = set(s.day_of_week for s in group.schedule_slots.all())
    if not lesson_day_of_weeks:
        return 0
    count = 0
    current = period_start
    while current <= period_end:
        if current.weekday() in lesson_day_of_weeks:
            count += 1
        current += timedelta(days=1)
    return count


def calculate_cycle_amount(student, group, period_start, period_end):
    """Сумма за цикл = цена_за_урок × кол-во_уроков"""
    lessons = count_lessons_in_period(group, period_start, period_end)
    price = student.price_per_lesson or Decimal('0')
    return Decimal(str(price)) * lessons


# Контакты для лендинга. Пустые поля на странице не показываются.
LANDING_CONTACTS = {
    'phone': '',       # например: '+7 700 000 00 00'
    'whatsapp': '',    # номер только цифрами: '77000000000'
    'instagram': '',   # логин без @: 'enjoy_english'
    'address': '',     # например: 'г. Алматы, ул. Абая, 1'
}


def landing_stats():
    """Живые цифры центра для лендинга (нулевые не показываем)"""
    stats = [
        {'value': Student.objects.filter(is_active=True).count(), 'label': 'учеников'},
        {'value': Group.objects.filter(is_active=True).count(), 'label': 'активных групп'},
        {'value': Profile.objects.filter(role='teacher').count(), 'label': 'учителей'},
        {'value': Attendance.objects.aggregate(total=Sum('stars'))['total'] or 0, 'label': 'звёзд выдано'},
    ]
    return [s for s in stats if s['value']]


LOGIN_LIMITS = (('user', 10), ('ip', 30))   # неудачных попыток за LOGIN_WINDOW
LOGIN_WINDOW = 15 * 60


def login_keys(request, username):
    ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
    return {'user': f'login-fail:u:{(username or "").strip().lower()[:150]}', 'ip': f'login-fail:ip:{ip}'}


def login_blocked(request, username):
    """Защита от подбора пароля: после серии неудачных входов — пауза 15 минут"""
    from django.core.cache import cache
    keys = login_keys(request, username)
    return any(cache.get(keys[kind], 0) >= limit for kind, limit in LOGIN_LIMITS)


def login_failed(request, username):
    from django.core.cache import cache
    for key in login_keys(request, username).values():
        cache.add(key, 0, LOGIN_WINDOW)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOGIN_WINDOW)


def login_view(request):
    """Лендинг центра + вход для сотрудников"""
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    # Окно входа открыто сразу, если сюда перекинуло со страницы CRM или вход не удался
    next_url = request.GET.get('next', '')
    show_login = bool(next_url)
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = None
        if login_blocked(request, username):
            messages.error(request, 'Слишком много неудачных попыток — подождите 15 минут')
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                login_failed(request, username)
                messages.error(request, 'Неверный логин или пароль')
        
        if user is not None:
            from django.core.cache import cache
            cache.delete(login_keys(request, username)['user'])
            login(request, user)
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('dashboard')
        else:
            show_login = True
    
    contacts = {k: v for k, v in LANDING_CONTACTS.items() if v}
    context = {
        'stats': landing_stats(),
        'contacts': contacts,
        'show_login': show_login,
        'year': timezone.localdate().year,
    }
    return render(request, 'dashboard/landing.html', context)


def logout_view(request):
    """Выход"""
    list(messages.get_messages(request))  # непоказанные сообщения CRM не должны всплыть в окне входа
    logout(request)
    return redirect('login')


def get_user_role(user):
    """Роль пользователя в CRM.

    Без профиля роль есть только у суперпользователя (admin). Остальным доступ закрыт,
    пока администратор не назначит роль в /admin/ → Профили.
    """
    profile = getattr(user, 'profile', None)
    if profile is not None:
        return profile.role
    return 'admin' if user.is_superuser else None


def crm_view(view):
    """Страница CRM: нужен вход и назначенная роль"""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if get_user_role(request.user) is None:
            if request.method == 'POST' or request.path.startswith('/api/'):
                return JsonResponse({'success': False, 'error': 'Роль не назначена'}, status=403)
            return render(request, 'dashboard/no_role.html', status=403)
        return view(request, *args, **kwargs)
    return wrapper


# Кто что может (кроме учителя, которому доступны только свои группы):
# группы и ученики — создавать, менять, архивировать, записывать и убирать из групп, вести уроки
MANAGE_ROLES = ('admin', 'developer', 'manager')
# оплаты — страница оплат и отметки об оплате
MONEY_ROLES = ('admin', 'accountant', 'developer', 'manager')


# ---------------- Ввод из форм: числа, цена, телефон ----------------

def to_int(value, default=None):
    """«12» → 12; пусто, буквы, мусор → default (а не ошибка 500)"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_ids(values):
    """Список id из формы: только числа"""
    return [i for i in (to_int(v) for v in values) if i is not None]


class FormError(Exception):
    pass


def parse_age(value):
    value = (value or '').strip()
    if not value:
        return None
    age = to_int(value)
    if age is None or not 1 <= age <= 99:
        raise FormError('Возраст — число от 1 до 99')
    return age


def parse_price(value, default=None):
    """«3 000», «3000,50» → Decimal; пусто → default"""
    value = (value or '').replace(' ', '').replace('\u00a0', '').replace(',', '.').strip()
    if not value:
        return default
    try:
        price = Decimal(value)
    except ArithmeticError:
        raise FormError('Цена за урок — число, например 3000')
    if not price.is_finite() or price < 0 or price > 10_000_000:
        raise FormError('Цена за урок — число от 0 до 10 000 000')
    return price.quantize(Decimal('0.01'))


def clean_text(value, limit):
    return (value or '').strip()[:limit]


IMAGE_FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}


def check_image(upload, max_mb=5):
    """Загруженный файл — настоящая картинка (а не HTML со скриптом под видом фото). Ошибка или None."""
    if upload.size > max_mb * 1024 * 1024:
        return f'Файл больше {max_mb} МБ — выберите фото поменьше'
    try:
        from PIL import Image
        with Image.open(upload) as image:
            fmt = image.format
            image.verify()
    except Exception:
        return 'Это не картинка — загрузите фото в JPG или PNG'
    finally:
        upload.seek(0)
    if fmt not in IMAGE_FORMATS:
        return 'Подойдёт фото в JPG, PNG или WEBP'
    # Расширение — по настоящему формату, чтобы файл не открылся как страница
    base = (upload.name.rsplit('.', 1)[0] or 'photo')[:60]
    upload.name = f'{base}.{IMAGE_FORMATS[fmt]}'
    return None


# Уровень группы по названию — тот же порядок слов, что в миграции 0027
LEVEL_WORDS = [
    (6, ('upper', 'b2')),
    (4, ('pre-int', 'pre int', 'preint', 'pre-inter', 'a2+')),
    (8, ('proficiency', 'c2')),
    (7, ('advanced', 'c1')),
    (5, ('intermediate', 'b1')),
    (3, ('elementary', 'a2')),
    (2, ('beginner', 'a1')),
    (1, ('starter', 'kids', 'kid ')),
]


def guess_level(name):
    name = (name or '').lower() + ' '
    return next((level for level, words in LEVEL_WORDS if any(w in name for w in words)), None)


def read_level(request, name):
    """Уровень из формы группы; «определить по названию» — пусто"""
    try:
        level = int(request.POST.get('level') or 0)
    except (TypeError, ValueError):
        level = 0
    return level if level in dict(Group.LEVEL_CHOICES) else guess_level(name)


def can_manage_group(user, role, group):
    """Вести уроки группы: админ, разработчик и менеджер — любой, учитель — только своей"""
    if role in MANAGE_ROLES:
        return True
    if role == 'teacher':
        return group.teacher_id == user.id or group.teachers.filter(id=user.id).exists()
    return False


DAY_KEYWORDS = {
    0: ['пн', 'понедельник'],
    1: ['вт', 'вторник'],
    2: ['ср', 'сред'],
    3: ['чт', 'четверг'],
    4: ['пт', 'пятниц'],
    5: ['сб', 'суббот'],
    6: ['вс', 'воскрес'],
}


def is_lesson_day(group, day):
    """Есть ли у группы урок в этот день: по расписанию, у старых групп без слотов — по тексту"""
    weekdays = {slot.day_of_week for slot in group.schedule_slots.all()}
    if weekdays:
        return day.weekday() in weekdays
    text = (group.schedule or '').lower()
    return any(keyword in text for keyword in DAY_KEYWORDS[day.weekday()])


def fill_attendance(lesson):
    """Отметки «не был» для всех, кто к дате урока уже был записан в группу"""
    for enrollment in Enrollment.objects.filter(group=lesson.group):
        if timezone.localtime(enrollment.enrolled_at).date() <= lesson.date:
            Attendance.objects.get_or_create(lesson=lesson, student_id=enrollment.student_id, defaults={'status': 'absent'})


def group_student_ids(group):
    return set(Enrollment.objects.filter(group=group).values_list('student_id', flat=True))


# Оттенки групп на главной: у каждой группы свой цвет
GROUP_HUES = [262, 316, 38, 190, 290, 336, 160, 20]
DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
WEEKDAYS_RU = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']


def dashboard_extras(base_groups, groups):
    """Данные для главной: занятия сегодня (лента дня) и оформление карточек групп"""
    from django.db.models import Count

    today = timezone.localdate()
    counts = dict(
        Enrollment.objects.filter(group__in=base_groups)
        .values('group').annotate(c=Count('id')).values_list('group', 'c')
    )
    today_lessons = []
    for slot in (ScheduleSlot.objects.filter(group__in=base_groups, day_of_week=today.weekday())
                 .select_related('group', 'group__teacher').order_by('start_time')):
        g = slot.group
        start = slot.start_time.hour * 60 + slot.start_time.minute
        end = slot.end_time.hour * 60 + slot.end_time.minute
        today_lessons.append({
            'group': g.name,
            'url': reverse('group_detail', args=[g.id]),
            'teacher': g.teacher.username if g.teacher else '',
            'students': counts.get(g.id, 0),
            'start': start,
            'end': max(end, start + 30),
            'hue': GROUP_HUES[g.id % len(GROUP_HUES)],
        })

    cards = list(groups.select_related('teacher').prefetch_related('schedule_slots', 'teachers', 'enrollments__student'))
    for g in cards:
        g.ee_hue = GROUP_HUES[g.id % len(GROUP_HUES)]
        lesson_days = {slot.day_of_week for slot in g.schedule_slots.all()}
        g.ee_days = [{'short': d, 'on': i in lesson_days} for i, d in enumerate(DAYS_SHORT)]
        students = [e.student for e in g.enrollments.all()]
        g.ee_count = len(students)
        g.ee_faces = [st.name[:1].upper() for st in students[:4]]
        g.ee_more = max(0, len(students) - 4)

    now = timezone.localtime()
    weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    return {
        'today_lessons_data': today_lessons,
        'today_lessons_count': len(today_lessons),
        'group_cards': cards,
        # Часы и лента идут по времени сервера (часовой пояс центра), а не браузера
        'now_seconds': now.hour * 3600 + now.minute * 60 + now.second,
        'today_label': f'{weekdays[now.weekday()]}, {now.day} {months[now.month - 1]}',
    }


TIME_RE = re.compile(r'^\d{1,2}:\d{2}$')


def save_schedule(request, group):
    """Расписание из формы (day_N, start_N, end_N) → слоты и текст расписания группы"""
    group.schedule_slots.all().delete()
    for i in range(7):
        if request.POST.get(f'day_{i}') != '1':
            continue
        start = request.POST.get(f'start_{i}', '').strip()
        end = request.POST.get(f'end_{i}', '').strip()
        if TIME_RE.match(start) and TIME_RE.match(end):
            try:
                ScheduleSlot.objects.create(group=group, day_of_week=i, start_time=start, end_time=end)
            except Exception as e:
                messages.error(request, f'Ошибка в дне {DAYS_SHORT[i]}: {e}')
    group.schedule = ', '.join(
        f"{DAYS_SHORT[slot.day_of_week]} {slot.start_time:%H:%M}-{slot.end_time:%H:%M}"
        for slot in group.schedule_slots.order_by('day_of_week', 'start_time')
    )
    group.save(update_fields=['schedule'])


def search_filter(queryset, query, field='name'):
    """Поиск без учёта регистра (и для кириллицы в SQLite) без ошибок на ( [ * и т.п."""
    query = (query or '').strip()[:100]
    if not query:
        return queryset
    return queryset.filter(**{f'{field}__iregex': re.escape(query)})


def current_payment_summary(groups):
    """Оплаты за текущий период каждой группы: (оплатили, всего должны оплатить)"""
    groups = list(groups.prefetch_related('enrollments'))
    periods = {g.id: get_current_period(g.cycle_start_day) for g in groups}
    paid_keys = set(Payment.objects.filter(
        group__in=groups,
        is_paid=True,
        cycle_number__in={y * 100 + m for y, m in periods.values()},
    ).values_list('student_id', 'group_id', 'cycle_number'))

    paid = total = 0
    for g in groups:
        y, m = periods[g.id]
        period_end = get_period(y, m, g.cycle_start_day)[1]
        for e in g.enrollments.all():
            is_paid = (e.student_id, g.id, y * 100 + m) in paid_keys
            # Пришёл после этого периода — за него не платит (как на странице оплат)
            if is_paid or is_enrolled_by(e, period_end):
                total += 1
                paid += is_paid
    return paid, total


@crm_view
def dashboard(request):
    """Главный дашборд"""
    role = get_user_role(request.user)
    search_query = request.GET.get('search', '')
    today = timezone.localdate()

    if role == 'teacher':
        mine = Q(teacher=request.user) | Q(teachers=request.user)
        base_groups = Group.objects.filter(mine, is_active=True).distinct()
        # Индивидуальные — в своей вкладке, здесь только групповые
        groups = search_filter(base_groups.exclude(group_type='individual'), search_query)
        context = {
            'role': 'teacher',
            'groups': groups,
            'total_students': Student.objects.filter(
                is_active=True, enrollments__group__in=base_groups
            ).distinct().count(),
            'search_query': search_query,
        }
        context.update(dashboard_extras(base_groups, groups))
    else:
        base_groups = Group.objects.filter(is_active=True)
        # Архив групп видят админ и разработчик
        show_archive = request.GET.get('archived') == '1' and role in MANAGE_ROLES
        groups = Group.objects.filter(is_active=not show_archive)
        if not show_archive:
            groups = groups.exclude(group_type='individual')  # они в своей вкладке
        groups = search_filter(groups, search_query)

        paid_count, billable_count = current_payment_summary(base_groups)
        today_attendance = Attendance.objects.filter(lesson__date=today, lesson__is_cancelled=False)

        context = {
            'role': role,
            'groups': groups,
            'total_students': Student.objects.filter(is_active=True).count(),
            'total_groups': base_groups.exclude(group_type='individual').count(),
            'paid_count': paid_count,
            'unpaid_count': billable_count - paid_count,
            'present_count': today_attendance.filter(status__in=['present', 'late']).count(),
            'total_attendance': today_attendance.count(),
            'search_query': search_query,
            'show_archive': show_archive,
            'archived_groups_count': Group.objects.filter(is_active=False).count(),
        }
        context.update(dashboard_extras(base_groups, groups))
    
    context['individual_count'] = Enrollment.objects.filter(
        group__in=base_groups.filter(group_type='individual'), student__is_active=True
    ).count()
    return render(request, 'dashboard/dashboard.html', context)


def rate(part, total):
    return round(part / total * 100) if total else None


@crm_view
def group_detail(request, group_id):
    """Страница группы: сегодняшний урок, статистика группы и каждого ученика"""
    group = get_object_or_404(Group.objects.select_related('teacher'), id=group_id)
    role = get_user_role(request.user)

    if role == 'teacher' and not can_manage_group(request.user, role, group):
        messages.error(request, 'У вас нет доступа к этой группе')
        return redirect('dashboard')

    enrollments = list(Enrollment.objects.filter(group=group).select_related('student').order_by('student__name'))
    today = timezone.localdate()
    now = timezone.localtime()
    slots = list(group.schedule_slots.order_by('day_of_week', 'start_time'))

    if group.is_active and is_lesson_day(group, today):
        lesson, created = Lesson.objects.get_or_create(group=group, date=today, defaults={'topic': ''})
        if created:
            fill_attendance(lesson)
    else:
        lesson = None
    today_att = {a.student_id: a for a in Attendance.objects.filter(lesson=lesson)} if lesson else {}

    # Последние проведённые уроки (без сегодняшнего — он ещё отмечается)
    recent = list(Lesson.objects.filter(group=group, is_cancelled=False, date__lt=today).order_by('-date')[:10])
    recent.reverse()
    by_lesson, by_student = {}, {}
    for a in Attendance.objects.filter(lesson__in=recent).values('lesson_id', 'student_id', 'status', 'homework_done'):
        by_lesson.setdefault(a['lesson_id'], []).append(a)
        by_student.setdefault(a['student_id'], {})[a['lesson_id']] = a
    rhythm = []
    for l in recent:
        rows = by_lesson.get(l.id, [])
        came = sum(1 for r in rows if r['status'] != 'absent')
        rhythm.append({'date': l.date, 'came': came, 'total': len(rows), 'rate': rate(came, len(rows)) or 0})

    # Группа за 30 дней и 30 дней до этого — для сравнения
    def window(days_from, days_to):
        return Attendance.objects.filter(
            lesson__group=group, lesson__is_cancelled=False,
            lesson__date__gte=today - timedelta(days=days_from), lesson__date__lt=today - timedelta(days=days_to),
        ).aggregate(total=Count('id'), came=Count('id', filter=~Q(status='absent')),
                    homework=Count('id', filter=Q(homework_done=True)))
    cur, prev = window(30, 0), window(60, 30)
    attendance_rate, homework_rate = rate(cur['came'], cur['total']), rate(cur['homework'], cur['total'])
    prev_attendance, prev_homework = rate(prev['came'], prev['total']), rate(prev['homework'], prev['total'])

    stars_total = dict(Attendance.objects.filter(lesson__group=group, stars__gt=0)
                       .values('student_id').annotate(c=Sum('stars')).values_list('student_id', 'c'))
    stars_month = Attendance.objects.filter(lesson__group=group, lesson__date__gte=today.replace(day=1)) \
        .aggregate(s=Sum('stars'))['s'] or 0

    # Текущий период оплаты: сколько уроков проведено из запланированных
    py, pm = get_current_period(group.cycle_start_day)
    period_start, period_end = get_period(py, pm, group.cycle_start_day)
    planned = count_lessons_in_period(group, period_start, period_end)
    held = Lesson.objects.filter(group=group, is_cancelled=False, date__gte=period_start,
                                 date__lte=min(period_end, today)).count()
    can_money = role in MONEY_ROLES
    paid_ids = set(Payment.objects.filter(group=group, cycle_number=py * 100 + pm, is_paid=True)
                   .values_list('student_id', flat=True)) if can_money else set()

    hue = GROUP_HUES[group.id % len(GROUP_HUES)]
    student_data = []
    for e in enrollments:
        s = e.student
        att = today_att.get(s.id)
        mine = by_student.get(s.id, {})
        came = sum(1 for a in mine.values() if a['status'] != 'absent')
        student_data.append({
            'student': s,
            'enrollment': e,
            'hue': GROUP_HUES[s.id % len(GROUP_HUES)],
            'attendance_status': att.status if att else 'absent',
            'homework_done': bool(att and att.homework_done),
            'stars': stars_total.get(s.id, 0),
            'stars_today': att.stars if att else 0,
            'rate': rate(came, len(mine)),
            # Последние 6 уроков: точка на каждый (None — ещё не учился в группе)
            'dots': [mine[l.id]['status'] if l.id in mine else None for l in recent[-6:]],
            'is_paid': s.id in paid_ids,
            'billable': is_enrolled_by(e, period_end),
        })
    leaders = sorted((d for d in student_data if d['stars']), key=lambda d: -d['stars'])[:3]
    billable = [d for d in student_data if d['billable'] or d['is_paid']]

    next_label, live = next_lesson_label(slots, now)
    today_slot = next((sl for sl in slots if sl.day_of_week == today.weekday()), None)
    present_today = sum(1 for d in student_data if d['attendance_status'] != 'absent')

    context = {
        'group': group,
        'hue': hue,
        'student_data': student_data,
        'today': today,
        'role': role,
        'lessons_history': [{'lesson': l, 'day': l.date.day, 'month': MONTHS_SHORT[l.date.month - 1].lower(),
                             'weekday': WEEKDAYS_RU[l.date.weekday()]}
                            for l in Lesson.objects.filter(group=group).order_by('-date')[:6]],
        'lesson': lesson,
        'today_slot': today_slot,
        'can_mark_attendance': can_manage_group(request.user, role, group),
        'can_manage': role in MANAGE_ROLES,
        # Написать админу про ученика (сам админ пишет не себе)
        'can_write': role in ('teacher', 'manager') and can_manage_group(request.user, role, group),
        'can_money': can_money,
        'lesson_active': bool(lesson and not lesson.is_cancelled),
        'week': [{'short': d, 'times': [f'{sl.start_time:%H:%M}' for sl in slots if sl.day_of_week == i],
                  'today': i == today.weekday()} for i, d in enumerate(DAYS_SHORT)],
        'next_label': next_label,
        'live': live,
        'rhythm': rhythm,
        'attendance_rate': attendance_rate,
        'homework_rate': homework_rate,
        'attendance_delta': attendance_rate - prev_attendance if attendance_rate is not None and prev_attendance is not None else None,
        'homework_delta': homework_rate - prev_homework if homework_rate is not None and prev_homework is not None else None,
        'stars_month': stars_month,
        'leaders': leaders,
        'planned': planned,
        'held': held,
        'held_share': min(100, rate(held, planned) or 0),
        'period_start': period_start,
        'period_end': period_end,
        'paid_count': sum(1 for d in billable if d['is_paid']),
        'billable_count': len(billable),
        'present_today': present_today,
        'homework_today': sum(1 for d in student_data if d['homework_done']),
        'back_url': reverse('individual_lessons') if group.group_type == 'individual' else reverse('dashboard'),
    }
    return render(request, 'dashboard/group_detail.html', context)


@crm_view
@require_POST
def mark_attendance(request, group_id):
    """Отметка посещаемости (AJAX)"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    if not can_manage_group(request.user, role, group):
        return JsonResponse({'success': False, 'error': 'Нет доступа'})

    today = timezone.localdate()
    if not is_lesson_day(group, today):
        return JsonResponse({'success': False, 'error': 'Сегодня нет урока'})

    status = request.POST.get('status')
    if status not in dict(Attendance.STATUS_CHOICES):
        return JsonResponse({'success': False, 'error': 'Неверный статус'})
    try:
        student_id = int(request.POST.get('student_id'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Неверный ученик'})
    if student_id not in group_student_ids(group):
        return JsonResponse({'success': False, 'error': 'Ученик не из этой группы'})

    lesson, _ = Lesson.objects.get_or_create(group=group, date=today)
    if lesson.is_cancelled:
        return JsonResponse({'success': False, 'error': 'Урок отменён'})

    attendance, _ = Attendance.objects.get_or_create(lesson=lesson, student_id=student_id, defaults={'status': status})
    attendance.status = status
    attendance.save()

    return JsonResponse({'success': True, 'status': status})


@crm_view
def students_list(request):
    """Список всех учеников с фильтрами"""
    role = get_user_role(request.user)

    if role == 'accountant':
        messages.error(request, 'У вас нет доступа')
        return redirect('payments_page')

    # Базовая выборка по роли
    if role == 'teacher':
        students = Student.objects.filter(
            Q(enrollments__group__teacher=request.user) |
            Q(enrollments__group__teachers=request.user)
        ).distinct()
        available_groups = Group.objects.filter(
            Q(teacher=request.user) | Q(teachers=request.user),
            is_active=True
        ).distinct().order_by('name')
    else:
        students = Student.objects.all()
        available_groups = Group.objects.filter(is_active=True).order_by('name')

    # Архив видят админ и разработчик, остальные списки — только те, кто учится
    show_archive = request.GET.get('archived') == '1' and role in MANAGE_ROLES
    students = students.filter(is_active=not show_archive)

    # ===== Поиск =====
    search_query = request.GET.get('search', '').strip()
    if search_query:
        students = search_filter(students, search_query)

    # ===== Фильтр: группа =====
    group_filter = request.GET.get('group', '')
    if group_filter == 'none':
        # Ученики без группы
        students = students.filter(enrollments__isnull=True)
    elif to_int(group_filter):
        students = students.filter(enrollments__group_id=to_int(group_filter))

    # ===== Фильтр: класс =====
    grade_filter = request.GET.get('grade', '').strip()
    if grade_filter:
        students = students.filter(grade=grade_filter)

    # ===== Фильтр: школа =====
    school_filter = request.GET.get('school', '').strip()
    if school_filter:
        students = students.filter(school=school_filter)

    # ===== Фильтр: возраст =====
    age_filter = request.GET.get('age', '').strip()
    if age_filter:
        try:
            students = students.filter(age=int(age_filter))
        except ValueError:
            pass

    students = students.distinct().order_by('name')

    # ===== Опции для фильтров (уникальные) =====
    base_for_options = Student.objects.filter(is_active=True)
    if role == 'teacher':
        base_for_options = base_for_options.filter(
            Q(enrollments__group__teacher=request.user) |
            Q(enrollments__group__teachers=request.user)
        ).distinct()

    grades_options = base_for_options.exclude(grade='').values_list('grade', flat=True).distinct().order_by('grade')
    schools_options = base_for_options.exclude(school='').values_list('school', flat=True).distinct().order_by('school')
    ages_options = base_for_options.exclude(age__isnull=True).values_list('age', flat=True).distinct().order_by('age')

    student_data = []
    for student in students.prefetch_related('enrollments__group'):
        groups_list = [e.group.name for e in student.enrollments.all()]
        student_data.append({
            'student': student,
            'groups': groups_list,
        })

    # Активен ли фильтр
    has_filters = any([search_query, group_filter, grade_filter, school_filter, age_filter])

    context = {
        'student_data': student_data,
        'role': role,
        'search_query': search_query,
        'group_filter': group_filter,
        'grade_filter': grade_filter,
        'school_filter': school_filter,
        'age_filter': age_filter,
        'available_groups': available_groups,
        'grades_options': grades_options,
        'schools_options': schools_options,
        'ages_options': ages_options,
        'has_filters': has_filters,
        'total_found': len(student_data),
        'show_archive': show_archive,
        'archived_count': Student.objects.filter(is_active=False).count() if role in MANAGE_ROLES else 0,
    }

    return render(request, 'dashboard/students_list.html', context)


@crm_view
def add_student(request):
    """Добавление ученика"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('students_list')
    
    form = {}
    if request.method == 'POST':
        form = request.POST
        name = clean_text(request.POST.get('name'), 100)
        try:
            if not name:
                raise FormError('Напишите имя ученика')
            age = parse_age(request.POST.get('age'))
            price = parse_price(request.POST.get('price_per_lesson'), default=Decimal('3000'))
        except FormError as e:
            messages.error(request, str(e))
        else:
            with transaction.atomic():
                student = Student.objects.create(
                    name=name,
                    phone=clean_text(request.POST.get('phone'), 20),
                    parent_name=clean_text(request.POST.get('parent_name'), 100),
                    parent_phone=clean_text(request.POST.get('parent_phone'), 20),
                    school=clean_text(request.POST.get('school'), 200),
                    grade=clean_text(request.POST.get('grade'), 20),
                    age=age,
                    price_per_lesson=price,
                )
                for group in Group.objects.filter(id__in=to_ids(request.POST.getlist('groups')), is_active=True):
                    Enrollment.objects.get_or_create(student=student, group=group)
            messages.success(request, f'Ученик {name} добавлен!')
            return redirect('students_list')
    
    groups = Group.objects.filter(is_active=True)
    context = {
        'groups': groups,
        'form': form,
        'checked_groups': [str(i) for i in to_ids(form.getlist('groups'))] if form else [],
    }
    
    return render(request, 'dashboard/add_student.html', context)


@crm_view
def lesson_history(request, group_id):
    """История уроков группы: просмотр и правка задним числом"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role == 'teacher' and not can_manage_group(request.user, role, group):
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    enrollments = list(Enrollment.objects.filter(group=group).select_related('student'))
    lessons = (Lesson.objects.filter(group=group)
               .select_related('edited_by')
               .prefetch_related('attendances__student')
               .order_by('-date'))
    
    lesson_data = []
    for lesson in lessons:
        rows = {}
        for att in lesson.attendances.all():
            rows[att.student_id] = {
                'student_id': att.student_id, 'name': att.student.name,
                'status': att.status, 'homework': att.homework_done, 'stars': att.stars,
            }
        # Кто уже учился в группе к дате урока, но отметки нет — показываем как «не был»
        for e in enrollments:
            if e.student_id not in rows and timezone.localtime(e.enrolled_at).date() <= lesson.date:
                rows[e.student_id] = {
                    'student_id': e.student_id, 'name': e.student.name,
                    'status': 'absent', 'homework': False, 'stars': 0,
                }
        rows = sorted(rows.values(), key=lambda r: r['name'].lower())
        came = [r for r in rows if r['status'] in ('present', 'late')]
        lesson_data.append({
            'lesson': lesson,
            'rows': rows,
            'present_count': len(came),
            'late_count': sum(1 for r in rows if r['status'] == 'late'),
            'absent_count': len(rows) - len(came),
            'total_count': len(rows),
            'present_students': [r['name'] + (' (опоздал)' if r['status'] == 'late' else '') for r in came],
            'absent_students': [r['name'] for r in rows if r['status'] == 'absent'],
            'homework_count': sum(1 for r in rows if r['homework']),
            'stars_total': sum(r['stars'] for r in rows),
        })
    
    can_edit = can_manage_group(request.user, role, group)
    context = {
        'group': group,
        'lesson_data': lesson_data,
        'role': role,
        'can_edit': can_edit,
        'today': timezone.localdate(),
        # Данные для окна правки урока
        'editor_data': {
            d['lesson'].id: {
                'id': d['lesson'].id,
                'date': d['lesson'].date.strftime('%d.%m.%Y'),
                'topic': d['lesson'].topic,
                'rows': d['rows'],
                'save_url': reverse('save_lesson', args=[d['lesson'].id]),
            }
            for d in lesson_data if not d['lesson'].is_cancelled
        } if can_edit else {},
    }
    
    return render(request, 'dashboard/lesson_history.html', context)


def lesson_or_403(request, lesson_id):
    """Урок и право его править (учитель — только свои группы)"""
    lesson = get_object_or_404(Lesson.objects.select_related('group'), id=lesson_id)
    allowed = can_manage_group(request.user, get_user_role(request.user), lesson.group)
    return lesson, allowed


def safe_next(request, fallback):
    """Куда вернуться после действия (только на свой сайт)"""
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


@crm_view
@require_POST
def save_lesson(request, lesson_id):
    """Правка урока задним числом: посещаемость, ДЗ, звёзды и тема (AJAX, JSON)"""
    lesson, allowed = lesson_or_403(request, lesson_id)
    if not allowed:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    if lesson.is_cancelled:
        return JsonResponse({'success': False, 'error': 'Урок отменён — сначала верните его'})
    try:
        data = json.loads(request.body)
        rows = list(data.get('rows', []))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'success': False, 'error': 'Неверные данные'}, status=400)

    allowed_ids = group_student_ids(lesson.group) | set(lesson.attendances.values_list('student_id', flat=True))
    statuses = dict(Attendance.STATUS_CHOICES)
    for row in rows:
        try:
            student_id = int(row.get('student_id'))
            stars = max(0, min(int(row.get('stars') or 0), 100))
        except (TypeError, ValueError, AttributeError):
            continue
        status = row.get('status')
        if student_id not in allowed_ids or status not in statuses:
            continue
        attendance, _ = Attendance.objects.get_or_create(lesson=lesson, student_id=student_id, defaults={'status': status})
        attendance.status = status
        attendance.homework_done = bool(row.get('homework'))
        attendance.stars = stars
        attendance.save()

    lesson.topic = str(data.get('topic') or '').strip()[:200]
    lesson.edited_by = request.user
    lesson.edited_at = timezone.now()
    lesson.save()
    return JsonResponse({'success': True})


@crm_view
@require_POST
def toggle_lesson_cancel(request, lesson_id):
    """Отменить урок (отметки удаляются, в статистику не попадает) или вернуть его"""
    lesson, allowed = lesson_or_403(request, lesson_id)
    back = safe_next(request, reverse('lesson_history', args=[lesson.group_id]))
    if not allowed:
        messages.error(request, 'Нет доступа')
        return redirect(back)

    if lesson.is_cancelled:
        lesson.is_cancelled = False
        fill_attendance(lesson)
        messages.success(request, f'Урок {lesson.date:%d.%m} вернули — можно отмечать.')
    else:
        lesson.is_cancelled = True
        lesson.attendances.all().delete()
        messages.success(request, f'Урок {lesson.date:%d.%m} отменён: отметки удалены, в статистику он не попадёт.')
    lesson.edited_by = request.user
    lesson.edited_at = timezone.now()
    lesson.save()
    return redirect(back)


@crm_view
@require_POST
def add_past_lesson(request, group_id):
    """Урок за прошедшую дату — если в тот день страницу группы никто не открыл"""
    group = get_object_or_404(Group, id=group_id)
    history = reverse('lesson_history', args=[group.id])
    if not can_manage_group(request.user, get_user_role(request.user), group):
        messages.error(request, 'Нет доступа')
        return redirect(history)
    try:
        day = date.fromisoformat(request.POST.get('date', ''))
    except ValueError:
        messages.error(request, 'Укажите дату урока')
        return redirect(history)
    if day > timezone.localdate():
        messages.error(request, 'Урок в будущем добавить нельзя')
        return redirect(history)

    lesson, created = Lesson.objects.get_or_create(group=group, date=day)
    if created:
        fill_attendance(lesson)
        lesson.edited_by = request.user
        lesson.edited_at = timezone.now()
        lesson.save()
        messages.success(request, f'Урок {day:%d.%m.%Y} добавлен — отметьте, кто был.')
    else:
        messages.info(request, f'Урок {day:%d.%m.%Y} уже есть — открыли его.')
    return redirect(f'{history}?open={lesson.id}#lesson-{lesson.id}')


@crm_view
def search_api(request):
    """Поиск для командной палитры (Ctrl+K): группы и ученики"""
    role = get_user_role(request.user)
    q = request.GET.get('q', '').strip().lower()[:50]
    if not q:
        return JsonResponse({'groups': [], 'students': []})

    groups = Group.objects.filter(is_active=True).select_related('teacher')
    enrollments = Enrollment.objects.select_related('student', 'group')
    if role == 'teacher':
        mine = Q(teacher=request.user) | Q(teachers=request.user)
        groups = groups.filter(mine).distinct()
        enrollments = enrollments.filter(group__in=groups)

    # Сравниваем в Python: в SQLite icontains не понимает регистр кириллицы
    found_groups = [
        {
            'name': g.name,
            'meta': g.teacher.username if g.teacher else '',
            'url': reverse('group_detail', args=[g.id]),
        }
        for g in groups if q in g.name.lower()
    ][:6]

    students = {}
    if role != 'accountant':
        for e in enrollments:
            st = e.student
            if q in st.name.lower() or (st.phone and q in st.phone.replace(' ', '')):
                students.setdefault(st.id, {
                    'name': st.name,
                    'meta': e.group.name,
                    'url': reverse('student_profile', args=[st.id]),
                })
        if role != 'teacher':
            for st in Student.objects.filter(enrollments__isnull=True, is_active=True):
                if q in st.name.lower():
                    students.setdefault(st.id, {
                        'name': st.name,
                        'meta': 'без группы',
                        'url': reverse('student_profile', args=[st.id]),
                    })

    return JsonResponse({'groups': found_groups, 'students': list(students.values())[:8]})


def money(value):
    """36000 → «36 000»"""
    return f'{int(value or 0):,}'.replace(',', '\u00a0')


def next_lesson_label(slots, now):
    """Ближайшее занятие по слотам: «идёт сейчас», «сегодня 16:00», «завтра 16:00», «в пт 16:00»"""
    today = now.date()
    for shift in range(8):
        day = today + timedelta(days=shift)
        for slot in sorted((sl for sl in slots if sl.day_of_week == day.weekday()), key=lambda sl: sl.start_time):
            if shift == 0 and slot.end_time <= now.time():
                continue
            if shift == 0 and slot.start_time <= now.time():
                return 'идёт сейчас', True
            when = 'сегодня' if shift == 0 else 'завтра' if shift == 1 else f'в {DAYS_SHORT[day.weekday()].lower()}'
            return f'{when} {slot.start_time:%H:%M}', False
    return '', False


def shared_individual_groups(groups):
    """Группы, где «индивидуальные» ученики сидят вместе: их стоит разнести по отдельным занятиям"""
    result = []
    for g in groups.annotate(students_count=Count('enrollments', distinct=True)):
        name = g.name.lower()
        looks_individual = 'individ' in name or 'индивид' in name
        if (g.group_type == 'individual' and g.students_count > 1) or (g.group_type == 'group' and looks_individual and g.students_count):
            result.append(g)
    return result


@crm_view
def individual_lessons(request):
    """Индивидуальные ученики: отдельная вкладка с карточкой на каждого"""
    role = get_user_role(request.user)
    can_money = role in MONEY_ROLES
    can_manage = role in MANAGE_ROLES

    groups = Group.objects.filter(is_active=True)
    if role == 'teacher':
        groups = groups.filter(Q(teacher=request.user) | Q(teachers=request.user)).distinct()
    individual = (groups.filter(group_type='individual')
                  .select_related('teacher')
                  .prefetch_related('schedule_slots', 'enrollments__student'))

    stats = {}
    for st in Attendance.objects.filter(lesson__group__in=individual).values('student_id', 'lesson__group_id').annotate(
        total=Count('id'),
        came=Count('id', filter=Q(status__in=['present', 'late'])),
        homework=Count('id', filter=Q(homework_done=True)),
        stars=Sum('stars'),
    ):
        stats[(st['student_id'], st['lesson__group_id'])] = st

    now = timezone.localtime()
    cards, today_lessons = [], []
    periods = {}
    for g in individual:
        slots = list(g.schedule_slots.all())
        y, m = periods.setdefault(g.id, get_current_period(g.cycle_start_day))
        start, end = get_period(y, m, g.cycle_start_day)
        lessons_in_period = count_lessons_in_period(g, start, end)
        next_label, live = next_lesson_label(slots, now)
        for slot in slots:
            if slot.day_of_week == now.weekday():
                state = 'past' if slot.end_time <= now.time() else 'live' if slot.start_time <= now.time() else 'soon'
                for e in g.enrollments.all():
                    today_lessons.append({'slot': slot, 'student': e.student, 'group': g, 'state': state,
                                          'hue': GROUP_HUES[e.student_id % len(GROUP_HUES)]})
        for e in g.enrollments.all():
            student = e.student
            st = stats.get((student.id, g.id), {})
            total = st.get('total', 0)
            price = student.price_per_lesson or Decimal('0')
            cards.append({
                'student': student,
                'group': g,
                'hue': GROUP_HUES[student.id % len(GROUP_HUES)],
                'days': [{'short': d, 'times': [f'{sl.start_time:%H:%M}' for sl in slots if sl.day_of_week == i]} for i, d in enumerate(DAYS_SHORT)],
                'next_label': next_label,
                'live': live,
                'attendance': round(st.get('came', 0) / total * 100) if total else None,
                'homework': round(st.get('homework', 0) / total * 100) if total else None,
                'stars': st.get('stars') or 0,
                'lessons_total': total,
                'price': price,
                'lessons_in_period': lessons_in_period,
                'amount': price * lessons_in_period,
                'price_str': money(price),
                'amount_str': money(price * lessons_in_period),
                'year': y,
                'month': m,
                'month_name': MONTHS_RU[m - 1],
            })

    paid_keys = set(Payment.objects.filter(
        group__in=[c['group'] for c in cards], is_paid=True,
        cycle_number__in={c['year'] * 100 + c['month'] for c in cards},
    ).values_list('student_id', 'group_id', 'cycle_number'))
    for c in cards:
        c['paid'] = (c['student'].id, c['group'].id, c['year'] * 100 + c['month']) in paid_keys
    cards.sort(key=lambda c: c['student'].name.lower())
    today_lessons.sort(key=lambda t: t['slot'].start_time)

    teachers = sorted({c['group'].teacher.username for c in cards if c['group'].teacher})
    context = {
        'role': role,
        'cards': cards,
        'today_lessons': today_lessons,
        'teachers': teachers,
        'can_money': can_money,
        'can_manage': can_manage,
        'week_lessons': sum(len(g.schedule_slots.all()) for g in individual),
        'paid_count': sum(1 for c in cards if c['paid']),
        'expected_total': money(sum(c['amount'] for c in cards)),
        'paid_total': money(sum(c['amount'] for c in cards if c['paid'])),
        'paid_share': round(sum(c['amount'] for c in cards if c['paid']) / sum(c['amount'] for c in cards) * 100)
                      if sum(c['amount'] for c in cards) else 0,
        'shared_groups': shared_individual_groups(Group.objects.filter(is_active=True)) if can_manage else [],
    }
    return render(request, 'dashboard/individual.html', context)


@crm_view
def add_individual(request):
    """Новый индивидуальный ученик: ученик + учитель + расписание одним шагом"""
    role = get_user_role(request.user)
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('individual_lessons')

    if request.method == 'POST':
        student = Student.objects.filter(id=to_int(request.POST.get('student'), 0), is_active=True).first()
        teacher = User.objects.filter(id=to_int(request.POST.get('teacher'), 0), profile__role='teacher').first()
        if not student or not teacher:
            messages.error(request, 'Выберите ученика и учителя')
        else:
            try:
                cycle_start_day = int(request.POST.get('cycle_start_day', 11))
            except (TypeError, ValueError):
                cycle_start_day = 11
            group = Group.objects.create(
                name=f'Индивид. {student.name}'[:100],
                group_type='individual',
                teacher=teacher,
                cycle_start_day=cycle_start_day if 2 <= cycle_start_day <= 28 else 11,
            )
            save_schedule(request, group)
            Enrollment.objects.create(student=student, group=group)
            price = request.POST.get('price', '').replace(' ', '').replace(',', '.')
            if price:
                try:
                    student.price_per_lesson = Decimal(price)
                    student.save(update_fields=['price_per_lesson'])
                except ArithmeticError:
                    messages.error(request, 'Цена за урок не сохранилась — проверьте число')
            messages.success(request, f'{student.name} — теперь индивидуально у {teacher.username}.')
            return redirect('individual_lessons')

    busy = Enrollment.objects.filter(group__group_type='individual', group__is_active=True).values_list('student_id', flat=True)
    context = {
        'role': role,
        'students': Student.objects.filter(is_active=True).exclude(id__in=busy).order_by('name'),
        'teachers': User.objects.filter(profile__role='teacher').order_by('username'),
        'days': list(enumerate(DAYS_SHORT)),
        'selected_student': request.GET.get('student', ''),
    }
    return render(request, 'dashboard/add_individual.html', context)


@crm_view
@require_POST
def split_individual_group(request, group_id):
    """Общую «индивидуальную» группу — в отдельное занятие на каждого ученика (вместе с историей)"""
    role = get_user_role(request.user)
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('individual_lessons')

    shared = get_object_or_404(Group, id=group_id)
    enrollments = list(Enrollment.objects.filter(group=shared).select_related('student'))
    if not enrollments:
        messages.error(request, f'В «{shared.name}» нет учеников')
        return redirect('individual_lessons')
    if len(enrollments) == 1:
        # Один ученик — достаточно сменить тип: группа со всей историей переезжает во вкладку
        shared.group_type = 'individual'
        shared.save(update_fields=['group_type'])
        messages.success(request, f'«{shared.name}» теперь во вкладке «Индивидуальные».')
        return redirect('individual_lessons')

    with transaction.atomic():
        for e in enrollments:
            student = e.student
            group = Group.objects.create(
                name=f'Индивид. {student.name}'[:100],
                group_type='individual',
                teacher=shared.teacher,
                schedule=shared.schedule,
                cycle_start_day=shared.cycle_start_day,
            )
            group.teachers.set(shared.teachers.all())
            group.books.set(shared.books.all())
            for slot in shared.schedule_slots.all():
                ScheduleSlot.objects.create(group=group, day_of_week=slot.day_of_week,
                                            start_time=slot.start_time, end_time=slot.end_time)
            # Переносим ученика со всей историей: запись, оплаты, калибровки, уроки
            Enrollment.objects.filter(id=e.id).update(group=group)
            Payment.objects.filter(student=student, group=shared).update(group=group)
            LevelCalibration.objects.filter(student=student, group=shared).update(group=group)
            # Уроки копируем по одному на исходный (два урока в один день не склеятся);
            # отменённые уроки без отметок — тоже, если они были после записи ученика
            attendances = list(Attendance.objects.filter(student=student, lesson__group=shared))
            old_lessons = Lesson.objects.filter(group=shared).filter(
                Q(id__in=[a.lesson_id for a in attendances]) |
                Q(is_cancelled=True, date__gte=timezone.localtime(e.enrolled_at).date())
            )
            new_lessons = {}
            for old in old_lessons:
                new_lessons[old.id] = Lesson.objects.create(
                    group=group, date=old.date, topic=old.topic, is_cancelled=old.is_cancelled,
                    edited_by=old.edited_by, edited_at=old.edited_at,
                )
            for att in attendances:
                att.lesson = new_lessons[att.lesson_id]
                att.save(update_fields=['lesson'])
        shared.is_active = False
        shared.save(update_fields=['is_active'])

    messages.success(request, f'{len(enrollments)} уч. из «{shared.name}» теперь занимаются индивидуально — история перенесена. '
                              'Расписание скопировано из общей группы: поправьте время у каждого.')
    return redirect('individual_lessons')


@crm_view
def weekly_schedule(request):
    """Расписание на неделю"""
    from django.db.models import Count

    role = get_user_role(request.user)
    
    if role == 'accountant':
        messages.error(request, 'Расписание пока недоступно для вашей роли')
        return redirect('dashboard')
    
    groups = Group.objects.filter(is_active=True)
    if role == 'teacher':
        groups = groups.filter(Q(teacher=request.user) | Q(teachers=request.user))
    groups = (groups.distinct()
              .select_related('teacher')
              .prefetch_related('schedule_slots', 'teachers')
              .annotate(students_count=Count('enrollments', distinct=True)))
    
    days = [
        {'name': 'Понедельник', 'short': 'Пн', 'num': 0},
        {'name': 'Вторник', 'short': 'Вт', 'num': 1},
        {'name': 'Среда', 'short': 'Ср', 'num': 2},
        {'name': 'Четверг', 'short': 'Чт', 'num': 3},
        {'name': 'Пятница', 'short': 'Пт', 'num': 4},
        {'name': 'Суббота', 'short': 'Сб', 'num': 5},
        {'name': 'Воскресенье', 'short': 'Вс', 'num': 6},
    ]
    
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    
    for i, day in enumerate(days):
        day_date = monday + timedelta(days=i)
        day['date'] = day_date.strftime('%d.%m')
        day['is_today'] = (day_date == today)
    
    lessons = []
    teachers = {}
    for group in groups:
        teacher = group.teacher
        teacher_key = teacher.id if teacher else 0
        if teacher_key not in teachers:
            teachers[teacher_key] = {
                'id': teacher_key,
                'name': teacher.username if teacher else 'Без учителя',
            }
        extra_teachers = [t.username for t in group.teachers.all() if t != teacher]
        for slot in group.schedule_slots.all():
            start = slot.start_time.hour * 60 + slot.start_time.minute
            end = slot.end_time.hour * 60 + slot.end_time.minute
            if end <= start:
                end = start + 30
            lessons.append({
                'group': group.name,
                'url': reverse('group_detail', args=[group.id]),
                'type': group.get_group_type_display(),
                'day': slot.day_of_week,
                'start': start,
                'end': end,
                'teacher_id': teacher_key,
                'teacher': teachers[teacher_key]['name'],
                'extra_teachers': extra_teachers,
                'students': group.students_count,
            })
    
    # Видимый диапазон часов: по занятиям, но не уже 9:00–19:00
    # Сетка — от первого занятия недели до последнего (а не всегда с 9 утра), но не меньше 4 часов
    first_hour = min([l['start'] // 60 for l in lessons] or [9])
    last_hour = max([-(-l['end'] // 60) for l in lessons] or [19])
    if last_hour - first_hour < 4:
        last_hour = min(24, first_hour + 4)
    
    now = timezone.localtime()
    context = {
        'days': days,
        'role': role,
        'schedule_json': {
            'lessons': lessons,
            'teachers': sorted(teachers.values(), key=lambda t: t['name'].lower()),
            'firstHour': first_hour,
            'lastHour': last_hour,
            'todayIndex': today.weekday(),
            'nowMinutes': now.hour * 60 + now.minute,
        },
        'lessons_count': len(lessons),
        'groups_count': len(groups),
        'teachers_count': len(teachers),
    }
    
    return render(request, 'dashboard/weekly_schedule.html', context)


@crm_view
def profile(request):
    """Личный кабинет пользователя"""
    role = get_user_role(request.user)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'change_password':
            old_password = request.POST.get('old_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            if not request.user.check_password(old_password):
                messages.error(request, 'Неверный текущий пароль')
            elif new_password != confirm_password:
                messages.error(request, 'Новые пароли не совпадают')
            elif len(new_password) < 6:
                messages.error(request, 'Пароль должен быть не менее 6 символов')
            else:
                request.user.set_password(new_password)
                request.user.save()
                messages.success(request, 'Пароль успешно изменён!')
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
        
        elif action == 'change_phone':
            phone = clean_text(request.POST.get('phone'), 20)
            profile_obj, created = Profile.objects.get_or_create(user=request.user, defaults={'role': role})
            profile_obj.phone = phone
            profile_obj.save()
            messages.success(request, 'Телефон обновлён!')
        
        elif action == 'change_photo':
            photo = request.FILES.get('photo')
            problem = check_image(photo) if photo else 'Выберите файл'
            if problem:
                messages.error(request, problem)
            else:
                profile_obj, created = Profile.objects.get_or_create(user=request.user, defaults={'role': role})
                profile_obj.photo = photo
                profile_obj.save()
                messages.success(request, 'Фото обновлено!')
    
    try:
        user_profile = request.user.profile
    except:
        user_profile = None
    
    context = {
        'role': role,
        'user_profile': user_profile,
    }
    
    return render(request, 'dashboard/profile.html', context)


import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from django.http import HttpResponse


@crm_view
def export_excel(request):
    """Экспорт данных в Excel"""
    role = get_user_role(request.user)
    
    if role not in MONEY_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    wb = openpyxl.Workbook()
    
    header_font = Font(bold=True, color='FFFFFF', size=12)
    header_fill = PatternFill(start_color='4F46E5', end_color='4F46E5', fill_type='solid')
    border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    header_alignment = Alignment(horizontal='center', vertical='center')
    cell_alignment = Alignment(vertical='center')
    
    # Лист 1: ученики — успеваемость и оплаты по новой схеме (уроки в периоде × цена за урок)
    from django.db.models import Count
    MONTHS_SHORT = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    paid_fill = PatternFill(start_color='DCFCE7', end_color='DCFCE7', fill_type='solid')
    unpaid_fill = PatternFill(start_color='FEE2E2', end_color='FEE2E2', fill_type='solid')
    partial_fill = PatternFill(start_color='FEF3C7', end_color='FEF3C7', fill_type='solid')
    muted_font = Font(color='9CA3AF')

    enrollments = list(Enrollment.objects.select_related('student', 'group').order_by('student__name', 'group__name'))
    students_without_group = list(Student.objects.filter(enrollments__isnull=True, is_active=True).order_by('name'))

    # Посещаемость, ДЗ и звёзды — одним запросом по каждой паре «ученик + группа»
    stats = {}
    for st in Attendance.objects.values('student_id', 'lesson__group_id').annotate(
        total=Count('id'),
        present=Count('id', filter=Q(status__in=['present', 'late'])),
        homework=Count('id', filter=Q(homework_done=True)),
        starred=Count('id', filter=Q(stars__gt=0)),
        stars=Sum('stars'),
    ):
        stats[(st['student_id'], st['lesson__group_id'])] = st

    # Последние 12 месяцев оплаты, заканчивая самым поздним текущим периодом среди групп
    current_by_group = {}
    for e in enrollments:
        if e.group_id not in current_by_group:
            current_by_group[e.group_id] = get_current_period(e.group.cycle_start_day)
    last_y, last_m = max(current_by_group.values(), default=get_current_period())
    months = []
    y, m = last_y, last_m
    for _ in range(12):
        months.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    months.reverse()

    payments = {
        (p_student, p_group, p_cycle): (p_paid, p_partial)
        for p_student, p_group, p_cycle, p_paid, p_partial in Payment.objects.filter(
            cycle_number__in=[y * 100 + m for y, m in months]
        ).values_list('student_id', 'group_id', 'cycle_number', 'is_paid', 'is_partial')
    }

    def payment_status(paid_partial):
        if paid_partial is None:
            return '❌ Не оплачено', unpaid_fill
        paid, partial = paid_partial
        if paid:
            return '✅ Оплачено', paid_fill
        if partial:
            return '🟡 Частично', partial_fill
        return '❌ Не оплачено', unpaid_fill

    ws1 = wb.active
    ws1.title = 'Ученики'
    headers1 = [
        'Ученик', 'Телефон', 'Телефон родителя', 'Группа',
        'Посещаемость', 'ДЗ выполнено', 'Уроки со звёздами', 'Звёзд всего',
        'Цена за урок (₸)', 'Уроков в текущем месяце', 'К оплате за текущий месяц (₸)', 'Текущий месяц',
    ] + [f'{MONTHS_SHORT[m - 1]} {y}' for y, m in months]
    for col, header in enumerate(headers1, 1):
        cell = ws1.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = border
    ws1.row_dimensions[1].height = 36

    def write_row(row, values, fills=None):
        for col, value in enumerate(values, 1):
            cell = ws1.cell(row=row, column=col, value=value)
            cell.alignment = cell_alignment
            cell.border = border
            if 5 <= col <= 7 and isinstance(value, float):
                cell.number_format = '0%'
            elif col in (9, 11) and isinstance(value, (int, float)):
                cell.number_format = '#,##0'
            if fills and col in fills:
                cell.fill = fills[col]
            if value == '—':
                cell.font = muted_font

    def share(part, total):
        return part / total if total else '—'

    row = 2
    for e in enrollments:
        student, group = e.student, e.group
        st = stats.get((student.id, group.id), {})
        total = st.get('total', 0)

        cur_y, cur_m = current_by_group[group.id]
        cur_start, cur_end = get_period(cur_y, cur_m, group.cycle_start_day)
        price = float(student.price_per_lesson or 0)
        lessons = count_lessons_in_period(group, cur_start, cur_end)
        cur_text, cur_fill = payment_status(payments.get((student.id, group.id, cur_y * 100 + cur_m)))

        values = [
            student.name, student.phone or '—', student.parent_phone or '—', group.name,
            share(st.get('present', 0), total), share(st.get('homework', 0), total),
            share(st.get('starred', 0), total), st.get('stars') or 0,
            price, lessons, price * lessons, cur_text,
        ]
        fills = {12: cur_fill}
        for i, (y, m) in enumerate(months):
            col = len(headers1) - len(months) + 1 + i
            paid_partial = payments.get((student.id, group.id, y * 100 + m))
            if paid_partial is not None:
                text, fill = payment_status(paid_partial)
                fills[col] = fill
            elif (y, m) > (cur_y, cur_m):
                text = ''  # период ещё не начался
            elif not is_enrolled_by(e, get_period(y, m, group.cycle_start_day)[1]):
                text = '—'  # ещё не учился в этой группе
            else:
                text, fill = payment_status(None)
                fills[col] = fill
            values.append(text)
        write_row(row, values, fills)
        row += 1

    for student in students_without_group:
        write_row(row, [student.name, student.phone or '—', student.parent_phone or '—', '—'] + ['—'] * (len(headers1) - 4))
        row += 1

    widths1 = [26, 18, 18, 22, 14, 14, 14, 12, 14, 14, 18, 16] + [14] * len(months)
    for i, width in enumerate(widths1, 1):
        ws1.column_dimensions[get_column_letter(i)].width = width
    ws1.freeze_panes = 'B2'
    ws1.auto_filter.ref = ws1.dimensions
    
    # Лист 3
    ws3 = wb.create_sheet('Группы')
    
    headers3 = ['Название', 'Учитель', 'Расписание', 'Учеников', 'Активна']
    for col, header in enumerate(headers3, 1):
        cell = ws3.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
    
    groups = Group.objects.all()
    for row, group in enumerate(groups, 2):
        data = [
            group.name,
            group.teacher.username if group.teacher else '—',
            group.schedule or '',
            group.enrollments.count(),
            'Да' if group.is_active else 'Нет',
        ]
        for col, value in enumerate(data, 1):
            cell = ws3.cell(row=row, column=col, value=value)
            cell.alignment = cell_alignment
            cell.border = border
    
    for letter, width in [('A', 25), ('B', 20), ('C', 30), ('D', 15), ('E', 10)]:
        ws3.column_dimensions[letter].width = width
    
    # Лист 4
    ws4 = wb.create_sheet('Посещаемость')
    
    headers4 = ['Дата', 'Группа', 'Ученик', 'Статус']
    for col, header in enumerate(headers4, 1):
        cell = ws4.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
    
    attendances = Attendance.objects.select_related('lesson', 'lesson__group', 'student').order_by('-lesson__date')
    for row, attendance in enumerate(attendances, 2):
        status_map = {
            'present': 'Присутствовал',
            'absent': 'Отсутствовал',
            'late': 'Опоздал',
        }
        data = [
            attendance.lesson.date.strftime('%d.%m.%Y'),
            attendance.lesson.group.name,
            attendance.student.name,
            status_map.get(attendance.status, attendance.status),
        ]
        for col, value in enumerate(data, 1):
            cell = ws4.cell(row=row, column=col, value=value)
            cell.alignment = cell_alignment
            cell.border = border
            if col == 4:
                if attendance.status == 'present':
                    cell.fill = PatternFill(start_color='DCFCE7', end_color='DCFCE7', fill_type='solid')
                elif attendance.status == 'absent':
                    cell.fill = PatternFill(start_color='FEE2E2', end_color='FEE2E2', fill_type='solid')
    
    for letter, width in [('A', 15), ('B', 25), ('C', 25), ('D', 20)]:
        ws4.column_dimensions[letter].width = width
    
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="english_center_report.xlsx"'
    
    wb.save(response)
    return response


@crm_view
def download_backup(request):
    """Админ скачивает свежую копию базы — на всякий случай держать у себя"""
    if get_user_role(request.user) not in ('admin', 'developer'):
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    import tempfile
    from pathlib import Path
    from django.http import FileResponse
    from .management.commands.backup_db import make_backup
    tmp = Path(tempfile.mkdtemp()) / 'db.sqlite3'
    make_backup(tmp)
    name = f'enjoy-english-{timezone.localtime():%Y-%m-%d-%H%M}.sqlite3'
    # Файл удалится сам, когда закроется (на Linux — сразу после открытия)
    handle = open(tmp, 'rb')
    try:
        tmp.unlink()
        tmp.parent.rmdir()
    except OSError:
        pass
    return FileResponse(handle, as_attachment=True, filename=name, content_type='application/octet-stream')


@crm_view
def payment_management(request):
    """Редирект на новую страницу оплат"""
    return redirect('payments_page')


STATS_RANGES = (6, 12, 24)
MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']


def cycles_back(year, month, count):
    """count месяцев подряд, заканчивая (year, month): [(год, месяц), ...] по возрастанию"""
    result = []
    for _ in range(count):
        result.append((year, month))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return result[::-1]


def month_short(year, month, with_year=False):
    return f'{MONTHS_SHORT[month - 1]} {year % 100:02d}' if with_year else MONTHS_SHORT[month - 1]


def percent_change(now, before):
    """Рост в процентах; None — сравнивать не с чем"""
    if not before:
        return None
    return round((now - before) / before * 100)


def income_stats(months, group_type='all'):
    """Статистика доходов за последние months месяцев (текущий — последний, он ещё идёт).

    Деньги считаются по месяцам оплаты (как на странице «Оплаты»): ждали = цена × уроки по расписанию,
    собрано = отмеченные оплаты. Месяцы до первой отметки оплаты в CRM не считаются — тогда оплаты
    ещё не вели, и все ученики выглядели бы должниками. Для сравнения берутся months месяцев до периода.
    """
    cur_year, cur_month = get_current_period()
    current = cur_year * 100 + cur_month
    all_cycles = cycles_back(cur_year, cur_month, months * 2)
    prev_cycles, range_cycles = all_cycles[:months], all_cycles[months:]
    first_cycle = Payment.objects.filter(is_paid=True).aggregate(first=Min('cycle_number'))['first']

    payments = {}
    for p in Payment.objects.filter(cycle_number__gte=all_cycles[0][0] * 100 + all_cycles[0][1], cycle_number__lte=current) \
            .select_related('student'):
        payments.setdefault((p.cycle_number, p.group_id), {})[p.student_id] = p
    groups = Group.objects.filter(Q(is_active=True) | Q(id__in={gid for (_, gid) in payments}))
    if group_type in ('group', 'individual'):
        groups = groups.filter(group_type=group_type)
    groups = list(groups.select_related('teacher').prefetch_related('schedule_slots', 'enrollments__student'))

    def empty():
        return {'expected': 0, 'collected': 0, 'paid': 0, 'total': 0, 'debt': 0, 'cash': 0}

    month_totals = {y * 100 + m: empty() for y, m in all_cycles}
    has_data = {y * 100 + m: bool(first_cycle) and y * 100 + m >= first_cycle for y, m in all_cycles}
    # На графиках — с первого месяца, где оплаты уже отмечали
    shown_cycles = [(y, m) for y, m in range_cycles if has_data[y * 100 + m]] or range_cycles
    range_set = {y * 100 + m for y, m in range_cycles}
    debtors, group_stats, teachers, by_type = {}, [], {}, {'group': 0, 'individual': 0}

    for g in groups:
        per_month = {}
        for y, m in all_cycles:
            cycle = y * 100 + m
            if not has_data[cycle] or (not g.is_active and (cycle, g.id) not in payments):
                continue
            rows = month_rows(g, y, m, payments.get((cycle, g.id), {}))[0]
            if not rows:
                continue
            t = empty()
            for r in rows:
                amount = int(r['amount'])
                t['expected'] += amount
                t['total'] += 1
                if r['is_paid']:
                    t['collected'] += amount
                    t['paid'] += 1
                    if r['method'] == 'cash':
                        t['cash'] += amount
                elif amount and cycle < current:
                    t['debt'] += amount
                    if cycle in range_set:
                        d = debtors.setdefault(r['student'].id, {'student': r['student'], 'amount': 0, 'months': []})
                        d['amount'] += amount
                        d['months'].append({'year': y, 'month': m, 'group': g, 'amount': amount})
            per_month[cycle] = t
            for key in t:
                month_totals[cycle][key] += t[key]

        in_range = [per_month[y * 100 + m] for y, m in range_cycles if y * 100 + m in per_month]
        if not in_range:
            continue
        done = [per_month[c] for c in sorted(per_month) if c in range_set and c < current]
        collected = sum(t['collected'] for t in in_range)
        expected_done = sum(t['expected'] for t in done)
        # Рост: последние 3 закрытых месяца против 3 предыдущих
        recent, earlier = done[-3:], done[-6:-3]
        trend = percent_change(sum(t['collected'] for t in recent) / len(recent),
                               sum(t['collected'] for t in earlier) / len(earlier)) if recent and earlier else None
        first_month = min(per_month) if per_month else None
        paid_count = sum(t['paid'] for t in in_range)
        # Текущий месяц не рисуем — он ещё идёт, и линия в конце всегда «падала» бы
        spark = [per_month.get(y * 100 + m, {}).get('collected') for y, m in shown_cycles if y * 100 + m < current]
        group_stats.append({
            'group': g,
            'collected': collected,
            'collected_str': money(collected),
            'rate': round(sum(t['collected'] for t in done) / expected_done * 100) if expected_done else None,
            'debt': sum(t['debt'] for t in done),
            'debt_str': money(sum(t['debt'] for t in done)),
            'trend': trend,
            # Новая — оплаты по группе пошли позже, чем по остальным, и сравнивать пока не с чем
            'is_new': trend is None and first_month is not None and first_cycle is not None and first_month > first_cycle,
            'students': sum(1 for e in g.enrollments.all() if e.student.is_active),
            'avg_check': money(collected / paid_count) if paid_count else '',
            'spark': spark,
            'months': [{
                'label': month_short(y, m, with_year=(m == 1)), 'name': f'{MONTHS_RU[m - 1]} {y}',
                'year': y, 'month': m, 'is_current': y * 100 + m == current,
                **per_month.get(y * 100 + m, empty()),
            } for y, m in shown_cycles],
        })
        by_type[g.group_type if g.group_type in by_type else 'group'] += collected
        teacher = g.teacher
        tkey = teacher.id if teacher else 0
        entry = teachers.setdefault(tkey, {'name': teacher.username if teacher else '', 'collected': 0, 'groups': 0, 'students': 0})
        entry['collected'] += collected
        entry['groups'] += 1
        entry['students'] += group_stats[-1]['students']

    group_stats.sort(key=lambda x: -x['collected'])
    total = sum(x['collected'] for x in group_stats)
    top = max([x['collected'] for x in group_stats] + [1])
    for x in group_stats:
        x['share'] = round(x['collected'] / total * 100) if total else 0
        x['bar'] = round(x['collected'] / top * 100)
        x['spark_points'] = sparkline_points(x['spark'])
        # Столбики по месяцам внутри строки группы: высота от самого большого месяца этой группы
        peak = max([m['expected'] for m in x['months']] + [m['collected'] for m in x['months']] + [1])
        for m in x['months']:
            m['track'] = round(m['expected'] / peak * 100)
            m['fill'] = round(m['collected'] / peak * 100)
            m['collected_str'], m['expected_str'] = money(m['collected']), money(m['expected'])

    # Месяцы периода: для графика и таблицы
    chart = []
    for y, m in shown_cycles:
        cycle = y * 100 + m
        t = month_totals[cycle]
        chart.append({
            'label': month_short(y, m, with_year=(m == 1 or (y, m) == shown_cycles[0])),
            'name': f'{MONTHS_RU[m - 1]} {y}', 'year': y, 'month': m,
            'is_current': cycle == current, 'has_data': has_data[cycle],
            'rate': round(t['collected'] / t['expected'] * 100) if t['expected'] else None,
            'collected_str': money(t['collected']), 'expected_str': money(t['expected']), 'debt_str': money(t['debt']),
            'cash_str': money(t['cash']),
            **t,
        })
    done = [c for c in chart if not c['is_current'] and c['has_data']]
    before = [month_totals[y * 100 + m] for y, m in prev_cycles if has_data[y * 100 + m]]
    done_collected = sum(c['collected'] for c in done)
    done_expected = sum(c['expected'] for c in done)
    done_paid = sum(c['paid'] for c in done)
    avg_month = done_collected / len(done) if done else 0
    avg_before = sum(t['collected'] for t in before) / len(before) if before else 0
    rate = round(done_collected / done_expected * 100) if done_expected else None
    before_expected = sum(t['expected'] for t in before)
    rate_before = round(sum(t['collected'] for t in before) / before_expected * 100) if before_expected else None
    best = max(done, key=lambda c: c['collected']) if done else None
    now = chart[-1]

    debtor_list = sorted(debtors.values(), key=lambda d: -d['amount'])
    for d in debtor_list:
        d['amount_str'] = money(d['amount'])
        d['months'].sort(key=lambda x: (x['year'], x['month']))
        d['first'] = d['months'][0]
        d['month_labels'] = ', '.join(dict.fromkeys(f'{MONTHS_SHORT[x["month"] - 1].lower()}' for x in d['months']))
        d['groups'] = list(dict.fromkeys(x['group'].name for x in d['months']))

    teacher_list = sorted(teachers.values(), key=lambda t: -t['collected'])
    top_teacher = max([t['collected'] for t in teacher_list] + [1])
    for t in teacher_list:
        t['collected_str'] = money(t['collected'])
        t['share'] = round(t['collected'] / total * 100) if total else 0
        t['bar'] = round(t['collected'] / top_teacher * 100)

    # Банк и наличные за период
    cash = sum(c['cash'] for c in chart)
    cash_share = round(cash / total * 100) if total else 0

    first_label = ''
    if first_cycle and first_cycle > range_cycles[0][0] * 100 + range_cycles[0][1]:
        first_label = f'{MONTHS_GEN[first_cycle % 100 - 1]} {first_cycle // 100}'

    return {
        'range_cycles': range_cycles,
        'chart': chart,
        'groups': group_stats,
        'debtors': debtor_list,
        'teachers': teacher_list,
        'total': total,
        'total_str': money(total),
        'avg_month': int(avg_month),
        'avg_month_str': money(avg_month),
        'avg_change': percent_change(avg_month, avg_before) if done else None,
        'rate': rate,
        'rate_change': rate - rate_before if rate is not None and rate_before is not None else None,
        'debt': sum(d['amount'] for d in debtor_list),
        'debt_str': money(sum(d['amount'] for d in debtor_list)),
        'best': best,
        'best_str': money(best['collected']) if best else '',
        'avg_check_str': money(done_collected / done_paid) if done_paid else '',
        'avg_paying': round(done_paid / len(done)) if done else 0,
        'now': now,
        'now_collected_str': money(now['collected']),
        'now_expected_str': money(now['expected']),
        'by_type': by_type,
        'individual_share': round(by_type['individual'] / total * 100) if total else 0,
        'group_share': 100 - round(by_type['individual'] / total * 100) if total else 0,
        'group_total_str': money(by_type['group']),
        'individual_total_str': money(by_type['individual']),
        'first_label': first_label,
        'has_data': bool(first_cycle),
        'cash': cash,
        'cash_str': money(cash),
        'bank_str': money(total - cash),
        'cash_share': cash_share,
        'bank_share': 100 - cash_share if total else 0,
    }


def sparkline_points(values, width=96, height=28):
    """Точки ломаной для маленького графика в строке группы; пропуски (нет данных) — None"""
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return ''
    top = max(known) or 1
    step = width / (len(values) - 1)
    return ' '.join(f'{i * step:.1f},{height - 2 - (v / top) * (height - 4):.1f}'
                    for i, v in enumerate(values) if v is not None)


def stats_params(request):
    try:
        months = int(request.GET.get('months', 12))
    except (TypeError, ValueError):
        months = 12
    months = months if months in STATS_RANGES else 12
    group_type = request.GET.get('type') if request.GET.get('type') in ('group', 'individual') else 'all'
    return months, group_type


@crm_view
def accountant_stats(request):
    """Статистика доходов: динамика за период, сравнение с прошлым, долги, учителя и группы"""
    role = get_user_role(request.user)

    if role not in ['accountant', 'developer', 'admin']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    months, group_type = stats_params(request)
    stats = income_stats(months, group_type)
    chart_data = {
        'labels': [c['label'] for c in stats['chart']],
        'names': [c['name'] for c in stats['chart']],
        'collected': [c['collected'] for c in stats['chart']],
        'expected': [c['expected'] for c in stats['chart']],
        'paid': [c['paid'] for c in stats['chart']],
        'total': [c['total'] for c in stats['chart']],
        'debt': [c['debt'] for c in stats['chart']],
        'cash': [c['cash'] for c in stats['chart']],
        'current': [c['is_current'] for c in stats['chart']],
        'text': {
            'collected': 'Собрано', 'expected': 'Ожидалось', 'paid': 'Оплатили', 'total': 'Всего учеников',
            'debt': 'Не оплатили', 'cash': 'Из них наличными', 'current': 'месяц ещё идёт', 'students': 'уч.', 'tenge': '₸',
            'thousand': 'тыс', 'million': 'млн',
        },
    }
    context = {
        'role': role,
        'months': months,
        'group_type': group_type,
        'ranges': STATS_RANGES,
        'stats': stats,
        'chart_data': chart_data,
        'start': stats['chart'][0],
        'end': stats['chart'][-1],
    }
    return render(request, 'dashboard/accountant_stats.html', context)


@crm_view
def export_income_excel(request):
    """Excel со статистикой доходов за тот же период: месяцы, группы, должники, оплаты"""
    role = get_user_role(request.user)

    if role not in ['accountant', 'developer', 'admin']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    months, group_type = stats_params(request)
    stats = income_stats(months, group_type)
    thin = Side(style='thin', color='E8E5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def sheet(ws, headers, rows, widths):
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill(start_color='F97316', end_color='F97316', fill_type='solid')
            cell.alignment = Alignment(horizontal='center')
            cell.border = border
        for r, values in enumerate(rows, 2):
            for col, value in enumerate(values, 1):
                cell = ws.cell(row=r, column=col, value=value)
                cell.border = border
                if isinstance(value, (int, float)) and headers[col - 1].endswith('₸'):
                    cell.number_format = '#,##0'
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = 'A2'

    wb = openpyxl.Workbook()
    sheet(wb.active, ['Месяц', 'Ожидалось, ₸', 'Собрано, ₸', 'Банк, ₸', 'Наличные, ₸', 'Собрано, %', 'Оплатили', 'Учеников', 'Не оплатили, ₸'],
          [[c['name'] + (' (идёт)' if c['is_current'] else ''), c['expected'], c['collected'], c['collected'] - c['cash'], c['cash'],
            c['rate'], c['paid'], c['total'], c['debt']] for c in stats['chart']],
          [20, 16, 16, 14, 14, 12, 12, 12, 16])
    wb.active.title = 'По месяцам'
    sheet(wb.create_sheet('По группам'), ['Группа', 'Учитель', 'Тип', 'Учеников', 'Собрано, ₸', 'Доля, %', 'Собрано вовремя, %', 'Долг, ₸'],
          [[x['group'].name, x['group'].teacher.username if x['group'].teacher else '', x['group'].get_group_type_display(),
            x['students'], x['collected'], x['share'], x['rate'], x['debt']] for x in stats['groups']],
          [28, 18, 16, 12, 16, 10, 18, 14])
    sheet(wb.create_sheet('Должники'), ['Ученик', 'Телефон', 'Телефон родителя', 'Группы', 'Месяцы', 'Долг, ₸'],
          [[d['student'].name, d['student'].phone, d['student'].parent_phone, ', '.join(d['groups']),
            ', '.join(f'{MONTHS_RU[x["month"] - 1]} {x["year"]}' for x in d['months']), d['amount']] for d in stats['debtors']],
          [26, 18, 18, 28, 30, 14])
    first = stats['range_cycles'][0]
    paid = Payment.objects.filter(is_paid=True, cycle_number__gte=first[0] * 100 + first[1]) \
        .select_related('student', 'group', 'marked_by').order_by('cycle_number', 'group__name', 'student__name')
    if group_type in ('group', 'individual'):
        paid = paid.filter(group__group_type=group_type)
    sheet(wb.create_sheet('Оплаты'), ['Месяц', 'Ученик', 'Группа', 'Сумма, ₸', 'Способ', 'Дата оплаты', 'Кто отметил'],
          [[f'{MONTHS_RU[p.cycle_number % 100 - 1]} {p.cycle_number // 100}', p.student.name, p.group.name,
            int(p.paid_amount or p.amount), p.get_method_display(),
            timezone.localtime(p.paid_at).strftime('%d.%m.%Y') if p.paid_at else '',
            p.marked_by.username if p.marked_by else ''] for p in paid],
          [18, 26, 26, 14, 12, 14, 18])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="income_{months}m.xlsx"'
    wb.save(response)
    return response


@crm_view
def add_group(request):
    """Создание группы"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = clean_text(request.POST.get('name'), 100)
        group_type = request.POST.get('group_type') if request.POST.get('group_type') in ('group', 'individual') else 'group'
        teacher_id = to_int(request.POST.get('teacher'))
        if teacher_id and not User.objects.filter(id=teacher_id).exists():
            teacher_id = None
        
        if name and teacher_id:
            try:
                cycle_start_day = int(request.POST.get('cycle_start_day', 11))
                if not (2 <= cycle_start_day <= 28):
                    cycle_start_day = 11
            except (ValueError, TypeError):
                cycle_start_day = 11

            group = Group.objects.create(
                name=name,
                group_type=group_type,
                teacher_id=teacher_id,
                cycle_start_day=cycle_start_day,
                is_active=True,
                level=read_level(request, name) if group_type == 'group' else None,
            )
            
            save_schedule(request, group)
            
            additional = User.objects.filter(id__in=to_ids(request.POST.getlist('additional_teachers'))).exclude(id=teacher_id)
            if additional:
                group.teachers.set(additional)
            messages.success(request, f'Группа "{name}" создана!')
            return redirect('individual_lessons' if group_type == 'individual' else 'dashboard')
        else:
            messages.error(request, 'Заполните название и выберите учителя')
    
    teachers = User.objects.filter(profile__role='teacher')
    
    context = {
        'teachers': teachers,
        'levels': Group.LEVEL_CHOICES,
    }
    
    return render(request, 'dashboard/add_group.html', context)


@crm_view
@require_POST
def remove_student_from_group(request, student_id, group_id):
    """Убрать ученика из группы (оплаты и посещаемость остаются в истории)"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    group = get_object_or_404(Group, id=group_id)
    
    Enrollment.objects.filter(student=student, group=group).delete()
    messages.success(request, f'{student.name} убран(а) из группы {group.name}')
    
    return redirect('group_detail', group_id=group.id)


@crm_view
@require_POST
def archive_student(request, student_id):
    """Ученик ушёл: в архив вместо удаления — история оплат и посещаемости сохраняется"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    Enrollment.objects.filter(student=student).delete()
    student.is_active = False
    student.archived_at = timezone.now()
    student.save(update_fields=['is_active', 'archived_at'])
    messages.success(request, f'{student.name} — в архиве. Оплаты и посещаемость сохранены, вернуть можно в «Ученики → Архив».')
    
    return redirect('students_list')


@crm_view
@require_POST
def bulk_archive_students(request):
    """Несколько учеников сразу в архив (отмечены галочками в списке учеников)"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    ids = [int(i) for i in request.POST.getlist('student_ids') if i.isdigit()]
    students = Student.objects.filter(id__in=ids, is_active=True)
    count = students.count()
    if not count:
        messages.error(request, 'Не выбрано ни одного ученика')
        return redirect('students_list')
    
    with transaction.atomic():
        Enrollment.objects.filter(student__in=students).delete()
        students.update(is_active=False, archived_at=timezone.now())
    messages.success(request, f'В архив перенесено учеников: {count}. Оплаты и посещаемость сохранены.')
    
    return redirect('students_list')


@crm_view
@require_POST
def restore_student(request, student_id):
    """Вернуть ученика из архива и сразу предложить группы"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    student.is_active = True
    student.archived_at = None
    student.save(update_fields=['is_active', 'archived_at'])
    messages.success(request, f'{student.name} снова учится — выберите группы.')
    
    return redirect('add_existing_student_to_group', student_id=student.id)


@crm_view
@require_POST
def archive_group(request, group_id):
    """Группа закрылась: в архив вместо удаления — уроки и оплаты сохраняются"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    group.is_active = False
    group.save(update_fields=['is_active'])
    messages.success(request, f'Группа «{group.name}» — в архиве. Уроки и оплаты сохранены.')
    
    return redirect('dashboard')


@crm_view
@require_POST
def restore_group(request, group_id):
    """Вернуть группу из архива"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    group.is_active = True
    group.save(update_fields=['is_active'])
    messages.success(request, f'Группа «{group.name}» снова активна.')
    
    return redirect('group_detail', group_id=group.id)


@crm_view
def add_existing_student_to_group(request, student_id):
    """Добавить существующего ученика в группу"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        group_ids = to_ids(request.POST.getlist('groups'))
        
        for group in Group.objects.filter(id__in=group_ids, is_active=True):
            Enrollment.objects.get_or_create(student=student, group=group)
        
        # Записали в группу — значит снова учится
        if group_ids and not student.is_active:
            student.is_active = True
            student.archived_at = None
            student.save(update_fields=['is_active', 'archived_at'])
        
        messages.success(request, f'{student.name} добавлен в выбранные группы')
        return redirect('students_list')
    
    groups = Group.objects.filter(is_active=True)
    current_groups = Enrollment.objects.filter(student=student).values_list('group_id', flat=True)
    available_groups = groups.exclude(id__in=current_groups)
    
    context = {
        'student': student,
        'available_groups': available_groups,
    }
    
    return render(request, 'dashboard/add_to_group.html', context)


@crm_view
def toggle_book_status(request, enrollment_id=None):
    """Переключить статус книги у ученика"""
    if request.method == 'POST':
        role = get_user_role(request.user)

        if role not in ['admin', 'teacher', 'developer']:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})

        action = request.POST.get('action')
        book_id = request.POST.get('book_id')
        comment = request.POST.get('comment', '').strip()

        enrollment_ids = request.POST.get('enrollment_ids', '')
        if enrollment_ids:
            for eid in enrollment_ids.split(','):
                try:
                    enrollment = Enrollment.objects.select_related('group').get(id=int(eid))
                    if not can_manage_group(request.user, role, enrollment.group):
                        continue
                    if action == 'need_book':
                        enrollment.book_needed = True
                        enrollment.has_book = False
                        enrollment.book_id = int(book_id) if book_id else None
                        enrollment.book_comment = comment
                    elif action == 'has_book':
                        enrollment.has_book = True
                        enrollment.book_needed = False
                    enrollment.save()
                except Exception:
                    pass
            return JsonResponse({'success': True})

        if enrollment_id:
            enrollment = get_object_or_404(Enrollment, id=enrollment_id)
            if not can_manage_group(request.user, role, enrollment.group):
                return JsonResponse({'success': False, 'error': 'Нет доступа'})
            if action == 'need_book':
                enrollment.book_needed = True
                enrollment.has_book = False
                enrollment.book_id = int(book_id) if book_id else None
                enrollment.book_comment = comment
            elif action == 'has_book':
                enrollment.has_book = True
                enrollment.book_needed = False
            enrollment.save()
            return JsonResponse({'success': True})

    return JsonResponse({'success': False})


@crm_view
def books_status(request):
    """Сетка всех книг с обложками"""
    role = get_user_role(request.user)

    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    books = Book.objects.all()

    book_stats = []
    for book in books:
        groups_using = Group.objects.filter(books=book).count()
        students_with_book = Enrollment.objects.filter(has_book=True, book=book).count()
        students_need_book = Enrollment.objects.filter(book_needed=True, book=book).count()

        book_stats.append({
            'book': book,
            'groups_using': groups_using,
            'students_with_book': students_with_book,
            'students_need_book': students_need_book,
            'missing': students_need_book - book.quantity,
        })

    book_requests = Enrollment.objects.filter(
        book_needed=True
    ).select_related('student', 'group', 'group__teacher', 'book').order_by('group__name', 'student__name')

    context = {
        'book_stats': book_stats,
        'book_requests': book_requests,
        'role': role,
    }

    return render(request, 'dashboard/books_status.html', context)


@crm_view
def book_detail(request, book_id):
    """Детали книги: у кого есть, кому нужно"""
    role = get_user_role(request.user)

    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    book = get_object_or_404(Book, id=book_id)

    students_with_book = Enrollment.objects.filter(
        has_book=True, book=book
    ).select_related('student', 'group').order_by('student__name')

    students_need_book = Enrollment.objects.filter(
        book_needed=True, book=book
    ).select_related('student', 'group').order_by('student__name')

    context = {
        'book': book,
        'students_with_book': students_with_book,
        'students_need_book': students_need_book,
        'role': role,
    }

    return render(request, 'dashboard/book_detail.html', context)


@crm_view
def edit_student(request, student_id):
    """Редактирование ученика"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('students_list')
    
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        name = clean_text(request.POST.get('name'), 100)
        try:
            if not name:
                raise FormError('Имя ученика не может быть пустым')
            age = parse_age(request.POST.get('age'))
            price = parse_price(request.POST.get('price_per_lesson'), default=Decimal('0'))
        except FormError as e:
            # Показываем форму снова с тем, что ввели (в базу ничего не пишем)
            messages.error(request, str(e))
            for field in ('phone', 'parent_name', 'parent_phone', 'school', 'grade'):
                setattr(student, field, request.POST.get(field, ''))
            student.name = name or student.name
            return render(request, 'dashboard/edit_student.html', {
                'student': student,
                'groups': Group.objects.filter(is_active=True),
                'student_groups': to_ids(request.POST.getlist('groups')),
            })
        student.name = name
        student.phone = clean_text(request.POST.get('phone'), 20)
        student.parent_name = clean_text(request.POST.get('parent_name'), 100)
        student.parent_phone = clean_text(request.POST.get('parent_phone'), 20)
        student.school = clean_text(request.POST.get('school'), 200)
        student.grade = clean_text(request.POST.get('grade'), 20)
        student.age = age
        student.price_per_lesson = price
        with transaction.atomic():
            student.save()
            # Записи в группы меняем точечно: у оставшихся сохраняются дата записи
            # (по ней считаются оплаты прошлых месяцев) и отметки о книгах
            wanted = set(Group.objects.filter(id__in=to_ids(request.POST.getlist('groups')), is_active=True)
                         .values_list('id', flat=True))
            current = Enrollment.objects.filter(student=student)
            # Архивные группы в форме не показываются — их записи не трогаем
            current.filter(group__is_active=True).exclude(group_id__in=wanted).delete()
            have = set(current.values_list('group_id', flat=True))
            for group_id in wanted - have:
                Enrollment.objects.create(student=student, group_id=group_id)
        
        messages.success(request, f'Ученик {student.name} обновлён!')
        return_url = request.POST.get('return_url', '')
        if return_url and url_has_allowed_host_and_scheme(return_url, allowed_hosts={request.get_host()}):
            return redirect(return_url)
        return redirect('students_list')
    
    groups = Group.objects.filter(is_active=True)
    student_groups = Enrollment.objects.filter(student=student).values_list('group_id', flat=True)
    
    context = {
        'student': student,
        'groups': groups,
        'student_groups': student_groups,
    }
    
    return render(request, 'dashboard/edit_student.html', context)


@crm_view
def edit_group(request, group_id):
    """Редактирование группы"""
    role = get_user_role(request.user)
    
    if role not in MANAGE_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    
    if request.method == 'POST':
        name = clean_text(request.POST.get('name'), 100)
        if not name:
            messages.error(request, 'Название группы не может быть пустым')
            return redirect('edit_group', group_id=group.id)
        group.name = name
        if request.POST.get('group_type') in ('group', 'individual'):
            group.group_type = request.POST['group_type']
        teacher_id = to_int(request.POST.get('teacher'))
        if teacher_id and User.objects.filter(id=teacher_id).exists():
            group.teacher_id = teacher_id
        # Основной учитель не дублируется в дополнительных
        additional = User.objects.filter(id__in=to_ids(request.POST.getlist('additional_teachers'))).exclude(id=group.teacher_id)
        group.teachers.set(additional)
        try:
            cycle_start_day = int(request.POST.get('cycle_start_day', group.cycle_start_day))
            if not (2 <= cycle_start_day <= 28):
                cycle_start_day = group.cycle_start_day
        except (ValueError, TypeError):
            cycle_start_day = group.cycle_start_day
        group.cycle_start_day = cycle_start_day
        group.level = read_level(request, group.name) if group.group_type == 'group' else None
        group.save()
        
        save_schedule(request, group)
        
        messages.success(request, f'Группа "{group.name}" обновлена!')
        return_url = request.POST.get('return_url', '')
        if return_url and url_has_allowed_host_and_scheme(return_url, allowed_hosts={request.get_host()}):
            return redirect(return_url)
        return redirect('individual_lessons' if group.group_type == 'individual' else 'dashboard')
    
    teachers = User.objects.filter(profile__role='teacher')
    schedule_slots = {str(s.day_of_week): s for s in group.schedule_slots.all()}
    
    context = {
        'group': group,
        'teachers': teachers,
        'schedule_slots': schedule_slots,
        'levels': Group.LEVEL_CHOICES,
    }
    
    return render(request, 'dashboard/edit_group.html', context)


from datetime import date

MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def get_period(year, month, cycle_start_day=11):
    """Период: с cycle_start_day этого месяца по (cycle_start_day-1) следующего (вкл)"""
    if not (2 <= cycle_start_day <= 28):
        cycle_start_day = 11
    start = date(year, month, cycle_start_day)
    end_day = cycle_start_day - 1
    if month == 12:
        end = date(year + 1, 1, end_day)
    else:
        end = date(year, month + 1, end_day)
    return start, end


def get_current_period(cycle_start_day=11):
    """Текущий цикл по дате"""
    if not (2 <= cycle_start_day <= 28):
        cycle_start_day = 11
    today = timezone.localdate()
    cycle_start_this = date(today.year, today.month, cycle_start_day)
    if today >= cycle_start_this:
        return today.year, today.month
    else:
        if today.month == 1:
            return today.year - 1, 12
        return today.year, today.month - 1


def is_enrolled_by(enrollment, period_end):
    """Ученик уже был в группе к концу периода (новых не включаем в прошлые месяцы)"""
    return timezone.localtime(enrollment.enrolled_at).date() <= period_end


MONTHS_SHORT = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']


def compact_money(value):
    """2300000 → «2,3 млн», 850000 → «850 тыс» — для подписей графика"""
    value = int(value)
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.').replace('.', ',') + ' млн'
    if value >= 1000:
        return f'{round(value / 1000)} тыс'
    return str(value)


def paid_label(payment):
    """«12.10 · admin» — когда и кто отметил оплату"""
    if not payment or not payment.is_paid or not payment.paid_at:
        return ''
    who = f' · {payment.marked_by.username}' if payment.marked_by else ''
    return f'{timezone.localtime(payment.paid_at):%d.%m}{who}'


def month_rows(group, year, month, cycle_payments):
    """Кто платит группе за месяц: записанные к концу периода и все, у кого оплата уже есть (даже если ушли).
    cycle_payments — {student_id: Payment} этой группы за этот месяц."""
    start, end = get_period(year, month, group.cycle_start_day)
    lessons = count_lessons_in_period(group, start, end)
    rows, seen = [], set()

    def add(student, payment, left):
        price = student.price_per_lesson or Decimal('0')
        is_paid = bool(payment and payment.is_paid)
        amount = (payment.paid_amount or payment.amount) if is_paid else price * lessons
        rows.append({'student': student, 'payment': payment, 'is_paid': is_paid, 'amount': amount,
                     'price': price, 'left': left, 'paid_label': paid_label(payment),
                     'method': payment.method if is_paid else 'bank',
                     'amount_int': int(amount), 'amount_str': money(amount), 'price_str': money(price),
                     # Цена могла поменяться после оплаты — тогда формулу «цена × уроки» не показываем
                     'formula_ok': bool(price) and amount == price * lessons})
        seen.add(student.id)

    for e in group.enrollments.all():
        payment = cycle_payments.get(e.student_id)
        if payment or is_enrolled_by(e, end):
            add(e.student, payment, left=False)
    for student_id, payment in cycle_payments.items():
        if student_id not in seen and payment.is_paid:
            add(payment.student, payment, left=True)
    rows.sort(key=lambda r: r['student'].name.lower())
    return rows, lessons, start, end


@crm_view
def payments_page(request):
    """Касса: один месяц целиком — сумма, график года, все группы и ученики с отметкой оплаты"""
    role = get_user_role(request.user)
    if role not in MONEY_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    today = timezone.localdate()
    current_year, current_month = get_current_period()
    try:
        year = int(request.GET.get('year', current_year))
        month = int(request.GET.get('month', current_month if year == current_year else 1))
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        year, month = current_year, current_month

    # Все оплаты за год одним запросом: {(цикл, группа): {ученик: Payment}}
    payments = {}
    for p in Payment.objects.filter(cycle_number__gte=year * 100 + 1, cycle_number__lte=year * 100 + 12) \
            .select_related('student', 'marked_by'):
        payments.setdefault((p.cycle_number, p.group_id), {})[p.student_id] = p
    groups_with_payments = {gid for (_, gid) in payments}
    groups = list(Group.objects.filter(Q(is_active=True) | Q(id__in=groups_with_payments))
                  .select_related('teacher')
                  .prefetch_related('schedule_slots', 'enrollments__student'))
    groups.sort(key=lambda g: (g.group_type == 'individual', g.name.lower()))

    # График года: сколько ждали и сколько собрали в каждом месяце
    year_bars = []
    for m in range(1, 13):
        cycle = year * 100 + m
        expected = collected = paid = total = 0
        for g in groups:
            if not g.is_active and (cycle, g.id) not in payments:
                continue
            rows = month_rows(g, year, m, payments.get((cycle, g.id), {}))[0]
            expected += sum(r['amount'] for r in rows)
            collected += sum(r['amount'] for r in rows if r['is_paid'])
            paid += sum(1 for r in rows if r['is_paid'])
            total += len(rows)
        year_bars.append({
            'num': m, 'short': MONTHS_SHORT[m - 1], 'name': MONTHS_RU[m - 1],
            'expected': int(expected), 'collected': int(collected), 'paid': paid, 'total': total,
            'is_selected': m == month,
            'is_future': (year, m) > (current_year, current_month),
        })
    max_expected = max([b['expected'] for b in year_bars] + [1])
    for b in year_bars:
        # Целые проценты: дробь с запятой (ru-локаль) сломала бы CSS
        b['track'] = max(round(b['expected'] / max_expected * 100), 1 if b['expected'] else 0)
        b['fill'] = max(round(b['collected'] / max_expected * 100), 1 if b['collected'] else 0)

    # Выбранный месяц по группам
    cycle = year * 100 + month
    cards = []
    for g in groups:
        if not g.is_active and (cycle, g.id) not in payments:
            continue
        rows, lessons, start, end = month_rows(g, year, month, payments.get((cycle, g.id), {}))
        if not rows:
            continue
        lesson_days = {s.day_of_week for s in g.schedule_slots.all()}
        strip = []
        d = start
        while d <= end:
            strip.append({'date': d, 'lesson': d.weekday() in lesson_days, 'today': d == today, 'first': d.day == 1})
            d += timedelta(days=1)
        paid_rows = [r for r in rows if r['is_paid']]
        cards.append({
            'group': g,
            'hue': GROUP_HUES[g.id % len(GROUP_HUES)],
            'rows': rows,
            'lessons': lessons,
            'start': start,
            'end': end,
            'strip': strip,
            'paid_count': len(paid_rows),
            'collected': money(sum(r['amount'] for r in paid_rows)),
            'expected': money(sum(r['amount'] for r in rows)),
            'all_paid': len(paid_rows) == len(rows),
        })

    all_rows = [r for c in cards for r in c['rows']]
    expected = sum(r['amount'] for r in all_rows)
    collected = sum(r['amount'] for r in all_rows if r['is_paid'])
    cash = sum(r['amount'] for r in all_rows if r['is_paid'] and r['method'] == 'cash')
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    period_start, period_end = get_period(year, month)

    context = {
        'role': role,
        'year': year,
        'month': month,
        'month_name': MONTHS_RU[month - 1],
        'month_lower': MONTHS_RU[month - 1].lower(),
        'period_start': period_start,
        'period_end': period_end,
        'is_current': (year, month) == (current_year, current_month),
        'current_year': current_year,
        'current_month': current_month,
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'cards': cards,
        'year_bars': year_bars,
        'expected': int(expected),
        'collected': int(collected),
        'expected_str': money(expected),
        'collected_str': money(collected),
        'left_str': money(expected - collected),
        'cash': int(cash),
        'cash_str': money(cash),
        'share': round(collected / expected * 100) if expected else 0,
        'students_total': len(all_rows),
        'paid_total': sum(1 for r in all_rows if r['is_paid']),
        'wait_total': sum(1 for r in all_rows if not r['is_paid']),
        'max_expected': max_expected,
        'axis_max': compact_money(max_expected),
        'axis_mid': compact_money(max_expected / 2),
        'no_price': sum(1 for r in all_rows if not r['price'] and not r['is_paid']),
        'search_query': request.GET.get('search', '')[:100],
    }
    return render(request, 'dashboard/payments.html', context)


@crm_view
@require_POST
def toggle_student_payment(request, group_id):
    """AJAX: отметить/отменить оплату"""
    role = get_user_role(request.user)
    if role not in MONEY_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})

    group = get_object_or_404(Group, id=group_id)
    student = get_object_or_404(Student, id=to_int(request.POST.get('student_id'), 0))
    try:
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Неверный месяц'}, status=400)
    cycle_num = year * 100 + month

    period_start, period_end = get_period(year, month, group.cycle_start_day)
    amount = calculate_cycle_amount(student, group, period_start, period_end)

    method = request.POST.get('method') if request.POST.get('method') in ('bank', 'cash') else 'bank'
    payment, created = Payment.objects.get_or_create(
        student=student,
        group=group,
        cycle_number=cycle_num,
        defaults={
            'start_date': period_start,
            'end_date': period_end,
            'amount': amount,
            'is_paid': True,
            'paid_amount': amount,
            'paid_at': timezone.now(),
            'marked_by': request.user,
            'method': method,
        }
    )

    if not created:
        payment.is_paid = not payment.is_paid
        if payment.is_paid:
            payment.amount = amount
            payment.paid_amount = amount
            payment.paid_at = timezone.now()
            payment.marked_by = request.user
            payment.method = method
        else:
            payment.paid_amount = 0
            payment.paid_at = None
            payment.marked_by = None
            payment.method = 'bank'
        payment.save()

    return JsonResponse({
        'success': True,
        'is_paid': payment.is_paid,
        'amount': int(payment.paid_amount if payment.is_paid else amount),
        'paid_label': paid_label(payment),
        'method': payment.method,
    })


@crm_view
@require_POST
def set_payment_method(request, group_id):
    """AJAX: оплачено банком или наличными (у уже принятой оплаты)"""
    if get_user_role(request.user) not in MONEY_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    method = request.POST.get('method')
    if method not in ('bank', 'cash'):
        return JsonResponse({'success': False, 'error': 'Неверный способ оплаты'}, status=400)
    try:
        cycle = int(request.POST.get('year')) * 100 + int(request.POST.get('month'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Неверный месяц'}, status=400)
    payment = Payment.objects.filter(group_id=group_id, student_id=to_int(request.POST.get('student_id'), 0),
                                     cycle_number=cycle, is_paid=True).first()
    if not payment:
        return JsonResponse({'success': False, 'error': 'Сначала примите оплату'})
    payment.method = method
    payment.save(update_fields=['method'])
    return JsonResponse({'success': True, 'method': method})


@crm_view
def book_reader(request, book_id):
    """Reader — просмотр PDF книги"""
    role = get_user_role(request.user)
    book = get_object_or_404(Book, id=book_id)
    
    context = {
        'role': role,
        'book': book,
    }
    
    return render(request, 'dashboard/book_reader.html', context)

@crm_view
@require_POST
def mark_homework(request, group_id):
    """Отметка домашки (AJAX)"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    if not can_manage_group(request.user, role, group):
        return JsonResponse({'success': False, 'error': 'Нет доступа'})

    try:
        student_id = int(request.POST.get('student_id'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Неверный ученик'})
    if student_id not in group_student_ids(group):
        return JsonResponse({'success': False, 'error': 'Ученик не из этой группы'})
    done = request.POST.get('done') == '1'

    lesson, _ = Lesson.objects.get_or_create(group=group, date=timezone.localdate())
    if lesson.is_cancelled:
        return JsonResponse({'success': False, 'error': 'Урок отменён'})

    attendance, _ = Attendance.objects.get_or_create(lesson=lesson, student_id=student_id, defaults={'status': 'absent'})
    attendance.homework_done = done
    attendance.save()

    return JsonResponse({'success': True, 'homework_done': done})


@crm_view
@require_POST
def create_calibration(request):
    """Создание калибровки уровня (AJAX)"""
    role = get_user_role(request.user)
    direction = request.POST.get('direction')
    comment = request.POST.get('comment', '').strip()

    if direction not in ['up', 'down']:
        return JsonResponse({'success': False, 'error': 'Неверное направление'})

    student = get_object_or_404(Student, id=to_int(request.POST.get('student_id'), 0))
    group = get_object_or_404(Group, id=to_int(request.POST.get('group_id'), 0))
    if not can_manage_group(request.user, role, group):
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    if student.id not in group_student_ids(group):
        return JsonResponse({'success': False, 'error': 'Ученик не из этой группы'})

    with transaction.atomic():
        cal = LevelCalibration.objects.create(
            student=student,
            group=group,
            direction=direction,
            comment=comment,
            created_by=request.user,
        )
        # Админ увидит её во «Сообщениях» и сможет сразу перевести ученика
        InboxMessage.objects.create(category='calibration', calibration=cal, student=student, group=group,
                                    text=comment, author=request.user)

    return JsonResponse({'success': True, 'id': cal.id})


@crm_view
def student_profile(request, student_id):
    """Профиль ученика"""
    role = get_user_role(request.user)
    student = get_object_or_404(Student, id=student_id)

    if role == 'teacher':
        has_access = Enrollment.objects.filter(
            student=student
        ).filter(
            Q(group__teacher=request.user) | Q(group__teachers=request.user)
        ).exists()
        if not has_access:
            messages.error(request, 'У вас нет доступа к этому ученику')
            return redirect('dashboard')

    enrollments = Enrollment.objects.filter(student=student).select_related('group', 'group__teacher')

    attendances = Attendance.objects.filter(student=student).select_related('lesson', 'lesson__group')

    total_lessons = attendances.count()
    present_count = attendances.filter(status__in=['present', 'late']).count()
    homework_count = attendances.filter(homework_done=True).count()
    total_stars = attendances.aggregate(total=Sum('stars'))['total'] or 0

    participation_percent = round((present_count / total_lessons) * 100) if total_lessons > 0 else 0
    homework_percent = round((homework_count / total_lessons) * 100) if total_lessons > 0 else 0

    def get_color(percent):
        if percent < 50:
            return {'stroke': '#ef4444', 'text': 'text-red-500', 'bg': 'bg-red-50 dark:bg-red-900/20'}
        elif percent < 80:
            return {'stroke': '#f59e0b', 'text': 'text-amber-500', 'bg': 'bg-amber-50 dark:bg-amber-900/20'}
        else:
            return {'stroke': '#10b981', 'text': 'text-green-500', 'bg': 'bg-green-50 dark:bg-green-900/20'}

    participation_color = get_color(participation_percent)
    homework_color = get_color(homework_percent)

    recent_lessons = attendances.order_by('-lesson__date')[:10]

    calibrations = LevelCalibration.objects.filter(student=student).order_by('-created_at')[:5]

    context = {
        'role': role,
        'student': student,
        'enrollments': enrollments,
        'total_lessons': total_lessons,
        'present_count': present_count,
        'homework_count': homework_count,
        'total_stars': total_stars,
        'participation_percent': participation_percent,
        'homework_percent': homework_percent,
        'participation_color': participation_color,
        'homework_color': homework_color,
        'recent_lessons': recent_lessons,
        'calibrations': calibrations,
    }

    return render(request, 'dashboard/student_profile.html', context)

@crm_view
def give_stars(request, group_id):
    """Выдача звёзд (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False})
    
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    if not can_manage_group(request.user, role, group):
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    today = timezone.localdate()
    lesson, _ = Lesson.objects.get_or_create(group=group, date=today)
    if lesson.is_cancelled:
        return JsonResponse({'success': False, 'error': 'Урок отменён'})
    enrolled_ids = group_student_ids(group)

    # stars = "id:кол-во,id:кол-во" — сколько звёзд у ученика за сегодняшний урок
    count = 0
    for pair in request.POST.get('stars', '').split(','):
        sid, _, value = pair.partition(':')
        try:
            sid = int(sid)
            value = max(0, min(int(value or 0), 100))
        except ValueError:
            continue
        if sid not in enrolled_ids:
            continue
        attendance = Attendance.objects.filter(lesson=lesson, student_id=sid).first()
        if attendance is None:
            if value == 0:
                continue
            attendance = Attendance(lesson=lesson, student_id=sid, status='absent')
        if attendance.stars != value:
            count += max(0, value - attendance.stars)
            attendance.stars = value
            attendance.save()

    return JsonResponse({'success': True, 'count': count})

@crm_view
def star_rating(request):
    """Рейтинг учеников по звёздам"""
    role = get_user_role(request.user)

    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    from django.db.models import Sum

    students = Student.objects.filter(is_active=True).annotate(
        stars_count=Sum('attendances__stars')
    ).filter(stars_count__gt=0).order_by('-stars_count', 'name')

    podium = list(students[:3])
    others = list(students[3:])
    has_podium = len(podium) > 0

    context = {
        'role': role,
        'podium': podium,
        'others': others,
        'has_podium': has_podium,
        'podium_count': len(podium),
    }

    return render(request, 'dashboard/star_rating.html', context)