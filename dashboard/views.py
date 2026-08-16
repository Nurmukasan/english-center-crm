from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime, timedelta
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment
from users.models import Profile


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
    
    if role == 'teacher':
        groups = Group.objects.filter(teacher=request.user, is_active=True)
        total_students = Enrollment.objects.filter(group__teacher=request.user).count()
        today = timezone.now().date()
        today_lessons = Lesson.objects.filter(
            group__teacher=request.user,
            date=today
        ).count()
        
        context = {
            'role': 'teacher',
            'groups': groups,
            'total_students': total_students,
            'today_lessons': today_lessons,
        }
    else:
        groups = Group.objects.filter(is_active=True)
        total_students = Student.objects.count()
        total_groups = groups.count()
        
        current_month = str(timezone.now().month)
        current_year = timezone.now().year
        month_payments = Payment.objects.filter(month=current_month, year=current_year)
        paid_count = month_payments.filter(is_paid=True).count()
        unpaid_count = month_payments.filter(is_paid=False).count()
        
        payment_stats = []
        for month in range(1, 13):
            count = Payment.objects.filter(month=str(month), year=current_year, is_paid=True).count()
            payment_stats.append(count)
        
        today = timezone.now().date()
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
            'current_year': current_year,
        }
    
    return render(request, 'dashboard/dashboard.html', context)


@login_required
def group_detail(request, group_id):
    """Страница группы: ученики, посещаемость, оплаты"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role == 'teacher' and group.teacher != request.user:
        messages.error(request, 'У вас нет доступа к этой группе')
        return redirect('dashboard')
    
    enrollments = Enrollment.objects.filter(group=group).select_related('student')
    students = [enrollment.student for enrollment in enrollments]
    
    today = timezone.now().date()
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
    
    attendances = Attendance.objects.filter(lesson=lesson)
    attendance_dict = {att.student_id: att.status for att in attendances}
    
    current_month = str(today.month)
    current_year = today.year
    payments = Payment.objects.filter(
        group=group,
        month=current_month,
        year=current_year
    )
    payment_dict = {pay.student_id: pay.is_paid for pay in payments}
    
    student_data = []
    for student in students:
        student_data.append({
            'student': student,
            'attendance_status': attendance_dict.get(student.id, 'absent'),
            'is_paid': payment_dict.get(student.id, False),
        })
    
    # История уроков группы (последние 10)
    lessons_history = Lesson.objects.filter(group=group).order_by('-date')[:10]
    
    context = {
        'group': group,
        'student_data': student_data,
        'today': today,
        'current_month': current_month,
        'current_year': current_year,
        'role': role,
        'lessons_history': lessons_history,
    }
    
    return render(request, 'dashboard/group_detail.html', context)


@login_required
def mark_attendance(request, group_id):
    """Отметка посещаемости (AJAX)"""
    if request.method == 'POST':
        group = get_object_or_404(Group, id=group_id)
        role = get_user_role(request.user)
        
        if role == 'teacher' and group.teacher != request.user:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        student_id = request.POST.get('student_id')
        status = request.POST.get('status')
        date_str = request.POST.get('date', None)
        
        # Если дата указана — используем её, иначе сегодня
        if date_str:
            lesson_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            lesson_date = timezone.now().date()
        
        lesson, _ = Lesson.objects.get_or_create(
            group=group,
            date=lesson_date
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
        month = request.POST.get('month')
        year = request.POST.get('year')
        
        payment, created = Payment.objects.get_or_create(
            student_id=student_id,
            group=group,
            month=month,
            year=year,
            defaults={'amount': group.price, 'is_paid': True}
        )
        
        if not created:
            payment.is_paid = not payment.is_paid
            payment.save()
        
        return JsonResponse({'success': True, 'is_paid': payment.is_paid})
    
    return JsonResponse({'success': False})


@login_required
def students_list(request):
    """Список всех учеников"""
    role = get_user_role(request.user)
    
    if role == 'teacher':
        # Учитель видит только своих учеников
        students = Student.objects.filter(
            enrollments__group__teacher=request.user
        ).distinct()
    else:
        students = Student.objects.all()
    
    # Для каждого ученика считаем его группы
    student_data = []
    for student in students:
        enrollments = Enrollment.objects.filter(student=student).select_related('group')
        groups_list = [e.group.name for e in enrollments]
        student_data.append({
            'student': student,
            'groups': groups_list,
        })
    
    context = {
        'student_data': student_data,
        'role': role,
    }
    
    return render(request, 'dashboard/students_list.html', context)


@login_required
def add_student(request):
    """Добавление ученика"""
    role = get_user_role(request.user)
    
    if role == 'teacher':
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
            )
            
            # Добавляем в выбранные группы
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
    
    current_month = str(timezone.now().month)
    current_year = timezone.now().year
    
    # Фильтры
    month = request.GET.get('month', current_month)
    year = request.GET.get('year', str(current_year))
    status = request.GET.get('status', 'all')
    
    payments = Payment.objects.filter(month=month, year=year)
    
    if role == 'teacher':
        payments = payments.filter(group__teacher=request.user)
    
    if status == 'paid':
        payments = payments.filter(is_paid=True)
    elif status == 'unpaid':
        payments = payments.filter(is_paid=False)
    
    payments = payments.select_related('student', 'group').order_by('student__name')
    
    context = {
        'payments': payments,
        'role': role,
        'current_month': month,
        'current_year': year,
        'status_filter': status,
        'months': Payment.MONTH_CHOICES,
    }
    
    return render(request, 'dashboard/payments_list.html', context)


@login_required
def lesson_history(request, group_id):
    """История уроков группы"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    if role == 'teacher' and group.teacher != request.user:
        messages.error(request, 'У вас нет доступа')
        return redirect('dashboard')
    
    lessons = Lesson.objects.filter(group=group).order_by('-date')
    
    # Для каждого урока получаем посещаемость с именами учеников
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
    
    if role == 'teacher':
        groups = Group.objects.filter(teacher=request.user, is_active=True)
    else:
        groups = Group.objects.filter(is_active=True)
    
    # Дни недели
    days = [
        {'name': 'Понедельник', 'short': 'Пн', 'num': 0},
        {'name': 'Вторник', 'short': 'Вт', 'num': 1},
        {'name': 'Среда', 'short': 'Ср', 'num': 2},
        {'name': 'Четверг', 'short': 'Чт', 'num': 3},
        {'name': 'Пятница', 'short': 'Пт', 'num': 4},
        {'name': 'Суббота', 'short': 'Сб', 'num': 5},
        {'name': 'Воскресенье', 'short': 'Вс', 'num': 6},
    ]
    
    # Определяем даты текущей недели
    today = timezone.now().date()
    monday = today - timedelta(days=today.weekday())
    
    for i, day in enumerate(days):
        day_date = monday + timedelta(days=i)
        day['date'] = day_date.strftime('%d.%m')
        day['full_date'] = day_date.strftime('%d %B')
        day['is_today'] = (day_date == today)
    
    # Время с 6:00 до 22:00 (каждый слот — 30 минут)
    time_slots = []
    for hour in range(6, 23):
        for minute in [0, 30]:
            time_slots.append({
                'hour': hour,
                'minute': minute,
                'label': f'{hour}:{minute:02d}',
            })
    
    # Для каждой группы определяем день, время начала и конца
    import re
    
    schedule_data = []
    for group in groups:
        schedule_text = group.schedule.lower()
        
        # Определяем дни
        day_indexes = []
        day_keywords = {
            0: ['пн', 'понедельник'],
            1: ['вт', 'вторник'],
            2: ['ср', 'сред'],
            3: ['чт', 'четверг'],
            4: ['пт', 'пятниц'],
            5: ['сб', 'суббот'],
            6: ['вс', 'воскрес'],
        }
        
        for day_num, keywords in day_keywords.items():
            if any(keyword in schedule_text for keyword in keywords):
                day_indexes.append(day_num)
        
        # Если дни не найдены — пропускаем
        if not day_indexes:
            continue
        
        # Ищем время начала и конца (например 18:00-20:00)
        time_match = re.search(r'(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})', schedule_text)
        
        if time_match:
            start_hour = int(time_match.group(1))
            start_minute = int(time_match.group(2))
            end_hour = int(time_match.group(3))
            end_minute = int(time_match.group(4))
        else:
            # Ищем только одно время
            time_match = re.search(r'(\d{1,2})[:.](\d{2})', schedule_text)
            if time_match:
                start_hour = int(time_match.group(1))
                start_minute = int(time_match.group(2))
                # По умолчанию длительность 1.5 часа
                end_hour = start_hour + 1
                end_minute = start_minute + 30
                if end_minute >= 60:
                    end_hour += 1
                    end_minute -= 60
            else:
                # Если время не указано — 18:00-19:30
                start_hour = 18
                start_minute = 0
                end_hour = 19
                end_minute = 30
        
        # Вычисляем длительность в слотах (30 минут)
        start_total_minutes = start_hour * 60 + start_minute
        end_total_minutes = end_hour * 60 + end_minute
        duration_slots = (end_total_minutes - start_total_minutes) // 30
        
        if duration_slots < 1:
            duration_slots = 1
        
        for day_index in day_indexes:
            schedule_data.append({
                'group': group,
                'day_index': day_index,
                'start_hour': start_hour,
                'start_minute': start_minute,
                'duration_slots': duration_slots,
            })
        # Генерируем цвет для каждой группы
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
    
    # Присваиваем цвета группам
    group_colors = {}
    for i, group in enumerate(groups):
        color_index = i % len(pastel_colors)
        group_colors[group.id] = pastel_colors[color_index]
    
    # Добавляем цвет в schedule_data
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