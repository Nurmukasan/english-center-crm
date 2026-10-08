from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime, timedelta
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book, ScheduleSlot, LevelCalibration
from users.models import Profile
from decimal import Decimal


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


def login_view(request):
    """Страница входа"""
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Неверный логин или пароль')
    
    return render(request, 'dashboard/login.html')


def logout_view(request):
    """Выход"""
    logout(request)
    return redirect('login')


def get_user_role(user):
    """Получаем роль пользователя"""
    try:
        return user.profile.role
    except:
        return 'admin'


@login_required
def dashboard(request):
    """Главный дашборд"""
    role = get_user_role(request.user)
    search_query = request.GET.get('search', '')

    if role == 'teacher':
        groups = Group.objects.filter(
            Q(teacher=request.user) | Q(teachers=request.user),
            is_active=True
        ).distinct()
        total_students = Enrollment.objects.filter(
            Q(group__teacher=request.user) | Q(group__teachers=request.user)
        ).distinct().count()
        today = timezone.localdate()
        today_lessons = Lesson.objects.filter(
            Q(group__teacher=request.user) | Q(group__teachers=request.user),
            date=today
        ).distinct().count()
        
        if search_query:
            groups = groups.filter(name__iregex=search_query)
        
        context = {
            'role': 'teacher',
            'groups': groups,
            'total_students': total_students,
            'today_lessons': today_lessons,
            'search_query': search_query,
        }
    else:
        groups = Group.objects.filter(is_active=True)
        
        if search_query:
            groups = groups.filter(name__iregex=search_query)
        
        total_students = Student.objects.count()
        total_groups = groups.count()

        # Топ должников
        top_debtors = []
        students_with_debt = Student.objects.all()
        for student in students_with_debt:
            unpaid = Payment.objects.filter(student=student, is_paid=False)
            total_debt = sum([float(p.amount) - float(p.paid_amount) for p in unpaid])
            if total_debt > 0:
                top_debtors.append({
                    'student': student,
                    'debt': total_debt,
                    'phone': student.phone,
                })
        top_debtors.sort(key=lambda x: x['debt'], reverse=True)
        top_debtors = top_debtors[:5]
        
        # Статистика оплат
        today = timezone.localdate()
        current_payments = Payment.objects.filter(start_date__lte=today, end_date__gte=today)
        paid_count = current_payments.filter(is_paid=True).count()
        unpaid_count = current_payments.filter(is_paid=False).count()
        
        payment_stats = []
        for cycle in range(1, 13):
            count = Payment.objects.filter(cycle_number=cycle, is_paid=True).count()
            payment_stats.append(count)
        
        today_attendance = Attendance.objects.filter(lesson__date=today)
        present_count = today_attendance.filter(status='present').count()
        total_attendance = today_attendance.count()
        
        context = {
            'role': role,
            'groups': groups,
            'total_students': total_students,
            'total_groups': total_groups,
            'paid_count': paid_count,
            'unpaid_count': unpaid_count,
            'payment_stats': payment_stats,
            'present_count': present_count,
            'total_attendance': total_attendance,
            'top_debtors': top_debtors,
            'search_query': search_query,
        }
    
    return render(request, 'dashboard/dashboard.html', context)


@login_required
def group_detail(request, group_id):
    """Страница группы"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role == 'teacher':
        is_main = group.teacher == request.user
        is_additional = group.teachers.filter(id=request.user.id).exists()
        if not (is_main or is_additional):
            messages.error(request, 'У вас нет доступа к этой группе')
            return redirect('dashboard')
    
    enrollments = Enrollment.objects.filter(group=group).select_related('student')
    students = [enrollment.student for enrollment in enrollments]
    
    today = timezone.localdate()
    
    day_keywords = {
        0: ['пн', 'понедельник'],
        1: ['вт', 'вторник'],
        2: ['ср', 'сред'],
        3: ['чт', 'четверг'],
        4: ['пт', 'пятниц'],
        5: ['сб', 'суббот'],
        6: ['вс', 'воскрес'],
    }
    
    schedule_text = group.schedule.lower()
    today_weekday = today.weekday()
    
    is_lesson_day = any(keyword in schedule_text for keyword in day_keywords.get(today_weekday, []))
    
    if is_lesson_day:
        lesson, created = Lesson.objects.get_or_create(
            group=group,
            date=today,
            defaults={'topic': ''}
        )
        
        if created:
            for student in students:
                Attendance.objects.get_or_create(
                    lesson=lesson,
                    student=student,
                    defaults={'status': 'absent'}
                )
    else:
        lesson = None
    
    attendance_dict = {}
    homework_dict = {}
    if lesson:
        attendances = Attendance.objects.filter(lesson=lesson)
        attendance_dict = {att.student_id: att.status for att in attendances}
        homework_dict = {att.student_id: att.homework_done for att in attendances}
    
    payments = Payment.objects.filter(
        group=group,
        start_date__lte=today,
        end_date__gte=today
    )
    payment_dict = {pay.student_id: pay.is_paid for pay in payments}
    
    # Звёзды по всей группе (за всё время)
    from django.db.models import Count
    stars_qs = Attendance.objects.filter(
        lesson__group=group,
        star_earned=True,
    ).values('student_id').annotate(c=Count('id'))
    stars_dict = {s['student_id']: s['c'] for s in stars_qs}
    
    # Кто получил звезду сегодня
    today_star_dict = {}
    if lesson:
        today_stars = Attendance.objects.filter(
            lesson=lesson,
            star_earned=True
        ).values_list('student_id', flat=True)
        today_star_dict = {sid: True for sid in today_stars}
    
    student_data = []
    for student in students:
        enrollment = Enrollment.objects.filter(student=student, group=group).first()
        student_data.append({
            'student': student,
            'attendance_status': attendance_dict.get(student.id, 'absent'),
            'homework_done': homework_dict.get(student.id, False),
            'is_paid': payment_dict.get(student.id, False),
            'enrollment': enrollment,
            'stars': stars_dict.get(student.id, 0),
            'star_today': today_star_dict.get(student.id, False),
        })
    
    lessons_history = Lesson.objects.filter(group=group).order_by('-date')[:10]
    
    context = {
        'group': group,
        'student_data': student_data,
        'today': today,
        'role': role,
        'lessons_history': lessons_history,
        'lesson': lesson,
        'can_mark_attendance': role in ['teacher', 'developer', 'admin'],
    }
    
    return render(request, 'dashboard/group_detail.html', context)


@login_required
def mark_attendance(request, group_id):
    """Отметка посещаемости (AJAX)"""
    if request.method == 'POST':
        group = get_object_or_404(Group, id=group_id)
        role = get_user_role(request.user)
        
        if role not in ['admin', 'teacher', 'developer']:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        if role == 'teacher':
            is_main = group.teacher == request.user
            is_additional = group.teachers.filter(id=request.user.id).exists()
            if not (is_main or is_additional):
                return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        today = timezone.localdate()
        day_keywords = {
            0: ['пн', 'понедельник'],
            1: ['вт', 'вторник'],
            2: ['ср', 'сред'],
            3: ['чт', 'четверг'],
            4: ['пт', 'пятниц'],
            5: ['сб', 'суббот'],
            6: ['вс', 'воскрес'],
        }
        schedule_text = group.schedule.lower()
        today_weekday = today.weekday()
        is_lesson_day = any(keyword in schedule_text for keyword in day_keywords.get(today_weekday, []))
        
        if not is_lesson_day:
            return JsonResponse({'success': False, 'error': 'Сегодня нет урока'})
        
        student_id = request.POST.get('student_id')
        status = request.POST.get('status')
        
        lesson, _ = Lesson.objects.get_or_create(
            group=group,
            date=today
        )
        
        attendance, _ = Attendance.objects.get_or_create(
            lesson=lesson,
            student_id=student_id,
            defaults={'status': status}
        )
        attendance.status = status
        attendance.save()
        
        return JsonResponse({'success': True, 'status': status})
    
    return JsonResponse({'success': False})


@login_required
def toggle_payment(request, group_id):
    """Переключение оплаты (AJAX)"""
    if request.method == 'POST':
        group = get_object_or_404(Group, id=group_id)
        role = get_user_role(request.user)
        
        if role == 'teacher':
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        student_id = request.POST.get('student_id')
        student = get_object_or_404(Student, id=student_id)
        year, month = get_current_period(group.cycle_start_day)
        period_start, period_end = get_period(year, month, group.cycle_start_day)
        amount = calculate_cycle_amount(student, group, period_start, period_end)
        
        payment, created = Payment.objects.get_or_create(
            student=student,
            group=group,
            start_date__lte=today,
            end_date__gte=today,
            defaults={
                'amount': amount,
                'is_paid': True,
                'paid_amount': amount,
                'paid_at': timezone.now(),
                'marked_by': request.user,
                'cycle_number': 1,
            }
        )
        
        if not created:
            payment.is_paid = not payment.is_paid
            if payment.is_paid:
                payment.amount = amount
                payment.paid_amount = amount
                payment.paid_at = timezone.now()
            else:
                payment.paid_amount = 0
                payment.paid_at = None
            payment.save()
        
        return JsonResponse({'success': True, 'is_paid': payment.is_paid})
    
    return JsonResponse({'success': False})


@login_required
def students_list(request):
    """Список всех учеников с фильтрами"""
    role = get_user_role(request.user)

    if role == 'accountant':
        messages.error(request, 'У вас нет доступа')
        return redirect('payment_management')

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

    # ===== Поиск =====
    search_query = request.GET.get('search', '').strip()
    if search_query:
        students = students.filter(name__iregex=search_query)

    # ===== Фильтр: группа =====
    group_filter = request.GET.get('group', '')
    if group_filter == 'none':
        # Ученики без группы
        students = students.filter(enrollments__isnull=True)
    elif group_filter:
        students = students.filter(enrollments__group_id=group_filter)

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
    base_for_options = Student.objects.all()
    if role == 'teacher':
        base_for_options = base_for_options.filter(
            Q(enrollments__group__teacher=request.user) |
            Q(enrollments__group__teachers=request.user)
        ).distinct()

    grades_options = base_for_options.exclude(grade='').values_list('grade', flat=True).distinct().order_by('grade')
    schools_options = base_for_options.exclude(school='').values_list('school', flat=True).distinct().order_by('school')
    ages_options = base_for_options.exclude(age__isnull=True).values_list('age', flat=True).distinct().order_by('age')

    student_data = []
    for student in students:
        enrollments = Enrollment.objects.filter(student=student).select_related('group')
        groups_list = [e.group.name for e in enrollments]
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
    }

    return render(request, 'dashboard/students_list.html', context)


@login_required
def add_student(request):
    """Добавление ученика"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('students_list')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone', '')
        parent_name = request.POST.get('parent_name', '')
        parent_phone = request.POST.get('parent_phone', '')
        group_ids = request.POST.getlist('groups')
        
        if name:
            student = Student.objects.create(
                name=name,
                phone=phone,
                parent_name=parent_name,
                parent_phone=parent_phone,
                school=request.POST.get('school', ''),
                grade=request.POST.get('grade', ''),
                age=request.POST.get('age') or None,
                price_per_lesson=request.POST.get('price_per_lesson') or 0,
            )
            
            for group_id in group_ids:
                group = Group.objects.get(id=group_id)
                Enrollment.objects.get_or_create(student=student, group=group)
            
            messages.success(request, f'Ученик {name} добавлен!')
            return redirect('students_list')
    
    groups = Group.objects.filter(is_active=True)
    context = {
        'groups': groups,
    }
    
    return render(request, 'dashboard/add_student.html', context)


@login_required
def payments_list(request):
    """Все оплаты"""
    role = get_user_role(request.user)
    
    payments = Payment.objects.select_related('student', 'group').order_by('-cycle_number')
    
    if role == 'teacher':
        payments = payments.filter(group__teacher=request.user)
    
    context = {
        'payments': payments,
        'role': role,
    }
    
    return render(request, 'dashboard/payments_list.html', context)


@login_required
def lesson_history(request, group_id):
    """История уроков группы"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role == 'teacher':
        is_main = group.teacher == request.user
        is_additional = group.teachers.filter(id=request.user.id).exists()
        if not (is_main or is_additional):
            messages.error(request, 'У вас нет доступа')
            return redirect('dashboard')
    
    lessons = Lesson.objects.filter(group=group).order_by('-date')
    
    lesson_data = []
    for lesson in lessons:
        attendances = Attendance.objects.filter(lesson=lesson).select_related('student')
        
        present_students = []
        absent_students = []
        
        for att in attendances:
            if att.status == 'present':
                present_students.append(att.student.name)
            else:
                absent_students.append(att.student.name)
        
        lesson_data.append({
            'lesson': lesson,
            'present_count': len(present_students),
            'absent_count': len(absent_students),
            'total_count': len(present_students) + len(absent_students),
            'present_students': present_students,
            'absent_students': absent_students,
        })
    
    context = {
        'group': group,
        'lesson_data': lesson_data,
        'role': role,
    }
    
    return render(request, 'dashboard/lesson_history.html', context)


@login_required
def weekly_schedule(request):
    """Расписание на неделю"""
    role = get_user_role(request.user)
    
    if role in ['admin', 'accountant']:
        messages.error(request, 'Расписание пока недоступно для вашей роли')
        return redirect('dashboard')
    
    if role == 'teacher':
        groups = Group.objects.filter(
            Q(teacher=request.user) | Q(teachers=request.user),
            is_active=True
        ).distinct()
    else:
        groups = Group.objects.filter(is_active=True)
    
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
    
    time_slots = []
    for hour in range(6, 23):
        for minute in [0, 30]:
            time_slots.append({
                'hour': hour,
                'minute': minute,
                'label': f'{hour}:{minute:02d}',
            })
    
    schedule_data = []
    for group in groups:
        for slot in group.schedule_slots.all():
            start_hour = slot.start_time.hour
            start_minute = slot.start_time.minute
            end_hour = slot.end_time.hour
            end_minute = slot.end_time.minute
            
            start_minute = 0 if start_minute < 30 else 30
            end_minute = 0 if end_minute < 30 else 30
            
            start_total = start_hour * 60 + start_minute
            end_total = end_hour * 60 + end_minute
            duration_slots = (end_total - start_total) // 30
            
            if duration_slots < 1:
                duration_slots = 1
            
            schedule_data.append({
                'group': group,
                'day_index': slot.day_of_week,
                'start_hour': start_hour,
                'start_minute': start_minute,
                'duration_slots': duration_slots,
            })
    
    pastel_colors = [
        {'bg': 'bg-blue-100', 'border': 'border-blue-300', 'text': 'text-blue-800'},
        {'bg': 'bg-green-100', 'border': 'border-green-300', 'text': 'text-green-800'},
        {'bg': 'bg-purple-100', 'border': 'border-purple-300', 'text': 'text-purple-800'},
        {'bg': 'bg-pink-100', 'border': 'border-pink-300', 'text': 'text-pink-800'},
        {'bg': 'bg-yellow-100', 'border': 'border-yellow-300', 'text': 'text-yellow-800'},
        {'bg': 'bg-teal-100', 'border': 'border-teal-300', 'text': 'text-teal-800'},
        {'bg': 'bg-orange-100', 'border': 'border-orange-300', 'text': 'text-orange-800'},
        {'bg': 'bg-indigo-100', 'border': 'border-indigo-300', 'text': 'text-indigo-800'},
        {'bg': 'bg-rose-100', 'border': 'border-rose-300', 'text': 'text-rose-800'},
        {'bg': 'bg-cyan-100', 'border': 'border-cyan-300', 'text': 'text-cyan-800'},
    ]
    
    group_colors = {}
    for i, group in enumerate(groups):
        color_index = i % len(pastel_colors)
        group_colors[group.id] = pastel_colors[color_index]
    
    for item in schedule_data:
        color = group_colors.get(item['group'].id, pastel_colors[0])
        item['bg_color'] = color['bg']
        item['border_color'] = color['border']
        item['text_color'] = color['text']
    
    context = {
        'days': days,
        'time_slots': time_slots,
        'schedule_data': schedule_data,
        'role': role,
    }
    
    return render(request, 'dashboard/weekly_schedule.html', context)


@login_required
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
            phone = request.POST.get('phone')
            profile_obj, created = Profile.objects.get_or_create(user=request.user)
            profile_obj.phone = phone
            profile_obj.save()
            messages.success(request, 'Телефон обновлён!')
        
        elif action == 'change_photo':
            photo = request.FILES.get('photo')
            if photo:
                profile_obj, created = Profile.objects.get_or_create(user=request.user)
                profile_obj.photo = photo
                profile_obj.save()
                messages.success(request, 'Фото обновлено!')
            else:
                messages.error(request, 'Выберите файл')
    
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
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from django.http import HttpResponse


@login_required
def export_excel(request):
    """Экспорт данных в Excel"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'accountant', 'developer']:
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
    
    # Лист 1
    ws1 = wb.active
    ws1.title = 'Ученики и долги'
    
    headers1 = ['Имя', 'Телефон', 'Группы', 'Общий долг (₸)', 'Не оплачено циклов']
    for col, header in enumerate(headers1, 1):
        cell = ws1.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
    
    students = Student.objects.all()
    for row, student in enumerate(students, 2):
        enrollments = Enrollment.objects.filter(student=student).select_related('group')
        
        groups_info = []
        for enrollment in enrollments:
            groups_info.append(enrollment.group.name)
        groups_str = ', '.join(groups_info) if groups_info else '—'
        
        total_debt = 0
        cycles_unpaid = 0
        
        for enrollment in enrollments:
            unpaid_payments = Payment.objects.filter(
                student=student,
                group=enrollment.group,
                is_paid=False
            )
            total_debt += sum([float(p.amount) - float(p.paid_amount) for p in unpaid_payments])
            cycles_unpaid += unpaid_payments.count()
        
        data = [
            student.name,
            student.phone or '',
            groups_str,
            total_debt if total_debt > 0 else 0,
            cycles_unpaid,
        ]
        for col, value in enumerate(data, 1):
            cell = ws1.cell(row=row, column=col, value=value)
            cell.alignment = cell_alignment
            cell.border = border
            if col == 4 and total_debt > 0:
                cell.fill = PatternFill(start_color='FEE2E2', end_color='FEE2E2', fill_type='solid')
                cell.font = Font(color='DC2626', bold=True)
            elif col == 5 and cycles_unpaid > 0:
                cell.fill = PatternFill(start_color='FEE2E2', end_color='FEE2E2', fill_type='solid')
                cell.font = Font(color='DC2626')
    
    ws1.column_dimensions['A'].width = 25
    ws1.column_dimensions['B'].width = 20
    ws1.column_dimensions['C'].width = 50
    ws1.column_dimensions['D'].width = 20
    ws1.column_dimensions['E'].width = 25
    
    # Лист 2
    ws2 = wb.create_sheet('Долги по циклам')
    
    headers2 = ['Ученик', 'Телефон', 'Телефон родителя', 'Группа', 'Цена за урок', 'Цикл', 'Период', 'Долг', 'Статус']
    for col, header in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border
    
    unpaid = Payment.objects.filter(is_paid=False).select_related('student', 'group').order_by('student__name', 'cycle_number')
    for row, payment in enumerate(unpaid, 2):
        period = f"{payment.start_date.strftime('%d.%m')} — {payment.end_date.strftime('%d.%m')}" if payment.start_date and payment.end_date else '—'
        data = [
            payment.student.name,
            payment.student.phone or '—',
            payment.student.parent_phone or '—',
            payment.group.name,
            float(payment.student.price_per_lesson),
            f"Цикл {payment.cycle_number}",
            period,
            float(payment.amount) - float(payment.paid_amount),
            '❌ Не оплачено',
        ]
        for col, value in enumerate(data, 1):
            cell = ws2.cell(row=row, column=col, value=value)
            cell.alignment = cell_alignment
            cell.border = border
            if col == 7:
                cell.fill = PatternFill(start_color='FEE2E2', end_color='FEE2E2', fill_type='solid')
                cell.font = Font(color='DC2626')
    
    for letter, width in [('A', 25), ('B', 20), ('C', 20), ('D', 25), ('E', 15), ('F', 15), ('G', 25), ('H', 15), ('I', 20)]:
        ws2.column_dimensions[letter].width = width
    
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


@login_required
def payment_management(request):
    """Редирект на новую страницу оплат"""
    return redirect('payments_page')


@login_required
def toggle_payment_management(request, payment_id):
    """Отметить полную оплату"""
    if request.method == 'POST':
        role = get_user_role(request.user)
        
        if role not in ['admin', 'accountant', 'developer']:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        payment = get_object_or_404(Payment, id=payment_id)
        
        if payment.is_paid:
            payment.is_paid = False
            payment.is_partial = False
            payment.paid_amount = 0
            payment.paid_at = None
            payment.marked_by = None
        else:
            payment.is_paid = True
            payment.is_partial = False
            payment.paid_amount = payment.amount
            payment.paid_at = timezone.now()
            payment.marked_by = request.user
        
        payment.save()
        return JsonResponse({'success': True, 'is_paid': payment.is_paid})
    
    return JsonResponse({'success': False})


@login_required
def partial_payment(request, payment_id):
    """Частичная оплата"""
    if request.method == 'POST':
        role = get_user_role(request.user)
        
        if role not in ['admin', 'accountant', 'developer']:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        payment = get_object_or_404(Payment, id=payment_id)
        amount = float(request.POST.get('amount', 0))
        
        if amount > 0:
            payment.paid_amount = Decimal(str(payment.paid_amount)) + Decimal(str(amount))
            payment.is_partial = True
            payment.marked_by = request.user
            
            if payment.paid_amount >= payment.amount:
                payment.is_paid = True
                payment.is_partial = False
                payment.paid_amount = payment.amount
                payment.paid_at = timezone.now()
            
            payment.save()
            return JsonResponse({'success': True})
    
    return JsonResponse({'success': False})


@login_required
def accountant_stats(request):
    """Статистика для бухгалтера"""
    role = get_user_role(request.user)
    
    if role not in ['accountant', 'developer', 'admin']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    today = timezone.localdate()
    period = request.GET.get('period', 'month')
    group_filter = request.GET.get('group', 'all')
    
    payments = Payment.objects.filter(paid_amount__gt=0)
    
    if group_filter != 'all':
        payments = payments.filter(group_id=group_filter)
    
    total_income = sum([float(p.paid_amount) for p in payments])
    
    groups_stats = []
    all_groups = Group.objects.all()
    for group in all_groups:
        group_payments = payments.filter(group=group)
        group_income = sum([float(p.paid_amount) for p in group_payments])
        if group_income > 0:
            groups_stats.append({
                'group': group,
                'income': group_income,
                'count': group_payments.count(),
            })
    
    groups_stats.sort(key=lambda x: x['income'], reverse=True)
    
    monthly_income = []
    for i in range(11, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=i*30)
        month_start = month_date.replace(day=1)
        if month_start.month == 12:
            month_end = month_start.replace(day=31)
        else:
            next_month = month_start.replace(month=month_start.month + 1, day=1)
            month_end = next_month - timedelta(days=1)
        
        paid_with_date = Payment.objects.filter(
            paid_amount__gt=0,
            paid_at__isnull=False,
            paid_at__date__gte=month_start,
            paid_at__date__lte=month_end
        )
        
        partial_without_date = Payment.objects.filter(
            paid_amount__gt=0,
            paid_at__isnull=True,
            end_date__gte=month_start,
            end_date__lte=month_end
        )
        
        month_income = sum([float(p.paid_amount) for p in paid_with_date]) + \
                       sum([float(p.paid_amount) for p in partial_without_date])
        
        monthly_income.append({
            'month': month_start.strftime('%B %Y'),
            'income': month_income,
        })
    
    context = {
        'role': role,
        'total_income': total_income,
        'groups_stats': groups_stats,
        'monthly_income': monthly_income,
        'period': period,
        'start_date': today.replace(day=1),
        'end_date': today,
        'group_filter': group_filter,
        'all_groups': all_groups,
    }
    
    return render(request, 'dashboard/accountant_stats.html', context)


@login_required
def export_income_excel(request):
    """Экспорт статистики доходов в Excel"""
    role = get_user_role(request.user)
    
    if role not in ['accountant', 'developer', 'admin']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    today = timezone.localdate()
    period = request.GET.get('period', 'month')
    group_filter = request.GET.get('group', 'all')
    
    if period == 'month':
        start_date = today.replace(day=1)
    elif period == '3months':
        start_date = today - timedelta(days=90)
    elif period == 'year':
        start_date = today.replace(month=1, day=1)
    else:
        start_date = today.replace(day=1)
    
    end_date = today
    
    payments = Payment.objects.filter(
        is_paid=True,
        paid_at__date__gte=start_date,
        paid_at__date__lte=end_date
    )
    
    if group_filter != 'all':
        payments = payments.filter(group_id=group_filter)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Доходы'
    
    headers = ['Ученик', 'Группа', 'Сумма', 'Дата оплаты', 'Кто отметил']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color='FFFFFF', size=12)
        cell.fill = PatternFill(start_color='4F46E5', end_color='4F46E5', fill_type='solid')
        cell.alignment = Alignment(horizontal='center')
        cell.border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )
    
    for row, payment in enumerate(payments, 2):
        data = [
            payment.student.name,
            payment.group.name,
            float(payment.paid_amount),
            payment.paid_at.strftime('%d.%m.%Y') if payment.paid_at else '—',
            payment.marked_by.username if payment.marked_by else '—',
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )
    
    for letter, width in [('A', 25), ('B', 25), ('C', 15), ('D', 15), ('E', 20)]:
        ws.column_dimensions[letter].width = width
    
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="income_report.xlsx"'
    wb.save(response)
    return response


@login_required
def add_group(request):
    """Создание группы"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        group_type = request.POST.get('group_type', 'group')
        teacher_id = request.POST.get('teacher')
        
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
            )
            
            import re
            for i in range(7):
                if request.POST.get(f'day_{i}') == '1':
                    start = request.POST.get(f'start_{i}', '').strip()
                    end = request.POST.get(f'end_{i}', '').strip()
                    
                    if re.match(r'^\d{1,2}:\d{2}$', start) and re.match(r'^\d{1,2}:\d{2}$', end):
                        try:
                            ScheduleSlot.objects.create(
                                group=group,
                                day_of_week=i,
                                start_time=start,
                                end_time=end,
                            )
                        except Exception as e:
                            messages.error(request, f'Ошибка в дне {i}: {e}')
            
            slots = group.schedule_slots.all().order_by('day_of_week')
            days_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
            schedule_parts = []
            for slot in slots:
                schedule_parts.append(f"{days_names[slot.day_of_week]} {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}")
            group.schedule = ', '.join(schedule_parts)
            group.save()
            
            additional_teacher_ids = request.POST.getlist('additional_teachers', [])
            additional_teacher_ids = [tid for tid in additional_teacher_ids if int(tid) != int(teacher_id)]
            if additional_teacher_ids:
                group.teachers.set(additional_teacher_ids)
            messages.success(request, f'Группа "{name}" создана!')
            return redirect('dashboard')
        else:
            messages.error(request, 'Заполните название и выберите учителя')
    
    teachers = User.objects.filter(profile__role='teacher')
    
    context = {
        'teachers': teachers,
    }
    
    return render(request, 'dashboard/add_group.html', context)


@login_required
def remove_student_from_group(request, student_id, group_id):
    """Удалить ученика из группы"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    group = get_object_or_404(Group, id=group_id)
    
    Enrollment.objects.filter(student=student, group=group).delete()
    messages.success(request, f'{student.name} удалён из группы {group.name}')
    
    return redirect('group_detail', group_id=group.id)


@login_required
def delete_student(request, student_id):
    """Удалить ученика из базы"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    name = student.name
    student.delete()
    messages.success(request, f'Ученик {name} удалён из базы')
    
    return redirect('students_list')


@login_required
def delete_group(request, group_id):
    """Удалить группу"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    name = group.name
    group.delete()
    messages.success(request, f'Группа "{name}" удалена')
    
    return redirect('dashboard')


@login_required
def add_existing_student_to_group(request, student_id):
    """Добавить существующего ученика в группу"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        group_ids = request.POST.getlist('groups')
        
        for group_id in group_ids:
            group = Group.objects.get(id=group_id)
            Enrollment.objects.get_or_create(student=student, group=group)
        
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


@login_required
def toggle_book_status(request, enrollment_id=None):
    """Переключить статус книги у ученика"""
    if request.method == 'POST':
        role = get_user_role(request.user)

        if role not in ['admin', 'teacher', 'developer']:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})

        action = request.POST.get('action')
        book_id = request.POST.get('book_id')

        enrollment_ids = request.POST.get('enrollment_ids', '')
        if enrollment_ids:
            for eid in enrollment_ids.split(','):
                try:
                    enrollment = Enrollment.objects.get(id=int(eid))
                    if action == 'need_book':
                        enrollment.book_needed = True
                        enrollment.has_book = False
                        if book_id:
                            enrollment.book_id = int(book_id)
                    elif action == 'has_book':
                        enrollment.has_book = True
                        enrollment.book_needed = False
                    enrollment.save()
                except Exception:
                    pass
            return JsonResponse({'success': True})

        if enrollment_id:
            enrollment = get_object_or_404(Enrollment, id=enrollment_id)
            if action == 'need_book':
                enrollment.book_needed = True
                enrollment.has_book = False
                if book_id:
                    enrollment.book_id = int(book_id)
            elif action == 'has_book':
                enrollment.has_book = True
                enrollment.book_needed = False
            enrollment.save()
            return JsonResponse({'success': True})

    return JsonResponse({'success': False})


@login_required
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

    context = {
        'book_stats': book_stats,
        'role': role,
    }

    return render(request, 'dashboard/books_status.html', context)


@login_required
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


@login_required
def edit_student(request, student_id):
    """Редактирование ученика"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('students_list')
    
    student = get_object_or_404(Student, id=student_id)
    
    if request.method == 'POST':
        student.name = request.POST.get('name', student.name)
        student.phone = request.POST.get('phone', '')
        student.parent_name = request.POST.get('parent_name', '')
        student.parent_phone = request.POST.get('parent_phone', '')
        student.school = request.POST.get('school', '')
        student.grade = request.POST.get('grade', '')
        student.age = request.POST.get('age') or None
        student.price_per_lesson = request.POST.get('price_per_lesson') or 0
        student.save()
        
        group_ids = request.POST.getlist('groups')
        Enrollment.objects.filter(student=student).delete()
        for group_id in group_ids:
            group = Group.objects.get(id=group_id)
            Enrollment.objects.create(student=student, group=group)
        
        messages.success(request, f'Ученик {student.name} обновлён!')
        return_url = request.POST.get('return_url', '')
        if return_url:
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


@login_required
def edit_group(request, group_id):
    """Редактирование группы"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    group = get_object_or_404(Group, id=group_id)
    
    if request.method == 'POST':
        group.name = request.POST.get('name', group.name)
        group.group_type = request.POST.get('group_type', group.group_type)
        group.teacher_id = request.POST.get('teacher', group.teacher_id)
        additional_teacher_ids = request.POST.getlist('additional_teachers', [])
        additional_teacher_ids = [tid for tid in additional_teacher_ids if int(tid) != group.teacher_id]
        group.teachers.set(additional_teacher_ids)
        try:
            cycle_start_day = int(request.POST.get('cycle_start_day', group.cycle_start_day))
            if not (2 <= cycle_start_day <= 28):
                cycle_start_day = group.cycle_start_day
        except (ValueError, TypeError):
            cycle_start_day = group.cycle_start_day
        group.cycle_start_day = cycle_start_day
        group.is_active = True
        group.save()
        
        group.schedule_slots.all().delete()
        
        import re
        for i in range(7):
            if request.POST.get(f'day_{i}') == '1':
                start = request.POST.get(f'start_{i}', '').strip()
                end = request.POST.get(f'end_{i}', '').strip()
                
                if re.match(r'^\d{1,2}:\d{2}$', start) and re.match(r'^\d{1,2}:\d{2}$', end):
                    try:
                        ScheduleSlot.objects.create(
                            group=group,
                            day_of_week=i,
                            start_time=start,
                            end_time=end,
                        )
                    except Exception as e:
                        messages.error(request, f'Ошибка в дне {i}: {e}')
        
        slots = group.schedule_slots.all().order_by('day_of_week')
        days_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
        schedule_parts = []
        for slot in slots:
            schedule_parts.append(f"{days_names[slot.day_of_week]} {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}")
        group.schedule = ', '.join(schedule_parts)
        group.save()
        
        messages.success(request, f'Группа "{group.name}" обновлена!')
        return_url = request.POST.get('return_url', '')
        if return_url:
            return redirect(return_url)
        return redirect('dashboard')
    
    teachers = User.objects.filter(profile__role='teacher')
    schedule_slots = {str(s.day_of_week): s for s in group.schedule_slots.all()}
    
    context = {
        'group': group,
        'teachers': teachers,
        'schedule_slots': schedule_slots,
    }
    
    return render(request, 'dashboard/edit_group.html', context)


from calendar import monthrange
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


def make_calendar(year, month, period_start, period_end, lesson_dates_set):
    """Структура для календаря"""
    days_in_month = monthrange(year, month)[1]
    first_weekday = date(year, month, 1).weekday()
    
    days = []
    for d in range(1, days_in_month + 1):
        current = date(year, month, d)
        days.append({
            'day': d,
            'is_in_period': period_start <= current <= period_end,
            'has_lesson': current in lesson_dates_set,
            'is_today': current == timezone.localdate(),
        })
    
    return {
        'name': MONTHS_RU[month - 1],
        'year': year,
        'month': month,
        'first_weekday': first_weekday,
        'days': days,
    }


@login_required
def payments_page(request):
    """Оплаты: соты месяцев или список групп"""
    role = get_user_role(request.user)

    if role not in ['admin', 'accountant', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    today = timezone.localdate()
    default_year, default_month = get_current_period()

    year_param = request.GET.get('year')
    month_param = request.GET.get('month')

    show_details = bool(year_param and month_param)
    display_year = int(year_param) if year_param else today.year

    total_students_all = Enrollment.objects.filter(group__is_active=True).count()

    months_grid = []
    for m in range(1, 13):
        cycle_num = display_year * 100 + m
        paid_count = Payment.objects.filter(cycle_number=cycle_num, is_paid=True).count()

        is_current = (display_year == default_year and m == default_month)
        is_past = (display_year < today.year) or (display_year == today.year and m < today.month)

        months_grid.append({
            'num': m,
            'name': MONTHS_RU[m - 1],
            'year': display_year,
            'is_current': is_current,
            'is_past': is_past,
            'paid_count': paid_count,
            'total_students': total_students_all,
        })

    context = {
        'role': role,
        'display_year': display_year,
        'prev_year': display_year - 1,
        'next_year': display_year + 1,
        'months_grid': months_grid,
        'show_details': show_details,
    }

    if show_details:
        year = int(year_param)
        month = int(month_param)

        if not (1 <= month <= 12):
            year, month = default_year, default_month

        period_start, period_end = get_period(year, month)
        cycle_num = year * 100 + month
        search_query = request.GET.get('search', '')

        groups_data = []
        groups = Group.objects.filter(is_active=True).select_related('teacher').order_by('name')

        if search_query:
            groups = groups.filter(name__iregex=search_query)

        for group in groups:
            students_count = Enrollment.objects.filter(group=group).count()
            paid_count = Payment.objects.filter(
                group=group,
                cycle_number=cycle_num,
                is_paid=True
            ).count()

            groups_data.append({
                'group': group,
                'total_students': students_count,
                'paid_count': paid_count,
                'unpaid_count': students_count - paid_count,
            })

        context.update({
            'year': year,
            'month': month,
            'month_name': MONTHS_RU[month - 1],
            'period_start': period_start,
            'period_end': period_end,
            'groups_data': groups_data,
            'search_query': search_query,
        })

    return render(request, 'dashboard/payments.html', context)


@login_required
def group_payment_detail(request, group_id):
    """AJAX: детали оплаты группы"""
    role = get_user_role(request.user)
    
    if role not in ['admin', 'accountant', 'developer']:
        return JsonResponse({'error': 'Нет доступа'}, status=403)
    
    group = get_object_or_404(Group, id=group_id)
    
    year = int(request.GET.get('year'))
    month = int(request.GET.get('month'))
    
    period_start, period_end = get_period(year, month, group.cycle_start_day)
    
    lesson_dates_set = set()
    schedule_slots = list(group.schedule_slots.all())
    lesson_day_of_weeks = set(s.day_of_week for s in schedule_slots)
    
    current = period_start
    while current <= period_end:
        if current.weekday() in lesson_day_of_weeks:
            lesson_dates_set.add(current)
        current += timedelta(days=1)
    
    cal1 = make_calendar(year, month, period_start, period_end, lesson_dates_set)
    
    if month == 12:
        y2, m2 = year + 1, 1
    else:
        y2, m2 = year, month + 1
    cal2 = make_calendar(y2, m2, period_start, period_end, lesson_dates_set)
    
    cycle_num = year * 100 + month
    enrollments = Enrollment.objects.filter(group=group).select_related('student').order_by('student__name')
    
    lesson_count = len(lesson_dates_set)
    
    students_data = []
    for enrollment in enrollments:
        student = enrollment.student
        payment = Payment.objects.filter(
            student=student,
            group=group,
            cycle_number=cycle_num
        ).first()
        
        amount = calculate_cycle_amount(student, group, period_start, period_end)
        
        students_data.append({
            'student': student,
            'is_paid': payment.is_paid if payment else False,
            'amount': amount,
            'price_per_lesson': student.price_per_lesson,
        })
    
    context = {
        'group': group,
        'year': year,
        'month': month,
        'month_name': MONTHS_RU[month - 1],
        'period_start': period_start,
        'period_end': period_end,
        'calendar1': cal1,
        'calendar2': cal2,
        'lesson_count': lesson_count,
        'students_data': students_data,
    }
    
    return render(request, 'dashboard/group_payment_detail.html', context)


@login_required
def toggle_student_payment(request, group_id):
    """AJAX: отметить/отменить оплату"""
    if request.method != 'POST':
        return JsonResponse({'success': False})
    
    role = get_user_role(request.user)
    if role not in ['admin', 'accountant', 'developer']:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    group = get_object_or_404(Group, id=group_id)
    
    student_id = request.POST.get('student_id')
    student = get_object_or_404(Student, id=student_id)
    year = int(request.POST.get('year'))
    month = int(request.POST.get('month'))
    cycle_num = year * 100 + month
    
    period_start, period_end = get_period(year, month, group.cycle_start_day)
    amount = calculate_cycle_amount(student, group, period_start, period_end)
    
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
        }
    )
    
    if not created:
        payment.is_paid = not payment.is_paid
        if payment.is_paid:
            payment.amount = amount
            payment.paid_amount = amount
            payment.paid_at = timezone.now()
            payment.marked_by = request.user
        else:
            payment.paid_amount = 0
            payment.paid_at = None
            payment.marked_by = None
        payment.save()
    
    return JsonResponse({'success': True, 'is_paid': payment.is_paid})


@login_required
def book_reader(request, book_id):
    """Reader — просмотр PDF книги"""
    role = get_user_role(request.user)
    book = get_object_or_404(Book, id=book_id)
    
    context = {
        'role': role,
        'book': book,
    }
    
    return render(request, 'dashboard/book_reader.html', context)

@login_required
def mark_homework(request, group_id):
    """Отметка домашки (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False})
    
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role not in ['admin', 'teacher', 'developer']:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    if role == 'teacher':
        is_main = group.teacher == request.user
        is_additional = group.teachers.filter(id=request.user.id).exists()
        if not (is_main or is_additional):
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    today = timezone.localdate()
    student_id = request.POST.get('student_id')
    done = request.POST.get('done') == '1'
    
    lesson, _ = Lesson.objects.get_or_create(group=group, date=today)
    
    attendance, _ = Attendance.objects.get_or_create(
        lesson=lesson,
        student_id=student_id,
        defaults={'status': 'absent'}
    )
    attendance.homework_done = done
    attendance.save()
    
    return JsonResponse({'success': True, 'homework_done': done})


@login_required
def create_calibration(request):
    """Создание калибровки уровня (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False})
    
    role = get_user_role(request.user)
    if role not in ['admin', 'teacher', 'developer']:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    student_id = request.POST.get('student_id')
    group_id = request.POST.get('group_id')
    direction = request.POST.get('direction')
    comment = request.POST.get('comment', '').strip()
    
    if direction not in ['up', 'down']:
        return JsonResponse({'success': False, 'error': 'Неверное направление'})
    
    student = get_object_or_404(Student, id=student_id)
    group = get_object_or_404(Group, id=group_id)
    
    cal = LevelCalibration.objects.create(
        student=student,
        group=group,
        direction=direction,
        comment=comment,
        created_by=request.user,
    )
    
    return JsonResponse({'success': True, 'id': cal.id})

@login_required
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
    total_stars = attendances.filter(star_earned=True).count()

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

@login_required
def give_stars(request, group_id):
    """Выдача звёзд (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'success': False})
    
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role not in ['admin', 'teacher', 'developer']:
        return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    if role == 'teacher':
        is_main = group.teacher == request.user
        is_additional = group.teachers.filter(id=request.user.id).exists()
        if not (is_main or is_additional):
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
    
    today = timezone.localdate()
    student_ids = request.POST.get('student_ids', '').split(',')
    
    lesson, _ = Lesson.objects.get_or_create(group=group, date=today)
    
    count = 0
    for sid in student_ids:
        sid = sid.strip()
        if not sid:
            continue
        try:
            attendance, _ = Attendance.objects.get_or_create(
                lesson=lesson,
                student_id=int(sid),
                defaults={'status': 'absent'}
            )
            if not attendance.star_earned:
                attendance.star_earned = True
                attendance.save()
                count += 1
        except Exception:
            pass
    
    return JsonResponse({'success': True, 'count': count})

@login_required
def star_rating(request):
    """Рейтинг учеников по звёздам"""
    role = get_user_role(request.user)

    if role not in ['admin', 'developer']:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')

    from django.db.models import Count, Q

    students = Student.objects.annotate(
        stars_count=Count('attendances', filter=Q(attendances__star_earned=True))
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