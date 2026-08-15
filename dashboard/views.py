from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime
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
        # Учитель видит только свои группы
        groups = Group.objects.filter(teacher=request.user, is_active=True)
        
        # Статистика для учителя
        total_students = Enrollment.objects.filter(group__teacher=request.user).count()
        today = timezone.now().date()
        
        # Сегодняшние уроки
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
        # Админ/разработчик видит всё
        groups = Group.objects.filter(is_active=True)
        total_students = Student.objects.count()
        total_groups = groups.count()
        
        # Статистика оплат за текущий месяц
        current_month = str(timezone.now().month)
        current_year = timezone.now().year
        month_payments = Payment.objects.filter(month=current_month, year=current_year)
        paid_count = month_payments.filter(is_paid=True).count()
        unpaid_count = month_payments.filter(is_paid=False).count()
        
        # Данные для графика (оплаты по месяцам)
        payment_stats = []
        for month in range(1, 13):
            count = Payment.objects.filter(month=str(month), year=current_year, is_paid=True).count()
            payment_stats.append(count)
        
        # Посещаемость за сегодня
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
        }
    
    return render(request, 'dashboard/dashboard.html', context)


@login_required
def group_detail(request, group_id):
    """Страница группы: ученики, посещаемость, оплаты"""
    group = get_object_or_404(Group, id=group_id)
    role = get_user_role(request.user)
    
    # Проверка: учитель может видеть только свои группы
    if role == 'teacher' and group.teacher != request.user:
        messages.error(request, 'У вас нет доступа к этой группе')
        return redirect('dashboard')
    
    # Получаем всех учеников группы
    enrollments = Enrollment.objects.filter(group=group).select_related('student')
    students = [enrollment.student for enrollment in enrollments]
    
    # Получаем или создаём урок на сегодня
    today = timezone.now().date()
    lesson, created = Lesson.objects.get_or_create(
        group=group,
        date=today,
        defaults={'topic': ''}
    )
    
    # Если урок только создан — создаём записи посещаемости
    if created:
        for student in students:
            Attendance.objects.get_or_create(
                lesson=lesson,
                student=student,
                defaults={'status': 'absent'}
            )
    
    # Получаем посещаемость
    attendances = Attendance.objects.filter(lesson=lesson)
    attendance_dict = {att.student_id: att.status for att in attendances}
    
    # Получаем оплаты за текущий месяц
    current_month = str(today.month)
    current_year = today.year
    payments = Payment.objects.filter(
        group=group,
        month=current_month,
        year=current_year
    )
    payment_dict = {pay.student_id: pay.is_paid for pay in payments}
    
    # Формируем список учеников с данными
    student_data = []
    for student in students:
        student_data.append({
            'student': student,
            'attendance_status': attendance_dict.get(student.id, 'absent'),
            'is_paid': payment_dict.get(student.id, False),
        })
    
    context = {
        'group': group,
        'student_data': student_data,
        'today': today,
        'current_month': current_month,
        'current_year': current_year,
        'role': role,
    }
    
    return render(request, 'dashboard/group_detail.html', context)


@login_required
def mark_attendance(request, group_id):
    """Отметка посещаемости (AJAX)"""
    if request.method == 'POST':
        group = get_object_or_404(Group, id=group_id)
        role = get_user_role(request.user)
        
        # Учитель может отмечать только в своих группах
        if role == 'teacher' and group.teacher != request.user:
            return JsonResponse({'success': False, 'error': 'Нет доступа'})
        
        student_id = request.POST.get('student_id')
        status = request.POST.get('status')
        
        today = timezone.now().date()
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
        
        # Только админ или разработчик может менять оплату
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