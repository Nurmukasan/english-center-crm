"""Рассылки в WhatsApp: кому, что и сколько — и сама отправка.

Получатели собираются на сервере (клиенту не доверяем): при создании рассылки
список считается заново по тем же фильтрам, что видел пользователь.
"""
import json
import re
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Broadcast, BroadcastMessage, Group, Payment, WhatsAppSettings
from .views import (
    MONTHS_RU, crm_view, get_current_period, get_period, get_user_role, money, month_rows, to_int,
)
from .whatsapp import (
    WhatsAppError, chat_id_for_phone, client, connection_status, format_phone, normalize_phone, wa_link, waha_status,
)

SEND_ROLES = ('admin', 'developer', 'accountant')
SETUP_ROLES = ('admin', 'developer')
AUDIENCES = [code for code, _ in Broadcast.AUDIENCE_CHOICES]
DEFAULT_AUDIENCE = {'payment': 'parent', 'news': 'group'}

TEMPLATES = {
    ('payment', 'personal'): (
        '{приветствие}\n'
        'Напоминаем об оплате занятий в Enjoy English за {месяц} ({период}):\n'
        '{детали}\n'
        'Итого к оплате: {сумма}\n'
        'Спасибо! 🙏'
    ),
    ('payment', 'group'): (
        'Здравствуйте! 👋\n'
        'Напоминаем об оплате занятий группы {группа} за {месяц} ({период}).\n'
        'Сумму для вашего ребёнка можно уточнить у администратора.\n'
        'Спасибо! 🙏'
    ),
    ('payment', 'teacher'): (
        '{приветствие}\n'
        'Ещё не оплатили за {месяц}:\n'
        '{детали}\n'
        'Итого: {сумма}. Напомните, пожалуйста, на уроке 🙏'
    ),
    ('news', 'personal'): '{приветствие}\n\n',
    ('news', 'group'): 'Здравствуйте! 📢\n\n',
    ('news', 'teacher'): '{приветствие}\n\n',
}

VARIABLES = [
    ('приветствие', 'Здравствуйте, Айгуль!'),
    ('имя', 'имя ученика'),
    ('родитель', 'имя родителя'),
    ('группа', 'название группы'),
    ('учитель', 'учитель'),
    ('месяц', 'октябрь'),
    ('период', '11.10–10.11'),
    ('сумма', 'к оплате'),
    ('детали', 'расчёт по урокам'),
]
VAR_RE = re.compile(r'\{([а-яё]+)\}')


def template_key(kind, audience):
    return (kind, audience if audience in ('group', 'teacher') else 'personal')


def render_text(template, variables):
    """Подставить {переменные}; неизвестные остаются как есть"""
    text = VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def default_month(today=None):
    """Месяц для напоминания: текущий период, а за неделю до следующего — уже следующий"""
    today = today or timezone.localdate()
    year, month = get_current_period()
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    if (get_period(next_year, next_month)[0] - today).days <= 7:
        return next_year, next_month
    return year, month


def join_names(names):
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' и ' + names[-1]


def first_name(name):
    return (name or '').split()[0] if name else ''


# ---------------- Получатели ----------------

def filtered_groups(group_ids=None, group_type='all'):
    groups = Group.objects.filter(is_active=True)
    if group_ids:
        groups = groups.filter(id__in=group_ids)
    if group_type in ('group', 'individual'):
        groups = groups.filter(group_type=group_type)
    return list(groups.select_related('teacher', 'teacher__profile')
                .prefetch_related('schedule_slots', 'enrollments__student', 'teachers__profile')
                .order_by('group_type', 'name'))


def payment_entries(groups, year, month, only_unpaid):
    """Кто сколько должен за месяц: {student_id: {student, lines, total}} по выбранным группам"""
    cycle = year * 100 + month
    payments = {}
    for p in Payment.objects.filter(group__in=groups, cycle_number=cycle).select_related('student', 'marked_by'):
        payments.setdefault(p.group_id, {})[p.student_id] = p
    entries = {}
    for g in groups:
        rows, lessons, start, end = month_rows(g, year, month, payments.get(g.id, {}))
        for r in rows:
            if r['left'] or (only_unpaid and r['is_paid']):
                continue
            entry = entries.setdefault(r['student'].id, {
                'student': r['student'], 'lines': [], 'total': Decimal('0'), 'start': start, 'end': end, 'groups': [],
            })
            entry['lines'].append({'group': g, 'lessons': lessons, 'price': r['price'], 'amount': r['amount'], 'paid': r['is_paid']})
            entry['groups'].append(g)
            if not r['is_paid']:
                entry['total'] += r['amount']
    return entries


def detail_line(student_name, line):
    who = f'{student_name} — '
    if line['paid']:
        return f'• {who}{line["group"].name}: оплачено ✓'
    if line['price']:
        return (f'• {who}{line["group"].name}: {line["lessons"]} ур. × {money(line["price"])} ₸ = '
                f'{money(line["amount"])} ₸')
    return f'• {who}{line["group"].name}: сумма уточняется'


def period_label(start, end):
    return f'{start:%d.%m}–{end:%d.%m}'


def build_recipients(kind, audience, group_ids=None, group_type='all', only_unpaid=True, year=None, month=None, mode='manual'):
    """Список получателей с переменными для текста.

    Каждый: key (стабильный id для галочек), name, sub, phone, chat_id, ok (можно отправить),
    warn (почему нельзя / на что обратить внимание), amount, vars.
    """
    groups = filtered_groups(group_ids, group_type)
    month_name = MONTHS_RU[month - 1].lower() if month else ''
    recipients = []

    if audience == 'group':
        for g in groups:
            # У индивидуальных занятий обычно нет группового чата — без привязки их пропускаем
            if g.group_type == 'individual' and not g.whatsapp_chat_id:
                continue
            start, end = get_period(year, month, g.cycle_start_day) if month else (None, None)
            chat = g.whatsapp_chat_id
            recipients.append({
                'key': f'g{g.id}', 'name': g.name, 'sub': g.teacher.username if g.teacher else '',
                'phone': '', 'chat_id': chat, 'ok': True,
                'warn': '' if chat else 'Чат не привязан — текст можно будет скопировать',
                'manual_only': not chat, 'amount': None, 'group_id': g.id, 'student_id': None,
                'vars': {
                    'приветствие': 'Здравствуйте!', 'группа': g.name, 'имя': '', 'родитель': '',
                    'учитель': g.teacher.username if g.teacher else '', 'месяц': month_name,
                    'период': period_label(start, end) if start else '', 'сумма': '', 'детали': '',
                },
            })
        return recipients

    if audience == 'teacher':
        entries = payment_entries(groups, year, month, True) if kind == 'payment' else {}
        teachers = {}
        for g in groups:
            for t in [g.teacher, *g.teachers.all()]:
                if t:
                    teachers.setdefault(t.id, {'user': t, 'groups': []})['groups'].append(g)
        for t in sorted(teachers.values(), key=lambda x: x['user'].username.lower()):
            user, tgroups = t['user'], t['groups']
            profile = getattr(user, 'profile', None)
            phone = normalize_phone(profile.phone if profile else '')
            lines, total = [], Decimal('0')
            for e in entries.values():
                for line in e['lines']:
                    if line['group'] in tgroups and not line['paid']:
                        lines.append(detail_line(e['student'].name, line))
                        total += line['amount']
            if kind == 'payment' and not lines:
                continue
            recipients.append({
                'key': f't{user.id}', 'name': user.get_full_name() or user.username,
                'sub': ', '.join(g.name for g in tgroups[:3]) + ('…' if len(tgroups) > 3 else ''),
                'phone': phone or '', 'chat_id': chat_id_for_phone(phone) if phone else '',
                'ok': bool(phone), 'warn': '' if phone else 'Нет телефона в профиле учителя',
                'manual_only': False, 'amount': float(total) if kind == 'payment' else None,
                'group_id': None, 'student_id': None,
                'vars': {
                    'приветствие': f'Здравствуйте, {user.first_name or user.username}!', 'имя': '', 'родитель': '',
                    'группа': ', '.join(g.name for g in tgroups), 'учитель': user.username,
                    'месяц': month_name, 'период': '', 'сумма': f'{money(total)} ₸' if kind == 'payment' else '',
                    'детали': '\n'.join(lines),
                },
            })
        return recipients

    # Родители или ученики — лично
    if kind == 'payment':
        entries = payment_entries(groups, year, month, only_unpaid)
    else:
        entries = {}
        for g in groups:
            for e in g.enrollments.all():
                if e.student.is_active:
                    entries.setdefault(e.student_id, {'student': e.student, 'lines': [], 'total': Decimal('0'),
                                                      'start': None, 'end': None, 'groups': []})['groups'].append(g)

    # Один номер — одно сообщение (братья и сёстры с общим родителем)
    people = {}
    for e in sorted(entries.values(), key=lambda x: x['student'].name.lower()):
        s = e['student']
        if audience == 'parent':
            phone = normalize_phone(s.parent_phone)
            fallback = not phone and bool(normalize_phone(s.phone))
            phone = phone or normalize_phone(s.phone)
        else:
            phone = normalize_phone(s.phone)
            fallback = False
        key = f'p{phone}' if phone else f's{s.id}'
        person = people.setdefault(key, {'phone': phone, 'students': [], 'entries': [], 'fallback': False, 'parent': ''})
        person['students'].append(s)
        person['entries'].append(e)
        person['fallback'] = person['fallback'] or fallback
        if audience == 'parent' and s.parent_name and not person['parent']:
            person['parent'] = s.parent_name

    for key, p in people.items():
        students, phone = p['students'], p['phone']
        names = join_names(s.name for s in students)
        groups_of = [g for e in p['entries'] for g in e['groups']]
        lines, total, no_price = [], Decimal('0'), False
        for e in p['entries']:
            for line in e['lines']:
                lines.append(detail_line(e['student'].name, line))
                if not line['paid'] and not line['price']:
                    no_price = True
            total += e['total']
        start = next((e['start'] for e in p['entries'] if e['start']), None)
        end = next((e['end'] for e in p['entries'] if e['end']), None)
        if audience == 'parent':
            greeting = f'Здравствуйте, {first_name(p["parent"])}!' if p['parent'] else 'Здравствуйте!'
            name = p['parent'] or f'Родитель: {names}'
        else:
            greeting = f'Привет, {first_name(students[0].name)}!' if len(students) == 1 else 'Привет!'
            name = names
        warn = ''
        if not phone:
            warn = 'Нет номера родителя и ученика' if audience == 'parent' else 'Нет номера ученика'
        elif kind == 'payment' and no_price:
            warn = 'Не задана цена за урок — сумма неполная'
        elif kind == 'payment' and total == 0:
            warn = 'За этот месяц всё оплачено'
        elif p['fallback']:
            warn = 'Номера родителя нет — отправим на номер ученика'
        recipients.append({
            'key': key, 'name': name,
            'sub': (names + ' · ' if audience == 'parent' else '') + join_names(g.name for g in groups_of),
            'phone': phone or '', 'chat_id': chat_id_for_phone(phone) if phone else '',
            'ok': bool(phone) and not (kind == 'payment' and (no_price or total == 0)),
            'warn': warn, 'manual_only': False,
            'amount': float(total) if kind == 'payment' else None,
            'group_id': groups_of[0].id if groups_of else None, 'student_id': students[0].id,
            'vars': {
                'приветствие': greeting, 'имя': names, 'родитель': p['parent'],
                'группа': join_names(g.name for g in groups_of),
                'учитель': join_names(g.teacher.username for g in groups_of if g.teacher),
                'месяц': month_name, 'период': period_label(start, end) if start else '',
                'сумма': f'{money(total)} ₸', 'детали': '\n'.join(lines),
            },
        })
    recipients.sort(key=lambda r: (not r['ok'], r['name'].lower()))
    return recipients


# ---------------- Параметры запроса ----------------

def read_filters(data):
    """Фильтры из GET/POST/JSON: kind, audience, groups, type, unpaid, year, month"""
    kind = data.get('kind') if data.get('kind') in ('payment', 'news') else 'payment'
    audience = data.get('audience') if data.get('audience') in AUDIENCES else DEFAULT_AUDIENCE[kind]
    raw_groups = data.get('groups') or ''
    if isinstance(raw_groups, str):
        raw_groups = raw_groups.split(',')
    group_ids = [int(x) for x in raw_groups if str(x).strip().isdigit()]
    group_type = data.get('type') if data.get('type') in ('group', 'individual') else 'all'
    only_unpaid = str(data.get('unpaid', '1')) not in ('0', 'false', 'False')
    year, month = default_month()
    try:
        y, m = int(data.get('year')), int(data.get('month'))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            year, month = y, m
    except (TypeError, ValueError):
        pass
    return {'kind': kind, 'audience': audience, 'group_ids': group_ids, 'group_type': group_type,
            'only_unpaid': only_unpaid, 'year': year, 'month': month}


def current_mode():
    settings = WhatsAppSettings.load()
    return connection_status(settings) + (settings,)


# ---------------- Страницы и API ----------------

@crm_view
def broadcast_page(request):
    """Новая рассылка + история"""
    role = get_user_role(request.user)
    if role not in SEND_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    filters = read_filters(request.GET)
    mode, status_label, settings = current_mode()
    groups = Group.objects.filter(is_active=True).order_by('group_type', 'name')
    history = (Broadcast.objects.select_related('created_by')
               .annotate(total=Count('messages'),
                         sent=Count('messages', filter=Q(messages__status='sent')),
                         failed=Count('messages', filter=Q(messages__status='failed')))[:15])
    year, month = filters['year'], filters['month']
    context = {
        'role': role,
        'filters': filters,
        'groups': groups,
        'groups_json': [{'id': g.id, 'name': g.name, 'type': g.group_type, 'chat': bool(g.whatsapp_chat_id)} for g in groups],
        'templates': {f'{k[0]}-{k[1]}': v for k, v in TEMPLATES.items()},
        'variables': VARIABLES,
        'mode': mode,
        'status_label': status_label,
        'settings': settings,
        'token_hint': ('•' * 6 + settings.token[-4:]) if settings.token else '',
        'can_setup': role in SETUP_ROLES,
        'history': history,
        'month_name': MONTHS_RU[month - 1],
        'year': year,
        'month': month,
        'prev': (year - 1, 12) if month == 1 else (year, month - 1),
        'next': (year + 1, 1) if month == 12 else (year, month + 1),
    }
    return render(request, 'dashboard/broadcast.html', context)


@crm_view
def broadcast_recipients(request):
    """JSON: получатели и их переменные — для предпросмотра"""
    if get_user_role(request.user) not in SEND_ROLES:
        return JsonResponse({'error': 'Нет доступа'}, status=403)
    f = read_filters(request.GET)
    mode = request.GET.get('mode') if request.GET.get('mode') in ('api', 'manual') else 'manual'
    recipients = build_recipients(f['kind'], f['audience'], f['group_ids'], f['group_type'], f['only_unpaid'],
                                  f['year'], f['month'], mode)
    for r in recipients:
        r['phone_display'] = format_phone(r['phone'])
        r['amount_str'] = money(r['amount']) if r['amount'] is not None else ''
    return JsonResponse({
        'recipients': recipients,
        'month_name': MONTHS_RU[f['month'] - 1],
        'year': f['year'], 'month': f['month'],
    })


@crm_view
@require_POST
def broadcast_create(request):
    """Создать рассылку: получатели пересчитываются здесь же, исключённые снимаются"""
    role = get_user_role(request.user)
    if role not in SEND_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Неверные данные'}, status=400)
    f = read_filters(data)
    template = (data.get('text') or '').strip()
    default = TEMPLATES[template_key(f['kind'], f['audience'])]
    if len(VAR_RE.sub('', template).strip(' \n!📢👋🙏')) < 5 or (f['kind'] == 'news' and template == default.strip()):
        return JsonResponse({'success': False, 'error': 'Напишите текст сообщения'})
    if len(template) > 3500:
        return JsonResponse({'success': False, 'error': 'Текст слишком длинный — WhatsApp примет до 4000 символов'})
    excluded = set(data.get('excluded') or [])
    mode = current_mode()[0]
    recipients = [r for r in build_recipients(f['kind'], f['audience'], f['group_ids'], f['group_type'],
                                              f['only_unpaid'], f['year'], f['month'], mode)
                  if r['ok'] and r['key'] not in excluded]
    if not recipients:
        return JsonResponse({'success': False, 'error': 'Некому отправлять — проверьте фильтры и номера'})

    # Название: для оплаты собирается в шаблоне из месяца, для новости — первая строка текста
    if f['kind'] == 'payment':
        title = f'Оплата за {MONTHS_RU[f["month"] - 1].lower()}'
    else:
        first_line = next((line for line in render_text(template, recipients[0]['vars']).splitlines()
                           if line.strip() and not line.startswith(('Здравствуйте', 'Привет'))), 'Новость')
        first_line = re.sub(r'[*_~]', '', first_line).strip()
        title = first_line[:80] + ('…' if len(first_line) > 80 else '')
    broadcast = Broadcast.objects.create(
        kind=f['kind'], audience=f['audience'], mode=mode, title=title, text=template,
        year=f['year'] if f['kind'] == 'payment' else None, month=f['month'] if f['kind'] == 'payment' else None,
        created_by=request.user,
    )
    BroadcastMessage.objects.bulk_create([
        BroadcastMessage(
            broadcast=broadcast, recipient=r['name'][:200], note=r['sub'][:200], phone=r['phone'],
            chat_id=r['chat_id'], text=render_text(template, r['vars']),
            amount=Decimal(str(r['amount'])) if r['amount'] is not None else None,
            student_id=r['student_id'], group_id=r['group_id'],
        ) for r in recipients
    ])
    # WhatsApp подключён — страница рассылки сразу начинает отправку, второй раз жать не нужно
    url = f'/broadcast/{broadcast.id}/' + ('?start=1' if mode == 'api' else '')
    return JsonResponse({'success': True, 'id': broadcast.id, 'url': url})


@crm_view
def broadcast_detail(request, broadcast_id):
    """Отправка и итог рассылки"""
    role = get_user_role(request.user)
    if role not in SEND_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    broadcast = get_object_or_404(Broadcast.objects.select_related('created_by'), id=broadcast_id)
    mode, status_label, _ = current_mode()
    items = list(broadcast.messages.all())
    for m in items:
        # Автоматически — если WhatsApp подключён (WAHA или Green API) и есть куда слать; иначе вручную по ссылке
        m.auto = mode == 'api' and bool(m.chat_id)
        m.link = wa_link(m.phone, m.text) if m.phone else ''
        m.phone_display = format_phone(m.phone)
        m.amount_str = money(m.amount) if m.amount else ''
    counts = {s: sum(1 for m in items if m.status == s) for s, _ in BroadcastMessage.STATUS_CHOICES}
    context = {
        'role': role,
        'broadcast': broadcast,
        'items': items,
        'counts': counts,
        'total': len(items),
        'done': counts['sent'] + counts['skipped'],
        'auto_left': sum(1 for m in items if m.auto and m.status in ('pending', 'failed')),
        'mode': mode,
        'status_label': status_label,
        'amount_total': money(sum((m.amount or 0) for m in items)),
    }
    return render(request, 'dashboard/broadcast_detail.html', context)


@crm_view
@require_POST
def broadcast_send(request, message_id):
    """Отправить одно сообщение через WAHA или Green API (страница шлёт их по очереди)"""
    if get_user_role(request.user) not in SEND_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    message = get_object_or_404(BroadcastMessage, id=message_id)
    if message.status == 'sent':
        return JsonResponse({'success': True, 'status': 'sent'})
    if not message.chat_id:
        return JsonResponse({'success': False, 'error': 'Нет чата для автоматической отправки'})
    settings = WhatsAppSettings.load()
    if not settings.is_configured:
        return JsonResponse({'success': False, 'error': 'WhatsApp не подключён'})
    try:
        client(settings).send(message.chat_id, message.text)
    except WhatsAppError as e:
        message.status, message.error = 'failed', str(e)[:300]
        message.save(update_fields=['status', 'error'])
        return JsonResponse({'success': False, 'status': 'failed', 'error': message.error})
    message.status, message.error, message.sent_at = 'sent', '', timezone.now()
    message.save(update_fields=['status', 'error', 'sent_at'])
    return JsonResponse({'success': True, 'status': 'sent'})


@crm_view
@require_POST
def broadcast_mark(request, message_id):
    """Ручной режим: отметить, что сообщение отправлено (или вернуть, или пропустить)"""
    if get_user_role(request.user) not in SEND_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    message = get_object_or_404(BroadcastMessage, id=message_id)
    status = request.POST.get('status')
    if status not in ('sent', 'pending', 'skipped'):
        return JsonResponse({'success': False, 'error': 'Неверный статус'}, status=400)
    message.status = status
    message.sent_at = timezone.now() if status == 'sent' else None
    message.error = ''
    message.save(update_fields=['status', 'sent_at', 'error'])
    return JsonResponse({'success': True, 'status': status})


@crm_view
@require_POST
def whatsapp_settings(request):
    """Сохранить подключение (WAHA или Green API) и сразу проверить его"""
    if get_user_role(request.user) not in SETUP_ROLES:
        messages.error(request, 'У вас нет доступа')
        return redirect('broadcast_page')
    settings = WhatsAppSettings.load()
    if request.POST.get('disconnect'):
        settings.instance_id, settings.token = '', ''
        settings.save()
        messages.success(request, 'WhatsApp отключён — рассылки будут в ручном режиме.')
        return redirect('broadcast_page')
    provider = request.POST.get('provider') if request.POST.get('provider') in ('waha', 'green') else 'waha'
    if provider != settings.provider:
        # Другой способ — старый ключ к нему не подходит
        settings.provider, settings.token = provider, ''
    settings.api_url = (request.POST.get('api_url') or settings.DEFAULT_URLS[provider]).strip()[:200]
    settings.instance_id = re.sub(r'\D', '', request.POST.get('instance_id', ''))[:50] if provider == 'green' else ''
    token = request.POST.get('token', '').strip()
    if token:
        settings.token = token[:200]
    settings.save()
    if provider == 'waha' and settings.is_configured:
        # WAHA: проверяем связь и запускаем WhatsApp — дальше на странице покажется QR-код
        try:
            state, _ = waha_status(settings)
        except WhatsAppError as e:
            messages.error(request, f'Не получилось подключиться: {e}')
            return redirect('broadcast_page')
        if state == 'WORKING':
            messages.success(request, 'WhatsApp подключён — рассылки будут уходить автоматически.')
        else:
            messages.success(request, 'WAHA на связи. Отсканируйте QR-код телефоном с WhatsApp центра.')
        return redirect(reverse('broadcast_page') + '?setup=1')
    mode, label = connection_status(settings, fresh=True)
    if mode == 'api':
        messages.success(request, 'WhatsApp подключён — рассылки будут уходить автоматически.')
    else:
        messages.error(request, f'Не получилось подключиться: {label}')
    return redirect('broadcast_page')


@crm_view
def whatsapp_qr(request):
    """JSON для экрана подключения WAHA: состояние и QR-код (страница спрашивает раз в несколько секунд)"""
    if get_user_role(request.user) not in SETUP_ROLES:
        return JsonResponse({'error': 'Нет доступа'}, status=403)
    settings = WhatsAppSettings.load()
    if settings.provider != 'waha' or not settings.is_configured:
        return JsonResponse({'error': 'WAHA не настроен'}, status=400)
    try:
        state, qr = waha_status(settings)
    except WhatsAppError as e:
        return JsonResponse({'state': 'error', 'error': str(e)})
    return JsonResponse({'state': state, 'qr': qr})


@crm_view
def whatsapp_groups(request):
    """JSON: групповые чаты WhatsApp — чтобы привязать их к группам CRM"""
    if get_user_role(request.user) not in SETUP_ROLES:
        return JsonResponse({'error': 'Нет доступа'}, status=403)
    settings = WhatsAppSettings.load()
    if not settings.is_configured:
        return JsonResponse({'error': 'WhatsApp не подключён'}, status=400)
    try:
        chats = client(settings).groups()
    except WhatsAppError as e:
        return JsonResponse({'error': str(e)}, status=502)
    return JsonResponse({'chats': [{'id': cid, 'name': name} for cid, name in chats]})


@crm_view
@require_POST
def bind_group_chat(request):
    """Привязать группу CRM к чату WhatsApp (или отвязать)"""
    if get_user_role(request.user) not in SETUP_ROLES:
        return JsonResponse({'success': False, 'error': 'Нет доступа'}, status=403)
    group = get_object_or_404(Group, id=to_int(request.POST.get('group_id'), 0))
    chat_id = request.POST.get('chat_id', '').strip()
    if chat_id and not re.fullmatch(r'[\w.-]+@g\.us', chat_id):
        return JsonResponse({'success': False, 'error': 'Это не групповой чат WhatsApp'}, status=400)
    group.whatsapp_chat_id = chat_id
    group.save(update_fields=['whatsapp_chat_id'])
    return JsonResponse({'success': True})
