"""Сообщения админу об учениках: комментарии и жалобы учителей, калибровки уровня.

Учитель (и менеджер) пишет про одного ученика — сообщение попадает во вкладку «Сообщения»
админа и менеджеров со счётчиком непрочитанных (входящие общие: прочитал один — прочитано у всех). Калибровка уровня приходит туда же: в ней сразу
подобраны группы сильнее или слабее, и ученика можно перевести одной кнопкой.
"""
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Enrollment, Group, InboxMessage, LevelCalibration, Payment, Student
from .views import MANAGE_ROLES, can_manage_group, crm_view, get_current_period, get_user_role, to_int

# Кому приходят сообщения
INBOX_ROLES = ('admin', 'developer', 'manager')
# Кто пишет (учителю — только про учеников своих групп)
SENDER_ROLES = ('teacher',) + MANAGE_ROLES
CATEGORY_ORDER = ['complaint', 'comment', 'calibration']


def unread_count():
    return InboxMessage.objects.filter(read_at__isnull=True).count()


def inbox_context(request):
    """Контекст-процессор: {{ inbox_unread }} — для счётчика в меню у админа и менеджеров"""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or get_user_role(user) not in INBOX_ROLES:
        return {}
    return {'inbox_unread': unread_count()}


def when_label(dt, now=None):
    """«только что», «5 мин назад», «3 ч назад», «вчера в 14:30», «12.10 в 14:30»"""
    now = now or timezone.now()
    local = timezone.localtime(dt)
    minutes = int((now - dt).total_seconds() // 60)
    if minutes < 1:
        return 'только что'
    if minutes < 60:
        return f'{minutes} мин назад'
    if minutes < 12 * 60 and local.date() == timezone.localtime(now).date():
        return f'{minutes // 60} ч назад'
    if local.date() == timezone.localtime(now).date():
        return f'сегодня в {local:%H:%M}'
    if local.date() == timezone.localtime(now).date() - timedelta(days=1):
        return f'вчера в {local:%H:%M}'
    return f'{local:%d.%m} в {local:%H:%M}'


def sender_students(user, role):
    """Про кого можно написать: учителю — ученики его групп, остальным — все, кто учится. [(группа, [ученики])]"""
    groups = Group.objects.filter(is_active=True)
    if role == 'teacher':
        groups = groups.filter(Q(teacher=user) | Q(teachers=user)).distinct()
    result = []
    for g in groups.prefetch_related('enrollments__student').order_by('group_type', 'name'):
        students = sorted((e.student for e in g.enrollments.all() if e.student.is_active), key=lambda s: s.name.lower())
        if students:
            result.append((g, students))
    return result


# ---------------- Калибровка: куда перевести ----------------

def level_suggestions(student, group, direction, limit=4):
    """Группы для перевода: при «выше» — уровнем выше, при «ниже» — ниже, ближайшие первыми.
    Если у групп не указан уровень — все остальные группы по порядку уровней."""
    taken = set(Enrollment.objects.filter(student=student).values_list('group_id', flat=True))
    candidates = [g for g in Group.objects.filter(is_active=True, group_type='group')
                  .select_related('teacher').prefetch_related('schedule_slots')
                  .annotate(size=Count('enrollments', filter=Q(enrollments__student__is_active=True)))
                  if g.id not in taken and (not group or g.id != group.id)]
    current = group.level if group else None
    if current:
        if direction == 'up':
            picked = sorted((g for g in candidates if g.level and g.level > current), key=lambda g: (g.level, g.name))
        else:
            picked = sorted((g for g in candidates if g.level and g.level < current), key=lambda g: (-g.level, g.name))
        if picked:
            return picked[:limit], False
    # Уровни не указаны — подсказать не можем, показываем все по уровню
    return sorted(candidates, key=lambda g: (g.level or 99, g.name.lower()))[:limit], True


def move_student(student, source, target, user):
    """Перевести ученика из группы в группу: история уроков остаётся, оплата текущего месяца переезжает с ним"""
    year, month = get_current_period()
    cycle = year * 100 + month
    with transaction.atomic():
        if source:
            Enrollment.objects.filter(student=student, group=source).delete()
        Enrollment.objects.get_or_create(student=student, group=target)
        if not student.is_active:
            student.is_active, student.archived_at = True, None
            student.save(update_fields=['is_active', 'archived_at'])
        moved = False
        if source and not Payment.objects.filter(student=student, group=target, cycle_number=cycle).exists():
            moved = bool(Payment.objects.filter(student=student, group=source, cycle_number=cycle).update(group=target))
    return moved


# ---------------- Страница ----------------

def decorate(items, now):
    """Подписи и подсказки для карточек сообщений"""
    since = now - timedelta(days=60)
    for m in items:
        m.when = when_label(m.created_at, now)
        m.direction = m.calibration.direction if m.calibration_id else ''
        m.suggestions, m.no_levels, m.repeats = [], False, 0
        if m.category == 'calibration' and not m.resolved_at:
            m.suggestions, m.no_levels = level_suggestions(m.student, m.group, m.direction)
            # Сколько раз за 2 месяца ученика калибровали в ту же сторону
            m.repeats = LevelCalibration.objects.filter(student=m.student, direction=m.direction, created_at__gte=since).count()
            for g in m.suggestions:
                g.schedule_short = g.get_schedule_display()
    return items


@crm_view
def inbox_page(request):
    """Админу и менеджерам — входящие с фильтром по категории (менеджер может и написать сам);
    учителю — форма и отправленные"""
    role = get_user_role(request.user)
    now = timezone.now()
    if role in INBOX_ROLES:
        category = request.GET.get('cat') if request.GET.get('cat') in CATEGORY_ORDER else 'all'
        base = InboxMessage.objects.select_related('student', 'group', 'author', 'read_by', 'resolved_by', 'calibration')
        unread = {c: 0 for c in CATEGORY_ORDER}
        for row in InboxMessage.objects.filter(read_at__isnull=True).values('category').annotate(n=Count('id')):
            unread[row['category']] = row['n']
        totals = {c: 0 for c in CATEGORY_ORDER}
        for row in InboxMessage.objects.values('category').annotate(n=Count('id')):
            totals[row['category']] = row['n']
        items = base if category == 'all' else base.filter(category=category)
        new = decorate(list(items.filter(read_at__isnull=True).order_by('-created_at')), now)
        old = decorate(list(items.filter(read_at__isnull=False).order_by('-created_at')[:100]), now)
        can_compose = role == 'manager'   # менеджер и сам пишет про учеников
        return render(request, 'dashboard/inbox.html', {
            'role': role, 'is_inbox': True, 'category': category, 'new': new, 'old': old,
            'unread': unread, 'unread_total': sum(unread.values()), 'totals': totals, 'all_total': sum(totals.values()),
            'all_groups': Group.objects.filter(is_active=True, group_type='group').order_by('level', 'name'),
            'can_compose': can_compose,
            'choices': sender_students(request.user, role) if can_compose else [],
            'preselect': request.GET.get('student', ''),
        })
    if role not in SENDER_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    sent = decorate(list(InboxMessage.objects.filter(author=request.user)
                         .select_related('student', 'group', 'calibration').order_by('-created_at')[:100]), now)
    return render(request, 'dashboard/inbox.html', {
        'role': role, 'is_inbox': False, 'sent': sent, 'choices': sender_students(request.user, role),
        'preselect': request.GET.get('student', ''),
    })


@crm_view
@require_POST
def send_message(request):
    """Учитель пишет админу про ученика: комментарий или жалоба (AJAX или обычная форма)"""
    role = get_user_role(request.user)
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('accept', '')

    def answer(ok, text, status=200):
        if is_ajax:
            return JsonResponse({'success': ok, 'message' if ok else 'error': text}, status=status)
        (messages.success if ok else messages.error)(request, text)
        return redirect('inbox_page')

    if role not in SENDER_ROLES:
        return answer(False, 'Нет доступа', 403)
    category = request.POST.get('category')
    if category not in ('comment', 'complaint'):
        return answer(False, 'Выберите: комментарий или жалоба', 400)
    text = request.POST.get('text', '').strip()
    if len(text) < 3:
        return answer(False, 'Напишите, что случилось')
    if len(text) > 2000:
        return answer(False, 'Слишком длинное сообщение — до 2000 символов')
    student = Student.objects.filter(id=to_int(request.POST.get('student_id'), 0)).first()
    if not student:
        return answer(False, 'Выберите ученика')
    # Группа — та, в которой учитель видит ученика (и есть доступ)
    enrollments = Enrollment.objects.filter(student=student).select_related('group')
    group = None
    group_id = request.POST.get('group_id')
    if group_id:
        group = next((e.group for e in enrollments if str(e.group_id) == str(group_id)), None)
    if role == 'teacher':
        allowed = [e.group for e in enrollments if can_manage_group(request.user, role, e.group)]
        if not allowed:
            return answer(False, 'Это не ваш ученик', 403)
        group = group if group in allowed else allowed[0]
    elif not group:
        group = enrollments[0].group if enrollments else None
    InboxMessage.objects.create(category=category, student=student, group=group, text=text, author=request.user)
    return answer(True, 'Отправлено админу')


def back(request):
    """Вернуться на ту же вкладку сообщений (только на свой сайт)"""
    url = request.POST.get('next', '')
    return redirect(url if url.startswith('/messages/') else 'inbox_page')


def _message_for_inbox(request, message_id):
    if get_user_role(request.user) not in INBOX_ROLES:
        return None
    return get_object_or_404(InboxMessage.objects.select_related('student', 'group'), id=message_id)


@crm_view
@require_POST
def toggle_read(request, message_id):
    """Отметить прочитанным / вернуть в новые"""
    message = _message_for_inbox(request, message_id)
    if not message:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    if message.read_at:
        message.read_at, message.read_by = None, None
    else:
        message.read_at, message.read_by = timezone.now(), request.user
    message.save(update_fields=['read_at', 'read_by'])
    return JsonResponse({'success': True, 'read': bool(message.read_at), 'unread': unread_count()})


@crm_view
@require_POST
def read_all(request):
    if get_user_role(request.user) not in INBOX_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    items = InboxMessage.objects.filter(read_at__isnull=True)
    if request.POST.get('cat') in CATEGORY_ORDER:
        items = items.filter(category=request.POST['cat'])
    count = items.update(read_at=timezone.now(), read_by=request.user)
    messages.success(request, f'Прочитано сообщений: {count}')
    return back(request)


@crm_view
@require_POST
def resolve_calibration(request, message_id):
    """Калибровка: перевести ученика в выбранную группу или оставить в текущей"""
    message = _message_for_inbox(request, message_id)
    if not message:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    if message.category != 'calibration' or message.resolved_at:
        return back(request)
    now = timezone.now()
    if request.POST.get('action') == 'move':
        target = get_object_or_404(Group, id=to_int(request.POST.get('group_id'), 0), is_active=True)
        moved_payment = move_student(message.student, message.group, target, request.user)
        message.resolution = f'Переведён в «{target.name}»'
        note = ' Оплата за текущий месяц перенесена в новую группу.' if moved_payment else ''
        messages.success(request, f'{message.student.name} теперь в группе «{target.name}».{note}')
    else:
        message.resolution = 'Оставлен в группе'
        messages.success(request, f'{message.student.name} остаётся в группе.')
    message.resolved_at, message.resolved_by = now, request.user
    if not message.read_at:
        message.read_at, message.read_by = now, request.user
    message.save(update_fields=['resolution', 'resolved_at', 'resolved_by', 'read_at', 'read_by'])
    return back(request)
