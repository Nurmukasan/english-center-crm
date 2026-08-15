from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
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


@login_required
def dashboard(request):
    """Главный дашборд"""
    # Проверяем роль пользователя
    try:
        profile = request.user.profile
        role = profile.role
    except:
        role = 'admin'  # Если нет профиля — считаем админом
    
    if role == 'teacher':
        # Учитель видит только свои группы
        groups = Group.objects.filter(teacher=request.user, is_active=True)
        context = {
            'role': 'teacher',
            'groups': groups,
        }
    else:
        # Админ/разработчик видит всё
        groups = Group.objects.filter(is_active=True)
        total_students = Student.objects.count()
        total_paid = Payment.objects.filter(is_paid=True).count()
        context = {
            'role': role,
            'groups': groups,
            'total_students': total_students,
            'total_paid': total_paid,
        }
    
    return render(request, 'dashboard/dashboard.html', context)